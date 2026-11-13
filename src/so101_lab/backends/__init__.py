"""Backends: wie so101-lab mit dem Servo-Bus spricht.

`create_backend()` baut aus einem Eintrag in config/arms.toml das passende Backend.
"""

from __future__ import annotations

from ..config import DeviceConfig
from ..devices import get_device_spec
from .base import (
    ArmBackend,
    BackendError,
    Capability,
    FoundServo,
    NotSupportedError,
    ServoNotRespondingError,
    ServoState,
    VerificationError,
)

__all__ = [
    "ArmBackend",
    "BackendError",
    "Capability",
    "FoundServo",
    "NotSupportedError",
    "ServoNotRespondingError",
    "ServoState",
    "VerificationError",
    "create_backend",
]


class PortNotSelectedError(BackendError):
    """Für ein USB-Gerät ist kein Port gewählt."""


def create_backend(cfg: DeviceConfig, port: str | None = None) -> ArmBackend:
    """Backend für ein Gerät. `port` überschreibt den Port aus der Konfiguration (Auswahl
    durch den Nutzer in CLI oder Web-App)."""
    if cfg.backend == "usb":
        from .usb import UsbBackend

        chosen = port or cfg.port
        if not chosen:
            raise PortNotSelectedError(
                f"Für '{cfg.name}' ist kein Port gewählt. "
                "Mit --port angeben oder aus 'so101-lab ports' wählen."
            )
        return UsbBackend(chosen)
    if cfg.backend == "mock":
        from .mock import MockBackend

        spec = get_device_spec(cfg.type)
        return MockBackend.for_ids(spec.ids, wheel_ids=[w.motor_id for w in spec.wheels])
    if cfg.backend == "remote":
        raise NotSupportedError("Das Remote-Backend folgt in Phase 7.")
    if cfg.backend == "esp32":
        raise NotSupportedError("Das ESP32-Backend folgt in Phase 8.")
    raise BackendError(f"Unbekanntes Backend '{cfg.backend}'.")
