"""USB-Backend: Servo-Bus am eigenen Rechner.

Benutzt `rustypot.Bus`, denselben Treiber, auf dem LeRobot 0.7.0 aufbaut
(`lerobot.motors.SerialMotorsBus`). Wir gehen eine Ebene tiefer als LeRobot, weil
- LeRobot keinen öffentlichen Bus-Scan über IDs und Baudraten hat,
- LeRobots Roboterklassen beim Verbinden Regelparameter ins EEPROM schreiben und bei
  LeKiwi das Drehmoment einschalten. Hier passiert beim Verbinden nichts an den Servos.

LeRobot-Werkzeug, das dasselbe „in fertig“ macht: `lerobot-setup-motors` (IDs) und
`lerobot.motors.SerialMotorsBus` (Lesen/Schreiben).
"""

from __future__ import annotations

import errno
import logging
from collections.abc import Iterable, Sequence

from .. import registers as regs
from .base import (
    ALL_IDS,
    DEFAULT_BAUDRATE,
    ArmBackend,
    BackendError,
    Capability,
    FoundServo,
    ServoNotRespondingError,
)

logger = logging.getLogger(__name__)

# Wartezeit je Antwort. LeRobot nimmt 50 ms: länger als der 16-ms-Latenztimer mancher USB-Adapter.
DEFAULT_TIMEOUT_S = 0.05


class UsbBackend(ArmBackend):
    name = "usb"
    capabilities = frozenset(Capability)

    def __init__(self, port: str, baudrate: int = DEFAULT_BAUDRATE, timeout_s: float = DEFAULT_TIMEOUT_S):
        self.port = port
        self.baudrate = baudrate
        self.timeout_s = timeout_s
        self._bus = None

    # ------------------------------------------------------------ Verbindung

    def connect(self) -> None:
        if self._bus is not None:
            return
        import rustypot

        definition = regs.definition()
        try:
            # rustypot spricht nur IDs an, die hier stehen. Für Scan und ID-Vergabe: alle.
            self._bus = rustypot.Bus(
                self.port, self.baudrate, self.timeout_s, dict.fromkeys(ALL_IDS, definition)
            )
        except OSError as exc:
            raise BackendError(_port_error_text(self.port, exc)) from exc
        logger.info("USB: %s geöffnet (%d Baud)", self.port, self.baudrate)

    def disconnect(self) -> None:
        if self._bus is None:
            return
        try:
            self._bus.close()
        finally:
            self._bus = None
            logger.info("USB: %s geschlossen", self.port)

    @property
    def is_connected(self) -> bool:
        return self._bus is not None

    @property
    def bus(self):
        if self._bus is None:
            raise BackendError("Nicht verbunden. Erst connect() aufrufen.")
        return self._bus

    def _set_port_baudrate(self, baudrate: int) -> None:
        self.bus.set_baudrate(baudrate)
        self.baudrate = baudrate

    # ------------------------------------------------------------ Scan

    def scan(
        self, ids: Sequence[int] | None = None, baudrates: Sequence[int] | None = None
    ) -> list[FoundServo]:
        """Ein Broadcast-Ping je Baudrate (ca. 0,8 s), dann je gefundenem Servo die Firmware lesen.

        Die Baudrate des Ports steht danach wieder auf dem Wert von vorher.
        """
        definition = regs.definition()
        ids = list(ids) if ids is not None else None
        rates = list(baudrates) if baudrates else [self.baudrate]
        original = self.baudrate
        found: list[FoundServo] = []
        try:
            for rate in rates:
                self._set_port_baudrate(rate)
                try:
                    hits = self.bus.broadcast_scan(definition, ids) if ids is None or ids else {}
                except (RuntimeError, OSError) as exc:
                    raise BackendError(f"Scan bei {rate} Baud fehlgeschlagen: {exc}") from exc
                for servo_id, model in sorted(hits.items()):
                    found.append(
                        FoundServo(
                            id=servo_id,
                            model_number=model,
                            model_name=regs.model_names().get(model, f"unbekannt ({model})"),
                            baudrate=rate,
                            firmware=self._firmware(servo_id),
                        )
                    )
        finally:
            self._set_port_baudrate(original)
        return found

    def _firmware(self, servo_id: int) -> str | None:
        try:
            major = self._read(servo_id, "firmware_major_version")
            minor = self._read(servo_id, "firmware_minor_version")
        except BackendError:
            return None
        return f"{major}.{minor}"

    def ping(self, servo_id: int) -> int | None:
        try:
            return self._read(servo_id, "model_number")
        except ServoNotRespondingError:
            return None

    # ------------------------------------------------------------ Register

    def _read(self, servo_id: int, name: str) -> int:
        try:
            value, _status = self.bus.read_register_with_error(servo_id, name)
        except (RuntimeError, OSError) as exc:
            raise ServoNotRespondingError(
                f"Servo {servo_id} antwortet nicht beim Lesen von '{name}' ({exc})."
            ) from exc
        except ValueError as exc:
            raise BackendError(f"Lesen von '{name}' bei Servo {servo_id} nicht möglich: {exc}") from exc
        return int(value)

    def _write(self, servo_id: int, name: str, value: int) -> None:
        try:
            self.bus.write_register_with_error(servo_id, name, int(value))
        except (RuntimeError, OSError) as exc:
            raise ServoNotRespondingError(
                f"Servo {servo_id} bestätigt das Schreiben von '{name}' nicht ({exc})."
            ) from exc
        except ValueError as exc:
            raise BackendError(f"Schreiben von '{name}' bei Servo {servo_id} nicht möglich: {exc}") from exc

    def _sync_read(self, ids: Sequence[int], name: str) -> dict[int, int]:
        try:
            values = self.bus.sync_read_register(list(ids), name)
        except (RuntimeError, OSError) as exc:
            raise ServoNotRespondingError(
                f"Sync-Read von '{name}' bei IDs {list(ids)} fehlgeschlagen ({exc})."
            ) from exc
        return {i: int(v) for i, v in zip(ids, values, strict=True)}

    def _sync_write(self, name: str, values: dict[int, int]) -> None:
        try:
            self.bus.sync_write_register(list(values), name, list(values.values()))
        except (RuntimeError, OSError) as exc:
            raise BackendError(f"Sync-Write von '{name}' fehlgeschlagen ({exc}).") from exc

    def set_torque(self, ids: Iterable[int], enabled: bool) -> None:
        # rustypot schaltet auf Feetech-Servos die EEPROM-Sperre mit dem Drehmoment (wie LeRobot).
        failed = self.bus.set_torque(list(ids), enabled, retries=2)
        if failed:
            details = ", ".join(f"ID {i}: {err}" for i, err in failed.items())
            raise ServoNotRespondingError(
                f"Drehmoment {'ein' if enabled else 'aus'} fehlgeschlagen ({details})."
            )


def _port_error_text(port: str, exc: OSError) -> str:
    if exc.errno == errno.ENOENT or "No such file" in str(exc):
        return f"Port {port} nicht gefunden. Ist der Adapter angesteckt? Ports anzeigen: so101-lab ports"
    if exc.errno == errno.EACCES or "Permission denied" in str(exc):
        return f"Keine Rechte für {port}. Abhilfe: sudo usermod -aG dialout $USER, danach neu anmelden."
    if exc.errno == errno.EBUSY or "busy" in str(exc).lower():
        return f"Port {port} ist belegt. Läuft noch ein anderes Programm (Web-App, LeRobot)?"
    return f"Port {port} lässt sich nicht öffnen: {exc}"
