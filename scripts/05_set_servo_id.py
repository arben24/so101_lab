"""05 – Einem neuen Servo seine Gelenk-ID oder eine freie ID geben (schreibt ins EEPROM!).

Aufruf (genau EIN Servo am Bus):
    uv run scripts/05_set_servo_id.py --gelenk gripper
    uv run scripts/05_set_servo_id.py --gelenk elbow_flex --port /dev/ttyACM0
    uv run scripts/05_set_servo_id.py --id 12        # freie ID, z. B. Ersatzservo, eigener Aufbau

Dasselbe mit Oberfläche: Web-App, Reiter „Einrichtung“ (`uv run so101-lab web`).

Warum nur ein Servo? Neue STS3215 haben alle die Werks-ID 1. Hängen zwei am Bus,
antworten beide gleichzeitig, die Antworten überlagern sich, und man kann keinen von beiden
gezielt ansprechen. Deshalb gilt: **einen Servo anschließen, ID setzen, abziehen, nächster.**

Was hier passiert (der Ablauf steht in `so101_lab/servo_setup.py`, die Web-App benutzt ihn auch):
1. Den Bus bei allen Baudraten nach allen IDs (0–253) absuchen (ca. 8 s). Gefunden werden
   muss genau ein Servo.
2. Die Soll-ID kommt aus der Gelenktabelle (Kapitel 01): `--gelenk gripper` → ID 6.
   Oder direkt als Zahl: `--id 12` (0–253).
3. Vor dem Schreiben den Servo zehnmal lesen. Wackeln die Antworten, hängen vermutlich
   zwei Servos mit derselben ID am Bus: Abbruch.
4. Nach Rückfrage: `change_id()` (Drehmoment aus → EEPROM entsperren → neue ID schreiben →
   sperren → Ping unter der neuen ID → alte ID muss frei sein). Läuft der Servo nicht mit
   1 Mbit/s, danach `change_baudrate()` auf 1 000 000. Reihenfolge wie bei LeRobot.
5. Prüfen: Register `id` und `baud_rate` zurücklesen, bei 1 Mbit/s suchen.
6. Eine Zeile ins Protokoll `logs/servo_ids.csv` schreiben.

Reihenfolge wie bei LeRobot: erst Greifer (6), dann zurück bis zur Schulter (1).
Beschriften! Einen Aufkleber mit der ID auf den Servo kleben spart später viel Suchen.

Empfohlen danach: Netzteil aus und wieder an, dann `uv run so101-lab scan --port …`. Steht der
Servo dort unter der neuen ID, ist sie dauerhaft gespeichert (die Web-App hat dafür die
„Neustart-Probe“).

Sicherheit: Nur mit einem Servo am Bus. Servo vorher nicht mechanisch einbauen oder belasten.

LeRobot-Werkzeug dafür: `lerobot-setup-motors --robot.type=so101_follower --robot.port=<Port>`
(führt durch alle Motoren nacheinander und setzt auch die Baudrate).
"""

from __future__ import annotations

import argparse

from so101_lab.backends import create_backend
from so101_lab.backends.base import BackendError
from so101_lab.config import load_config
from so101_lab.devices import get_device_spec
from so101_lab.ports import adapter_serial
from so101_lab.script_support import add_device_arguments, confirm, resolve_port, run_script
from so101_lab.servo_setup import ServoSetup, describe


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Servo-ID setzen (EEPROM, genau ein Servo am Bus).")
    add_device_arguments(parser)
    ziel = parser.add_mutually_exclusive_group(required=True)
    ziel.add_argument("--gelenk", help="Gelenk, dessen ID der Servo bekommen soll")
    ziel.add_argument("--id", type=int, help="freie ID (0–253) statt eines Gelenks")
    args = parser.parse_args(argv)

    cfg = load_config(args.config).device(args.device)
    if args.gelenk is not None:
        try:
            get_device_spec(cfg.type).joint(args.gelenk)  # Tippfehler sofort melden, vor der Suche
        except KeyError as exc:
            raise BackendError(str(exc.args[0])) from None
    port = resolve_port(args)
    backend = create_backend(cfg, port)

    with backend:  # schließt den Port am Ende, auch bei Fehlern
        setup = ServoSetup(backend, cfg.type, port=port, adapter_sn=adapter_serial(port))
        target = setup.target(args.gelenk, args.id)  # prüft auch den Bereich der freien ID

        # Schritt 1: Bus absuchen, alle Baudraten
        print(f"Suche Servos an {port or args.device} bei allen Baudraten (ca. 8 s) …")
        for found in setup.scan():
            print(f"  gefunden: {describe(found)}")
        setup.single()  # Abbruch mit Erklärung, wenn es nicht genau einer ist

        # Schritt 2: Was würde sich ändern?
        plan = setup.plan(servo_id=target.id)
        if not plan:
            print(f"Der Servo hat schon {target.label} und 1 Mbit/s. Nichts zu tun.")
            return 0
        print("\nGeplant: " + ", ".join(plan) + ". Das wird dauerhaft in den Servo geschrieben.")
        if not confirm("Jetzt schreiben?"):
            print("Abgebrochen, nichts geschrieben.")
            return 1

        # Schritte 3–6: probieren, schreiben, prüfen, protokollieren
        result = setup.assign(servo_id=target.id)
        for check in result.checks:
            print(f"  {'✔' if check.ok else '✘'} {check.text}")
        print(f"Protokoll: {setup.log_path}")
        if not result.ok:
            raise BackendError(result.message)
        print(f"Fertig und geprüft. {result.message}")
    return 0


if __name__ == "__main__":
    run_script(main)
