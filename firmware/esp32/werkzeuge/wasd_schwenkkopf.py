# /// script
# requires-python = ">=3.10,<3.14"
# dependencies = ["pyserial", "pygame-ce"]
# ///
"""Beispiel Positionsmodus: zwei Servos per WASD schwenken (z. B. Kamerakopf).

Zwei STS3215-Servos im Positionsmodus. Solange eine Taste gedrückt ist, wandert
die Sollposition gleichmäßig weiter. Lässt man los, bleibt der Servo stehen und hält.

  A / D      Servo SCHWENK_ID nach links / rechts
  W / S      Servo NEIGE_ID hoch / runter
  1 / 2 / 3  langsam / mittel / schnell
  R          zurück zur Startstellung
  Leertaste  STOPP (Servos halten die aktuelle Stellung)
  Esc        beenden (Servos halten weiter, Drehmoment bleibt an)

Aufruf aus dem Ordner esp32_funkknoten:

  uv run werkzeuge/wasd_schwenkkopf.py --ip so101-1a2b.local
  uv run werkzeuge/wasd_schwenkkopf.py --gateway COM5        # über ESP-NOW

Die Servo-IDs müssen im Profil des Knotens im Positionsmodus stehen. Die IDs 1
und 2 passen zu den Profilen lekiwi und so101 (dort Schulter des Arms, also
vorsichtig und mit freiem Bewegungsraum ausprobieren).

Beim Start liest das Skript die aktuelle Stellung und fährt von dort aus los. So
springt kein Servo beim Einschalten des Drehmoments. Jeder Servo darf sich nur
SPIELRAUM Schritte von dieser Startstellung entfernen.
"""

from __future__ import annotations

import argparse
import sys

import pygame

from knoten_test import GatewayVerbindung, Knoten, UdpVerbindung, mac_lesen

# ─── An den eigenen Aufbau anpassen ─────────────────────────────────────
SCHWENK_ID = 1  # A/D
NEIGE_ID = 2    # W/S
SCHWENK_UMGEDREHT = False  # Dreht der Servo bei A nach rechts: umstellen
NEIGE_UMGEDREHT = False
SPIELRAUM = 600  # Schritte je Richtung ab Startstellung, 4096 Schritte = 360°, 600 ≈ 53°
STUFEN = {"langsam": 300, "mittel": 800, "schnell": 1600}  # Schritt/s, mit der die Sollposition wandert
TAKT = 0.05


def verbinden(a) -> Knoten:
    if a.gateway:
        v = GatewayVerbindung(a.gateway, a.mac)
        knoten = Knoten(v)
        if a.mac is None:
            gefunden = knoten.suchen(2.5)
            if not gefunden:
                sys.exit("Kein Knoten hinter dem Gateway gefunden")
            v.mac = mac_lesen(gefunden[0][1])
        return knoten
    knoten = Knoten(UdpVerbindung(a.ip))
    info = knoten.info()
    if not info:
        sys.exit(f"{a.ip} antwortet nicht. Gleiches WLAN? Firewall für UDP 4210 offen?")
    print(f"Knoten {info['name']}, Profil {info['profil']}")
    return knoten


def startstellung(knoten: Knoten) -> dict[int, int]:
    """Aktuelle Position beider Servos lesen (ohne Steuerrecht zu übernehmen)."""
    z = knoten.zustand()
    if z is None:
        sys.exit("keine Antwort vom Knoten")
    ist = {s["id"]: s for s in z["servos"]}
    for sid in (SCHWENK_ID, NEIGE_ID):
        if sid not in ist:
            sys.exit(f"Servo {sid} steht nicht im Profil des Knotens")
        if ist[sid]["tempomodus"]:
            sys.exit(f"Servo {sid} ist im Profil im Tempomodus, hier wird der Positionsmodus gebraucht")
        if not ist[sid]["online"]:
            sys.exit(f"Servo {sid} antwortet nicht (Kabel, Versorgung, ID?)")
    return {sid: ist[sid]["position"] for sid in (SCHWENK_ID, NEIGE_ID)}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    weg = ap.add_mutually_exclusive_group(required=True)
    weg.add_argument("--ip", help="Knoten im WLAN (IP oder name.local)")
    weg.add_argument("--gateway", help="serieller Port des Gateway-ESP (COM5, /dev/ttyUSB0)")
    ap.add_argument("--mac", type=mac_lesen, help="Knoten hinter dem Gateway, sonst der erste, der antwortet")
    a = ap.parse_args()

    knoten = verbinden(a)
    start = startstellung(knoten)
    soll = {sid: float(p) for sid, p in start.items()}
    print(f"Startstellung {start}")

    pygame.init()
    fenster = pygame.display.set_mode((480, 200))
    pygame.display.set_caption("Schwenkkopf")
    schrift = pygame.font.SysFont("consolas,dejavusansmono,monospace", 18)
    uhr = pygame.time.Clock()

    stufe, angehalten, meldung, ist = "langsam", False, "", {}
    try:
        laeuft = True
        while laeuft:
            for e in pygame.event.get():
                if e.type == pygame.QUIT or (e.type == pygame.KEYDOWN and e.key == pygame.K_ESCAPE):
                    laeuft = False
                elif e.type == pygame.KEYDOWN and e.key == pygame.K_SPACE:
                    knoten.stopp()
                    angehalten = True
                elif e.type == pygame.KEYDOWN and e.key == pygame.K_r:
                    soll = {sid: float(p) for sid, p in start.items()}
                    angehalten = False
                elif e.type == pygame.KEYDOWN and e.key in (pygame.K_1, pygame.K_2, pygame.K_3):
                    stufe = list(STUFEN)[e.key - pygame.K_1]

            if pygame.key.get_focused():
                t = pygame.key.get_pressed()
                schwenk = t[pygame.K_a] - t[pygame.K_d]
                neige = t[pygame.K_w] - t[pygame.K_s]
            else:
                schwenk = neige = 0
            if schwenk or neige:
                angehalten = False

            # Sollposition weiterschieben, innerhalb des Spielraums und des Servobereichs 0..4095
            schritt = STUFEN[stufe] * TAKT
            for sid, richtung, umgedreht in ((SCHWENK_ID, schwenk, SCHWENK_UMGEDREHT),
                                             (NEIGE_ID, neige, NEIGE_UMGEDREHT)):
                neu = soll[sid] + (-1 if umgedreht else 1) * richtung * schritt
                soll[sid] = max(start[sid] - SPIELRAUM, 0, min(start[sid] + SPIELRAUM, 4095, neu))

            if not angehalten:
                z = knoten.befehl({sid: (True, int(round(p))) for sid, p in soll.items()})
                if z is None:
                    meldung = "keine Antwort vom Knoten"
                elif z["belegt"]:
                    meldung = "BELEGT, eine andere Quelle steuert"
                else:
                    meldung = "verbunden"
                    ist = {s["id"]: s["position"] for s in z["servos"]}
            else:
                meldung = "STOPP, Servos halten. Mit WASD oder R weiter"

            fenster.fill((25, 25, 30))
            for i, text in enumerate([
                "A/D schwenken   W/S neigen   R Start",
                "1/2/3 Tempo   Leertaste STOPP   Esc Ende",
                "",
                f"Stufe {stufe} ({STUFEN[stufe]} Schritt/s)",
                "   ".join(f"ID {sid}: soll {int(p):4d} ist {ist.get(sid, '?'):>4}" for sid, p in soll.items()),
                meldung,
            ]):
                fenster.blit(schrift.render(text, True, (230, 230, 230)), (16, 14 + 28 * i))
            pygame.display.flip()
            uhr.tick(1 / TAKT)
    except KeyboardInterrupt:
        pass
    finally:
        knoten.stopp()  # Servos halten die Stellung, Steuerrecht ist frei
        pygame.quit()
        print("STOPP gesendet. Drehmoment aus: uv run werkzeuge/knoten_test.py --ip ... drehmoment-aus")


if __name__ == "__main__":
    main()
