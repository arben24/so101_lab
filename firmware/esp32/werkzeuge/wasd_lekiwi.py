# /// script
# requires-python = ">=3.10,<3.14"
# dependencies = ["pyserial", "pygame-ce"]
# ///
"""LeKiwi mit der Tastatur fahren, direkt über den ESP32-Knoten.

Öffnet ein kleines Fenster. Solange es den Fokus hat, gilt:

  W / S      vorwärts / rückwärts
  A / D      seitlich nach links / rechts
  Q / E      auf der Stelle nach links / rechts drehen
  1 / 2 / 3  langsam / mittel / schnell
  Leertaste  STOPP (Räder stehen, Steuerrecht frei)
  Esc        beenden

Verliert das Fenster den Fokus, stehen die Räder. Bricht das Skript ab, greift der
Watchdog des Knotens.

Aufruf aus dem Ordner esp32_funkknoten:

  uv run werkzeuge/wasd_lekiwi.py --ip lekiwi-3fa2.local
  uv run werkzeuge/wasd_lekiwi.py --gateway COM5          # über ESP-NOW

Kiwi-Formel und Umrechnung in Servo-Schritte stehen in kiwi.py.
"""

from __future__ import annotations

import argparse
import sys
import time

import pygame

from kiwi import kiwi_schritte
from knoten_test import GatewayVerbindung, Knoten, UdpVerbindung, mac_lesen

MAX_TEMPO = 3000  # Schritt/s, wie die Grenze auf der Webseite des Knotens (Standard)
STUFEN = {  # Name: (m/s, °/s), wie die Tastatursteuerung in LeRobot
    "langsam": (0.1, 30.0),
    "mittel": (0.2, 60.0),
    "schnell": (0.3, 90.0),
}
TAKT = 0.05  # 20 Hz


def fahrbefehl(tasten, stufe: str) -> tuple[float, float, float]:
    v, w = STUFEN[stufe]
    vx = v * (tasten[pygame.K_w] - tasten[pygame.K_s])
    vy = v * (tasten[pygame.K_a] - tasten[pygame.K_d])
    omega = w * (tasten[pygame.K_q] - tasten[pygame.K_e])
    return vx, vy, omega


def verbinden(a) -> Knoten:
    if a.gateway:
        v = GatewayVerbindung(a.gateway, a.mac)
        knoten = Knoten(v)
        if a.mac is None:
            gefunden = knoten.suchen(2.5)
            if not gefunden:
                sys.exit("Kein Knoten hinter dem Gateway gefunden")
            v.mac = mac_lesen(gefunden[0][1])
            print(f"Knoten {gefunden[0][0]['name']} ({gefunden[0][1]})")
        return knoten
    knoten = Knoten(UdpVerbindung(a.ip))
    info = knoten.info()
    if not info:
        sys.exit(f"{a.ip} antwortet nicht. Gleiches WLAN? Firewall für UDP 4210 offen?")
    print(f"Knoten {info['name']}, Profil {info['profil']}, Betrieb {info['betrieb']}")
    return knoten


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    weg = ap.add_mutually_exclusive_group(required=True)
    weg.add_argument("--ip", help="Knoten im WLAN (IP oder name.local)")
    weg.add_argument("--gateway", help="serieller Port des Gateway-ESP (COM5, /dev/ttyUSB0)")
    ap.add_argument("--mac", type=mac_lesen, help="Knoten hinter dem Gateway, sonst der erste, der antwortet")
    ap.add_argument("--stufe", choices=STUFEN, default="langsam")
    a = ap.parse_args()

    knoten = verbinden(a)

    pygame.init()
    fenster = pygame.display.set_mode((560, 300))
    pygame.display.set_caption("LeKiwi fahren")
    schrift = pygame.font.SysFont("consolas,dejavusansmono,monospace", 18)
    uhr = pygame.time.Clock()

    stufe, angehalten = a.stufe, False
    letzte_antwort, z = 0.0, None
    try:
        laeuft = True
        while laeuft:
            for e in pygame.event.get():
                if e.type == pygame.QUIT or (e.type == pygame.KEYDOWN and e.key == pygame.K_ESCAPE):
                    laeuft = False
                elif e.type == pygame.KEYDOWN:
                    if e.key == pygame.K_SPACE:
                        knoten.stopp()
                        angehalten = True
                    elif e.key in (pygame.K_1, pygame.K_2, pygame.K_3):
                        stufe = list(STUFEN)[e.key - pygame.K_1]

            tasten = pygame.key.get_pressed()
            vx, vy, omega = fahrbefehl(tasten, stufe) if pygame.key.get_focused() else (0.0, 0.0, 0.0)
            bewegt = bool(vx or vy or omega)
            if bewegt:
                angehalten = False
            roh = kiwi_schritte(vx, vy, omega, MAX_TEMPO)

            if not angehalten:
                # Auch im Stand senden: Tempo 0 hält die Räder und füttert den Watchdog
                antwort = knoten.befehl({sid: (True, wert) for sid, wert in roh.items()})
                if antwort:
                    z, letzte_antwort = antwort, time.monotonic()

            verbunden = time.monotonic() - letzte_antwort < 0.5
            zeilen = [
                ("W/S vor/zurück   A/D seitlich   Q/E drehen", (160, 160, 160)),
                ("1/2/3 Tempo   Leertaste STOPP   Esc Ende", (160, 160, 160)),
                ("", None),
                (f"Stufe {stufe}: {STUFEN[stufe][0]} m/s, {STUFEN[stufe][1]:.0f} °/s", (230, 230, 230)),
                (f"vx {vx:+.2f} m/s   vy {vy:+.2f} m/s   omega {omega:+.0f} °/s", (230, 230, 230)),
                ("Soll  " + "   ".join(f"{sid}: {w:+5d}" for sid, w in roh.items()), (230, 230, 230)),
            ]
            if z:
                ist = {s["id"]: s for s in z["servos"]}
                zeilen.append(("Ist   " + "   ".join(
                    f"{sid}: {ist[sid]['tempo']:+5d}" if sid in ist and ist[sid]["online"] else f"{sid}: fehlt"
                    for sid in roh), (230, 230, 230)))
                spannung = [ist[sid]["spannung"] for sid in roh if sid in ist and ist[sid]["online"]]
                if spannung:
                    zeilen.append((f"Akku  {min(spannung):.1f} V   Zyklus {z['zyklus_us']} µs   "
                                   f"Busfehler {z['busfehler']}", (230, 230, 230)))
            zeilen.append(("", None))
            if angehalten:
                zeilen.append(("STOPP, mit W/A/S/D/Q/E weiterfahren", (255, 190, 60)))
            elif not verbunden:
                zeilen.append(("keine Antwort vom Knoten", (255, 90, 90)))
            elif z and z["belegt"]:
                zeilen.append(("BELEGT, eine andere Quelle steuert", (255, 90, 90)))
            elif not pygame.key.get_focused():
                zeilen.append(("Fenster ohne Fokus, Räder stehen", (255, 190, 60)))
            else:
                zeilen.append(("verbunden" + (", fährt" if bewegt else ""), (90, 220, 120)))

            fenster.fill((25, 25, 30))
            for i, (text, farbe) in enumerate(zeilen):
                if text:
                    fenster.blit(schrift.render(text, True, farbe), (16, 14 + 24 * i))
            pygame.display.flip()
            uhr.tick(1 / TAKT)
    except KeyboardInterrupt:
        pass
    finally:
        knoten.stopp()
        pygame.quit()
        print("STOPP gesendet")


if __name__ == "__main__":
    main()
