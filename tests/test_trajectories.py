import pytest

from so101_lab import register_info
from so101_lab import registers as regs
from so101_lab.trajectories import Trajectory, TrajectoryError, load_trajectory, save_trajectory


def make(name="beispiel") -> Trajectory:
    return Trajectory(
        name=name,
        joints=("shoulder_pan", "gripper"),
        times=[0.0, 0.5, 1.0],
        positions=[(2048, 100), (2100, 150), (2200, 200)],
        meta={"device": "follower", "calibration_id": "follower_1"},
    )


def test_roundtrip(tmp_path):
    csv_path, json_path = save_trajectory(make(), tmp_path)
    assert csv_path.read_text().splitlines()[0] == "t_s,shoulder_pan,gripper"
    loaded = load_trajectory(csv_path)
    assert loaded.joints == ("shoulder_pan", "gripper")
    assert loaded.positions == make().positions
    assert loaded.times == [0.0, 0.5, 1.0]
    assert loaded.meta["calibration_id"] == "follower_1"
    assert loaded.meta["unit"] == "raw_steps"
    assert loaded.sample_rate_hz == pytest.approx(2.0)
    assert load_trajectory("beispiel", tmp_path).name == "beispiel"


def test_refuses_overwrite(tmp_path):
    save_trajectory(make(), tmp_path)
    with pytest.raises(TrajectoryError, match="gibt es schon"):
        save_trajectory(make(), tmp_path)
    save_trajectory(make(), tmp_path, overwrite=True)


@pytest.mark.parametrize("name", ["", "../x", "a b", "-x", "ä"])
def test_rejects_unsafe_names(name, tmp_path):
    with pytest.raises(TrajectoryError):
        save_trajectory(make(name), tmp_path)


def test_rejects_invalid_content(tmp_path):
    bad = make()
    bad.times = [0.0, 1.0, 0.5]
    with pytest.raises(TrajectoryError, match="rückwärts"):
        save_trajectory(bad, tmp_path)
    short = make()
    short.times, short.positions = [0.0], [(1, 2)]
    with pytest.raises(TrajectoryError, match="mindestens zwei"):
        save_trajectory(short, tmp_path)


def test_load_rejects_broken_csv(tmp_path):
    (tmp_path / "kaputt.csv").write_text("zeit,a\n0,1\n1,x\n", encoding="utf-8")
    with pytest.raises(TrajectoryError):
        load_trajectory("kaputt", tmp_path)
    (tmp_path / "kaputt.csv").write_text("t_s,a\n0,1\n1,x\n", encoding="utf-8")
    with pytest.raises(TrajectoryError, match="nicht lesbar"):
        load_trajectory("kaputt", tmp_path)


def test_register_info_covers_documented_registers():
    for reg in regs.registers():
        if reg.address < regs.RAM_END:
            assert reg.name in register_info.INFO, reg.name
    assert set(register_info.INFO) <= {r.name for r in regs.registers()}
    assert register_info.info("dts").text.startswith("in der Feetech-Tabelle")
