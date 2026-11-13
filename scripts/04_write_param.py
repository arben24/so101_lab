"""04 – Ein RAM-Register schreiben (nur freigegebene Register).

Aufruf:
    uv run scripts/04_write_param.py --liste
    uv run scripts/04_write_param.py --gelenk wrist_flex --register acceleration --wert 50
    uv run scripts/04_write_param.py --gelenk wrist_flex --register goal_velocity --wert 300
    uv run scripts/04_write_param.py --gelenk wrist_flex --register acceleration --wert 50 --device demo

Was hier passiert:
1. Das Register muss auf der Whitelist stehen (`registers.RAM_WRITE_WHITELIST`). Alles
   andere wird abgelehnt, auch EEPROM-Register wie `p_coefficient` oder `homing_offset`.
   Diese verändern den Servo dauerhaft und haben eigene Funktionen (Kapitel 03).
2. Der alte Wert wird gelesen, der neue geschrieben und danach **zurückgelesen**. Nur wenn
   beides übereinstimmt, gilt der Schreibzugriff als erfolgreich.
3. RAM-Werte gelten bis zum Ausschalten des Servos. Nach einem Neustart ist alles wieder
   wie vorher. Das macht das Ausprobieren ungefährlich.

Zwei freigegebene Register gehen in diesem Skript bewusst nicht:
`torque_enable` und `goal_position`. Ein einzelner Schreibzugriff hätte hier keine Wirkung,
weil `open_arm` beim Beenden sofort wieder sicher zurücksetzt. Wie man sie richtig benutzt
(`enable_torque` setzt vorher das Ziel auf die Istposition, `move_to` begrenzt auf Gelenkgrenzen
und größten Sprung), zeigt Skript 03.

Am Ende setzt `open_arm` sicher zurück (Torque aus bzw. halten, siehe `safe_state`).

LeRobot-Werkzeug dafür: kein eigenes. LeRobot schreibt Register nur im Rahmen von `configure()`
beim Verbinden und in `lerobot-calibrate`.
"""

from __future__ import annotations

import argparse

from so101_lab import registers as regs
from so101_lab.backends.base import BackendError
from so101_lab.register_info import info
from so101_lab.safety import open_arm, write_ram_register
from so101_lab.script_support import add_device_arguments, resolve_port, run_script


def print_whitelist() -> None:
    print("Freigegebene Register (RAM, gelten bis zum Ausschalten):\n")
    print(f"{'Register':<16}{'Adr':>4}  Einheit / Bedeutung")
    for name in sorted(regs.RAM_WRITE_WHITELIST, key=lambda n: regs.lookup(n).address):
        reg = regs.lookup(name)
        meaning = info(name)
        unit = f"[{meaning.unit}] " if meaning.unit else ""
        print(f"{name:<16}{reg.address:>4}  {unit}{meaning.text}")
    print("\nHinweis: torque_enable und goal_position zeigt Skript 03 (mit Sicherheitsfunktionen).")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="RAM-Register eines Servos schreiben (Whitelist).")
    add_device_arguments(parser)
    parser.add_argument("--liste", action="store_true", help="freigegebene Register anzeigen und beenden")
    parser.add_argument("--gelenk", help="Gelenkname, z. B. wrist_flex")
    parser.add_argument("--register", help="Registername, z. B. acceleration")
    parser.add_argument("--wert", type=int, help="neuer Wert")
    args = parser.parse_args(argv)

    if args.liste:
        print_whitelist()
        return 0
    if not (args.gelenk and args.register and args.wert is not None):
        parser.error("--gelenk, --register und --wert sind nötig (oder --liste).")

    # Whitelist prüfen, bevor irgendeine Verbindung aufgebaut wird.
    reg = regs.lookup(args.register)
    if reg.name not in regs.RAM_WRITE_WHITELIST:
        allowed = ", ".join(sorted(regs.RAM_WRITE_WHITELIST))
        raise BackendError(
            f"Register '{reg.name}' ist nicht freigegeben (Bereich {reg.area.value}). Erlaubt: {allowed}."
        )

    if reg.name in ("torque_enable", "goal_position"):
        raise BackendError(
            f"'{reg.name}' ist freigegeben, wird aber nur in Skript 03 benutzt: Dort schaltet "
            "enable_torque() sicher ein und move_to() begrenzt das Ziel."
        )

    port = resolve_port(args)
    with open_arm(args.device, port=port, config_path=args.config) as arm:
        joint = arm.spec.joint(args.gelenk)
        servo_id = joint.motor_id
        old = arm.backend.read_register(servo_id, reg.name)
        print(f"{joint.name} (ID {servo_id}): {reg.name} = {old}")

        write_ram_register(arm.backend, servo_id, reg.name, args.wert)

        new = arm.backend.read_register(servo_id, reg.name)
        print(f"{joint.name} (ID {servo_id}): {reg.name} = {new}  (zurückgelesen)")
        if new != args.wert:
            raise BackendError(f"Zurückgelesen wurde {new}, geschrieben {args.wert}.")
    print("Fertig. RAM-Werte verschwinden beim Ausschalten des Servos.")
    return 0


if __name__ == "__main__":
    run_script(main)
