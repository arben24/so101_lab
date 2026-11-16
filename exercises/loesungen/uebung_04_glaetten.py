"""Übung 4 – Eine Bewegung glätten (Musterlösung).

Aufruf:
    uv run exercises/loesungen/uebung_04_glaetten.py --beispiel               Beispielaufnahme erzeugen
    uv run exercises/loesungen/uebung_04_glaetten.py --name winken --fenster 7
    uv run exercises/loesungen/uebung_04_glaetten.py --name winken --ziel winken_glatt

Keine Hardware nötig. Die geglättete Aufnahme lässt sich danach mit Skript 07 abspielen.
"""

from __future__ import annotations

import argparse
import math
import random

from so101_lab.script_support import run_script
from so101_lab.trajectories import Trajectory, load_trajectory, save_trajectory


def moving_average(values: list[float], window: int) -> list[float]:
    """Gleitender Mittelwert über `window` Werte (ungerade), am Rand mit kleinerem Fenster.

    Der erste und der letzte Wert bleiben unverändert: Die Anfahrt zur Startpose und die
    Endstellung sollen sich durch das Glätten nicht verschieben.
    """
    if window < 1 or window % 2 == 0:
        raise ValueError("Das Fenster muss ungerade und mindestens 1 sein.")
    half = window // 2
    out = []
    for i in range(len(values)):
        lo, hi = max(0, i - half), min(len(values), i + half + 1)
        out.append(sum(values[lo:hi]) / (hi - lo))
    out[0], out[-1] = values[0], values[-1]
    return out


def roughness(values: list[float]) -> float:
    """Mittlere Beschleunigung: |zweite Differenz|. Je ruckeliger die Kurve, desto größer."""
    second = [values[i - 1] - 2 * values[i] + values[i + 1] for i in range(1, len(values) - 1)]
    return sum(abs(d) for d in second) / len(second) if second else 0.0


def smooth_trajectory(traj: Trajectory, window: int, name: str) -> Trajectory:
    """Alle Gelenke glätten, die Zeiten bleiben. Rohwerte bleiben ganze Zahlen."""
    columns = [[float(row[i]) for row in traj.positions] for i in range(len(traj.joints))]
    smooth = [moving_average(col, window) for col in columns]
    positions = [tuple(round(col[i]) for col in smooth) for i in range(len(traj.times))]
    meta = {**traj.meta, "recorded_with": "uebung_04_glaetten", "smoothed_window": window}
    return Trajectory(name, traj.joints, list(traj.times), positions, meta)


def example(name: str = "beispiel_wackelig") -> Trajectory:
    """Sinusbewegung des Handgelenks mit Messrauschen, 3 s bei 30 Hz."""
    rng = random.Random(1)
    times = [i / 30 for i in range(91)]
    wrist = tuple(round(2048 + 300 * math.sin(2 * math.pi * t / 3) + rng.gauss(0, 15)) for t in times)
    return Trajectory(name, ("wrist_flex",), times, [(v,) for v in wrist], {"recorded_with": "uebung_04"})


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Eine Aufnahme mit gleitendem Mittelwert glätten.")
    parser.add_argument("--name", default="beispiel_wackelig", help="Aufnahme in recordings/trajectories/")
    parser.add_argument("--ziel", default=None, help="Name der geglätteten Aufnahme (Standard: <name>_glatt)")
    parser.add_argument("--fenster", type=int, default=5, help="Fenstergröße (ungerade)")
    parser.add_argument("--beispiel", action="store_true", help="erst eine Beispielaufnahme anlegen")
    args = parser.parse_args(argv)

    if args.beispiel:
        path, _ = save_trajectory(example(args.name), overwrite=True)
        print(f"Beispielaufnahme geschrieben: {path}")
    traj = load_trajectory(args.name)
    target = args.ziel or f"{traj.name}_glatt"
    smooth = smooth_trajectory(traj, args.fenster, target)
    csv_path, _ = save_trajectory(smooth, overwrite=True)

    print(f"{traj.name} → {target}  (Fenster {args.fenster}, {len(traj.times)} Abtastungen)\n")
    print(f"{'Gelenk':<18}{'Rauheit vorher':>16}{'nachher':>10}{'größte Abweichung':>20}")
    for i, joint in enumerate(traj.joints):
        before = [row[i] for row in traj.positions]
        after = [row[i] for row in smooth.positions]
        deviation = max(abs(a - b) for a, b in zip(before, after, strict=True))
        print(f"{joint:<18}{roughness(before):>16.2f}{roughness(after):>10.2f}{deviation:>16} Schr.")
    print(f"\nGeschrieben: {csv_path}")
    print(f"Abspielen:   uv run scripts/07_replay_trajectory.py --name {target} --speed 0.3")
    return 0


if __name__ == "__main__":
    run_script(main)
