"""Einrichtungsassistent der Web-App: Servo-IDs vergeben (Reiter „Einrichtung“).

Läuft unabhängig von den Geräteverbindungen: Bei der ID-Vergabe hängt genau EIN Servo am
Bus, ein Gerät mit allen Gelenken gibt es dabei noch nicht. Der Ablauf selbst steht in
`so101_lab.servo_setup` (dieselbe Logik wie Skript 05).

Regeln:
- Höchstens ein Assistent gleichzeitig. Er gehört dem Browser-Tab (`client_id`), der ihn
  gestartet hat. Schließt der Tab (WebSocket weg), endet der Assistent und der Port wird frei.
- Ein Port gehört entweder einer Geräteverbindung oder dem Assistenten, nie beiden.
- Immer nur eine Aktion zur Zeit (die Suche dauert etwa 8 s). Eine zweite wird abgelehnt.
- Der Assistent schaltet das Drehmoment nie ein, ID und Baudrate werden bei ausgeschaltetem
  Drehmoment geschrieben. Ein Not-Aus muss hier nichts anhalten.
- Simulation: Port `mock` ist ein neuer Servo ohne Hardware. Anstecken anderer Servos und
  Aus-/Einschalten werden dort nachgebildet.
"""

from __future__ import annotations

import itertools
import logging
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import asdict
from typing import Any, TypeVar

from ..backends.base import DEFAULT_BAUDRATE, ArmBackend, BackendError
from ..backends.mock import MockBackend
from ..servo_setup import ServoSetup, SetupResult, describe
from .session import ControlError, Event

logger = logging.getLogger(__name__)

MOCK_PORT = "mock"

# Was sich in der Simulation „anstecken“ lässt: (Baudrate, Anzahl Servos mit ID 1)
SIMULATED_PLUGS = {
    "neu": (DEFAULT_BAUDRATE, 1),  # ab Werk
    "baudrate": (115_200, 1),  # falsche Baudrate, wird auf 1 Mbit/s gestellt
    "doppelt": (DEFAULT_BAUDRATE, 2),  # Fehlerfall: zwei Servos mit ID 1
}

T = TypeVar("T")


class SetupSession:
    def __init__(self, owner: str, port: str, backend: ArmBackend, setup: ServoSetup):
        self.owner = owner
        self.port = port
        self.backend = backend
        self.setup = setup
        self.simulated = port == MOCK_PORT
        self.busy: str | None = None
        self._lock = threading.Lock()
        self._events: deque[Event] = deque(maxlen=50)
        self._event_ids = itertools.count(1)

    @classmethod
    def open(cls, owner: str, port: str, device_type: str) -> SetupSession:
        if port == MOCK_PORT:
            backend: ArmBackend = MockBackend()
            backend.plug_new_servo()
            adapter_sn = None
        else:
            from ..backends.usb import UsbBackend
            from ..ports import adapter_serial

            backend = UsbBackend(port)
            adapter_sn = adapter_serial(port)
        try:
            setup = ServoSetup(
                backend, device_type, port=None if port == MOCK_PORT else port, adapter_sn=adapter_sn
            )
        except KeyError as exc:
            raise BackendError(str(exc.args[0])) from None
        backend.connect()
        session = cls(owner, port, backend, setup)
        where = "Simulation" if session.simulated else port
        session.event("info", f"Assistent gestartet ({setup.spec.description}, {where}).")
        return session

    # ------------------------------------------------------------ Hilfen

    def event(self, level: str, text: str) -> None:
        self._events.append(Event(next(self._event_ids), time.time(), level, text))
        (logger.info if level == "info" else logger.warning)("Einrichtung: %s", text)

    def require_owner(self, client_id: str | None) -> None:
        if client_id is None or client_id != self.owner:
            raise ControlError("Der Einrichtungsassistent gehört einem anderen Browser-Tab.")

    def _do(self, client_id: str | None, label: str, fn: Callable[[], T]) -> T:
        """Eine Aktion exklusiv ausführen. Läuft schon eine, wird abgelehnt statt gewartet."""
        self.require_owner(client_id)
        if not self._lock.acquire(blocking=False):
            raise ControlError(f"Der Assistent ist gerade beschäftigt ({self.busy}). Bitte warten.")
        self.busy = label
        try:
            return fn()
        finally:
            self.busy = None
            self._lock.release()

    def _report(self, result: SetupResult) -> None:
        label = result.joint or f"ID {result.new_id}"
        self.event("info" if result.ok else "error", f"{label}: {result.message}")

    # ------------------------------------------------------------ Aktionen

    def scan(self, client_id: str | None) -> dict[str, Any]:
        def run() -> None:
            found = self.setup.scan()
            listing = "; ".join(describe(s) for s in found) or "nichts"
            level = "info" if len(found) == 1 else "warn"
            self.event(level, f"Suche über alle Baudraten: {listing}.")

        self._do(client_id, "Suche läuft", run)
        return self.snapshot()

    def assign(
        self, client_id: str | None, joint: str | None = None, servo_id: int | None = None
    ) -> dict[str, Any]:
        """Ziel ist ein Gelenk des Gerätetyps oder eine freie ID."""
        self.setup.target(joint, servo_id)  # ungültiges Ziel sofort melden
        self._report(
            self._do(client_id, "ID wird geschrieben", lambda: self.setup.assign(joint, servo_id=servo_id))
        )
        return self.snapshot()

    def verify(
        self, client_id: str | None, joint: str | None = None, servo_id: int | None = None
    ) -> dict[str, Any]:
        def run() -> SetupResult:
            if self.simulated:
                assert isinstance(self.backend, MockBackend)
                self.backend.power_cycle()
                self.event("info", "Simulation: Servo aus- und wieder eingeschaltet.")
            return self.setup.verify_after_restart(joint, servo_id=servo_id)

        self._report(self._do(client_id, "Neustart-Probe läuft", run))
        return self.snapshot()

    def simulate_plug(self, client_id: str | None, variant: str) -> dict[str, Any]:
        if not self.simulated:
            raise BackendError("Nur in der Simulation: echte Servos bitte von Hand anstecken.")
        if variant not in SIMULATED_PLUGS:
            raise BackendError(f"Unbekannte Variante '{variant}'. Erlaubt: {', '.join(SIMULATED_PLUGS)}.")
        baudrate, count = SIMULATED_PLUGS[variant]

        def run() -> None:
            assert isinstance(self.backend, MockBackend)
            self.backend.plug_new_servo(baudrate, count)
            self.setup.found = None
            what = "zwei neue Servos (beide ID 1)" if count == 2 else f"neuer Servo (ID 1, {baudrate} Baud)"
            self.event("info", f"Simulation: alle Servos abgezogen, {what} angesteckt.")

        self._do(client_id, "Simulation", run)
        return self.snapshot()

    def close(self) -> None:
        """Port schließen. Wartet, bis eine laufende Aktion fertig ist (höchstens 20 s)."""
        acquired = self._lock.acquire(timeout=20)
        try:
            self.backend.disconnect()
        finally:
            if acquired:
                self._lock.release()

    # ------------------------------------------------------------ Anzeige

    def snapshot(self) -> dict[str, Any]:
        return {
            **self.setup.snapshot(),
            "port": self.port,
            "owner": self.owner,
            "simulated": self.simulated,
            "busy": self.busy,
            "events": [asdict(e) for e in list(self._events)[-20:]],
        }
