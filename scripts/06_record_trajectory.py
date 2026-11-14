"""06 – Eine Bewegung aufzeichnen (ohne Kamera, als CSV und JSON).

Aufruf:
    uv run scripts/06_record_trajectory.py --name winken
    uv run scripts/06_record_trajectory.py --name winken --device leader --dauer 15 --rate 30
    uv run scripts/06_record_trajectory.py --name probe --device demo --dauer 2

Was hier passiert:
1. Torque wird ausgeschaltet. Der Arm lässt sich jetzt von Hand bewegen.
2. Nach Enter wird mit fester Abtastrate (`--rate`, Standard 30 Hz) die Position aller
   Gelenke gelesen: ein Sync-Read je Abtastung, also ein Buspaket für alle Servos.
3. Die Aufnahme endet nach `--dauer` Sekunden oder früher mit Strg+C. Beides speichert.
4. Gespeichert wird nach `recordings/trajectories/<name>.csv` (Zeit und Rohwerte) und
   `<name>.json` (Beschreibung, Kalibrierung, tatsächlich erreichte Abtastrate).

Dasselbe mit Oberfläche: Web-App, Reiter „Aufnahmen“.

Der Takt wird nicht durch `sleep(1/rate)` erzeugt, sondern gegen einen festen Zeitplan
(`t0 + i / rate`). So summieren sich Verzögerungen nicht auf. Schafft der Bus die Rate nicht,
steht die tatsächliche Rate im JSON und das Skript warnt.

Sicherheit: Ein **Follower** sackt bei ausgeschaltetem Torque zusammen. Beim Aufnehmen mit
dem Follower den Arm festhalten. Üblich ist, den **Leader** aufzunehmen (`--device leader`,
Eintrag in arms.toml, eigenes Netzteil, Kapitel 01), weil er dafür gebaut ist.

LeRobot-Werkzeug dafür: `lerobot-record` (mit Kamera und als Dataset, Kapitel 05).
"""

from __future__ import annotations

import argparse
import time
from pathlib import Path

from so101_lab.backends.base import BackendError
from so101_lab.motion import calibration_to_dict
from so101_lab.paths import project_root
from so101_lab.safety import open_arm
from so101_lab.script_support import add_device_arguments, resolve_port, run_script
from so101_lab.trajectories import Trajectory, save_trajectory


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Bewegung per Hand führen und als CSV speichern.")
    add_device_arguments(parser)
    parser.add_argument("--name", required=True, help="Name der Aufnahme (Buchstaben, Ziffern, _ und -)")
    parser.add_argument("--dauer", type=float, default=10.0, help="größte Dauer in Sekunden (Standard 10)")
    parser.add_argument("--rate", type=float, default=30.0, help="Abtastrate in Hz (Standard 30)")
    parser.add_argument(
        "--ueberschreiben", action="store_true", help="vorhandene Aufnahme gleichen Namens ersetzen"
    )
    parser.add_argument("--ohne-warten", action="store_true", help="nicht auf Enter warten (z. B. in Tests)")
    args = parser.parse_args(argv)
    if not 1 <= args.rate <= 100:
        raise BackendError("--rate muss zwischen 1 und 100 Hz liegen.")
    if args.dauer <= 0:
        raise BackendError("--dauer muss größer als 0 sein.")

    port = resolve_port(args)
    with open_arm(args.device, port=port, config_path=args.config) as arm:
        joints = [j.name for j in arm.spec.position_joints]  # Räder (LeKiwi) werden nicht aufgenommen

        # Homing-Offsets merken: Der Servo meldet Rohwert = Stellung − Offset. Wird später neu
        # kalibriert, kann das Abspielen den geänderten Offset herausrechnen.
        offsets = arm.read_homing_offsets(joints)

        # Schritt 1: Torque aus. Ab jetzt trägt der Servo den Arm nicht mehr.
        arm.disable_torque(joints)
        print(f"Torque aus für {len(joints)} Gelenke. Arm festhalten oder Leader führen.")
        if not args.ohne_warten:
            input(
                f"Enter startet die Aufnahme ({args.dauer:g} s, {args.rate:g} Hz), "
                "Strg+C beendet sie früher: "
            )

        # Schritt 2: Abtasten gegen einen festen Zeitplan
        times: list[float] = []
        rows: list[tuple[int, ...]] = []
        period = 1.0 / args.rate
        t0 = time.monotonic()
        index = 0
        try:
            while True:
                due = index * period
                if due > args.dauer:
                    break
                wait = t0 + due - time.monotonic()
                if wait > 0:
                    time.sleep(wait)
                raw = arm.read_positions(joints)  # ein Sync-Read für alle Gelenke
                times.append(time.monotonic() - t0)
                rows.append(tuple(raw[j] for j in joints))
                index += 1
        except KeyboardInterrupt:
            print("\nAufnahme beendet.")

    # Schritt 3: Speichern (nach dem sicheren Zurücksetzen, die Verbindung ist schon zu)
    trajectory = Trajectory(
        name=args.name,
        joints=tuple(joints),
        times=times,
        positions=rows,
        meta={
            "device": args.device,
            "device_type": arm.spec.type,
            "calibration_id": arm.cfg.calibration_id,
            "calibration_file": _relative(arm.calibration_path),
            # Die Kalibrierung selbst: Damit lässt sich die Aufnahme auf einem anderen Arm abspielen
            # (Umrechnung über Grad/Prozent, siehe so101_lab/motion.py).
            "calibration": calibration_to_dict(arm.calibration),
            "homing_offsets": offsets,
            "target_rate_hz": args.rate,
            "source": "von Hand geführt (Drehmoment aus)",
            "recorded_with": "skript_06",
        },
    )
    csv_path, json_path = save_trajectory(trajectory, overwrite=args.ueberschreiben)
    rate = trajectory.sample_rate_hz
    print(f"{len(times)} Abtastungen in {trajectory.duration_s:.1f} s gespeichert:")
    print(f"  {_relative(csv_path)}\n  {_relative(json_path)}")
    if rate is not None:
        print(f"Erreichte Abtastrate: {rate:.1f} Hz (Ziel {args.rate:g} Hz)")
        if rate < 0.9 * args.rate:
            print(
                "Warnung: Der Bus schafft die Rate nicht. Kleinere --rate wählen oder Kabel/Adapter prüfen."
            )
    print(f"Abspielen: uv run scripts/07_replay_trajectory.py --name {args.name} --device {args.device}")
    return 0


def _relative(path: Path | None) -> str | None:
    """Pfad relativ zum Projektordner, wenn er darin liegt (für Anzeige und JSON)."""
    if path is None:
        return None
    try:
        return str(Path(path).resolve().relative_to(project_root().resolve()))
    except ValueError:
        return str(path)


if __name__ == "__main__":
    run_script(main)
