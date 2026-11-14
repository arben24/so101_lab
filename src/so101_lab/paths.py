"""Wichtige Ordner des Repos."""

from __future__ import annotations

import os
from pathlib import Path


def project_root() -> Path:
    """Wurzel des Repos: Umgebungsvariable SO101_LAB_ROOT, sonst aktueller Ordner (falls er
    config/arms.toml enthält), sonst der Ordner über src/."""
    if env := os.environ.get("SO101_LAB_ROOT"):
        return Path(env).expanduser()
    cwd = Path.cwd()
    if (cwd / "config" / "arms.toml").exists():
        return cwd
    return Path(__file__).resolve().parents[2]


def default_config_path() -> Path:
    if env := os.environ.get("SO101_LAB_CONFIG"):
        return Path(env).expanduser()
    return project_root() / "config" / "arms.toml"


def calibration_root() -> Path:
    return project_root() / "calibration"


def servo_setup_log_path() -> Path:
    """Protokoll der ID-Vergabe (eine CSV-Zeile je Schritt), liegt mit im Repo."""
    return project_root() / "logs" / "servo_ids.csv"
