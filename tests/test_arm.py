import pytest

from so101_lab.arm import Arm
from so101_lab.backends import BackendError, ServoNotRespondingError
from so101_lab.backends.mock import MockBackend
from so101_lab.calibration import MotorCalibration
from so101_lab.config import DeviceConfig
from so101_lab.devices import get_device_spec

FOLLOWER = get_device_spec("so101_follower")
LEKIWI = get_device_spec("lekiwi")


def make_arm(bus, spec=FOLLOWER, calibration=None, **cfg):
    config = DeviceConfig(name="t", type=spec.type, backend="mock", calibration_id="t", **cfg)
    return Arm(bus, spec, config, calibration)


def ops(bus):
    return [(op, name) for op, _, name, _ in bus.log]


def test_check_motors_names_missing_joint():
    bus = MockBackend.for_ids([1, 2, 3, 4, 5])
    bus.connect()
    with pytest.raises(ServoNotRespondingError, match="gripper"):
        make_arm(bus).check_motors()


def test_limits_are_set_before_first_goal(mock_bus):
    arm = make_arm(mock_bus)
    arm.move_to({"shoulder_pan": 2100})
    sequence = ops(mock_bus)
    assert sequence.index(("sync_write", "goal_velocity")) < sequence.index(("sync_write", "goal_position"))
    assert sequence.index(("sync_write", "acceleration")) < sequence.index(("sync_write", "goal_position"))


def test_goal_is_clamped_to_calibration(mock_bus):
    cal = {"shoulder_pan": MotorCalibration(1, 0, 0, 1000, 3000)}
    arm = make_arm(mock_bus, calibration=cal, max_relative_target=None)
    assert arm.move_to({"shoulder_pan": 4000}) == {"shoulder_pan": 3000}
    assert arm.move_to({"shoulder_pan": 10}) == {"shoulder_pan": 1000}


def test_goal_step_is_limited(mock_bus):
    arm = make_arm(mock_bus, max_relative_target=100)
    assert arm.move_to({"shoulder_pan": 3000}) == {"shoulder_pan": 2148}


def test_enable_torque_does_not_jump(mock_bus, clock):
    mock_bus.servo(1).values["goal_position"] = 100  # alter Wert im Register
    mock_bus.move_by_hand(1, 2500)
    arm = make_arm(mock_bus)
    arm.enable_torque(["shoulder_pan"])
    clock.advance(1)
    assert mock_bus.read_positions([1])[1] == 2500


def test_safe_shutdown_torque_off(mock_bus):
    arm = make_arm(mock_bus)
    arm.enable_torque()
    arm.safe_shutdown()
    assert all(not s.torque_enabled for s in mock_bus.read_state(FOLLOWER.ids).values())


def test_safe_shutdown_hold_keeps_torque(mock_bus):
    arm = make_arm(mock_bus, safe_state="hold")
    arm.enable_torque()
    arm.safe_shutdown()
    assert all(s.torque_enabled for s in mock_bus.read_state(FOLLOWER.ids).values())


def test_wheels_stop_first_and_are_limited(clock):
    bus = MockBackend.for_ids(LEKIWI.ids, wheel_ids=[7, 8, 9], clock=clock)
    bus.connect()
    arm = make_arm(bus, spec=LEKIWI, max_wheel_velocity=500)
    arm.enable_torque()
    assert arm.set_wheel_velocities({"base_left_wheel": 9999}) == {"base_left_wheel": 500}
    with pytest.raises(BackendError, match="Rad"):
        arm.move_to({"base_left_wheel": 100})
    bus.log.clear()
    arm.safe_shutdown()
    first = bus.log[0]
    assert first[0] == "sync_write" and first[2] == "goal_velocity" and first[3] == {7: 0, 8: 0, 9: 0}


def test_configure_motion_does_not_command_wheels(clock):
    bus = MockBackend.for_ids(LEKIWI.ids, wheel_ids=[7, 8, 9], clock=clock)
    bus.connect()
    make_arm(bus, spec=LEKIWI).configure_motion()
    velocity_writes = [v for op, _, name, v in bus.log if name == "goal_velocity"]
    assert all(not set(v) & {7, 8, 9} for v in velocity_writes)


def test_emergency_stop_turns_torque_off_even_with_hold(mock_bus):
    arm = make_arm(mock_bus, safe_state="hold")
    arm.enable_torque()
    arm.emergency_stop()
    assert all(not s.torque_enabled for s in mock_bus.read_state(FOLLOWER.ids).values())


def test_joint_values_with_calibration(mock_bus):
    cal = {"shoulder_pan": MotorCalibration(1, 0, 0, 1048, 3048)}
    arm = make_arm(mock_bus, calibration=cal)
    values = arm.to_joint_values(arm.read_positions(["shoulder_pan", "elbow_flex"]))
    assert values["shoulder_pan"] == pytest.approx(0.0)
    assert values["elbow_flex"] is None


def test_safety_whitelist(mock_bus):
    from so101_lab.safety import write_ram_register

    write_ram_register(mock_bus, 1, "Acceleration", 10)
    with pytest.raises(BackendError, match="nicht freigegeben"):
        write_ram_register(mock_bus, 1, "lock", 0)
