"""Gemeinsame Hilfen der Lernskripte in `scripts/`.

Hält die Skripte kurz: Argumente für Gerät und Port, Portwahl und eine einheitliche
Fehlerbehandlung mit deutschen Meldungen. Alles Lehrreiche (Registerzugriffe, Bewegung,
Sicherheit) steht bewusst in den Skripten selbst.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable

from .backends.base import BackendError
from .cli import CliError, choose_port
from .config import ConfigError, load_config
from .safety import install_sigterm_handler
from .trajectories import TrajectoryError


def add_device_arguments(parser: argparse.ArgumentParser, default_device: str = "follower") -> None:
    """`--device`, `--port`, `--config`: wie bei `so101-lab state`."""
    parser.add_argument(
        "--device",
        default=default_device,
        help=f"Gerätename aus config/arms.toml (Standard: {default_device}; 'demo' = simuliert)",
    )
    parser.add_argument("--port", default=None, help="serieller Port, z. B. /dev/ttyACM0 oder COM3")
    parser.add_argument("--config", default=None, help="Pfad zu arms.toml")


def resolve_port(args: argparse.Namespace) -> str | None:
    """Port für das Gerät: `--port`, sonst Eintrag in arms.toml, sonst Auswahl aus der Liste.

    Für Geräte ohne USB-Backend (z. B. `demo`) kommt `None` zurück.
    """
    cfg = load_config(args.config).device(args.device)
    if cfg.backend != "usb":
        return None
    return choose_port(args.port or cfg.port)


def confirm(question: str) -> bool:
    """Ja/Nein-Frage. Ohne Terminal (Pipe, Test) gilt: nein."""
    if not sys.stdin.isatty():
        print(f"{question} [j/N] – kein Terminal, abgebrochen.")
        return False
    return input(f"{question} [j/N] ").strip().lower() in ("j", "ja", "y", "yes")


def run_script(main: Callable[[], int]) -> None:
    """Skript ausführen, Fehler als eine Zeile melden und mit passendem Exit-Code beenden."""
    install_sigterm_handler()
    try:
        code = main()
    except (CliError, BackendError, ConfigError, TrajectoryError) as exc:
        print(f"Fehler: {exc}", file=sys.stderr)
        code = 1
    except KeyboardInterrupt:
        print("\nAbgebrochen.", file=sys.stderr)
        code = 130
    raise SystemExit(code)
