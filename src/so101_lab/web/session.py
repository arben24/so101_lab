"""Eine Verbindung der Web-App zu einem Gerät.

Hier liegen die Regeln für den Betrieb über den Browser:

- **Ein Bus, ein Schloss.** Zustandsabfrage und Befehle teilen sich den Bus. Jeder Zugriff
  läuft unter `self.bus_lock`, damit sich zwei Pakete nie überlagern.
- **Zustandsabfrage im Hintergrund.** Ein Thread liest mit `poll_hz` die Positionen und
  seltener (jede `slow_every`-te Runde) den vollen Zustand (Spannung, Temperatur, Torque …).
- **Steuerrecht.** Höchstens ein Client steuert (`controller`). Alle anderen beobachten.
  Übernehmen geht nur ausdrücklich (`force=True`).
- **Watchdog.** Der steuernde Client schickt laufend Heartbeats. Bleiben sie länger als
  `watchdog_s` aus, verliert er das Steuerrecht und das Gerät geht in den sicheren Zustand
  (Räder stopp, dann `safe_state` aus arms.toml). Dasselbe passiert, wenn er die Verbindung
  schließt oder das Steuerrecht abgibt.
- **Not-Aus** darf jeder auslösen: Räder stopp, Torque aus für jeden Motor, Steuerrecht weg.
- **Kalibrierung** läuft schrittweise (`calibrate.CalibrationRun`). Verliert der Client das
  Steuerrecht mittendrin, werden die alten EEPROM-Werte zurückgeschrieben.
- **Register schreiben:** RAM nur aus der Whitelist, EEPROM nur mit ausdrücklicher Bestätigung
  und nie ID, Baudrate oder die Kalibrierwerte (dafür gibt es eigene Abläufe).
- **Aufnehmen, Abspielen, Teleoperation** (`motion.py`) laufen im Takt der Zustandsabfrage mit:
  Der Recorder bekommt jede gelesene Position, Abspieler und Teleoperation liefern je Runde ein
  Ziel für `move_to`. So gibt es keinen zweiten Thread am Bus. Verliert der Client das
  Steuerrecht (Abgabe, Watchdog, Not-Aus, Trennen), endet alles: Eine laufende Aufnahme wird
  bis dahin gespeichert, Abspielen und Teleoperation hören auf.
- **Kameras** aus arms.toml werden beim Verbinden geöffnet (eigener Lese-Thread je Kamera).
  Fehlt eine, verbindet das Gerät trotzdem, mit Warnung.
- **LeRobot-Dataset** (`lab_dataset.py`): Episoden laufen in einem eigenen Thread mit fester
  Rate und lesen nur die zuletzt abgefragten Werte und das neueste Kamerabild. Verliert der
  Client das Steuerrecht, wird die laufende Episode verworfen (unvollständige Vorführungen
  sind schlechte Trainingsdaten).
"""

from __future__ import annotations

import itertools
import logging
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import asdict, dataclass
from typing import Any, TypeVar

from .. import registers as regs
from ..arm import Arm
from ..backends.base import BackendError, ServoState
from ..calibrate import CalibrationRun, Stage, target_path
from ..calibration import find_calibration_file, load_calibration
from ..cameras import LabCamera, open_camera
from ..devices.spec import Joint, JointMode, NormMode
from ..lab_dataset import DatasetError, LabDataset, joint_values
from ..motion import (
    LEADER_TIMEOUT_S,
    Converter,
    PlaybackPlan,
    Player,
    Recorder,
    TeleopFollow,
    approach_duration,
    calibration_to_dict,
    prepare_playback,
)
from ..register_info import info
from ..safety import write_ram_register
from ..trajectories import TrajectoryError, check_name, check_new_name, load_trajectory, save_trajectory

logger = logging.getLogger(__name__)

# Register, die im Register-Explorer nicht direkt geschrieben werden, mit Begründung
EEPROM_LOCKED = {
    "id": "ID nur mit dem Einrichtungsassistenten bzw. Skript 05 ändern (genau ein Servo am Bus).",
    "baud_rate": "Baudrate nur mit dem Einrichtungsassistenten ändern, sonst ist der Servo unerreichbar.",
    "homing_offset": "Gehört zur Kalibrierung: über den Reiter „Kalibrierung“ ändern.",
    "min_position_limit": "Gehört zur Kalibrierung: über den Reiter „Kalibrierung“ ändern.",
    "max_position_limit": "Gehört zur Kalibrierung: über den Reiter „Kalibrierung“ ändern.",
}
VIA_PANEL = {
    "torque_enable": "Über „Drehmoment an/aus“: setzt vorher das Ziel auf die Istposition.",
    "goal_position": "Über den Schieberegler im Reiter „Einzelservo“: begrenzt auf Gelenkgrenzen.",
}

T = TypeVar("T")

UNITS = {NormMode.DEGREES: "°", NormMode.RANGE_0_100: "%", NormMode.RANGE_M100_100: "%"}


class ControlError(BackendError):
    """Aktion braucht das Steuerrecht, das ein anderer (oder niemand) hat."""


@dataclass(frozen=True)
class Event:
    id: int
    time: float  # Unix-Zeit
    level: str  # info | warn | error
    text: str


class DeviceSession:
    def __init__(
        self,
        name: str,
        arm: Arm,
        port: str | None,
        *,
        watchdog_s: float = 0.4,
        poll_hz: float = 30.0,
        slow_every: int = 10,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.name = name
        self.arm = arm
        self.port = port
        self.watchdog_s = watchdog_s
        self.poll_hz = poll_hz
        self.slow_every = slow_every
        self.clock = clock

        self.bus_lock = threading.RLock()
        self._control_lock = threading.Lock()
        self.controller: str | None = None
        self._last_heartbeat = 0.0

        self.present: list[str] = []  # Gelenke, die beim Verbinden geantwortet haben
        self.missing: list[str] = []
        self._events: deque[Event] = deque(maxlen=50)
        self._event_ids = itertools.count(1)
        self._snapshot: dict[str, Any] = {}
        self._slow: dict[str, ServoState] = {}
        self._refresh_slow = True
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._cycle_hz = 0.0
        self._error: str | None = None
        self.calibration_run: CalibrationRun | None = None
        self.motion = {"velocity": arm.cfg.max_velocity, "acceleration": arm.cfg.acceleration}
        self.recorder: Recorder | None = None
        self.recording_source = ""
        self._recording_overwrite = False
        self._recording_offsets: dict[str, int] = {}
        self.player: Player | None = None
        self.teleop: TeleopFollow | None = None
        self.teleop_leader: DeviceSession | None = None
        self._latest: tuple[dict[str, int], float] | None = None  # letzte Positionen (für Teleoperation)
        self.last_recording: dict[str, Any] | None = None
        self.cameras: dict[str, LabCamera] = {}
        self.dataset: LabDataset | None = None
        self._last_targets: dict[str, int] | None = None  # letztes Ziel des Abspielers (Aktion im Dataset)

    # ------------------------------------------------------------ Start und Ende

    def start(self) -> None:
        """Antwortende Motoren feststellen und die Zustandsabfrage starten."""
        with self.bus_lock:
            for joint in self.arm.joints:
                if self.arm.backend.ping(joint.motor_id) is None:
                    self.missing.append(f"{joint.name} (ID {joint.motor_id})")
                else:
                    self.present.append(joint.name)
        if not self.present:
            raise BackendError("Kein Servo antwortet. Servo-Netzteil an? Kabel gesteckt? Richtiger Port?")
        self.event("info", f"Verbunden ({self.arm.backend.name}{', ' + self.port if self.port else ''}).")
        if self.missing:
            self.event("warn", f"Keine Antwort von: {', '.join(self.missing)}.")
        for spec in self.arm.cfg.cameras:
            try:
                self.cameras[spec.name] = open_camera(spec)
                self.event("info", f"Kamera '{spec.name}' geöffnet ({spec.source}).")
            except Exception as exc:
                self.event("warn", f"Kamera '{spec.name}' nicht verfügbar: {exc}")
        self._thread = threading.Thread(target=self._loop, name=f"poll-{self.name}", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Abfrage beenden, sicher zurücksetzen, Verbindung schließen."""
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
        with self._control_lock:
            self.controller = None
        self._abort_calibration("Verbindung getrennt")
        self._end_motion("Verbindung getrennt")
        self._close_dataset()
        for camera in self.cameras.values():
            camera.close()
        with self.bus_lock:
            try:
                self.arm.safe_shutdown()
            except Exception:
                logger.exception("Sicheres Zurücksetzen beim Trennen fehlgeschlagen")
            finally:
                self.arm.backend.disconnect()

    # ------------------------------------------------------------ Ereignisse

    def event(self, level: str, text: str) -> None:
        self._events.append(Event(next(self._event_ids), time.time(), level, text))
        log = logger.warning if level != "info" else logger.info
        log("%s: %s", self.name, text)

    # ------------------------------------------------------------ Steuerrecht

    def take_control(self, client_id: str, force: bool = False) -> None:
        with self._control_lock:
            previous = self.controller
            if previous not in (None, client_id) and not force:
                raise ControlError(
                    "Ein anderer Client steuert dieses Gerät. "
                    "Übernehmen nur ausdrücklich („Übernehmen erzwingen“)."
                )
            self.controller = client_id
            self._last_heartbeat = self.clock()
        if previous not in (None, client_id):
            # Beim Übernehmen nichts weiterfahren lassen, was der vorige Client befohlen hat.
            self._run_safely(lambda arm: arm.stop_wheels(), "Räder stoppen")
            self.event("warn", "Steuerrecht wurde von einem anderen Client übernommen.")
        elif previous is None:
            self.event("info", "Steuerrecht vergeben.")

    def release_control(self, client_id: str, reason: str = "Steuerrecht abgegeben") -> bool:
        """Steuerrecht abgeben und sicheren Zustand herstellen. False, wenn der Client nicht steuerte."""
        with self._control_lock:
            if self.controller != client_id:
                return False
            self.controller = None
        self.event("info", f"{reason}: sicherer Zustand ({self.arm.cfg.safe_state}).")
        self._make_safe()
        return True

    def heartbeat(self, client_id: str) -> None:
        with self._control_lock:
            if self.controller == client_id:
                self._last_heartbeat = self.clock()

    def require_control(self, client_id: str | None) -> None:
        if client_id is None or self.controller != client_id:
            raise ControlError("Dafür ist das Steuerrecht nötig. Erst „Steuerung übernehmen“.")

    def check_watchdog(self) -> bool:
        """Steuerrecht entziehen, wenn der Heartbeat zu lange fehlt. True, wenn ausgelöst."""
        with self._control_lock:
            if self.controller is None:
                return False
            silent = self.clock() - self._last_heartbeat
            if silent <= self.watchdog_s:
                return False
            self.controller = None
        self.event(
            "warn",
            f"Watchdog: {silent * 1000:.0f} ms ohne Heartbeat (Grenze {self.watchdog_s * 1000:.0f} ms). "
            f"Steuerrecht entzogen, sicherer Zustand ({self.arm.cfg.safe_state}).",
        )
        self._make_safe()
        return True

    # ------------------------------------------------------------ Aktionen

    def run(self, fn: Callable[[Arm], T]) -> T:
        """Eine Aktion mit exklusivem Buszugriff ausführen."""
        with self.bus_lock:
            result = fn(self.arm)
        self._refresh_slow = True
        return result

    def joint(self, name: str) -> Joint:
        if name not in self.present:
            raise BackendError(f"Gelenk '{name}' gibt es nicht oder es antwortet nicht.")
        return self.arm.spec.joint(name)

    def _require_free(self, client_id: str | None) -> None:
        """Steuerrecht nötig, und weder Kalibrierung noch Abspielen oder Teleoperation dürfen laufen."""
        self.require_control(client_id)
        run = self.calibration_run
        if run is not None and run.stage in (Stage.MIDDLE, Stage.RANGE):
            raise ControlError("Die Kalibrierung läuft. Erst speichern oder abbrechen.")
        if self.player is not None:
            raise ControlError("Eine Aufnahme wird gerade abgespielt. Erst stoppen.")
        if self.teleop is not None:
            raise ControlError("Die Teleoperation läuft. Erst stoppen.")

    def set_torque(self, client_id: str | None, enabled: bool, joints: list[str] | None = None) -> None:
        if enabled:
            self._require_free(client_id)
        else:
            self.require_control(client_id)  # Ausschalten geht immer, auch beim Kalibrieren
            self._stop_following("Drehmoment aus")
        names = [self.joint(n).name for n in joints] if joints else list(self.present)
        which = "alle Gelenke" if not joints else ", ".join(names)
        if enabled:
            self.run(lambda arm: arm.enable_torque(names))  # Ziel = Istposition, kein Sprung
            self.event("info", f"Drehmoment an ({which}).")
        else:
            self.run(lambda arm: arm.disable_torque(names))
            self.event("info", f"Drehmoment aus ({which}).")

    def set_motion(self, client_id: str | None, velocity: int, acceleration: int) -> dict[str, int]:
        """Tempo- und Beschleunigungsgrenze setzen (gilt für alle Gelenke, bis zur nächsten Änderung)."""
        self._require_free(client_id)
        self.run(lambda arm: arm.configure_motion(max_velocity=velocity, acceleration=acceleration))
        self.motion = {
            "velocity": max(1, min(3400, velocity)),
            "acceleration": max(1, min(254, acceleration)),
        }
        self.event(
            "info",
            f"Bewegungsgrenzen: {self.motion['velocity']} Schritt/s, Beschleunigung "
            f"{self.motion['acceleration']} × 100 Schritt/s².",
        )
        return dict(self.motion)

    def move(self, client_id: str | None, targets: dict[str, int]) -> dict[str, int]:
        """Zielpositionen (roh). `move_to` setzt vorher die Grenzen und begrenzt jedes Ziel."""
        self._require_free(client_id)
        for name in targets:
            self.joint(name)
        if not self.arm.motion_ready:
            self.run(lambda arm: arm.configure_motion(**self._motion_kwargs()))
        return self.run(lambda arm: arm.move_to({n: int(v) for n, v in targets.items()}))

    def _motion_kwargs(self) -> dict[str, int]:
        return {"max_velocity": self.motion["velocity"], "acceleration": self.motion["acceleration"]}

    # ------------------------------------------------------------ Register-Explorer

    def read_registers(self, joint_name: str) -> list[dict[str, Any]]:
        """Alle Register eines Gelenks lesen (darf jeder, verändert nichts)."""
        servo = self.joint(joint_name).motor_id
        rows = []
        with self.bus_lock:
            for reg in sorted(regs.registers(), key=lambda r: r.address):
                try:
                    value: int | None = self.arm.backend.read_register(servo, reg.name)
                except BackendError:
                    value = None
                meaning = info(reg.name)
                write, reason = self._write_rule(reg)
                rows.append(
                    {
                        "address": reg.address,
                        "name": reg.name,
                        "size": reg.size,
                        "area": reg.area.value,
                        "writable": reg.writable,
                        "value": value,
                        "unit": meaning.unit,
                        "text": meaning.text,
                        "write": write,  # ram | eeprom | panel | locked
                        "reason": reason,
                    }
                )
        return rows

    @staticmethod
    def _write_rule(reg: regs.Register) -> tuple[str, str]:
        if not reg.writable:
            return "locked", "nur lesbar"
        if reg.name in VIA_PANEL:
            return "panel", VIA_PANEL[reg.name]
        if reg.area is regs.Area.RAM:
            if reg.name in regs.RAM_WRITE_WHITELIST:
                return "ram", "RAM, freigegeben (gilt bis zum Ausschalten)"
            return "locked", "RAM, nicht auf der Whitelist"
        if reg.area is regs.Area.EEPROM:
            if reg.name in EEPROM_LOCKED:
                return "locked", EEPROM_LOCKED[reg.name]
            return "eeprom", "EEPROM: dauerhaft, nur mit Bestätigung"
        return "locked", "nicht in der Feetech-Tabelle V3.7, Schreiben gesperrt"

    def write_register(
        self, client_id: str | None, joint_name: str, name: str, value: int, confirm_eeprom: bool = False
    ) -> int:
        """Ein Register schreiben und zurücklesen. Liefert den zurückgelesenen Wert."""
        self._require_free(client_id)
        joint = self.joint(joint_name)
        reg = regs.lookup(name)
        rule, reason = self._write_rule(reg)
        if rule in ("locked", "panel"):
            raise BackendError(f"'{reg.name}' wird hier nicht geschrieben: {reason}")
        if rule == "eeprom":
            if not confirm_eeprom:
                raise BackendError(
                    f"'{reg.name}' liegt im EEPROM. Schreiben nur mit ausdrücklicher Bestätigung."
                )
            self.run(lambda arm: arm.backend.write_eeprom(joint.motor_id, reg.name, value))  # prüft selbst
            readback = self.run(lambda arm: arm.backend.read_register(joint.motor_id, reg.name))
            self.event(
                "warn", f"EEPROM: {joint.name}.{reg.name} = {readback} (dauerhaft, Drehmoment dabei aus)."
            )
            return readback

        def write_and_read(arm: Arm) -> int:
            write_ram_register(arm.backend, joint.motor_id, reg.name, value)
            return arm.backend.read_register(joint.motor_id, reg.name)

        readback = self.run(write_and_read)
        if readback != value:
            raise BackendError(f"Zurückgelesen {readback}, geschrieben {value}.")
        self.event("info", f"RAM: {joint.name}.{reg.name} = {readback}.")
        return readback

    # ------------------------------------------------------------ Kalibrierung

    def calibration_start(self, client_id: str | None) -> dict[str, Any]:
        self._require_free(client_id)
        if self.missing:
            raise BackendError(
                f"Kalibrieren geht nur, wenn alle Motoren antworten. Es fehlen: {', '.join(self.missing)}."
            )
        self.calibration_run = self.run(CalibrationRun.start)
        self.event("info", "Kalibrierung gestartet: Drehmoment aus. Gelenke in die Mitte stellen.")
        return self.calibration_run.status()

    # ------------------------------------------------------------ Kalibrierdatei und Servos

    def _calibrating(self) -> bool:
        run = self.calibration_run
        return run is not None and run.stage in (Stage.MIDDLE, Stage.RANGE)

    def reload_calibration(self, client_id: str | None = None, quiet: bool = False) -> dict[str, Any]:
        """Kalibrierdatei neu suchen und laden (nach Löschen, Wiederherstellen, Import)."""
        if self._calibrating():
            raise ControlError("Die Kalibrierung läuft. Erst speichern oder abbrechen.")
        spec, cfg = self.arm.spec, self.arm.cfg
        path = find_calibration_file(spec, cfg.calibration_id, cfg.calibration_dir)
        before = self.arm.calibration_path
        self.arm.calibration = load_calibration(path) if path else None
        self.arm.calibration_path = path
        if not quiet or path != before:
            self.event(
                "info" if path else "warn",
                f"Kalibrierung neu geladen: {path}" if path else "Keine Kalibrierdatei mehr: Grenzen 0…4095.",
            )
        return {"calibration_path": str(path) if path else None}

    def compare_calibration(self) -> dict[str, Any]:
        """Datei gegen die Werte im EEPROM der Servos (wie LeRobots `is_calibrated`). Liest nur."""
        cal = self.arm.calibration
        rows = []
        with self.bus_lock:
            for name in self.present:
                joint = self.arm.spec.joint(name)
                servo = {}
                for reg in ("homing_offset", "min_position_limit", "max_position_limit"):
                    try:
                        servo[reg] = self.arm.backend.read_register(joint.motor_id, reg)
                    except BackendError:
                        servo[reg] = None
                c = cal.get(name) if cal else None
                expected = (
                    {
                        "homing_offset": c.homing_offset,
                        "min_position_limit": c.range_min,
                        "max_position_limit": c.range_max,
                    }
                    if c
                    else None
                )
                rows.append(
                    {
                        "name": name,
                        "file": expected,
                        "servo": servo,
                        "match": expected is not None and expected == servo,
                    }
                )
        return {
            "calibration_path": str(self.arm.calibration_path) if self.arm.calibration_path else None,
            "joints": rows,
            "all_match": bool(rows) and all(r["match"] for r in rows),
        }

    def apply_calibration(self, client_id: str | None) -> dict[str, Any]:
        """Werte der geladenen Datei ins EEPROM der Servos schreiben (nur geänderte, mit Prüfung).

        So macht es auch `lerobot-calibrate`, wenn man eine vorhandene Datei übernimmt. Nötig,
        wenn Datei und Servos nicht zusammenpassen (andere Datei wiederhergestellt, Servo getauscht).
        Das Drehmoment der betroffenen Servos geht dabei aus.
        """
        self._require_free(client_id)
        if self.recorder is not None:
            raise ControlError("Erst die laufende Aufnahme beenden.")
        cal = self.arm.calibration
        if not cal:
            raise BackendError("Keine Kalibrierdatei geladen, nichts zu schreiben.")
        written: list[str] = []

        def write(arm: Arm) -> None:
            for name in self.present:
                c = cal.get(name)
                if c is None:
                    continue
                servo = arm.spec.joint(name).motor_id
                for reg, value in (
                    ("homing_offset", c.homing_offset),
                    ("min_position_limit", c.range_min),
                    ("max_position_limit", c.range_max),
                ):
                    if arm.backend.read_register(servo, reg) != value:
                        arm.backend.write_eeprom(servo, reg, value)  # Drehmoment aus, schreiben, zurücklesen
                        written.append(f"{name}.{reg}={value}")

        self.run(write)
        if written:
            self.event(
                "warn", "Kalibrierdatei in die Servos geschrieben (EEPROM): " + ", ".join(written) + "."
            )
        else:
            self.event("info", "Servos und Kalibrierdatei stimmen schon überein, nichts geschrieben.")
        return {"written": written, **self.compare_calibration()}

    def _calibration(self, client_id: str | None, stage: Stage) -> CalibrationRun:
        self.require_control(client_id)
        run = self.calibration_run
        if run is None or run.stage is not stage:
            raise BackendError("Dieser Kalibrierschritt passt gerade nicht. Kalibrierung neu starten.")
        return run

    def calibration_middle(self, client_id: str | None) -> dict[str, Any]:
        run = self._calibration(client_id, Stage.MIDDLE)
        offsets = self.run(lambda arm: run.set_middle())
        self.event(
            "warn",
            "Kalibrierung: Mitte übernommen, Homing-Offsets ins EEPROM geschrieben: "
            + ", ".join(f"{n} {o}" for n, o in offsets.items())
            + ".",
        )
        return run.status()

    def calibration_finish(self, client_id: str | None) -> dict[str, Any]:
        run = self._calibration(client_id, Stage.RANGE)
        path = self.run(lambda arm: run.finish())
        self.event("info", f"Kalibrierung gespeichert: {path}. Grenzen sind ab sofort aktiv.")
        return run.status()

    def calibration_cancel(self, client_id: str | None) -> dict[str, Any] | None:
        self.require_control(client_id)
        self._abort_calibration("Abgebrochen")
        return self.calibration_run.status() if self.calibration_run else None

    def _abort_calibration(self, reason: str) -> None:
        run = self.calibration_run
        if run is None or run.stage not in (Stage.MIDDLE, Stage.RANGE):
            return
        try:
            self.run(lambda arm: run.cancel())
            self.event("warn", f"Kalibrierung abgebrochen ({reason}). Alte Werte wiederhergestellt.")
        except Exception as exc:
            self.event("error", f"Kalibrierung abgebrochen ({reason}), Zurückschreiben fehlgeschlagen: {exc}")

    # ------------------------------------------------------------ Aufnehmen

    def start_recording(self, client_id: str | None, name: str, overwrite: bool = False) -> dict[str, Any]:
        """Aufnahme starten. Ohne Teleoperation geht das Drehmoment aus: Arm von Hand führen."""
        self.require_control(client_id)
        if self.recorder is not None:
            raise ControlError("Es läuft schon eine Aufnahme.")
        if self.player is not None:
            raise ControlError("Während des Abspielens wird nicht aufgenommen. Erst stoppen.")
        run = self.calibration_run
        if run is not None and run.stage in (Stage.MIDDLE, Stage.RANGE):
            raise ControlError("Die Kalibrierung läuft. Erst speichern oder abbrechen.")
        try:
            check_new_name(name, overwrite=overwrite)
        except TrajectoryError as exc:
            raise BackendError(str(exc)) from None
        joints = [n for n in self.present if self.arm.spec.joint(n).mode is JointMode.POSITION]
        if self.teleop is not None:
            self.recording_source = f"Teleoperation, Leader '{self.teleop.leader}'"
        else:
            self.run(lambda arm: arm.disable_torque(joints))
            self.recording_source = "von Hand geführt (Drehmoment aus)"
        self._recording_overwrite = overwrite
        self._recording_offsets = self.run(lambda arm: arm.read_homing_offsets(joints))
        self.recorder = Recorder(name, joints, started=self.clock())
        self.event("info", f"Aufnahme '{name}' läuft ({self.recording_source}).")
        return self.recorder.status(self.clock())

    def stop_recording(self, client_id: str | None) -> dict[str, Any] | None:
        self.require_control(client_id)
        if self.recorder is None:
            raise BackendError("Es läuft keine Aufnahme.")
        self._finish_recording(None)
        return self.last_recording

    def _finish_recording(self, interrupted: str | None) -> None:
        """Aufnahme beenden und speichern (auch bei Unterbrechung, dann mit Grund im JSON)."""
        rec, self.recorder = self.recorder, None
        if rec is None:
            return
        if len(rec.times) < 2:
            self.event("warn", f"Aufnahme '{rec.name}' verworfen: zu kurz.")
            self.last_recording = {"name": rec.name, "saved": False}
            return
        meta = {
            "device": self.name,
            "device_type": self.arm.spec.type,
            "calibration_id": self.arm.cfg.calibration_id,
            "calibration_file": str(self.arm.calibration_path) if self.arm.calibration_path else None,
            "calibration": calibration_to_dict(self.arm.calibration),
            # Offsets im Servo beim Aufnehmen: Rohwerte hängen davon ab (motion.prepare_playback)
            "homing_offsets": self._recording_offsets,
            "target_rate_hz": self.poll_hz,
            "source": self.recording_source,
            "recorded_with": "web",
        }
        if interrupted:
            meta["interrupted"] = interrupted
        traj = rec.trajectory(meta)
        try:
            csv_path, _ = save_trajectory(traj, overwrite=self._recording_overwrite)
        except (TrajectoryError, OSError) as exc:
            self.event("error", f"Aufnahme '{rec.name}' nicht gespeichert: {exc}")
            self.last_recording = {"name": rec.name, "saved": False, "error": str(exc)}
            return
        rate = traj.sample_rate_hz
        why = f" (unterbrochen: {interrupted})" if interrupted else ""
        self.event(
            "warn" if interrupted else "info",
            f"Aufnahme '{rec.name}' gespeichert{why}: {len(rec.times)} Abtastungen, {traj.duration_s:.1f} s, "
            f"{rate or 0:.1f} Hz → {csv_path}",
        )
        self.last_recording = {
            "name": rec.name,
            "saved": True,
            "path": str(csv_path),
            "interrupted": interrupted,
        }

    # ------------------------------------------------------------ Abspielen

    def _plan_playback(self, name: str) -> tuple[Any, PlaybackPlan]:
        try:
            traj = load_trajectory(check_name(name))
        except TrajectoryError as exc:
            raise BackendError(str(exc)) from None
        joints = [n for n in self.present if self.arm.spec.joint(n).mode is JointMode.POSITION]
        offsets = self.run(lambda arm: arm.read_homing_offsets(joints))
        try:
            plan = prepare_playback(
                traj,
                self.arm.spec,
                self.arm.calibration,
                self.arm.limits,
                self.arm.cfg.calibration_id,
                current_offsets=offsets,
            )
        except ValueError as exc:
            raise BackendError(str(exc)) from None
        missing = [j for j in plan.joints if j not in self.present]
        if missing:
            raise BackendError(f"Diese Gelenke antworten nicht: {', '.join(missing)}.")
        return traj, plan

    def check_playback(self, name: str, speed: float = 1.0) -> dict[str, Any]:
        """Was beim Abspielen passieren würde (Anfahrt, Dauer, Hinweise). Bewegt nichts."""
        if not 0.1 <= speed <= 3:
            raise BackendError("Tempofaktor muss zwischen 0,1 und 3 liegen.")
        traj, plan = self._plan_playback(name)
        present = self.run(lambda arm: arm.read_positions(plan.joints))
        approach = approach_duration(present, plan.rows[0])
        return {
            "name": traj.name,
            "approach_s": round(approach, 1),
            "play_s": round(traj.duration_s / speed, 1),
            "conversion": plan.converter.describe(),
            "calibrated": self.arm.calibration is not None,
            "notes": plan.notes,
        }

    def start_playback(self, client_id: str | None, name: str, speed: float = 1.0) -> dict[str, Any]:
        self._require_free(client_id)
        if self.recorder is not None:
            raise ControlError("Erst die laufende Aufnahme beenden.")
        if not 0.1 <= speed <= 3:
            raise BackendError("Tempofaktor muss zwischen 0,1 und 3 liegen.")
        traj, plan = self._plan_playback(name)

        def begin(arm: Arm) -> Player:
            if not arm.motion_ready:
                arm.configure_motion(**self._motion_kwargs())
            arm.enable_torque(plan.joints)  # Ziel = Istposition, kein Sprung
            present = arm.read_positions(plan.joints)
            return Player(traj.name, plan.joints, traj.times, plan.rows, present, self.clock(), speed)

        self.player = self.run(begin)
        how = plan.converter.describe()
        self.event(
            "info",
            f"Spiele '{traj.name}' ab (×{speed:g}): Anfahrt {self.player.approach_s:.1f} s, "
            f"dann {self.player.play_s:.1f} s. {how[:1].upper()}{how[1:]}.",
        )
        for note in plan.notes:
            self.event("warn", f"'{traj.name}': {note}")
        return self.player.status(self.clock())

    def stop_playback(self, client_id: str | None) -> None:
        self.require_control(client_id)
        if self.player is None:
            raise BackendError("Es wird nichts abgespielt.")
        self._stop_following("Abspielen gestoppt", hold=True)

    # ------------------------------------------------------------ Teleoperation

    def start_teleop(self, client_id: str | None, leader: DeviceSession) -> dict[str, Any]:
        """Dieses Gerät folgt `leader`. Der Leader wird nur gelesen."""
        self._require_free(client_id)
        if leader is self:
            raise BackendError("Leader und Follower müssen verschiedene Geräte sein.")
        converter = Converter(leader.arm.spec, leader.arm.calibration, self.arm.spec, self.arm.calibration)
        joints = [j for j in converter.mapping if j in self.present]
        if not joints:
            raise BackendError(f"'{leader.name}' und '{self.name}' haben keine gemeinsamen Gelenke.")
        if leader.latest() is None:
            raise BackendError(f"Vom Leader '{leader.name}' kommen noch keine Positionen.")

        def begin(arm: Arm) -> TeleopFollow:
            if not arm.motion_ready:
                arm.configure_motion(**self._motion_kwargs())
            present = arm.read_positions(joints)
            follow = TeleopFollow(leader.name, converter, present, self.clock())  # prüft die Kalibrierung
            arm.enable_torque(joints)
            return follow

        try:
            self.teleop = self.run(begin)
        except ValueError as exc:
            raise BackendError(str(exc)) from None
        self.teleop_leader = leader
        self.event(
            "info", f"Teleoperation: folgt '{leader.name}' ({converter.describe()}). Erst sanfte Anfahrt."
        )
        return self.teleop.status(self.clock())

    def stop_teleop(self, client_id: str | None) -> None:
        self.require_control(client_id)
        if self.teleop is None:
            raise BackendError("Die Teleoperation läuft nicht.")
        self._stop_following("Teleoperation gestoppt", hold=True)

    def latest(self) -> tuple[dict[str, int], float] | None:
        """Zuletzt gelesene Positionen und Zeitpunkt (für einen Follower, der diesem Gerät folgt)."""
        return None if self._stop.is_set() else self._latest

    # ------------------------------------------------------------ LeRobot-Dataset

    def dataset_open(self, client_id: str | None, name: str, task: str, fps: int = 30) -> dict[str, Any]:
        """Dataset anlegen oder ein vorhandenes gleichen Namens zum Weiterschreiben öffnen."""
        self.require_control(client_id)
        if self.dataset is not None:
            raise ControlError(f"Das Dataset '{self.dataset.name}' ist schon geöffnet. Erst abschließen.")
        if not self.arm.is_calibrated:
            raise BackendError(
                "Für ein LeRobot-Dataset braucht das Gerät eine Kalibrierung: Gespeichert werden Grad und "
                "Prozent wie bei LeRobot, nicht Rohwerte."
            )
        joints = [n for n in self.present if self.arm.spec.joint(n).mode is JointMode.POSITION]
        try:
            self.dataset = LabDataset(name, joints, self.cameras, fps, self.arm.spec.lerobot_name, task)
        except DatasetError as exc:
            raise BackendError(str(exc)) from None
        how = "weitergeschrieben" if self.dataset.resumed else "angelegt"
        cams = ", ".join(self.cameras) or "keine Kamera"
        self.event("info", f"Dataset '{name}' {how} ({fps} fps, {cams}): {self.dataset.path}")
        return self.dataset.status()

    def _dataset_values(self) -> tuple[dict[str, float], dict[str, float]] | None:
        """Beobachtung und Aktion in Grad/Prozent (Aufruf aus dem Dataset-Thread)."""
        latest = self._latest
        if latest is None or self.clock() - latest[1] > 0.2:
            return None
        dataset = self.dataset
        if dataset is None:
            return None
        positions = {j: latest[0][j] for j in dataset.joints if j in latest[0]}
        obs = joint_values(self.arm.to_joint_values(positions))
        if obs is None or len(obs) != len(dataset.joints):
            return None
        action = obs
        teleop, leader, player = self.teleop, self.teleop_leader, self.player
        if teleop is not None and leader is not None:
            lead = leader.latest()
            if lead is None:
                return None
            raw = {src: lead[0][src] for dst, src in teleop.converter.mapping.items() if src in lead[0]}
            values = joint_values(leader.arm.to_joint_values(raw))
            if values is None:
                return None
            action = {dst: values[src] for dst, src in teleop.converter.mapping.items() if src in values}
        elif player is not None and self._last_targets:
            targets = joint_values(self.arm.to_joint_values(self._last_targets))
            if targets is not None:
                action = {**obs, **targets}
        if any(j not in action for j in dataset.joints):
            return None
        return obs, action

    def episode_start(self, client_id: str | None) -> dict[str, Any]:
        self.require_control(client_id)
        if self.dataset is None:
            raise BackendError("Erst ein Dataset öffnen.")
        try:
            self.dataset.start_episode(self._dataset_values)
        except DatasetError as exc:
            raise BackendError(str(exc)) from None
        source = (
            f"Aktion vom Leader '{self.teleop.leader}'"
            if self.teleop
            else "Aktion = Ziel des Abspielers"
            if self.player
            else "von Hand, Aktion = Beobachtung"
        )
        self.event("info", f"Episode {self.dataset.episode.index} läuft ({source}).")  # type: ignore[union-attr]
        return self.dataset.status()

    def episode_stop(self, client_id: str | None, keep: bool = True) -> dict[str, Any]:
        self.require_control(client_id)
        if self.dataset is None or self.dataset.episode is None:
            raise BackendError("Es läuft keine Episode.")
        episode = self.dataset.stop_episode(keep=keep)
        if keep and episode.frames >= 2:
            self.event(
                "info",
                f"Episode {episode.index} gespeichert: {episode.frames} Frames, {episode.duration_s:.1f} s"
                + (
                    f", {episode.skipped} Zeitschritte ausgelassen (Kamera zu langsam?)"
                    if episode.skipped
                    else ""
                )
                + ".",
            )
        else:
            self.event("warn", f"Episode {episode.index} verworfen.")
        return self.dataset.status()

    def dataset_close(self, client_id: str | None) -> None:
        self.require_control(client_id)
        if self.dataset is None:
            raise BackendError("Es ist kein Dataset geöffnet.")
        self._close_dataset()

    def _discard_episode(self, reason: str) -> None:
        dataset = self.dataset
        if dataset is not None and dataset.episode is not None:
            try:
                dataset.stop_episode(keep=False)
                self.event("warn", f"Episode verworfen ({reason}).")
            except Exception as exc:
                self.event("error", f"Episode verwerfen fehlgeschlagen: {exc}")

    def _close_dataset(self) -> None:
        dataset, self.dataset = self.dataset, None
        if dataset is None:
            return
        try:
            dataset.close()
            self.event("info", f"Dataset '{dataset.name}' abgeschlossen: {dataset.saved_episodes} Episoden.")
        except Exception as exc:
            self.event("error", f"Dataset '{dataset.name}' abschließen fehlgeschlagen: {exc}")

    # ------------------------------------------------------------ Bewegungsabläufe beenden

    def _stop_following(self, reason: str, hold: bool = False) -> None:
        """Abspielen und Teleoperation beenden. `hold`: Stellung halten (Drehmoment bleibt an)."""
        was = "Abspielen" if self.player else "Teleoperation" if self.teleop else None
        self.player = None
        self.teleop = None
        self.teleop_leader = None
        if was is None:
            return
        if hold:
            self._run_safely(lambda arm: arm.hold(), "Stellung halten")
        self.event("info", f"{was} beendet: {reason}.")

    def _end_motion(self, reason: str) -> None:
        """Bei Verlust des Steuerrechts: alles beenden, Aufnahme bis hierher speichern."""
        self._stop_following(reason)
        if self.recorder is not None:
            self._finish_recording(reason)
        self._discard_episode(reason)

    def _motion_tick(self, positions: dict[str, int], now: float) -> None:
        """Einmal je Abfragerunde: aufnehmen, abspielen oder folgen."""
        rec = self.recorder
        if rec is not None and not rec.add(positions, now):
            self._finish_recording(f"Höchstdauer {rec.max_s:.0f} s erreicht")
        player, teleop = self.player, self.teleop
        if player is None and teleop is None:
            return
        try:
            if player is not None:
                targets = player.targets(now)
                self._last_targets = targets
                with self.bus_lock:
                    self.arm.move_to(targets)
                if player.finished and self.player is player:
                    self.player = None
                    self.event("info", f"'{player.name}' fertig abgespielt. Der Arm hält die Endstellung.")
            elif teleop is not None:
                leader = self.teleop_leader
                latest = leader.latest() if leader is not None else None
                if latest is None or now - latest[1] > LEADER_TIMEOUT_S:
                    self._stop_following(
                        "keine aktuellen Werte vom Leader (getrennt oder Lesefehler)", hold=True
                    )
                    return
                with self.bus_lock:
                    self.arm.move_to(teleop.targets(latest[0], now))
        except BackendError as exc:
            self.player = None
            self.teleop = None
            self.event("error", f"Bewegung abgebrochen: {exc}. Sicherer Zustand.")
            self._run_safely(lambda arm: arm.safe_shutdown(), "Sicherer Zustand")

    def emergency_stop(self) -> None:
        with self._control_lock:
            self.controller = None
        self._stop_following("Not-Aus")
        with self.bus_lock:
            self.arm.emergency_stop()  # meldet Fehler je Motor selbst ins Log, wirft nicht
        self._refresh_slow = True
        self.event("error", "NOT-AUS: Räder stopp, Drehmoment aus.")
        self._abort_calibration("Not-Aus")
        if self.recorder is not None:
            self._finish_recording("Not-Aus")
        self._discard_episode("Not-Aus")

    def _make_safe(self) -> None:
        self._abort_calibration("Steuerrecht verloren")
        self._end_motion("Steuerrecht verloren")
        self._run_safely(lambda arm: arm.safe_shutdown(), "Sicherer Zustand")

    def _run_safely(self, fn: Callable[[Arm], object], label: str) -> None:
        try:
            self.run(fn)
        except Exception as exc:
            self.event("error", f"{label} fehlgeschlagen: {exc}. Im Zweifel Netzteil aus!")

    # ------------------------------------------------------------ Zustand

    def _loop(self) -> None:
        period = 1.0 / self.poll_hz
        next_due = self.clock()
        cycles, window_start = 0, self.clock()
        i = 0
        while not self._stop.is_set():
            self.check_watchdog()
            try:
                with self.bus_lock:
                    positions = self.arm.read_positions(self.present)
                    if self._refresh_slow or i % self.slow_every == 0:
                        self._refresh_slow = False
                        self._slow = self.arm.read_state(self.present)
                self._error = None
                now = self.clock()
                self._latest = (positions, now)
                run = self.calibration_run
                if run is not None:
                    run.track(positions)  # zeichnet nur im Schritt „Bereich“ auf
                self._motion_tick(positions, now)
                self._snapshot_joints(positions)
            except BackendError as exc:
                if self._error != str(exc):
                    self.event("error", f"Lesen fehlgeschlagen: {exc}")
                self._error = str(exc)
            except Exception as exc:  # nie den Thread sterben lassen
                logger.exception("Unerwarteter Fehler in der Zustandsabfrage")
                self._error = f"Unerwarteter Fehler: {exc}"
            i += 1
            cycles += 1
            now = self.clock()
            if now - window_start >= 1.0:
                self._cycle_hz = cycles / (now - window_start)
                cycles, window_start = 0, now
            next_due += period
            wait = next_due - self.clock()
            if wait < 0:
                next_due = self.clock()  # zu langsam: nicht aufholen, sondern neu takten
            else:
                self._stop.wait(wait)

    def _snapshot_joints(self, positions: dict[str, int]) -> None:
        values = self.arm.to_joint_values(positions)
        joints = []
        for name in self.present:
            joint = self.arm.spec.joint(name)
            slow = self._slow.get(name)
            low, high = self.arm.limits(name)
            joints.append(
                {
                    "name": name,
                    "id": joint.motor_id,
                    "mode": joint.mode.value,
                    "position": positions[name],
                    "value": None if values[name] is None else round(values[name], 1),
                    "unit": UNITS.get(joint.norm, "") if joint.mode is JointMode.POSITION else "",
                    "limits": [low, high],
                    "velocity": slow.velocity if slow else None,
                    "load": slow.load if slow else None,
                    "voltage": slow.voltage if slow else None,
                    "temperature": slow.temperature if slow else None,
                    "current_ma": slow.current_ma if slow else None,
                    "torque": slow.torque_enabled if slow else None,
                    "errors": slow.errors if slow else [],
                }
            )
        self._snapshot = {"joints": joints, "time": time.time()}

    def snapshot(self) -> dict[str, Any]:
        """Alles, was der Browser zur Anzeige braucht. Wird vom Abfrage-Thread aktuell gehalten."""
        cal = self.arm.calibration_path
        return {
            "name": self.name,
            "type": self.arm.spec.type,
            "description": self.arm.spec.description,
            "backend": self.arm.backend.name,
            "port": self.port,
            "calibration_id": self.arm.cfg.calibration_id,
            "calibration_path": str(cal) if cal else None,
            "calibrated": self.arm.is_calibrated,
            "safe_state": self.arm.cfg.safe_state,
            "watchdog_ms": round(self.watchdog_s * 1000),
            "controller": self.controller,
            "cycle_hz": round(self._cycle_hz, 1),
            "error": self._error,
            "missing": list(self.missing),
            "events": [asdict(e) for e in list(self._events)[-20:]],
            "motion": dict(self.motion),
            "calibration": self.calibration_run.status() if self.calibration_run else None,
            "calibration_target": str(target_path(self.arm)),
            "recording": self.recorder.status(self.clock()) if self.recorder else None,
            "recording_source": self.recording_source if self.recorder else None,
            "last_recording": self.last_recording,
            "playback": self.player.status(self.clock()) if self.player else None,
            "teleop": self.teleop.status(self.clock()) if self.teleop else None,
            "cameras": [c.status() for c in self.cameras.values()],
            "camera_config": [s.name for s in self.arm.cfg.cameras],
            "dataset": self.dataset.status() if self.dataset else None,
            **self._snapshot,
        }
