"""Kalibrierdateien verwalten: Liste, Prüfen, Löschen mit Backup, Wiederherstellen, Import, Web-API."""

import json

import pytest
from fastapi.testclient import TestClient

from so101_lab import calibration_files as files
from so101_lab.calibration import MotorCalibration, save_calibration
from so101_lab.calibration_files import CalibrationFileError, CalRef, validate
from so101_lab.devices import get_device_spec
from so101_lab.web.app import LabServer, create_app

SPEC = get_device_spec("so101_follower")


def good(offset=0):
    return {
        j.name: {
            "id": j.motor_id,
            "drive_mode": 0,
            "homing_offset": offset,
            "range_min": 0 if j.full_turn else 1000,
            "range_max": 4095 if j.full_turn else 3000,
        }
        for j in SPEC.joints
    }


@pytest.fixture
def root(tmp_path, monkeypatch):
    monkeypatch.setenv("SO101_LAB_ROOT", str(tmp_path))
    monkeypatch.setenv("HF_LEROBOT_CALIBRATION", str(tmp_path / "lerobot"))
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "arms.toml").write_text(
        '[[device]]\nname = "arm"\ntype = "so101_follower"\nbackend = "mock"\ncalibration_id = "f1"\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("SO101_LAB_CONFIG", str(tmp_path / "config" / "arms.toml"))
    return tmp_path


def write(ref, data):
    ref.path.parent.mkdir(parents=True, exist_ok=True)
    ref.path.write_text(json.dumps(data), encoding="utf-8")


def test_refs_are_checked():
    with pytest.raises(CalibrationFileError):
        CalRef("repo", "robots", "so_follower", "../../etc/passwd")
    with pytest.raises(CalibrationFileError):
        CalRef("repo", "..", "so_follower", "x")
    with pytest.raises(CalibrationFileError):
        CalRef("anderswo", "robots", "so_follower", "x")
    assert CalRef("repo", "teleoperators", "so_leader", "leader_1").spec.type == "so101_leader"


def test_validate():
    cal, problems = validate(good(), SPEC)
    assert problems == [] and cal["gripper"].id == 6
    bad = good()
    bad["elbow_flex"]["range_min"] = 3500
    bad["gripper"]["id"] = 9
    del bad["shoulder_pan"]
    _, problems = validate(bad, SPEC)
    assert any("Es fehlen" in p for p in problems)
    assert any("elbow_flex: Bereich" in p for p in problems)
    assert any("gripper: ID 9" in p for p in problems)
    with pytest.raises(CalibrationFileError):
        validate({"x": {"id": 1}}, SPEC)
    with pytest.raises(CalibrationFileError):
        validate([], SPEC)


def test_list_delete_restore(root):
    ref = CalRef("repo", "robots", "so_follower", "f1")
    write(ref, good())
    [item] = files.list_files()
    assert item["exists"] and item["joints"] == 6 and not item["has_backup"] and item["problems"] == []
    files.delete(ref)
    assert not ref.path.exists() and ref.backup.exists()
    [item] = files.list_files()
    assert not item["exists"] and item["has_backup"]  # gelöscht, aber wiederherstellbar
    files.restore(ref)
    assert ref.path.exists() and not ref.backup.exists()
    # Wiederherstellen mit vorhandener Datei tauscht beide
    ref.backup.write_text(json.dumps(good(offset=5)), encoding="utf-8")
    files.restore(ref)
    assert json.loads(ref.path.read_text())["gripper"]["homing_offset"] == 5
    assert json.loads(ref.backup.read_text())["gripper"]["homing_offset"] == 0


def test_import_and_copy_from_lerobot(root):
    ref = CalRef("repo", "robots", "so_follower", "neu")
    files.save_new(ref, good())
    with pytest.raises(CalibrationFileError, match="gibt es schon"):
        files.save_new(ref, good(offset=3))
    files.save_new(ref, good(offset=3), overwrite=True)
    assert ref.backup.exists()
    bad = good()
    bad["gripper"]["range_max"] = 5000
    with pytest.raises(CalibrationFileError, match="Nicht übernommen"):
        files.save_new(CalRef("repo", "robots", "so_follower", "kaputt"), bad)
    lr = CalRef("lerobot", "robots", "so_follower", "aus_lerobot")
    write(lr, good())
    with pytest.raises(CalibrationFileError, match="nur gelesen"):
        files.delete(lr)
    path = files.copy_to_repo(lr)
    assert path == CalRef("repo", "robots", "so_follower", "aus_lerobot").path and path.exists()


# ------------------------------------------------------------------ Web


def test_web_manage_and_apply(root):
    ref = CalRef("repo", "robots", "so_follower", "f1")
    save_calibration(
        ref.path,
        {n: MotorCalibration(**v) for n, v in good(offset=100).items()},
    )
    server = LabServer(watchdog_ms=2000, poll_hz=50)
    client = TestClient(create_app(server))
    with client, client.websocket_connect("/ws") as ws:
        me = ws.receive_json()["client_id"]
        client.post("/api/devices/arm/connect", json={})
        session = server.sessions["arm"]
        assert session.arm.calibration_path == ref.path

        items = client.get("/api/calibrations").json()
        assert items[0]["used_by"] == ["arm"] and items[0]["device_type"] == "so101_follower"
        detail = client.get("/api/calibrations/repo/robots/so_follower/f1").json()
        assert detail["joints"][0]["homing_offset"] == 100
        assert client.get("/api/calibrations/repo/robots/so_follower/f1/json").json()["gripper"]["id"] == 6
        assert client.get("/api/calibrations/repo/robots/so_follower/..%2Fx").status_code in (400, 404)

        # Servos (Mock: Offset 0, Grenzen 0…4095) passen nicht zur Datei
        cmp = client.get("/api/devices/arm/calibration/compare").json()
        assert not cmp["all_match"] and cmp["joints"][0]["servo"]["homing_offset"] == 0
        r = client.post("/api/devices/arm/calibration/apply", json={"client_id": me})
        assert r.status_code == 409  # braucht das Steuerrecht
        client.post("/api/devices/arm/control", json={"client_id": me})
        r = client.post("/api/devices/arm/calibration/apply", json={"client_id": me}).json()
        assert r["all_match"] and "gripper.homing_offset=100" in r["written"]
        assert session.arm.backend.servo(6).persisted["homing_offset"] == 100  # dauerhaft im EEPROM

        # Löschen: Gerät lädt neu und ist danach ohne Kalibrierung
        r = client.post("/api/calibrations/repo/robots/so_follower/f1/delete", json={"client_id": me})
        assert r.status_code == 200 and session.arm.calibration is None
        assert client.get("/api/devices/arm/state").json()["calibration_path"] is None
        client.post("/api/calibrations/repo/robots/so_follower/f1/restore", json={"client_id": me})
        assert session.arm.calibration_path == ref.path

        # Import
        r = client.post(
            "/api/calibrations/import",
            json={"client_id": me, "device_type": "so101_leader", "cal_id": "leader_1", "content": good()},
        )
        assert r.status_code == 200 and r.json()["path"].endswith("teleoperators/so_leader/leader_1.json")
        r = client.post(
            "/api/calibrations/import",
            json={"client_id": me, "device_type": "so101_leader", "cal_id": "leader_1", "content": {"x": 1}},
        )
        assert r.status_code == 400
        # ohne Live-Verbindung: kein Löschen
        r = client.post("/api/calibrations/repo/robots/so_follower/f1/delete", json={"client_id": "erfunden"})
        assert r.status_code == 409
