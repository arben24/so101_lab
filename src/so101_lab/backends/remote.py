"""Remote-Backend: Servo-Bus an einem anderen Rechner, z. B. am Raspberry Pi im LeKiwi.

Aufbau wie beim LeKiwi von LeRobot (Host auf dem Pi, Client am Laptop), aber mit der
Backend-Schnittstelle von so101-lab:

    Laptop: Skript / Web-App → Arm → RemoteBackend ──WebSocket──┐
    Pi:     so101-lab web  →  /ws/bus/<gerät>  →  UsbBackend → Servos

Auf dem Pi läuft dieselbe Web-App (`so101-lab web --host 0.0.0.0`). Sie öffnet den Bus des
Geräts aus *ihrer* arms.toml und führt die Aufrufe aus. Gelenknamen, Kalibrierung und
Grenzen bleiben am Laptop (in `Arm`), über das Netz gehen nur IDs, Registernamen und
Rohwerte.

Protokoll (JSON-Texte über einen WebSocket, Einzelheiten in Kapitel 06, API-Referenz):

- Pi → Laptop zuerst `{"type": "hello", "device_type": …, "ids": […], "capabilities": […], …}`
  oder `{"type": "error", "message": …}`.
- Laptop → Pi: `{"id": 7, "op": "sync_read", "args": {"ids": [1, 2], "name": "present_position"}}`
- Pi → Laptop: `{"id": 7, "ok": true, "result": {...}}` oder
  `{"id": 7, "ok": false, "error": "ServoNotRespondingError", "message": "…"}`

Immer nur eine Anfrage unterwegs (Schloss), Antworten kommen also in der Reihenfolge der
Anfragen. Ein Hintergrund-Thread schickt alle 100 ms ein Lebenszeichen (`heartbeat`), wenn
gerade nichts anderes läuft. Bleibt es auf dem Pi länger als der Watchdog aus (Standard
500 ms), stoppt der Pi die Räder und setzt den Arm in seinen `safe_state`. Ein ordentliches
Ende (`close`) lässt die Servos, wie sie sind: Das Zurücksetzen hat dann schon `open_arm`
am Laptop erledigt.

Was am Stück auf dem Pi läuft (nicht in Einzelschritten über das Netz): `write_eeprom`,
`change_id`, `change_baudrate`, `read_state`. Bricht das Netz mittendrin ab, bleibt das
EEPROM also nicht offen.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import threading
import time
from collections.abc import Iterable, Sequence
from dataclasses import asdict
from typing import Any
from urllib.parse import quote, urlsplit

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

logger = logging.getLogger(__name__)

PROTOCOL_VERSION = 1
DEFAULT_PORT = 8000
# Am Laptop eigener Name: SO101_LAB_TOKEN würde dort auch die eigene Web-App absichern.
DEFAULT_TOKEN_ENV = "SO101_REMOTE_TOKEN"
HEARTBEAT_S = 0.1  # Lebenszeichen alle 100 ms (wie der Browser)
CALL_TIMEOUT_S = 3.0  # Wartezeit auf eine Antwort
# Suche über alle Baudraten dauert ca. 8 s, ID-/Baudratenwechsel mit Prüfung bis 2 s
SLOW_OPS = {"scan": 60.0, "change_id": 10.0, "change_baudrate": 10.0, "write_eeprom": 10.0}

# Fehlerklassen, die über das Netz als dieselbe Klasse ankommen
ERRORS: dict[str, type[BackendError]] = {
    cls.__name__: cls for cls in (BackendError, NotSupportedError, ServoNotRespondingError, VerificationError)
}


class RemoteConnectionError(BackendError):
    """Keine Verbindung zum anderen Rechner oder Verbindung abgebrochen."""


def bus_url(address: str, device: str) -> str:
    """`raspberrypi.local`, `pi:8000`, `http://pi:8000` oder `ws://pi:8000` → WebSocket-URL des Busses."""
    address = address.strip()
    if "://" not in address:
        address = "ws://" + address
    parts = urlsplit(address)
    scheme = {"http": "ws", "https": "wss", "ws": "ws", "wss": "wss"}.get(parts.scheme)
    if scheme is None or not parts.hostname:
        raise BackendError(f"Adresse '{address}' nicht verständlich. Beispiel: raspberrypi.local:8000")
    host = parts.hostname if ":" not in parts.hostname else f"[{parts.hostname}]"
    port = parts.port or DEFAULT_PORT
    return f"{scheme}://{host}:{port}/ws/bus/{quote(device, safe='')}"


# ------------------------------------------------------------------ Umwandeln (beide Seiten)


def encode_found(servos: list[FoundServo]) -> list[dict[str, Any]]:
    return [asdict(s) for s in servos]


def decode_found(data: list[dict[str, Any]]) -> list[FoundServo]:
    return [FoundServo(**d) for d in data]


def encode_ids(values: dict[int, Any]) -> dict[str, Any]:
    """JSON kennt nur Text als Schlüssel."""
    return {str(k): v for k, v in values.items()}


def decode_ids(values: dict[str, Any]) -> dict[int, Any]:
    return {int(k): v for k, v in values.items()}


def encode_states(states: dict[int, ServoState]) -> dict[str, Any]:
    return {str(k): asdict(s) for k, s in states.items()}


def decode_states(data: dict[str, Any]) -> dict[int, ServoState]:
    return {int(k): ServoState(**v) for k, v in data.items()}


# ------------------------------------------------------------------ Backend


class RemoteBackend(ArmBackend):
    name = "remote"

    def __init__(
        self,
        address: str,
        device: str,
        token: str | None = None,
        expected_ids: Sequence[int] | None = None,
        connect_timeout_s: float = 5.0,
    ):
        self.address = address
        self.device = device
        self.url = bus_url(address, device)
        self.token = token
        self.expected_ids = list(expected_ids) if expected_ids is not None else None
        self.connect_timeout_s = connect_timeout_s
        self.capabilities: frozenset[Capability] = frozenset()
        self.baudrate = 1_000_000
        self.hello: dict[str, Any] = {}
        self._ws = None
        self._lock = threading.Lock()
        self._next_id = 0
        self._last_send = 0.0
        self._broken: str | None = None  # Grund, falls die Verbindung abgebrochen ist
        self._stop = threading.Event()
        self._heartbeat: threading.Thread | None = None

    # ------------------------------------------------------------ Verbindung

    def connect(self) -> None:
        if self._ws is not None:
            return
        from websockets.exceptions import InvalidStatus, WebSocketException
        from websockets.sync.client import connect

        headers = {"Authorization": f"Bearer {self.token}"} if self.token else None
        try:
            ws = connect(
                self.url,
                additional_headers=headers,
                open_timeout=self.connect_timeout_s,
                close_timeout=1.0,
                ping_interval=None,  # eigenes Lebenszeichen, siehe oben
                max_size=2**22,
            )
        except InvalidStatus as exc:
            raise RemoteConnectionError(
                f"{self.url}: Antwort {exc.response.status_code}. Läuft dort `so101-lab web` "
                "in derselben Version?"
            ) from exc
        except (OSError, TimeoutError, WebSocketException) as exc:
            raise RemoteConnectionError(
                f"Keine Verbindung zu {self.url} ({exc}). Ist der Pi eingeschaltet und im selben "
                "Netz, läuft dort `so101-lab web --host 0.0.0.0`? (Kapitel 06, Fehlersuche)"
            ) from exc
        ws.__enter__()  # websockets ≥ 17.1: Verbindung ohne with-Block offen halten, close() in disconnect()
        try:
            hello = json.loads(ws.recv(timeout=self.connect_timeout_s))
        except Exception as exc:
            ws.close()
            raise RemoteConnectionError(f"{self.url}: keine Begrüßung vom Pi ({exc}).") from exc
        if hello.get("type") == "error":
            ws.close()
            raise RemoteConnectionError(f"Pi lehnt ab: {hello.get('message')}")
        if hello.get("type") != "hello" or hello.get("protocol") != PROTOCOL_VERSION:
            ws.close()
            raise RemoteConnectionError(
                f"{self.url}: unbekanntes Protokoll ({hello.get('protocol')!r} statt {PROTOCOL_VERSION}). "
                "Auf beiden Rechnern dieselbe Version von so101-lab benutzen."
            )
        remote_ids = set(hello.get("ids", []))
        if self.expected_ids is not None and not set(self.expected_ids) <= remote_ids:
            ws.close()
            raise RemoteConnectionError(
                f"Gerät '{self.device}' auf dem Pi ist ein {hello.get('device_type')} mit den IDs "
                f"{sorted(remote_ids)}, hier werden {sorted(self.expected_ids)} erwartet. "
                "type in beiden arms.toml prüfen."
            )
        self._ws = ws
        self.hello = hello
        self.capabilities = frozenset(Capability(c) for c in hello.get("capabilities", []))
        self.baudrate = int(hello.get("baudrate", self.baudrate))
        self._broken = None
        self._last_send = time.monotonic()
        self._stop.clear()
        self._heartbeat = threading.Thread(target=self._heartbeat_loop, name="remote-heartbeat", daemon=True)
        self._heartbeat.start()
        logger.info(
            "Remote: verbunden mit %s (%s, %s), Watchdog %s ms",
            self.url,
            hello.get("device_type"),
            hello.get("host"),
            hello.get("watchdog_ms"),
        )
        if hello.get("version") and hello.get("version") != _local_version():
            logger.warning(
                "Remote: auf dem Pi läuft so101-lab %s, hier %s.", hello.get("version"), _local_version()
            )

    def disconnect(self) -> None:
        """Ordentlich beenden: Der Pi lässt die Servos, wie sie sind (siehe Moduldoku)."""
        if self._ws is None:
            return
        self._stop.set()
        try:
            if self._broken is None:
                self._call("close", {}, timeout=1.0)
        except BackendError:
            pass
        finally:
            ws, self._ws = self._ws, None
            with contextlib.suppress(Exception):
                ws.close()
            if self._heartbeat is not None:
                self._heartbeat.join(timeout=1.0)
            logger.info("Remote: %s getrennt", self.url)

    @property
    def is_connected(self) -> bool:
        return self._ws is not None and self._broken is None

    # ------------------------------------------------------------ Anfrage und Antwort

    def _call(self, op: str, args: dict[str, Any], timeout: float | None = None) -> Any:
        with self._lock:
            if self._ws is None:
                raise RemoteConnectionError("Nicht verbunden. Erst connect() aufrufen.")
            if self._broken is not None:
                raise RemoteConnectionError(f"Verbindung zum Pi abgebrochen ({self._broken}). Neu verbinden.")
            self._next_id += 1
            request_id = self._next_id
            try:
                self._ws.send(json.dumps({"id": request_id, "op": op, "args": args}))
                self._last_send = time.monotonic()
                reply = json.loads(self._ws.recv(timeout=timeout or SLOW_OPS.get(op, CALL_TIMEOUT_S)))
            except TimeoutError:
                self._broken = f"keine Antwort auf '{op}'"
                raise RemoteConnectionError(
                    f"Pi antwortet nicht auf '{op}'. Netz prüfen, dann neu verbinden."
                ) from None
            except Exception as exc:
                self._broken = str(exc) or type(exc).__name__
                raise RemoteConnectionError(f"Verbindung zum Pi abgebrochen: {self._broken}") from exc
        if reply.get("type") == "error":  # Pi beendet die Verbindung (Not-Aus, Watchdog, Server-Ende)
            self._broken = reply.get("message", "vom Pi beendet")
            raise RemoteConnectionError(f"Pi hat die Verbindung beendet: {self._broken}")
        if reply.get("id") != request_id:
            self._broken = "Antworten vertauscht"
            raise RemoteConnectionError(f"Antwort {reply.get('id')} passt nicht zu Anfrage {request_id}.")
        if not reply.get("ok"):
            cls = ERRORS.get(reply.get("error", ""), BackendError)
            raise cls(reply.get("message", "Fehler auf dem Pi"))
        return reply.get("result")

    def _heartbeat_loop(self) -> None:
        while not self._stop.wait(HEARTBEAT_S / 2):
            if time.monotonic() - self._last_send < HEARTBEAT_S:
                continue
            if self._lock.locked():  # gerade läuft eine Anfrage, die zählt als Lebenszeichen
                continue
            try:
                self._call("heartbeat", {})
            except BackendError as exc:
                logger.warning("Remote: Lebenszeichen fehlgeschlagen: %s", exc)
                return

    # ------------------------------------------------------------ Aufrufe

    def scan(
        self, ids: Sequence[int] | None = None, baudrates: Sequence[int] | None = None
    ) -> list[FoundServo]:
        args = {
            "ids": list(ids) if ids is not None else None,
            "baudrates": list(baudrates) if baudrates is not None else None,
        }
        return decode_found(self._call("scan", args))

    def ping(self, servo_id: int) -> int | None:
        return self._call("ping", {"id": int(servo_id)})

    def read_register(self, servo_id: int, name: str) -> int:
        return self._call("read_register", {"id": int(servo_id), "name": name})

    def write_register(self, servo_id: int, name: str, value: int) -> None:
        self._call("write_register", {"id": int(servo_id), "name": name, "value": int(value)})

    def sync_read(self, ids: Sequence[int], name: str) -> dict[int, int]:
        if not ids:
            return {}
        return decode_ids(self._call("sync_read", {"ids": [int(i) for i in ids], "name": name}))

    def sync_write(self, name: str, values: dict[int, int]) -> None:
        if not values:
            return
        self._call(
            "sync_write", {"name": name, "values": encode_ids({int(k): int(v) for k, v in values.items()})}
        )

    def set_torque(self, ids: Iterable[int], enabled: bool) -> None:
        self._call("set_torque", {"ids": [int(i) for i in ids], "enabled": bool(enabled)})

    def read_positions(self, ids: Sequence[int]) -> dict[int, int]:
        if not ids:
            return {}
        return decode_ids(self._call("read_positions", {"ids": [int(i) for i in ids]}))

    def read_state(self, ids: Sequence[int]) -> dict[int, ServoState]:
        if not ids:
            return {}
        return decode_states(self._call("read_state", {"ids": [int(i) for i in ids]}))

    def write_eeprom(self, servo_id: int, name: str, value: int) -> None:
        self._call("write_eeprom", {"id": int(servo_id), "name": name, "value": int(value)})

    def change_id(self, old_id: int, new_id: int) -> None:
        self._call("change_id", {"old_id": int(old_id), "new_id": int(new_id)})

    def change_baudrate(self, servo_id: int, baudrate: int) -> None:
        self.baudrate = int(self._call("change_baudrate", {"id": int(servo_id), "baudrate": int(baudrate)}))

    def set_port_baudrate(self, baudrate: int) -> None:
        self.baudrate = int(self._call("set_port_baudrate", {"baudrate": int(baudrate)}))

    # Rohzugriffe der Basisklasse: über die geprüften Aufrufe oben, der Pi prüft noch einmal.

    def _read(self, servo_id: int, name: str) -> int:
        return self.read_register(servo_id, name)

    def _write(self, servo_id: int, name: str, value: int) -> None:
        self.write_register(servo_id, name, value)

    def _sync_read(self, ids: Sequence[int], name: str) -> dict[int, int]:
        return self.sync_read(ids, name)

    def _sync_write(self, name: str, values: dict[int, int]) -> None:
        self.sync_write(name, values)

    def _set_port_baudrate(self, baudrate: int) -> None:
        self.set_port_baudrate(baudrate)


def token_from_env(token_env: str | None) -> str | None:
    return os.environ.get(token_env or DEFAULT_TOKEN_ENV) or None


def _local_version() -> str:
    from .. import __version__

    return __version__
