"""Übung 3 – Wie wirkt der P-Anteil? Sprungantwort messen (Musterlösung).

Aufruf (nur am **echten Arm** aussagekräftig, die Simulation kennt keinen Regler):
    uv run exercises/loesungen/uebung_03_p_anteil.py --gelenk wrist_flex
    uv run exercises/loesungen/uebung_03_p_anteil.py --gelenk wrist_flex --p 8 16 32

ACHTUNG: Das Programm schreibt `p_coefficient` ins **EEPROM** des Servos (dauerhaft!). Der
ursprüngliche Wert wird am Ende (auch nach Fehler oder Strg+C) zurückgeschrieben. Der Arm
bewegt sich um ±`--delta` Schritt. Hand in der Nähe, Gelenk frei.
"""

from __future__ import annotations

import argparse
import time

from so101_lab.arm import Arm
from so101_lab.backends.base import BackendError
from so101_lab.devices.spec import JointMode
from so101_lab.safety import open_arm
from so101_lab.script_support import add_device_arguments, confirm, resolve_port, run_script

MAX_P = 48  # Lehrwert: darüber schwingen manche Gelenke auf
TOLERANCE = 12  # Schritt (≈ 1°): ab hier gilt das Ziel als erreicht

Sample = tuple[float, int]  # (Sekunden seit dem Sprung, Position in Schritt)


def step_metrics(samples: list[Sample], start: int, goal: int, tolerance: int = TOLERANCE) -> dict:
    """Kennzahlen einer Sprungantwort.

    - `settle_s`: Zeit ab der die Position dauerhaft innerhalb `tolerance` um das Ziel bleibt
      (None, wenn sie das bis zum Ende nicht schafft)
    - `overshoot`: wie weit (Schritt) die Position über das Ziel hinausschießt, 0 wenn nie
    - `final_error`: Abstand der letzten Messung zum Ziel (Schritt, mit Vorzeichen)
    """
    direction = 1 if goal >= start else -1
    overshoot = max((max(0, direction * (pos - goal)) for _, pos in samples), default=0)
    settle_s = None
    for t, pos in samples:
        if abs(pos - goal) <= tolerance:  # noqa: SIM108 (ausgeschrieben ist es leichter zu lesen)
            settle_s = t if settle_s is None else settle_s
        else:
            settle_s = None  # wieder draußen: von vorn zählen
    return {"settle_s": settle_s, "overshoot": overshoot, "final_error": samples[-1][1] - goal}


def measure_step(arm: Arm, joint: str, goal: int, seconds: float = 1.5, hz: float = 50) -> list[Sample]:
    """Ziel setzen und die Position `seconds` lang mit `hz` lesen."""
    samples: list[Sample] = []
    arm.move_to({joint: goal}, limit_step=False)
    t0 = time.monotonic()
    while (t := time.monotonic() - t0) < seconds:
        samples.append((t, arm.read_positions([joint])[joint]))
        time.sleep(1 / hz)
    return samples


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Sprungantwort bei verschiedenem P-Anteil messen.")
    add_device_arguments(parser)
    parser.add_argument("--gelenk", required=True)
    parser.add_argument("--p", type=int, nargs="+", default=[8, 16, 32], help="P-Werte (Standard: 8 16 32)")
    parser.add_argument("--delta", type=int, default=300, help="Sprunghöhe in Schritt (≤ 400)")
    parser.add_argument("--ohne-warten", action="store_true")
    args = parser.parse_args(argv)
    if any(not 1 <= p <= MAX_P for p in args.p):
        raise BackendError(f"P muss zwischen 1 und {MAX_P} liegen (Lehrwert, siehe Kapitel 03).")
    if not 0 < args.delta <= 400:
        raise BackendError("--delta muss zwischen 1 und 400 Schritt liegen.")

    step_metrics([(0.0, 0), (0.1, 100)], 0, 100)  # Selbsttest: läuft deine Funktion? Dann erst bewegen

    with open_arm(args.device, port=resolve_port(args), config_path=args.config) as arm:
        joint = arm.spec.joint(args.gelenk)
        if joint.mode is not JointMode.POSITION:
            raise BackendError(f"'{joint.name}' ist ein Rad, hier geht nur ein Positionsgelenk.")
        servo = joint.motor_id
        original = arm.backend.read_register(servo, "p_coefficient")
        low, high = arm.limits(joint.name)
        start = arm.read_positions([joint.name])[joint.name]
        far = start + args.delta if start + args.delta <= high else start - args.delta
        far = max(low, min(high, far))
        print(f"{joint.name}: Start {start}, Sprung auf {far}, P war {original} (wird zurückgeschrieben).")
        if not args.ohne_warten and not confirm("P-Anteil im EEPROM ändern und Gelenk bewegen. Los?"):
            print("Abgebrochen, nichts geändert.")
            return 1
        arm.configure_motion(max_velocity=3000, acceleration=254)  # Sprung: so schnell wie möglich
        results = {}
        try:
            for p in args.p:
                arm.backend.write_eeprom(servo, "p_coefficient", p)  # schaltet das Drehmoment aus
                arm.enable_torque([joint.name])
                for goal in (start, far):  # erst zurück an den Start, dann der eigentliche Sprung
                    samples = measure_step(arm, joint.name, goal)
                results[p] = step_metrics(samples, start, far)
        finally:
            arm.backend.write_eeprom(servo, "p_coefficient", original)  # in jedem Fall zurück

    print(f"\n{'P':>4}{'Einschwingen':>15}{'Überschwingen':>16}{'Restfehler':>13}")
    for p, m in results.items():
        settle = "–" if m["settle_s"] is None else f"{m['settle_s']:.2f} s"
        print(f"{p:>4}{settle:>15}{m['overshoot']:>12} Schr.{m['final_error']:>9} Schr.")
    print(f"\np_coefficient steht wieder auf {original}.")
    return 0


if __name__ == "__main__":
    run_script(main)
