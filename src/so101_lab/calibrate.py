"""Kalibrierung Schritt für Schritt (für die Web-App), gleiches Ergebnis wie `lerobot-calibrate`.

Ablauf (wie `SerialMotorsBus.record_calibration` in LeRobot 0.7.0):

1. **Start:** Drehmoment aus, die bisherigen EEPROM-Werte (Homing-Offset, Grenzen) merken.
   Noch wird nichts geschrieben.
2. **Mitte:** Alle Gelenke stehen von Hand in der Mitte ihres Bewegungsbereichs.
   Kalibrierung zurücksetzen (Offset 0, Grenzen 0…4095), Rohposition lesen und den Offset so
   schreiben, dass die Mitte 2047 ergibt: `homing_offset = Rohposition − 2047` (Feetech zieht
   den Offset ab), begrenzt auf ±2047.
3. **Bereich:** Jedes Gelenk von Hand bis zu beiden Anschlägen bewegen. Kleinster und größter
   Wert werden mitgeschrieben (`track`). Volldreher (`wrist_roll`) und Räder bekommen 0…4095.
4. **Speichern:** Grenzen ins EEPROM schreiben, Datei im LeRobot-Format speichern.

**Abbrechen** (auch durch Watchdog, Not-Aus oder geschlossenen Tab) schreibt die gemerkten
alten Werte zurück, damit Servos und alte Kalibrierdatei wieder zusammenpassen.

Jedes EEPROM-Schreiben läuft über `write_eeprom` (Drehmoment aus, Sperre auf, schreiben,
Sperre zu, zurücklesen).
"""

from __future__ import annotations

import logging
import shutil
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

from .arm import Arm
from .backends.base import BackendError
from .calibration import Calibration, MotorCalibration, calibration_dir, save_calibration
from .devices.spec import JointMode

logger = logging.getLogger(__name__)

HALF_TURN = 2047  # (4096 − 1) // 2, wie LeRobot
MAX_OFFSET = 2047  # Betrag + Vorzeichenbit 11
FULL_RANGE = (0, 4095)
CAL_REGISTERS = ("homing_offset", "min_position_limit", "max_position_limit")


def target_path(arm: Arm) -> Path:
    """Wohin die Kalibrierdatei geschrieben wird: calibration/<robots|teleoperators>/<name>/<id>.json."""
    return calibration_dir(arm.spec, arm.cfg.calibration_dir) / f"{arm.cfg.calibration_id}.json"


class Stage(StrEnum):
    MIDDLE = "mitte"  # wartet auf „Mitte übernehmen“
    RANGE = "bereich"  # zeichnet Minimum und Maximum auf
    DONE = "fertig"
    CANCELLED = "abgebrochen"


@dataclass
class CalibrationRun:
    arm: Arm
    stage: Stage = Stage.MIDDLE
    backup: dict[str, dict[str, int]] = field(default_factory=dict)
    offsets: dict[str, int] = field(default_factory=dict)
    mins: dict[str, int] = field(default_factory=dict)
    maxes: dict[str, int] = field(default_factory=dict)
    positions: dict[str, int] = field(default_factory=dict)
    written: bool = False  # wurde schon etwas ins EEPROM geschrieben?
    saved_path: Path | None = None

    # ------------------------------------------------------------ Hilfen

    @property
    def joints(self) -> list[str]:
        return [j.name for j in self.arm.joints]

    @property
    def recorded(self) -> list[str]:
        """Gelenke, deren Bereich von Hand abgefahren wird (keine Volldreher, keine Räder)."""
        return [j.name for j in self.arm.spec.position_joints if not j.full_turn]

    @property
    def target_path(self) -> Path:
        return target_path(self.arm)

    def _id(self, name: str) -> int:
        return self.arm.spec.joint(name).motor_id

    def _write(self, name: str, register: str, value: int) -> None:
        """EEPROM schreiben, aber nur wenn sich der Wert ändert (schont das EEPROM)."""
        servo = self._id(name)
        if self.arm.backend.read_register(servo, register) == value:
            return
        self.written = True
        self.arm.backend.write_eeprom(servo, register, value)

    # ------------------------------------------------------------ Schritte

    @classmethod
    def start(cls, arm: Arm) -> CalibrationRun:
        """Schritt 1: prüfen, Drehmoment aus, alte Werte merken. Schreibt nichts ins EEPROM."""
        wrong = arm.check_operating_modes()
        if wrong:
            raise BackendError(
                "Betriebsart passt nicht zur Gelenktabelle: "
                + ", ".join(wrong)
                + ". Erst die Betriebsart korrigieren (Register-Explorer, operating_mode)."
            )
        arm.disable_torque()
        run = cls(arm)
        for name in run.joints:
            run.backup[name] = {r: arm.backend.read_register(run._id(name), r) for r in CAL_REGISTERS}
        logger.info("Kalibrierung gestartet für %s", arm.cfg.name)
        return run

    def set_middle(self) -> dict[str, int]:
        """Schritt 2: aktuelle Stellung als Mitte übernehmen. Liefert die geschriebenen Offsets."""
        self._expect(Stage.MIDDLE)
        # Zurücksetzen: Offset 0 und volle Grenzen, damit wir die Rohposition lesen.
        for name in self.joints:
            self._write(name, "homing_offset", 0)
            self._write(name, "min_position_limit", FULL_RANGE[0])
            self._write(name, "max_position_limit", FULL_RANGE[1])
        raw = self.arm.read_positions()
        for joint in self.arm.joints:
            if joint.mode is JointMode.VELOCITY:
                self.offsets[joint.name] = 0  # Räder: kein Homing (wie LeRobot)
                continue
            offset = max(-MAX_OFFSET, min(MAX_OFFSET, raw[joint.name] - HALF_TURN))
            self._write(joint.name, "homing_offset", offset)
            self.offsets[joint.name] = offset
        self.positions = self.arm.read_positions()
        self.mins = {n: self.positions[n] for n in self.recorded}
        self.maxes = dict(self.mins)
        self.stage = Stage.RANGE
        return dict(self.offsets)

    def track(self, positions: dict[str, int]) -> None:
        """Schritt 3: aufgerufen bei jeder Positionsabfrage, merkt Minimum und Maximum."""
        if self.stage is not Stage.RANGE:
            return
        self.positions = dict(positions)
        for name in self.recorded:
            if name in positions:
                self.mins[name] = min(self.mins[name], positions[name])
                self.maxes[name] = max(self.maxes[name], positions[name])

    def finish(self) -> Path:
        """Schritt 4: Grenzen schreiben, Datei speichern, Kalibrierung im Arm aktivieren."""
        self._expect(Stage.RANGE)
        unmoved = [n for n in self.recorded if self.mins[n] == self.maxes[n]]
        if unmoved:
            raise BackendError(
                f"Nicht bewegt: {', '.join(unmoved)}. Jedes Gelenk bis zu beiden Anschlägen führen."
            )
        calibration: Calibration = {}
        for joint in self.arm.joints:
            name = joint.name
            low, high = (self.mins[name], self.maxes[name]) if name in self.recorded else FULL_RANGE
            self._write(name, "min_position_limit", low)
            self._write(name, "max_position_limit", high)
            calibration[name] = MotorCalibration(
                id=joint.motor_id,
                drive_mode=0,
                homing_offset=self.offsets[name],
                range_min=low,
                range_max=high,
            )
        path = self.target_path
        if path.exists():
            shutil.copy2(path, path.with_suffix(".json.bak"))  # alte Datei aufheben
        save_calibration(path, calibration)
        self.arm.calibration = calibration
        self.arm.calibration_path = path
        self.saved_path = path
        self.stage = Stage.DONE
        logger.info("Kalibrierung gespeichert: %s", path)
        return path

    def cancel(self) -> None:
        """Abbrechen: bisherige EEPROM-Werte zurückschreiben (falls schon etwas geschrieben wurde)."""
        if self.stage in (Stage.DONE, Stage.CANCELLED):
            return
        self.stage = Stage.CANCELLED
        if not self.written:
            return
        errors = []
        for name, values in self.backup.items():
            for register in CAL_REGISTERS:
                try:
                    self._write(name, register, values[register])
                except BackendError as exc:
                    errors.append(f"{name}.{register}: {exc}")
        if errors:
            raise BackendError("Alte Kalibrierung nicht vollständig zurückgeschrieben: " + "; ".join(errors))

    def _expect(self, stage: Stage) -> None:
        if self.stage is not stage:
            raise BackendError(
                f"Falscher Schritt: Kalibrierung steht bei '{self.stage}', nötig ist '{stage}'."
            )

    def status(self) -> dict:
        """Für die Anzeige im Browser."""
        return {
            "stage": self.stage.value,
            "target_path": str(self.target_path),
            "target_exists": self.target_path.exists(),
            "saved_path": str(self.saved_path) if self.saved_path else None,
            "joints": [
                {
                    "name": j.name,
                    "recorded": j.name in self.recorded,
                    "offset": self.offsets.get(j.name),
                    "position": self.positions.get(j.name),
                    "min": self.mins.get(j.name),
                    "max": self.maxes.get(j.name),
                }
                for j in self.arm.joints
            ],
        }
