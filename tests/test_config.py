import pytest

from so101_lab.config import ConfigError, load_config, parse_config
from so101_lab.paths import project_root


def test_example_config_loads():
    cfg = load_config(project_root() / "config" / "arms.toml")
    assert "demo" in cfg.devices
    assert cfg.device("demo").backend == "mock"


def _one(**kw):
    base = {"name": "a", "type": "so101_follower", "backend": "usb"}
    base.update(kw)
    return parse_config({"device": [base]}).device("a")


def test_defaults():
    d = _one()
    assert d.port is None and d.calibration_id == "a" and d.safe_state == "torque_off"


@pytest.mark.parametrize(
    ("kw", "message"),
    [
        ({"type": "roboter"}, "type"),
        ({"backend": "bluetooth"}, "backend"),
        ({"backend": "esp32"}, "address"),
        ({"safe_state": "egal"}, "safe_state"),
        ({"max_velocity": 5000}, "max_velocity"),
        ({"farbe": "rot"}, "unbekannte"),
    ],
)
def test_invalid(kw, message):
    with pytest.raises(ConfigError, match=message):
        _one(**kw)


def test_duplicate_names():
    dev = {"name": "a", "type": "so101_follower", "backend": "mock"}
    with pytest.raises(ConfigError, match="doppelt"):
        parse_config({"device": [dev, dev]})


def test_unknown_device_name():
    with pytest.raises(ConfigError, match="nicht in der Konfiguration"):
        parse_config({}).device("x")
