"""Übung 6 – Kinematik der LeKiwi-Basis selbst rechnen (Musterlösung).

Aufruf:
    uv run exercises/loesungen/uebung_06_kinematik.py

Keine Hardware nötig. Das Programm vergleicht deine Rechnung mit `so101_lab.kinematics`.
"""

from __future__ import annotations

import math

from so101_lab import kinematics
from so101_lab.script_support import run_script

R = 0.125  # Abstand Mitte–Rad in m
RADIUS = 0.05  # Radradius in m
ANGLES = (150.0, -90.0, 30.0)  # Laufrichtung von links, hinten, rechts in Grad
STEPS_PER_DEGREE = 4096 / 360
WHEELS = ("base_left_wheel", "base_back_wheel", "base_right_wheel")


def wheel_speeds(x: float, y: float, theta: float) -> dict[str, int]:
    """Radtempi in Schritt/s für x, y in m/s und theta in Grad/s, ohne Begrenzung."""
    theta_rad = math.radians(theta)
    speeds = {}
    for name, angle in zip(WHEELS, ANGLES, strict=True):
        a = math.radians(angle)
        v = math.cos(a) * x + math.sin(a) * y + R * theta_rad  # Bahntempo am Radumfang (m/s)
        omega_deg = math.degrees(v / RADIUS)  # Drehtempo des Rads (Grad/s)
        speeds[name] = round(omega_deg * STEPS_PER_DEGREE)
    return speeds


def limit_speeds(speeds: dict[str, int], max_raw: int = 3000) -> dict[str, int]:
    """Ist ein Rad schneller als `max_raw`, alle drei im gleichen Verhältnis verkleinern."""
    fastest = max(abs(v) for v in speeds.values())
    if fastest <= max_raw:
        return dict(speeds)
    return {name: round(v * max_raw / fastest) for name, v in speeds.items()}


def circle_command(radius_m: float, speed_ms: float) -> tuple[float, float, float]:
    """Kreisfahrt, bei der die Basis immer nach vorn zeigt: (x, y, theta).

    Die Basis fährt mit `speed_ms` vorwärts und dreht sich dabei so schnell, dass sie nach
    einem Umlauf wieder in die Ausgangsrichtung zeigt: ω = v / r (rad/s).
    """
    return speed_ms, 0.0, math.degrees(speed_ms / radius_m)


def max_forward_speed(max_raw: int = 3000) -> float:
    """Größte Vorwärtsgeschwindigkeit (m/s), bei der kein Rad über `max_raw` Schritt/s kommt."""
    per_ms = abs(math.cos(math.radians(ANGLES[0]))) / RADIUS  # Drehtempo (rad/s) je m/s
    raw_per_ms = math.degrees(per_ms) * STEPS_PER_DEGREE  # Schritt/s je m/s
    return max_raw / raw_per_ms


def main(argv: list[str] | None = None) -> int:
    cases = [(0.1, 0, 0), (0, 0.1, 0), (0, 0, 30), (0.1, 0.1, 20), (-0.1, 0.05, -30)]
    print(f"{'Fahrbefehl (x, y, θ)':<26}{'deine Räder':>28}   Repo")
    ok = True
    for case in cases:
        mine = wheel_speeds(*case)
        repo = kinematics.body_to_wheels(*case)
        same = all(abs(mine[w] - repo[w]) <= 1 for w in WHEELS)
        ok &= same
        print(f"{case!s:<26}{tuple(mine.values())!s:>28}   {'gleich' if same else tuple(repo.values())}")

    fast = (-0.2, 0.05, -45)  # zu schnell: ein Rad würde über 3000 Schritt/s kommen
    mine, repo = limit_speeds(wheel_speeds(*fast)), kinematics.body_to_wheels(*fast)
    same = all(abs(mine[w] - repo[w]) <= 1 for w in WHEELS)
    ok &= same
    print(f"\nZu schnell {fast}: begrenzt {tuple(mine.values())}  Repo {tuple(repo.values())}  "
          f"{'gleich' if same else 'ABWEICHUNG'}")  # fmt: skip

    x, y, theta = circle_command(radius_m=0.5, speed_ms=0.1)
    print(f"\nKreis r = 0,5 m bei 0,1 m/s: x = {x}, y = {y}, θ = {theta:.1f} °/s")
    body = kinematics.wheels_to_body(kinematics.body_to_wheels(x, y, theta))
    measured_radius = body["x.vel"] / math.radians(body["theta.vel"])
    print(f"  Rückrechnung: Radius {measured_radius:.3f} m")
    ok &= abs(measured_radius - 0.5) < 0.02

    limit = max_forward_speed()
    print(f"\nGrößte Vorwärtsgeschwindigkeit bei 3000 Schritt/s: {limit:.3f} m/s")
    scaled = kinematics.body_to_wheels(limit, 0, 0)
    ok &= max(abs(v) for v in scaled.values()) in (2999, 3000, 3001)
    print(f"  Probe mit dem Repo: {scaled}")
    print("\nAlles stimmt." if ok else "\nEtwas weicht ab: noch einmal nachrechnen.")
    return 0 if ok else 1


if __name__ == "__main__":
    run_script(main)
