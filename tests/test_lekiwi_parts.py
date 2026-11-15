"""LeKiwi als Ganzes, nur Arm, nur Basis: eine Hardware, drei Gerätetypen, eine Kalibrierdatei."""

import json
from pathlib import Path

import pytest

from so101_lab.arm import Arm
from so101_lab.backends.mock import MockBackend
from so101_lab.calibrate import CalibrationRun
from so101_lab.calibration_files import folders, validate
from so101_lab.config import DeviceConfig, load_config
from so101_lab.devices import DEVICE_TYPES, get_device_spec

ROOT = Path(__file__).resolve().parents[1]
LEKIWI = get_device_spec("lekiwi")
ARM = get_device_spec("lekiwi_arm")
BASE = get_device_spec("lekiwi_base")


@pytest.fixture(autouse=True)
def project(tmp_path, monkeypatch):
    monkeypatch.setenv("SO101_LAB_ROOT", str(tmp_path))
    return tmp_path


def make_arm(spec, clock):
    # Immer alle neun Servos am Bus, wie an der echten Hardware
    bus = MockBackend.for_ids(LEKIWI.ids, wheel_ids=[w.motor_id for w in LEKIWI.wheels], clock=clock)
    bus.connect()
    cfg = DeviceConfig(name=spec.type, type=spec.type, backend="mock", calibration_id="kiwi")
    return Arm(bus, spec, cfg), bus


def test_parts_split_the_motors():
    assert {"lekiwi", "lekiwi_arm", "lekiwi_base"} <= set(DEVICE_TYPES)
    assert ARM.ids == [1, 2, 3, 4, 5, 6] and not ARM.wheels
    assert BASE.ids == [7, 8, 9] and not BASE.position_joints
    assert ARM.joints + BASE.joints == LEKIWI.joints
    for spec in (ARM, BASE):
        assert (spec.lerobot_kind, spec.lerobot_name) == ("robots", "lekiwi")
    # Kalibrierordner robots/lekiwi wird gegen den ganzen LeKiwi geprüft
    assert folders()[("robots", "lekiwi")].type == "lekiwi"


def calibrate(arm, bus):
    run = CalibrationRun.start(arm)
    run.set_middle()
    for name in run.recorded:
        servo = arm.spec.joint(name).motor_id
        for raw in (1500, 2600, 2047):
            bus.move_by_hand(servo, raw)
            run.track(arm.read_positions())
    return run.finish()


def test_calibrating_arm_only_keeps_wheels_in_file(clock):
    kiwi, bus = make_arm(LEKIWI, clock)
    path = calibrate(kiwi, bus)
    before = json.loads(path.read_text())
    assert list(before) == [j.name for j in LEKIWI.joints]

    arm, bus2 = make_arm(ARM, clock)
    bus2.move_by_hand(1, 1000)
    assert calibrate(arm, bus2) == path
    after = json.loads(path.read_text())
    assert list(after) == list(before)  # gleiche Einträge, gleiche Reihenfolge
    for wheel in BASE.joints:
        assert after[wheel.name] == before[wheel.name]
    assert after["arm_shoulder_pan"] != before["arm_shoulder_pan"]
    assert validate(after, LEKIWI)[1] == []
    assert arm.is_calibrated


def test_calibrating_base_only_keeps_arm(clock):
    kiwi, bus = make_arm(LEKIWI, clock)
    path = calibrate(kiwi, bus)
    before = json.loads(path.read_text())
    base, bus2 = make_arm(BASE, clock)
    calibrate(base, bus2)
    after = json.loads(path.read_text())
    for joint in ARM.joints:
        assert after[joint.name] == before[joint.name]
    assert after["base_left_wheel"] == {
        "id": 7,
        "drive_mode": 0,
        "homing_offset": 0,
        "range_min": 0,
        "range_max": 4095,
    }


def test_base_only_touches_only_wheels(clock):
    base, bus = make_arm(BASE, clock)
    base.enable_torque()
    assert not any(bus.servo(i).values["torque_enable"] for i in ARM.ids)
    assert all(bus.servo(i).values["torque_enable"] for i in BASE.ids)
    base.set_wheel_velocities({"base_left_wheel": 500})
    base.safe_shutdown()
    assert bus.servo(7).values["goal_velocity"] == 0


def test_repo_config_and_calibration_file():
    cfg = load_config(ROOT / "config" / "arms.toml")
    for name in ("lekiwi", "lekiwi_arm", "lekiwi_base"):
        assert cfg.device(name).type == name
        assert cfg.device(name).calibration_id == "lekiwi_1"
    data = json.loads((ROOT / "calibration" / "robots" / "lekiwi" / "lekiwi_1.json").read_text())
    assert validate(data, LEKIWI)[1] == []
