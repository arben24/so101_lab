"""Kommandozeile `so101-lab`.

Unterbefehle:
  doctor  prüft die Umgebung (Version, Ports, Rechte, Kameras)
  ports   listet die USB-Seriell-Ports
  scan    sucht Servos am Bus (IDs, Modell, Baudrate, Firmware)
  state   liest den Zustand aller Gelenke eines Geräts (nur lesend)
  web     startet die Web-App samt Doku (kommt mit Phase 4)

Der Port wird nicht über udev-Regeln festgelegt, sondern bei jedem Aufruf gewählt:
mit --port, aus config/arms.toml oder interaktiv aus der Liste.
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from collections.abc import Sequence

from . import __version__
from .doctor import run_doctor


class CliError(Exception):
    pass


# ------------------------------------------------------------------ Portauswahl


def choose_port(port: str | None, interactive: bool | None = None) -> str:
    """Port aus Argument, sonst aus der Liste der USB-Seriell-Ports wählen."""
    if port:
        return port
    from .ports import list_serial_ports

    ports = list_serial_ports()
    if not ports:
        raise CliError("Kein USB-Seriell-Port gefunden. Adapter anstecken oder --port angeben.")
    if interactive is None:
        interactive = sys.stdin.isatty()
    if not interactive:
        if len(ports) == 1:
            print(f"Port: {ports[0].label}")
            return ports[0].device
        raise CliError("Mehrere Ports gefunden, bitte mit --port wählen:\n" + _port_lines(ports))
    print("Gefundene Ports:\n" + _port_lines(ports))
    while True:
        answer = input(f"Port wählen [1–{len(ports)}, Enter = 1]: ").strip() or "1"
        if answer.isdigit() and 1 <= int(answer) <= len(ports):
            return ports[int(answer) - 1].device
        print("Bitte eine Nummer aus der Liste eingeben.")


def _port_lines(ports) -> str:
    return "\n".join(f"  {i}) {p.label}" for i, p in enumerate(ports, start=1))


def _parse_ids(text: str | None) -> list[int] | None:
    """'1-6,9' → [1, 2, 3, 4, 5, 6, 9]"""
    if not text:
        return None
    ids: list[int] = []
    for part in text.split(","):
        if "-" in part:
            a, b = part.split("-", 1)
            ids.extend(range(int(a), int(b) + 1))
        else:
            ids.append(int(part))
    if any(not 0 <= i <= 253 for i in ids):
        raise CliError("IDs müssen zwischen 0 und 253 liegen.")
    return ids


# ------------------------------------------------------------------ Befehle


def cmd_ports(_args) -> int:
    from .ports import list_serial_ports

    ports = list_serial_ports(usb_only=not _args.all)
    if not ports:
        print("Kein USB-Seriell-Port gefunden.")
        return 1
    print(_port_lines(ports))
    return 0


def cmd_scan(args) -> int:
    from . import registers as regs
    from .backends import create_backend
    from .backends.usb import UsbBackend
    from .config import load_config

    if args.device:
        cfg = load_config(args.config).device(args.device)
        backend = create_backend(cfg, args.port or (None if cfg.backend != "usb" else choose_port(cfg.port)))
        where = f"Gerät '{cfg.name}' ({cfg.backend})"
    else:
        port = choose_port(args.port)
        backend = UsbBackend(port)
        where = port
    baudrates = sorted(regs.baudrates(), reverse=True) if args.all_baudrates else None
    ids = _parse_ids(args.ids)
    print(f"Scanne {where}" + (" über alle Baudraten" if baudrates else " bei 1 000 000 Baud") + " …")
    started = time.perf_counter()
    with backend:
        found = backend.scan(ids=ids, baudrates=baudrates)
    print(f"Dauer {time.perf_counter() - started:.1f} s, {len(found)} Servo(s) gefunden.\n")
    if not found:
        hint = "" if baudrates else " Andere Baudrate? --all-baudrates"
        print(f"Kein Servo antwortet. Servo-Netzteil an? Kabel zum Arm gesteckt?{hint}")
        return 1
    print(f"{'ID':>3}  {'Modell':<10} {'Nr.':>5}  {'Baudrate':>9}  Firmware")
    for s in found:
        print(f"{s.id:>3}  {s.model_name:<10} {s.model_number:>5}  {s.baudrate:>9}  {s.firmware or '?'}")
    duplicates = sorted({s.id for s in found if sum(x.id == s.id for x in found) > 1})
    if duplicates:
        print(f"\nAchtung: ID(s) {duplicates} bei mehreren Baudraten gefunden.")
    return 0


def cmd_state(args) -> int:
    from .config import load_config
    from .safety import open_arm

    cfg = load_config(args.config).device(args.device)
    port = args.port
    if cfg.backend == "usb" and not port:
        port = choose_port(cfg.port) if not cfg.port else cfg.port
    with open_arm(args.device, port=port, config_path=args.config, check_motors=False, read_only=True) as arm:
        present = [j for j in arm.joints if arm.backend.ping(j.motor_id) is not None]
        missing = [f"{j.name} (ID {j.motor_id})" for j in arm.joints if j not in present]
        if arm.calibration_path:
            print(f"Kalibrierung: {arm.calibration_path}")
        else:
            print("Kalibrierung: keine gefunden, Gradangaben fehlen.")
        if missing:
            print(f"Keine Antwort von: {', '.join(missing)}")
        if not present:
            return 1
        names = [j.name for j in present]
        try:
            while True:
                states = arm.read_state(names)
                values = arm.to_joint_values({n: s.position for n, s in states.items()})
                _print_state(states, values)
                if not args.watch:
                    break
                time.sleep(0.2)
                print(f"\033[{len(states) + 1}A", end="")  # Tabelle überschreiben
        except KeyboardInterrupt:
            print()
    return 0 if not missing else 1


def _print_state(states, values) -> None:
    print(
        f"{'Gelenk':<18}{'ID':>3}{'Roh':>6}{'Wert':>9}{'Tempo':>7}{'Last':>6}"
        f"{'U/V':>6}{'T/°C':>6}  DM  Fehler"
    )
    for name, s in states.items():
        v = values.get(name)
        value = f"{v:8.1f}" if v is not None else "       -"
        errors = ", ".join(s.errors) or "-"
        print(
            f"{name:<18}{s.id:>3}{s.position:>6} {value}{s.velocity:>7}{s.load:>6}"
            f"{s.voltage:>6.1f}{s.temperature:>6}  {'an ' if s.torque_enabled else 'aus'} {errors}"
        )


def _not_yet(name: str, phase: int) -> int:
    print(f"'{name}' ist noch nicht umgesetzt (geplant in Phase {phase}, siehe PLAN.md).", file=sys.stderr)
    return 2


# ------------------------------------------------------------------ Parser


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="so101-lab",
        description="Lehr-Repository für den SO-101-Roboterarm und die LeKiwi-Plattform.",
    )
    parser.add_argument("--version", action="version", version=f"so101-lab {__version__}")
    parser.add_argument("-v", "--verbose", action="store_true", help="ausführliche Meldungen")
    sub = parser.add_subparsers(dest="command", required=True, metavar="BEFEHL")

    doctor = sub.add_parser("doctor", help="prüft Umgebung, Ports, Rechte und Kameras")
    doctor.add_argument("--config", default=None, help="Pfad zu arms.toml (Standard: config/arms.toml)")

    ports = sub.add_parser("ports", help="listet die USB-Seriell-Ports")
    ports.add_argument("--all", action="store_true", help="auch Ports ohne USB-Kennung zeigen")

    scan = sub.add_parser("scan", help="sucht Servos am Bus")
    scan.add_argument("--port", default=None, help="serieller Port, z. B. /dev/ttyACM0 oder COM3")
    scan.add_argument("--device", default=None, help="Gerät aus arms.toml statt eines Ports (z. B. demo)")
    scan.add_argument("--config", default=None, help="Pfad zu arms.toml")
    scan.add_argument("--all-baudrates", action="store_true", help="alle Baudraten durchsuchen (ca. 7 s)")
    scan.add_argument("--ids", default=None, help="nur diese IDs, z. B. 1-6,9")

    state = sub.add_parser("state", help="liest den Zustand aller Gelenke (verändert nichts)")
    state.add_argument("device", help="Gerätename aus arms.toml, z. B. follower oder demo")
    state.add_argument("--port", default=None, help="Port statt des Eintrags in arms.toml")
    state.add_argument("--config", default=None, help="Pfad zu arms.toml")
    state.add_argument("--watch", action="store_true", help="laufend anzeigen, Ende mit Strg+C")

    web = sub.add_parser("web", help="startet Web-App und Doku (Phase 4)")
    web.add_argument("--host", default="127.0.0.1", help="Adresse (0.0.0.0 für Netzwerkzugriff)")
    web.add_argument("--port", type=int, default=8000, help="TCP-Port")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    from .backends.base import BackendError
    from .config import ConfigError

    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING, format="%(levelname)s: %(message)s"
    )
    commands = {
        "doctor": lambda a: run_doctor(config_path=a.config),
        "ports": cmd_ports,
        "scan": cmd_scan,
        "state": cmd_state,
    }
    if args.command == "web":
        return _not_yet("web", 4)
    try:
        return commands[args.command](args)
    except (CliError, BackendError, ConfigError) as exc:
        print(f"Fehler: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nAbgebrochen.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
