import pytest

from so101_lab.backends import BackendError, ServoNotRespondingError, VerificationError
from so101_lab.backends.mock import MockBackend, MockServo


def test_scan_and_ping(mock_bus):
    found = mock_bus.scan()
    assert [s.id for s in found] == [1, 2, 3, 4, 5, 6]
    assert found[0].model_name == "STS3215" and found[0].firmware == "3.7"
    assert mock_bus.ping(1) == 777
    assert mock_bus.ping(42) is None


def test_scan_over_baudrates():
    servo = MockServo.factory_new(1)
    servo.values["baud_rate"] = 4  # 115200 laut rustypot
    bus = MockBackend([servo])
    bus.connect()
    assert bus.scan() == []
    found = bus.scan(baudrates=[1_000_000, 115_200])
    assert [(s.id, s.baudrate) for s in found] == [(1, 115_200)]
    assert bus.baudrate == 1_000_000


def test_write_register_refuses_eeprom_and_readonly(mock_bus):
    with pytest.raises(BackendError, match="EEPROM"):
        mock_bus.write_register(1, "P_Coefficient", 16)
    with pytest.raises(BackendError, match="nur lesbar"):
        mock_bus.write_register(1, "present_position", 5)
    with pytest.raises(BackendError, match="128"):
        mock_bus.write_register(1, "torque_enable", 128)


def test_write_eeprom_unlocks_writes_locks_and_verifies(mock_bus):
    mock_bus.set_torque([1], True)
    mock_bus.write_eeprom(1, "p_coefficient", 16)
    assert mock_bus.read_register(1, "p_coefficient") == 16
    assert mock_bus.read_register(1, "lock") == 1
    assert mock_bus.read_register(1, "torque_enable") == 0
    ops = [(op, name, value) for op, _, name, value in mock_bus.log]
    i = ops.index(("write", "p_coefficient", 16))
    assert ops[i - 1] == ("write", "lock", 0) and ops[i + 1] == ("write", "lock", 1)
    # bleibt nach dem Ausschalten erhalten
    mock_bus.servo(1).power_cycle()
    assert mock_bus.read_register(1, "p_coefficient") == 16


def test_eeprom_write_with_lock_closed_is_lost_after_power_cycle(mock_bus):
    servo = mock_bus.servo(1)
    servo.values["lock"] = 1
    servo.write("p_coefficient", 10)
    assert servo.values["p_coefficient"] == 10
    servo.power_cycle()
    assert servo.values["p_coefficient"] == 32


def test_write_eeprom_detects_mismatch(mock_bus, monkeypatch):
    real_read = mock_bus._read
    monkeypatch.setattr(mock_bus, "_read", lambda i, n: 99 if n == "p_coefficient" else real_read(i, n))
    with pytest.raises(VerificationError):
        mock_bus.write_eeprom(1, "p_coefficient", 16)


def test_change_id():
    bus = MockBackend([MockServo.factory_new(1)])
    bus.connect()
    bus.change_id(1, 6)
    assert bus.ping(6) == 777 and bus.ping(1) is None
    bus.servo(6).power_cycle()
    assert bus.ping(6) == 777


def test_change_id_refuses_taken_id(mock_bus):
    with pytest.raises(BackendError, match="belegt"):
        mock_bus.change_id(1, 2)


def test_change_id_missing_servo(mock_bus):
    with pytest.raises(ServoNotRespondingError):
        mock_bus.change_id(42, 43)


def test_duplicate_ids_collide():
    bus = MockBackend([MockServo.factory_new(1), MockServo.factory_new(1)])
    bus.connect()
    with pytest.raises(BackendError, match="Mehrere"):
        bus.read_register(1, "present_position")


def test_change_baudrate():
    bus = MockBackend([MockServo.factory_new(1)])
    bus.connect()
    bus.change_baudrate(1, 500_000)
    assert bus.baudrate == 500_000 and bus.ping(1) == 777
    bus._set_port_baudrate(1_000_000)
    assert bus.ping(1) is None


def test_motion_respects_speed_and_limits(mock_bus, clock):
    mock_bus.sync_write("goal_velocity", {1: 1000})
    mock_bus.set_torque([1], True)
    mock_bus.sync_write("goal_position", {1: 3048})
    clock.advance(0.5)
    assert mock_bus.read_positions([1])[1] == 2548  # 1000 Schritt/s · 0,5 s
    clock.advance(2)
    assert mock_bus.read_positions([1])[1] == 3048


def test_no_motion_without_torque(mock_bus, clock):
    mock_bus.sync_write("goal_position", {1: 3000})
    clock.advance(1)
    assert mock_bus.read_positions([1])[1] == 2048


def test_read_state(mock_bus):
    state = mock_bus.read_state([1, 2])
    assert state[1].voltage == 12.0 and state[1].torque_enabled is False and state[1].errors == []


def test_not_connected():
    bus = MockBackend.for_ids([1])
    with pytest.raises(BackendError, match="Nicht verbunden"):
        bus.ping(1)
