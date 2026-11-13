"""Kalibrierung Schritt für Schritt gegen das Mock-Backend."""

import json

import pytest

from so101_lab.arm import Arm
from so101_lab.backends import BackendError
from so101_lab.backends.mock import MockBackend
from so101_lab.calibrate import CalibrationRun, Stage
from so101_lab.config import DeviceConfig
from so101_lab.devices import get_device_spec

FOLLOWER = get_device_spec("so101_follower")
LEKIWI = get_device_spec("lekiwi")


@pytest.fixture(autouse=True)
def project(tmp_path, monkeypatch):
    monkeypatch.setenv("SO101_LAB_ROOT", str(tmp_path))
    return tmp_path


def make_arm(spec=FOLLOWER, clock=None):
    wheels = [w.motor_id for w in spec.wheels]
    bus = MockBackend.for_ids(spec.ids, wheel_ids=wheels, **({"clock": clock} if clock else {}))
    bus.connect()
    cfg = DeviceConfig(name="f", type=spec.type, backend="mock", calibration_id="lab_1")
    return Arm(bus, spec, cfg), bus


def eeprom(bus, servo_id, name):
    return bus.servo(servo_id).values[name]


def test_full_run_writes_offsets_limits_and_file(project, clock):
    arm, bus = make_arm(clock=clock)
    # Ausgangslage: Gelenke stehen nicht in der Mitte des Encoders
    for i, raw in zip(range(1, 7), (1000, 3000, 2048, 500, 2500, 1500), strict=True):
        bus.move_by_hand(i, raw)
    run = CalibrationRun.start(arm)
    assert run.target_path == project / "calibration" / "robots" / "so_follower" / "lab_1.json"
    assert not run.written

    offsets = run.set_middle()
    assert offsets["shoulder_pan"] == 1000 - 2047
    assert offsets["shoulder_lift"] == 3000 - 2047
    assert arm.read_positions()["shoulder_pan"] == 2047  # Mitte ist jetzt 2047
    assert run.stage is Stage.RANGE

    # Bereich abfahren (wrist_roll ist Volldreher und wird nicht aufgezeichnet)
    for name in run.recorded:
        servo = arm.spec.joint(name).motor_id
        for raw in (1200, 2900, 2047):
            bus.move_by_hand(servo, raw)
            run.track(arm.read_positions())
    path = run.finish()

    data = json.loads(path.read_text())
    assert data["shoulder_pan"] == {
        "id": 1,
        "drive_mode": 0,
        "homing_offset": 1000 - 2047,
        "range_min": 1200,
        "range_max": 2900,
    }
    assert data["wrist_roll"]["range_min"] == 0 and data["wrist_roll"]["range_max"] == 4095
    assert eeprom(bus, 1, "min_position_limit") == 1200
    assert eeprom(bus, 1, "max_position_limit") == 2900
    assert bus.servo(1).persisted["homing_offset"] == 1000 - 2047  # dauerhaft gespeichert
    assert arm.limits("shoulder_pan") == (1200, 2900)  # Grenzen sofort aktiv
    assert arm.calibration_path == path
    assert all(s.values["torque_enable"] == 0 for s in bus.servos)


def test_offset_is_clamped_at_encoder_end():
    arm, bus = make_arm()
    bus.move_by_hand(1, 4095)
    run = CalibrationRun.start(arm)
    assert run.set_middle()["shoulder_pan"] == 2047  # 4095 − 2047 = 2048 passt nicht ins Register


def test_unmoved_joint_blocks_saving():
    arm, bus = make_arm()
    run = CalibrationRun.start(arm)
    run.set_middle()
    bus.move_by_hand(1, 1500)
    run.track(arm.read_positions())
    with pytest.raises(BackendError, match="Nicht bewegt: shoulder_lift"):
        run.finish()
    assert not run.target_path.exists()


def test_cancel_restores_previous_values():
    arm, bus = make_arm()
    for s in bus.servos:  # alte Kalibrierung im Servo
        s.values.update(homing_offset=-100, min_position_limit=800, max_position_limit=3100)
    bus.move_by_hand(2, 2600)
    run = CalibrationRun.start(arm)
    run.set_middle()
    assert eeprom(bus, 2, "homing_offset") != -100
    run.cancel()
    assert run.stage is Stage.CANCELLED
    for s in bus.servos:
        assert (
            s.values["homing_offset"],
            s.values["min_position_limit"],
            s.values["max_position_limit"],
        ) == (
            -100,
            800,
            3100,
        )


def test_cancel_before_middle_writes_nothing():
    arm, bus = make_arm()
    run = CalibrationRun.start(arm)
    bus.log.clear()
    run.cancel()
    assert not [entry for entry in bus.log if entry[0] in ("write", "sync_write")]


def test_existing_file_is_kept_as_backup(project):
    arm, bus = make_arm()
    target = project / "calibration" / "robots" / "so_follower" / "lab_1.json"
    target.parent.mkdir(parents=True)
    target.write_text("{}\n")
    run = CalibrationRun.start(arm)
    run.set_middle()
    for name in run.recorded:
        bus.move_by_hand(arm.spec.joint(name).motor_id, 2200)
    run.track(arm.read_positions())
    run.finish()
    assert target.with_suffix(".json.bak").read_text() == "{}\n"


def test_lekiwi_wheels_get_full_range_without_homing():
    arm, bus = make_arm(LEKIWI)
    run = CalibrationRun.start(arm)
    run.set_middle()
    for name in run.recorded:
        bus.move_by_hand(arm.spec.joint(name).motor_id, 2300)
    run.track(arm.read_positions())
    data = json.loads(run.finish().read_text())
    assert "base_left_wheel" not in run.recorded
    assert data["base_left_wheel"] == {
        "id": 7,
        "drive_mode": 0,
        "homing_offset": 0,
        "range_min": 0,
        "range_max": 4095,
    }
    assert run.target_path.parent.name == "lekiwi"


def test_wrong_operating_mode_refuses_to_start():
    arm, bus = make_arm()
    bus.servo(3).values["operating_mode"] = 1
    with pytest.raises(BackendError, match="Betriebsart"):
        CalibrationRun.start(arm)
