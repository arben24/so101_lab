"""FastAPI-Server der Web-App: REST für einmalige Aktionen, WebSocket für den Live-Zustand.

Derselbe Server läuft am Laptop (`so101-lab web`) und auf dem Raspberry Pi
(`so101-lab web --host 0.0.0.0 --token …`). Es gibt kein zweites Programm.

Zugriffsschutz: Ist ein Token gesetzt, braucht jede Anfrage an `/api/…` und `/ws` das Token
(Kopfzeile `Authorization: Bearer <token>` oder `?token=<token>`). Die Seiten selbst
(HTML, CSS, JS, Doku) sind ohne Token abrufbar, sie enthalten keine Daten.

Steuerrecht: Jeder Browser-Tab bekommt beim Öffnen des WebSockets eine `client_id`.
Aktionen, die etwas bewegen, schicken sie mit (`client_id` im JSON). Nur der steuernde
Client darf sie ausführen. Der Not-Aus geht immer.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import secrets
import threading
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .. import __version__
from ..backends.base import BackendError
from ..config import ConfigError, load_config
from ..safety import connect_arm
from .session import ControlError, DeviceSession
from .setup_session import MOCK_PORT, SIMULATED_PLUGS, SetupSession

logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"
STATE_INTERVAL_S = 0.05  # 20 Zustandsmeldungen je Sekunde an den Browser


# ------------------------------------------------------------------ Zustand des Servers


class LabServer:
    """Alle Geräteverbindungen und offenen Live-Verbindungen des Servers."""

    def __init__(
        self,
        config_path: str | None = None,
        token: str | None = None,
        watchdog_ms: int = 400,
        poll_hz: float = 30.0,
    ):
        self.config_path = config_path
        self.token = token or None
        self.watchdog_s = watchdog_ms / 1000
        self.poll_hz = poll_hz
        self.sessions: dict[str, DeviceSession] = {}
        self.clients: set[str] = set()  # client_ids mit offenem WebSocket
        self.setup: SetupSession | None = None  # Einrichtungsassistent (höchstens einer)
        self._lock = threading.Lock()

    def config(self):
        return load_config(self.config_path)

    def session(self, name: str) -> DeviceSession:
        session = self.sessions.get(name)
        if session is None:
            raise HTTPException(409, f"Gerät '{name}' ist nicht verbunden.")
        return session

    def connect(self, name: str, port: str | None) -> DeviceSession:
        cfg = self.config().device(name)
        with self._lock:
            if name in self.sessions:
                raise HTTPException(409, f"Gerät '{name}' ist schon verbunden.")
            if cfg.backend == "usb":
                port = port or cfg.port
                busy = [s.name for s in self.sessions.values() if port and s.port == port]
                if busy:
                    raise HTTPException(409, f"Port {port} wird schon von '{busy[0]}' benutzt.")
                if port and self.setup is not None and self.setup.port == port:
                    raise HTTPException(
                        409, f"Port {port} gehört gerade dem Einrichtungsassistenten. Erst dort beenden."
                    )
            arm = connect_arm(name, port=port, config_path=self.config_path)
            session = DeviceSession(
                name,
                arm,
                port if cfg.backend == "usb" else None,
                watchdog_s=self.watchdog_s,
                poll_hz=self.poll_hz,
            )
            try:
                session.start()
            except Exception:
                arm.backend.disconnect()
                raise
            self.sessions[name] = session
            return session

    def disconnect(self, name: str, client_id: str | None) -> None:
        session = self.session(name)
        if session.controller not in (None, client_id):
            raise ControlError(
                "Ein anderer Client steuert dieses Gerät. Trennen erst nach Abgabe oder Not-Aus."
            )
        with self._lock:
            self.sessions.pop(name, None)
        session.stop()

    def emergency_stop(self) -> list[str]:
        stopped = []
        for session in list(self.sessions.values()):
            session.emergency_stop()
            stopped.append(session.name)
        return stopped

    def client_gone(self, client_id: str) -> None:
        self.clients.discard(client_id)
        for session in list(self.sessions.values()):
            session.release_control(client_id, "Live-Verbindung getrennt")
        setup = self.setup
        if setup is not None and setup.owner == client_id:
            self.stop_setup(client_id)

    # ------------------------------------------------------------ Einrichtungsassistent

    def start_setup(self, client_id: str, port: str, device_type: str) -> SetupSession:
        with self._lock:
            if self.setup is not None:
                where = "Simulation" if self.setup.simulated else self.setup.port
                raise ControlError(f"Der Einrichtungsassistent läuft schon ({where}). Erst dort beenden.")
            busy = [s.name for s in self.sessions.values() if s.port == port]
            if busy:
                raise ControlError(
                    f"Port {port} ist mit dem Gerät '{busy[0]}' verbunden. Erst trennen: "
                    "Für die ID-Vergabe hängt nur ein einzelner Servo am Bus."
                )
            self.setup = SetupSession.open(client_id, port, device_type)
            return self.setup

    def require_setup(self, client_id: str | None) -> SetupSession:
        setup = self.setup
        if setup is None:
            raise ControlError("Der Einrichtungsassistent läuft nicht. Erst starten.")
        setup.require_owner(client_id)
        return setup

    def stop_setup(self, client_id: str | None) -> None:
        setup = self.require_setup(client_id)
        with self._lock:
            if self.setup is setup:
                self.setup = None
        setup.close()

    def require_client(self, client_id: str | None) -> str:
        if not client_id or client_id not in self.clients:
            raise ControlError("Keine aktive Live-Verbindung (WebSocket). Seite neu laden.")
        return client_id

    def shutdown(self) -> None:
        for name in list(self.sessions):
            session = self.sessions.pop(name)
            session.stop()
        setup, self.setup = self.setup, None
        if setup is not None:
            setup.close()

    def check_token(self, presented: str | None) -> bool:
        if self.token is None:
            return True
        return presented is not None and secrets.compare_digest(presented.encode(), self.token.encode())


def _token_from(headers, query) -> str | None:
    auth = headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return query.get("token")


# ------------------------------------------------------------------ Anfragen


class ConnectRequest(BaseModel):
    port: str | None = None


class ClientRequest(BaseModel):
    client_id: str | None = None


class ControlRequest(ClientRequest):
    force: bool = False


class TorqueRequest(ClientRequest):
    enabled: bool
    joints: list[str] | None = None  # None = alle Gelenke


class MotionRequest(ClientRequest):
    velocity: int  # Schritt/s
    acceleration: int  # 100 Schritt/s²


class MoveRequest(ClientRequest):
    targets: dict[str, int]  # Gelenkname → Rohwert


class RegisterWriteRequest(ClientRequest):
    joint: str
    register_name: str
    value: int
    confirm_eeprom: bool = False


class SetupStartRequest(ClientRequest):
    port: str  # serieller Port oder "mock" (Simulation)
    device_type: str  # so101_follower | so101_leader | lekiwi


class SetupTargetRequest(ClientRequest):
    joint: str | None = None  # Gelenk des Gerätetyps …
    id: int | None = None  # … oder eine freie ID (0–253)


class SetupSimulateRequest(ClientRequest):
    variant: str  # siehe SIMULATED_PLUGS


# ------------------------------------------------------------------ App


def create_app(server: LabServer, docs_dir: Path | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        yield
        # Server wird beendet (Strg+C): alle Geräte sicher zurücksetzen und trennen.
        await asyncio.to_thread(server.shutdown)

    app = FastAPI(
        title="so101-lab", version=__version__, lifespan=lifespan, docs_url="/api/docs", redoc_url=None
    )
    app.state.lab = server

    def auth(request: Request) -> None:
        if not server.check_token(_token_from(request.headers, request.query_params)):
            raise HTTPException(401, "Token fehlt oder ist falsch.")

    @app.exception_handler(ControlError)
    async def _control_error(_request: Request, exc: ControlError) -> JSONResponse:
        return JSONResponse({"detail": str(exc)}, status_code=409)

    @app.exception_handler(BackendError)
    async def _backend_error(_request: Request, exc: BackendError) -> JSONResponse:
        return JSONResponse({"detail": str(exc)}, status_code=400)

    @app.exception_handler(ConfigError)
    async def _config_error(_request: Request, exc: ConfigError) -> JSONResponse:
        return JSONResponse({"detail": str(exc)}, status_code=400)

    # --- ohne Token -------------------------------------------------------------

    @app.get("/api/info")
    def info() -> dict[str, Any]:
        return {
            "version": __version__,
            "auth_required": server.token is not None,
            "docs": docs_dir is not None,
        }

    # --- mit Token --------------------------------------------------------------

    @app.get("/api/ports", dependencies=[Depends(auth)])
    def ports() -> list[dict[str, Any]]:
        from ..ports import list_serial_ports

        return [
            {
                "device": p.device,
                "label": p.label,
                "usb_id": p.usb_id,
                "known_adapter": p.known_adapter is not None,
            }
            for p in list_serial_ports()
        ]

    @app.get("/api/devices", dependencies=[Depends(auth)])
    def devices() -> list[dict[str, Any]]:
        return [
            {
                "name": d.name,
                "type": d.type,
                "backend": d.backend,
                "port": d.port,
                "safe_state": d.safe_state,
                "connected": d.name in server.sessions,
            }
            for d in server.config().devices.values()
        ]

    @app.post("/api/devices/{name}/connect", dependencies=[Depends(auth)])
    def connect(name: str, body: ConnectRequest) -> dict[str, Any]:
        _known(server, name)
        return server.connect(name, body.port).snapshot()

    @app.post("/api/devices/{name}/disconnect", dependencies=[Depends(auth)])
    def disconnect(name: str, body: ClientRequest) -> dict[str, str]:
        server.disconnect(name, body.client_id)
        return {"status": "getrennt"}

    @app.get("/api/devices/{name}/state", dependencies=[Depends(auth)])
    def state(name: str) -> dict[str, Any]:
        return server.session(name).snapshot()

    @app.post("/api/devices/{name}/control", dependencies=[Depends(auth)])
    def take_control(name: str, body: ControlRequest) -> dict[str, Any]:
        client = server.require_client(body.client_id)
        server.session(name).take_control(client, force=body.force)
        return {"controller": client}

    @app.post("/api/devices/{name}/control/release", dependencies=[Depends(auth)])
    def release_control(name: str, body: ClientRequest) -> dict[str, Any]:
        released = server.session(name).release_control(body.client_id or "")
        return {"released": released}

    @app.post("/api/devices/{name}/torque", dependencies=[Depends(auth)])
    def torque(name: str, body: TorqueRequest) -> dict[str, Any]:
        server.session(name).set_torque(server.require_client(body.client_id), body.enabled, body.joints)
        return {"torque": body.enabled}

    @app.post("/api/devices/{name}/motion", dependencies=[Depends(auth)])
    def motion(name: str, body: MotionRequest) -> dict[str, int]:
        client = server.require_client(body.client_id)
        return server.session(name).set_motion(client, body.velocity, body.acceleration)

    @app.post("/api/devices/{name}/move", dependencies=[Depends(auth)])
    def move(name: str, body: MoveRequest) -> dict[str, Any]:
        sent = server.session(name).move(server.require_client(body.client_id), body.targets)
        return {"sent": sent}

    # --- Register-Explorer ----------------------------------------------------------

    @app.get("/api/devices/{name}/registers/{joint}", dependencies=[Depends(auth)])
    def read_registers(name: str, joint: str) -> list[dict[str, Any]]:
        return server.session(name).read_registers(joint)

    @app.post("/api/devices/{name}/registers", dependencies=[Depends(auth)])
    def write_register(name: str, body: RegisterWriteRequest) -> dict[str, Any]:
        client = server.require_client(body.client_id)
        value = server.session(name).write_register(
            client, body.joint, body.register_name, body.value, confirm_eeprom=body.confirm_eeprom
        )
        return {"register": body.register_name, "value": value}

    # --- Kalibrierung -----------------------------------------------------------------

    @app.post("/api/devices/{name}/calibration/{step}", dependencies=[Depends(auth)])
    def calibration(name: str, step: str, body: ClientRequest) -> dict[str, Any] | None:
        session = server.session(name)
        steps = {
            "start": session.calibration_start,
            "middle": session.calibration_middle,
            "finish": session.calibration_finish,
            "cancel": session.calibration_cancel,
        }
        if step not in steps:
            raise HTTPException(404, f"Unbekannter Kalibrierschritt '{step}'.")
        return steps[step](server.require_client(body.client_id))

    # --- Einrichtungsassistent (Servo-IDs) -------------------------------------------

    @app.get("/api/setup/types", dependencies=[Depends(auth)])
    def setup_types() -> dict[str, Any]:
        from ..devices.spec import DEVICE_TYPES, get_device_spec
        from ..servo_setup import setup_order

        types = []
        for device_type in DEVICE_TYPES:
            spec = get_device_spec(device_type)
            joints = [{"name": j.name, "id": j.motor_id} for j in setup_order(spec)]
            types.append({"type": device_type, "description": spec.description, "joints": joints})
        return {"types": types, "mock_port": MOCK_PORT, "simulations": list(SIMULATED_PLUGS)}

    @app.get("/api/setup", dependencies=[Depends(auth)])
    def setup_state() -> dict[str, Any] | None:
        return server.setup.snapshot() if server.setup else None

    @app.post("/api/setup/start", dependencies=[Depends(auth)])
    def setup_start(body: SetupStartRequest) -> dict[str, Any]:
        client = server.require_client(body.client_id)
        return server.start_setup(client, body.port, body.device_type).snapshot()

    @app.post("/api/setup/scan", dependencies=[Depends(auth)])
    def setup_scan(body: ClientRequest) -> dict[str, Any]:
        client = server.require_client(body.client_id)
        return server.require_setup(client).scan(client)

    @app.post("/api/setup/assign", dependencies=[Depends(auth)])
    def setup_assign(body: SetupTargetRequest) -> dict[str, Any]:
        client = server.require_client(body.client_id)
        return server.require_setup(client).assign(client, body.joint, body.id)

    @app.post("/api/setup/verify", dependencies=[Depends(auth)])
    def setup_verify(body: SetupTargetRequest) -> dict[str, Any]:
        client = server.require_client(body.client_id)
        return server.require_setup(client).verify(client, body.joint, body.id)

    @app.post("/api/setup/simulate", dependencies=[Depends(auth)])
    def setup_simulate(body: SetupSimulateRequest) -> dict[str, Any]:
        client = server.require_client(body.client_id)
        return server.require_setup(client).simulate_plug(client, body.variant)

    @app.post("/api/setup/stop", dependencies=[Depends(auth)])
    def setup_stop(body: ClientRequest) -> dict[str, str]:
        server.stop_setup(body.client_id)
        return {"status": "beendet"}

    @app.post("/api/estop", dependencies=[Depends(auth)])
    def estop() -> dict[str, Any]:
        return {"stopped": server.emergency_stop()}

    # --- Live-Verbindung ----------------------------------------------------------

    @app.websocket("/ws")
    async def live(ws: WebSocket) -> None:
        if not server.check_token(_token_from(ws.headers, ws.query_params)):
            await ws.close(code=4401, reason="Token fehlt oder ist falsch.")
            return
        await ws.accept()
        client_id = uuid.uuid4().hex[:12]
        server.clients.add(client_id)
        await ws.send_json({"type": "hello", "client_id": client_id})

        async def send_state() -> None:
            while True:
                setup = server.setup
                await ws.send_json(
                    {
                        "type": "state",
                        "devices": {n: s.snapshot() for n, s in list(server.sessions.items())},
                        "setup": setup.snapshot() if setup else None,
                    }
                )
                await asyncio.sleep(STATE_INTERVAL_S)

        sender = asyncio.create_task(send_state())
        try:
            while True:
                message = await ws.receive_json()
                if message.get("type") == "heartbeat":
                    for session in list(server.sessions.values()):
                        session.heartbeat(client_id)
        except (WebSocketDisconnect, RuntimeError, ValueError):
            pass
        finally:
            sender.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await sender
            # Tab geschlossen oder Netz weg: Steuerrecht abgeben, sicherer Zustand.
            await asyncio.to_thread(server.client_gone, client_id)

    # --- Seiten -------------------------------------------------------------------

    if docs_dir is not None and docs_dir.is_dir():
        app.mount("/docs", StaticFiles(directory=docs_dir, html=True), name="docs")
    app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
    return app


def _known(server: LabServer, name: str) -> None:
    if name not in server.config().devices:
        raise HTTPException(404, f"Gerät '{name}' steht nicht in arms.toml.")
