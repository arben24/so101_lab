"""Tests für CLI und doctor. Sie brauchen keine Hardware."""

import tomllib
from pathlib import Path

import pytest

from so101_lab import LEROBOT_PINNED_VERSION, doctor
from so101_lab.cli import build_parser, main

ROOT = Path(__file__).resolve().parent.parent


def test_pinned_version_matches_pyproject():
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    deps = " ".join(pyproject["project"]["dependencies"])
    assert f"lerobot[feetech,lekiwi,core-scripts]=={LEROBOT_PINNED_VERSION}" in deps


def test_parser_requires_command():
    with pytest.raises(SystemExit):
        build_parser().parse_args([])


@pytest.mark.parametrize("command", ["scan", "web"])
def test_unimplemented_commands_exit_with_2(command, capsys):
    assert main([command]) == 2
    assert "noch nicht umgesetzt" in capsys.readouterr().err


def test_doctor_runs_without_ports(monkeypatch, capsys):
    monkeypatch.setattr(doctor, "list_serial_ports", lambda: [])
    code = main(["doctor", "--config", str(ROOT / "config" / "arms.toml")])
    out = capsys.readouterr().out
    assert "Kein serieller Port gefunden" in out
    assert code == 0  # fehlender Port ist eine Warnung, kein Fehler


def test_doctor_flags_invalid_config(tmp_path):
    bad = tmp_path / "arms.toml"
    bad.write_text("das ist [ kein toml", encoding="utf-8")
    (result,) = doctor.check_config(str(bad))
    assert result.status == doctor.FAIL


def test_example_config_is_valid_and_has_required_keys():
    data = tomllib.loads((ROOT / "config" / "arms.toml").read_text(encoding="utf-8"))
    for device in data["device"]:
        assert {"name", "type", "backend", "calibration_id"} <= device.keys()
        assert device["type"] in {"so101_follower", "so101_leader", "lekiwi"}
        assert device["backend"] in {"usb", "remote", "esp32", "mock"}
