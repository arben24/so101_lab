"""Kommandozeile `so101-lab`.

Unterbefehle:
  doctor  prüft die Umgebung (Version, Ports, Rechte, Kameras)
  scan    scannt den Servo-Bus (kommt mit Phase 2)
  web     startet die Web-App samt Doku (kommt mit Phase 4)
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

from . import __version__
from .doctor import run_doctor


def _not_yet(name: str, phase: int) -> int:
    print(f"'{name}' ist noch nicht umgesetzt (geplant in Phase {phase}, siehe PLAN.md).", file=sys.stderr)
    return 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="so101-lab",
        description="Lehr-Repository für den SO-101-Roboterarm und die LeKiwi-Plattform.",
    )
    parser.add_argument("--version", action="version", version=f"so101-lab {__version__}")
    sub = parser.add_subparsers(dest="command", required=True, metavar="BEFEHL")

    doctor = sub.add_parser("doctor", help="prüft Umgebung, Ports, Rechte und Kameras")
    doctor.add_argument("--config", default=None, help="Pfad zu arms.toml (Standard: config/arms.toml)")

    scan = sub.add_parser("scan", help="scannt den Servo-Bus (Phase 2)")
    scan.add_argument("--port", default=None, help="serieller Port, z. B. /dev/ttyACM0")

    web = sub.add_parser("web", help="startet Web-App und Doku (Phase 4)")
    web.add_argument("--host", default="127.0.0.1", help="Adresse (0.0.0.0 für Netzwerkzugriff)")
    web.add_argument("--port", type=int, default=8000, help="TCP-Port")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "doctor":
        return run_doctor(config_path=args.config)
    if args.command == "scan":
        return _not_yet("scan", 2)
    if args.command == "web":
        return _not_yet("web", 4)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
