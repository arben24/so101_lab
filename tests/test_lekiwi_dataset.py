"""LeKiwi im Dataset: Leader führt den Arm, gleichzeitig wird gefahren, Merkmale wie LeRobots LeKiwi."""

import json
import threading
import time

import pytest
from fastapi.testclient import TestClient

from so101_lab import kinematics, motion
from so101_lab.calibration import MotorCalibration, save_calibration
from so101_lab.cameras import CameraSpec, open_camera
from so101_lab.devices import get_device_spec
from so101_lab.lab_dataset import DatasetError, LabDataset
from so101_lab.web.app import LabServer, create_app

LEKIWI = get_device_spec("lekiwi")
ARM_JOINTS = [j.name for j in LEKIWI.position_joints]


def wait_for(condition, timeout=5.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if condition():
            return True
        time.sleep(0.02)
    return False


@pytest.fixture
def root(tmp_path, monkeypatch):
    monkeypatch.setenv("SO101_LAB_ROOT", str(tmp_path))
    monkeypatch.setenv("HF_LEROBOT_CALIBRATION", str(tmp_path / "keine"))
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    monkeypatch.setattr(motion, "APPROACH_MIN_S", 0.2)
    return tmp_path


def lerobot_lekiwi_features() -> list[str]:
    from lerobot.robots.lekiwi.lekiwi import LeKiwi

    return list(LeKiwi._state_ft.fget(None))  # _state_ft benutzt self nicht


def test_dataset_with_base_has_lerobot_lekiwi_features(root):
    camera = open_camera(CameraSpec("front", "test", width=64, height=48, fps=30))
    try:
        assert wait_for(lambda: camera.latest() is not None)
        ds = LabDataset("kiwi", ARM_JOINTS, {"front": camera}, 30, "lekiwi", "fahren", base=True)
        obs = {**dict.fromkeys(ARM_JOINTS, 1.0), "x.vel": 0.1, "y.vel": 0.0, "theta.vel": 5.0}
        act = {**dict.fromkeys(ARM_JOINTS, 2.0), "x.vel": 0.2, "y.vel": 0.0, "theta.vel": 0.0}
        ds.start_episode(lambda: (obs, act))
        time.sleep(0.3)
        assert ds.stop_episode().frames >= 3
        ds.close()
        info = json.loads((root / "recordings/datasets/kiwi/meta/info.json").read_text())
        expected = lerobot_lekiwi_features()
        assert info["features"]["action"]["names"] == expected
        assert info["features"]["observation.state"]["names"] == expected
        assert info["robot_type"] == "lekiwi"
        # Arm-Dataset aus Phase 6 (ohne Basis) lässt sich nicht mit Basis weiterschreiben
        with pytest.raises(DatasetError, match="dort zusätzlich x.vel, y.vel, theta.vel"):
            LabDataset("kiwi", ARM_JOINTS, {"front": camera}, 30, "lekiwi", "fahren", base=False)
    finally:
        camera.close()


def test_base_only_dataset_and_nothing_to_record(root):
    ds = LabDataset("basis", [], {}, 30, "lekiwi", "fahren", base=True)
    ds.close()
    with pytest.raises(DatasetError, match="Nichts aufzunehmen"):
        LabDataset("leer", [], {}, 30, "lekiwi", "x")


# ------------------------------------------------------------------ Web: Leader + Fahren + Episode


def make_lab(root):
    cal = root / "cal"
    save_calibration(
        cal / "kiwi_1.json",
        {
            j.name: MotorCalibration(
                j.motor_id, 0, 0, *((0, 4095) if j.full_turn or j in LEKIWI.wheels else (1000, 3000))
            )
            for j in LEKIWI.joints
        },
    )
    save_calibration(
        cal / "lead_1.json",
        {
            j.name: MotorCalibration(j.motor_id, 0, 0, *((0, 4095) if j.full_turn else (1000, 3000)))
            for j in get_device_spec("so101_leader").position_joints
        },
    )
    (root / "arms.toml").write_text(
        f"""
[[device]]
name = "kiwi"
type = "lekiwi"
backend = "mock"
calibration_id = "kiwi_1"
calibration_dir = "{cal}"
cameras = [{{ name = "front", source = "test", width = 64, height = 48 }}]

[[device]]
name = "leader"
type = "so101_leader"
backend = "mock"
calibration_id = "lead_1"
calibration_dir = "{cal}"
""",
        encoding="utf-8",
    )
    server = LabServer(config_path=str(root / "arms.toml"), watchdog_ms=2000, poll_hz=50)
    return TestClient(create_app(server)), server


class Heartbeat:
    def __init__(self, ws):
        self.ws, self.stop = ws, threading.Event()
        self.thread = threading.Thread(target=self.run, daemon=True)

    def run(self):
        while not self.stop.wait(0.1):
            self.ws.send_json({"type": "heartbeat"})

    def __enter__(self):
        self.thread.start()

    def __exit__(self, *exc):
        self.stop.set()
        self.thread.join()


def test_leader_arm_and_driving_together_into_one_episode(root):
    client, server = make_lab(root)
    with client, client.websocket_connect("/ws") as ws:
        me = ws.receive_json()["client_id"]
        with Heartbeat(ws):
            for name in ("leader", "kiwi"):
                assert client.post(f"/api/devices/{name}/connect", json={}).status_code == 200
            client.post("/api/devices/kiwi/control", json={"client_id": me})
            leader_bus = server.sessions["leader"].arm.backend
            leader_bus.move_by_hand(1, 2300)  # shoulder_pan des Leaders

            r = client.post("/api/devices/kiwi/teleop/start", json={"client_id": me, "leader": "leader"})
            assert r.status_code == 200, r.text
            r = client.post(
                "/api/devices/kiwi/dataset/open", json={"client_id": me, "name": "kiwi_web", "task": "fahren"}
            )
            assert r.status_code == 200, r.text
            assert r.json()["base"] is True
            assert (
                client.post("/api/devices/kiwi/dataset/episode/start", json={"client_id": me}).status_code
                == 200
            )

            # 0,6 s fahren, während der Arm dem Leader folgt
            for _ in range(6):
                r = client.post("/api/devices/kiwi/drive", json={"client_id": me, "x": 0.1})
                assert r.status_code == 200, r.text
                time.sleep(0.1)
            client.post("/api/devices/kiwi/drive", json={"client_id": me})
            time.sleep(0.3)
            kiwi_bus = server.sessions["kiwi"].arm.backend
            assert wait_for(lambda: abs(kiwi_bus.servo(1).values["present_position"] - 2300) < 30)
            r = client.post("/api/devices/kiwi/dataset/episode/stop", json={"client_id": me, "keep": True})
            assert r.json()["saved_episodes"] == 1
            client.post("/api/devices/kiwi/dataset/close", json={"client_id": me})

    from lerobot.datasets.lerobot_dataset import LeRobotDataset

    loaded = LeRobotDataset("lab/kiwi_web", root=root / "recordings" / "datasets" / "kiwi_web")
    names = loaded.meta.features["action"]["names"]
    assert names == lerobot_lekiwi_features()
    x = names.index("x.vel")
    pan = names.index("arm_shoulder_pan.pos")
    actions = [loaded[i]["action"] for i in range(loaded.num_frames)]
    states = [loaded[i]["observation.state"] for i in range(loaded.num_frames)]
    driving = [i for i, a in enumerate(actions) if float(a[x]) == pytest.approx(0.1)]
    assert driving, "kein Frame mit Fahrbefehl"
    assert any(float(a[x]) == 0.0 for a in actions)  # vor und nach dem Fahren: 0
    # Beobachtung: aus dem gemessenen Radtempo zurückgerechnet (Mock dreht sofort mit dem Sollwert)
    assert any(float(states[i][x]) == pytest.approx(0.1, abs=0.01) for i in driving)
    # Arm-Aktion vom Leader: 2300 Schritt bei Bereich 1000…3000 → (2300 − 2000) · 360/4095 Grad
    expected_deg = (2300 - 2000) * 360 / 4095
    assert float(actions[-1][pan]) == pytest.approx(expected_deg, abs=0.2)
    for key in kinematics.BODY_KEYS:
        assert key in names
