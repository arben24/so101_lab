"""Sicherheitsfunktionen, die überall gelten.

- Zielpositionen auf die kalibrierten Gelenkgrenzen begrenzen (`clamp`),
- große Sprünge je Sollwert begrenzen (`clamp_relative`),
- RAM-Register nur aus einer Whitelist schreiben (`write_ram_register`),
- beim Beenden oder bei einem Fehler sicher zurücksetzen (`open_arm`, try/finally),
- SIGTERM (z. B. von systemd) wie Strg+C behandeln (`install_sigterm_handler`).
"""

from __future__ import annotations

import contextlib
import logging
import signal
from collections.abc import Iterator
from contextlib import contextmanager
from typing import TYPE_CHECKING

from . import registers as regs
from .backends.base import ArmBackend, BackendError

if TYPE_CHECKING:
    from .arm import Arm

logger = logging.getLogger(__name__)


def clamp(value: int, lo: int, hi: int) -> int:
    return max(lo, min(hi, int(value)))


def clamp_relative(goal: int, present: int, max_step: int | None) -> int:
    """Begrenzt den Abstand zwischen Ziel und Istposition auf `max_step` Schritte."""
    if max_step is None:
        return int(goal)
    return clamp(goal, present - max_step, present + max_step)


def write_ram_register(backend: ArmBackend, servo_id: int, name: str, value: int) -> None:
    """RAM-Register schreiben, aber nur die freigegebenen (registers.RAM_WRITE_WHITELIST)."""
    reg = regs.lookup(name)
    if reg.name not in regs.RAM_WRITE_WHITELIST:
        allowed = ", ".join(sorted(regs.RAM_WRITE_WHITELIST))
        raise BackendError(f"Register '{reg.name}' ist nicht freigegeben. Erlaubt: {allowed}.")
    backend.write_register(servo_id, reg.name, value)


def install_sigterm_handler() -> None:
    """SIGTERM löst KeyboardInterrupt aus, damit try/finally auch beim Beenden durch systemd greift."""

    def _handler(signum, frame):
        raise KeyboardInterrupt

    with contextlib.suppress(ValueError):  # ValueError: nicht im Hauptthread
        signal.signal(signal.SIGTERM, _handler)


def connect_arm(device: str, *, port: str | None = None, config_path: str | None = None) -> Arm:
    """Gerät aus config/arms.toml verbinden und als `Arm` zurückgeben, ohne Zurücksetzen am Ende.

    Für langlebige Verbindungen (Web-App). Wer das benutzt, muss selbst `arm.safe_shutdown()`
    und `arm.backend.disconnect()` aufrufen. In Skripten stattdessen `open_arm` verwenden.
    """
    from .arm import Arm
    from .backends import create_backend
    from .calibration import find_calibration_file, load_calibration
    from .config import load_config
    from .devices import get_device_spec

    cfg = load_config(config_path).device(device)
    spec = get_device_spec(cfg.type)
    cal_path = find_calibration_file(spec, cfg.calibration_id, cfg.calibration_dir)
    calibration = load_calibration(cal_path) if cal_path else None
    if cal_path is None:
        logger.warning("Keine Kalibrierung für '%s' gefunden: Grenzen sind 0…4095.", cfg.calibration_id)
    backend = create_backend(cfg, port)
    backend.connect()
    return Arm(backend, spec, cfg, calibration, calibration_path=cal_path)


@contextmanager
def open_arm(
    device: str,
    *,
    port: str | None = None,
    config_path: str | None = None,
    check_motors: bool = True,
    read_only: bool = False,
) -> Iterator[Arm]:
    """Gerät aus config/arms.toml öffnen und am Ende in jedem Fall sicher zurücksetzen.

        with open_arm("follower", port="/dev/ttyACM0") as arm:
            print(arm.read_positions())

    Beim Verlassen (auch nach Fehler oder Strg+C): Räder stopp, dann je nach `safe_state`
    Drehmoment aus oder Stellung halten. Danach wird die Verbindung geschlossen.
    Mit `read_only=True` (nur Lesen, z. B. `so101-lab state`) wird am Ende nichts verändert.
    """
    arm = connect_arm(device, port=port, config_path=config_path)
    backend = arm.backend
    try:
        if check_motors:
            arm.check_motors()
        yield arm
    finally:
        try:
            if not read_only:
                arm.safe_shutdown()
        except Exception:
            logger.exception("Sicheres Zurücksetzen fehlgeschlagen. Not-Aus: Netzteil ausschalten!")
        finally:
            backend.disconnect()
