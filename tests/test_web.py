"""Web-App gegen das Mock-Backend: Token, Steuerrecht, Watchdog, Not-Aus, sicheres Beenden."""

import time

import pytest
from fastapi.testclient import TestClient

from so101_lab.cli import main
from so101_lab.web.app import LabServer, create_app

CONFIG = """
[[device]]
name = "demo"
type = "so101_follower"
backend = "mock"

[[device]]
name = "halter"
type = "so101_follower"
backend = "mock"
safe_state = "hold"

[[device]]
name = "usbarm"
type = "so101_follower"
backend = "usb"
"""


@pytest.fixture
def config_path(tmp_path, monkeypatch):
    monkeypatch.setenv("SO101_LAB_ROOT", str(tmp_path))
    monkeypatch.setenv("HF_LEROBOT_CALIBRATION", str(tmp_path / "keine"))
    path = tmp_path / "arms.toml"
    path.write_text(CONFIG, encoding="utf-8")
    return str(path)


def make_client(config_path, **kwargs):
    server = LabServer(
        config_path=config_path, watchdog_ms=kwargs.pop("watchdog_ms", 200), poll_hz=50, **kwargs
    )
    return TestClient(create_app(server)), server


def torque_flags(server, name="demo"):
    return [s.values["torque_enable"] for s in server.sessions[name].arm.backend.servos]


def wait_for(condition, timeout=2.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if condition():
            return True
        time.sleep(0.02)
    return False


# ------------------------------------------------------------------ Grundfunktionen


def test_info_devices_and_static_page(config_path):
    client, _ = make_client(config_path)
    with client:
        assert client.get("/api/info").json()["auth_required"] is False
        names = [d["name"] for d in client.get("/api/devices").json()]
        assert names == ["demo", "halter", "usbarm"]
        page = client.get("/")
        assert page.status_code == 200 and "NOT-AUS" in page.text
        assert client.get("/app.js").status_code == 200


def test_connect_and_state(config_path):
    client, server = make_client(config_path)
    with client:
        snap = client.post("/api/devices/demo/connect", json={}).json()
        assert snap["backend"] == "mock" and snap["calibration_path"] is None
        assert wait_for(lambda: len(server.sessions["demo"].snapshot().get("joints", [])) == 6)
        state = client.get("/api/devices/demo/state").json()
        assert state["joints"][0]["name"] == "shoulder_pan"
        assert state["joints"][0]["position"] == 2048
        assert client.post("/api/devices/demo/connect", json={}).status_code == 409  # schon verbunden


def test_unknown_device_and_usb_without_port(config_path):
    client, _ = make_client(config_path)
    with client:
        assert client.post("/api/devices/gibtsnicht/connect", json={}).status_code == 404
        response = client.post("/api/devices/usbarm/connect", json={})
        assert response.status_code == 400
        assert "kein Port" in response.json()["detail"]


def test_live_stream_sends_hello_and_state(config_path):
    client, _ = make_client(config_path)
    with client:
        client.post("/api/devices/demo/connect", json={})
        with client.websocket_connect("/ws") as ws:
            assert ws.receive_json()["type"] == "hello"
            msg = ws.receive_json()
            assert msg["type"] == "state" and "demo" in msg["devices"]


# ------------------------------------------------------------------ Token


def test_token_protects_api_and_websocket(config_path):
    client, _ = make_client(config_path, token="geheim")
    with client:
        assert client.get("/api/info").json()["auth_required"] is True
        assert client.get("/api/devices").status_code == 401
        assert client.get("/api/devices", headers={"Authorization": "Bearer falsch"}).status_code == 401
        assert client.get("/api/devices", headers={"Authorization": "Bearer geheim"}).status_code == 200
        assert client.get("/api/devices?token=geheim").status_code == 200
        assert client.post("/api/estop").status_code == 401
        with pytest.raises(Exception), client.websocket_connect("/ws") as ws:  # noqa: B017
            ws.receive_json()
        with client.websocket_connect("/ws?token=geheim") as ws:
            assert ws.receive_json()["type"] == "hello"


def test_cli_refuses_network_without_token(config_path, monkeypatch, capsys):
    monkeypatch.delenv("SO101_LAB_TOKEN", raising=False)
    assert main(["web", "--host", "0.0.0.0", "--config", config_path, "--no-docs"]) == 1
    assert "Token Pflicht" in capsys.readouterr().err


# ------------------------------------------------------------------ Steuerrecht


def test_torque_needs_control(config_path):
    client, server = make_client(config_path)
    with client:
        client.post("/api/devices/demo/connect", json={})
        with client.websocket_connect("/ws") as ws:
            me = ws.receive_json()["client_id"]
            r = client.post("/api/devices/demo/torque", json={"client_id": me, "enabled": True})
            assert r.status_code == 409
            assert client.post("/api/devices/demo/control", json={"client_id": me}).status_code == 200
            ws.send_json({"type": "heartbeat"})
            assert (
                client.post("/api/devices/demo/torque", json={"client_id": me, "enabled": True}).status_code
                == 200
            )
            assert torque_flags(server) == [1] * 6
            # Torque an ohne Sprung: Ziel = Istposition
            assert all(s.values["goal_position"] == 2048 for s in server.sessions["demo"].arm.backend.servos)


def test_control_requires_open_live_connection(config_path):
    client, _ = make_client(config_path)
    with client:
        client.post("/api/devices/demo/connect", json={})
        r = client.post("/api/devices/demo/control", json={"client_id": "erfunden"})
        assert r.status_code == 409 and "Live-Verbindung" in r.json()["detail"]


def test_second_client_needs_force(config_path):
    client, server = make_client(config_path, watchdog_ms=2000)
    with client:
        client.post("/api/devices/demo/connect", json={})
        with client.websocket_connect("/ws") as ws1, client.websocket_connect("/ws") as ws2:
            a, b = ws1.receive_json()["client_id"], ws2.receive_json()["client_id"]
            client.post("/api/devices/demo/control", json={"client_id": a})
            assert client.post("/api/devices/demo/control", json={"client_id": b}).status_code == 409
            assert client.post("/api/devices/demo/disconnect", json={"client_id": b}).status_code == 409
            assert (
                client.post("/api/devices/demo/control", json={"client_id": b, "force": True}).status_code
                == 200
            )
            assert server.sessions["demo"].controller == b
            r = client.post("/api/devices/demo/torque", json={"client_id": a, "enabled": True})
            assert r.status_code == 409


# ------------------------------------------------------------------ Watchdog und sicherer Zustand


def test_watchdog_releases_control_and_turns_torque_off(config_path):
    client, server = make_client(config_path, watchdog_ms=150)
    with client:
        client.post("/api/devices/demo/connect", json={})
        with client.websocket_connect("/ws") as ws:
            me = ws.receive_json()["client_id"]
            client.post("/api/devices/demo/control", json={"client_id": me})
            client.post("/api/devices/demo/torque", json={"client_id": me, "enabled": True})
            assert torque_flags(server) == [1] * 6
            # keine Heartbeats mehr: Watchdog muss greifen
            assert wait_for(lambda: server.sessions["demo"].controller is None)
            assert torque_flags(server) == [0] * 6
            events = [e["text"] for e in server.sessions["demo"].snapshot()["events"]]
            assert any("Watchdog" in t for t in events)


def test_heartbeats_keep_control(config_path):
    client, server = make_client(config_path, watchdog_ms=150)
    with client:
        client.post("/api/devices/demo/connect", json={})
        with client.websocket_connect("/ws") as ws:
            me = ws.receive_json()["client_id"]
            client.post("/api/devices/demo/control", json={"client_id": me})
            for _ in range(15):  # 0,6 s mit Heartbeat alle 40 ms
                ws.send_json({"type": "heartbeat"})
                time.sleep(0.04)
            assert server.sessions["demo"].controller == me


def test_hold_state_keeps_torque_on_watchdog(config_path):
    client, server = make_client(config_path, watchdog_ms=150)
    with client:
        client.post("/api/devices/halter/connect", json={})
        with client.websocket_connect("/ws") as ws:
            me = ws.receive_json()["client_id"]
            client.post("/api/devices/halter/control", json={"client_id": me})
            client.post("/api/devices/halter/torque", json={"client_id": me, "enabled": True})
            assert wait_for(lambda: server.sessions["halter"].controller is None)
            assert torque_flags(server, "halter") == [1] * 6  # hold: Stellung halten


def test_closing_the_tab_makes_safe(config_path):
    client, server = make_client(config_path, watchdog_ms=2000)
    with client:
        client.post("/api/devices/demo/connect", json={})
        with client.websocket_connect("/ws") as ws:
            me = ws.receive_json()["client_id"]
            client.post("/api/devices/demo/control", json={"client_id": me})
            client.post("/api/devices/demo/torque", json={"client_id": me, "enabled": True})
        # WebSocket zu, lange bevor der Watchdog (2 s) greifen würde
        assert wait_for(lambda: server.sessions["demo"].controller is None, timeout=1.0)
        assert torque_flags(server) == [0] * 6


def test_estop_works_without_control(config_path):
    client, server = make_client(config_path, watchdog_ms=2000)
    with client:
        client.post("/api/devices/demo/connect", json={})
        client.post("/api/devices/halter/connect", json={})
        with client.websocket_connect("/ws") as ws1, client.websocket_connect("/ws") as ws2:
            a = ws1.receive_json()["client_id"]
            ws2.receive_json()
            for name in ("demo", "halter"):
                client.post(f"/api/devices/{name}/control", json={"client_id": a})
                client.post(f"/api/devices/{name}/torque", json={"client_id": a, "enabled": True})
            result = client.post("/api/estop").json()  # ein Beobachter löst aus
            assert sorted(result["stopped"]) == ["demo", "halter"]
            assert torque_flags(server, "demo") == [0] * 6
            assert torque_flags(server, "halter") == [0] * 6  # Not-Aus ignoriert safe_state
            assert server.sessions["demo"].controller is None


def test_server_shutdown_resets_and_disconnects(config_path):
    client, server = make_client(config_path, watchdog_ms=2000)
    with client:
        client.post("/api/devices/demo/connect", json={})
        session = server.sessions["demo"]
        with client.websocket_connect("/ws") as ws:
            me = ws.receive_json()["client_id"]
            client.post("/api/devices/demo/control", json={"client_id": me})
            client.post("/api/devices/demo/torque", json={"client_id": me, "enabled": True})
            backend = session.arm.backend
    assert server.sessions == {}
    assert not backend.is_connected
    assert all(s.values["torque_enable"] == 0 for s in backend.servos)
