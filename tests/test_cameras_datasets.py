"""Kameras (Testbild) und LeRobot-Datasets, ohne Hardware."""

import json
import threading
import time

import pytest
from fastapi.testclient import TestClient

from so101_lab.calibration import MotorCalibration, save_calibration
from so101_lab.cameras import CameraError, CameraSpec, open_camera, parse_camera, parse_cameras, to_jpeg
from so101_lab.config import ConfigError, parse_config
from so101_lab.devices import get_device_spec
from so101_lab.lab_dataset import DatasetError, LabDataset, delete_dataset, list_datasets
from so101_lab.web.app import LabServer, create_app

JOINTS = [j.name for j in get_device_spec("so101_follower").position_joints]


def wait_for(condition, timeout=5.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if condition():
            return True
        time.sleep(0.02)
    return False


# ------------------------------------------------------------------ Kameras


def test_parse_camera_entries():
    spec = parse_camera({"name": "front", "source": "/dev/video0", "rotation": 90}, "x")
    assert (spec.width, spec.height, spec.fps, spec.fourcc) == (640, 480, 30, "MJPG")
    assert spec.frame_shape == (640, 480, 3)  # gedreht: hoch statt quer
    assert parse_camera({"name": "c0", "source": 2}, "x").source == "2"
    for bad, text in (
        ({"name": "Front", "source": "x"}, "name"),
        ({"name": "front"}, "source"),
        ({"name": "front", "source": "x", "fps": 500}, "fps"),
        ({"name": "front", "source": "x", "rotation": 45}, "rotation"),
        ({"name": "front", "source": "x", "farbe": 1}, "unbekannte"),
    ):
        with pytest.raises(CameraError, match=text):
            parse_camera(bad, "x")
    with pytest.raises(CameraError, match="verschieden"):
        parse_cameras([{"name": "a", "source": "test"}, {"name": "a", "source": "test"}], "x")


def test_config_checks_cameras():
    cfg = parse_config(
        {
            "device": [
                {
                    "name": "d",
                    "type": "so101_follower",
                    "backend": "mock",
                    "cameras": [{"name": "sim", "source": "test"}],
                }
            ]
        }
    )
    assert cfg.device("d").cameras[0].is_test
    with pytest.raises(ConfigError, match="'d'.*Kamera Nr. 1"):
        parse_config(
            {
                "device": [
                    {"name": "d", "type": "so101_follower", "backend": "mock", "cameras": [{"name": "x"}]}
                ]
            }
        )


def test_test_pattern_camera_delivers_frames():
    camera = open_camera(CameraSpec("sim", "test", width=64, height=48, fps=30, rotation=180))
    try:
        assert wait_for(lambda: camera.latest() is not None)
        frame = camera.latest()
        assert frame.shape == (48, 64, 3) and frame.dtype.name == "uint8"
        assert to_jpeg(frame)[:2] == b"\xff\xd8"
        time.sleep(0.3)
        status = camera.status()
        assert status["size"] == [64, 48] and status["frames"] > 3 and status["error"] is None
    finally:
        camera.close()
    assert camera.latest(max_age_s=0.0) is None


def test_missing_camera_device():
    with pytest.raises(CameraError, match="gibt es nicht"):
        open_camera(CameraSpec("front", "/dev/gibt_es_nicht"))


# ------------------------------------------------------------------ Dataset


@pytest.fixture
def root(tmp_path, monkeypatch):
    monkeypatch.setenv("SO101_LAB_ROOT", str(tmp_path))
    monkeypatch.setenv("HF_LEROBOT_CALIBRATION", str(tmp_path / "keine"))
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    return tmp_path


@pytest.fixture
def camera():
    cam = open_camera(CameraSpec("sim", "test", width=64, height=48, fps=30))
    assert wait_for(lambda: cam.latest() is not None)
    yield cam
    cam.close()


def values():
    obs = dict.fromkeys(JOINTS, 1.5)
    return obs, dict.fromkeys(JOINTS, 2.5)


def test_dataset_episodes_resume_and_reload(root, camera):
    ds = LabDataset("probe", JOINTS, {"sim": camera}, 30, "so_follower", "Würfel greifen")
    ds.start_episode(values)
    time.sleep(0.4)
    first = ds.stop_episode(keep=True)
    assert first.frames >= 5 and ds.saved_episodes == 1
    ds.start_episode(values)
    time.sleep(0.2)
    ds.stop_episode(keep=False)  # verworfen
    assert ds.saved_episodes == 1
    ds.close()

    [item] = list_datasets()
    assert item["episodes"] == 1 and item["frames"] == first.frames and item["cameras"] == ["sim"]
    assert item["robot_type"] == "so_follower" and item["codebase_version"] == "v3.0"

    # Weiterschreiben mit gleichen Merkmalen
    again = LabDataset("probe", JOINTS, {"sim": camera}, 30, "so_follower", "Würfel greifen")
    assert again.resumed and again.saved_episodes == 1
    again.start_episode(values)
    time.sleep(0.2)
    again.stop_episode()
    again.close()
    assert list_datasets()[0]["episodes"] == 2

    # So liest es LeRobot (lerobot-train, lerobot-dataset-viz)
    from lerobot.datasets.lerobot_dataset import LeRobotDataset

    loaded = LeRobotDataset("lab/probe", root=root / "recordings" / "datasets" / "probe")
    assert loaded.num_episodes == 2 and loaded.fps == 30
    sample = loaded[0]
    assert sample["observation.images.sim"].shape == (3, 48, 64)
    assert float(sample["observation.state"][0]) == 1.5 and float(sample["action"][0]) == 2.5
    assert sample["task"] == "Würfel greifen"
    names = json.loads((root / "recordings/datasets/probe/meta/info.json").read_text())["features"]["action"][
        "names"
    ]
    assert names == [f"{j}.pos" for j in JOINTS]  # wie LeRobots SOFollower

    # Andere fps: passt nicht zum vorhandenen Dataset
    with pytest.raises(DatasetError, match="fps"):
        LabDataset("probe", JOINTS, {"sim": camera}, 15, "so_follower", "x")
    delete_dataset("probe")
    assert list_datasets() == []


def test_dataset_checks(root, camera):
    with pytest.raises(DatasetError, match="ungültig"):
        LabDataset("../weg", JOINTS, {}, 30, "so_follower", "x")
    with pytest.raises(DatasetError, match="Aufgabe"):
        LabDataset("ok", JOINTS, {}, 30, "so_follower", "  ")


def test_frames_without_values_are_skipped(root, camera):
    ds = LabDataset("leer", JOINTS, {"sim": camera}, 30, "so_follower", "x")
    ds.start_episode(lambda: None)
    time.sleep(0.2)
    ep = ds.stop_episode()
    assert ep.frames == 0 and ep.skipped > 0 and ds.saved_episodes == 0  # nichts gespeichert
    ds.close()


# ------------------------------------------------------------------ Web


def make_lab(root, calibrated=True):
    cal_dir = root / "cal"
    if calibrated:
        save_calibration(
            cal_dir / "c1.json",
            {
                j.name: MotorCalibration(j.motor_id, 0, 0, *((0, 4095) if j.full_turn else (1000, 3000)))
                for j in get_device_spec("so101_follower").position_joints
            },
        )
    (root / "arms.toml").write_text(
        f"""
[[device]]
name = "arm"
type = "so101_follower"
backend = "mock"
calibration_id = "c1"
calibration_dir = "{cal_dir}"
cameras = [{{ name = "sim", source = "test", width = 64, height = 48 }}]
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


def test_web_camera_and_dataset(root):
    client, server = make_lab(root)
    with client, client.websocket_connect("/ws") as ws:
        me = ws.receive_json()["client_id"]
        with Heartbeat(ws):
            client.post("/api/devices/arm/connect", json={})
            assert wait_for(lambda: client.get("/api/devices/arm/cameras/sim/frame.jpg").status_code == 200)
            assert client.get("/api/devices/arm/cameras/sim/frame.jpg").content[:2] == b"\xff\xd8"
            assert client.get("/api/devices/arm/cameras/gibtsnicht/frame.jpg").status_code == 404
            state = client.get("/api/devices/arm/state").json()
            assert state["cameras"][0]["name"] == "sim"

            r = client.post(
                "/api/devices/arm/dataset/open", json={"client_id": me, "name": "web1", "task": "Greifen"}
            )
            assert r.status_code == 409  # Steuerrecht nötig
            client.post("/api/devices/arm/control", json={"client_id": me})
            r = client.post(
                "/api/devices/arm/dataset/open", json={"client_id": me, "name": "web1", "task": "Greifen"}
            )
            assert r.status_code == 200, r.text
            client.post("/api/devices/arm/dataset/episode/start", json={"client_id": me})
            time.sleep(0.4)
            r = client.post(
                "/api/devices/arm/dataset/episode/stop", json={"client_id": me, "keep": True}
            ).json()
            assert r["saved_episodes"] == 1
            # Episode läuft, Steuerrecht weg: Episode wird verworfen
            client.post("/api/devices/arm/dataset/episode/start", json={"client_id": me})
            time.sleep(0.2)
            client.post("/api/devices/arm/control/release", json={"client_id": me})
            session = server.sessions["arm"]
            assert session.dataset.episode is None and session.dataset.saved_episodes == 1
            r = client.delete(f"/api/datasets/web1?client_id={me}")
            assert r.status_code == 409  # noch geöffnet
            client.post("/api/devices/arm/control", json={"client_id": me})
            client.post("/api/devices/arm/dataset/close", json={"client_id": me})
            [item] = client.get("/api/datasets").json()
            assert item["name"] == "web1" and item["episodes"] == 1


def test_web_dataset_needs_calibration(root):
    client, _ = make_lab(root, calibrated=False)
    with client, client.websocket_connect("/ws") as ws:
        me = ws.receive_json()["client_id"]
        client.post("/api/devices/arm/connect", json={})
        client.post("/api/devices/arm/control", json={"client_id": me})
        r = client.post("/api/devices/arm/dataset/open", json={"client_id": me, "name": "x", "task": "y"})
        assert r.status_code == 400 and "Kalibrierung" in r.json()["detail"]
