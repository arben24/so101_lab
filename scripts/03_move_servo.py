"""03 – Ein Gelenk kontrolliert bewegen.

Aufruf:
    uv run scripts/03_move_servo.py --gelenk wrist_flex
    uv run scripts/03_move_servo.py --gelenk wrist_flex --delta 150 --tempo 500
    uv run scripts/03_move_servo.py --gelenk gripper --device demo     (simuliert)

Was hier passiert (Reihenfolge ist Absicht, siehe Kapitel 03 und 04):
1. Tempo- und Beschleunigungsgrenze setzen (`configure_motion`) – **bevor** ein Ziel geschrieben wird.
2. Aktuelle Position lesen. Sie ist der Ausgangspunkt, das Gelenk fährt nur relativ dazu.
3. Torque einschalten. `enable_torque` schreibt vorher die Istposition als Ziel,
   sonst würde der Servo zum alten Wert im Zielregister springen.
4. Ziel schreiben (`move_to`). Dabei wird das Ziel auf die kalibrierten Grenzen und auf
   den größten erlaubten Sprung begrenzt. Ohne Kalibrierung gilt der volle Bereich 0…4095.
5. Warten, bis die Position erreicht ist, und die Bewegung ausgeben.
6. Am Ende (auch nach Fehler oder Strg+C) setzt `open_arm` sicher zurück: Räder stopp,
   dann je nach `safe_state` in arms.toml Torque aus (Standard) oder Stellung halten.

Bewegung: Start → Start+Delta → Start → Start−Delta → Start. Bevor es losgeht, bitte das
Gelenk von Hand in eine Mittelstellung bringen, nicht an einen Anschlag.

Sicherheit: Der Arm sinkt bei „Torque aus“ unter seinem Gewicht ab. Hand in der Nähe halten.
Quetschgefahr: Finger weg vom Gelenk, während es sich bewegt.

LeRobot-Werkzeug dafür: `lerobot-teleoperate` (bewegt alle Gelenke nach einem Leader).
"""

from __future__ import annotations

import argparse
import time

from so101_lab.arm import Arm
from so101_lab.backends.base import BackendError
from so101_lab.devices.spec import JointMode
from so101_lab.safety import open_arm
from so101_lab.script_support import add_device_arguments, confirm, resolve_port, run_script

STEPS_PER_DEGREE = 4096 / 360
REACHED_WITHIN = 12  # Schritt (≈ 1°): ab hier gilt das Ziel als erreicht
TIMEOUT_S = 4.0


def wait_until_reached(arm: Arm, joint: str, goal: int) -> int:
    """Position lesen, bis das Ziel erreicht ist oder die Zeit abläuft. Liefert die Endposition."""
    deadline = time.monotonic() + TIMEOUT_S
    while True:
        position = arm.read_positions([joint])[joint]
        if abs(position - goal) <= REACHED_WITHIN or time.monotonic() > deadline:
            return position
        time.sleep(0.05)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Ein Gelenk kontrolliert hin und her fahren.")
    add_device_arguments(parser)
    parser.add_argument("--gelenk", required=True, help="Gelenkname, z. B. wrist_flex")
    parser.add_argument(
        "--delta", type=int, default=200, help="Auslenkung in Schritt (4096 = 360°), Standard 200"
    )
    parser.add_argument(
        "--tempo", type=int, default=None, help="Höchsttempo in Schritt/s (Standard: arms.toml)"
    )
    parser.add_argument(
        "--ohne-warten", action="store_true", help="nicht auf Bestätigung warten (z. B. in Tests)"
    )
    args = parser.parse_args(argv)

    port = resolve_port(args)
    with open_arm(args.device, port=port, config_path=args.config) as arm:
        joint = arm.spec.joint(args.gelenk)
        if joint.mode is not JointMode.POSITION:
            raise BackendError(f"'{joint.name}' ist ein Rad. Für Räder gibt es Skript 09 (kommt mit LeKiwi).")
        delta = abs(args.delta)
        step_limit = arm.cfg.max_relative_target
        if step_limit is not None and delta > step_limit:
            raise BackendError(
                f"Delta {delta} ist größer als der erlaubte Sprung ({step_limit} Schritt, "
                f"max_relative_target in arms.toml). Bitte ein kleineres --delta wählen."
            )

        # Schritt 2: Ausgangsposition lesen
        start = arm.read_positions([joint.name])[joint.name]
        low, high = arm.limits(joint.name)
        print(f"{joint.name} (ID {joint.motor_id}) steht bei {start} Schritt, erlaubt {low}…{high}.")
        if not arm.is_calibrated:
            print("Hinweis: keine Kalibrierung gefunden, die Grenzen sind der volle Bereich 0…4095.")
        print(f"Das Gelenk fährt gleich um ±{delta} Schritt (±{delta / STEPS_PER_DEGREE:.1f}°).")
        if not args.ohne_warten and not confirm("Gelenk frei, Hand in der Nähe, los?"):
            print("Abgebrochen, nichts bewegt.")
            return 1

        # Schritt 1: Grenzen zuerst. Danach erst Torque und Ziele.
        arm.configure_motion(max_velocity=args.tempo)
        arm.enable_torque([joint.name])  # setzt vorher Ziel = Istposition: kein Sprung

        for label, goal in (("+", start + delta), ("zurück", start), ("−", start - delta), ("zurück", start)):
            sent = arm.move_to({joint.name: goal})[joint.name]  # begrenzt auf Grenzen und Sprung
            reached = wait_until_reached(arm, joint.name, sent)
            note = "" if sent == goal else f" (Ziel {goal} wurde auf {sent} begrenzt)"
            print(f"  {label:<7} Ziel {sent:>4}  erreicht {reached:>4}{note}")
    print("Fertig. Torque ist wieder aus (safe_state).")
    return 0


if __name__ == "__main__":
    run_script(main)
