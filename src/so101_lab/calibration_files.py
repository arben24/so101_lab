"""Kalibrierdateien verwalten (Reiter „Kalibrierdateien“ der Web-App).

Zwei Ablagen, beide im LeRobot-Format und mit LeRobots Ordnerstruktur
`<robots|teleoperators>/<so_follower|so_leader|lekiwi>/<id>.json`:

- **repo**: `calibration/` im Repo. Hier wird gelöscht, wiederhergestellt und importiert.
- **lerobot**: LeRobots eigene Ablage (`$HF_LEROBOT_CALIBRATION`, Standard
  `~/.cache/huggingface/lerobot/calibration`), z. B. von `lerobot-calibrate`. Nur lesen;
  eine Datei lässt sich ins Repo kopieren.

Gelöscht wird nie endgültig: Die Datei wird zu `<id>.json.bak` (eine ältere `.bak` wird dabei
ersetzt). „Wiederherstellen“ tauscht Datei und Backup. So bleibt ein versehentliches Löschen
oder Überschreiben (auch durch eine neue Kalibrierung) umkehrbar.

Namen kommen aus dem Browser. Deshalb wird jeder Teil des Pfads gegen feste Listen bzw. ein
enges Muster geprüft (`CalRef`), ein Pfad außerhalb der Ablagen ist nicht möglich.
"""

from __future__ import annotations

import json
import os
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .calibration import (
    Calibration,
    MotorCalibration,
    find_calibration_file,
    lerobot_calibration_root,
    save_calibration,
)
from .config import LabConfig
from .devices import DEVICE_TYPES, get_device_spec
from .devices.spec import DeviceSpec
from .paths import calibration_root

_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
FIELDS = ("id", "drive_mode", "homing_offset", "range_min", "range_max")
MAX_OFFSET = 2047


class CalibrationFileError(ValueError):
    """Text für Nutzer auf Deutsch."""


def _roots() -> dict[str, Path]:
    return {"repo": calibration_root(), "lerobot": lerobot_calibration_root()}


def folders() -> dict[tuple[str, str], DeviceSpec]:
    """(kind, name) → Gerätetyp, z. B. ("robots", "so_follower") → so101_follower."""
    result: dict[tuple[str, str], DeviceSpec] = {}
    for device_type in DEVICE_TYPES:
        spec = get_device_spec(device_type)
        result.setdefault((spec.lerobot_kind, spec.lerobot_name), spec)
    return result


@dataclass(frozen=True)
class CalRef:
    source: str  # repo | lerobot
    kind: str  # robots | teleoperators
    name: str  # so_follower | so_leader | lekiwi
    cal_id: str

    def __post_init__(self) -> None:
        if self.source not in ("repo", "lerobot"):
            raise CalibrationFileError(f"Unbekannte Ablage '{self.source}'.")
        if (self.kind, self.name) not in folders():
            raise CalibrationFileError(f"Unbekannter Ordner '{self.kind}/{self.name}'.")
        if not _ID.match(self.cal_id) or self.cal_id.endswith((".json", ".bak")):
            raise CalibrationFileError(
                f"Kalibrier-ID '{self.cal_id}' ungültig. Erlaubt: Buchstaben, Ziffern, '_', '-', '.'."
            )

    @property
    def spec(self) -> DeviceSpec:
        return folders()[(self.kind, self.name)]

    @property
    def path(self) -> Path:
        return _roots()[self.source] / self.kind / self.name / f"{self.cal_id}.json"

    @property
    def backup(self) -> Path:
        return self.path.with_suffix(".json.bak")

    def require_repo(self) -> None:
        if self.source != "repo":
            raise CalibrationFileError(
                "Dateien in LeRobots Ablage werden hier nur gelesen. Erst ins Repo kopieren."
            )


# ------------------------------------------------------------------ Prüfen


def validate(data: Any, spec: DeviceSpec) -> tuple[Calibration, list[str]]:
    """Inhalt prüfen. Liefert die Kalibrierung und eine Liste von Auffälligkeiten.

    Wirft CalibrationFileError, wenn der Inhalt gar nicht als Kalibrierung lesbar ist.
    Auffälligkeiten (fehlende Gelenke, Werte außerhalb 0…4095, falsche IDs …) verhindern das
    Laden nicht, werden aber angezeigt und beim Import abgelehnt.
    """
    if not isinstance(data, dict) or not data:
        raise CalibrationFileError(
            "Keine Kalibrierung: erwartet wird ein JSON-Objekt mit einem Eintrag je Gelenk."
        )
    calibration: Calibration = {}
    problems: list[str] = []
    for name, entry in data.items():
        if not isinstance(entry, dict) or set(entry) != set(FIELDS):
            raise CalibrationFileError(f"Eintrag '{name}' braucht genau die Felder {', '.join(FIELDS)}.")
        try:
            values = {k: int(entry[k]) for k in FIELDS}
        except (TypeError, ValueError):
            raise CalibrationFileError(f"Eintrag '{name}': alle Werte müssen ganze Zahlen sein.") from None
        if any(isinstance(entry[k], float) and not float(entry[k]).is_integer() for k in FIELDS):
            raise CalibrationFileError(f"Eintrag '{name}': alle Werte müssen ganze Zahlen sein.")
        calibration[str(name)] = MotorCalibration(**values)
    expected = {j.name: j for j in spec.joints}
    missing = [n for n in expected if n not in calibration]
    unknown = [n for n in calibration if n not in expected]
    if missing:
        problems.append(f"Es fehlen Gelenke: {', '.join(missing)}.")
    if unknown:
        problems.append(f"Unbekannte Gelenke für {spec.type}: {', '.join(unknown)}.")
    for name, c in calibration.items():
        joint = expected.get(name)
        if joint is not None and c.id != joint.motor_id:
            problems.append(f"{name}: ID {c.id}, laut Gelenktabelle {joint.motor_id}.")
        if not (0 <= c.range_min < c.range_max <= 4095):
            problems.append(f"{name}: Bereich {c.range_min}…{c.range_max} ungültig (0 ≤ min < max ≤ 4095).")
        if abs(c.homing_offset) > MAX_OFFSET:
            problems.append(f"{name}: Homing-Offset {c.homing_offset} außerhalb ±{MAX_OFFSET}.")
        if c.drive_mode not in (0, 1):
            problems.append(f"{name}: drive_mode {c.drive_mode} (erlaubt 0 oder 1).")
    return calibration, problems


def _load(path: Path, spec: DeviceSpec) -> tuple[Calibration | None, list[str], Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return None, [f"Datei nicht lesbar: {exc}"], None
    try:
        calibration, problems = validate(data, spec)
    except CalibrationFileError as exc:
        return None, [str(exc)], data
    return calibration, problems, data


# ------------------------------------------------------------------ Auflisten und Lesen


def users(ref: CalRef, config: LabConfig | None) -> list[str]:
    """Geräte aus arms.toml, die genau diese Datei laden würden."""
    if config is None:
        return []
    names = []
    for cfg in config.devices.values():
        spec = get_device_spec(cfg.type)
        if (spec.lerobot_kind, spec.lerobot_name) != (ref.kind, ref.name) or cfg.calibration_id != ref.cal_id:
            continue
        found = find_calibration_file(spec, cfg.calibration_id, cfg.calibration_dir)
        if found is not None and found.resolve() == ref.path.resolve():
            names.append(cfg.name)
    return names


def list_files(config: LabConfig | None = None) -> list[dict[str, Any]]:
    """Alle Kalibrierdateien beider Ablagen, dazu gelöschte (nur noch als Backup vorhanden)."""
    items = []
    for source, root in _roots().items():
        for (kind, name), spec in folders().items():
            folder = root / kind / name
            if not folder.is_dir():
                continue
            ids = {p.name.removesuffix(".json") for p in folder.glob("*.json")}
            ids |= {p.name.removesuffix(".json.bak") for p in folder.glob("*.json.bak")}
            for cal_id in sorted(ids):
                try:
                    ref = CalRef(source, kind, name, cal_id)
                except CalibrationFileError:
                    continue
                exists = ref.path.is_file()
                calibration, problems, _ = _load(ref.path, spec) if exists else (None, [], None)
                items.append(
                    {
                        "source": source,
                        "kind": kind,
                        "name": name,
                        "id": cal_id,
                        "device_type": spec.type,
                        "path": str(ref.path),
                        "exists": exists,
                        "has_backup": ref.backup.is_file(),
                        "modified": ref.path.stat().st_mtime if exists else ref.backup.stat().st_mtime,
                        "joints": len(calibration) if calibration else 0,
                        "problems": problems,
                        "used_by": users(ref, config) if exists else [],
                    }
                )
    return items


def read_file(ref: CalRef, config: LabConfig | None = None, backup: bool = False) -> dict[str, Any]:
    path = ref.backup if backup else ref.path
    if not path.is_file():
        raise CalibrationFileError(f"{path} gibt es nicht.")
    calibration, problems, data = _load(path, ref.spec)
    return {
        "source": ref.source,
        "kind": ref.kind,
        "name": ref.name,
        "id": ref.cal_id,
        "device_type": ref.spec.type,
        "path": str(path),
        "backup": backup,
        "has_backup": ref.backup.is_file(),
        "problems": problems,
        "used_by": users(ref, config),
        "joints": [{"name": n, **{k: getattr(c, k) for k in FIELDS}} for n, c in (calibration or {}).items()],
        "raw": data if calibration is None else None,
    }


# ------------------------------------------------------------------ Ändern (nur Repo)


def delete(ref: CalRef) -> Path:
    """Datei zu `.json.bak` machen (ein älteres Backup wird ersetzt). Liefert den Backup-Pfad."""
    ref.require_repo()
    if not ref.path.is_file():
        raise CalibrationFileError(f"{ref.path} gibt es nicht.")
    os.replace(ref.path, ref.backup)
    return ref.backup


def restore(ref: CalRef) -> Path:
    """Backup zurückholen. Gibt es die Datei noch, tauschen Datei und Backup die Plätze."""
    ref.require_repo()
    if not ref.backup.is_file():
        raise CalibrationFileError(f"Für '{ref.cal_id}' gibt es kein Backup.")
    if ref.path.is_file():
        swap = ref.path.with_suffix(".json.tausch")
        os.replace(ref.path, swap)
        os.replace(ref.backup, ref.path)
        os.replace(swap, ref.backup)
    else:
        os.replace(ref.backup, ref.path)
    return ref.path


def save_new(ref: CalRef, data: Any, overwrite: bool = False) -> Path:
    """Inhalt prüfen und ins Repo schreiben (Import). Eine vorhandene Datei wird zum Backup."""
    ref.require_repo()
    calibration, problems = validate(data, ref.spec)
    if problems:
        raise CalibrationFileError("Nicht übernommen: " + " ".join(problems))
    if ref.path.exists():
        if not overwrite:
            raise CalibrationFileError(f"'{ref.cal_id}' gibt es schon. „Vorhandene ersetzen“ anhaken.")
        shutil.copy2(ref.path, ref.backup)
    save_calibration(ref.path, calibration)
    return ref.path


def copy_to_repo(ref: CalRef, overwrite: bool = False) -> Path:
    """Datei aus LeRobots Ablage ins Repo kopieren."""
    if ref.source != "lerobot":
        raise CalibrationFileError("Kopieren geht nur aus LeRobots Ablage ins Repo.")
    data = json.loads(ref.path.read_text(encoding="utf-8"))
    return save_new(CalRef("repo", ref.kind, ref.name, ref.cal_id), data, overwrite=overwrite)
