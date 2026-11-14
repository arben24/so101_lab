"""Servo-IDs vergeben: ein Servo nach dem anderen, mit Prüfung und Protokoll.

Gemeinsamer Ablauf für den Einrichtungsassistenten der Web-App (Reiter „Einrichtung“) und
Skript 05. Vorbild ist LeRobot (`SerialMotorsBus.setup_motor`, `lerobot-setup-motors`).

Je Servo:

1. **Suchen** (`scan`): alle Baudraten, die schnellste zuerst (über USB etwa 8 s). Es muss
   genau **ein** Servo antworten (`single`).
2. **Ein-Servo-Probe**: den gefundenen Servo mehrmals nach seiner Position fragen. Zwei
   Servos mit derselben ID (zum Beispiel zwei neue mit ID 1) antworten beide gleichzeitig.
   Ihre Antworten überlagern sich und kommen kaputt an. Eine Suche bemerkt das nicht immer,
   wiederholtes Lesen meistens. Ganz sicher ist nur: vorher alle anderen Servos abziehen.
   Ziel ist entweder ein Gelenk des Gerätetyps (`gripper` → ID 6) oder eine **freie ID**
   (`servo_id=…`, 0–253), etwa für Ersatzservos oder eigene Aufbauten.
3. **Schreiben** (`assign`): bei der Baudrate des Servos zuerst die ID ändern, dann die
   Baudrate auf 1 Mbit/s stellen (Reihenfolge wie LeRobot). `change_id` und
   `change_baudrate` im Backend prüfen selbst per Ping.
4. **Prüfen**: Register `id` und `baud_rate` zurücklesen, dann bei 1 Mbit/s suchen: Es darf
   nur dieser Servo unter der neuen ID antworten.
5. **Neustart-Probe** (`verify_after_restart`, empfohlen): Servo kurz stromlos machen und
   wieder suchen. Erst das zeigt, dass die Werte dauerhaft im EEPROM stehen.
6. **Protokoll**: jede Aktion als Zeile in `logs/servo_ids.csv`.
"""

from __future__ import annotations

import csv
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from . import registers as regs
from .backends.base import DEFAULT_BAUDRATE, ArmBackend, BackendError, FoundServo
from .devices import get_device_spec
from .devices.spec import DeviceSpec, Joint, JointMode
from .paths import servo_setup_log_path

PROBE_READS = 10  # so oft wird der gefundene Servo vor dem Schreiben gelesen

LOG_FIELDS = (
    "zeit",
    "aktion",
    "ergebnis",
    "geraetetyp",
    "gelenk",
    "alte_id",
    "alte_baudrate",
    "neue_id",
    "neue_baudrate",
    "modell",
    "firmware",
    "port",
    "adapter_sn",
    "meldung",
)


class SetupError(BackendError):
    """Eine Vorbedingung fehlt (kein oder mehr als ein Servo …). Es wurde nichts geschrieben."""


@dataclass(frozen=True)
class Check:
    text: str
    ok: bool


@dataclass(frozen=True)
class Target:
    """Wohin der Servo soll: eine ID, dazu das Gelenk, falls die ID zum Gerätetyp gehört."""

    id: int
    joint: str | None = None

    @property
    def label(self) -> str:
        return f"ID {self.id} ({self.joint})" if self.joint else f"ID {self.id}"


@dataclass
class SetupResult:
    action: str  # "id_gesetzt" | "unveraendert" | "neustart"
    joint: str  # leer bei einer freien ID, die zu keinem Gelenk gehört
    new_id: int
    old_id: int | None = None
    old_baudrate: int | None = None
    baudrate: int | None = None  # Baudrate danach
    model: str | None = None
    firmware: str | None = None
    checks: list[Check] = field(default_factory=list)
    ok: bool = False
    message: str = ""
    time: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def setup_order(spec: DeviceSpec) -> list[Joint]:
    """Reihenfolge wie `lerobot-setup-motors`: Arm vom Greifer zur Schulter, dann Räder rückwärts."""
    arm = [j for j in spec.joints if j.mode is JointMode.POSITION]
    wheels = [j for j in spec.joints if j.mode is JointMode.VELOCITY]
    return list(reversed(arm)) + list(reversed(wheels))


def describe(servo: FoundServo) -> str:
    return f"ID {servo.id}, {servo.model_name}, {servo.baudrate} Baud, Firmware {servo.firmware or '?'}"


class ServoSetup:
    """ID-Vergabe für die Servos eines Gerätetyps an einem Bus mit genau einem Servo."""

    def __init__(
        self,
        backend: ArmBackend,
        device_type: str,
        *,
        port: str | None = None,
        adapter_sn: str | None = None,
        log_path: Path | None = None,
    ):
        self.backend = backend
        self.spec = get_device_spec(device_type)
        self.port = port
        self.adapter_sn = adapter_sn
        self.log_path = log_path if log_path is not None else servo_setup_log_path()
        self.order = setup_order(self.spec)
        self.found: list[FoundServo] | None = None  # Ergebnis der letzten Suche
        self.status: dict[str, str] = {j.name: "offen" for j in self.order}  # offen|gesetzt|dauerhaft|fehler
        self.last: SetupResult | None = None

    # ------------------------------------------------------------ Suchen

    def target(self, joint_name: str | None = None, servo_id: int | None = None) -> Target:
        """Ziel aus Gelenkname **oder** freier ID. Eine freie ID, die zu einem Gelenk gehört,
        zählt als dieses Gelenk (Reihenfolge-Tabelle wird mitgeführt)."""
        if (joint_name is None) == (servo_id is None):
            raise SetupError("Entweder ein Gelenk oder eine freie ID angeben.")
        if joint_name is not None:
            try:
                joint = self.spec.joint(joint_name)
            except KeyError as exc:
                raise SetupError(str(exc.args[0])) from None
            return Target(joint.motor_id, joint.name)
        if not 0 <= int(servo_id) <= 253:
            raise SetupError(f"ID {servo_id} ungültig. Erlaubt sind 0 bis 253 (254 ist Broadcast).")
        joint = next((j for j in self.order if j.motor_id == servo_id), None)
        return Target(int(servo_id), joint.name if joint else None)

    def next_joint(self) -> str | None:
        """Erstes Gelenk in der Reihenfolge, das noch keine ID hat."""
        return next((j.name for j in self.order if self.status[j.name] not in ("gesetzt", "dauerhaft")), None)

    def scan(self) -> list[FoundServo]:
        """Alle Baudraten absuchen, 1 Mbit/s zuerst. Danach steht der Port wieder auf 1 Mbit/s."""
        rates = sorted(regs.baudrates(), reverse=True)
        self.found = None
        try:
            self.found = self.backend.scan(baudrates=rates)
        finally:
            self._restore_port()
        return self.found

    def single(self) -> FoundServo:
        """Der eine gefundene Servo. Sonst SetupError mit Abhilfe."""
        if self.found is None:
            raise SetupError("Erst nach dem Servo suchen.")
        if not self.found:
            raise SetupError(
                "Kein Servo gefunden (alle Baudraten durchsucht). Servo-Netzteil an? "
                "Kabel richtig gesteckt? Richtiger Port?"
            )
        if len(self.found) > 1:
            by_rate: dict[int, list[str]] = {}
            for s in self.found:
                by_rate.setdefault(s.baudrate, []).append(str(s.id))
            listing = "; ".join(f"IDs {', '.join(ids)} bei {rate} Baud" for rate, ids in by_rate.items())
            raise SetupError(
                f"Es antworten {len(self.found)} Servos ({listing}). Für die ID-Vergabe darf nur EIN Servo "
                "am Bus hängen. Alle anderen abziehen und neu suchen."
            )
        servo = self.found[0]
        if servo.model_number not in regs.model_names():
            raise SetupError(
                f"Unbekanntes Servomodell (Modellnummer {servo.model_number}). "
                "Eingerichtet werden nur Feetech-Servos wie der STS3215."
            )
        return servo

    def plan(self, joint_name: str | None = None, *, servo_id: int | None = None) -> list[str]:
        """Was `assign` ändern würde. Leere Liste: nichts zu tun."""
        servo, target = self.single(), self.target(joint_name, servo_id)
        self._refuse_finished(servo, target)
        steps = []
        if servo.id != target.id:
            steps.append(f"ID {servo.id} → {target.id}" + (f" ({target.joint})" if target.joint else ""))
        if servo.baudrate != DEFAULT_BAUDRATE:
            steps.append(f"Baudrate {servo.baudrate} → {DEFAULT_BAUDRATE}")
        return steps

    def _refuse_finished(self, servo: FoundServo, target: Target) -> None:
        """Den gerade fertigen Servo nicht aus Versehen auf das nächste Gelenk umstellen."""
        finished = {j.motor_id: j.name for j in self.order if self.status[j.name] in ("gesetzt", "dauerhaft")}
        if servo.id in finished and servo.id != target.id:
            raise SetupError(
                f"Der gefundene Servo hat die ID {servo.id} und wurde eben als {finished[servo.id]} "
                "eingerichtet. Noch nicht getauscht? Erst den nächsten Servo anschließen und neu suchen. "
                "(Soll genau dieser Servo eine andere ID bekommen: Assistent neu starten.)"
            )

    # ------------------------------------------------------------ Schreiben

    def assign(self, joint_name: str | None = None, *, servo_id: int | None = None) -> SetupResult:
        """Dem einen gefundenen Servo die ID des Gelenks (oder die freie ID `servo_id`) und
        1 Mbit/s geben, dann prüfen.

        Fehlt eine Vorbedingung, kommt SetupError und nichts wurde geschrieben. Geht beim
        Schreiben oder Prüfen etwas schief, steht das im Ergebnis (`ok` ist False).
        """
        target = self.target(joint_name, servo_id)
        servo = self.single()
        new_id = target.id
        result = SetupResult(
            action="id_gesetzt" if self.plan(servo_id=new_id) else "unveraendert",
            joint=target.joint or "",
            new_id=new_id,
            old_id=servo.id,
            old_baudrate=servo.baudrate,
            baudrate=DEFAULT_BAUDRATE,
            model=servo.model_name,
            firmware=servo.firmware,
        )
        try:
            self._probe(servo)
        except SetupError:
            self._restore_port()
            raise
        try:
            if servo.id != new_id:
                self.backend.change_id(servo.id, new_id)  # Ping unter neuer ID, alte ID frei
                result.checks.append(
                    Check(
                        f"ID {servo.id} → {new_id} geschrieben, der Servo antwortet unter ID {new_id}", True
                    )
                )
                result.checks.append(Check(f"Unter der alten ID {servo.id} antwortet keiner mehr", True))
            if servo.baudrate != DEFAULT_BAUDRATE:
                self.backend.change_baudrate(new_id, DEFAULT_BAUDRATE)  # Port zieht mit, Ping
                result.checks.append(
                    Check(
                        f"Baudrate {servo.baudrate} → {DEFAULT_BAUDRATE} geschrieben, der Servo antwortet",
                        True,
                    )
                )
            self.backend.set_port_baudrate(DEFAULT_BAUDRATE)
            result.checks.extend(self._verify(new_id))
        except BackendError as exc:
            result.checks.append(Check(str(exc), False))
        finally:
            self._restore_port()
        result.ok = all(c.ok for c in result.checks)
        if result.ok:
            sticker = f"{new_id} {target.joint}" if target.joint else str(new_id)
            result.message = (
                f"Der Servo ist jetzt {target.label} bei {DEFAULT_BAUDRATE} Baud. "
                f"Beschriften („{sticker}“), abziehen, nächster Servo."
            )
        else:
            result.message = "Nicht alle Prüfungen bestanden: " + "; ".join(
                c.text for c in result.checks if not c.ok
            )
        if target.joint:
            self.status[target.joint] = "gesetzt" if result.ok else "fehler"
        self.found = None  # nach dem Schreiben gilt die alte Suche nicht mehr
        self._finish(result)
        return result

    def _probe(self, servo: FoundServo) -> None:
        """Mehrmals lesen: Zwei Servos mit gleicher ID fallen durch kaputte Antworten auf."""
        self.backend.set_port_baudrate(servo.baudrate)
        failures = 0
        for _ in range(PROBE_READS):
            try:
                self.backend.read_register(servo.id, "present_position")
            except BackendError:
                failures += 1
        if failures:
            # Alle fehlgeschlagen kann beides heißen: abgezogen oder zwei Servos, die sich überlagern.
            raise SetupError(
                f"Servo {servo.id} antwortet unzuverlässig ({failures} von {PROBE_READS} Lesungen "
                "fehlgeschlagen). Hängen zwei Servos mit derselben ID am Bus? Oder wurde der Servo "
                "seit der Suche abgezogen? Alle bis auf einen abziehen und neu suchen."
            )
        if self.backend.ping(servo.id) != servo.model_number:
            raise SetupError(f"Servo {servo.id} hat sich seit der Suche verändert. Neu suchen.")

    def _verify(self, new_id: int) -> list[Check]:
        checks = []
        expected_baud = regs.baudrates()[DEFAULT_BAUDRATE]
        for name, expected, note in (
            ("id", new_id, ""),
            ("baud_rate", expected_baud, f" = {DEFAULT_BAUDRATE} Baud"),
        ):
            try:
                value: int | None = self.backend.read_register(new_id, name)
            except BackendError:
                value = None
            shown = "keine Antwort" if value is None else value
            checks.append(
                Check(f"Register {name} zurückgelesen: {shown} (soll {expected}{note})", value == expected)
            )
        ids = [s.id for s in self.backend.scan(baudrates=[DEFAULT_BAUDRATE])]
        shown_ids = ", ".join(map(str, ids)) or "nichts"
        checks.append(
            Check(
                f"Suche bei {DEFAULT_BAUDRATE} Baud findet: {shown_ids} (soll nur {new_id})", ids == [new_id]
            )
        )
        return checks

    # ------------------------------------------------------------ Neustart-Probe

    def verify_after_restart(
        self, joint_name: str | None = None, *, servo_id: int | None = None
    ) -> SetupResult:
        """Nach Aus- und Einschalten suchen: Steht der Servo dauerhaft unter der neuen ID?"""
        target = self.target(joint_name, servo_id)
        result = SetupResult(action="neustart", joint=target.joint or "", new_id=target.id)
        found = self.scan()
        if len(found) == 1:
            servo = found[0]
            result.old_id, result.old_baudrate = servo.id, servo.baudrate  # wie vorgefunden
            result.baudrate, result.model, result.firmware = servo.baudrate, servo.model_name, servo.firmware
        expected = len(found) == 1 and found[0].id == target.id and found[0].baudrate == DEFAULT_BAUDRATE
        listing = "; ".join(describe(s) for s in found) or "nichts"
        result.checks.append(
            Check(
                f"Nach dem Neustart gefunden: {listing} (soll ID {target.id} bei {DEFAULT_BAUDRATE} Baud)",
                expected,
            )
        )
        result.ok = expected
        if expected:
            result.message = f"{target.label} ist dauerhaft im EEPROM gespeichert."
        elif len(found) == 1:
            result.message = (
                "Der Servo hat nach dem Neustart andere Werte: Die Änderung war nicht dauerhaft. "
                "Noch einmal schreiben und erneut prüfen. Bleibt es so, Kapitel 03 (EEPROM-Sperre) lesen."
            )
        else:
            result.message = (
                "Nach dem Neustart muss genau ein Servo am Bus hängen. Prüfen und erneut versuchen."
            )
        if target.joint:
            self.status[target.joint] = "dauerhaft" if expected else "fehler"
        self.found = None  # als Nächstes kommt ein anderer Servo: neu suchen
        self._finish(result)
        return result

    # ------------------------------------------------------------ Protokoll

    def _finish(self, result: SetupResult) -> None:
        self.last = result
        try:
            self._log(result)
        except OSError as exc:
            result.message += f" (Protokoll nicht geschrieben: {exc})"

    def _log(self, result: SetupResult) -> None:
        path = self.log_path
        path.parent.mkdir(parents=True, exist_ok=True)
        new_file = not path.exists()
        with path.open("a", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, LOG_FIELDS)
            if new_file:
                writer.writeheader()
            writer.writerow(
                {
                    "zeit": datetime.fromtimestamp(result.time).astimezone().isoformat(timespec="seconds"),
                    "aktion": result.action,
                    "ergebnis": "ok" if result.ok else "fehler",
                    "geraetetyp": self.spec.type,
                    "gelenk": result.joint,
                    "alte_id": result.old_id,
                    "alte_baudrate": result.old_baudrate,
                    "neue_id": result.new_id,
                    "neue_baudrate": result.baudrate,
                    "modell": result.model,
                    "firmware": result.firmware,
                    "port": self.port,
                    "adapter_sn": self.adapter_sn,
                    "meldung": result.message,
                }
            )

    def _restore_port(self) -> None:
        try:
            if self.backend.baudrate != DEFAULT_BAUDRATE:
                self.backend.set_port_baudrate(DEFAULT_BAUDRATE)
        except BackendError:
            pass  # beim nächsten Zugriff fällt es ohnehin auf

    # ------------------------------------------------------------ Anzeige

    def snapshot(self) -> dict[str, Any]:
        return {
            "device_type": self.spec.type,
            "description": self.spec.description,
            "port": self.port,
            "adapter_sn": self.adapter_sn,
            "log_path": str(self.log_path),
            "order": [{"name": j.name, "id": j.motor_id, "status": self.status[j.name]} for j in self.order],
            "next": self.next_joint(),
            "found": None if self.found is None else [asdict(s) for s in self.found],
            "last": self.last.to_dict() if self.last else None,
        }
