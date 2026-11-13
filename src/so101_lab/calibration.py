"""Kalibrierdateien im LeRobot-Format lesen und schreiben, Rohwerte umrechnen.

Format (eine JSON-Datei je Gerät, ein Eintrag je Motor), wie LeRobot 0.7.0 es schreibt:

    {"shoulder_pan": {"id": 1, "drive_mode": 0, "homing_offset": -123,
                      "range_min": 800, "range_max": 3200}, ...}

Ablage im Repo wie bei LeRobot: calibration/<robots|teleoperators>/<so_follower|so_leader|
lekiwi>/<calibration_id>.json. So lässt sich LeRobot mit
`--robot.calibration_dir=calibration/robots/so_follower` auf dieselbe Datei zeigen.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path

from .devices.spec import DeviceSpec, Joint, NormMode
from .paths import calibration_root

RESOLUTION = 4096  # Schritte je Umdrehung (STS3215)


@dataclass(frozen=True)
class MotorCalibration:
    id: int
    drive_mode: int
    homing_offset: int
    range_min: int
    range_max: int


Calibration = dict[str, MotorCalibration]


def lerobot_calibration_root() -> Path:
    """Ablage von LeRobot: $HF_LEROBOT_CALIBRATION, Standard ~/.cache/huggingface/lerobot/calibration."""
    if env := os.environ.get("HF_LEROBOT_CALIBRATION"):
        return Path(env).expanduser()
    hf_home = Path(os.environ.get("HF_HOME", Path.home() / ".cache" / "huggingface")).expanduser()
    lerobot_home = Path(os.environ.get("HF_LEROBOT_HOME", hf_home / "lerobot")).expanduser()
    return lerobot_home / "calibration"


def calibration_dir(spec: DeviceSpec, override: Path | None = None) -> Path:
    if override is not None:
        return override
    return calibration_root() / spec.lerobot_kind / spec.lerobot_name


def find_calibration_file(spec: DeviceSpec, calibration_id: str, override: Path | None = None) -> Path | None:
    """Erst im Repo (bzw. `override`) suchen, dann in der Ablage von LeRobot."""
    candidates = [calibration_dir(spec, override) / f"{calibration_id}.json"]
    if override is None:
        candidates.append(
            lerobot_calibration_root() / spec.lerobot_kind / spec.lerobot_name / f"{calibration_id}.json"
        )
    for path in candidates:
        if path.is_file():
            return path
    return None


def load_calibration(path: Path) -> Calibration:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return {name: MotorCalibration(**{k: int(v) for k, v in entry.items()}) for name, entry in data.items()}


def save_calibration(path: Path, calibration: Calibration) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {name: asdict(cal) for name, cal in calibration.items()}
    path.write_text(json.dumps(data, indent=4) + "\n", encoding="utf-8")


def raw_to_joint(raw: int, joint: Joint, cal: MotorCalibration) -> float:
    """Rohwert → Gelenkwert, rechnet wie LeRobot (SerialMotorsBus._normalize).

    Grad: 0° liegt in der Mitte des kalibrierten Bereichs. Greifer: 0…100 %.
    """
    lo, hi = cal.range_min, cal.range_max
    if hi == lo:
        raise ValueError(f"Kalibrierung von '{joint.name}' ungültig: range_min = range_max.")
    inverted = bool(cal.drive_mode)
    if joint.norm is NormMode.DEGREES:
        return (raw - (lo + hi) / 2) * 360 / (RESOLUTION - 1)
    bounded = min(hi, max(lo, raw))
    if joint.norm is NormMode.RANGE_0_100:
        value = (bounded - lo) / (hi - lo) * 100
        return 100 - value if inverted else value
    value = (bounded - lo) / (hi - lo) * 200 - 100
    return -value if inverted else value


def joint_to_raw(value: float, joint: Joint, cal: MotorCalibration) -> int:
    """Gelenkwert → Rohwert (Umkehrung von raw_to_joint, wie SerialMotorsBus._unnormalize)."""
    lo, hi = cal.range_min, cal.range_max
    if hi == lo:
        raise ValueError(f"Kalibrierung von '{joint.name}' ungültig: range_min = range_max.")
    inverted = bool(cal.drive_mode)
    if joint.norm is NormMode.DEGREES:
        return int(value * (RESOLUTION - 1) / 360 + (lo + hi) / 2)
    if joint.norm is NormMode.RANGE_0_100:
        value = 100 - value if inverted else value
        return int(min(100.0, max(0.0, value)) / 100 * (hi - lo) + lo)
    value = -value if inverted else value
    return int((min(100.0, max(-100.0, value)) + 100) / 200 * (hi - lo) + lo)
