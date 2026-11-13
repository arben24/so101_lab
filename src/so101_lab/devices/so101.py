"""SO-101 Follower und Leader.

Quelle: lerobot/robots/so_follower/so_follower.py und
lerobot/teleoperators/so_leader/so_leader.py (LeRobot 0.7.0). Leader und Follower haben
dieselben Gelenknamen und IDs, aber andere Servo-Varianten und Netzteile (Kapitel 01).
"""

from .spec import DeviceSpec, Joint, NormMode

SO101_JOINTS = (
    Joint("shoulder_pan", 1),
    Joint("shoulder_lift", 2),
    Joint("elbow_flex", 3),
    Joint("wrist_flex", 4),
    Joint("wrist_roll", 5, full_turn=True),
    Joint("gripper", 6, norm=NormMode.RANGE_0_100),
)

SO101_FOLLOWER = DeviceSpec(
    type="so101_follower",
    description="SO-101 Follower (Folgearm)",
    joints=SO101_JOINTS,
    lerobot_kind="robots",
    lerobot_name="so_follower",
)

SO101_LEADER = DeviceSpec(
    type="so101_leader",
    description="SO-101 Leader (wird von Hand geführt)",
    joints=SO101_JOINTS,
    lerobot_kind="teleoperators",
    lerobot_name="so_leader",
)
