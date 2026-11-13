"""LeKiwi: dreirädrige Basis mit aufgesetztem SO-101-Follower an einem gemeinsamen Bus.

Quelle: lerobot/robots/lekiwi/lekiwi.py (LeRobot 0.7.0). Die Kinematik der Basis
folgt in Phase 9.
"""

from .spec import DeviceSpec, Joint, JointMode, NormMode

LEKIWI_JOINTS = (
    Joint("arm_shoulder_pan", 1),
    Joint("arm_shoulder_lift", 2),
    Joint("arm_elbow_flex", 3),
    Joint("arm_wrist_flex", 4),
    Joint("arm_wrist_roll", 5, full_turn=True),
    Joint("arm_gripper", 6, norm=NormMode.RANGE_0_100),
    Joint("base_left_wheel", 7, mode=JointMode.VELOCITY, norm=NormMode.RANGE_M100_100),
    Joint("base_back_wheel", 8, mode=JointMode.VELOCITY, norm=NormMode.RANGE_M100_100),
    Joint("base_right_wheel", 9, mode=JointMode.VELOCITY, norm=NormMode.RANGE_M100_100),
)

LEKIWI = DeviceSpec(
    type="lekiwi",
    description="LeKiwi (Basis mit drei Allseitenrädern und SO-101-Arm)",
    joints=LEKIWI_JOINTS,
    lerobot_kind="robots",
    lerobot_name="lekiwi",
)
