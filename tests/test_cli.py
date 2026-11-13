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


def test_web_not_yet(capsys):
    assert main(["web"]) == 2
    assert "noch nicht umgesetzt" in capsys.readouterr().err


def test_scan_and_state_with_mock_device(capsys):
    config = str(ROOT / "config" / "arms.toml")
    assert main(["scan", "--device", "demo", "--config", config]) == 0
    assert "STS3215" in capsys.readouterr().out
    assert main(["state", "demo", "--config", config]) == 0
    assert "shoulder_pan" in capsys.readouterr().out


def test_choose_port_without_ports(monkeypatch):
    from so101_lab import cli, ports

    monkeypatch.setattr(ports, "list_serial_ports", lambda usb_only=True: [])
    with pytest.raises(cli.CliError):
        cli.choose_port(None, interactive=False)


def test_doctor_runs_without_ports(monkeypatch, capsys):
    monkeypatch.setattr(doctor, "list_serial_ports", lambda: [])
    code = main(["doctor", "--config", str(ROOT / "config" / "arms.toml")])
    out = capsys.readouterr().out
    assert "Kein USB-Seriell-Port gefunden" in out
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
