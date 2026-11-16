"""Übung 1 – Messwerte lesen und bewerten (Vorlage).

Aufruf:
    uv run exercises/uebung_01_messen.py --device demo
    uv run exercises/uebung_01_messen.py --soll-volt 12 --max-temp 45

Das Programm liest den Zustand aller Servos (nur lesen!) und meldet Auffälligkeiten.

Deine Aufgabe: die mit TODO markierten Funktionen schreiben, dann das Programm starten.
Hängst du fest? Die Musterlösung steht in exercises/loesungen/uebung_01_messen.py.
"""

from __future__ import annotations

import argparse

from so101_lab.backends.base import ServoState
from so101_lab.safety import open_arm
from so101_lab.script_support import add_device_arguments, resolve_port, run_script


def find_problems(
    states: dict[str, ServoState], nominal_voltage: float, max_temperature: int = 50
) -> list[str]:
    """Eine Zeile je Auffälligkeit, z. B. 'elbow_flex: Temperatur 56 °C (Grenze 50 °C)'."""
    # TODO: hier kommt dein Code hin
    raise NotImplementedError(
        "find_problems(): Je Gelenk Spannung (±10 % um den Sollwert), Temperatur und Fehlerbits "
        "(state.errors) prüfen und je Auffälligkeit eine Textzeile sammeln."
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Servo-Zustand lesen und bewerten (verändert nichts).")
    add_device_arguments(parser)
    parser.add_argument("--soll-volt", type=float, default=12.0, help="Sollspannung deines Netzteils (V)")
    parser.add_argument("--max-temp", type=int, default=50, help="Temperaturgrenze in °C (Lehrwert)")
    args = parser.parse_args(argv)

    with open_arm(args.device, port=resolve_port(args), config_path=args.config, read_only=True) as arm:
        states = arm.read_state()
    print(f"{'Gelenk':<18}{'Pos':>6}{'Spannung':>11}{'Temp':>8}  Drehmoment")
    for joint, s in states.items():
        torque = "an" if s.torque_enabled else "aus"
        print(f"{joint:<18}{s.position:>6}{s.voltage:>9.1f} V{s.temperature:>5} °C  {torque}")
    problems = find_problems(states, args.soll_volt, args.max_temp)
    print()
    print("\n".join(f"AUFFÄLLIG  {p}" for p in problems) if problems else "Alles im grünen Bereich.")
    return 1 if problems else 0


if __name__ == "__main__":
    run_script(main)
