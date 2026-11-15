"""LeKiwi: dreirädrige Basis mit aufgesetztem SO-101-Follower an einem gemeinsamen Bus.

Quelle: lerobot/robots/lekiwi/lekiwi.py (LeRobot 0.7.0). Die Kinematik der Basis
folgt in Phase 9.

Drei Gerätetypen für dieselbe Hardware (am selben Bus, also nie gleichzeitig verbunden):

- `lekiwi`       Arm und Basis zusammen (IDs 1–9), wie LeRobots `LeKiwi`
- `lekiwi_arm`   nur der Arm (IDs 1–6), die Räder bleiben unberührt
- `lekiwi_base`  nur die Basis (IDs 7–9), der Arm bleibt unberührt

Alle drei benutzen **dieselbe Kalibrierdatei** `calibration/robots/lekiwi/<id>.json`
(LeRobot-Format mit allen neun Motoren). Wer nur einen Teil kalibriert, ändert nur dessen
Einträge, die anderen bleiben stehen.
"""

from .spec import DeviceSpec, Joint, JointMode, NormMode

LEKIWI_ARM_JOINTS = (
    Joint("arm_shoulder_pan", 1),
    Joint("arm_shoulder_lift", 2),
    Joint("arm_elbow_flex", 3),
    Joint("arm_wrist_flex", 4),
    Joint("arm_wrist_roll", 5, full_turn=True),
    Joint("arm_gripper", 6, norm=NormMode.RANGE_0_100),
)

LEKIWI_BASE_JOINTS = (
    Joint("base_left_wheel", 7, mode=JointMode.VELOCITY, norm=NormMode.RANGE_M100_100),
    Joint("base_back_wheel", 8, mode=JointMode.VELOCITY, norm=NormMode.RANGE_M100_100),
    Joint("base_right_wheel", 9, mode=JointMode.VELOCITY, norm=NormMode.RANGE_M100_100),
)

LEKIWI_JOINTS = LEKIWI_ARM_JOINTS + LEKIWI_BASE_JOINTS

LEKIWI = DeviceSpec(
    type="lekiwi",
    description="LeKiwi (Basis mit drei Allseitenrädern und SO-101-Arm)",
    joints=LEKIWI_JOINTS,
    lerobot_kind="robots",
    lerobot_name="lekiwi",
)

LEKIWI_ARM = DeviceSpec(
    type="lekiwi_arm",
    description="LeKiwi, nur der Arm (Räder unberührt)",
    joints=LEKIWI_ARM_JOINTS,
    lerobot_kind="robots",
    lerobot_name="lekiwi",
)

LEKIWI_BASE = DeviceSpec(
    type="lekiwi_base",
    description="LeKiwi, nur die Basis (Arm unberührt)",
    joints=LEKIWI_BASE_JOINTS,
    lerobot_kind="robots",
    lerobot_name="lekiwi",
)
