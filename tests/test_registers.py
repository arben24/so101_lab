import pytest

from so101_lab import registers as regs


def test_lerobot_names_and_addresses():
    assert regs.lookup("Goal_Position").address == 42
    assert regs.lookup("Present_Position").address == 56
    assert regs.lookup("goal_speed").name == "goal_velocity"  # Alias
    assert regs.lookup("Baud_Rate").address == 6
    assert regs.model_number() == 777


def test_memory_areas():
    assert regs.lookup("id").area is regs.Area.EEPROM
    assert regs.lookup("operating_mode").area is regs.Area.EEPROM
    assert regs.lookup("torque_enable").area is regs.Area.RAM
    assert regs.lookup("lock").area is regs.Area.RAM
    assert regs.lookup("maximum_acceleration").area is regs.Area.UNKNOWN


def test_whitelist_contains_only_writable_ram_registers():
    for name in regs.RAM_WRITE_WHITELIST:
        reg = regs.lookup(name)
        assert reg.area is regs.Area.RAM and reg.writable
    assert "lock" not in regs.RAM_WRITE_WHITELIST


@pytest.mark.parametrize(("value", "raw"), [(300, 300), (-300, 33068), (0, 0)])
def test_sign_magnitude(value, raw):
    reg = regs.lookup("goal_velocity")
    assert reg.encode(value) == raw
    assert reg.decode(raw) == value


def test_encode_rejects_out_of_range():
    with pytest.raises(ValueError):
        regs.lookup("homing_offset").encode(2048)


def test_unknown_register():
    with pytest.raises(KeyError):
        regs.lookup("gibt_es_nicht")
