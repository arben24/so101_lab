"""Mock-Backend: simulierte STS3215-Servos ohne Hardware.

Für Tests und zum Ausprobieren. Nachgebildet wird, was für Lernskripte und Sicherheit
zählt:
- Registerspeicher mit Werkswerten (Feetech-Speichertabelle V3.7),
- EEPROM-Sperre: Mit lock = 1 geschriebene EEPROM-Werte gelten nur bis zum Ausschalten
  (`power_cycle()`), mit lock = 0 bleiben sie erhalten,
- EEPROM-Schreiben nur bei ausgeschaltetem Drehmoment (strenger als nötig, damit Tests
  eine falsche Reihenfolge bemerken),
- Bewegung: Im Positionsmodus fährt der Servo mit Goal_Velocity (0 = 3400) auf die
  Zielposition, begrenzt auf Min/Max_Position_Limit. Im Geschwindigkeitsmodus dreht er
  endlos. Ohne Drehmoment bleibt er stehen, außer man bewegt ihn „von Hand“
  (`move_by_hand`),
- Baudrate: Ein Servo antwortet nur, wenn Bus und Servo dieselbe Rate haben.

Annahmen der Simulation (kein Abbild der echten Hardware): Spannung 12,0 V,
Temperatur 25 °C, Last und Strom proportional zur Geschwindigkeit.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field

from .. import registers as regs
from .base import (
    DEFAULT_BAUDRATE,
    ArmBackend,
    BackendError,
    Capability,
    FoundServo,
    ServoNotRespondingError,
)

MAX_SPEED = 3400  # Schritt/s bei Goal_Velocity = 0

# Werkswerte nach Feetech V3.7 (Spalte „初始值“), soweit nicht 0
FACTORY_DEFAULTS: dict[str, int] = {
    "firmware_major_version": 3,
    "firmware_minor_version": 7,
    "id": 1,
    "baud_rate": 0,
    "response_status_level": 1,
    "max_position_limit": 4095,
    "max_temperature_limit": 70,
    "max_voltage_limit": 80,
    "min_voltage_limit": 40,
    "max_torque_limit": 1000,
    "phase": 12,
    "unloading_condition": 44,
    "led_alarm_condition": 47,
    "p_coefficient": 32,
    "d_coefficient": 32,
    "minimum_startup_force": 16,
    "cw_dead_zone": 1,
    "ccw_dead_zone": 1,
    "protection_current": 500,
    "angular_resolution": 1,
    "protective_torque": 20,
    "protection_time": 200,
    "overload_torque": 80,
    "speed_closed_loop_p_coefficient": 10,
    "over_current_protection_time": 200,
    "velocity_closed_loop_i_coefficient": 10,
    "torque_limit": 1000,
}


@dataclass
class MockServo:
    """Ein simulierter Servo. `values` enthält die dekodierten Registerwerte."""

    values: dict[str, int] = field(default_factory=dict)
    persisted: dict[str, int] = field(default_factory=dict)  # EEPROM nach dem Ausschalten
    exact_position: float = 2048.0
    last_update: float | None = None
    voltage: float = 12.0
    temperature: int = 25

    @classmethod
    def factory_new(cls, servo_id: int = 1, position: int = 2048, mode: int = 0) -> MockServo:
        values = {reg.name: 0 for reg in regs.registers()}
        values["model_number"] = regs.model_number()
        values.update(FACTORY_DEFAULTS)
        values["id"] = servo_id
        values["operating_mode"] = mode
        values["goal_position"] = position
        servo = cls(values=values, exact_position=float(position))
        servo.persisted = {r.name: values[r.name] for r in regs.registers() if r.area is regs.Area.EEPROM}
        servo._refresh_sensors(0.0)
        return servo

    @property
    def id(self) -> int:
        return self.values["id"]

    @property
    def baudrate(self) -> int:
        table = {v: k for k, v in regs.baudrates().items()}
        return table[self.values["baud_rate"]]

    def power_cycle(self) -> None:
        """Aus- und einschalten: RAM auf Werkswerte, EEPROM auf den gespeicherten Stand."""
        position = round(self.exact_position) % 4096
        fresh = MockServo.factory_new(position=position)
        self.values = fresh.values
        self.values.update(self.persisted)
        self.values["goal_position"] = position
        self.exact_position = float(position)
        self._refresh_sensors(0.0)

    def update(self, now: float) -> None:
        dt = 0.0 if self.last_update is None else max(0.0, now - self.last_update)
        self.last_update = now
        velocity = 0.0
        if self.values["torque_enable"] and dt > 0:
            if self.values["operating_mode"] == 1:  # Geschwindigkeitsmodus: endlos drehen
                velocity = float(self.values["goal_velocity"])
                self.exact_position = (self.exact_position + velocity * dt) % 4096
            elif self.values["operating_mode"] == 0:
                # Ziel und Grenzen gelten wie Present_Position in Koordinaten mit Homing-Offset.
                lo, hi = self.values["min_position_limit"], self.values["max_position_limit"]
                target = min(hi, max(lo, self.values["goal_position"])) + self.values["homing_offset"]
                speed = abs(self.values["goal_velocity"]) or MAX_SPEED
                diff = target - self.exact_position
                step = max(-speed * dt, min(speed * dt, diff))
                self.exact_position += step
                velocity = step / dt
        self._refresh_sensors(velocity)

    def _refresh_sensors(self, velocity: float) -> None:
        position = round(self.exact_position) - self.values["homing_offset"]  # present = actual − offset
        self.values["present_position"] = max(-32767, min(32767, position))
        self.values["present_velocity"] = round(velocity)
        self.values["present_load"] = max(-1000, min(1000, round(velocity / 10)))
        self.values["present_voltage"] = round(self.voltage * 10)
        self.values["present_temperature"] = self.temperature
        self.values["present_current"] = abs(round(velocity / 50))
        self.values["moving"] = int(abs(velocity) > 1)

    def write(self, name: str, value: int) -> None:
        reg = regs.lookup(name)
        if not reg.writable:
            raise BackendError(f"Register '{reg.name}' ist nur lesbar.")
        reg.encode(value)
        if reg.area is regs.Area.EEPROM:
            if self.values["torque_enable"]:
                raise BackendError(
                    f"Simulation: EEPROM-Register '{reg.name}' bei eingeschaltetem Drehmoment."
                )
            if self.values["lock"] == 0:
                self.persisted[reg.name] = value
        self.values[reg.name] = value


class MockBackend(ArmBackend):
    name = "mock"
    capabilities = frozenset(Capability)

    def __init__(
        self,
        servos: Iterable[MockServo] | None = None,
        clock: Callable[[], float] = time.monotonic,
        baudrate: int = DEFAULT_BAUDRATE,
    ):
        self.servos: list[MockServo] = list(servos) if servos is not None else []
        self.clock = clock
        self.baudrate = baudrate
        self._connected = False
        self.log: list[tuple] = []  # (Operation, IDs, Register, Wert): für Tests der Reihenfolge

    @classmethod
    def for_ids(cls, ids: Iterable[int], wheel_ids: Iterable[int] = (), **kwargs) -> MockBackend:
        """Fertig eingerichteter Bus: je ID ein Servo, Räder im Geschwindigkeitsmodus."""
        wheel_ids = set(wheel_ids)
        servos = [MockServo.factory_new(i, mode=1 if i in wheel_ids else 0) for i in ids]
        return cls(servos, **kwargs)

    # ------------------------------------------------------------ Verbindung

    def connect(self) -> None:
        self._connected = True

    def disconnect(self) -> None:
        self._connected = False

    @property
    def is_connected(self) -> bool:
        return self._connected

    def _check(self) -> None:
        if not self._connected:
            raise BackendError("Nicht verbunden. Erst connect() aufrufen.")

    # ------------------------------------------------------------ Hilfen für Tests

    def servo(self, servo_id: int) -> MockServo:
        """Servo unter dieser ID (unabhängig von der Baudrate)."""
        matches = [s for s in self.servos if s.id == servo_id]
        if len(matches) != 1:
            raise KeyError(f"{len(matches)} Servos mit ID {servo_id} in der Simulation.")
        return matches[0]

    def move_by_hand(self, servo_id: int, position: int) -> None:
        """Simuliert, dass jemand den Servo (ohne Drehmoment) von Hand verstellt."""
        s = self.servo(servo_id)
        s.exact_position = float(position + s.values["homing_offset"])
        s._refresh_sensors(0.0)

    def _responding(self, servo_id: int) -> MockServo:
        self._check()
        now = self.clock()
        matches = [s for s in self.servos if s.id == servo_id and s.baudrate == self.baudrate]
        for s in matches:
            s.update(now)
        if not matches:
            raise ServoNotRespondingError(f"Servo {servo_id} antwortet nicht (Simulation).")
        if len(matches) > 1:
            raise BackendError(f"Mehrere Servos mit ID {servo_id}: Antworten überlagern sich (Simulation).")
        return matches[0]

    def _set_port_baudrate(self, baudrate: int) -> None:
        if baudrate not in regs.baudrates():
            raise BackendError(f"Baudrate {baudrate} nicht möglich.")
        self.baudrate = baudrate

    # ------------------------------------------------------------ Rohzugriffe

    def scan(
        self, ids: Sequence[int] | None = None, baudrates: Sequence[int] | None = None
    ) -> list[FoundServo]:
        self._check()
        wanted = set(ids) if ids is not None else None
        found = []
        for rate in baudrates or [self.baudrate]:
            seen = sorted({s.id for s in self.servos if s.baudrate == rate})
            for servo_id in seen:
                if wanted is not None and servo_id not in wanted:
                    continue
                s = next(x for x in self.servos if x.id == servo_id and x.baudrate == rate)
                v = s.values
                found.append(
                    FoundServo(
                        id=servo_id,
                        model_number=v["model_number"],
                        model_name=regs.model_names()[v["model_number"]],
                        baudrate=rate,
                        firmware=f"{v['firmware_major_version']}.{v['firmware_minor_version']}",
                    )
                )
        return found

    def ping(self, servo_id: int) -> int | None:
        try:
            return self._responding(servo_id).values["model_number"]
        except ServoNotRespondingError:
            return None

    def _read(self, servo_id: int, name: str) -> int:
        return self._responding(servo_id).values[regs.lookup(name).name]

    def _write(self, servo_id: int, name: str, value: int) -> None:
        servo = self._responding(servo_id)
        self.log.append(("write", servo_id, regs.lookup(name).name, value))
        servo.write(name, value)

    def _sync_read(self, ids: Sequence[int], name: str) -> dict[int, int]:
        return {i: self._read(i, name) for i in ids}

    def _sync_write(self, name: str, values: dict[int, int]) -> None:
        self._check()
        self.log.append(("sync_write", tuple(values), regs.lookup(name).name, dict(values)))
        now = self.clock()
        for servo_id, value in values.items():
            for s in self.servos:  # Sync-Write hat keine Antwort: fehlende Servos fallen nicht auf
                if s.id == servo_id and s.baudrate == self.baudrate:
                    s.update(now)
                    s.write(name, value)

    def set_torque(self, ids: Iterable[int], enabled: bool) -> None:
        ids = list(ids)
        self.log.append(("torque", tuple(ids), "torque_enable", int(enabled)))
        failed = []
        for servo_id in ids:
            try:
                servo = self._responding(servo_id)
            except ServoNotRespondingError:
                failed.append(servo_id)
                continue
            servo.write("torque_enable", int(enabled))
            servo.values["lock"] = int(enabled)  # wie rustypot: Sperre folgt dem Drehmoment
        if failed:
            raise ServoNotRespondingError(
                f"Drehmoment {'ein' if enabled else 'aus'} fehlgeschlagen bei IDs {failed}."
            )
