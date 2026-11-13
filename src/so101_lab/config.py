"""Lädt config/arms.toml.

Jeder [[device]]-Block beschreibt ein Gerät. Fehler werden auf Deutsch und mit dem
Namen des Geräts gemeldet, damit Studierende die Stelle in der Datei finden.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .devices import DEVICE_TYPES
from .paths import default_config_path

BACKENDS = ("usb", "remote", "esp32", "mock")
SAFE_STATES = ("torque_off", "hold")

# Grenzen der Servo-Register (Feetech V3.7): Goal_Velocity höchstens 3400 Schritt/s,
# Acceleration 0…254 in 100 Schritt/s².
MAX_VELOCITY_LIMIT = 3400
MAX_ACCELERATION_LIMIT = 254


class ConfigError(ValueError):
    pass


@dataclass
class DeviceConfig:
    name: str
    type: str
    backend: str
    calibration_id: str
    port: str | None = None  # usb: kann leer bleiben, dann wählt der Nutzer den Port
    address: str | None = None  # remote/esp32
    token_env: str | None = None  # remote: Name der Umgebungsvariable mit dem Token
    calibration_dir: Path | None = None
    cameras: list[dict[str, Any]] = field(default_factory=list)
    # Was beim Beenden oder bei einem Fehler mit dem Arm passiert:
    #   torque_off  Drehmoment aus (wie LeRobot). Der Arm sinkt unter seinem Gewicht ab.
    #   hold        Stellung halten, Drehmoment bleibt an.
    # Räder werden in beiden Fällen gestoppt.
    safe_state: str = "torque_off"
    max_velocity: int = 1000  # Schritt/s für Gelenkbewegungen (4096 Schritt = 360°)
    acceleration: int = 20  # Einheit 100 Schritt/s², also 2000 Schritt/s²
    max_wheel_velocity: int = 3000  # Schritt/s
    max_relative_target: int | None = 400  # größter Sprung je Sollwert in Schritt, None = aus


@dataclass
class LabConfig:
    path: Path | None
    devices: dict[str, DeviceConfig]

    def device(self, name: str) -> DeviceConfig:
        try:
            return self.devices[name]
        except KeyError:
            known = ", ".join(self.devices) or "keine"
            raise ConfigError(
                f"Gerät '{name}' steht nicht in der Konfiguration (vorhanden: {known})."
            ) from None


_ALLOWED_KEYS = set(DeviceConfig.__dataclass_fields__)


def _parse_device(raw: dict[str, Any], index: int) -> DeviceConfig:
    where = f"[[device]] Nr. {index + 1}" + (f" ('{raw['name']}')" if "name" in raw else "")
    if unknown := set(raw) - _ALLOWED_KEYS:
        raise ConfigError(f"{where}: unbekannte Schlüssel {sorted(unknown)}.")
    for key in ("name", "type", "backend"):
        if key not in raw:
            raise ConfigError(f"{where}: Schlüssel '{key}' fehlt.")
    if raw["type"] not in DEVICE_TYPES:
        raise ConfigError(f"{where}: type '{raw['type']}' unbekannt, erlaubt: {', '.join(DEVICE_TYPES)}.")
    if raw["backend"] not in BACKENDS:
        raise ConfigError(f"{where}: backend '{raw['backend']}' unbekannt, erlaubt: {', '.join(BACKENDS)}.")
    if raw["backend"] in ("remote", "esp32") and not raw.get("address"):
        raise ConfigError(f"{where}: backend '{raw['backend']}' braucht 'address'.")
    data = dict(raw)
    data.setdefault("calibration_id", raw["name"])
    if "calibration_dir" in data:
        data["calibration_dir"] = Path(data["calibration_dir"]).expanduser()
    cfg = DeviceConfig(**data)
    if cfg.safe_state not in SAFE_STATES:
        raise ConfigError(
            f"{where}: safe_state '{cfg.safe_state}' unbekannt, erlaubt: {', '.join(SAFE_STATES)}."
        )
    if not 1 <= cfg.max_velocity <= MAX_VELOCITY_LIMIT:
        raise ConfigError(f"{where}: max_velocity muss zwischen 1 und {MAX_VELOCITY_LIMIT} liegen.")
    if not 1 <= cfg.acceleration <= MAX_ACCELERATION_LIMIT:
        raise ConfigError(f"{where}: acceleration muss zwischen 1 und {MAX_ACCELERATION_LIMIT} liegen.")
    if not 0 <= cfg.max_wheel_velocity <= MAX_VELOCITY_LIMIT:
        raise ConfigError(f"{where}: max_wheel_velocity muss zwischen 0 und {MAX_VELOCITY_LIMIT} liegen.")
    if cfg.max_relative_target is not None and cfg.max_relative_target <= 0:
        raise ConfigError(f"{where}: max_relative_target muss positiv sein (oder fehlen).")
    return cfg


def parse_config(data: dict[str, Any], path: Path | None = None) -> LabConfig:
    devices: dict[str, DeviceConfig] = {}
    raw_devices = data.get("device", [])
    if not isinstance(raw_devices, list):
        raise ConfigError("'device' muss eine Liste von [[device]]-Blöcken sein.")
    for i, raw in enumerate(raw_devices):
        cfg = _parse_device(raw, i)
        if cfg.name in devices:
            raise ConfigError(f"Gerätename '{cfg.name}' kommt doppelt vor.")
        devices[cfg.name] = cfg
    return LabConfig(path=path, devices=devices)


def load_config(path: str | Path | None = None) -> LabConfig:
    path = Path(path) if path else default_config_path()
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise ConfigError(f"Konfigurationsdatei {path} nicht gefunden.") from None
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{path} ist kein gültiges TOML: {exc}") from None
    return parse_config(data, path)
