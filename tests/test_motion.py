"""Aufnehmen, Abspielen, Umrechnen zwischen Armen (motion.py), ohne Bus und mit erfundenen Zeiten."""

import pytest

from so101_lab.calibration import MotorCalibration
from so101_lab.devices import get_device_spec
from so101_lab.motion import (
    APPROACH_MIN_S,
    Converter,
    Player,
    Recorder,
    TeleopFollow,
    calibration_from_dict,
    calibration_to_dict,
    joint_mapping,
    prepare_playback,
    smoothstep,
)
from so101_lab.trajectories import Trajectory

FOLLOWER = get_device_spec("so101_follower")
LEADER = get_device_spec("so101_leader")
LEKIWI = get_device_spec("lekiwi")
JOINTS = [j.name for j in FOLLOWER.position_joints]


def cal(offset=0, lo=1000, hi=3000, prefix=""):
    """Kalibrierung: alle Gelenke mit Bereich lo…hi (wrist_roll 0…4095 wie LeRobot)."""
    result = {}
    for j in FOLLOWER.position_joints:
        low, high = (0, 4095) if j.full_turn else (lo + offset, hi + offset)
        result[prefix + j.name] = MotorCalibration(j.motor_id, 0, 0, low, high)
    return result


def full_range(name):
    return (0, 4095)


def test_smoothstep():
    assert smoothstep(-1) == 0 and smoothstep(0) == 0 and smoothstep(1) == 1 and smoothstep(2) == 1
    assert smoothstep(0.5) == 0.5


def test_recorder_collects_and_stops_at_max():
    rec = Recorder("probe", ["a", "b"], started=10.0, max_s=1.0)
    assert rec.add({"a": 1, "b": 2}, 10.0)
    assert rec.add({"a": 3, "b": 4, "c": 9}, 10.5)
    assert not rec.add({"a": 5, "b": 6}, 11.5)
    traj = rec.trajectory({"device": "x"})
    assert traj.times == [0.0, 0.5] and traj.positions == [(1, 2), (3, 4)]
    assert rec.status(10.5)["samples"] == 2


def test_player_approach_then_interpolated_playback():
    rows = [{"a": 1000}, {"a": 2000}, {"a": 3000}]
    player = Player("p", ["a"], [0.0, 1.0, 2.0], rows, present={"a": 400}, now=0.0, speed=1.0)
    assert player.approach_s == APPROACH_MIN_S  # 600 Schritt Weg: Mindestdauer
    assert player.targets(0.0) == {"a": 400}
    assert player.targets(1.0) == {"a": 700}  # Mitte der Anfahrt (smoothstep 0,5)
    assert player.phase(1.0) == "anfahrt"
    assert player.targets(2.0) == {"a": 1000}  # Startpose
    assert player.targets(2.5) == {"a": 1500}  # zwischen zwei Abtastungen linear
    assert player.phase(3.5) == "abspielen"
    assert player.targets(4.5) == {"a": 3000} and player.finished
    assert player.status(9.0)["elapsed_s"] == player.total_s == 4.0


def test_player_speed_and_long_approach():
    rows = [{"a": 4000}, {"a": 4000}]
    player = Player("p", ["a"], [0.0, 2.0], rows, present={"a": 400}, now=0.0, speed=2.0)
    assert player.approach_s == pytest.approx(12.0)  # 3600 Schritt bei 300 Schritt/s
    assert player.play_s == 1.0
    with pytest.raises(ValueError):
        Player("p", ["a"], [0.0, 1.0], rows, present={"a": 0}, now=0.0, speed=5)


def test_joint_mapping_lekiwi():
    mapping = joint_mapping(LEADER, LEKIWI)
    assert mapping["arm_gripper"] == "gripper" and len(mapping) == 6  # Räder bleiben außen vor
    assert joint_mapping(LEKIWI, FOLLOWER)["shoulder_pan"] == "arm_shoulder_pan"


def test_converter_uses_degrees_between_different_calibrations():
    conv = Converter(LEADER, cal(offset=0), FOLLOWER, cal(offset=500))
    assert conv.calibrated and not conv.identical
    out = conv({"shoulder_pan": 2000, "gripper": 1000})
    assert out["shoulder_pan"] == 2500  # Mitte 2000 → Mitte 2500
    assert out["gripper"] == 1500  # 0 % → 0 %
    assert "Grad" in conv.describe()


def test_converter_identity_and_raw_fallback():
    same = Converter(FOLLOWER, cal(), FOLLOWER, cal())
    assert same.identical and same({"elbow_flex": 1234}) == {"elbow_flex": 1234}
    raw = Converter(LEADER, None, FOLLOWER, cal())
    assert not raw.calibrated and raw({"elbow_flex": 1234}) == {"elbow_flex": 1234}
    assert "Rohwerte" in raw.describe()


def test_calibration_roundtrip_through_dict():
    c = cal(offset=7)
    assert calibration_from_dict(calibration_to_dict(c)) == c
    assert calibration_from_dict(None) is None


def make_traj(device_type="so101_leader", calibration=None, start=2000, meta=None):
    rows = [tuple(start for _ in JOINTS), tuple(start + 100 for _ in JOINTS)]
    meta = {"device_type": device_type, **(meta or {})}
    if calibration is not None:
        meta["calibration"] = calibration_to_dict(calibration)
    return Trajectory("t", tuple(JOINTS), [0.0, 0.5], rows, meta)


def test_prepare_playback_converts_leader_recording():
    traj = make_traj(calibration=cal(offset=0))
    plan = prepare_playback(traj, FOLLOWER, cal(offset=500), full_range)
    assert plan.joints == JOINTS and plan.converter.calibrated
    assert plan.rows[0]["shoulder_pan"] == 2500
    assert any("Grad/Prozent" in n for n in plan.notes)


def test_prepare_playback_rejects_start_far_outside_limits():
    traj = make_traj(device_type="so101_follower", start=100)
    with pytest.raises(ValueError, match="weit außerhalb"):
        prepare_playback(traj, FOLLOWER, None, lambda name: (1000, 3000))


def test_rest_pose_just_beyond_calibrated_range_is_clamped():
    """Echter Fall (14.11.2026): Ruhestellung am Anschlag, elbow_flex 3069 bei range_max 3068."""
    c = cal()
    c["elbow_flex"] = MotorCalibration(3, 0, -968, 856, 3068)
    rows = [tuple(3069 if j == "elbow_flex" else 2000 for j in JOINTS), tuple(2500 for _ in JOINTS)]
    meta = {"device_type": "so101_follower", "calibration": calibration_to_dict(c)}
    traj = Trajectory("t", tuple(JOINTS), [0.0, 0.5], rows, meta)
    limits = {j: (c[j].range_min, c[j].range_max) for j in JOINTS}
    plan = prepare_playback(traj, FOLLOWER, c, limits.__getitem__)
    assert plan.rows[0]["elbow_flex"] == 3068
    assert any("begrenzt" in n and "elbow_flex (1 von 2" in n for n in plan.notes)


def test_without_calibration_playback_works_with_warning():
    traj = make_traj(device_type="so101_follower", start=2000)
    plan = prepare_playback(traj, FOLLOWER, None, full_range)
    assert plan.rows[0]["shoulder_pan"] == 2000
    assert any("Keine Kalibrierdatei" in n for n in plan.notes)


def test_changed_homing_offsets_are_compensated():
    """Aufgenommen mit Offset 100, inzwischen neu kalibriert (Offset 400): Rohwerte um 300 kleiner."""
    traj = make_traj(
        device_type="so101_follower", start=2000, meta={"homing_offsets": dict.fromkeys(JOINTS, 100)}
    )
    plan = prepare_playback(traj, FOLLOWER, None, full_range, current_offsets=dict.fromkeys(JOINTS, 400))
    assert plan.rows[0]["elbow_flex"] == 1700 and plan.rows[1]["elbow_flex"] == 1800
    assert any("Homing-Offsets" in n for n in plan.notes)
    same = prepare_playback(traj, FOLLOWER, None, full_range, current_offsets=dict.fromkeys(JOINTS, 100))
    assert same.rows[0]["elbow_flex"] == 2000 and not any("Homing" in n for n in same.notes)
    old = make_traj(device_type="so101_follower", start=2000)
    plan = prepare_playback(old, FOLLOWER, None, full_range, current_offsets=dict.fromkeys(JOINTS, 400))
    assert any("älteres Format" in n for n in plan.notes)


def test_prepare_playback_on_lekiwi_and_unknown_joints():
    plan = prepare_playback(make_traj(device_type="so101_follower"), LEKIWI, None, full_range)
    assert plan.joints[0] == "arm_shoulder_pan"
    bad = Trajectory("x", ("ellbogen",), [0.0, 1.0], [(1,), (2,)], {})
    with pytest.raises(ValueError, match="passt nicht"):
        prepare_playback(bad, FOLLOWER, None, full_range)


def test_old_recording_with_same_calibration_id_counts_as_calibrated():
    traj = make_traj(device_type="so101_follower", meta={"calibration_id": "f1"})
    assert prepare_playback(traj, FOLLOWER, cal(), full_range, calibration_id="f1").converter.identical
    plan = prepare_playback(traj, FOLLOWER, cal(), full_range, calibration_id="anders")
    assert not plan.converter.calibrated


def test_teleop_needs_calibration_and_approaches_softly():
    with pytest.raises(ValueError, match="Kalibrierung"):
        TeleopFollow("leader", Converter(LEADER, None, FOLLOWER, cal()), {}, 0.0)
    conv = Converter(LEADER, cal(), FOLLOWER, cal())
    present = dict.fromkeys(JOINTS, 2000)
    tele = TeleopFollow("leader", conv, present, now=5.0)
    leader = dict.fromkeys(JOINTS, 2600)
    assert tele.targets(leader, 10.0)["elbow_flex"] == 2000  # Anfahrt beginnt beim ersten Wert
    assert tele.phase(10.5) == "anfahrt"
    assert tele.targets(leader, 11.0)["elbow_flex"] == 2300
    assert tele.targets(leader, 12.0)["elbow_flex"] == 2600
    assert tele.phase(12.0) == "folgt"
    assert tele.targets(dict.fromkeys(JOINTS, 1500), 12.1)["elbow_flex"] == 1500  # folgt direkt
