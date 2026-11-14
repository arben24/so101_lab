"""Web-App: Aufnehmen, Abspielen, Teleoperation und Bibliothek gegen simulierte Arme."""

import csv
import threading
import time

import pytest
from fastapi.testclient import TestClient

from so101_lab import motion
from so101_lab.calibration import MotorCalibration, save_calibration
from so101_lab.devices import get_device_spec
from so101_lab.trajectories import Trajectory, default_directory, save_trajectory
from so101_lab.web.app import LabServer, create_app

JOINTS = [j.name for j in get_device_spec("so101_follower").position_joints]


@pytest.fixture
def lab(tmp_path, monkeypatch):
    """Zwei simulierte Arme mit Kalibrierung (leader und follower) und einer ohne (roh)."""
    monkeypatch.setenv("SO101_LAB_ROOT", str(tmp_path))
    monkeypatch.setenv("HF_LEROBOT_CALIBRATION", str(tmp_path / "keine"))
    monkeypatch.setattr(motion, "APPROACH_MIN_S", 0.2)  # Tests sollen nicht 2 s anfahren
    cal_dir = tmp_path / "cal"
    for cal_id, offset in (("leader_1", 0), ("follower_1", 300)):
        save_calibration(
            cal_dir / f"{cal_id}.json",
            {
                j.name: MotorCalibration(
                    j.motor_id, 0, 0, *((0, 4095) if j.full_turn else (1000 + offset, 3000 + offset))
                )
                for j in get_device_spec("so101_follower").position_joints
            },
        )
    config = tmp_path / "arms.toml"
    config.write_text(
        f"""
[[device]]
name = "leader"
type = "so101_follower"
backend = "mock"
calibration_id = "leader_1"
calibration_dir = "{cal_dir}"

[[device]]
name = "follower"
type = "so101_follower"
backend = "mock"
calibration_id = "follower_1"
calibration_dir = "{cal_dir}"
safe_state = "hold"

[[device]]
name = "roh"
type = "so101_follower"
backend = "mock"
""",
        encoding="utf-8",
    )
    server = LabServer(config_path=str(config), watchdog_ms=2000, poll_hz=50)
    return TestClient(create_app(server)), server


def wait_for(condition, timeout=5.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if condition():
            return True
        time.sleep(0.02)
    return False


class Heartbeat:
    """Hält das Steuerrecht am Leben, solange ein Test wartet."""

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


def control(client, ws, *names):
    me = ws.receive_json()["client_id"]
    for name in names:
        client.post(f"/api/devices/{name}/connect", json={})
        assert client.post(f"/api/devices/{name}/control", json={"client_id": me}).status_code == 200
    return me


def servos(server, name):
    return server.sessions[name].arm.backend


def test_record_by_hand_and_library(lab):
    client, server = lab
    with client, client.websocket_connect("/ws") as ws, Heartbeat(ws):
        me = control(client, ws, "follower")
        bus = servos(server, "follower")
        client.post("/api/devices/follower/torque", json={"client_id": me, "enabled": True})
        r = client.post("/api/devices/follower/recording/start", json={"client_id": me, "name": "winken"})
        assert r.status_code == 200, r.text
        assert all(s.values["torque_enable"] == 0 for s in bus.servos)  # von Hand führen
        for pos in (2100, 2200, 2300):
            bus.move_by_hand(3, pos)
            time.sleep(0.1)
        snap = client.post("/api/devices/follower/recording/stop", json={"client_id": me}).json()
        assert snap["saved"] and snap["path"].endswith("winken.csv")

        # Bibliothek
        items = client.get("/api/trajectories").json()
        assert [i["name"] for i in items] == ["winken"]
        assert items[0]["device"] == "follower" and items[0]["source"].startswith("von Hand")
        assert "calibration" not in items[0]
        detail = client.get("/api/trajectories/winken").json()
        assert detail["has_calibration"] and detail["joints"] == JOINTS
        assert 2300 in [row[2] for row in detail["positions"]]
        download = client.get("/api/trajectories/winken/csv")
        assert download.status_code == 200 and download.text.startswith("t_s,shoulder_pan")
        # gleicher Name ein zweites Mal: abgelehnt
        r = client.post("/api/devices/follower/recording/start", json={"client_id": me, "name": "winken"})
        assert r.status_code == 400 and "gibt es schon" in r.json()["detail"]
        assert client.delete(f"/api/trajectories/winken?client_id={me}").status_code == 200
        assert client.get("/api/trajectories").json() == []


def test_names_cannot_escape_the_library(lab):
    client, _ = lab
    with client:
        assert client.get("/api/trajectories/..%2F..%2Farms").status_code in (400, 404)
        assert client.get("/api/trajectories/a.b").status_code == 400


def test_recording_is_saved_when_control_is_lost(lab):
    client, server = lab
    with client:
        with client.websocket_connect("/ws") as ws:
            me = control(client, ws, "roh")
            client.post("/api/devices/roh/recording/start", json={"client_id": me, "name": "kurz"})
            time.sleep(0.2)
        # Tab zu: Steuerrecht weg, Aufnahme bis hierher gespeichert
        assert wait_for(lambda: (default_directory() / "kurz.csv").exists())
        meta = client.get("/api/trajectories/kurz").json()
        assert meta["interrupted"] == "Steuerrecht verloren"


def write_traj(name, rows, device_type="so101_follower", calibration_id="follower_1"):
    times = [i * 0.1 for i in range(len(rows))]
    meta = {"device_type": device_type, "calibration_id": calibration_id}
    save_trajectory(Trajectory(name, tuple(JOINTS), times, [tuple(r) for r in rows], meta))


def test_playback_moves_arm_and_blocks_other_moves(lab):
    client, server = lab
    write_traj("hin", [[2400] * 6, [2500] * 6, [2600] * 6])
    with client, client.websocket_connect("/ws") as ws, Heartbeat(ws):
        me = control(client, ws, "follower")
        r = client.post("/api/devices/follower/playback/start", json={"client_id": me, "trajectory": "hin"})
        assert r.status_code == 200, r.text
        assert r.json()["phase"] == "anfahrt"
        r = client.post("/api/devices/follower/move", json={"client_id": me, "targets": {"gripper": 2000}})
        assert r.status_code == 409 and "abgespielt" in r.json()["detail"]
        session = server.sessions["follower"]
        assert wait_for(lambda: session.player is None)
        bus = servos(server, "follower")
        assert wait_for(lambda: abs(bus.servo(3).values["present_position"] - 2600) < 5)
        assert bus.servo(3).values["torque_enable"] == 1  # hält die Endstellung


def test_playback_converts_between_calibrations(lab):
    client, server = lab
    leader_cal = {
        j: {"id": i + 1, "drive_mode": 0, "homing_offset": 0, "range_min": 1000, "range_max": 3000}
        for i, j in enumerate(JOINTS)
    }
    leader_cal["wrist_roll"].update(range_min=0, range_max=4095)
    times, rows = [0.0, 0.1], [(2000,) * 6, (2000,) * 6]
    meta = {"device_type": "so101_follower", "calibration": leader_cal}
    save_trajectory(Trajectory("vom_leader", tuple(JOINTS), times, rows, meta))
    with client, client.websocket_connect("/ws") as ws, Heartbeat(ws):
        me = control(client, ws, "follower")
        client.post(
            "/api/devices/follower/playback/start", json={"client_id": me, "trajectory": "vom_leader"}
        )
        session = server.sessions["follower"]
        assert wait_for(lambda: session.player is None)
        bus = servos(server, "follower")
        # Mitte des Leaders (2000) = Mitte des Followers (2300)
        assert wait_for(lambda: abs(bus.servo(1).values["present_position"] - 2300) < 5)


def test_playback_refuses_start_far_outside_limits(lab):
    client, _ = lab
    write_traj("zu_weit", [[100] * 6, [100] * 6])
    with client, client.websocket_connect("/ws") as ws:
        me = control(client, ws, "follower")
        r = client.post(
            "/api/devices/follower/playback/start", json={"client_id": me, "trajectory": "zu_weit"}
        )
        assert r.status_code == 400 and "weit außerhalb" in r.json()["detail"]


def test_playback_check_and_slight_overshoot(lab):
    """Ruhestellung knapp jenseits des kalibrierten Bereichs: wird begrenzt, nicht abgelehnt."""
    client, server = lab
    write_traj("ruhe", [[3305] * 6, [2500] * 6])  # follower_1: Bereich 1300…3300
    with client, client.websocket_connect("/ws") as ws, Heartbeat(ws):
        me = control(client, ws, "follower")
        check = client.get("/api/devices/follower/playback/check?trajectory=ruhe&speed=1").json()
        assert check["calibrated"] and any("begrenzt" in n for n in check["notes"]), check
        r = client.post("/api/devices/follower/playback/start", json={"client_id": me, "trajectory": "ruhe"})
        assert r.status_code == 200, r.text
        assert wait_for(lambda: server.sessions["follower"].player is None)


def test_record_without_calibration_and_play_after_new_offsets(lab):
    """Ohne Kalibrierung aufnehmen, Offsets ändern (neu kalibriert), abspielen: Offset herausrechnen."""
    client, server = lab
    with client, client.websocket_connect("/ws") as ws, Heartbeat(ws):
        me = control(client, ws, "roh")
        bus = servos(server, "roh")
        bus.move_by_hand(3, 2600)
        client.post("/api/devices/roh/recording/start", json={"client_id": me, "name": "roh1"})
        time.sleep(0.2)
        client.post("/api/devices/roh/recording/stop", json={"client_id": me})
        assert client.get("/api/trajectories/roh1").json()["homing_offsets"]["elbow_flex"] == 0
        # Neue Offsets im EEPROM (wie nach einer Kalibrierung): Rohwert = Stellung − 300
        for servo in bus.servos:
            servo.values["homing_offset"] = 300
        check = client.get("/api/devices/roh/playback/check?trajectory=roh1").json()
        assert not check["calibrated"]
        assert any("Keine Kalibrierdatei" in n for n in check["notes"])
        assert any("Homing-Offsets" in n for n in check["notes"])
        r = client.post("/api/devices/roh/playback/start", json={"client_id": me, "trajectory": "roh1"})
        assert r.status_code == 200, r.text
        assert wait_for(lambda: server.sessions["roh"].player is None)
        # Gleiche tatsächliche Stellung wie bei der Aufnahme: 2600 → neuer Rohwert 2300
        assert wait_for(lambda: abs(bus.servo(3).values["present_position"] - 2300) < 5)


def test_teleop_follows_leader_through_calibrations(lab):
    client, server = lab
    with client, client.websocket_connect("/ws") as ws, Heartbeat(ws):
        me = control(client, ws, "leader", "follower")
        leader_bus, follower_bus = servos(server, "leader"), servos(server, "follower")
        r = client.post("/api/devices/follower/teleop/start", json={"client_id": me, "leader": "leader"})
        assert r.status_code == 200, r.text
        leader_bus.move_by_hand(3, 2500)  # Leader: Mitte + 500
        # Follower: Mitte 2300 + 500 = 2800
        assert wait_for(lambda: abs(follower_bus.servo(3).values["present_position"] - 2800) < 5)
        # Aufnahme während der Teleoperation: Drehmoment bleibt an
        client.post("/api/devices/follower/recording/start", json={"client_id": me, "name": "tele"})
        assert follower_bus.servo(3).values["torque_enable"] == 1
        time.sleep(0.2)
        snap = client.post("/api/devices/follower/recording/stop", json={"client_id": me}).json()
        assert snap["saved"]
        assert client.get("/api/trajectories/tele").json()["source"].startswith("Teleoperation")
        # Leader trennen: Follower hört auf zu folgen und hält
        client.post("/api/devices/leader/control/release", json={"client_id": me})
        client.post("/api/devices/leader/disconnect", json={"client_id": me})
        session = server.sessions["follower"]
        assert wait_for(lambda: session.teleop is None)
        assert follower_bus.servo(3).values["torque_enable"] == 1


def test_teleop_needs_calibration(lab):
    client, _ = lab
    with client, client.websocket_connect("/ws") as ws:
        me = control(client, ws, "leader", "roh")
        r = client.post("/api/devices/roh/teleop/start", json={"client_id": me, "leader": "leader"})
        assert r.status_code == 400 and "Kalibrierung" in r.json()["detail"]
        r = client.post("/api/devices/roh/teleop/start", json={"client_id": me, "leader": "roh"})
        assert r.status_code == 400


def test_estop_ends_playback(lab):
    client, server = lab
    write_traj("lang", [[2400 + i] * 6 for i in range(100)])
    with client, client.websocket_connect("/ws") as ws, Heartbeat(ws):
        me = control(client, ws, "follower")
        client.post("/api/devices/follower/playback/start", json={"client_id": me, "trajectory": "lang"})
        client.post("/api/estop")
        session = server.sessions["follower"]
        assert session.player is None
        assert all(s.values["torque_enable"] == 0 for s in servos(server, "follower").servos)


def test_recorded_csv_has_rows(lab):
    client, server = lab
    with client, client.websocket_connect("/ws") as ws, Heartbeat(ws):
        me = control(client, ws, "roh")
        client.post("/api/devices/roh/recording/start", json={"client_id": me, "name": "zeilen"})
        time.sleep(0.3)
        client.post("/api/devices/roh/recording/stop", json={"client_id": me})
    with (default_directory() / "zeilen.csv").open(encoding="utf-8") as fh:
        rows = list(csv.reader(fh))
    assert rows[0] == ["t_s", *JOINTS] and len(rows) > 5
