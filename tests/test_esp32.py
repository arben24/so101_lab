"""ESP32-Backend: Protokoll gegen die Referenz der Firmware, Backend gegen den nachgebauten Knoten."""

import importlib.util
import socket
import threading
import time
from pathlib import Path

import pytest

from so101_lab.arm import Arm
from so101_lab.backends import BackendError, create_backend
from so101_lab.backends import esp32_protocol as p
from so101_lab.backends.base import Capability, NotSupportedError
from so101_lab.backends.esp32 import Esp32Backend, Esp32BusyError, Esp32Error
from so101_lab.config import DeviceConfig, load_config
from so101_lab.devices import get_device_spec
from so101_lab.esp32_sim import SimNode
from so101_lab.safety import open_arm

ROOT = Path(__file__).resolve().parents[1]
LEKIWI = get_device_spec("lekiwi")


def reference():
    """firmware/esp32/werkzeuge/knoten_test.py, die Python-Seite der Firmware."""
    spec = importlib.util.spec_from_file_location(
        "knoten_test", ROOT / "firmware/esp32/werkzeuge/knoten_test.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def wait_for(condition, timeout=2.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if condition():
            return True
        time.sleep(0.02)
    return False


@pytest.fixture
def node():
    with SimNode(profile="lekiwi", port=0) as n:
        yield n


def backend(node, device="lekiwi"):
    b = Esp32Backend(f"127.0.0.1:{node.port}", get_device_spec(device))
    b.connect()
    return b


def servo(node, i):
    return node.bus.servo(i).values


# ------------------------------------------------------------------ Protokoll


def test_crc_and_packets_match_firmware_reference():
    ref = reference()
    assert p.crc16(b"123456789") == 0x29B1  # Prüfwert von CRC-16/CCITT-FALSE
    for typ, seq, payload in (
        (p.Typ.PING, 1, b""),
        (p.Typ.BEFEHL, 65535, bytes(9)),
        (p.Typ.REG_LESEN, 7, b"\x01\x38\x02"),
    ):
        assert p.build(typ, seq, payload) == ref.bauen(typ, seq, payload)
        assert p.parse(p.build(typ, seq, payload)) == (typ, seq, payload)
    setpoints = {1: (True, 2048), 7: (False, -300)}
    nd = bytes([2]) + b"".join(
        ref.struct.pack("<BBh", i, 1 if t else 0, v) for i, (t, v) in setpoints.items()
    )
    assert p.build_befehl(setpoints) == nd
    assert p.parse_befehl(nd) == setpoints


def test_parse_rejects_broken_packets():
    good = p.build(p.Typ.PING, 5)
    assert p.parse(good[:-1] + bytes([good[-1] ^ 1])) is None  # CRC
    assert p.parse(good[:-3]) is None  # Länge
    assert p.parse(b"\x00\x00" + good[2:]) is None  # Magic
    assert p.parse_befehl(bytes([2, 1, 1, 0, 0])) is None  # Anzahl passt nicht zur Länge
    assert p.newer(1, 65535) and p.newer(5, 4) and not p.newer(4, 5) and not p.newer(3, 3)


def test_zustand_and_info_readable_by_reference(node):
    ref = reference()
    info = ref.info_lesen(p.build_info(node.info()))
    assert info["profil"] == "lekiwi" and info["watchdog_ms"] == 300
    assert info["servos"] == [(i, 0) for i in range(1, 7)] + [(i, 1) for i in (7, 8, 9)]
    z = p.Zustand(3, 1000, p.KNOTEN_BELEGT, 1, 400, 0, {1: p.ServoZustand(1, 3, -12, 5, -7, 120, 30, 0)})
    parsed = ref.zustand_lesen(p.build_zustand(z))
    assert (
        parsed["belegt"]
        and parsed["servos"][0]["position"] == -12
        and parsed["servos"][0]["spannung"] == 12.0
    )
    assert p.parse_zustand(p.build_zustand(z)) == z


# ------------------------------------------------------------------ Verbinden und Profil


def test_connect_reads_profile_and_capabilities(node):
    b = backend(node)
    try:
        assert b.info.profile == "lekiwi" and b.info.ids == list(range(1, 10))
        assert {Capability.SCAN, Capability.REGISTERS, Capability.EEPROM, Capability.WHEELS} == set(
            b.capabilities
        )
        assert not b.supports(Capability.SCAN_BAUDRATES) and not b.supports(Capability.CHANGE_BAUDRATE)
        assert b.read_positions([1, 7]) == {1: 2048, 7: 2048}
        state = b.read_state([1])[1]
        assert state.voltage == 12.0 and state.current_ma is None and not state.torque_enabled
    finally:
        b.disconnect()


@pytest.mark.parametrize(
    ("profile", "device", "ok"),
    [
        ("lekiwi", "lekiwi_arm", True),
        ("lekiwi", "so101_follower", True),
        ("so101", "lekiwi", False),
        ("so101_leader", "so101_follower", False),
        ("so101", "so101_leader", True),
    ],
)
def test_profile_must_fit_device(profile, device, ok):
    with SimNode(profile=profile, port=0) as n:
        b = Esp32Backend(f"127.0.0.1:{n.port}", get_device_spec(device))
        if ok:
            b.connect()
            b.disconnect()
        else:
            with pytest.raises(Esp32Error, match="Profil"):
                b.connect()


def test_unreachable_node_fails_fast():
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    b = Esp32Backend(f"127.0.0.1:{port}", LEKIWI, timeout_s=0.05)
    start = time.monotonic()
    with pytest.raises(Esp32Error, match="antwortet nicht"):
        b.connect()
    assert time.monotonic() - start < 1.0 and not b.is_connected


def test_leader_profile_never_gets_torque():
    with SimNode(profile="so101_leader", port=0) as n:
        b = Esp32Backend(f"127.0.0.1:{n.port}", get_device_spec("so101_leader"))
        b.connect()
        try:
            with pytest.raises(NotSupportedError, match="nur lesen"):
                b.set_torque([1], True)
            assert b.read_positions([1, 6])  # lesen geht
        finally:
            b.disconnect()


# ------------------------------------------------------------------ Sicherheit


def test_enable_torque_without_goal_holds_present_position(node):
    b = backend(node)
    try:
        node.bus.move_by_hand(1, 3000)  # jemand hat das Gelenk verstellt, Tabelle kennt noch 2048
        assert wait_for(lambda: b.read_positions([1])[1] == 3000)
        b.set_torque([1], True)
        assert wait_for(lambda: servo(node, 1)["torque_enable"] == 1)
        assert servo(node, 1)["goal_position"] == 3000  # kein Sprung auf 2048
    finally:
        b.disconnect()


def test_old_setpoints_of_another_source_are_not_activated(node):
    ref = reference()
    other = ref.Knoten(ref.UdpVerbindung("127.0.0.1"))
    other.v.ziel = ("127.0.0.1", node.port)
    other.befehl({2: (True, 2048)})  # fremde Quelle: Servo 2 an
    other.befehl({2: (True, 2048), 3: (False, 100)})  # und ein gefährlicher Rest für Servo 3
    assert wait_for(lambda: servo(node, 2)["torque_enable"] == 1)
    other.stopp()
    b = backend(node)
    try:
        b.sync_write("goal_position", {1: 2100})
        b.set_torque([1, 3], True)
        time.sleep(0.2)
        assert servo(node, 3)["goal_position"] == 2048  # nicht 100 aus der fremden Tabelle
        assert servo(node, 2)["torque_enable"] == 1 and servo(node, 2)["goal_position"] == 2048
    finally:
        b.disconnect()


def test_second_source_is_busy_and_stopp_still_works(node):
    a, b = backend(node), backend(node)
    try:
        a.set_torque(range(1, 10), True)
        a.sync_write("goal_velocity", {7: 400})
        assert wait_for(lambda: servo(node, 7)["goal_velocity"] == 400)
        with pytest.raises(Esp32BusyError, match="anderen Quelle"):
            b.sync_write("goal_position", {1: 2200})
        with pytest.raises(Esp32BusyError, match="STOPP gesendet"):
            b.set_torque([7], False)  # Anhalten geht dann per STOPP
        assert wait_for(lambda: servo(node, 7)["goal_velocity"] == 0)
        assert servo(node, 1)["torque_enable"] == 1  # STOPP: Drehmoment bleibt an
    finally:
        b.disconnect()
        a.disconnect()


def test_keepalive_holds_control_and_gap_requires_reconnect(node):
    b = backend(node)
    b.set_torque([7, 8, 9], True)
    b.sync_write("goal_velocity", {7: 600, 8: 600, 9: 600})
    time.sleep(1.0)  # dreimal der Watchdog, ohne eigene Befehle
    assert not b.zustand().safe_state and servo(node, 7)["goal_velocity"] == 600
    b._stop.set()  # WLAN weg: keine Lebenszeichen
    b._keepalive.join(timeout=1)
    assert wait_for(lambda: b.zustand().safe_state)
    assert wait_for(lambda: servo(node, 7)["goal_velocity"] == 0)  # Knoten hat die Räder gestoppt
    with pytest.raises(Esp32Error, match="Neu verbinden"):
        b.sync_write("goal_velocity", {7: 600})
    time.sleep(0.1)
    assert servo(node, 7)["goal_velocity"] == 0  # alte Sollwerte fahren nicht wieder an
    b.disconnect()


def test_disconnect_sends_stopp(node):
    b = backend(node)
    b.set_torque([7], True)
    b.sync_write("goal_velocity", {7: 500})
    assert wait_for(lambda: servo(node, 7)["goal_velocity"] == 500)
    b.disconnect()
    assert wait_for(lambda: servo(node, 7)["goal_velocity"] == 0)
    assert wait_for(lambda: node.log[-1].startswith("STOPP"))
    alive = [t for t in threading.enumerate() if t.name == "esp32-keepalive"]
    assert not alive  # kein verwaister Lebenszeichen-Thread


# ------------------------------------------------------------------ Register und Suche


def test_registers_scan_and_eeprom(node):
    b = backend(node)
    try:
        assert [s.id for s in b.scan(ids=range(0, 12))] == list(range(1, 10))
        assert b.scan(ids=[1], baudrates=[1_000_000])[0].firmware == "3.7"
        with pytest.raises(NotSupportedError, match="1 Mbit/s"):
            b.scan(baudrates=[1_000_000, 115_200])
        assert b.sync_read([1, 2], "operating_mode") == {1: 0, 2: 0}  # einzelne REG_LESEN
        b.write_register(1, "acceleration", 30)
        assert servo(node, 1)["acceleration"] == 30
        b.sync_write("goal_velocity", {1: 500})  # Gelenk: Tempogrenze per Register
        assert servo(node, 1)["goal_velocity"] == 500
        with pytest.raises(BackendError, match="EEPROM"):
            b.write_register(1, "max_position_limit", 3000)
        b.write_eeprom(1, "max_position_limit", 3000)
        assert servo(node, 1)["max_position_limit"] == 3000 and servo(node, 1)["lock"] == 1
        with pytest.raises(NotSupportedError, match="nicht im Profil"):
            b.sync_write("goal_position", {12: 100})
    finally:
        b.disconnect()


def test_arm_over_esp32(node, tmp_path, monkeypatch):
    monkeypatch.setenv("SO101_LAB_ROOT", str(tmp_path))
    monkeypatch.setenv("HF_LEROBOT_CALIBRATION", str(tmp_path / "keine"))
    config = tmp_path / "arms.toml"
    config.write_text(
        f'[[device]]\nname = "kiwi"\ntype = "lekiwi"\nbackend = "esp32"\naddress = "127.0.0.1:{node.port}"\n',
        encoding="utf-8",
    )
    assert isinstance(create_backend(load_config(config).device("kiwi")), Esp32Backend)
    with open_arm("kiwi", config_path=str(config)) as arm:
        arm.enable_torque()
        sent = arm.move_to({"arm_shoulder_pan": 2300})
        assert wait_for(lambda: servo(node, 1)["goal_position"] == sent["arm_shoulder_pan"])
        assert servo(node, 1)["goal_velocity"] == 1000  # configure_motion: Tempogrenze per Register
        arm.set_wheel_velocities({"base_left_wheel": 800})
        assert wait_for(lambda: servo(node, 7)["goal_velocity"] == 800)
    # open_arm: Räder stopp, Drehmoment aus (safe_state), dann STOPP
    assert wait_for(lambda: servo(node, 7)["goal_velocity"] == 0)
    assert wait_for(lambda: not any(servo(node, i)["torque_enable"] for i in range(1, 10)))


def test_emergency_stop(node):
    cfg = DeviceConfig(name="k", type="lekiwi", backend="esp32", calibration_id="k", address="x")
    b = backend(node)
    arm = Arm(b, LEKIWI, cfg)
    try:
        arm.enable_torque()
        arm.set_wheel_velocities({"base_back_wheel": -700})
        assert wait_for(lambda: servo(node, 8)["goal_velocity"] == -700)
        arm.emergency_stop()
        assert wait_for(lambda: not any(servo(node, i)["torque_enable"] for i in range(1, 10)))
        assert servo(node, 8)["goal_velocity"] == 0
    finally:
        b.disconnect()


def test_web_app_with_esp32_device(node, tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from so101_lab.web.app import LabServer, create_app

    monkeypatch.setenv("SO101_LAB_ROOT", str(tmp_path))
    monkeypatch.setenv("HF_LEROBOT_CALIBRATION", str(tmp_path / "keine"))
    config = tmp_path / "arms.toml"
    config.write_text(
        f'[[device]]\nname = "kiwi"\ntype = "lekiwi"\nbackend = "esp32"\naddress = "127.0.0.1:{node.port}"\n',
        encoding="utf-8",
    )
    server = LabServer(config_path=str(config), poll_hz=30)
    with TestClient(create_app(server)) as client, client.websocket_connect("/ws") as ws:
        me = ws.receive_json()["client_id"]
        assert client.post("/api/devices/kiwi/connect", json={}).status_code == 200
        session = server.sessions["kiwi"]
        assert wait_for(lambda: session.snapshot().get("joints"))
        snap = session.snapshot()
        assert snap["backend"] == "esp32" and snap["error"] is None and len(snap["joints"]) == 9
        assert client.post("/api/devices/kiwi/control", json={"client_id": me}).status_code == 200
        assert (
            client.post("/api/devices/kiwi/torque", json={"client_id": me, "enabled": True}).status_code
            == 200
        )
        assert wait_for(lambda: servo(node, 1)["torque_enable"] == 1)
        assert client.post("/api/estop").status_code == 200
        assert wait_for(lambda: not any(servo(node, i)["torque_enable"] for i in range(1, 10)))
        assert client.post("/api/devices/kiwi/disconnect", json={"client_id": me}).status_code == 200
