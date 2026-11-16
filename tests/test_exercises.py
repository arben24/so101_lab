"""Übungen: Musterlösungen laufen gegen die Simulation, Vorlagen melden „Noch zu lösen“."""

import ast
import importlib.util
import re
from pathlib import Path

import pytest

from so101_lab import kinematics
from so101_lab.backends.base import ServoState
from so101_lab.calibration import MotorCalibration, raw_to_joint, save_calibration
from so101_lab.devices import get_device_spec
from so101_lab.trajectories import load_trajectory

ROOT = Path(__file__).resolve().parent.parent
EXERCISES = ROOT / "exercises"
NAMES = sorted(p.stem for p in EXERCISES.glob("uebung_*.py"))


def load(name: str, solution: bool = True):
    path = EXERCISES / ("loesungen" if solution else "") / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"{'sol' if solution else 'tpl'}_{name}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def solution(number: int):
    return load(next(n for n in NAMES if n.startswith(f"uebung_0{number}_")))


@pytest.fixture(autouse=True)
def project(tmp_path, monkeypatch):
    """Eigener Projektordner mit simuliertem Arm und LeKiwi, Kalibrierdatei und ohne Netz."""
    monkeypatch.setenv("SO101_LAB_ROOT", str(tmp_path))
    monkeypatch.setenv("HF_LEROBOT_CALIBRATION", str(tmp_path / "keine"))
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "arms.toml").write_text(
        '[[device]]\nname = "demo"\ntype = "so101_follower"\nbackend = "mock"\n', encoding="utf-8"
    )
    monkeypatch.setenv("SO101_LAB_CONFIG", str(tmp_path / "config" / "arms.toml"))
    spec = get_device_spec("so101_follower")
    save_calibration(
        tmp_path / "calibration" / "robots" / "so_follower" / "follower_1.json",
        {
            j.name: MotorCalibration(j.motor_id, 0, 0, *((0, 4095) if j.full_turn else (800, 3300)))
            for j in spec.position_joints
        },
    )
    return tmp_path


def test_every_exercise_has_template_solution_and_docs():
    assert len(NAMES) == 7
    docs = (ROOT / "docs" / "uebungen.md").read_text(encoding="utf-8")
    for name in NAMES:
        assert (EXERCISES / "loesungen" / f"{name}.py").is_file(), f"Musterlösung zu {name} fehlt"
        assert name in docs, f"{name} steht nicht in docs/uebungen.md"
    assert not (set(p.stem for p in (EXERCISES / "loesungen").glob("uebung_*.py")) - set(NAMES))


# ------------------------------------------------------------------ Vorlagen


def stubbed(tree: ast.Module) -> set[str]:
    """Funktionen, deren Rumpf nur aus `raise NotImplementedError` besteht (nach dem Docstring)."""
    stubs = set()
    for node in tree.body:
        if isinstance(node, ast.FunctionDef):
            body = [
                n
                for n in node.body
                if not (isinstance(n, ast.Expr) and isinstance(getattr(n, "value", None), ast.Constant))
            ]
            if len(body) == 1 and isinstance(body[0], ast.Raise):
                stubs.add(node.name)
    return stubs


@pytest.mark.parametrize("name", NAMES)
def test_template_equals_solution_except_the_gaps(name):
    """Außerhalb der Lücken sind Vorlage und Lösung identisch (Vorlage wird aus der Lösung erzeugt)."""
    template = ast.parse((EXERCISES / f"{name}.py").read_text(encoding="utf-8"))
    sol = ast.parse((EXERCISES / "loesungen" / f"{name}.py").read_text(encoding="utf-8"))
    gaps = stubbed(template)
    assert gaps, "eine Vorlage ohne Aufgabe"
    sol_funcs = {n.name: n for n in sol.body if isinstance(n, ast.FunctionDef)}
    tpl_funcs = {n.name: n for n in template.body if isinstance(n, ast.FunctionDef)}
    assert set(sol_funcs) == set(tpl_funcs)
    for fname, node in tpl_funcs.items():
        if fname in gaps:
            assert ast.dump(node.args) == ast.dump(sol_funcs[fname].args)  # gleiche Signatur
            assert any(isinstance(n, ast.Expr) for n in node.body[:1])  # Docstring bleibt
        else:
            assert ast.dump(node) == ast.dump(sol_funcs[fname]), f"{fname} weicht ab"


# Aufruf, der bis zur ersten Lücke kommt
TEMPLATE_ARGS = {
    "uebung_01_messen": ["--device", "demo"],
    "uebung_02_rohwert_grad": [],
    "uebung_03_p_anteil": ["--gelenk", "wrist_flex", "--device", "demo", "--ohne-warten"],
    "uebung_04_glaetten": ["--beispiel"],
    "uebung_05_schutzfunktionen": ["--device", "demo", "--tipps", "a", "b"],
    "uebung_06_kinematik": [],
    "uebung_07_dataset_training": ["--name", "gibtsnicht"],
}


@pytest.mark.parametrize("name", NAMES)
def test_template_says_what_is_left_to_do(name, capsys, monkeypatch):
    template = load(name, solution=False)
    monkeypatch.setattr("sys.argv", [name, *TEMPLATE_ARGS[name]])
    with pytest.raises(SystemExit) as stop:
        template.run_script(lambda: template.main(TEMPLATE_ARGS[name]))
    assert stop.value.code == 2
    assert "Noch zu lösen:" in capsys.readouterr().err


def test_template_3_does_not_touch_the_arm_before_the_exercise_works(project):
    """Die Vorlage von Übung 3 bricht ab, bevor sie P schreibt oder etwas bewegt."""
    template = load("uebung_03_p_anteil", solution=False)
    with pytest.raises(NotImplementedError):
        template.main(["--gelenk", "wrist_flex", "--device", "demo", "--ohne-warten"])


# ------------------------------------------------------------------ Lösungen


def state(voltage=12.0, temperature=30, status=0):
    return ServoState(1, 2048, 0, 0, voltage, temperature, status, False)


def test_1_flags_voltage_temperature_and_error_bits(capsys):
    sol = solution(1)
    assert sol.find_problems({"a": state()}, 12.0) == []
    found = sol.find_problems(
        {"a": state(voltage=9.0), "b": state(temperature=60), "c": state(status=0b100001)}, 12.0
    )
    assert len(found) == 4
    assert any("a: Spannung 9.0 V" in f for f in found)
    assert any("b: Temperatur 60" in f for f in found)
    assert any("Spannung" in f and f.startswith("c:") for f in found) and any("Überlast" in f for f in found)
    assert sol.main(["--device", "demo"]) == 0
    assert "Alles im grünen Bereich" in capsys.readouterr().out
    assert sol.main(["--device", "demo", "--soll-volt", "7.4"]) == 1  # falsches Netzteil gedacht


def test_2_degrees_match_the_repo_and_roundtrip(capsys):
    sol = solution(2)
    joint = get_device_spec("so101_follower").joint("elbow_flex")
    cal = MotorCalibration(3, 0, 0, 856, 3068)
    for raw in (856, 1500, 1962, 3068):
        assert sol.raw_to_degrees(raw, 856, 3068) == pytest.approx(raw_to_joint(raw, joint, cal))
        assert abs(sol.degrees_to_raw(sol.raw_to_degrees(raw, 856, 3068), 856, 3068) - raw) <= 1
    assert sol.raw_to_degrees(1962, 856, 3068) == pytest.approx(0)
    assert sol.main([]) == 0
    assert "wrist_roll" in capsys.readouterr().out
    assert sol.main(["--kalibrierung", "gibtsnicht"]) == 1


def test_3_step_metrics():
    sol = solution(3)
    samples = [(0.0, 0), (0.1, 60), (0.2, 112), (0.3, 105), (0.4, 99), (0.5, 100), (0.6, 101)]
    m = sol.step_metrics(samples, 0, 100, tolerance=5)
    assert m == {"settle_s": 0.3, "overshoot": 12, "final_error": 1}
    down = sol.step_metrics([(0.0, 100), (0.1, 40), (0.2, 3)], 100, 0, tolerance=5)
    assert down["settle_s"] == 0.2 and down["overshoot"] == 0  # abwärts: kein Überschwingen
    never = sol.step_metrics([(0.0, 0), (0.5, 50)], 0, 100)
    assert never["settle_s"] is None and never["final_error"] == -50


def test_3_runs_on_simulation_and_restores_p(capsys, monkeypatch):
    sol = solution(3)
    buses = []
    from so101_lab.backends.mock import MockBackend

    original = MockBackend.for_ids.__func__
    monkeypatch.setattr(
        MockBackend,
        "for_ids",
        classmethod(lambda cls, *a, **k: buses.append(original(cls, *a, **k)) or buses[-1]),
    )
    assert sol.main(["--gelenk", "wrist_flex", "--device", "demo", "--ohne-warten", "--p", "8", "16"]) == 0
    out = capsys.readouterr().out
    assert "p_coefficient steht wieder auf 32" in out
    (bus,) = buses
    assert bus.servo(4).values["p_coefficient"] == 32
    written = [op[3] for op in bus.log if op[0] == "write" and op[2] == "p_coefficient"]
    assert written == [8, 16, 32]  # beide Testwerte, dann der ursprüngliche
    with pytest.raises(Exception, match="zwischen 1 und"):
        sol.main(["--gelenk", "wrist_flex", "--device", "demo", "--p", "200"])


def test_3_restores_p_even_if_the_measurement_fails(monkeypatch):
    sol = solution(3)
    from so101_lab.backends.mock import MockBackend

    buses = []
    original = MockBackend.for_ids.__func__
    monkeypatch.setattr(
        MockBackend,
        "for_ids",
        classmethod(lambda cls, *a, **k: buses.append(original(cls, *a, **k)) or buses[-1]),
    )

    def broken(*args, **kwargs):
        raise RuntimeError("Messung kaputt")

    monkeypatch.setattr(sol, "measure_step", broken)
    with pytest.raises(RuntimeError):
        sol.main(["--gelenk", "wrist_flex", "--device", "demo", "--ohne-warten", "--p", "8"])
    assert buses[0].servo(4).values["p_coefficient"] == 32


def test_4_moving_average_keeps_the_ends_and_smooths(project, capsys):
    sol = solution(4)
    assert sol.moving_average([0, 0, 9, 0, 0], 3) == [0, 3, 3, 3, 0]
    values = [1.0, 5.0, 2.0, 8.0, 3.0]
    assert sol.moving_average(values, 1) == values
    out = sol.moving_average(values, 5)
    assert out[0] == 1.0 and out[-1] == 3.0
    with pytest.raises(ValueError, match="ungerade"):
        sol.moving_average(values, 4)
    assert sol.roughness([0, 1, 2, 3]) == 0  # gerade Linie
    assert sol.roughness([0, 2, 0]) == 4

    assert sol.main(["--beispiel", "--fenster", "7"]) == 0
    before, after = load_trajectory("beispiel_wackelig"), load_trajectory("beispiel_wackelig_glatt")
    assert after.times == before.times and after.joints == before.joints
    col = lambda t: [row[0] for row in t.positions]  # noqa: E731
    assert sol.roughness(col(after)) < sol.roughness(col(before)) / 3
    assert after.positions[0] == before.positions[0] and after.positions[-1] == before.positions[-1]
    assert after.meta["smoothed_window"] == 7 and all(isinstance(v, int) for v in after.positions[5])
    assert "07_replay_trajectory.py" in capsys.readouterr().out


def test_5_protection_experiments(capsys):
    sol = solution(5)
    assert sol.main(["--device", "demo", "--tipps", "a", "b", "c", "d"]) == 0
    out = capsys.readouterr().out
    assert "gesendet 4095 (Grenze 4095)" in out  # Gelenkgrenze, ohne Sprungbegrenzung
    assert "Ist 2048, gewünscht 4048, gesendet 2448" in out  # Sprungbegrenzung 400
    assert "Register 'p_coefficient' ist nicht freigegeben" in out
    assert "Wert 9999 liegt außerhalb" in out
    assert "Dein Tipp:  c" in out


def test_6_kinematics_by_hand_matches_the_repo(capsys):
    sol = solution(6)
    assert sol.wheel_speeds(0.1, 0, 0) == {
        "base_left_wheel": -1129,
        "base_back_wheel": 0,
        "base_right_wheel": 1129,
    }
    assert sol.wheel_speeds(0, 0, 30) == dict.fromkeys(sol.WHEELS, 853)
    fast = sol.wheel_speeds(-0.2, 0.05, -45)
    limited = sol.limit_speeds(fast, 3000)
    assert max(abs(v) for v in limited.values()) == 3000
    repo = kinematics.body_to_wheels(-0.2, 0.05, -45)
    assert all(abs(limited[w] - repo[w]) <= 1 for w in sol.WHEELS)
    assert sol.limit_speeds({"a": 100, "b": -200}, 3000) == {"a": 100, "b": -200}
    x, y, theta = sol.circle_command(0.5, 0.1)
    assert (x, y) == (0.1, 0.0) and theta == pytest.approx(11.459, abs=1e-3)
    assert sol.max_forward_speed() == pytest.approx(0.2657, abs=1e-3)
    assert sol.main([]) == 0
    assert "Alles stimmt." in capsys.readouterr().out


@pytest.fixture
def dataset(project):
    from so101_lab.cameras import CameraSpec, open_camera
    from so101_lab.lab_dataset import LabDataset

    joints = [j.name for j in get_device_spec("so101_follower").position_joints]
    camera = open_camera(CameraSpec("front", "test", width=64, height=48, fps=30))
    try:
        import time

        end = time.monotonic() + 5
        while camera.latest() is None and time.monotonic() < end:
            time.sleep(0.02)
        ds = LabDataset("uebung", joints, {"front": camera}, 30, "so_follower", "Test")
        for _ in range(2):
            ds.start_episode(lambda: (dict.fromkeys(joints, 1.0), dict.fromkeys(joints, 2.0)))
            time.sleep(0.3)
            ds.stop_episode()
        ds.close()
    finally:
        camera.close()
    return "uebung"


def test_7_describes_dataset_and_builds_training_command(dataset, capsys, monkeypatch):
    sol = solution(7)
    from so101_lab.lab_dataset import dataset_path

    d = sol.describe_dataset(dataset_path(dataset))
    assert d["episodes"] == 2 and d["fps"] == 30 and d["cameras"] == ["front"] and d["frames"] > 5
    assert d["action"][0] == "shoulder_pan.pos" and d["robot_type"] == "so_follower"
    with pytest.raises(FileNotFoundError):
        sol.describe_dataset(dataset_path("gibtsnicht"))

    command = sol.training_command("uebung", Path("/x/root"), 100, 4, "cpu")
    assert command[:5] == ["uv", "run", "--group", "training", "lerobot-train"]
    assert "--dataset.root=/x/root" in command and "--policy.push_to_hub=false" in command  # nie zum Hub
    assert (
        "--steps=100" in command and "--batch_size=4" in command and "--dataset.repo_id=lab/uebung" in command
    )

    assert sol.main(["--name", dataset]) == 0
    out = capsys.readouterr().out
    assert "2 Episoden" in out and "Trainingsbefehl" in out and "sehr wenig" in out

    called = []
    monkeypatch.setattr(sol.subprocess, "call", lambda cmd, env=None: called.append((cmd, env)) or 0)
    assert sol.main(["--name", dataset, "--starten"]) == 0
    assert called[0][0][:5] == ["uv", "run", "--group", "training", "lerobot-train"]
    assert called[0][1]["HF_HUB_OFFLINE"] == "1"


def test_every_option_of_the_training_command_exists_in_lerobot():
    """Die Optionen stehen in `lerobot-train --help` (Optionsnamen aus der installierten Version)."""
    import subprocess
    import sys

    help_text = subprocess.run(
        [sys.executable, "-m", "lerobot.scripts.lerobot_train", "--policy.type=act", "--help"],
        capture_output=True,
        text=True,
    ).stdout
    if not help_text:
        pytest.skip("lerobot-train --help liefert nichts")
    command = solution(7).training_command("n", Path("/r"), 1, 1, "cpu")
    for option in re.findall(r"^--([\w.]+)=", "\n".join(command), flags=re.M):
        assert f"--{option}" in help_text, option
