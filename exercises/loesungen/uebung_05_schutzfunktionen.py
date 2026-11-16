"""Übung 5 – Schutzfunktionen herausfordern: erst raten, dann ausprobieren (Musterlösung).

Aufruf:
    uv run exercises/loesungen/uebung_05_schutzfunktionen.py --device demo

Das Programm stellt vier Versuche an. Vor jedem fragt es nach deinem Tipp, danach zeigt es,
was passiert ist. Drehmoment bleibt aus: Am echten Arm bewegt sich nichts.
"""

from __future__ import annotations

import argparse

from so101_lab.arm import Arm
from so101_lab.backends.base import BackendError
from so101_lab.safety import open_arm, write_ram_register
from so101_lab.script_support import add_device_arguments, resolve_port, run_script

JOINT = "wrist_flex"


def try_it(action) -> str:
    """Führt `action()` aus. Liefert deren Ergebnistext oder die Fehlermeldung des Backends."""
    try:
        return str(action())
    except BackendError as exc:
        return f"abgelehnt: {exc}"


def experiment_1(arm: Arm) -> str:
    """Ziel weit hinter der Gelenkgrenze. Ohne Sprungbegrenzung, damit nur die Grenze wirkt."""
    low, high = arm.limits(JOINT)
    sent = arm.move_to({JOINT: high + 500}, limit_step=False)[JOINT]
    return f"gewünscht {high + 500}, gesendet {sent} (Grenze {high})"


def experiment_2(arm: Arm) -> str:
    """Sprung weit weg von der Istposition."""
    present = arm.read_positions([JOINT])[JOINT]
    wanted = present + 2000
    sent = arm.move_to({JOINT: wanted})[JOINT]
    limit = arm.cfg.max_relative_target
    return f"Ist {present}, gewünscht {wanted}, gesendet {sent} (max_relative_target {limit})"


def experiment_3(arm: Arm) -> str:
    """EEPROM-Register über den Whitelist-Weg für RAM schreiben."""
    servo = arm.spec.joint(JOINT).motor_id
    return try_it(lambda: write_ram_register(arm.backend, servo, "p_coefficient", 16))


def experiment_4(arm: Arm) -> str:
    """Wert außerhalb des erlaubten Bereichs in ein erlaubtes RAM-Register."""
    servo = arm.spec.joint(JOINT).motor_id
    return try_it(lambda: write_ram_register(arm.backend, servo, "acceleration", 9999))


EXPERIMENTS = [
    ("Ziel 500 Schritt hinter der Gelenkgrenze (move_to ohne Sprungbegrenzung)", experiment_1),
    ("Sprung von 2000 Schritt (move_to)", experiment_2),
    ("p_coefficient mit write_ram_register schreiben", experiment_3),
    ("acceleration = 9999 schreiben (das Register fasst 0…255)", experiment_4),
]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Schutzfunktionen: raten, ausprobieren, vergleichen.")
    add_device_arguments(parser)
    parser.add_argument("--tipps", nargs="*", default=None, help="deine Tipps (sonst Eingabe im Terminal)")
    args = parser.parse_args(argv)

    with open_arm(args.device, port=resolve_port(args), config_path=args.config) as arm:
        for number, (title, run) in enumerate(EXPERIMENTS, start=1):
            print(f"\nVersuch {number}: {title}")
            if args.tipps is not None:
                guess = args.tipps[number - 1] if number <= len(args.tipps) else "(kein Tipp)"
            else:
                guess = input("  Dein Tipp: ")
            print(f"  Dein Tipp:  {guess}")
            print(f"  Ergebnis:   {run(arm)}")
    print("\nWelche Schicht hat jeweils eingegriffen? Kapitel 09, Abschnitt „Sicherheitsschichten“.")
    return 0


if __name__ == "__main__":
    run_script(main)
