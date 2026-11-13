"""Gemeinsame Schnittstelle aller Backends.

Ein Backend kennt nur Servo-IDs, Registernamen und **rohe Servo-Einheiten** (Position in
Schritten 0…4095, Tempo in Schritt/s). Gelenknamen, Kalibrierung und Grenzen liegen eine
Ebene höher in `so101_lab.arm.Arm`.

Implementierungen: UsbBackend (lokal per USB), MockBackend (simuliert), später
RemoteBackend (Phase 7) und Esp32Backend (Phase 8).

Schreibzugriffe:
- `write_register` schreibt nur RAM-Register. EEPROM-Register lehnt es ab.
- `write_eeprom` ist der einzige Weg ins EEPROM: Drehmoment aus, Sperre öffnen, schreiben,
  Sperre schließen, zurücklesen und vergleichen.
- `change_id` und `change_baudrate` bauen darauf auf und prüfen das Ergebnis per Ping.
"""

from __future__ import annotations

import abc
import logging
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from enum import StrEnum

from .. import registers as regs

logger = logging.getLogger(__name__)

ALL_IDS = tuple(range(0, 254))  # 254 ist Broadcast, 255 ungültig
DEFAULT_BAUDRATE = 1_000_000


class BackendError(RuntimeError):
    """Allgemeiner Fehler im Backend, Text für Nutzer auf Deutsch."""


class NotSupportedError(BackendError):
    """Das Backend kann diese Operation nicht (z. B. Baudrate ändern über den ESP32)."""


class ServoNotRespondingError(BackendError):
    """Ein Servo antwortet nicht (Kabel, Versorgung, falsche ID oder Baudrate)."""


class VerificationError(BackendError):
    """Ein EEPROM-Wert wurde geschrieben, steht danach aber nicht im Servo."""


class Capability(StrEnum):
    SCAN = "scan"  # IDs bei der aktuellen Baudrate finden
    SCAN_BAUDRATES = "scan_baudrates"  # auch andere Baudraten durchsuchen
    REGISTERS = "registers"  # einzelne Register lesen und schreiben
    SYNC = "sync"  # Sync-Read/-Write (ein Paket für viele Servos)
    EEPROM = "eeprom"  # EEPROM schreiben (inkl. ID ändern)
    CHANGE_BAUDRATE = "change_baudrate"
    WHEELS = "wheels"  # Geschwindigkeitsmodus (LeKiwi-Räder)


@dataclass(frozen=True)
class FoundServo:
    id: int
    model_number: int
    model_name: str
    baudrate: int
    firmware: str | None = None  # "Haupt.Neben", falls lesbar


@dataclass(frozen=True)
class ServoState:
    id: int
    position: int  # Schritte
    velocity: int  # Schritt/s
    load: int  # 0,1 % Tastverhältnis, Vorzeichen = Richtung
    voltage: float  # V
    temperature: int  # °C
    status: int  # Register 65: Bit 0 Spannung, 1 Sensor, 2 Temperatur, 3 Strom, 4 Winkel, 5 Überlast
    torque_enabled: bool
    current_ma: float | None = None  # mA, nicht jedes Backend liefert den Strom

    @property
    def errors(self) -> list[str]:
        names = ["Spannung", "Sensor", "Temperatur", "Strom", "Winkel", "Überlast"]
        return [name for bit, name in enumerate(names) if self.status & (1 << bit)]


# Register, aus denen `read_state` einen ServoState baut
STATE_REGISTERS = (
    "present_position",
    "present_velocity",
    "present_load",
    "present_voltage",
    "present_temperature",
    "status",
    "torque_enable",
    "present_current",
)
CURRENT_MA_PER_UNIT = 6.5  # Feetech V3.7, Register 69


class ArmBackend(abc.ABC):
    """Zugriff auf einen Feetech-Bus, egal ob per USB, Netzwerk oder ESP32."""

    name: str = "abstract"
    capabilities: frozenset[Capability] = frozenset()

    # ------------------------------------------------------------ Verbindung

    @abc.abstractmethod
    def connect(self) -> None: ...

    @abc.abstractmethod
    def disconnect(self) -> None:
        """Verbindung schließen. Ändert nichts an den Servos (das erledigt Arm.safe_shutdown)."""

    @property
    @abc.abstractmethod
    def is_connected(self) -> bool: ...

    def __enter__(self) -> ArmBackend:
        self.connect()
        return self

    def __exit__(self, *exc) -> None:
        self.disconnect()

    def supports(self, capability: Capability) -> bool:
        return capability in self.capabilities

    def require(self, capability: Capability) -> None:
        if not self.supports(capability):
            raise NotSupportedError(f"Das Backend '{self.name}' unterstützt '{capability}' nicht.")

    # ------------------------------------------------------------ Rohzugriffe (je Backend)

    @abc.abstractmethod
    def scan(
        self, ids: Sequence[int] | None = None, baudrates: Sequence[int] | None = None
    ) -> list[FoundServo]:
        """Servos finden. `ids=None` sucht alle IDs, `baudrates=None` nur die aktuelle Baudrate."""

    @abc.abstractmethod
    def ping(self, servo_id: int) -> int | None:
        """Modellnummer des Servos, `None` wenn er nicht antwortet."""

    @abc.abstractmethod
    def _read(self, servo_id: int, name: str) -> int: ...

    @abc.abstractmethod
    def _write(self, servo_id: int, name: str, value: int) -> None: ...

    @abc.abstractmethod
    def _sync_read(self, ids: Sequence[int], name: str) -> dict[int, int]: ...

    @abc.abstractmethod
    def _sync_write(self, name: str, values: dict[int, int]) -> None: ...

    @abc.abstractmethod
    def set_torque(self, ids: Iterable[int], enabled: bool) -> None:
        """Drehmoment ein/aus. Wirft BackendError mit den IDs, die nicht reagiert haben."""

    def _set_port_baudrate(self, baudrate: int) -> None:
        raise NotSupportedError(f"Das Backend '{self.name}' kann die Baudrate nicht umstellen.")

    # ------------------------------------------------------------ öffentliche Zugriffe

    def read_register(self, servo_id: int, name: str) -> int:
        reg = regs.lookup(name)
        return self._read(servo_id, reg.name)

    def sync_read(self, ids: Sequence[int], name: str) -> dict[int, int]:
        reg = regs.lookup(name)
        if not ids:
            return {}
        return self._sync_read(list(ids), reg.name)

    def write_register(self, servo_id: int, name: str, value: int) -> None:
        """RAM-Register schreiben. EEPROM nur über `write_eeprom`."""
        reg = self._check_writable(name, value)
        self._write(servo_id, reg.name, value)

    def sync_write(self, name: str, values: dict[int, int]) -> None:
        """Mehrere Servos mit einem Paket. Ohne Antwort: ein verlorenes Paket fällt nicht auf."""
        if not values:
            return
        reg = None
        for value in values.values():
            reg = self._check_writable(name, value)
        assert reg is not None
        self._sync_write(reg.name, {int(k): int(v) for k, v in values.items()})

    @staticmethod
    def _check_writable(name: str, value: int, allow_eeprom: bool = False) -> regs.Register:
        reg = regs.lookup(name)
        if not reg.writable:
            raise BackendError(f"Register '{reg.name}' ist nur lesbar.")
        if reg.area is regs.Area.EEPROM and not allow_eeprom:
            raise BackendError(f"Register '{reg.name}' liegt im EEPROM. Nur über write_eeprom() schreiben.")
        if reg.name == "torque_enable" and value not in regs.TORQUE_ENABLE_VALUES:
            raise BackendError("torque_enable darf nur 0 oder 1 sein (128 verstellt die Nullstellung).")
        try:
            reg.encode(int(value))  # prüft den Wertebereich
        except ValueError as exc:
            raise BackendError(str(exc)) from exc
        return reg

    def read_positions(self, ids: Sequence[int]) -> dict[int, int]:
        return self.sync_read(ids, "present_position")

    def read_state(self, ids: Sequence[int]) -> dict[int, ServoState]:
        """Vollständiger Zustand. Kostet ein Sync-Read je Größe, also langsamer als read_positions."""
        ids = list(ids)
        if not ids:
            return {}
        values = {name: self.sync_read(ids, name) for name in STATE_REGISTERS}
        return {
            i: ServoState(
                id=i,
                position=values["present_position"][i],
                velocity=values["present_velocity"][i],
                load=values["present_load"][i],
                voltage=values["present_voltage"][i] / 10,
                temperature=values["present_temperature"][i],
                status=values["status"][i],
                torque_enabled=bool(values["torque_enable"][i]),
                current_ma=values["present_current"][i] * CURRENT_MA_PER_UNIT,
            )
            for i in ids
        }

    # ------------------------------------------------------------ EEPROM (klar gekennzeichnet)

    def write_eeprom(self, servo_id: int, name: str, value: int) -> None:
        """EEPROM-Register dauerhaft schreiben und prüfen.

        Ablauf: Drehmoment aus → Sperre öffnen (lock = 0) → schreiben → Sperre schließen
        (lock = 1) → zurücklesen. Weicht der Wert ab, folgt VerificationError.
        Für `id` und `baud_rate` die eigenen Funktionen benutzen.
        """
        self.require(Capability.EEPROM)
        reg = self._check_writable(name, value, allow_eeprom=True)
        if reg.name in ("id", "baud_rate"):
            raise BackendError(f"'{reg.name}' bitte mit change_id() bzw. change_baudrate() ändern.")
        if reg.area is not regs.Area.EEPROM:
            raise BackendError(f"Register '{reg.name}' liegt nicht im EEPROM, write_register() benutzen.")
        logger.info("EEPROM: Servo %d, %s = %d", servo_id, reg.name, value)
        self.set_torque([servo_id], False)
        self._write(servo_id, "lock", 0)
        try:
            self._write(servo_id, reg.name, value)
        finally:
            self._write(servo_id, "lock", 1)
        readback = self._read(servo_id, reg.name)
        if readback != value:
            raise VerificationError(
                f"Servo {servo_id}: '{reg.name}' sollte {value} sein, gelesen wurde {readback}."
            )

    def change_id(self, old_id: int, new_id: int) -> None:
        """Servo-ID dauerhaft ändern und per Ping unter der neuen ID prüfen.

        Die neue ID muss frei sein. Der Servo antwortet auf den Schreibbefehl noch unter der
        alten ID und wechselt danach.
        """
        self.require(Capability.EEPROM)
        if not 0 <= new_id <= 253:
            raise BackendError(f"Neue ID {new_id} ungültig (erlaubt 0…253).")
        if old_id == new_id:
            return
        model = self.ping(old_id)
        if model is None:
            raise ServoNotRespondingError(f"Kein Servo unter ID {old_id}.")
        if self.ping(new_id) is not None:
            raise BackendError(f"ID {new_id} ist bereits belegt. Nur einen Servo anschließen.")
        logger.info("EEPROM: ID %d → %d", old_id, new_id)
        self.set_torque([old_id], False)
        self._write(old_id, "lock", 0)
        self._write(old_id, "id", new_id)
        self._write(new_id, "lock", 1)
        if self.ping(new_id) != model:
            raise VerificationError(f"Servo antwortet nach dem Umstellen nicht unter ID {new_id}.")
        if self.ping(old_id) is not None:
            raise VerificationError(f"Unter der alten ID {old_id} antwortet noch ein Servo.")

    def change_baudrate(self, servo_id: int, baudrate: int) -> None:
        """Baudrate eines Servos dauerhaft ändern, den Port nachziehen und per Ping prüfen.

        Danach antwortet der Servo nur noch bei der neuen Baudrate. Der Port des Backends
        steht anschließend auf der neuen Rate.
        """
        self.require(Capability.CHANGE_BAUDRATE)
        table = regs.baudrates()
        if baudrate not in table:
            raise BackendError(f"Baudrate {baudrate} nicht möglich. Erlaubt: {sorted(table)}.")
        model = self.ping(servo_id)
        if model is None:
            raise ServoNotRespondingError(f"Kein Servo unter ID {servo_id}.")
        logger.info("EEPROM: Servo %d, Baudrate → %d", servo_id, baudrate)
        self.set_torque([servo_id], False)
        self._write(servo_id, "lock", 0)
        self._write(servo_id, "baud_rate", table[baudrate])
        self._set_port_baudrate(baudrate)
        self._write(servo_id, "lock", 1)
        if self.ping(servo_id) != model:
            raise VerificationError(f"Servo {servo_id} antwortet bei {baudrate} Baud nicht.")
