"""Aufgezeichnete Bewegungen (Trajektorien) speichern und laden.

Eine Aufnahme besteht aus zwei Dateien in `recordings/trajectories/`:

    <name>.csv    Zeit und Rohposition je Gelenk, eine Zeile je Abtastung
    <name>.json   Beschreibung: Gerät, Kalibrierung, Abtastrate, Gelenke, Format

CSV (Rohwerte in Servo-Schritten, 4096 Schritt = 360°):

    t_s,shoulder_pan,shoulder_lift,...
    0.000,2048,2051,...
    0.033,2049,2051,...

Rohwerte statt Grad, weil sie ohne Kalibrierung aufgenommen werden können und beim
Abspielen exakt dieselben Sollwerte ergeben. Die Kalibrierung steht im JSON, damit man
sie später zuordnen kann. Die Phase 6 baut Bibliothek und Web-Recorder auf diesem Format auf.
"""

from __future__ import annotations

import csv
import json
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .paths import project_root

FORMAT_VERSION = 1
_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")


class TrajectoryError(ValueError):
    """Aufnahme ungültig oder nicht lesbar. Text für Nutzer auf Deutsch."""


def default_directory() -> Path:
    return project_root() / "recordings" / "trajectories"


@dataclass
class Trajectory:
    name: str
    joints: tuple[str, ...]
    times: list[float]  # Sekunden seit Aufnahmebeginn
    positions: list[tuple[int, ...]]  # Rohwerte, Reihenfolge wie `joints`
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def duration_s(self) -> float:
        return self.times[-1] if self.times else 0.0

    @property
    def sample_rate_hz(self) -> float | None:
        """Gemessene mittlere Abtastrate."""
        if len(self.times) < 2 or self.duration_s <= 0:
            return None
        return (len(self.times) - 1) / self.duration_s

    def validate(self) -> None:
        if not _NAME.match(self.name):
            raise TrajectoryError(
                f"Name '{self.name}' ungültig. Erlaubt sind Buchstaben, Ziffern, '_' und '-'."
            )
        if not self.joints:
            raise TrajectoryError("Die Aufnahme enthält keine Gelenke.")
        if len(self.times) < 2:
            raise TrajectoryError("Eine Aufnahme braucht mindestens zwei Abtastungen.")
        if len(self.times) != len(self.positions):
            raise TrajectoryError("Zeiten und Positionen haben unterschiedlich viele Einträge.")
        if any(len(row) != len(self.joints) for row in self.positions):
            raise TrajectoryError("Eine Zeile hat nicht für jedes Gelenk einen Wert.")
        if any(b < a for a, b in zip(self.times, self.times[1:], strict=False)):
            raise TrajectoryError("Die Zeitstempel laufen rückwärts.")


def save_trajectory(
    traj: Trajectory, directory: Path | None = None, overwrite: bool = False
) -> tuple[Path, Path]:
    """Schreibt `<name>.csv` und `<name>.json`. Überschreibt nur mit `overwrite=True`."""
    traj.validate()
    directory = directory or default_directory()
    directory.mkdir(parents=True, exist_ok=True)
    csv_path, json_path = directory / f"{traj.name}.csv", directory / f"{traj.name}.json"
    if not overwrite and (csv_path.exists() or json_path.exists()):
        raise TrajectoryError(f"'{traj.name}' gibt es schon in {directory}. Anderen Namen wählen.")
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["t_s", *traj.joints])
        for t, row in zip(traj.times, traj.positions, strict=True):
            writer.writerow([f"{t:.4f}", *row])
    description = {
        "format_version": FORMAT_VERSION,
        "name": traj.name,
        "created": datetime.now(UTC).isoformat(timespec="seconds"),
        "unit": "raw_steps",
        "joints": list(traj.joints),
        "samples": len(traj.times),
        "duration_s": round(traj.duration_s, 4),
        "measured_rate_hz": round(traj.sample_rate_hz, 2) if traj.sample_rate_hz else None,
        **traj.meta,
    }
    json_path.write_text(json.dumps(description, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return csv_path, json_path


def load_trajectory(name_or_path: str | Path, directory: Path | None = None) -> Trajectory:
    """Lädt eine Aufnahme per Name (aus `recordings/trajectories/`) oder per Pfad zur CSV-Datei."""
    path = Path(name_or_path)
    if path.suffix != ".csv":
        path = (directory or default_directory()) / f"{path.name}.csv"
    if not path.exists():
        raise TrajectoryError(f"Aufnahme {path} nicht gefunden.")
    meta: dict[str, Any] = {}
    json_path = path.with_suffix(".json")
    if json_path.exists():
        meta = json.loads(json_path.read_text(encoding="utf-8"))
    with path.open(newline="", encoding="utf-8") as f:
        rows = list(csv.reader(f))
    if not rows or rows[0][:1] != ["t_s"]:
        raise TrajectoryError(f"{path.name}: Die erste Spalte muss 't_s' heißen.")
    joints = tuple(rows[0][1:])
    try:
        times = [float(r[0]) for r in rows[1:]]
        positions = [tuple(int(v) for v in r[1:]) for r in rows[1:]]
    except ValueError as exc:
        raise TrajectoryError(f"{path.name}: Zahl nicht lesbar ({exc}).") from exc
    traj = Trajectory(path.stem, joints, times, positions, meta)
    traj.validate()
    return traj
