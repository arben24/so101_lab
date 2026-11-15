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
import json
import logging
import secrets
import threading
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .. import __version__
from ..backends.base import BackendError
from ..calibration_files import CalibrationFileError, CalRef
from ..config import ConfigError, load_config
from ..lab_dataset import DatasetError
from ..safety import connect_arm
from ..trajectories import TrajectoryError
from .bus_server import DEFAULT_BUS_WATCHDOG_MS, RemoteBusSession
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
        bus_watchdog_ms: int = DEFAULT_BUS_WATCHDOG_MS,
    ):
        self.config_path = config_path
        self.token = token or None
        self.watchdog_s = watchdog_ms / 1000
        self.bus_watchdog_s = bus_watchdog_ms / 1000
        self.poll_hz = poll_hz
        self.sessions: dict[str, DeviceSession] = {}
        self.remote_buses: dict[str, RemoteBusSession] = {}  # Geräte, die ein Laptop über /ws/bus benutzt
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
                if port and (remote := self._remote_on_port(port)):
                    raise HTTPException(
                        409,
                        f"Port {port} wird gerade über das Netz benutzt ({remote.name}, von {remote.peer}).",
                    )
                if port and self.setup is not None and self.setup.port == port:
                    raise HTTPException(
                        409, f"Port {port} gehört gerade dem Einrichtungsassistenten. Erst dort beenden."
                    )
            if name in self.remote_buses:
                raise HTTPException(409, f"Gerät '{name}' wird gerade über das Netz benutzt.")
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
        for bus in list(self.remote_buses.values()):
            try:
                bus.emergency_stop()
            except Exception:
                logger.exception("Not-Aus: Bus-Freigabe %s", bus.name)
            stopped.append(bus.name)
        return stopped

    # ------------------------------------------------------------ Bus-Freigabe (RemoteBackend)

    def _remote_on_port(self, port: str) -> RemoteBusSession | None:
        return next((b for b in self.remote_buses.values() if b.port == port), None)

    def open_remote_bus(self, name: str, peer: str) -> RemoteBusSession:
        cfg = self.config().device(name)
        with self._lock:
            if name in self.remote_buses:
                other = self.remote_buses[name]
                raise ControlError(f"'{name}' wird schon von {other.peer} über das Netz benutzt.")
            if name in self.sessions:
                raise ControlError(f"'{name}' ist hier in der Web-App verbunden. Erst dort trennen.")
            port = cfg.port if cfg.backend == "usb" else None
            if port:
                busy = [s.name for s in self.sessions.values() if s.port == port]
                if busy:
                    raise ControlError(f"Port {port} ist hier mit '{busy[0]}' verbunden. Erst dort trennen.")
                if (remote := self._remote_on_port(port)) is not None:
                    raise ControlError(f"Port {port} wird schon von {remote.peer} benutzt ({remote.name}).")
                if self.setup is not None and self.setup.port == port:
                    raise ControlError(f"Port {port} gehört gerade dem Einrichtungsassistenten.")
            bus = RemoteBusSession(cfg, peer, self.bus_watchdog_s)
            self.remote_buses[name] = bus
            logger.info("Bus-Freigabe: %s für %s geöffnet", name, peer)
            return bus

    def close_remote_bus(self, bus: RemoteBusSession, safe_reason: str | None) -> None:
        """Freigabe beenden. `safe_reason` gesetzt = Verbindung unsauber weg: erst sicher zurücksetzen."""
        with self._lock:
            if self.remote_buses.get(bus.name) is not bus:
                return  # schon geschlossen (z. B. beim Beenden des Servers)
            del self.remote_buses[bus.name]
        if safe_reason and bus.stopped_reason is None:
            bus.safe_shutdown(safe_reason)
        bus.close()
        logger.info("Bus-Freigabe: %s geschlossen (%s)", bus.name, safe_reason or "ordentlich beendet")

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
            if (remote := self._remote_on_port(port)) is not None:
                raise ControlError(f"Port {port} wird gerade über das Netz benutzt (von {remote.peer}).")
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
        for bus in list(self.remote_buses.values()):
            self.close_remote_bus(bus, "Server wird beendet")
        setup, self.setup = self.setup, None
        if setup is not None:
            setup.close()

    def check_token(self, presented: str | None) -> bool:
        if self.token is None:
            return True
        return presented is not None and secrets.compare_digest(presented.encode(), self.token.encode())


async def _refuse(ws: WebSocket, message: str, code: int) -> None:
    """Grund als Nachricht schicken (der Laptop zeigt ihn an), dann schließen."""
    with contextlib.suppress(Exception):
        await ws.send_json({"type": "error", "message": message})
        await ws.close(code=code, reason=message[:120])


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


class RecordingRequest(ClientRequest):
    name: str
    overwrite: bool = False


class PlaybackRequest(ClientRequest):
    trajectory: str
    speed: float = 1.0


class TeleopRequest(ClientRequest):
    leader: str  # Gerätename des Leaders (muss verbunden sein)


class CalibrationImportRequest(ClientRequest):
    device_type: str  # so101_follower | so101_leader | lekiwi[_arm|_base] (bestimmt den Ordner)
    cal_id: str
    content: Any  # Inhalt der JSON-Datei
    overwrite: bool = False


class OverwriteRequest(ClientRequest):
    overwrite: bool = False


class DatasetOpenRequest(ClientRequest):
    name: str
    task: str
    fps: int = 30


class EpisodeStopRequest(ClientRequest):
    keep: bool = True


class SetupStartRequest(ClientRequest):
    port: str  # serieller Port oder "mock" (Simulation)
    device_type: str  # so101_follower | so101_leader | lekiwi | lekiwi_arm | lekiwi_base


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

    @app.exception_handler(CalibrationFileError)
    async def _calfile_error(_request: Request, exc: CalibrationFileError) -> JSONResponse:
        return JSONResponse({"detail": str(exc)}, status_code=400)

    @app.exception_handler(DatasetError)
    async def _dataset_error(_request: Request, exc: DatasetError) -> JSONResponse:
        return JSONResponse({"detail": str(exc)}, status_code=400)

    @app.exception_handler(TrajectoryError)
    async def _trajectory_error(_request: Request, exc: TrajectoryError) -> JSONResponse:
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
                "remote": server.remote_buses[d.name].peer if d.name in server.remote_buses else None,
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
            "apply": session.apply_calibration,  # Datei → EEPROM der Servos
            "reload": session.reload_calibration,  # Datei neu laden
        }
        if step not in steps:
            raise HTTPException(404, f"Unbekannter Kalibrierschritt '{step}'.")
        return steps[step](server.require_client(body.client_id))

    # --- Aufnehmen, Abspielen, Teleoperation -----------------------------------------

    @app.post("/api/devices/{name}/recording/start", dependencies=[Depends(auth)])
    def recording_start(name: str, body: RecordingRequest) -> dict[str, Any]:
        client = server.require_client(body.client_id)
        return server.session(name).start_recording(client, body.name, overwrite=body.overwrite)

    @app.post("/api/devices/{name}/recording/stop", dependencies=[Depends(auth)])
    def recording_stop(name: str, body: ClientRequest) -> dict[str, Any] | None:
        return server.session(name).stop_recording(server.require_client(body.client_id))

    @app.get("/api/devices/{name}/playback/check", dependencies=[Depends(auth)])
    def playback_check(name: str, trajectory: str, speed: float = 1.0) -> dict[str, Any]:
        """Vorschau: Anfahrt, Dauer, Umrechnung und Hinweise. Bewegt nichts."""
        return server.session(name).check_playback(trajectory, speed)

    @app.post("/api/devices/{name}/playback/start", dependencies=[Depends(auth)])
    def playback_start(name: str, body: PlaybackRequest) -> dict[str, Any]:
        client = server.require_client(body.client_id)
        return server.session(name).start_playback(client, body.trajectory, body.speed)

    @app.post("/api/devices/{name}/playback/stop", dependencies=[Depends(auth)])
    def playback_stop(name: str, body: ClientRequest) -> dict[str, str]:
        server.session(name).stop_playback(server.require_client(body.client_id))
        return {"status": "gestoppt"}

    @app.post("/api/devices/{name}/teleop/start", dependencies=[Depends(auth)])
    def teleop_start(name: str, body: TeleopRequest) -> dict[str, Any]:
        client = server.require_client(body.client_id)
        follower, leader = server.session(name), server.session(body.leader)
        return follower.start_teleop(client, leader)

    @app.post("/api/devices/{name}/teleop/stop", dependencies=[Depends(auth)])
    def teleop_stop(name: str, body: ClientRequest) -> dict[str, str]:
        server.session(name).stop_teleop(server.require_client(body.client_id))
        return {"status": "gestoppt"}

    # --- Kalibrierdateien -----------------------------------------------------------------

    def config_or_none():
        try:
            return server.config()
        except ConfigError:
            return None

    def reload_users(ref: CalRef) -> None:
        """Verbundene Geräte, die diese Kalibrierung benutzen, laden ihre Datei neu."""
        for session in list(server.sessions.values()):
            spec, cfg = session.arm.spec, session.arm.cfg
            if (spec.lerobot_kind, spec.lerobot_name, cfg.calibration_id) == (ref.kind, ref.name, ref.cal_id):
                with contextlib.suppress(ControlError):  # läuft gerade eine Kalibrierung: nicht anfassen
                    session.reload_calibration(quiet=True)

    @app.get("/api/devices/{name}/calibration/compare", dependencies=[Depends(auth)])
    def calibration_compare(name: str) -> dict[str, Any]:
        return server.session(name).compare_calibration()

    @app.get("/api/calibrations", dependencies=[Depends(auth)])
    def calibrations() -> list[dict[str, Any]]:
        from ..calibration_files import list_files

        return list_files(config_or_none())

    @app.get("/api/calibrations/{source}/{kind}/{folder}/{cal_id}", dependencies=[Depends(auth)])
    def calibration_file(
        source: str, kind: str, folder: str, cal_id: str, backup: bool = False
    ) -> dict[str, Any]:
        from ..calibration_files import read_file

        return read_file(CalRef(source, kind, folder, cal_id), config_or_none(), backup=backup)

    @app.get("/api/calibrations/{source}/{kind}/{folder}/{cal_id}/json", dependencies=[Depends(auth)])
    def calibration_download(source: str, kind: str, folder: str, cal_id: str) -> FileResponse:
        ref = CalRef(source, kind, folder, cal_id)
        if not ref.path.is_file():
            raise HTTPException(404, f"{ref.path} gibt es nicht.")
        return FileResponse(ref.path, media_type="application/json", filename=ref.path.name)

    @app.post("/api/calibrations/{source}/{kind}/{folder}/{cal_id}/{action}", dependencies=[Depends(auth)])
    def calibration_action(
        source: str, kind: str, folder: str, cal_id: str, action: str, body: OverwriteRequest
    ) -> dict[str, Any]:
        from .. import calibration_files as files

        server.require_client(body.client_id)
        ref = CalRef(source, kind, folder, cal_id)
        if action == "delete":
            path = files.delete(ref)
            reload_users(ref)
            return {"backup": str(path)}
        if action == "restore":
            path = files.restore(ref)
            reload_users(ref)
            return {"path": str(path)}
        if action == "copy":
            path = files.copy_to_repo(ref, overwrite=body.overwrite)
            reload_users(CalRef("repo", kind, folder, cal_id))
            return {"path": str(path)}
        raise HTTPException(404, f"Unbekannte Aktion '{action}'.")

    @app.post("/api/calibrations/import", dependencies=[Depends(auth)])
    def calibration_import(body: CalibrationImportRequest) -> dict[str, Any]:
        from ..calibration_files import save_new
        from ..devices import get_device_spec

        server.require_client(body.client_id)
        try:
            spec = get_device_spec(body.device_type)
        except KeyError as exc:
            raise HTTPException(400, str(exc.args[0])) from None
        ref = CalRef("repo", spec.lerobot_kind, spec.lerobot_name, body.cal_id)
        path = save_new(ref, body.content, overwrite=body.overwrite)
        reload_users(ref)
        return {"path": str(path)}

    # --- Kameras ---------------------------------------------------------------------------

    @app.get("/api/cameras", dependencies=[Depends(auth)])
    def cameras() -> list[dict[str, Any]]:
        """Kameras dieses Rechners. Schon geöffnete werden nicht noch einmal geöffnet."""
        from ..cameras import find_cameras

        in_use = {c.spec.source for s in server.sessions.values() for c in s.cameras.values()}
        return find_cameras(skip=in_use)

    def camera_of(name: str, cam: str):
        camera = server.session(name).cameras.get(cam)
        if camera is None:
            raise HTTPException(404, f"Gerät '{name}' hat keine geöffnete Kamera '{cam}'.")
        return camera

    @app.get("/api/devices/{name}/cameras/{cam}/frame.jpg", dependencies=[Depends(auth)])
    def camera_frame(name: str, cam: str) -> Response:
        from ..cameras import to_jpeg

        frame = camera_of(name, cam).latest()
        if frame is None:
            raise HTTPException(503, f"Kamera '{cam}' liefert gerade kein Bild.")
        return Response(to_jpeg(frame), media_type="image/jpeg", headers={"Cache-Control": "no-store"})

    @app.get("/api/devices/{name}/cameras/{cam}/stream", dependencies=[Depends(auth)])
    async def camera_stream(name: str, cam: str, request: Request, fps: float = 10) -> StreamingResponse:
        """MJPEG-Strom für ein <img>: höchstens `fps` Bilder je Sekunde (Vorschau, nicht Aufnahme)."""
        from ..cameras import to_jpeg

        camera = camera_of(name, cam)
        period = 1.0 / max(1.0, min(30.0, fps))

        async def frames():
            last = None
            while not await request.is_disconnected() and name in server.sessions:
                frame = camera.latest()
                if frame is not None and frame is not last:
                    last = frame
                    jpeg = await asyncio.to_thread(to_jpeg, frame, 70)
                    yield (
                        b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: "
                        + str(len(jpeg)).encode()
                        + b"\r\n\r\n"
                        + jpeg
                        + b"\r\n"
                    )
                await asyncio.sleep(period)

        return StreamingResponse(
            frames(),
            media_type="multipart/x-mixed-replace; boundary=frame",
            headers={"Cache-Control": "no-store"},
        )

    # --- LeRobot-Dataset -----------------------------------------------------------------

    @app.post("/api/devices/{name}/dataset/open", dependencies=[Depends(auth)])
    def dataset_open(name: str, body: DatasetOpenRequest) -> dict[str, Any]:
        client = server.require_client(body.client_id)
        return server.session(name).dataset_open(client, body.name, body.task, body.fps)

    @app.post("/api/devices/{name}/dataset/episode/start", dependencies=[Depends(auth)])
    def episode_start(name: str, body: ClientRequest) -> dict[str, Any]:
        return server.session(name).episode_start(server.require_client(body.client_id))

    @app.post("/api/devices/{name}/dataset/episode/stop", dependencies=[Depends(auth)])
    def episode_stop(name: str, body: EpisodeStopRequest) -> dict[str, Any]:
        client = server.require_client(body.client_id)
        return server.session(name).episode_stop(client, keep=body.keep)

    @app.post("/api/devices/{name}/dataset/close", dependencies=[Depends(auth)])
    def dataset_close(name: str, body: ClientRequest) -> dict[str, str]:
        server.session(name).dataset_close(server.require_client(body.client_id))
        return {"status": "abgeschlossen"}

    @app.get("/api/datasets", dependencies=[Depends(auth)])
    def datasets() -> list[dict[str, Any]]:
        from ..lab_dataset import list_datasets

        return list_datasets()

    @app.delete("/api/datasets/{ds_name}", dependencies=[Depends(auth)])
    def dataset_delete(ds_name: str, client_id: str | None = None) -> dict[str, str]:
        from ..lab_dataset import delete_dataset

        server.require_client(client_id)
        if any(s.dataset is not None and s.dataset.name == ds_name for s in server.sessions.values()):
            raise ControlError(f"Das Dataset '{ds_name}' ist gerade geöffnet. Erst abschließen.")
        delete_dataset(ds_name)
        return {"status": "gelöscht"}

    # --- Bibliothek der Aufnahmen ------------------------------------------------------

    @app.get("/api/trajectories", dependencies=[Depends(auth)])
    def trajectories() -> list[dict[str, Any]]:
        from ..trajectories import list_trajectories

        return list_trajectories()

    @app.get("/api/trajectories/{traj_name}", dependencies=[Depends(auth)])
    def trajectory(traj_name: str) -> dict[str, Any]:
        """Beschreibung und Kurven (auf höchstens 400 Punkte ausgedünnt) für die Anzeige."""
        from ..trajectories import check_name, load_trajectory

        traj = load_trajectory(check_name(traj_name))
        step = max(1, len(traj.times) // 400)
        index = list(range(0, len(traj.times), step))
        if index[-1] != len(traj.times) - 1:
            index.append(len(traj.times) - 1)
        meta = {k: v for k, v in traj.meta.items() if k != "calibration"}
        return {
            **meta,
            "name": traj.name,
            "joints": list(traj.joints),
            "has_calibration": bool(traj.meta.get("calibration")),
            "times": [round(traj.times[i], 4) for i in index],
            "positions": [list(traj.positions[i]) for i in index],
        }

    @app.get("/api/trajectories/{traj_name}/csv", dependencies=[Depends(auth)])
    def trajectory_csv(traj_name: str) -> FileResponse:
        from ..trajectories import check_name, default_directory

        path = default_directory() / f"{check_name(traj_name)}.csv"
        if not path.exists():
            raise HTTPException(404, f"Aufnahme '{traj_name}' nicht gefunden.")
        return FileResponse(path, media_type="text/csv", filename=path.name)

    @app.delete("/api/trajectories/{traj_name}", dependencies=[Depends(auth)])
    def trajectory_delete(traj_name: str, client_id: str | None = None) -> dict[str, str]:
        from ..trajectories import delete_trajectory

        server.require_client(client_id)
        delete_trajectory(traj_name)
        return {"status": "gelöscht"}

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
                        "remote": {n: b.snapshot() for n, b in list(server.remote_buses.items())},
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
            # Tab geschlossen oder Netz weg: Steuerrecht abgeben, sicherer Zustand. Als eigene
            # Aufgabe gestartet, bevor irgendetwas gewartet wird: Wird dieser Handler selbst
            # abgebrochen (Server-Ende, Test-Client), läuft das Aufräumen trotzdem zu Ende.
            cleanup = asyncio.ensure_future(asyncio.to_thread(server.client_gone, client_id))
            sender.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await sender
            await asyncio.shield(cleanup)

    @app.websocket("/ws/bus/{name}")
    async def bus(ws: WebSocket, name: str) -> None:
        """Bus-Freigabe für das RemoteBackend eines anderen Rechners (siehe web/bus_server.py)."""
        await ws.accept()
        peer = f"{ws.client.host}:{ws.client.port}" if ws.client else "unbekannt"
        if not server.check_token(_token_from(ws.headers, ws.query_params)):
            await _refuse(ws, "Token fehlt oder ist falsch.", 4401)
            return
        try:
            bus = await asyncio.to_thread(server.open_remote_bus, name, peer)
        except (ConfigError, ControlError, BackendError) as exc:
            await _refuse(ws, str(exc), 4409)
            return
        await ws.send_json(bus.hello())
        reason: str | None = "Verbindung getrennt"
        try:
            while True:
                try:
                    text = await asyncio.wait_for(ws.receive_text(), timeout=bus.watchdog_s)
                except TimeoutError:
                    reason = f"Watchdog: {round(bus.watchdog_s * 1000)} ms ohne Lebenszeichen"
                    await _refuse(ws, reason, 4408)
                    break
                try:
                    message = json.loads(text)
                    request_id, op = message.get("id"), message.get("op")
                except (ValueError, AttributeError):
                    await ws.send_json(
                        {"id": None, "ok": False, "error": "BackendError", "message": "Kein JSON."}
                    )
                    continue
                if op == "close":
                    reason = None  # ordentlich: der Laptop hat schon selbst zurückgesetzt
                    await ws.send_json({"id": request_id, "ok": True, "result": None})
                    await ws.close()
                    break
                if bus.stopped_reason:
                    reason = bus.stopped_reason
                    await _refuse(ws, bus.stopped_reason, 4410)
                    break
                if op == "heartbeat":
                    await ws.send_json({"id": request_id, "ok": True, "result": None})
                    continue
                reply = await asyncio.to_thread(bus.call, request_id, op, message.get("args") or {})
                await ws.send_json(reply)
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            # wie bei /ws: auch bei Abbruch dieses Handlers sicher zurücksetzen
            await asyncio.shield(
                asyncio.ensure_future(asyncio.to_thread(server.close_remote_bus, bus, reason))
            )

    # --- Seiten -------------------------------------------------------------------

    if docs_dir is not None and docs_dir.is_dir():
        app.mount("/docs", StaticFiles(directory=docs_dir, html=True), name="docs")
    app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
    return app


def _known(server: LabServer, name: str) -> None:
    if name not in server.config().devices:
        raise HTTPException(404, f"Gerät '{name}' steht nicht in arms.toml.")
