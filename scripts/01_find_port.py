"""01 – Den seriellen Port des Servo-Adapters finden.

Aufruf:
    uv run scripts/01_find_port.py              zeigt alle USB-Seriell-Ports
    uv run scripts/01_find_port.py --vergleich  Adapter abziehen und die Differenz bilden
    uv run scripts/01_find_port.py --alle       auch Ports ohne USB-Kennung zeigen

Was hier passiert:
Der Servo-Adapter (USB-Seriell-Wandler) meldet sich beim Anstecken als Port an:
  Linux    /dev/ttyACM0 oder /dev/ttyUSB0
  Windows  COM3, COM4, …
  macOS    /dev/cu.usbmodem…
Die Nummer hängt von der Reihenfolge des Ansteckens ab und kann sich ändern. Deshalb
wird der Port bei jedem Aufruf gewählt (`--port` oder Auswahl aus dieser Liste).
Feste Namen wären über udev-Regeln möglich, sind hier aber noch nicht eingerichtet.

Dieses Skript sendet nichts an den Adapter. Es liest nur die Geräteliste des Systems.

LeRobot-Werkzeug dafür: `lerobot-find-port`. Es nutzt dieselbe Idee wie `--vergleich`:
Kabel abziehen, Liste vergleichen, der verschwundene Port ist der gesuchte.
"""

from __future__ import annotations

import argparse
import time

from so101_lab.ports import list_serial_ports
from so101_lab.script_support import run_script


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Serielle Ports auflisten.")
    parser.add_argument("--vergleich", action="store_true", help="Adapter abziehen und Differenz bilden")
    parser.add_argument("--alle", action="store_true", help="auch Ports ohne USB-Kennung zeigen")
    args = parser.parse_args(argv)

    ports = list_serial_ports(usb_only=not args.alle)
    if not ports:
        print("Kein Port gefunden. Adapter angesteckt? Servo-Netzteil ist dafür nicht nötig.")
        return 1
    print("Gefundene Ports:")
    for p in ports:
        print(f"  {p.label}")

    if args.vergleich:
        # 1. Liste merken, 2. Adapter abziehen lassen, 3. Liste erneut lesen, 4. Differenz.
        before = {p.device: p for p in ports}
        input("\nAdapter jetzt vom Rechner abziehen, dann Enter: ")
        time.sleep(1.0)  # dem System Zeit lassen, den Port zu entfernen
        after = {p.device for p in list_serial_ports(usb_only=not args.alle)}
        gone = [before[d] for d in before if d not in after]
        if len(gone) == 1:
            print(f"\nDer Adapter war: {gone[0].label}")
            print("Jetzt wieder anstecken und den Port bei den anderen Skripten mit --port angeben.")
        elif not gone:
            print("\nEs ist kein Port verschwunden. Wurde der richtige Stecker gezogen?")
            return 1
        else:
            print("\nMehrere Ports sind verschwunden, bitte nur einen Adapter abziehen:")
            for p in gone:
                print(f"  {p.label}")
            return 1
    else:
        print("\nTipp: Bei mehreren Adaptern hilft --vergleich, oder die Seriennummer [SN …].")
    return 0


if __name__ == "__main__":
    run_script(main)
