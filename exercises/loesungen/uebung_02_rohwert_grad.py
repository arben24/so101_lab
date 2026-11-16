"""Übung 2 – Rohwert ↔ Grad selbst rechnen (Musterlösung).

Aufruf:
    uv run exercises/loesungen/uebung_02_rohwert_grad.py
    uv run exercises/loesungen/uebung_02_rohwert_grad.py --kalibrierung follower_1

Keine Hardware nötig: Es wird nur die Kalibrierdatei gelesen.
"""

from __future__ import annotations

import argparse

from so101_lab.calibration import find_calibration_file, load_calibration, raw_to_joint
from so101_lab.devices import get_device_spec
from so101_lab.script_support import run_script

STEPS_PER_TURN = 4096  # ein Umlauf, aber LeRobot teilt durch 4095 (größter Rohwert), siehe Kapitel 03


def raw_to_degrees(raw: int, range_min: int, range_max: int) -> float:
    """0° ist die Mitte des kalibrierten Bereichs."""
    return (raw - (range_min + range_max) / 2) * 360 / (STEPS_PER_TURN - 1)


def degrees_to_raw(degrees: float, range_min: int, range_max: int) -> int:
    """Umkehrung von raw_to_degrees."""
    return int(degrees * (STEPS_PER_TURN - 1) / 360 + (range_min + range_max) / 2)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Rohwerte einer Kalibrierung in Grad umrechnen.")
    parser.add_argument("--kalibrierung", default="follower_1", help="Name der Kalibrierdatei (ohne .json)")
    parser.add_argument("--typ", default="so101_follower", help="Gerätetyp, bestimmt den Ordner")
    args = parser.parse_args(argv)

    spec = get_device_spec(args.typ)
    path = find_calibration_file(spec, args.kalibrierung)
    if path is None:
        print(f"Kalibrierdatei '{args.kalibrierung}' für {args.typ} nicht gefunden.")
        return 1
    calibration = load_calibration(path)
    print(f"Kalibrierung: {path}\n")
    print(f"{'Gelenk':<18}{'min':>6}{'max':>6}{'Mitte':>8}{'unten':>9}{'oben':>9}{'Bereich':>9}")
    worst = 0.0
    for joint in spec.position_joints:
        cal = calibration.get(joint.name)
        if cal is None or joint.norm.value != "degrees":
            continue  # Greifer rechnet in Prozent, nicht in Grad
        lo, hi = cal.range_min, cal.range_max
        low, high = raw_to_degrees(lo, lo, hi), raw_to_degrees(hi, lo, hi)
        print(
            f"{joint.name:<18}{lo:>6}{hi:>6}{(lo + hi) / 2:>8.0f}{low:>8.1f}°{high:>8.1f}°{high - low:>8.1f}°"
        )
        for raw in (lo, (lo + hi) // 2, hi):  # Vergleich mit der Funktion des Repos
            worst = max(worst, abs(raw_to_degrees(raw, lo, hi) - raw_to_joint(raw, joint, cal)))
            assert abs(degrees_to_raw(raw_to_degrees(raw, lo, hi), lo, hi) - raw) <= 1
    print(f"\nGrößte Abweichung zu so101_lab.calibration.raw_to_joint: {worst:.6f}°")
    return 0 if worst < 1e-6 else 1


if __name__ == "__main__":
    run_script(main)
