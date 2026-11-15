"""Bus-Freigabe für das RemoteBackend: der Pi-Teil von `/ws/bus/<gerät>`.

Ein Laptop (RemoteBackend) verbindet sich mit dem WebSocket, die Web-App auf dem Pi öffnet
den Bus des Geräts aus ihrer arms.toml und führt die Aufrufe aus. Je Gerät höchstens ein
Laptop, und der Port gehört dann weder einer Geräteverbindung im Browser noch dem
Einrichtungsassistenten.

Sicherheit auf dem Pi (unabhängig davon, was der Laptop tut):
- **Watchdog:** kommt länger als `watchdog_s` keine Nachricht (Standard 500 ms, wie LeRobots
  LeKiwi-Host), stoppt der Pi die Räder und setzt den Arm in den `safe_state` aus *seiner*
  arms.toml. Die Zeit, in der der Pi selbst an einer Anfrage arbeitet, zählt nicht.
- **Verbindung weg** ohne ordentliches `close`: dasselbe.
- **Not-Aus** auf dem Pi (Browser, Esc, `/api/emergency_stop`): Räder stopp, Drehmoment aus,
  Verbindung zum Laptop wird beendet. Der Laptop muss neu verbinden.
- Alle Prüfungen der Backend-Schicht (EEPROM nur über `write_eeprom`, Wertebereiche,
  `torque_enable` nur 0/1) laufen hier auf dem Pi, nicht nur am Laptop.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

from .. import __version__
from ..arm import Arm
from ..backends import create_backend
from ..backends.base import ArmBackend, BackendError
from ..backends.remote import (
    PROTOCOL_VERSION,
    decode_ids,
    encode_found,
    encode_ids,
    encode_states,
)
from ..config import DeviceConfig
from ..devices import get_device_spec

logger = logging.getLogger(__name__)

DEFAULT_BUS_WATCHDOG_MS = 500


def _args(args: dict[str, Any], *names: str) -> list[Any]:
    try:
        return [args[n] for n in names]
    except KeyError as exc:
        raise BackendError(f"Argument {exc} fehlt.") from None


def _ops(b: ArmBackend) -> dict[str, Any]:
    """Erlaubte Aufrufe. Nur öffentliche, geprüfte Methoden der Backend-Schicht."""
    return {
        "scan": lambda a: encode_found(b.scan(a.get("ids"), a.get("baudrates"))),
        "ping": lambda a: b.ping(*_args(a, "id")),
        "read_register": lambda a: b.read_register(*_args(a, "id", "name")),
        "write_register": lambda a: b.write_register(*_args(a, "id", "name", "value")),
        "sync_read": lambda a: encode_ids(b.sync_read(*_args(a, "ids", "name"))),
        "sync_write": lambda a: b.sync_write(a["name"], decode_ids(_args(a, "values")[0])),
        "set_torque": lambda a: b.set_torque(*_args(a, "ids", "enabled")),
        "read_positions": lambda a: encode_ids(b.read_positions(*_args(a, "ids"))),
        "read_state": lambda a: encode_states(b.read_state(*_args(a, "ids"))),
        "write_eeprom": lambda a: b.write_eeprom(*_args(a, "id", "name", "value")),
        "change_id": lambda a: b.change_id(*_args(a, "old_id", "new_id")),
        "change_baudrate": lambda a: (b.change_baudrate(*_args(a, "id", "baudrate")), b.baudrate)[1],
        "set_port_baudrate": lambda a: (b.set_port_baudrate(*_args(a, "baudrate")), b.baudrate)[1],
    }


class RemoteBusSession:
    """Ein Gerät des Pi, freigegeben für genau einen Laptop."""

    def __init__(self, cfg: DeviceConfig, peer: str, watchdog_s: float):
        if cfg.backend == "remote":
            raise BackendError(f"'{cfg.name}' ist selbst ein Remote-Gerät, das wird nicht weitergereicht.")
        if cfg.backend == "usb" and not cfg.port:
            raise BackendError(
                f"Auf dem Pi fehlt für '{cfg.name}' der Port in arms.toml (port = \"/dev/ttyACM0\"). "
                "Ports zeigt `so101-lab ports`."
            )
        self.name = cfg.name
        self.cfg = cfg
        self.peer = peer
        self.watchdog_s = watchdog_s
        self.port = cfg.port if cfg.backend == "usb" else None
        self.spec = get_device_spec(cfg.type)
        self.since = time.time()
        self.requests = 0
        self.stopped_reason: str | None = None  # gesetzt nach Not-Aus: keine Aufrufe mehr
        self._lock = threading.Lock()
        backend = create_backend(cfg)
        backend.connect()
        # Arm ohne Kalibrierung: nur für sicheres Zurücksetzen und Not-Aus auf dem Pi
        self.arm = Arm(backend, self.spec, cfg)
        self._ops = _ops(backend)

    def hello(self) -> dict[str, Any]:
        b = self.arm.backend
        return {
            "type": "hello",
            "protocol": PROTOCOL_VERSION,
            "version": __version__,
            "device": self.name,
            "device_type": self.cfg.type,
            "ids": self.spec.ids,
            "capabilities": sorted(c.value for c in b.capabilities),
            "baudrate": b.baudrate,
            "safe_state": self.cfg.safe_state,
            "watchdog_ms": round(self.watchdog_s * 1000),
            "host": _hostname(),
        }

    def call(self, request_id: Any, op: str, args: dict[str, Any]) -> dict[str, Any]:
        """Einen Aufruf ausführen und die Antwort bauen. Fehler gehen als Antwort zurück."""
        func = self._ops.get(op)
        if func is None:
            return _error(request_id, BackendError(f"Unbekannter Aufruf '{op}'."))
        with self._lock:
            if self.stopped_reason:
                return _error(request_id, BackendError(self.stopped_reason))
            self.requests += 1
            try:
                result = func(args or {})
            except BackendError as exc:
                return _error(request_id, exc)
            except (TypeError, ValueError, KeyError) as exc:
                return _error(request_id, BackendError(f"Ungültige Anfrage '{op}': {exc}"))
            except Exception as exc:  # unerwartet: protokollieren, als BackendError melden
                logger.exception("Bus-Freigabe %s: Fehler bei %s", self.name, op)
                return _error(request_id, BackendError(f"Fehler auf dem Pi bei '{op}': {exc}"))
        return {"id": request_id, "ok": True, "result": result}

    def safe_shutdown(self, reason: str) -> None:
        logger.warning(
            "Bus-Freigabe %s (%s): %s → Räder stopp, %s", self.name, self.peer, reason, self.cfg.safe_state
        )
        with self._lock:
            try:
                self.arm.safe_shutdown()
            except BackendError:
                logger.exception("Bus-Freigabe %s: sicheres Zurücksetzen unvollständig", self.name)

    def emergency_stop(self) -> None:
        with self._lock:
            self.stopped_reason = "Not-Aus auf dem Pi ausgelöst. Verbindung beendet, bitte neu verbinden."
            self.arm.emergency_stop()

    def close(self) -> None:
        with self._lock:
            try:
                self.arm.backend.disconnect()
            except Exception:
                logger.exception("Bus-Freigabe %s: Trennen fehlgeschlagen", self.name)

    def snapshot(self) -> dict[str, Any]:
        return {
            "peer": self.peer,
            "port": self.port,
            "since": self.since,
            "requests": self.requests,
            "device_type": self.cfg.type,
        }


def _error(request_id: Any, exc: BackendError) -> dict[str, Any]:
    return {"id": request_id, "ok": False, "error": type(exc).__name__, "message": str(exc)}


def _hostname() -> str:
    import socket

    return socket.gethostname()
