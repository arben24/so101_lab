"""LeKiwi fahren: Kinematik (gegen LeRobot), Arm.drive, Fahrbefehl in der Web-App, Skript 09."""

import importlib.util
import random
import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from so101_lab import kinematics
from so101_lab.arm import Arm
from so101_lab.backends.base import BackendError
from so101_lab.backends.esp32 import Esp32Backend
from so101_lab.backends.mock import MockBackend
from so101_lab.config import DeviceConfig
from so101_lab.devices import get_device_spec
from so101_lab.esp32_sim import SimNode
from so101_lab.web import session as session_module
from so101_lab.web.app import LabServer, create_app

LEKIWI = get_device_spec("lekiwi")
WHEEL_IDS = {"base_left_wheel": 7, "base_back_wheel": 8, "base_right_wheel": 9}


# ------------------------------------------------------------------ Kinematik


def test_kinematics_matches_lerobot_exactly():
    from lerobot.robots.lekiwi.lekiwi import LeKiwi

    rng = random.Random(7)
    for _ in range(2000):
        x, y, theta = rng.uniform(-0.5, 0.5), rng.uniform(-0.5, 0.5), rng.uniform(-180, 180)
        max_raw = rng.choice([500, 3000, 40000])
        # _body_to_wheel_raw braucht von self nur die statische Methode _degps_to_raw
        expected = LeKiwi._body_to_wheel_raw(LeKiwi, x, y, theta, max_raw=max_raw)
        assert kinematics.body_to_wheels(x, y, theta, max_raw=max_raw) == expected
        raw = {name: rng.randint(-3000, 3000) for name in kinematics.WHEELS}
        body = LeKiwi._wheel_raw_to_body(LeKiwi, *(raw[n] for n in kinematics.WHEELS))
        assert kinematics.wheels_to_body(raw) == pytest.approx({k: float(v) for k, v in body.items()})


def test_wheel_geometry_and_simple_cases():
    assert kinematics.WHEEL_ANGLES_DEG == (150.0, -90.0, 30.0)
    # vorwärts: hinteres Rad steht, links und rechts gegenläufig
    assert kinematics.body_to_wheels(0.1, 0, 0) == {
        "base_left_wheel": -1129,
        "base_back_wheel": 0,
        "base_right_wheel": 1129,
    }
    # drehen: alle drei gleich schnell, gleiche Richtung
    assert len(set(kinematics.body_to_wheels(0, 0, 30).values())) == 1


def test_too_fast_is_scaled_down_keeping_direction():
    wheels = kinematics.body_to_wheels(0.3, 0.1, 0, max_raw=3000)
    assert max(abs(v) for v in wheels.values()) == 3000
    body = kinematics.wheels_to_body(wheels)
    assert body["y.vel"] / body["x.vel"] == pytest.approx(0.1 / 0.3, rel=1e-3)
    assert body["theta.vel"] == pytest.approx(0, abs=0.1)


def test_round_trip():
    # Rohwerte sind ganze Schritt/s: Hin und zurück stimmt bis auf die Rundung
    body = kinematics.wheels_to_body(kinematics.body_to_wheels(0.1, -0.05, 20))
    assert body["x.vel"] == pytest.approx(0.1, abs=1e-3)
    assert body["y.vel"] == pytest.approx(-0.05, abs=1e-3)
    assert body["theta.vel"] == pytest.approx(20, abs=0.1)


# ------------------------------------------------------------------ Arm


def make_arm(device="lekiwi", clock=None, **cfg):
    bus = MockBackend.for_ids(LEKIWI.ids, wheel_ids=WHEEL_IDS.values(), clock=clock or time.monotonic)
    bus.connect()
    spec = get_device_spec(device)
    return Arm(
        bus, spec, DeviceConfig(name=device, type=device, backend="mock", calibration_id="x", **cfg)
    ), bus


def test_arm_drive_writes_wheels_and_respects_max_wheel_velocity(clock):
    arm, bus = make_arm(clock=clock, max_wheel_velocity=1000)
    arm.enable_torque(list(WHEEL_IDS))
    sent = arm.drive(0.3, 0, 0)
    assert sent == kinematics.body_to_wheels(0.3, 0, 0, max_raw=1000)
    assert {bus.servo(i).values["goal_velocity"] for i in (7, 9)} == {-1000, 1000}
    # Arm unberührt: kein Drehmoment, kein neues Ziel (goal_velocity ist dort nur die Tempogrenze)
    assert all(bus.servo(i).values["torque_enable"] == 0 for i in range(1, 7))
    assert not any(op[2] == "goal_position" for op in bus.log)
    clock.advance(0.1)
    assert arm.read_body_velocity()["x.vel"] > 0
    arm.stop_wheels()
    assert all(bus.servo(i).values["goal_velocity"] == 0 for i in WHEEL_IDS.values())


def test_arm_without_wheels_cannot_drive():
    arm, _ = make_arm("lekiwi_arm")
    assert not arm.can_drive
    with pytest.raises(BackendError, match="keine LeKiwi-Basis"):
        arm.drive(0.1, 0, 0)
    assert make_arm("lekiwi_base")[0].can_drive


def test_drive_over_esp32_node():
    with SimNode(profile="lekiwi", port=0) as node:
        backend = Esp32Backend(f"127.0.0.1:{node.port}", LEKIWI)
        backend.connect()
        try:
            arm = Arm(
                backend, LEKIWI, DeviceConfig(name="k", type="lekiwi", backend="esp32", calibration_id="x")
            )
            arm.enable_torque(list(WHEEL_IDS))
            sent = arm.drive(0.1, 0, 0)
            end = time.monotonic() + 2
            while (
                node.bus.servo(9).values["goal_velocity"] != sent["base_right_wheel"]
                and time.monotonic() < end
            ):
                time.sleep(0.02)
            assert node.bus.servo(9).values["goal_velocity"] == 1129
            arm.stop_wheels()
        finally:
            backend.disconnect()


# ------------------------------------------------------------------ Web-App


@pytest.fixture
def lab(tmp_path, monkeypatch):
    monkeypatch.setenv("SO101_LAB_ROOT", str(tmp_path))
    monkeypatch.setenv("HF_LEROBOT_CALIBRATION", str(tmp_path / "keine"))
    config = tmp_path / "arms.toml"
    config.write_text(
        """
[[device]]
name = "kiwi"
type = "lekiwi"
backend = "mock"
calibration_id = "kiwi"

[[device]]
name = "arm"
type = "so101_follower"
backend = "mock"
calibration_id = "arm"
""",
        encoding="utf-8",
    )
    server = LabServer(config_path=str(config), watchdog_ms=2000, poll_hz=50)
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
        return self

    def __exit__(self, *exc):
        self.stop.set()
        self.thread.join()


def wait_for(condition, timeout=3.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if condition():
            return True
        time.sleep(0.02)
    return False


def wheels(server, name="kiwi"):
    bus = server.sessions[name].arm.backend
    return {n: bus.servo(i).values for n, i in WHEEL_IDS.items()}


def drive(client, me, x=0.0, y=0.0, theta=0.0, name="kiwi"):
    return client.post(f"/api/devices/{name}/drive", json={"client_id": me, "x": x, "y": y, "theta": theta})


def test_drive_needs_control_and_switches_on_only_the_wheels(lab):
    client, server = lab
    with client, client.websocket_connect("/ws") as ws, Heartbeat(ws):
        me = ws.receive_json()["client_id"]
        client.post("/api/devices/kiwi/connect", json={})
        assert drive(client, me, x=0.1).status_code == 409  # ohne Steuerrecht
        client.post("/api/devices/kiwi/control", json={"client_id": me})
        r = drive(client, me, x=0.1)
        assert r.status_code == 200, r.text
        assert r.json()["wheels"] == kinematics.body_to_wheels(0.1, 0, 0)
        w = wheels(server)
        assert all(v["torque_enable"] == 1 for v in w.values())
        assert w["base_right_wheel"]["goal_velocity"] == 1129
        arm_bus = server.sessions["kiwi"].arm.backend
        assert all(arm_bus.servo(i).values["torque_enable"] == 0 for i in range(1, 7))  # Arm bleibt aus
        assert any("Räder: Drehmoment an" in e["text"] for e in server.sessions["kiwi"].snapshot()["events"])

        snap = client.get("/api/devices/kiwi/state").json()["drive"]
        assert snap["command"] == {"x.vel": 0.1, "y.vel": 0.0, "theta.vel": 0.0}
        assert snap["levels"][0] == {"xy": 0.1, "theta": 30.0}
        assert wait_for(
            lambda: (
                (client.get("/api/devices/kiwi/state").json()["drive"]["measured"] or {}).get("x.vel", 0)
                > 0.05
            )
        )

        assert drive(client, me).status_code == 200  # alles 0 = anhalten
        assert all(v["goal_velocity"] == 0 for v in wheels(server).values())


def test_wheels_stop_when_commands_stop(lab):
    client, server = lab
    with client, client.websocket_connect("/ws") as ws, Heartbeat(ws):
        me = ws.receive_json()["client_id"]
        client.post("/api/devices/kiwi/connect", json={})
        client.post("/api/devices/kiwi/control", json={"client_id": me})
        sent_at = time.monotonic()
        assert drive(client, me, theta=30).status_code == 200
        assert wait_for(lambda: all(v["goal_velocity"] == 0 for v in wheels(server).values()))
        stopped_after = time.monotonic() - sent_at
        assert session_module.DRIVE_TIMEOUT_S <= stopped_after < 1.0
        events = server.sessions["kiwi"].snapshot()["events"]
        assert any("Kein Fahrbefehl seit" in e["text"] for e in events)
        # Steuerrecht und Heartbeat bleiben, nur die Räder halten an
        assert server.sessions["kiwi"].controller == me


def test_repeated_commands_keep_driving(lab):
    client, server = lab
    with client, client.websocket_connect("/ws") as ws, Heartbeat(ws):
        me = ws.receive_json()["client_id"]
        client.post("/api/devices/kiwi/connect", json={})
        client.post("/api/devices/kiwi/control", json={"client_id": me})
        for _ in range(8):  # 0,8 s, länger als die Totmannzeit
            assert drive(client, me, y=0.1).status_code == 200
            time.sleep(0.1)
            assert wheels(server)["base_back_wheel"]["goal_velocity"] != 0


@pytest.mark.parametrize("how", ["release", "estop", "torque_off"])
def test_safety_paths_stop_the_wheels(lab, how):
    client, server = lab
    with client, client.websocket_connect("/ws") as ws, Heartbeat(ws):
        me = ws.receive_json()["client_id"]
        client.post("/api/devices/kiwi/connect", json={})
        client.post("/api/devices/kiwi/control", json={"client_id": me})
        server.sessions["kiwi"].arm.cfg.safe_state = "hold"  # Drehmoment bleibt, also muss Tempo 0 sein
        assert drive(client, me, x=0.2).status_code == 200
        if how == "release":
            client.post("/api/devices/kiwi/control/release", json={"client_id": me})
        elif how == "estop":
            client.post("/api/estop")
        else:
            client.post("/api/devices/kiwi/torque", json={"client_id": me, "enabled": False})
        assert all(v["goal_velocity"] == 0 for v in wheels(server).values())
        assert client.get("/api/devices/kiwi/state").json()["drive"]["wheels"] == dict.fromkeys(
            kinematics.WHEELS, 0
        )


def test_wrong_operating_mode_is_refused(lab):
    client, server = lab
    with client, client.websocket_connect("/ws") as ws, Heartbeat(ws):
        me = ws.receive_json()["client_id"]
        client.post("/api/devices/kiwi/connect", json={})
        client.post("/api/devices/kiwi/control", json={"client_id": me})
        wheels(server)["base_back_wheel"]["operating_mode"] = 0
        r = drive(client, me, x=0.1)
        assert r.status_code == 400 and "base_back_wheel (ID 8): 0" in r.json()["detail"]
        assert all(v["torque_enable"] == 0 and v["goal_velocity"] == 0 for v in wheels(server).values())


def test_device_without_wheels_and_bad_input(lab):
    client, server = lab
    with client, client.websocket_connect("/ws") as ws, Heartbeat(ws):
        me = ws.receive_json()["client_id"]
        client.post("/api/devices/arm/connect", json={})
        client.post("/api/devices/arm/control", json={"client_id": me})
        assert client.get("/api/devices/arm/state").json()["drive"] is None
        r = drive(client, me, x=0.1, name="arm")
        assert r.status_code == 400 and "keine Räder" in r.json()["detail"]
        assert drive(client, me, x=5, name="arm").status_code == 422  # außerhalb ±1 m/s


# ------------------------------------------------------------------ Skript 09

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"


@pytest.fixture
def script(tmp_path, monkeypatch):
    monkeypatch.setenv("SO101_LAB_ROOT", str(tmp_path))
    monkeypatch.setenv("HF_LEROBOT_CALIBRATION", str(tmp_path / "keine"))
    config = tmp_path / "arms.toml"
    config.write_text('[[device]]\nname = "demo_kiwi"\ntype = "lekiwi"\nbackend = "mock"\n', encoding="utf-8")
    monkeypatch.setenv("SO101_LAB_CONFIG", str(config))
    spec = importlib.util.spec_from_file_location("script_09", SCRIPTS / "09_drive_base.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_09_only_calculates(script, capsys):
    assert script.main(["--x", "0.1", "--rechnen"]) == 0
    out = capsys.readouterr().out
    assert "links -1129, hinten +0, rechts +1129" in out and "nichts bewegt" in out


def test_09_drives_and_stops(script, monkeypatch, capsys):
    buses = []
    original = MockBackend.for_ids.__func__

    def remember(cls, *args, **kwargs):
        buses.append(original(cls, *args, **kwargs))
        return buses[-1]

    monkeypatch.setattr(MockBackend, "for_ids", classmethod(remember))
    assert script.main(["--device", "demo_kiwi", "--theta", "30", "--dauer", "0.3", "--ohne-warten"]) == 0
    out = capsys.readouterr().out
    assert "gemessen: x +0.000 m/s" in out and "Fertig" in out
    (bus,) = buses
    assert all(
        bus.servo(i).values["goal_velocity"] == 0 and bus.servo(i).values["torque_enable"] == 0
        for i in (7, 8, 9)
    )
    assert any(
        op[0] == "sync_write" and op[2] == "goal_velocity" and 853 in op[3].values()
        for op in bus.log
        if len(op) == 4 and isinstance(op[3], dict)
    )


def test_09_refuses_too_fast_and_long(script):
    with pytest.raises(BackendError, match="Zu schnell"):
        script.main(["--x", "0.5", "--rechnen"])
    with pytest.raises(BackendError, match="--dauer"):
        script.main(["--x", "0.1", "--dauer", "10", "--rechnen"])
    with pytest.raises(BackendError, match="Kein Fahrbefehl"):
        script.main(["--rechnen"])
