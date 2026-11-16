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
        assert device["type"] in {"so101_follower", "so101_leader", "lekiwi", "lekiwi_arm", "lekiwi_base"}
        assert device["backend"] in {"usb", "remote", "esp32", "mock"}


def test_service_unit(capsys, tmp_path):
    from so101_lab.service import unit_text

    text = unit_text(user="kiwi", root=tmp_path, uv="/opt/uv", port=8001)
    assert "User=kiwi" in text and f"WorkingDirectory={tmp_path.resolve()}" in text
    assert "ExecStart=/opt/uv run --no-sync so101-lab web --host 0.0.0.0 --port 8001 --no-docs" in text
    assert "EnvironmentFile=/etc/so101-lab.env" in text and "SO101_LAB_TOKEN=<geheim>" in text
    assert "SupplementaryGroups=dialout video" in text
    assert "--no-docs" not in unit_text(user="kiwi", root=tmp_path, uv="/opt/uv", docs=True)
    assert main(["service"]) == 0
    assert "[Service]" in capsys.readouterr().out


def test_doctor_warns_about_foreign_python_packages_in_pythonpath(monkeypatch):
    ros = "/opt/ros/humble/lib/python3.10/site-packages:/opt/ros/humble/local/lib/python3.10/dist-packages"
    assert doctor.foreign_pythonpath_entries(f"{ros}:/home/x/eigener_code") == ros.split(":")
    assert doctor.foreign_pythonpath_entries("") == []
    monkeypatch.setenv("PYTHONPATH", ros)
    (result,) = doctor.check_pythonpath()
    assert result.status == doctor.WARN and "unset PYTHONPATH" in result.hint
    monkeypatch.delenv("PYTHONPATH")
    (result,) = doctor.check_pythonpath()
    assert result.status == doctor.OK
