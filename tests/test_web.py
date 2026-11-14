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


# ------------------------------------------------------------------ Bewegen, Register, Kalibrierung


def take(client, ws, name="demo"):
    me = ws.receive_json()["client_id"]
    assert client.post(f"/api/devices/{name}/control", json={"client_id": me}).status_code == 200
    return me


def test_move_is_clamped_and_needs_control(config_path):
    client, server = make_client(config_path, watchdog_ms=2000)
    with client:
        client.post("/api/devices/demo/connect", json={})
        with client.websocket_connect("/ws") as ws:
            me = ws.receive_json()["client_id"]
            r = client.post(
                "/api/devices/demo/move", json={"client_id": me, "targets": {"shoulder_pan": 2200}}
            )
            assert r.status_code == 409
            client.post("/api/devices/demo/control", json={"client_id": me})
            client.post(
                "/api/devices/demo/torque",
                json={"client_id": me, "enabled": True, "joints": ["shoulder_pan"]},
            )
            assert torque_flags(server) == [1, 0, 0, 0, 0, 0]
            r = client.post(
                "/api/devices/demo/move", json={"client_id": me, "targets": {"shoulder_pan": 3500}}
            )
            assert r.json()["sent"] == {"shoulder_pan": 2048 + 400}  # größter Sprung
            r = client.post("/api/devices/demo/move", json={"client_id": me, "targets": {"gibtsnicht": 1}})
            assert r.status_code == 400


def test_motion_limits_are_written(config_path):
    client, server = make_client(config_path, watchdog_ms=2000)
    with client:
        client.post("/api/devices/demo/connect", json={})
        with client.websocket_connect("/ws") as ws:
            me = take(client, ws)
            r = client.post(
                "/api/devices/demo/motion", json={"client_id": me, "velocity": 300, "acceleration": 5}
            )
            assert r.json() == {"velocity": 300, "acceleration": 5}
            servos = server.sessions["demo"].arm.backend.servos
            assert all(s.values["acceleration"] == 5 and s.values["goal_velocity"] == 300 for s in servos)


def test_register_explorer_rules(config_path):
    client, server = make_client(config_path, watchdog_ms=2000)
    with client:
        client.post("/api/devices/demo/connect", json={})
        rows = {r["name"]: r for r in client.get("/api/devices/demo/registers/elbow_flex").json()}
        assert rows["id"]["value"] == 3 and rows["id"]["write"] == "locked"
        assert rows["acceleration"]["write"] == "ram"
        assert rows["p_coefficient"]["write"] == "eeprom"
        assert rows["torque_enable"]["write"] == "panel"
        assert rows["lock"]["write"] == "locked"
        assert rows["present_position"]["write"] == "locked"
        assert rows["dts"]["write"] == "locked"
        with client.websocket_connect("/ws") as ws:
            me = take(client, ws)

            def write(register, value, **extra):
                body = {
                    "client_id": me,
                    "joint": "elbow_flex",
                    "register_name": register,
                    "value": value,
                    **extra,
                }
                return client.post("/api/devices/demo/registers", json=body)

            assert write("acceleration", 33).json()["value"] == 33
            assert write("lock", 0).status_code == 400
            assert write("id", 9).status_code == 400
            assert write("torque_enable", 1).status_code == 400
            assert write("homing_offset", 10, confirm_eeprom=True).status_code == 400
            r = write("p_coefficient", 20)
            assert r.status_code == 400 and "Bestätigung" in r.json()["detail"]
            assert write("p_coefficient", 20, confirm_eeprom=True).json()["value"] == 20
            servo = server.sessions["demo"].arm.backend.servo(3)
            assert servo.persisted["p_coefficient"] == 20  # dauerhaft


def test_calibration_through_the_api(config_path, tmp_path):
    client, server = make_client(config_path, watchdog_ms=2000)
    with client:
        client.post("/api/devices/demo/connect", json={})
        bus = server.sessions["demo"].arm.backend
        with client.websocket_connect("/ws") as ws:
            me = take(client, ws)

            def step(name):
                return client.post(f"/api/devices/demo/calibration/{name}", json={"client_id": me})

            assert step("middle").status_code == 400  # erst starten
            status = step("start").json()
            assert status["stage"] == "mitte"
            assert status["target_path"].endswith("calibration/robots/so_follower/demo.json")
            # während der Kalibrierung keine Bewegung
            r = client.post("/api/devices/demo/torque", json={"client_id": me, "enabled": True})
            assert r.status_code == 409
            bus.move_by_hand(1, 1000)
            assert step("middle").json()["stage"] == "bereich"
            for raw in (1500, 2600):  # von Hand durch den Bereich, die Abfrage schreibt mit
                for s in range(1, 7):
                    bus.move_by_hand(s, raw)
                time.sleep(0.15)
            status = step("finish").json()
            assert status["stage"] == "fertig"
            path = tmp_path / "calibration" / "robots" / "so_follower" / "demo.json"
            assert status["saved_path"] == str(path) and path.exists()
            snap = client.get("/api/devices/demo/state").json()
            assert snap["calibration_path"] == str(path) and snap["calibrated"]
            pan = next(j for j in snap["joints"] if j["name"] == "shoulder_pan")
            assert pan["limits"][0] <= 1500 and pan["limits"][1] >= 2600


def test_watchdog_aborts_calibration_and_restores(config_path):
    client, server = make_client(config_path, watchdog_ms=150)
    with client:
        client.post("/api/devices/demo/connect", json={})
        bus = server.sessions["demo"].arm.backend
        with client.websocket_connect("/ws") as ws:
            me = take(client, ws)
            client.post("/api/devices/demo/calibration/start", json={"client_id": me})
            bus.move_by_hand(2, 3000)
            ws.send_json({"type": "heartbeat"})
            client.post("/api/devices/demo/calibration/middle", json={"client_id": me})
            assert bus.servo(2).values["homing_offset"] != 0
            assert wait_for(lambda: server.sessions["demo"].calibration_run.stage == "abgebrochen")
            assert all(s.values["homing_offset"] == 0 for s in bus.servos)
            assert all(s.values["max_position_limit"] == 4095 for s in bus.servos)


def test_frontend_uses_no_blocking_dialogs():
    """confirm()/alert()/prompt() halten im Browser alles JavaScript an, auch das Lebenszeichen.
    Dann entzieht der Watchdog das Steuerrecht, während die Rückfrage offen ist."""
    import re

    from so101_lab.web.app import STATIC_DIR

    code = "\n".join(
        line
        for line in (STATIC_DIR / "app.js").read_text(encoding="utf-8").splitlines()
        if not line.lstrip().startswith("//")
    )
    assert not re.search(r"(?<![\w.])(confirm|alert|prompt)\s*\(", code)
    assert not re.search(r"window\.(confirm|alert|prompt)\s*\(", code)


# ------------------------------------------------------------------ Einrichtungsassistent (Servo-IDs)


def start_setup(client, me, device_type="so101_follower"):
    r = client.post("/api/setup/start", json={"client_id": me, "port": "mock", "device_type": device_type})
    assert r.status_code == 200, r.text
    return r.json()


def test_setup_types():
    from so101_lab.web.app import LabServer

    client = TestClient(create_app(LabServer()))
    data = client.get("/api/setup/types").json()
    types = {t["type"]: t for t in data["types"]}
    assert [j["id"] for j in types["so101_follower"]["joints"]] == [6, 5, 4, 3, 2, 1]
    assert [j["id"] for j in types["lekiwi"]["joints"]][-3:] == [9, 8, 7]
    assert data["mock_port"] == "mock" and "doppelt" in data["simulations"]


def test_setup_flow_in_simulation(config_path, tmp_path):
    client, server = make_client(config_path)
    with client, client.websocket_connect("/ws") as ws:
        me = ws.receive_json()["client_id"]
        snap = start_setup(client, me)
        assert snap["simulated"] and snap["next"] == "gripper" and snap["found"] is None
        states = (ws.receive_json() for _ in range(20))  # Zustand geht an alle Tabs
        assert any(m["setup"] and m["setup"]["owner"] == me for m in states)

        snap = client.post("/api/setup/scan", json={"client_id": me}).json()
        assert [s["id"] for s in snap["found"]] == [1]
        snap = client.post("/api/setup/assign", json={"client_id": me, "joint": "gripper"}).json()
        assert snap["last"]["ok"], snap["last"]
        assert snap["order"][0] == {"name": "gripper", "id": 6, "status": "gesetzt"}
        assert snap["next"] == "wrist_roll"

        # Neustart-Probe: In der Simulation wird aus- und eingeschaltet
        snap = client.post("/api/setup/verify", json={"client_id": me, "joint": "gripper"}).json()
        assert snap["last"]["ok"] and snap["order"][0]["status"] == "dauerhaft"

        # Nächster Servo, diesmal mit falscher Baudrate
        client.post("/api/setup/simulate", json={"client_id": me, "variant": "baudrate"})
        snap = client.post("/api/setup/scan", json={"client_id": me}).json()
        assert snap["found"][0]["baudrate"] == 115_200
        snap = client.post("/api/setup/assign", json={"client_id": me, "joint": "wrist_roll"}).json()
        assert snap["last"]["ok"]
        servo = server.setup.backend.servos[0]
        assert servo.id == 5 and servo.baudrate == 1_000_000

        log = (tmp_path / "logs" / "servo_ids.csv").read_text(encoding="utf-8").splitlines()
        assert log[0].startswith("zeit,aktion,ergebnis") and len(log) == 4
        assert client.post("/api/setup/stop", json={"client_id": me}).status_code == 200
        assert server.setup is None


def test_setup_refuses_two_servos_with_same_id(config_path):
    client, server = make_client(config_path)
    with client, client.websocket_connect("/ws") as ws:
        me = ws.receive_json()["client_id"]
        start_setup(client, me)
        client.post("/api/setup/simulate", json={"client_id": me, "variant": "doppelt"})
        client.post("/api/setup/scan", json={"client_id": me})
        r = client.post("/api/setup/assign", json={"client_id": me, "joint": "gripper"})
        assert r.status_code == 400 and "unzuverlässig" in r.json()["detail"]
        assert [s.id for s in server.setup.backend.servos] == [1, 1]


def test_setup_belongs_to_one_tab_and_ends_with_it(config_path):
    client, server = make_client(config_path)
    with client:
        with client.websocket_connect("/ws") as ws1, client.websocket_connect("/ws") as ws2:
            me, other = ws1.receive_json()["client_id"], ws2.receive_json()["client_id"]
            start_setup(client, me)
            r = client.post("/api/setup/scan", json={"client_id": other})
            assert r.status_code == 409 and "anderen Browser-Tab" in r.json()["detail"]
            r = client.post(
                "/api/setup/start", json={"client_id": other, "port": "mock", "device_type": "lekiwi"}
            )
            assert r.status_code == 409 and "läuft schon" in r.json()["detail"]
            assert (
                client.post(
                    "/api/setup/simulate", json={"client_id": me, "variant": "gibtsnicht"}
                ).status_code
                == 400
            )
        # Beide Tabs zu: Assistent beendet, Port frei
        assert wait_for(lambda: server.setup is None)


def test_setup_and_device_do_not_share_a_port(config_path, monkeypatch):
    from so101_lab.backends.mock import MockBackend

    client, server = make_client(config_path)

    class FakeUsb(MockBackend):
        def __init__(self, port):
            super().__init__()
            self.plug_new_servo()

    monkeypatch.setattr("so101_lab.backends.usb.UsbBackend", FakeUsb)
    with client, client.websocket_connect("/ws") as ws:
        me = ws.receive_json()["client_id"]
        r = client.post(
            "/api/setup/start",
            json={"client_id": me, "port": "/dev/ttyFAKE0", "device_type": "so101_follower"},
        )
        assert r.status_code == 200, r.text
        assert server.setup.port == "/dev/ttyFAKE0" and not server.setup.simulated
        r = client.post("/api/devices/usbarm/connect", json={"port": "/dev/ttyFAKE0"})
        assert r.status_code == 409 and "Einrichtungsassistenten" in r.json()["detail"]
        client.post("/api/setup/stop", json={"client_id": me})


def test_setup_unknown_type(config_path):
    client, _ = make_client(config_path)
    with client, client.websocket_connect("/ws") as ws:
        me = ws.receive_json()["client_id"]
        r = client.post("/api/setup/start", json={"client_id": me, "port": "mock", "device_type": "roboter"})
        assert r.status_code == 400 and "Unbekannter Gerätetyp" in r.json()["detail"]


def test_setup_free_id(config_path):
    client, server = make_client(config_path)
    with client, client.websocket_connect("/ws") as ws:
        me = ws.receive_json()["client_id"]
        start_setup(client, me)
        client.post("/api/setup/scan", json={"client_id": me})
        snap = client.post("/api/setup/assign", json={"client_id": me, "id": 99}).json()
        assert snap["last"]["ok"] and snap["last"]["new_id"] == 99
        assert server.setup.backend.servos[0].id == 99
        snap = client.post("/api/setup/verify", json={"client_id": me, "id": 99}).json()
        assert snap["last"]["ok"]
        r = client.post("/api/setup/assign", json={"client_id": me, "id": 300})
        assert r.status_code == 400 and "0 bis 253" in r.json()["detail"]
