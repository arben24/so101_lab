"""Beschreibung eines Geräts: welche Motoren mit welcher ID in welcher Betriebsart."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class JointMode(StrEnum):
    POSITION = "position"  # Register 33 = 0, Gelenke
    VELOCITY = "velocity"  # Register 33 = 1, Räder


class NormMode(StrEnum):
    """Wie LeRobot Rohwerte in Gelenkwerte umrechnet (MotorNormMode)."""

    DEGREES = "degrees"
    RANGE_0_100 = "range_0_100"
    RANGE_M100_100 = "range_m100_100"


@dataclass(frozen=True)
class Joint:
    name: str
    motor_id: int
    mode: JointMode = JointMode.POSITION
    norm: NormMode = NormMode.DEGREES
    full_turn: bool = False  # Volldreher: Kalibrierung über den ganzen Encoder (wrist_roll)


@dataclass(frozen=True)
class DeviceSpec:
    type: str  # Schlüssel in config/arms.toml
    description: str
    joints: tuple[Joint, ...]
    lerobot_kind: str  # "robots" oder "teleoperators": Unterordner der Kalibrierablage
    lerobot_name: str  # Klassenname in LeRobot (so_follower, so_leader, lekiwi)

    @property
    def ids(self) -> list[int]:
        return [j.motor_id for j in self.joints]

    def joint(self, name: str) -> Joint:
        for j in self.joints:
            if j.name == name:
                return j
        raise KeyError(f"Gelenk '{name}' gibt es bei {self.type} nicht.")

    def joint_by_id(self, motor_id: int) -> Joint:
        for j in self.joints:
            if j.motor_id == motor_id:
                return j
        raise KeyError(f"Motor-ID {motor_id} gibt es bei {self.type} nicht.")

    @property
    def position_joints(self) -> tuple[Joint, ...]:
        return tuple(j for j in self.joints if j.mode is JointMode.POSITION)

    @property
    def wheels(self) -> tuple[Joint, ...]:
        return tuple(j for j in self.joints if j.mode is JointMode.VELOCITY)


def _get_registry() -> dict[str, DeviceSpec]:
    from .lekiwi import LEKIWI, LEKIWI_ARM, LEKIWI_BASE
    from .so101 import SO101_FOLLOWER, SO101_LEADER

    return {s.type: s for s in (SO101_FOLLOWER, SO101_LEADER, LEKIWI, LEKIWI_ARM, LEKIWI_BASE)}


# Reihenfolge zählt: Je Kalibrierordner gilt der erste Typ als maßgeblich (lekiwi vor den Teilen).
DEVICE_TYPES: tuple[str, ...] = ("so101_follower", "so101_leader", "lekiwi", "lekiwi_arm", "lekiwi_base")


def get_device_spec(device_type: str) -> DeviceSpec:
    try:
        return _get_registry()[device_type]
    except KeyError:
        raise KeyError(
            f"Unbekannter Gerätetyp '{device_type}'. Erlaubt: {', '.join(DEVICE_TYPES)}."
        ) from None
