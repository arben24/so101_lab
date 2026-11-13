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

from ..arm import Arm
from ..backends.base import BackendError, ServoState
from ..devices.spec import JointMode, NormMode

logger = logging.getLogger(__name__)

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
        self._thread = threading.Thread(target=self._loop, name=f"poll-{self.name}", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Abfrage beenden, sicher zurücksetzen, Verbindung schließen."""
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
        with self._control_lock:
            self.controller = None
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

    def set_torque(self, client_id: str | None, enabled: bool) -> None:
        self.require_control(client_id)
        if enabled:
            self.run(lambda arm: arm.enable_torque(self.present))  # Ziel = Istposition, kein Sprung
            self.event("info", "Drehmoment an.")
        else:
            self.run(lambda arm: arm.disable_torque(self.present))
            self.event("info", "Drehmoment aus.")

    def emergency_stop(self) -> None:
        with self._control_lock:
            self.controller = None
        with self.bus_lock:
            self.arm.emergency_stop()  # meldet Fehler je Motor selbst ins Log, wirft nicht
        self._refresh_slow = True
        self.event("error", "NOT-AUS: Räder stopp, Drehmoment aus.")

    def _make_safe(self) -> None:
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
            **self._snapshot,
        }
