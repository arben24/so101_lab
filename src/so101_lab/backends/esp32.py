"""ESP32-Backend: Servo-Bus an einem ESP32-Funkknoten (firmware/esp32), über WLAN (UDP 4210).

Der Knoten ist kein „verlängertes USB-Kabel“ wie der Pi beim RemoteBackend. Er hat eine
eigene 50-Hz-Schleife mit einem festen Profil (`lekiwi`, `so101`, `so101_leader`) und
einer **Sollwerttabelle**: je Servo Drehmoment an/aus und ein Zielwert (Position bzw.
Radtempo). Daraus folgt, was dieses Backend kann und was nicht (Kapitel 07):

| Backend-Aufruf | über den Knoten |
|---|---|
| `read_positions`, `read_state` | ein `ZUSTAND_ANFRAGE`, Abbild der 50-Hz-Schleife (ohne Strom) |
| `goal_position`, Radtempo, `torque_enable` | Sollwerttabelle, ein `BEFEHL` mit allen Servos des Profils |
| andere Register (Grenzen, Beschleunigung, EEPROM) | `REG_LESEN`/`REG_SCHREIBEN` einzeln, etwa 50/s |
| `sync_read`/`sync_write` anderer Register | Folge einzelner Registerzugriffe |
| `scan` | je ID ein `REG_LESEN`, nur 1 Mbit/s, alle 254 IDs dauern einige Sekunden |
| andere Baudraten, Baudrate ändern, ID-Assistent | nein, der Knoten-Bus läuft fest mit 1 Mbit/s |

Sicherheit:
- **Ganze Tabelle schicken.** Der erste `BEFEHL` einer neuen Quelle aktiviert die ganze
  Sollwerttabelle des Knotens, auch alte Einträge einer früheren Quelle. Darum geht jeder
  `BEFEHL` mit allen Servos des Profils raus. Die Tabelle startet mit dem Istzustand
  (Drehmoment wie gemeldet, Ziel = Istposition, Rad = 0).
- **Kein Sprung beim Einschalten.** Geht ein Servo von aus nach an, ohne dass vorher ein
  Ziel gesetzt wurde, wird die gerade gemessene Position zum Ziel (Rad: 0).
- **Lebenszeichen.** Solange das Backend steuert, wiederholt ein Thread den `BEFEHL` alle
  100 ms (Watchdog des Knotens Standard 300 ms). Fällt der Laptop aus, stehen die Räder und
  der Arm **hält** die Stellung (Firmware; `safe_state` gilt dort nicht).
- **Nach einer Lücke nicht weitermachen.** War länger als der Watchdog keine Antwort da,
  ist der Knoten im sicheren Zustand. Das Backend schickt dann keine alten Sollwerte mehr
  (die Räder würden wieder anfahren), sondern verlangt ein neues Verbinden.
- **Belegt:** Steuert eine andere Quelle, lehnt der Knoten ab. Muss dann etwas anhalten
  (Drehmoment aus, Räder stopp), schickt das Backend `STOPP` (darf jeder): Räder stehen,
  Arm hält, **Drehmoment bleibt an**.
- Das Protokoll hat **keine Anmeldung**. Jeder im selben Netz kann Register schreiben.
"""

from __future__ import annotations

import logging
import socket
import threading
import time
from collections.abc import Iterable, Sequence

from .. import registers as regs
from ..devices.spec import DeviceSpec, JointMode
from . import esp32_protocol as p
from .base import (
    ALL_IDS,
    DEFAULT_BAUDRATE,
    ArmBackend,
    BackendError,
    Capability,
    FoundServo,
    NotSupportedError,
    ServoNotRespondingError,
    ServoState,
)

logger = logging.getLogger(__name__)

TIMEOUT_S = 0.15  # Wartezeit je Versuch (Registerzugriffe brauchen bis 2 Zyklen à 20 ms)
RETRIES = 3
KEEPALIVE_S = 0.1
REG_BUSY_PAUSE_S = 0.03  # Warteschlange voll (2 Aufträge): kurz warten, nochmal
WHEEL_ZERO_WAIT_S = 0.045  # gut zwei Zyklen der 50-Hz-Schleife

# Register, die der Knoten in jedem ZUSTAND mitschickt (nur für Servos des Profils)
ZUSTAND_FIELDS = {
    "present_position": lambda s: s.position,
    "present_velocity": lambda s: s.velocity,
    "present_load": lambda s: s.load,
    "present_voltage": lambda s: s.voltage_raw,
    "present_temperature": lambda s: s.temperature,
    "status": lambda s: s.status,
    "torque_enable": lambda s: int(s.torque),
}

# Gerätetyp in arms.toml → passende Profile auf dem Knoten
PROFILES = {
    "so101_follower": ("so101", "lekiwi"),
    "so101_leader": ("so101_leader", "so101"),
    "lekiwi": ("lekiwi",),
    "lekiwi_arm": ("lekiwi",),
    "lekiwi_base": ("lekiwi",),
}


class Esp32Error(BackendError):
    """Verbindung zum Knoten fehlt oder ist abgebrochen."""


class Esp32BusyError(BackendError):
    """Eine andere Quelle steuert den Knoten."""


def check_profile(info: p.Info, spec: DeviceSpec) -> None:
    """Passt das Profil des Knotens zum Gerät in arms.toml? Sonst Esp32Error mit Abhilfe."""
    allowed = PROFILES.get(spec.type, ())
    if info.profile not in allowed:
        raise Esp32Error(
            f"Knoten '{info.name}' läuft mit Profil '{info.profile}', für {spec.type} passt "
            f"{' oder '.join(allowed) or 'keins'}. Profil auf der Webseite des Knotens umstellen."
        )
    wanted = {JointMode.POSITION: p.Modus.POSITION, JointMode.VELOCITY: p.Modus.TEMPO}
    for joint in spec.joints:
        mode = info.modus(joint.motor_id)
        if mode is None or mode != wanted[joint.mode]:
            raise Esp32Error(
                f"{joint.name} (ID {joint.motor_id}) fehlt im Profil '{info.profile}' des Knotens."
            )


class Esp32Backend(ArmBackend):
    name = "esp32"

    def __init__(
        self,
        address: str,
        spec: DeviceSpec | None = None,
        port: int = p.UDP_PORT,
        timeout_s: float = TIMEOUT_S,
        retries: int = RETRIES,
    ):
        host, _, maybe_port = address.rpartition(":")
        if host and maybe_port.isdigit():  # "lekiwi-esp.local:4210" oder "127.0.0.1:5000" (Simulation)
            address, port = host, int(maybe_port)
        self.address = address
        self.udp_port = port
        self.spec = spec
        self.timeout_s = timeout_s
        self.retries = retries
        self.baudrate = DEFAULT_BAUDRATE
        self.capabilities: frozenset[Capability] = frozenset()
        self.info: p.Info | None = None
        self.last_zustand: p.Zustand | None = None
        self._sock: socket.socket | None = None
        self._lock = threading.RLock()
        self._seq = 0
        self._last_seq = -1  # seq der zuletzt beantworteten Anfrage
        # Sollwerttabelle (Spiegel der Tabelle im Knoten): ID → [Drehmoment, Wert]
        self._table: dict[int, list] = {}
        self._fresh: set[int] = set()  # Ziel gesetzt, während das Drehmoment aus war
        self._control = False  # ein BEFEHL von uns wurde angenommen
        self._last_ok = 0.0  # Zeit der letzten angenommenen BEFEHL-Antwort
        self._broken: str | None = None
        self._stop = threading.Event()
        self._keepalive: threading.Thread | None = None
        self._closing = False  # beim Trennen keinen neuen Lebenszeichen-Thread starten

    # ------------------------------------------------------------ Verbindung

    def connect(self) -> None:
        if self._sock is not None:
            return
        try:
            host = socket.gethostbyname(self.address)
        except OSError as exc:
            raise Esp32Error(
                f"ESP32 '{self.address}' nicht gefunden ({exc}). Adresse oder Name prüfen, "
                "`so101-lab esp32 suchen` zeigt die Knoten im Netz."
            ) from exc
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.connect((host, self.udp_port))  # nimmt nur noch Pakete dieses Knotens an
        self._sock = sock
        try:
            self.info = p.parse_info(self._request(p.Typ.PING, expect=p.Typ.INFO))
            if self.spec is not None:
                check_profile(self.info, self.spec)
            z = self.zustand()
        except BaseException:
            self._close_socket()
            raise
        modes = dict(self.info.servos)
        self._table = {
            sid: [s.torque, s.position if modes.get(sid) == p.Modus.POSITION else 0]
            for sid, s in z.servos.items()
        }
        self._fresh = set()
        self._control = False
        self._broken = None
        self._closing = False
        caps = {Capability.SCAN, Capability.REGISTERS, Capability.EEPROM}
        if p.Modus.TEMPO in modes.values():
            caps.add(Capability.WHEELS)
        self.capabilities = frozenset(caps)
        logger.info(
            "ESP32: %s (%s, Profil %s, Firmware %d, Watchdog %d ms, %s)",
            self.info.name,
            host,
            self.info.profile,
            self.info.firmware,
            self.info.watchdog_ms,
            self.info.mode,
        )

    def disconnect(self) -> None:
        """Steuerrecht mit STOPP freigeben (Räder stehen, Arm hält), Socket schließen.

        Das Zurücksetzen nach `safe_state` hat vorher `open_arm` bzw. die Web-App erledigt.
        """
        if self._sock is None:
            return
        self._closing = True
        self._stop.set()
        if self._keepalive is not None:
            self._keepalive.join(timeout=1.0)
            self._keepalive = None
        if self._control and self._broken is None:
            try:
                self._wait_applied()
                self.stop()
            except BackendError as exc:
                logger.warning("ESP32: STOPP beim Trennen fehlgeschlagen: %s", exc)
        self._close_socket()
        logger.info("ESP32: %s getrennt", self.address)

    def _wait_applied(self, timeout_s: float = 0.25) -> None:
        """Warten, bis der Knoten das Drehmoment so meldet, wie es in der Tabelle steht.

        Nötig vor STOPP: Der sichere Zustand übernimmt das Drehmoment, wie die Firmware es
        gerade kennt. Käme STOPP im selben 20-ms-Zyklus wie „Drehmoment aus“, bliebe es an.
        """
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            z = self._send_table()  # hält zugleich den Watchdog des Knotens
            if all(
                s.torque == bool(self._table[sid][0])
                for sid, s in z.servos.items()
                if s.online and sid in self._table
            ):
                return
            time.sleep(0.02)
        logger.warning("ESP32: Drehmoment laut Knoten noch nicht wie gewünscht, STOPP trotzdem.")

    def _close_socket(self) -> None:
        sock, self._sock = self._sock, None
        if sock is not None:
            sock.close()

    @property
    def is_connected(self) -> bool:
        return self._sock is not None and self._broken is None

    @property
    def watchdog_s(self) -> float:
        return (self.info.watchdog_ms if self.info else 300) / 1000

    # ------------------------------------------------------------ Pakete

    def _request(self, typ: int, payload: bytes = b"", *, expect: int, retries: int | None = None) -> bytes:
        """Anfrage schicken, passende Antwort (gleiche seq, erwarteter Typ) abwarten, sonst wiederholen.

        Die Sperre gilt je Versuch, nicht für alle Versuche: Zwischen zwei Versuchen kommt das
        Lebenszeichen durch, sonst schlüge der Watchdog des Knotens an. Späte Antworten eines
        früheren Versuchs fallen über die seq heraus.
        """
        for _ in range(retries or self.retries):
            with self._lock:
                if self._sock is None:
                    raise Esp32Error("Nicht verbunden. Erst connect() aufrufen.")
                seq = self._next_seq()
                try:
                    self._sock.send(p.build(typ, seq, payload))
                except OSError as exc:
                    raise Esp32Error(f"Senden an den ESP32 fehlgeschlagen: {exc}") from exc
                deadline = time.monotonic() + self.timeout_s
                while (left := deadline - time.monotonic()) > 0:
                    self._sock.settimeout(left)
                    try:
                        raw = self._sock.recv(512)
                    except TimeoutError:
                        break
                    except OSError:  # ICMP „Port nicht erreichbar“: Knoten aus, falsche Adresse
                        time.sleep(min(left, 0.05))
                        break
                    packet = p.parse(raw)
                    if packet and packet[1] == seq and packet[0] == expect:
                        self._last_seq = seq
                        return packet[2]
        raise Esp32Error(
            f"ESP32 '{self.address}' antwortet nicht ({p.Typ(typ).name}, "
            f"{retries or self.retries} Versuche). Ist er eingeschaltet und im selben WLAN?"
        )

    def _next_seq(self) -> int:
        self._seq = (self._seq + 1) & 0xFFFF
        return self._seq

    def zustand(self) -> p.Zustand:
        """Abbild der 50-Hz-Schleife. Liest nur, übernimmt kein Steuerrecht."""
        z = p.parse_zustand(self._request(p.Typ.ZUSTAND_ANFRAGE, expect=p.Typ.ZUSTAND))
        self.last_zustand = z
        return z

    def stop(self) -> p.Zustand:
        """STOPP (darf jeder): Räder stehen, Arm hält, Drehmoment bleibt, Steuerrecht ist frei."""
        z = p.parse_zustand(self._request(p.Typ.STOPP, expect=p.Typ.ZUSTAND))
        with self._lock:
            self._control = False
        return z

    def _send_table(self) -> p.Zustand:
        with self._lock:
            if self._broken:
                raise Esp32Error(f"Verbindung zum ESP32 unterbrochen ({self._broken}). Neu verbinden.")
            if self._control and time.monotonic() - self._last_ok > self.watchdog_s:
                self._broken = "länger als der Watchdog keine Antwort, der Knoten hält"
                self._control = False
                raise Esp32Error(
                    f"ESP32: länger als {self.info.watchdog_ms if self.info else 300} ms keine Antwort. "
                    "Der Knoten hat angehalten (Räder stopp, Arm hält). Neu verbinden."
                )
            setpoints = {sid: (bool(t), int(v)) for sid, (t, v) in self._table.items()}
            try:
                payload = self._request(p.Typ.BEFEHL, p.build_befehl(setpoints), expect=p.Typ.ZUSTAND)
            except Esp32Error as exc:
                if self._control:
                    self._broken = str(exc)
                raise
            z = p.parse_zustand(payload)
            self.last_zustand = z
            if z.busy:
                self._control = False
                raise Esp32BusyError(
                    f"ESP32 '{self.address}' wird gerade von einer anderen Quelle gesteuert "
                    f"({p.QUELLE.get(z.source, z.source)}). Frei, wenn sie {self.watchdog_s * 1000:.0f} ms "
                    "schweigt oder jemand STOPP sendet."
                )
            if z.ack != self._last_seq:
                raise Esp32Error("ESP32 hat den Befehl nicht angenommen (veraltete Sequenznummer).")
            self._control = True
            self._last_ok = time.monotonic()
        if not self._closing and (self._keepalive is None or not self._keepalive.is_alive()):
            self._stop.clear()
            self._keepalive = threading.Thread(
                target=self._keepalive_loop, name="esp32-keepalive", daemon=True
            )
            self._keepalive.start()
        return z

    def _keepalive_loop(self) -> None:
        period = min(KEEPALIVE_S, self.watchdog_s / 3)
        while not self._stop.wait(period / 2):
            with self._lock:
                if not self._control or self._broken:
                    continue
                if time.monotonic() - self._last_ok < period:
                    continue
                try:
                    self._send_table()
                except BackendError as exc:
                    logger.warning("ESP32: Lebenszeichen fehlgeschlagen: %s", exc)

    # ------------------------------------------------------------ Register

    def _reg_read(self, servo_id: int, reg: regs.Register, attempts: int = 3) -> int | None:
        """Ein Register lesen. `None`, wenn der Servo nicht antwortet.

        `ok = 0` heißt: Servo antwortet nicht oder Warteschlange des Knotens voll. Darum
        normalerweise dreimal versuchen; die Suche nimmt einen Versuch (sonst 45 s für alle IDs).
        """
        payload = p.build_reg_request(servo_id, reg.address, reg.size)
        for attempt in range(attempts):
            answer = p.parse_reg_answer(self._request(p.Typ.REG_LESEN, payload, expect=p.Typ.REG_ANTWORT))
            _, _, ok, data = answer
            if ok and len(data) == reg.size:
                return reg.decode(int.from_bytes(data, "little"))
            if attempt < attempts - 1:
                time.sleep(REG_BUSY_PAUSE_S)
        return None

    def _reg_write(self, servo_id: int, reg: regs.Register, value: int) -> None:
        data = reg.encode(int(value)).to_bytes(reg.size, "little")
        payload = p.build_reg_request(servo_id, reg.address, reg.size, data)
        for attempt in range(3):
            _, _, ok, _ = p.parse_reg_answer(
                self._request(p.Typ.REG_SCHREIBEN, payload, expect=p.Typ.REG_ANTWORT)
            )
            if ok:
                return
            if attempt < 2:
                time.sleep(REG_BUSY_PAUSE_S)
        raise ServoNotRespondingError(
            f"Servo {servo_id}: '{reg.name}' schreiben über den ESP32 fehlgeschlagen."
        )

    def _read(self, servo_id: int, name: str) -> int:
        value = self._reg_read(servo_id, regs.lookup(name))
        if value is None:
            raise ServoNotRespondingError(f"Servo {servo_id} antwortet nicht (über ESP32).")
        return value

    def _write(self, servo_id: int, name: str, value: int) -> None:
        """Roh ins Register (für write_eeprom/change_id der Basisklasse)."""
        self._reg_write(servo_id, regs.lookup(name), value)

    def _sync_read(self, ids: Sequence[int], name: str) -> dict[int, int]:
        return {i: self._read(i, name) for i in ids}

    def _sync_write(self, name: str, values: dict[int, int]) -> None:
        for servo_id, value in values.items():
            self._write(servo_id, name, value)

    # ------------------------------------------------------------ Profil-Hilfen

    def _mode(self, servo_id: int) -> p.Modus | None:
        return self.info.modus(servo_id) if self.info else None

    def _profile_states(self, ids: Sequence[int]) -> dict[int, p.ServoZustand] | None:
        """ZUSTAND für diese IDs, wenn alle zum Profil gehören, sonst None."""
        if not ids or any(self._mode(i) is None for i in ids):
            return None
        z = self.zustand()
        offline = [i for i in ids if not z.servos[i].online]
        if offline:
            raise ServoNotRespondingError(f"Keine Antwort von IDs {offline} (laut ESP32).")
        return {i: z.servos[i] for i in ids}

    # ------------------------------------------------------------ Lesen

    def ping(self, servo_id: int, attempts: int = 3) -> int | None:
        return self._reg_read(servo_id, regs.lookup("model_number"), attempts)

    def scan(
        self, ids: Sequence[int] | None = None, baudrates: Sequence[int] | None = None
    ) -> list[FoundServo]:
        if baudrates and set(baudrates) != {DEFAULT_BAUDRATE}:
            raise NotSupportedError(
                "Der ESP32-Bus läuft fest mit 1 Mbit/s. Andere Baudraten nur mit dem USB-Adapter "
                "(Servo einzeln anschließen, Einrichtung in der Web-App)."
            )
        found = []
        for servo_id in ids if ids is not None else ALL_IDS:
            model = self.ping(servo_id, attempts=1)
            if model is None:
                continue
            major = self._reg_read(servo_id, regs.lookup("firmware_major_version"))
            minor = self._reg_read(servo_id, regs.lookup("firmware_minor_version"))
            found.append(
                FoundServo(
                    id=servo_id,
                    model_number=model,
                    model_name=regs.model_names().get(model, f"unbekannt ({model})"),
                    baudrate=DEFAULT_BAUDRATE,
                    firmware=f"{major}.{minor}" if major is not None and minor is not None else None,
                )
            )
        return found

    def read_register(self, servo_id: int, name: str) -> int:
        return self._read(servo_id, name)

    def sync_read(self, ids: Sequence[int], name: str) -> dict[int, int]:
        reg = regs.lookup(name)
        ids = list(ids)
        if not ids:
            return {}
        if reg.name in ZUSTAND_FIELDS and (states := self._profile_states(ids)) is not None:
            return {i: ZUSTAND_FIELDS[reg.name](s) for i, s in states.items()}
        return {i: self._read(i, reg.name) for i in ids}

    def read_positions(self, ids: Sequence[int]) -> dict[int, int]:
        return self.sync_read(ids, "present_position")

    def read_state(self, ids: Sequence[int]) -> dict[int, ServoState]:
        ids = list(ids)
        states = self._profile_states(ids)
        if states is None:
            return super().read_state(ids)  # Servos außerhalb des Profils: Register einzeln
        return {
            i: ServoState(
                id=i,
                position=s.position,
                velocity=s.velocity,
                load=s.load,
                voltage=s.voltage_raw / 10,
                temperature=s.temperature,
                status=s.status,
                torque_enabled=s.torque,
                current_ma=None,  # schickt der Knoten nicht mit
            )
            for i, s in states.items()
        }

    # ------------------------------------------------------------ Schreiben

    def write_register(self, servo_id: int, name: str, value: int) -> None:
        self.sync_write(name, {servo_id: value})

    def sync_write(self, name: str, values: dict[int, int]) -> None:
        if not values:
            return
        reg = None
        for value in values.values():
            reg = self._check_writable(name, value)
        assert reg is not None
        values = {int(k): int(v) for k, v in values.items()}
        if reg.name == "torque_enable":
            for enabled in (False, True):
                ids = [i for i, v in values.items() if bool(v) is enabled]
                if ids:
                    self.set_torque(ids, enabled)
            return
        table_mode = {"goal_position": p.Modus.POSITION, "goal_velocity": p.Modus.TEMPO}.get(reg.name)
        to_table = {i: v for i, v in values.items() if table_mode is not None and self._mode(i) == table_mode}
        rest = {i: v for i, v in values.items() if i not in to_table}
        if reg.name == "goal_position" and rest:
            raise NotSupportedError(
                f"Zielposition für IDs {sorted(rest)} geht über den ESP32 nicht: nicht im Profil "
                f"'{self.info.profile if self.info else '?'}' als Positions-Servo."
            )
        for servo_id, value in rest.items():  # z. B. goal_velocity der Gelenke = Tempogrenze
            self._reg_write(servo_id, reg, value)
        if to_table:
            with self._lock:
                for servo_id, value in to_table.items():
                    self._table[servo_id][1] = value
                    if not self._table[servo_id][0]:
                        self._fresh.add(servo_id)
                # Räder auf 0 ist Anhalten: notfalls auch per STOPP
                self._send_or_stop(stopping=reg.name == "goal_velocity" and not any(to_table.values()))

    def set_torque(self, ids: Iterable[int], enabled: bool) -> None:
        ids = [int(i) for i in ids]
        outside = [i for i in ids if self._mode(i) is None]
        inside = [i for i in ids if i not in outside]
        if enabled and self.info and inside and self.last_zustand and self.last_zustand.read_only:
            raise NotSupportedError(f"Profil '{self.info.profile}' ist nur lesen: kein Drehmoment.")
        if enabled and outside:
            raise NotSupportedError(
                f"Drehmoment an für IDs {outside} geht über den ESP32 nicht (nicht im Profil des Knotens)."
            )
        for servo_id in outside:  # Drehmoment aus geht auch ohne Profil, die Firmware fasst sie nicht an
            self._reg_write(servo_id, regs.lookup("torque_enable"), 0)
        if not inside:
            return
        with self._lock:
            switching_on = [i for i in inside if enabled and not self._table[i][0] and i not in self._fresh]
            if switching_on:
                present = self.zustand().servos
                for servo_id in switching_on:
                    # Kein Ziel gesetzt: aktuelle Stellung halten bzw. Rad steht
                    self._table[servo_id][1] = (
                        present[servo_id].position if self._mode(servo_id) == p.Modus.POSITION else 0
                    )
            wheels_running = [
                i for i in inside if not enabled and self._mode(i) == p.Modus.TEMPO and self._table[i][0]
            ]
            if wheels_running:
                # Erst Tempo 0 bei eingeschaltetem Drehmoment: Die Firmware schreibt das Radtempo nur,
                # solange das Drehmoment an ist. Sonst bliebe das alte Tempo im Register stehen und
                # das Rad führe beim nächsten Einschalten (z. B. mit einem anderen Werkzeug) sofort los.
                for servo_id in wheels_running:
                    self._table[servo_id][1] = 0
                self._send_or_stop(stopping=True)
                time.sleep(WHEEL_ZERO_WAIT_S)
            for servo_id in inside:
                self._table[servo_id][0] = enabled
                if not enabled:
                    self._fresh.discard(servo_id)
            self._send_or_stop(stopping=not enabled)

    def _send_or_stop(self, stopping: bool) -> None:
        """Tabelle schicken. Belegt und es geht ums Anhalten (Drehmoment aus, Rad 0): STOPP."""
        try:
            self._send_table()
        except Esp32BusyError as exc:
            if not stopping:
                raise
            self.stop()
            raise Esp32BusyError(
                f"{exc} Deshalb STOPP gesendet: Räder stehen, Arm hält, Drehmoment bleibt an."
            ) from exc


def find_nodes(
    addresses: Iterable[str] = (), wait_s: float = 1.5, broadcast: bool = True
) -> list[tuple[p.Info, str]]:
    """Knoten suchen: PING als Broadcast (Port 4210) und an `addresses`, dazu Baken auf Port 4211.

    Liefert (INFO, Absender) je Knoten. Baken kommen einmal je Sekunde; ist Port 4211 schon
    belegt (anderes Werkzeug), zählen nur die PING-Antworten.
    """
    found: dict[str, tuple[p.Info, str]] = {}
    sockets = []
    ping_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    ping_sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    ping_sock.bind(("", 0))
    sockets.append(ping_sock)
    try:
        beacon = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        beacon.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        beacon.bind(("", p.BAKEN_PORT))
        sockets.append(beacon)
    except OSError:
        logger.info("Port %d belegt, Baken werden nicht abgehört.", p.BAKEN_PORT)
    packet = p.build(p.Typ.PING, 1)
    targets = [("255.255.255.255", p.UDP_PORT)] if broadcast else []
    for address in addresses:
        host, _, port = address.rpartition(":")
        host, port = (host, int(port)) if host and port.isdigit() else (address, p.UDP_PORT)
        try:
            targets.append((socket.gethostbyname(host), port))
        except OSError as exc:
            logger.warning("%s nicht auflösbar: %s", address, exc)
    for target in targets:
        try:
            ping_sock.sendto(packet, target)
        except OSError as exc:
            logger.info("PING an %s fehlgeschlagen: %s", target, exc)
    deadline = time.monotonic() + wait_s
    try:
        import selectors

        with selectors.DefaultSelector() as sel:
            for s in sockets:
                sel.register(s, selectors.EVENT_READ)
            while (left := deadline - time.monotonic()) > 0:
                for key, _ in sel.select(left):
                    raw, (ip, port) = key.fileobj.recvfrom(512)
                    parsed = p.parse(raw)
                    if parsed and parsed[0] == p.Typ.INFO:
                        try:
                            found[f"{ip}:{port}"] = (p.parse_info(parsed[2]), f"{ip}:{port}")
                        except p.ProtocolError:
                            continue
    finally:
        for s in sockets:
            s.close()
    # Bake und PING-Antwort kommen vom selben Knoten, aber von verschiedenen Ports: nach MAC zusammenfassen
    by_mac: dict[str, tuple[p.Info, str]] = {}
    for info, sender in found.values():
        if info.mac not in by_mac or sender.endswith(f":{p.UDP_PORT}"):
            by_mac[info.mac] = (info, sender)
    return sorted(by_mac.values(), key=lambda x: x[0].name)
