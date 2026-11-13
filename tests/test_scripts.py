"""Die Lernskripte 01–07 laufen gegen das Mock-Backend (Gerät `demo`), ohne Hardware."""

import importlib.util
from pathlib import Path

import pytest

from so101_lab import registers as regs
from so101_lab.backends.base import BackendError
from so101_lab.backends.mock import MockBackend, MockServo
from so101_lab.trajectories import TrajectoryError, load_trajectory

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"


def load_script(name: str):
    path = next(SCRIPTS.glob(f"{name}_*.py"))
    spec = importlib.util.spec_from_file_location(f"script_{name}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    """Eigener Projektordner: Aufnahmen und Kalibrierung landen nicht im Repo."""
    monkeypatch.setenv("SO101_LAB_ROOT", str(tmp_path))
    monkeypatch.setenv("HF_LEROBOT_CALIBRATION", str(tmp_path / "keine-lerobot-kalibrierung"))
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "arms.toml").write_text(
        '[[device]]\nname = "demo"\ntype = "so101_follower"\nbackend = "mock"\n', encoding="utf-8"
    )
    monkeypatch.setenv("SO101_LAB_CONFIG", str(tmp_path / "config" / "arms.toml"))
    return tmp_path


def run(module, *argv: str) -> int:
    return module.main(["--device", "demo", *argv])


# ------------------------------------------------------------------ 01


def test_01_lists_ports(monkeypatch, capsys):
    module = load_script("01")
    from so101_lab.ports import SerialPortInfo

    port = SerialPortInfo("/dev/ttyACM0", 0x1A86, 0x55D3, "x", "SN1", None)
    monkeypatch.setattr(module, "list_serial_ports", lambda usb_only=True: [port])
    assert module.main([]) == 0
    assert "/dev/ttyACM0" in capsys.readouterr().out


def test_01_without_ports(monkeypatch, capsys):
    module = load_script("01")
    monkeypatch.setattr(module, "list_serial_ports", lambda usb_only=True: [])
    assert module.main([]) == 1
    assert "Kein Port" in capsys.readouterr().out


# ------------------------------------------------------------------ 02


def test_02_prints_every_register_and_changes_nothing(capsys):
    module = load_script("02")
    assert run(module, "--gelenk", "elbow_flex") == 0
    out = capsys.readouterr().out
    for reg in regs.registers():
        assert reg.name in out
    assert "Modell 777 (STS3215)" in out
    assert "KEINE ANTWORT" not in out


def test_02_unknown_joint_is_an_error():
    module = load_script("02")
    with pytest.raises(KeyError):
        run(module, "--gelenk", "gibt_es_nicht")


# ------------------------------------------------------------------ 03


def test_03_moves_and_ends_with_torque_off(monkeypatch, capsys):
    module = load_script("03")
    created = {}
    original = MockBackend.for_ids.__func__

    def remember(cls, *a, **kw):
        created["bus"] = original(cls, *a, **kw)
        return created["bus"]

    monkeypatch.setattr(MockBackend, "for_ids", classmethod(remember))
    assert run(module, "--gelenk", "wrist_flex", "--delta", "100", "--tempo", "3000", "--ohne-warten") == 0
    out = capsys.readouterr().out
    assert out.count("Ziel") >= 4
    bus = created["bus"]
    assert all(s.values["torque_enable"] == 0 for s in bus.servos)
    assert not bus.is_connected


def test_03_without_confirmation_moves_nothing(capsys):
    module = load_script("03")
    assert run(module, "--gelenk", "wrist_flex") == 1  # kein Terminal: Rückfrage = nein
    assert "nichts bewegt" in capsys.readouterr().out


def test_03_refuses_too_large_delta():
    module = load_script("03")
    with pytest.raises(BackendError, match="größer als der erlaubte Sprung"):
        run(module, "--gelenk", "wrist_flex", "--delta", "1000", "--ohne-warten")


# ------------------------------------------------------------------ 04


def test_04_lists_whitelist(capsys):
    module = load_script("04")
    assert module.main(["--liste"]) == 0
    out = capsys.readouterr().out
    for name in regs.RAM_WRITE_WHITELIST:
        assert name in out


def test_04_writes_whitelisted_register_and_reads_back(capsys):
    module = load_script("04")
    assert run(module, "--gelenk", "wrist_flex", "--register", "acceleration", "--wert", "77") == 0
    out = capsys.readouterr().out
    assert "acceleration = 77  (zurückgelesen)" in out


@pytest.mark.parametrize("register", ["p_coefficient", "homing_offset", "id", "lock"])
def test_04_rejects_registers_outside_whitelist(register):
    module = load_script("04")
    with pytest.raises(BackendError, match="nicht freigegeben"):
        run(module, "--gelenk", "wrist_flex", "--register", register, "--wert", "1")


@pytest.mark.parametrize("register", ["torque_enable", "goal_position"])
def test_04_points_to_script_03_for_dangerous_registers(register):
    module = load_script("04")
    with pytest.raises(BackendError, match="Skript 03"):
        run(module, "--gelenk", "wrist_flex", "--register", register, "--wert", "1")


def test_04_rejects_out_of_range_value():
    module = load_script("04")
    with pytest.raises(BackendError):
        run(module, "--gelenk", "wrist_flex", "--register", "acceleration", "--wert", "300")


# ------------------------------------------------------------------ 05


def one_servo_backend(servo_id: int = 1) -> MockBackend:
    return MockBackend([MockServo.factory_new(servo_id)], baudrate=1_000_000)


def test_05_sets_id_with_one_servo(monkeypatch, capsys):
    module = load_script("05")
    bus = one_servo_backend()
    monkeypatch.setattr(module, "create_backend", lambda cfg, port=None: bus)
    monkeypatch.setattr(module, "confirm", lambda question: True)
    assert run(module, "--gelenk", "gripper") == 0  # ID 6
    assert [s.id for s in bus.servos] == [6]  # EEPROM-Wert ist dauerhaft geändert
    assert "Fertig und geprüft" in capsys.readouterr().out


def test_05_asks_before_writing(monkeypatch):
    module = load_script("05")
    bus = one_servo_backend()
    monkeypatch.setattr(module, "create_backend", lambda cfg, port=None: bus)
    monkeypatch.setattr(module, "confirm", lambda question: False)
    assert run(module, "--gelenk", "gripper") == 1
    assert [s.id for s in bus.servos] == [1]  # nichts geschrieben


def test_05_blocks_with_several_servos():
    module = load_script("05")
    # Gerät demo hat sechs Servos mit IDs 1–6: genau der Fall, den das Skript verhindern soll
    with pytest.raises(BackendError, match="nur EIN Servo"):
        run(module, "--gelenk", "gripper")


def test_05_does_nothing_if_id_is_already_correct(monkeypatch, capsys):
    module = load_script("05")
    bus = one_servo_backend(servo_id=6)
    monkeypatch.setattr(module, "create_backend", lambda cfg, port=None: bus)
    monkeypatch.setattr(module, "confirm", lambda question: pytest.fail("darf nicht fragen"))
    assert run(module, "--gelenk", "gripper") == 0
    assert "Nichts zu tun" in capsys.readouterr().out


# ------------------------------------------------------------------ 06 und 07


def test_06_records_and_07_replays(isolated, capsys):
    record, replay = load_script("06"), load_script("07")
    assert run(record, "--name", "probe", "--dauer", "0.5", "--rate", "20", "--ohne-warten") == 0
    csv_file = isolated / "recordings" / "trajectories" / "probe.csv"
    assert csv_file.exists() and csv_file.with_suffix(".json").exists()
    traj = load_trajectory("probe")
    assert len(traj.times) >= 8
    assert traj.joints[0] == "shoulder_pan" and len(traj.joints) == 6
    capsys.readouterr()
    assert run(replay, "--name", "probe", "--speed", "3", "--ohne-warten") == 0
    assert "Fertig." in capsys.readouterr().out


def test_06_does_not_overwrite(isolated):
    record = load_script("06")
    run(record, "--name", "probe", "--dauer", "0.2", "--rate", "20", "--ohne-warten")
    with pytest.raises(TrajectoryError, match="gibt es schon"):
        run(record, "--name", "probe", "--dauer", "0.2", "--rate", "20", "--ohne-warten")


def test_06_rejects_bad_name():
    record = load_script("06")
    with pytest.raises(TrajectoryError, match="ungültig"):
        run(record, "--name", "../böse", "--dauer", "0.2", "--rate", "20", "--ohne-warten")


def test_07_rejects_foreign_joints(isolated):
    from so101_lab.trajectories import Trajectory, save_trajectory

    save_trajectory(Trajectory("fremd", ("arm_gripper",), [0.0, 1.0], [(2000,), (2100,)]))
    with pytest.raises(BackendError, match="keine Gelenke"):
        run(load_script("07"), "--name", "fremd", "--ohne-warten")


def test_07_rejects_start_pose_outside_limits(isolated):
    """Mit Kalibrierung begrenzt: Startpose außerhalb des erlaubten Bereichs wird nicht angefahren."""
    import json

    cal = isolated / "calibration" / "robots" / "so_follower"
    cal.mkdir(parents=True)
    names = ["shoulder_pan", "shoulder_lift", "elbow_flex", "wrist_flex", "wrist_roll", "gripper"]
    data = {
        n: {"id": i, "drive_mode": 0, "homing_offset": 0, "range_min": 1000, "range_max": 3000}
        for i, n in enumerate(names, start=1)
    }
    (cal / "demo.json").write_text(json.dumps(data), encoding="utf-8")
    from so101_lab.trajectories import Trajectory, save_trajectory

    save_trajectory(Trajectory("zuweit", ("shoulder_pan",), [0.0, 1.0], [(100,), (200,)]))
    with pytest.raises(BackendError, match="außerhalb der Gelenkgrenzen"):
        run(load_script("07"), "--name", "zuweit", "--ohne-warten")


def test_07_missing_recording():
    with pytest.raises(TrajectoryError, match="nicht gefunden"):
        run(load_script("07"), "--name", "gibtsnicht", "--ohne-warten")
