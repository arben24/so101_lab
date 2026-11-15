"""RemoteBackend gegen eine echte Web-App im selben Prozess („Pi“ auf 127.0.0.1).

Der Pi-Server benutzt Mock-Geräte, das RemoteBackend spricht über einen echten WebSocket
mit ihm. So werden Protokoll, Watchdog, Not-Aus und sicheres Zurücksetzen auf dem Pi
ohne Hardware geprüft.
"""

import socket
import threading
import time

import httpx
import pytest
import uvicorn

from so101_lab.backends import BackendError, create_backend
from so101_lab.backends.base import Capability, ServoNotRespondingError
from so101_lab.backends.remote import RemoteBackend, RemoteConnectionError, bus_url
from so101_lab.config import load_config
from so101_lab.safety import open_arm
from so101_lab.web.app import LabServer, create_app

TOKEN = "geheim"

PI_CONFIG = """
[[device]]
name = "demo"
type = "so101_follower"
backend = "mock"

[[device]]
name = "kiwi"
type = "lekiwi"
backend = "mock"

[[device]]
name = "halter"
type = "so101_follower"
backend = "mock"
safe_state = "hold"

[[device]]
name = "ohne_port"
type = "so101_follower"
backend = "usb"
"""


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def wait_for(condition, timeout=3.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if condition():
            return True
        time.sleep(0.02)
    return False


class Pi:
    def __init__(self, server: LabServer, port: int):
        self.server = server
        self.port = port
        self.address = f"127.0.0.1:{port}"

    def backend(self, device="demo", token=TOKEN, expected_ids=None) -> RemoteBackend:
        return RemoteBackend(self.address, device, token=token, expected_ids=expected_ids)

    def mock(self, device="demo"):
        return self.server.remote_buses[device].arm.backend

    def post(self, path, **kwargs):
        return httpx.post(
            f"http://{self.address}{path}", headers={"Authorization": f"Bearer {TOKEN}"}, **kwargs
        )


@pytest.fixture
def pi(tmp_path, monkeypatch):
    monkeypatch.setenv("SO101_LAB_ROOT", str(tmp_path))
    monkeypatch.setenv("HF_LEROBOT_CALIBRATION", str(tmp_path / "keine"))
    path = tmp_path / "pi.toml"
    path.write_text(PI_CONFIG, encoding="utf-8")
    server = LabServer(config_path=str(path), token=TOKEN, bus_watchdog_ms=300, poll_hz=50)
    port = free_port()
    uv = uvicorn.Server(uvicorn.Config(create_app(server), host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=uv.run, daemon=True)
    thread.start()
    assert wait_for(lambda: uv.started), "Server startet nicht"
    yield Pi(server, port)
    uv.should_exit = True
    thread.join(timeout=5)


def torque(mock):
    return {s.id: s.values["torque_enable"] for s in mock.servos}


def test_bus_url():
    assert bus_url("raspberrypi.local", "lekiwi") == "ws://raspberrypi.local:8000/ws/bus/lekiwi"
    assert bus_url("http://pi:8001", "a b") == "ws://pi:8001/ws/bus/a%20b"
    assert bus_url("https://pi", "x") == "wss://pi:8000/ws/bus/x"
    with pytest.raises(BackendError):
        bus_url("ftp://pi", "x")


def test_read_write_and_checks_run_on_pi(pi):
    b = pi.backend()
    b.connect()
    try:
        assert b.hello["device_type"] == "so101_follower"
        assert Capability.EEPROM in b.capabilities
        mock = pi.mock()
        assert b.ping(1) == mock.ping(1)
        assert b.ping(42) is None
        assert sorted(s.id for s in b.scan()) == [1, 2, 3, 4, 5, 6]
        assert b.read_positions([1, 2]) == mock.read_positions([1, 2])
        state = b.read_state([1, 2])
        assert state[1].voltage == pytest.approx(12.0) and state[2].id == 2
        b.set_torque([1], True)
        assert torque(mock)[1] == 1
        b.sync_write("goal_position", {1: 2100})
        assert mock.servo(1).values["goal_position"] == 2100
        # Prüfungen der Backend-Schicht greifen auf dem Pi, Fehlerklasse kommt mit
        with pytest.raises(BackendError, match="EEPROM"):
            b.write_register(1, "max_position_limit", 3000)
        with pytest.raises(BackendError, match="0 oder 1"):
            b.write_register(1, "torque_enable", 128)
        with pytest.raises(ServoNotRespondingError):
            b.change_id(42, 43)
        # EEPROM am Stück auf dem Pi: Drehmoment aus, schreiben, zurücklesen
        b.write_eeprom(2, "max_position_limit", 3000)
        assert mock.servo(2).values["max_position_limit"] == 3000
        assert mock.servo(2).values["lock"] == 1
        assert b.read_register(2, "max_position_limit") == 3000
        # nach einem Fehler geht es normal weiter
        assert b.ping(1) is not None
    finally:
        b.disconnect()
    assert not pi.server.remote_buses


def test_refusals(pi):
    with pytest.raises(RemoteConnectionError, match="Token"):
        pi.backend(token="falsch").connect()
    with pytest.raises(RemoteConnectionError, match="Token"):
        pi.backend(token=None).connect()
    with pytest.raises(RemoteConnectionError, match="nicht in der Konfiguration"):
        pi.backend("gibtsnicht").connect()
    with pytest.raises(RemoteConnectionError, match="Port in arms.toml"):
        pi.backend("ohne_port").connect()
    # lekiwi am Laptop, aber auf dem Pi nur ein Arm mit 6 Servos
    with pytest.raises(RemoteConnectionError, match="IDs"):
        pi.backend("demo", expected_ids=range(1, 10)).connect()
    assert not pi.server.remote_buses
    # Teil eines Geräts geht: lekiwi_arm (1–6) gegen kiwi (1–9) auf dem Pi
    b = pi.backend("kiwi", expected_ids=range(1, 7))
    b.connect()
    try:
        # zweiter Laptop für dasselbe Gerät wird abgelehnt
        with pytest.raises(RemoteConnectionError, match="schon"):
            pi.backend("kiwi").connect()
        # und im Browser auf dem Pi lässt es sich nicht verbinden
        r = pi.post("/api/devices/kiwi/connect", json={})
        assert r.status_code == 409 and "Netz" in r.json()["detail"]
    finally:
        b.disconnect()


def test_orderly_disconnect_leaves_servos_alone(pi):
    b = pi.backend()
    b.connect()
    mock = pi.mock()
    b.set_torque([1, 2, 3, 4, 5, 6], True)
    b.disconnect()
    assert wait_for(lambda: not pi.server.remote_buses)
    assert all(torque(mock).values())  # `close` heißt: Laptop hat selbst zurückgesetzt


def test_watchdog_stops_wheels_and_sets_safe_state(pi):
    b = pi.backend("kiwi")
    b.connect()
    mock = pi.mock("kiwi")
    b.set_torque(range(1, 10), True)
    b.sync_write("goal_velocity", {7: 800, 8: 800, 9: 800})
    assert b.hello["watchdog_ms"] == 300
    b._stop.set()  # Netz weg: keine Lebenszeichen mehr
    b._heartbeat.join(timeout=1)
    assert wait_for(lambda: not pi.server.remote_buses)
    assert not any(torque(mock).values())  # safe_state torque_off
    assert all(mock.servo(i).values["goal_velocity"] == 0 for i in (7, 8, 9))
    with pytest.raises(RemoteConnectionError):
        b.ping(1)
    b.disconnect()  # aufräumen geht trotzdem


def test_heartbeat_keeps_idle_connection(pi):
    b = pi.backend()
    b.connect()
    try:
        time.sleep(1.0)  # dreimal so lang wie der Watchdog, ohne eigene Anfragen
        assert "demo" in pi.server.remote_buses
        assert b.ping(1) is not None
    finally:
        b.disconnect()


def test_connection_lost_hold(pi):
    b = pi.backend("halter")
    b.connect()
    mock = pi.mock("halter")
    b.set_torque([1, 2, 3, 4, 5, 6], True)
    b._stop.set()
    b._ws.close()  # abrupt, ohne `close`-Anfrage
    assert wait_for(lambda: not pi.server.remote_buses)
    assert all(torque(mock).values())  # safe_state hold: Drehmoment bleibt an
    b._ws = None


def test_emergency_stop_on_pi_ends_connection(pi):
    b = pi.backend("kiwi")
    b.connect()
    mock = pi.mock("kiwi")
    b.set_torque(range(1, 10), True)
    b.sync_write("goal_velocity", {7: 500})
    r = pi.post("/api/estop")
    assert "kiwi" in r.json()["stopped"]
    assert not any(torque(mock).values())
    assert mock.servo(7).values["goal_velocity"] == 0
    with pytest.raises(BackendError, match="Not-Aus"):
        b.set_torque([1], True)
    assert not any(torque(mock).values())
    assert wait_for(lambda: not pi.server.remote_buses)
    b.disconnect()


def test_open_arm_over_remote(pi, tmp_path, monkeypatch):
    laptop = tmp_path / "laptop.toml"
    laptop.write_text(
        f"""
[[device]]
name = "pi_arm"
type = "so101_follower"
backend = "remote"
address = "{pi.address}"
remote_device = "demo"
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("SO101_REMOTE_TOKEN", TOKEN)
    backend = create_backend(load_config(laptop).device("pi_arm"))
    assert isinstance(backend, RemoteBackend) and backend.device == "demo"
    with open_arm("pi_arm", config_path=str(laptop)) as arm:
        mock = pi.mock()
        arm.enable_torque()
        start = arm.read_positions()["shoulder_pan"]
        sent = arm.move_to({"shoulder_pan": start + 100})
        assert mock.servo(1).values["goal_position"] == sent["shoulder_pan"]
        assert mock.servo(1).values["goal_velocity"] == 1000  # configure_motion lief über das Netz
    # open_arm setzt zurück (Drehmoment aus) und trennt ordentlich
    assert not any(torque(mock).values())
    assert wait_for(lambda: not pi.server.remote_buses)


def test_laptop_web_app_with_remote_device(pi, tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    laptop = tmp_path / "laptop.toml"
    laptop.write_text(
        f'[[device]]\nname = "demo"\ntype = "so101_follower"\nbackend = "remote"\naddress = "{pi.address}"\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("SO101_REMOTE_TOKEN", TOKEN)
    server = LabServer(config_path=str(laptop), poll_hz=30)
    with TestClient(create_app(server)) as client:
        assert client.post("/api/devices/demo/connect", json={}).status_code == 200
        session = server.sessions["demo"]
        assert wait_for(lambda: session.snapshot().get("joints"))
        assert session.snapshot()["backend"] == "remote" and session.snapshot()["error"] is None
        assert "demo" in pi.server.remote_buses
        assert client.post("/api/devices/demo/disconnect", json={}).status_code == 200
    assert wait_for(lambda: not pi.server.remote_buses)
