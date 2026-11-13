"""Kiwi-Kinematik der LeKiwi (drei Allseitenräder im 120°-Abstand).

Rechnet einen Fahrbefehl (vx, vy in m/s, omega in °/s) in das Tempo der drei
Radservos um. Formel wie in LeRobot (lerobot/robots/lekiwi/lekiwi.py,
_body_to_wheel_raw und _degps_to_raw).

Koordinaten: x vorn, y links, omega gegen den Uhrzeigersinn positiv.
Radius und Abstand sind die Standardwerte aus LeRobot. Für einen anderen
Aufbau hier anpassen.
"""

from __future__ import annotations

import math

# Antriebsrichtung jedes Rads in Grad (x vorn, y links)
KIWI_ANTRIEB = {"base_left_wheel": 150.0, "base_back_wheel": -90.0, "base_right_wheel": 30.0}
KIWI_RADRADIUS = 0.05  # m
KIWI_ABSTAND = 0.125   # m, Radmitte bis Mittelpunkt der Plattform

# Servo-IDs der Räder (Profil lekiwi der Firmware)
KIWI_SERVO_IDS = {"base_left_wheel": 7, "base_back_wheel": 8, "base_right_wheel": 9}
SCHRITTE_JE_GRAD = 4096 / 360  # STS3215: 4096 Schritte je Umdrehung


def kiwi_raeder(vx: float, vy: float, omega: float) -> dict[str, float]:
    """Fahrbefehl (m/s, m/s, °/s) → Drehzahl jedes Rads in °/s."""
    w = math.radians(omega)
    return {
        rad: math.degrees((math.cos(math.radians(a)) * vx + math.sin(math.radians(a)) * vy + KIWI_ABSTAND * w)
                          / KIWI_RADRADIUS)
        for rad, a in KIWI_ANTRIEB.items()
    }


def kiwi_schritte(vx: float, vy: float, omega: float, max_tempo: int = 3000) -> dict[int, int]:
    """Fahrbefehl → Tempo je Servo-ID in Schritt/s. Überschreitet ein Rad max_tempo, werden alle drei
    im gleichen Verhältnis kleiner, damit die Fahrtrichtung bleibt (wie in LeRobot)."""
    roh = {KIWI_SERVO_IDS[rad]: g * SCHRITTE_JE_GRAD for rad, g in kiwi_raeder(vx, vy, omega).items()}
    groesste = max(abs(v) for v in roh.values())
    faktor = max_tempo / groesste if groesste > max_tempo else 1.0
    return {sid: int(round(v * faktor)) for sid, v in roh.items()}
