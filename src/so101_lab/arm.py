"""Ein Gerät mit Gelenknamen, Kalibrierung und Grenzen über einem beliebigen Backend.

Hier liegen die Sicherheitsregeln für Bewegungen:
1. Geschwindigkeit und Beschleunigung werden gesetzt, **bevor** Zielpositionen geschrieben
   werden (`configure_motion`, `move_to` ruft es bei Bedarf selbst auf).
2. Zielpositionen werden auf die kalibrierten Grenzen und auf einen größten Sprung
   (`max_relative_target`) begrenzt.
3. Vor dem Einschalten des Drehmoments wird die Zielposition auf die Istposition gesetzt,
   sonst springt der Servo zum alten Wert im Register.
4. `safe_shutdown` stoppt die Räder und schaltet danach je nach Konfiguration das
   Drehmoment aus oder hält die Stellung.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from pathlib import Path

from .backends.base import ArmBackend, BackendError, ServoNotRespondingError, ServoState
from .calibration import Calibration, raw_to_joint
from .config import DeviceConfig
from .devices.spec import DeviceSpec, Joint, JointMode
from .safety import clamp, clamp_relative

logger = logging.getLogger(__name__)

FULL_RANGE = (0, 4095)


class Arm:
    def __init__(
        self,
        backend: ArmBackend,
        spec: DeviceSpec,
        cfg: DeviceConfig,
        calibration: Calibration | None = None,
        calibration_path: Path | None = None,
    ):
        self.backend = backend
        self.spec = spec
        self.cfg = cfg
        self.calibration = calibration
        self.calibration_path = calibration_path
        self._motion_ready = False

    # ------------------------------------------------------------ Hilfen

    @property
    def joints(self) -> tuple[Joint, ...]:
        return self.spec.joints

    def _ids(self, names: Iterable[str] | None, mode: JointMode | None = None) -> dict[str, int]:
        joints = self.joints if names is None else tuple(self.spec.joint(n) for n in names)
        return {j.name: j.motor_id for j in joints if mode is None or j.mode is mode}

    def limits(self, name: str) -> tuple[int, int]:
        """Erlaubter Rohbereich eines Gelenks: kalibrierter Bereich, sonst der ganze Encoder."""
        joint = self.spec.joint(name)
        if self.calibration and name in self.calibration and not joint.full_turn:
            c = self.calibration[name]
            return min(c.range_min, c.range_max), max(c.range_min, c.range_max)
        return FULL_RANGE

    @property
    def motion_ready(self) -> bool:
        """Sind Tempo- und Beschleunigungsgrenze in dieser Verbindung schon gesetzt?"""
        return self._motion_ready

    @property
    def is_calibrated(self) -> bool:
        return bool(self.calibration) and all(j.name in self.calibration for j in self.spec.position_joints)

    # ------------------------------------------------------------ Prüfen und Lesen

    def check_motors(self) -> None:
        """Antworten alle Motoren des Geräts? Sonst ServoNotRespondingError mit den Gelenknamen."""
        missing = [
            f"{j.name} (ID {j.motor_id})" for j in self.joints if self.backend.ping(j.motor_id) is None
        ]
        if missing:
            raise ServoNotRespondingError(f"Keine Antwort von: {', '.join(missing)}.")

    def check_operating_modes(self) -> list[str]:
        """Liste der Gelenke, deren Betriebsart (Register 33) nicht zur Gelenktabelle passt."""
        expected = {JointMode.POSITION: 0, JointMode.VELOCITY: 1}
        actual = self.backend.sync_read([j.motor_id for j in self.joints], "operating_mode")
        return [
            f"{j.name}: {actual[j.motor_id]} statt {expected[j.mode]}"
            for j in self.joints
            if actual[j.motor_id] != expected[j.mode]
        ]

    def read_positions(self, names: Iterable[str] | None = None) -> dict[str, int]:
        ids = self._ids(names)
        raw = self.backend.read_positions(list(ids.values()))
        return {name: raw[i] for name, i in ids.items()}

    def read_state(self, names: Iterable[str] | None = None) -> dict[str, ServoState]:
        ids = self._ids(names)
        raw = self.backend.read_state(list(ids.values()))
        return {name: raw[i] for name, i in ids.items()}

    def to_joint_values(self, positions: dict[str, int]) -> dict[str, float | None]:
        """Rohwerte → Grad bzw. Prozent (wie LeRobot). `None`, wenn das Gelenk nicht kalibriert ist."""
        result: dict[str, float | None] = {}
        for name, raw in positions.items():
            joint = self.spec.joint(name)
            cal = self.calibration.get(name) if self.calibration else None
            result[name] = raw_to_joint(raw, joint, cal) if cal and joint.mode is JointMode.POSITION else None
        return result

    # ------------------------------------------------------------ Bewegen

    def configure_motion(self, max_velocity: int | None = None, acceleration: int | None = None) -> None:
        """Geschwindigkeits- und Beschleunigungsgrenze setzen (RAM, gilt bis zum Ausschalten).

        Bei Gelenken im Positionsmodus ist Goal_Velocity das Höchsttempo der Bewegung.
        Bei Rädern ist dasselbe Register der Fahrbefehl, deshalb bekommen sie hier nur die
        Beschleunigung.
        """
        velocity = clamp(max_velocity or self.cfg.max_velocity, 1, 3400)
        accel = clamp(acceleration or self.cfg.acceleration, 1, 254)
        all_ids = [j.motor_id for j in self.joints]
        position_ids = [j.motor_id for j in self.spec.position_joints]
        self.backend.sync_write("acceleration", dict.fromkeys(all_ids, accel))
        if position_ids:
            self.backend.sync_write("goal_velocity", dict.fromkeys(position_ids, velocity))
        self._motion_ready = True
        logger.info("Bewegungsgrenzen: %d Schritt/s, Beschleunigung %d × 100 Schritt/s²", velocity, accel)

    def enable_torque(self, names: Iterable[str] | None = None) -> None:
        """Drehmoment einschalten, ohne dass ein Gelenk springt."""
        if not self._motion_ready:
            self.configure_motion()
        ids = self._ids(names)
        position_ids = self._ids(names, JointMode.POSITION)
        if position_ids:
            present = self.backend.read_positions(list(position_ids.values()))
            self.backend.sync_write("goal_position", {i: present[i] for i in position_ids.values()})
        wheel_ids = self._ids(names, JointMode.VELOCITY)
        if wheel_ids:
            self.backend.sync_write("goal_velocity", dict.fromkeys(wheel_ids.values(), 0))
        self.backend.set_torque(ids.values(), True)

    def disable_torque(self, names: Iterable[str] | None = None) -> None:
        self.backend.set_torque(self._ids(names).values(), False)

    def move_to(self, targets: dict[str, int], *, limit_step: bool = True) -> dict[str, int]:
        """Zielpositionen (roh) schreiben. Liefert die tatsächlich gesendeten Werte zurück.

        Begrenzt auf die kalibrierten Grenzen und, wenn `limit_step`, auf höchstens
        `max_relative_target` Schritte Abstand zur Istposition.
        """
        if not self._motion_ready:
            self.configure_motion()
        for name in targets:
            if self.spec.joint(name).mode is not JointMode.POSITION:
                raise BackendError(f"'{name}' ist ein Rad. Räder mit set_wheel_velocities() steuern.")
        goals = {name: clamp(value, *self.limits(name)) for name, value in targets.items()}
        if limit_step and self.cfg.max_relative_target is not None:
            present = self.read_positions(goals)
            goals = {n: clamp_relative(g, present[n], self.cfg.max_relative_target) for n, g in goals.items()}
            goals = {n: clamp(g, *self.limits(n)) for n, g in goals.items()}
        self.backend.sync_write("goal_position", {self.spec.joint(n).motor_id: g for n, g in goals.items()})
        return goals

    def hold(self) -> None:
        """Alle Positionsgelenke halten ihre aktuelle Stellung."""
        ids = self._ids(None, JointMode.POSITION)
        if ids:
            present = self.backend.read_positions(list(ids.values()))
            self.backend.sync_write("goal_position", {i: present[i] for i in ids.values()})

    def set_wheel_velocities(self, velocities: dict[str, int]) -> dict[str, int]:
        """Radtempo in Schritt/s, begrenzt auf ± max_wheel_velocity."""
        limit = self.cfg.max_wheel_velocity
        sent = {}
        for name, value in velocities.items():
            if self.spec.joint(name).mode is not JointMode.VELOCITY:
                raise BackendError(f"'{name}' ist kein Rad.")
            sent[name] = clamp(value, -limit, limit)
        self.backend.sync_write("goal_velocity", {self.spec.joint(n).motor_id: v for n, v in sent.items()})
        return sent

    def stop_wheels(self) -> None:
        wheels = [j.motor_id for j in self.spec.wheels]
        if wheels:
            self.backend.sync_write("goal_velocity", dict.fromkeys(wheels, 0))

    # ------------------------------------------------------------ Sicher beenden

    def safe_shutdown(self) -> None:
        """Räder stopp, dann `safe_state`: Drehmoment aus oder Stellung halten.

        Versucht jeden Schritt, auch wenn ein vorheriger fehlschlägt, und meldet am Ende alle Fehler.
        """
        errors = []
        try:
            self.stop_wheels()
        except Exception as exc:
            errors.append(f"Räder stoppen: {exc}")
        try:
            if self.cfg.safe_state == "hold":
                self.hold()
            else:
                self.disable_torque()
        except Exception as exc:
            errors.append(f"{self.cfg.safe_state}: {exc}")
        if errors:
            raise BackendError("Sicheres Zurücksetzen unvollständig: " + "; ".join(errors))

    def emergency_stop(self) -> None:
        """Not-Aus: Räder stopp und Drehmoment aus für jeden einzelnen Motor, unabhängig von safe_state."""
        try:
            self.stop_wheels()
        except Exception:
            logger.exception("Not-Aus: Räder stoppen fehlgeschlagen")
        for joint in self.joints:
            try:
                self.backend.set_torque([joint.motor_id], False)
            except Exception:
                logger.exception("Not-Aus: Drehmoment aus bei %s fehlgeschlagen", joint.name)
