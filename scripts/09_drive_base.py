"""09 – Die LeKiwi-Basis fahren: vom Fahrbefehl zum Tempo der drei Räder.

Aufruf:
    uv run scripts/09_drive_base.py --x 0.1 --rechnen               nur rechnen, nichts bewegt sich
    uv run scripts/09_drive_base.py --x 0.1 --dauer 1               1 s vorwärts mit 0,1 m/s
    uv run scripts/09_drive_base.py --theta 30 --dauer 2            2 s auf der Stelle drehen
    uv run scripts/09_drive_base.py --x 0.1 --device demo_kiwi      simuliert, ohne Hardware

Koordinaten wie bei LeRobot: x vorwärts, y nach links (beides m/s), Drehung theta gegen den
Uhrzeigersinn in Grad/s. Kapitel 08 erklärt die Rechnung.

Was hier passiert:
1. Rechnen (`so101_lab.kinematics`): Jedes Rad hat eine Laufrichtung a. Sein Bahntempo ist
   cos(a)·x + sin(a)·y + R·θ. Geteilt durch den Radradius ergibt das sein Drehtempo, das in
   Schritt/s umgerechnet wird. Ist ein Rad zu schnell, werden alle drei gleich verkleinert.
2. Prüfen, ob die Räder im Geschwindigkeitsmodus sind (Register 33 `operating_mode` = 1).
   Im Positionsmodus würde `goal_velocity` nur die Höchstgeschwindigkeit begrenzen.
3. Drehmoment der Räder einschalten. `enable_torque` setzt vorher das Tempo auf 0.
4. Fahrbefehl alle 0,1 s wiederholen und dabei das gemessene Tempo zurückrechnen.
5. Am Ende (auch nach Fehler oder Strg+C) hält `open_arm` die Räder an und setzt sicher zurück.

Sicherheit: Zuerst **aufgebockt** fahren (Räder frei), Richtungen prüfen, dann auf den Boden.
Die Fahrt dauert höchstens 5 s, das Tempo ist auf die schnellste Stufe von LeRobot begrenzt.

LeRobot-Werkzeug dafür: `examples/lekiwi/teleoperate.py` (Tastatur W/A/S/D/Z/X).
"""

from __future__ import annotations

import argparse
import math
import time

from so101_lab import kinematics
from so101_lab.backends.base import BackendError
from so101_lab.safety import open_arm
from so101_lab.script_support import add_device_arguments, confirm, resolve_port, run_script

MAX_XY = kinematics.SPEED_LEVELS[-1]["xy"]  # 0,3 m/s
MAX_THETA = kinematics.SPEED_LEVELS[-1]["theta"]  # 90 °/s
MAX_DURATION_S = 5.0
COMMAND_PERIOD_S = 0.1
LABELS = {"base_left_wheel": "links", "base_back_wheel": "hinten", "base_right_wheel": "rechts"}


def explain(x: float, y: float, theta: float, max_raw: int) -> dict[str, int]:
    """Rechnung Schritt für Schritt ausgeben. Liefert die Rohwerte je Rad."""
    theta_rad = math.radians(theta)
    r, big_r = kinematics.WHEEL_RADIUS_M, kinematics.BASE_RADIUS_M
    print(
        f"Fahrbefehl: x = {x:+.2f} m/s, y = {y:+.2f} m/s, Drehung = {theta:+.0f} °/s ({theta_rad:+.3f} rad/s)"
    )
    print(f"Radradius r = {r} m, Abstand Mitte–Rad R = {big_r} m, 4096 Schritt je Umdrehung\n")
    print(f"{'Rad':<8}{'a':>7}{'Bahntempo':>12}{'Drehtempo':>12}{'roh':>9}")
    for name, angle in zip(kinematics.WHEELS, kinematics.WHEEL_ANGLES_DEG, strict=True):
        a = math.radians(angle)
        v = math.cos(a) * x + math.sin(a) * y + big_r * theta_rad  # m/s am Radumfang
        degps = math.degrees(v / r)  # Drehtempo des Rads
        raw = degps * kinematics.STEPS_PER_DEGREE
        print(f"{LABELS[name]:<8}{angle:>6.0f}°{v:>+10.3f} m/s{degps:>+8.0f} °/s{raw:>+9.0f}")

    wheels = kinematics.body_to_wheels(x, y, theta, max_raw=max_raw)  # dieselbe Rechnung, mit Begrenzung
    print(f"\nGesendet (begrenzt auf ±{max_raw} Schritt/s, alle Räder im gleichen Verhältnis):")
    print("  " + ", ".join(f"{LABELS[n]} {v:+d}" for n, v in wheels.items()))
    return wheels


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="LeKiwi-Basis kurz fahren und die Radtempi zeigen.")
    add_device_arguments(parser, default_device="lekiwi_base")
    parser.add_argument("--x", type=float, default=0.0, help="vorwärts in m/s (rückwärts negativ)")
    parser.add_argument("--y", type=float, default=0.0, help="nach links in m/s (rechts negativ)")
    parser.add_argument("--theta", type=float, default=0.0, help="Drehung gegen den Uhrzeigersinn in Grad/s")
    parser.add_argument(
        "--dauer", type=float, default=1.0, help=f"Fahrzeit in s (höchstens {MAX_DURATION_S:.0f})"
    )
    parser.add_argument("--rechnen", action="store_true", help="nur rechnen und anzeigen, nichts verbinden")
    parser.add_argument(
        "--ohne-warten", action="store_true", help="nicht auf Bestätigung warten (z. B. in Tests)"
    )
    args = parser.parse_args(argv)

    if max(abs(args.x), abs(args.y)) > MAX_XY or abs(args.theta) > MAX_THETA:
        raise BackendError(f"Zu schnell: höchstens ±{MAX_XY} m/s und ±{MAX_THETA:.0f} °/s.")
    if not 0 < args.dauer <= MAX_DURATION_S:
        raise BackendError(f"--dauer muss zwischen 0 und {MAX_DURATION_S:.0f} s liegen.")
    if not (args.x or args.y or args.theta):
        raise BackendError("Kein Fahrbefehl: --x, --y oder --theta angeben.")

    if args.rechnen:
        explain(args.x, args.y, args.theta, max_raw=3000)
        print("\nNur gerechnet (--rechnen), nichts bewegt.")
        return 0

    port = resolve_port(args)
    with open_arm(args.device, port=port, config_path=args.config) as arm:
        if not arm.can_drive:
            raise BackendError(
                f"Gerät '{args.device}' ({arm.spec.type}) hat keine Räder. lekiwi oder lekiwi_base wählen."
            )
        explain(args.x, args.y, args.theta, max_raw=arm.cfg.max_wheel_velocity)

        # Schritt 2: Betriebsart der Räder prüfen
        wheel_ids = {name: arm.spec.joint(name).motor_id for name in kinematics.WHEELS}
        modes = arm.backend.sync_read(list(wheel_ids.values()), "operating_mode")
        wrong = [f"{n} (ID {i}): {modes[i]}" for n, i in wheel_ids.items() if modes[i] != 1]
        if wrong:
            raise BackendError(
                "Räder nicht im Geschwindigkeitsmodus (operating_mode 1): "
                + ", ".join(wrong)
                + ". Siehe Kapitel 08."
            )

        print(f"\nDie Basis fährt gleich {args.dauer:.1f} s.")
        if not args.ohne_warten and not confirm("Aufgebockt oder freie Fläche, Hand am Netzteil, los?"):
            print("Abgebrochen, nichts bewegt.")
            return 1

        # Schritt 3: Drehmoment nur für die Räder (Tempo vorher 0)
        arm.configure_motion()
        arm.enable_torque(list(wheel_ids))

        # Schritt 4: Befehl wiederholen, gemessenes Tempo ausgeben
        start = time.monotonic()
        next_print = start
        while (now := time.monotonic()) - start < args.dauer:
            arm.drive(args.x, args.y, args.theta)
            if now >= next_print:
                m = {k: round(v, 3) + 0.0 for k, v in arm.read_body_velocity().items()}  # + 0.0: kein „−0“
                print(
                    f"  t = {now - start:4.1f} s  gemessen: x {m['x.vel']:+.3f} m/s, "
                    f"y {m['y.vel']:+.3f} m/s, Drehung {m['theta.vel']:+5.1f} °/s"
                )
                next_print += 0.25
            time.sleep(COMMAND_PERIOD_S)
        arm.stop_wheels()
    print("Fertig. Räder angehalten, Drehmoment aus (safe_state).")
    return 0


if __name__ == "__main__":
    run_script(main)
