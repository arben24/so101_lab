"""05 – Einem neuen Servo seine Gelenk-ID geben (schreibt ins EEPROM!).

Aufruf (genau EIN Servo am Bus):
    uv run scripts/05_set_servo_id.py --gelenk gripper
    uv run scripts/05_set_servo_id.py --gelenk elbow_flex --port /dev/ttyACM0

Warum nur ein Servo? Neue STS3215 haben alle die Werks-ID 1. Hängen zwei am Bus,
antworten beide gleichzeitig, die Antworten überlagern sich, und man kann keinen von beiden
gezielt ansprechen. Deshalb gilt: **einen Servo anschließen, ID setzen, abziehen, nächster.**
Das Skript bricht ab, wenn es nicht genau einen Servo findet.

Was hier passiert:
1. Den Bus bei 1 000 000 Baud nach allen IDs (0–253) absuchen. Gefunden werden muss genau ein Servo.
2. Die Soll-ID kommt aus der Gelenktabelle (Kapitel 01): `--gelenk gripper` → ID 6.
3. Nach Rückfrage `change_id()`: Drehmoment aus → EEPROM entsperren (lock = 0) → neue ID
   schreiben → sperren (lock = 1) → Ping unter der neuen ID → prüfen, dass die alte ID
   nicht mehr antwortet. Schlägt eine Prüfung fehl, meldet das Skript es als Fehler.
4. Der Servo behält die ID dauerhaft, auch nach dem Ausschalten.

Reihenfolge wie bei LeRobot: erst Greifer (6), dann zurück bis zur Schulter (1).
Beschriften! Einen Aufkleber mit der ID auf den Servo kleben spart später viel Suchen.

Das Skript ändert nur die ID, **nicht** die Baudrate. Antwortet ein Servo nur bei einer
anderen Baudrate, meldet es das und bricht ab (die Baudrate-Umstellung kommt mit dem
Assistenten in der Web-App).

Sicherheit: Nur mit einem Servo am Bus. Servo vorher nicht mechanisch einbauen oder belasten.

LeRobot-Werkzeug dafür: `lerobot-setup-motors --robot.type=so101_follower --robot.port=<Port>`
(führt durch alle Motoren nacheinander und setzt auch die Baudrate).
"""

from __future__ import annotations

import argparse

from so101_lab import registers as regs
from so101_lab.backends import create_backend
from so101_lab.backends.base import DEFAULT_BAUDRATE, BackendError
from so101_lab.config import load_config
from so101_lab.devices import get_device_spec
from so101_lab.script_support import add_device_arguments, confirm, resolve_port, run_script


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Servo-ID setzen (EEPROM, genau ein Servo am Bus).")
    add_device_arguments(parser)
    parser.add_argument("--gelenk", required=True, help="Gelenk, dessen ID der Servo bekommen soll")
    args = parser.parse_args(argv)

    cfg = load_config(args.config).device(args.device)
    spec = get_device_spec(cfg.type)
    target = spec.joint(args.gelenk)
    port = resolve_port(args)
    backend = create_backend(cfg, port)

    with backend:  # schließt den Port am Ende, auch bei Fehlern
        # Schritt 1: Bus absuchen. Zuerst die Standard-Baudrate, dann alle (dauert ca. 8 s).
        print(f"Suche Servos an {port or args.device} bei {DEFAULT_BAUDRATE} Baud …")
        found = backend.scan()
        if not found:
            print("Nichts gefunden, suche bei allen Baudraten (ca. 8 s) …")
            found = backend.scan(baudrates=sorted(regs.baudrates(), reverse=True))
        if not found:
            raise BackendError("Kein Servo antwortet. Servo-Netzteil an? Kabel gesteckt?")
        if len(found) > 1:
            ids = ", ".join(str(s.id) for s in found)
            raise BackendError(
                f"Es antworten {len(found)} Servos (IDs {ids}). Für die ID-Vergabe darf nur EIN Servo "
                "am Bus hängen. Alle anderen abziehen und noch einmal starten."
            )
        servo = found[0]
        print(
            f"Gefunden: ID {servo.id}, {servo.model_name}, {servo.baudrate} Baud, "
            f"Firmware {servo.firmware or '?'}"
        )
        if servo.baudrate != DEFAULT_BAUDRATE:
            raise BackendError(
                f"Der Servo antwortet bei {servo.baudrate} Baud statt {DEFAULT_BAUDRATE}. "
                "Dieses Skript ändert nur die ID. Die Baudrate bitte mit lerobot-setup-motors oder "
                "dem Assistenten in der Web-App umstellen."
            )

        # Schritt 2: Soll-ID aus der Gelenktabelle
        if servo.id == target.motor_id:
            print(f"Der Servo hat schon die ID {target.motor_id} ({target.name}). Nichts zu tun.")
            return 0
        print(
            f"\nGeplant: ID {servo.id} → {target.motor_id} ({target.name}). "
            "Das wird dauerhaft in den Servo geschrieben."
        )
        if not confirm("ID jetzt schreiben?"):
            print("Abgebrochen, nichts geschrieben.")
            return 1

        # Schritt 3: change_id() schreibt und prüft. Fehler kommen als Ausnahme zurück.
        backend.change_id(servo.id, target.motor_id)
        print(f"Fertig und geprüft: Der Servo antwortet unter ID {target.motor_id}, die alte ID ist frei.")
        print(f"Jetzt beschriften: „{target.motor_id} {target.name}“. Dann den nächsten Servo anschließen.")
    return 0


if __name__ == "__main__":
    run_script(main)
