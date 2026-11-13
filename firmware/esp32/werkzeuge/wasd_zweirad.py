# /// script
# requires-python = ">=3.10,<3.14"
# dependencies = ["pyserial", "pygame-ce"]
# ///
"""Minimalbeispiel: ein Fahrzeug mit zwei Rädern (Differentialantrieb) per WASD fahren.

Gedacht als Vorlage für eigene Projekte. Zwei STS3215-Servos im Tempomodus
treiben das linke und das rechte Rad an. Solange das Fenster den Fokus hat:

  W / S      vorwärts / rückwärts
  A / D      nach links / rechts drehen (zusammen mit W oder S: Kurve)
  1 / 2 / 3  langsam / mittel / schnell
  Leertaste  STOPP
  Esc        beenden

Aufruf aus dem Ordner esp32_funkknoten:

  uv run werkzeuge/wasd_zweirad.py --ip lekiwi-3fa2.local
  uv run werkzeuge/wasd_zweirad.py --gateway COM5            # über ESP-NOW

Die Servo-IDs müssen im Profil des Knotens im Tempomodus stehen. Ohne eigenes
Profil passen die Räder 7 und 9 des Profils lekiwi (zum Ausprobieren auf dem Tisch).
"""

from __future__ import annotations

import argparse
import sys

import pygame

from knoten_test import GatewayVerbindung, Knoten, UdpVerbindung, mac_lesen

# ─── An den eigenen Aufbau anpassen ─────────────────────────────────────
LINKS_ID = 7
RECHTS_ID = 9
# Die Servos sitzen meist spiegelbildlich. Dann muss ein Rad andersherum drehen,
# damit beide das Fahrzeug nach vorn schieben. Fährt es bei W im Kreis: umstellen.
LINKS_UMGEDREHT = False
RECHTS_UMGEDREHT = True
STUFEN = {"langsam": 500, "mittel": 1200, "schnell": 2500}  # Schritt/s am Rad, 4096 Schritte = 1 Umdrehung
DREHANTEIL = 0.6  # A/D: wie stark gelenkt wird, bezogen auf das Tempo der Stufe
TAKT = 0.05       # 20 Hz, deutlich schneller als der Watchdog (Standard 300 ms)


def radtempo(tasten, tempo: int) -> tuple[int, int]:
    """Tastenzustand → (links, rechts) in Schritt/s, vorwärts positiv."""
    vor = tasten[pygame.K_w] - tasten[pygame.K_s]       # +1, 0, −1
    dreh = tasten[pygame.K_a] - tasten[pygame.K_d]      # +1 = nach links
    links = tempo * (vor - DREHANTEIL * dreh)
    rechts = tempo * (vor + DREHANTEIL * dreh)
    return int(links), int(rechts)


def verbinden(a) -> Knoten:
    if a.gateway:
        v = GatewayVerbindung(a.gateway, a.mac)
        knoten = Knoten(v)
        if a.mac is None:  # ersten Knoten nehmen, der antwortet
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


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    weg = ap.add_mutually_exclusive_group(required=True)
    weg.add_argument("--ip", help="Knoten im WLAN (IP oder name.local)")
    weg.add_argument("--gateway", help="serieller Port des Gateway-ESP (COM5, /dev/ttyUSB0)")
    ap.add_argument("--mac", type=mac_lesen, help="Knoten hinter dem Gateway, sonst der erste, der antwortet")
    a = ap.parse_args()

    knoten = verbinden(a)

    pygame.init()
    fenster = pygame.display.set_mode((480, 200))
    pygame.display.set_caption("Zweirad fahren")
    schrift = pygame.font.SysFont("consolas,dejavusansmono,monospace", 18)
    uhr = pygame.time.Clock()

    stufe, angehalten, meldung = "langsam", False, ""
    try:
        laeuft = True
        while laeuft:
            for e in pygame.event.get():
                if e.type == pygame.QUIT or (e.type == pygame.KEYDOWN and e.key == pygame.K_ESCAPE):
                    laeuft = False
                elif e.type == pygame.KEYDOWN and e.key == pygame.K_SPACE:
                    knoten.stopp()
                    angehalten = True
                elif e.type == pygame.KEYDOWN and e.key in (pygame.K_1, pygame.K_2, pygame.K_3):
                    stufe = list(STUFEN)[e.key - pygame.K_1]

            # Ohne Fokus keine Tasten: sonst fährt das Fahrzeug weiter, wenn man ins Terminal klickt
            if pygame.key.get_focused():
                links, rechts = radtempo(pygame.key.get_pressed(), STUFEN[stufe])
            else:
                links, rechts = 0, 0
            if links or rechts:
                angehalten = False

            if not angehalten:
                # Jeden Takt senden, auch Tempo 0. Das hält den Watchdog des Knotens ruhig.
                z = knoten.befehl({
                    LINKS_ID: (True, -links if LINKS_UMGEDREHT else links),
                    RECHTS_ID: (True, -rechts if RECHTS_UMGEDREHT else rechts),
                })
                if z is None:
                    meldung = "keine Antwort vom Knoten"
                elif z["belegt"]:
                    meldung = "BELEGT, eine andere Quelle steuert"
                else:
                    meldung = "verbunden"
            else:
                meldung = "STOPP, mit W/A/S/D weiterfahren"

            fenster.fill((25, 25, 30))
            for i, text in enumerate([
                "W/S vor/zurück   A/D drehen",
                "1/2/3 Tempo   Leertaste STOPP   Esc Ende",
                "",
                f"Stufe {stufe} ({STUFEN[stufe]} Schritt/s)",
                f"links {links:+5d}   rechts {rechts:+5d}",
                meldung,
            ]):
                fenster.blit(schrift.render(text, True, (230, 230, 230)), (16, 14 + 28 * i))
            pygame.display.flip()
            uhr.tick(1 / TAKT)
    except KeyboardInterrupt:
        pass
    finally:
        knoten.stopp()  # Räder stehen sofort, nicht erst nach dem Watchdog
        pygame.quit()
        print("STOPP gesendet")


if __name__ == "__main__":
    main()
