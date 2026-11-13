"""07 – Eine aufgezeichnete Bewegung abspielen, mit sanfter Anfahrt.

Aufruf:
    uv run scripts/07_replay_trajectory.py --name winken
    uv run scripts/07_replay_trajectory.py --name winken --speed 0.5
    uv run scripts/07_replay_trajectory.py --name probe --device demo --ohne-warten

Was hier passiert:
1. Die Aufnahme (`recordings/trajectories/<name>.csv`) wird geladen und geprüft: Passen die
   Gelenke zum Gerät? Liegt die Startpose innerhalb der Gelenkgrenzen?
2. Tempo- und Beschleunigungsgrenze werden gesetzt, **bevor** ein Ziel geschrieben wird.
3. **Sanfte Anfahrt:** Der Arm steht irgendwo, die Aufnahme beginnt woanders. Ein direkter
   Sprung zur Startpose wäre ruckartig. Deshalb fährt das Skript in mindestens 2 s mit
   glattem Verlauf (smoothstep: langsam anfahren, langsam ankommen) zur Startpose.
4. Danach läuft die Aufnahme nach ihren Zeitstempeln. `--speed 0.5` halbiert das Tempo,
   `--speed 2` verdoppelt es. Jede Zeile geht durch `move_to` und wird auf die Gelenkgrenzen
   und auf den größten Sprung je Schritt begrenzt.
5. Strg+C bricht jederzeit ab. `open_arm` setzt danach sicher zurück (je nach `safe_state`
   Torque aus: der Arm sinkt dann ab; oder Stellung halten).

Zur Aufnahme gehört das Gerät, mit dem sie gemacht wurde. Eine Aufnahme vom Leader lässt
sich auf dem Follower abspielen (gleiche Gelenknamen), vorausgesetzt beide sind kalibriert.

Sicherheit: Arbeitsbereich freihalten, Hand am Netzteil-Schalter. Zum ersten Mal mit
`--speed 0.3` probieren.

LeRobot-Werkzeug dafür: `lerobot-replay` (spielt eine Episode aus einem LeRobot-Dataset ab).
"""

from __future__ import annotations

import argparse
import time

from so101_lab.backends.base import BackendError
from so101_lab.safety import open_arm
from so101_lab.script_support import add_device_arguments, confirm, resolve_port, run_script
from so101_lab.trajectories import load_trajectory

APPROACH_STEPS_PER_S = 300  # Anfahrtstempo, ca. 26°/s
APPROACH_MIN_S = 2.0
TICK_S = 1 / 30  # Takt der Anfahrt


def smoothstep(x: float) -> float:
    """0 → 1 mit weichem Anfang und Ende (Ableitung 0 an beiden Enden)."""
    x = max(0.0, min(1.0, x))
    return x * x * (3 - 2 * x)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Aufgezeichnete Bewegung sanft anfahren und abspielen.")
    add_device_arguments(parser)
    parser.add_argument("--name", required=True, help="Name der Aufnahme oder Pfad zur CSV-Datei")
    parser.add_argument("--speed", type=float, default=1.0, help="Tempofaktor 0.1…3 (Standard 1)")
    parser.add_argument(
        "--tempo", type=int, default=None, help="Höchsttempo der Servos in Schritt/s (Standard: arms.toml)"
    )
    parser.add_argument(
        "--ohne-warten", action="store_true", help="nicht auf Bestätigung warten (z. B. in Tests)"
    )
    args = parser.parse_args(argv)
    if not 0.1 <= args.speed <= 3:
        raise BackendError("--speed muss zwischen 0.1 und 3 liegen.")

    traj = load_trajectory(args.name)
    print(
        f"Aufnahme '{traj.name}': {len(traj.times)} Abtastungen, {traj.duration_s:.1f} s, "
        f"Gelenke {', '.join(traj.joints)}"
    )

    port = resolve_port(args)
    with open_arm(args.device, port=port, config_path=args.config) as arm:
        # Schritt 1: Prüfen
        known = {j.name for j in arm.spec.position_joints}
        unknown = [j for j in traj.joints if j not in known]
        if unknown:
            raise BackendError(f"Das Gerät '{args.device}' hat keine Gelenke {unknown}. Falsche Aufnahme?")
        joints = list(traj.joints)
        start = dict(zip(joints, traj.positions[0], strict=True))
        for name, value in start.items():
            low, high = arm.limits(name)
            if not low <= value <= high:
                raise BackendError(
                    f"Startpose: {name} = {value} liegt außerhalb der Gelenkgrenzen {low}…{high}. "
                    "Anderes Gerät oder andere Kalibrierung?"
                )
        if not arm.is_calibrated:
            print("Hinweis: keine Kalibrierung gefunden, Grenzen sind der volle Bereich 0…4095.")

        present = arm.read_positions(joints)
        distance = max(abs(start[j] - present[j]) for j in joints)
        approach_s = max(APPROACH_MIN_S, distance / APPROACH_STEPS_PER_S)
        play_s = traj.duration_s / args.speed
        print(
            f"Anfahrt zur Startpose: {approach_s:.1f} s (größter Weg {distance} Schritt), "
            f"danach {play_s:.1f} s Abspielen."
        )
        ending = "Torque aus, Arm sinkt ab" if arm.cfg.safe_state == "torque_off" else "Stellung halten"
        print(f"Beim Beenden: safe_state = {arm.cfg.safe_state} ({ending}).")
        if not args.ohne_warten and not confirm("Arbeitsbereich frei, Hand am Netzteil, los?"):
            print("Abgebrochen, nichts bewegt.")
            return 1

        # Schritt 2: Grenzen zuerst, dann Torque (Ziel = Istposition), dann Ziele
        arm.configure_motion(max_velocity=args.tempo)
        arm.enable_torque(joints)

        # Schritt 3: sanfte Anfahrt
        t0 = time.monotonic()
        while (elapsed := time.monotonic() - t0) < approach_s + TICK_S:
            s = smoothstep(elapsed / approach_s)
            arm.move_to({j: round(present[j] + (start[j] - present[j]) * s) for j in joints})
            time.sleep(TICK_S)

        # Schritt 4: Abspielen nach den Zeitstempeln der Aufnahme
        print("Spiele ab, Strg+C bricht ab …")
        t0 = time.monotonic()
        for t, row in zip(traj.times, traj.positions, strict=True):
            wait = t0 + t / args.speed - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            arm.move_to(dict(zip(joints, row, strict=True)))
        print("Fertig.")
    return 0


if __name__ == "__main__":
    run_script(main)
