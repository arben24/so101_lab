import pytest

from so101_lab.calibration import (
    MotorCalibration,
    find_calibration_file,
    joint_to_raw,
    load_calibration,
    raw_to_joint,
    save_calibration,
)
from so101_lab.devices import get_device_spec

SPEC = get_device_spec("so101_follower")
CAL = {
    "shoulder_pan": MotorCalibration(1, 0, -123, 800, 3200),
    "gripper": MotorCalibration(6, 0, 10, 2000, 3400),
}


def test_roundtrip_json(tmp_path):
    path = tmp_path / "x.json"
    save_calibration(path, CAL)
    assert load_calibration(path) == CAL


def test_find_prefers_repo_then_lerobot(tmp_path, monkeypatch):
    monkeypatch.setenv("SO101_LAB_ROOT", str(tmp_path / "repo"))
    monkeypatch.setenv("HF_LEROBOT_CALIBRATION", str(tmp_path / "lerobot"))
    assert find_calibration_file(SPEC, "f1") is None
    lerobot_file = tmp_path / "lerobot" / "robots" / "so_follower" / "f1.json"
    save_calibration(lerobot_file, CAL)
    assert find_calibration_file(SPEC, "f1") == lerobot_file
    repo_file = tmp_path / "repo" / "calibration" / "robots" / "so_follower" / "f1.json"
    save_calibration(repo_file, CAL)
    assert find_calibration_file(SPEC, "f1") == repo_file


@pytest.mark.parametrize("raw", [800, 1500, 2000, 3200])
@pytest.mark.parametrize("name", ["shoulder_pan", "gripper"])
def test_normalization_matches_lerobot(raw, name):
    """Gleiche Zahlen wie LeRobots SerialMotorsBus._normalize/_unnormalize (ohne Hardware)."""
    from lerobot.motors import Motor, MotorNormMode, SerialMotorsBus
    from lerobot.motors import MotorCalibration as LrCal

    joint = SPEC.joint(name)
    norm = MotorNormMode.DEGREES if name == "shoulder_pan" else MotorNormMode.RANGE_0_100
    c = CAL[name]
    bus = SerialMotorsBus(
        "/dev/null",
        {name: Motor(c.id, "sts3215", norm)},
        {name: LrCal(c.id, c.drive_mode, c.homing_offset, c.range_min, c.range_max)},
    )
    expected = bus._normalize({c.id: raw})[c.id]
    assert raw_to_joint(raw, joint, c) == pytest.approx(expected)
    assert joint_to_raw(expected, joint, c) == bus._unnormalize({c.id: expected})[c.id]
