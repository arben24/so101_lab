"""02 – Einen Servo auslesen: Register für Register, danach live.

Aufruf:
    uv run scripts/02_read_servo.py --gelenk shoulder_pan
    uv run scripts/02_read_servo.py --gelenk shoulder_pan --port /dev/ttyACM0
    uv run scripts/02_read_servo.py --live        Positionen aller Gelenke laufend anzeigen
    uv run scripts/02_read_servo.py --device demo simulierte Servos, keine Hardware nötig

Was hier passiert:
1. Jeder Motor des Geräts bekommt einen Ping (Register `model_number`). Wer nicht antwortet,
   hat Kabel-, Strom- oder ID-Probleme.
2. Alle Register eines Servos werden einzeln gelesen und mit Adresse, Speicherbereich
   (EEPROM oder RAM), Einheit und Bedeutung angezeigt. Die Namen und Adressen kommen aus
   dem Treiber rustypot, den auch LeRobot benutzt; die Einheiten aus der Feetech-Tabelle.
3. Mit --live werden die Positionen aller Gelenke fünfmal pro Sekunde gelesen.
   Torque ist aus, der Arm lässt sich von Hand bewegen.

Sicherheit: Das Skript **schreibt nichts**. Es ändert weder Register noch Drehmoment.

LeRobot-Werkzeug dafür: kein einzelnes. `lerobot-teleoperate` liest dieselben Positionen,
bewegt dabei aber den Follower.
"""

from __future__ import annotations

import argparse
import time

from so101_lab import registers as regs
from so101_lab.backends.base import BackendError
from so101_lab.register_info import info
from so101_lab.safety import open_arm
from so101_lab.script_support import add_device_arguments, resolve_port, run_script

STEPS_PER_TURN = 4096


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Servo-Register lesen (verändert nichts).")
    add_device_arguments(parser)
    parser.add_argument(
        "--gelenk", default=None, help="Gelenkname, z. B. shoulder_pan (Standard: erstes Gelenk)"
    )
    parser.add_argument("--live", action="store_true", help="danach Positionen laufend anzeigen")
    args = parser.parse_args(argv)

    port = resolve_port(args)
    # read_only=True: am Ende wird nichts verändert (kein Torque-Aus, keine Schreibzugriffe).
    # check_motors=False: Wir prüfen unten selbst und zeigen, wer fehlt.
    with open_arm(args.device, port=port, config_path=args.config, check_motors=False, read_only=True) as arm:
        backend = arm.backend

        # --- Teil 1: Wer antwortet? --------------------------------------------------
        print(f"Gerät '{args.device}' ({arm.spec.description})\n")
        print(f"{'Gelenk':<16}{'ID':>3}  Antwort")
        answering = []
        for joint in arm.joints:
            model = backend.ping(joint.motor_id)  # liest Register model_number (Adresse 3)
            names = regs.model_names()
            text = (
                f"Modell {model} ({names.get(model, 'unbekannt')})" if model is not None else "KEINE ANTWORT"
            )
            print(f"{joint.name:<16}{joint.motor_id:>3}  {text}")
            if model is not None:
                answering.append(joint)
        if not answering:
            print("\nKein Servo antwortet. Servo-Netzteil an? Kabel gesteckt? Dann: so101-lab scan")
            return 1

        # --- Teil 2: Alle Register eines Servos --------------------------------------
        joint = arm.spec.joint(args.gelenk) if args.gelenk else answering[0]
        if joint not in answering:
            print(f"\n'{joint.name}' antwortet nicht.")
            return 1
        print(f"\nRegister von '{joint.name}' (ID {joint.motor_id}):\n")
        print(f"{'Adr':>3}  {'Name':<36}{'Bereich':<9}{'Wert':>7}  Einheit / Bedeutung")
        for reg in sorted(regs.registers(), key=lambda r: r.address):
            try:
                value = str(backend.read_register(joint.motor_id, reg.name))
            except BackendError:
                value = "–"  # z. B. Register, die dieser Servo nicht kennt
            meaning = info(reg.name)
            unit = f"[{meaning.unit}] " if meaning.unit else ""
            print(f"{reg.address:>3}  {reg.name:<36}{reg.area.value:<9}{value:>7}  {unit}{meaning.text}")
        position = backend.read_register(joint.motor_id, "present_position")
        print(
            f"\nPosition {position} Schritt = {position * 360 / STEPS_PER_TURN:.1f}° "
            "von 360° (Rohwert, ohne Kalibrierung)"
        )

        # --- Teil 3: Live ----------------------------------------------------------
        if args.live:
            print("\nLive-Anzeige, Ende mit Strg+C. Arm von Hand bewegen.\n")
            names = [j.name for j in answering]
            try:
                while True:
                    raw = arm.read_positions(names)
                    line = "  ".join(f"{n}={v:>4}" for n, v in raw.items())
                    print(f"\r{line}", end="", flush=True)
                    time.sleep(0.2)
            except KeyboardInterrupt:
                print()
    return 0


if __name__ == "__main__":
    run_script(main)
