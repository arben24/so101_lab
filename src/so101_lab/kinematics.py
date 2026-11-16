"""Kinematik der LeKiwi-Basis: Fahrbefehl (x, y, Drehung) ↔ Tempo der drei Räder.

Die LeKiwi fährt auf drei **Allseitenrädern** (Omni-Wheels), die im Abstand von 120° um die
Mitte sitzen. Ein Allseitenrad treibt nur in seiner Laufrichtung an, quer dazu rollen die
kleinen Rollen am Umfang frei. Deshalb kann die Basis in jede Richtung fahren und sich
gleichzeitig drehen.

Koordinaten (wie LeRobot): x = vorwärts, y = nach links, θ = Drehung gegen den Uhrzeigersinn
(von oben gesehen). x und y in m/s, θ in Grad/s.

Für jedes Rad gilt (a = Winkel seiner Laufrichtung, R = Abstand Mitte–Rad, r = Radradius):

    Bahntempo des Rads  v = cos(a) · x + sin(a) · y + R · θ        (θ hier in rad/s)
    Drehtempo des Rads  ω = v / r                                   (rad/s)
    Rohwert             ω in Grad/s · 4096/360                      (Schritt/s, Register goal_velocity)

Als Matrix: [v_links, v_hinten, v_rechts] = M · [x, y, θ]. Rückwärts (aus gemessenen
Radtempi die Bewegung der Basis) rechnet man mit der inversen Matrix M⁻¹.

Übersteigt ein Rad das Höchsttempo (`max_raw`), werden **alle drei im gleichen Verhältnis**
verkleinert. So bleibt die Fahrtrichtung erhalten, die Basis fährt nur langsamer.

Quelle: `lerobot/robots/lekiwi/lekiwi.py`, `_body_to_wheel_raw` und `_wheel_raw_to_body`
(LeRobot 0.7.0). Die Rechnung hier ist dieselbe, Schritt für Schritt; ein Test vergleicht
beide. Maße und Winkel sind LeRobots Standardwerte für die LeKiwi.
"""

from __future__ import annotations

import numpy as np

WHEEL_RADIUS_M = 0.05  # Radradius r
BASE_RADIUS_M = 0.125  # Abstand Mitte–Rad R
STEPS_PER_DEGREE = 4096.0 / 360.0  # Encoder: 4096 Schritt je Umdrehung
MAX_RAW = 0x7FFF  # goal_velocity: Betrag mit Vorzeichen in Bit 15

# Reihenfolge der Räder in allen Listen und Matrizen
WHEELS = ("base_left_wheel", "base_back_wheel", "base_right_wheel")

# Laufrichtung je Rad in Grad, gemessen von der x-Achse (vorwärts) gegen den Uhrzeigersinn.
# LeRobot schreibt das als [240, 0, 120] − 90, also links 150°, hinten −90°, rechts 30°.
WHEEL_ANGLES_DEG = tuple(float(a - 90) for a in (240, 0, 120))

# Tempostufen wie im Tastatur-Client von LeRobot (lekiwi_client.py): langsam, mittel, schnell
SPEED_LEVELS = (
    {"xy": 0.1, "theta": 30.0},
    {"xy": 0.2, "theta": 60.0},
    {"xy": 0.3, "theta": 90.0},
)

BODY_KEYS = ("x.vel", "y.vel", "theta.vel")  # Namen wie in LeRobots Aktion und Beobachtung


def kinematic_matrix(base_radius: float = BASE_RADIUS_M) -> np.ndarray:
    """M: eine Zeile je Rad, (cos a, sin a, R). M · [x, y, θ] = Bahntempo der Räder in m/s."""
    angles = np.radians(np.array(WHEEL_ANGLES_DEG))
    return np.array([[np.cos(a), np.sin(a), base_radius] for a in angles])


def degps_to_raw(degps: float) -> int:
    """Drehtempo in Grad/s → Rohwert in Schritt/s (gerundet, begrenzt auf ±32767)."""
    raw = int(round(degps * STEPS_PER_DEGREE))
    return max(-MAX_RAW, min(MAX_RAW, raw))


def raw_to_degps(raw: int) -> float:
    """Rohwert in Schritt/s → Drehtempo in Grad/s."""
    return raw / STEPS_PER_DEGREE


def body_to_wheels(
    x: float,
    y: float,
    theta: float,
    *,
    max_raw: int = 3000,
    wheel_radius: float = WHEEL_RADIUS_M,
    base_radius: float = BASE_RADIUS_M,
) -> dict[str, int]:
    """Fahrbefehl → Rohtempo je Rad (Schritt/s).

    x, y in m/s, theta in Grad/s. Liefert {"base_left_wheel": …, "base_back_wheel": …,
    "base_right_wheel": …}. Ist ein Rad schneller als `max_raw`, werden alle drei
    im gleichen Verhältnis verkleinert.
    """
    body = np.array([x, y, theta * (np.pi / 180.0)])  # Drehung in rad/s
    wheel_linear = kinematic_matrix(base_radius).dot(body)  # m/s am Radumfang
    wheel_degps = (wheel_linear / wheel_radius) * (180.0 / np.pi)  # Grad/s des Rads

    fastest = max(abs(d) * STEPS_PER_DEGREE for d in wheel_degps)
    if fastest > max_raw:
        wheel_degps = wheel_degps * (max_raw / fastest)  # alle gleich verkleinern

    return {name: degps_to_raw(d) for name, d in zip(WHEELS, wheel_degps, strict=True)}


def wheels_to_body(
    wheels: dict[str, int],
    *,
    wheel_radius: float = WHEEL_RADIUS_M,
    base_radius: float = BASE_RADIUS_M,
) -> dict[str, float]:
    """Rohtempo der Räder (z. B. gemessen, `present_velocity`) → Bewegung der Basis.

    Liefert {"x.vel": m/s, "y.vel": m/s, "theta.vel": Grad/s} wie LeRobots Beobachtung.
    """
    wheel_radps = np.array([raw_to_degps(wheels[name]) for name in WHEELS]) * (np.pi / 180.0)
    wheel_linear = wheel_radps * wheel_radius
    x, y, theta_rad = np.linalg.inv(kinematic_matrix(base_radius)).dot(wheel_linear)
    return {"x.vel": float(x), "y.vel": float(y), "theta.vel": float(theta_rad * (180.0 / np.pi))}
