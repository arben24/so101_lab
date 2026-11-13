"""open_arm setzt auch nach einem Fehler sicher zurück."""

import pytest

from so101_lab import safety
from so101_lab.backends.mock import MockBackend

CONFIG = """
[[device]]
name = "sim"
type = "so101_follower"
backend = "mock"
"""


@pytest.fixture
def config_file(tmp_path, monkeypatch):
    monkeypatch.setenv("SO101_LAB_ROOT", str(tmp_path))
    monkeypatch.setenv("HF_LEROBOT_CALIBRATION", str(tmp_path / "nichts"))
    path = tmp_path / "arms.toml"
    path.write_text(CONFIG, encoding="utf-8")
    return path


def test_torque_off_after_exception(config_file, monkeypatch):
    created = {}
    original = MockBackend.for_ids.__func__

    def remember(cls, *a, **kw):
        created["bus"] = original(cls, *a, **kw)
        return created["bus"]

    monkeypatch.setattr(MockBackend, "for_ids", classmethod(remember))
    with pytest.raises(RuntimeError), safety.open_arm("sim", config_path=str(config_file)) as arm:
        arm.enable_torque()
        raise RuntimeError("Fehler im Lernskript")
    bus = created["bus"]
    assert not bus.is_connected
    assert all(s.values["torque_enable"] == 0 for s in bus.servos)


def test_read_only_changes_nothing(config_file, monkeypatch):
    created = {}
    original = MockBackend.for_ids.__func__

    def remember(cls, *a, **kw):
        created["bus"] = original(cls, *a, **kw)
        return created["bus"]

    monkeypatch.setattr(MockBackend, "for_ids", classmethod(remember))
    with safety.open_arm("sim", config_path=str(config_file), read_only=True) as arm:
        arm.read_state()
    assert [op for op, *_ in created["bus"].log] == []
