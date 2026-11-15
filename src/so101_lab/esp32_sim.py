"""Nachgebauter ESP32-Knoten am PC: dieselben Pakete und Regeln wie die Firmware.

Zum Üben ohne Hardware und für die Tests des `Esp32Backend`. Nachgebildet nach
`firmware/esp32/src/knoten/steuerung.cpp` (50-Hz-Schleife) und `netz.cpp` (UDP), darunter
simulierte Servos (`MockBackend`):

- Profile `lekiwi`, `so101`, `so101_leader` wie `profile.cpp`,
- Sollwerttabelle, ein `BEFEHL` gilt nur von der steuernden Quelle (IP und Port),
  eine andere bekommt BELEGT, bis die erste länger als der Watchdog schweigt,
- Watchdog: ohne `BEFEHL` stehen die Räder, die Positions-Servos halten die Stellung,
  das Drehmoment bleibt wie es ist; `STOPP` darf jeder und gibt das Steuerrecht frei,
- je Zyklus höchstens ein Registerzugriff, Warteschlange mit zwei Plätzen.

Nicht nachgebildet: WLAN, ESP-NOW, Gateway, Webseite, Baken.

    uv run so101-lab esp32 sim --profil lekiwi      # Knoten auf 127.0.0.1:4210
"""

from __future__ import annotations

import collections
import logging
import socket
import threading
import time
from dataclasses import dataclass, field

from . import registers as regs
from .backends import esp32_protocol as p
from .backends.base import BackendError
from .backends.mock import MockBackend

logger = logging.getLogger(__name__)

CYCLE_S = 0.02  # 50 Hz
FIRMWARE = 1

PROFILES: dict[str, tuple[tuple[tuple[int, p.Modus], ...], bool]] = {
    "lekiwi": (
        tuple((i, p.Modus.POSITION) for i in range(1, 7)) + tuple((i, p.Modus.TEMPO) for i in (7, 8, 9)),
        False,
    ),
    "so101": (tuple((i, p.Modus.POSITION) for i in range(1, 7)), False),
    "so101_leader": (tuple((i, p.Modus.POSITION) for i in range(1, 7)), True),
}


@dataclass
class _Servo:
    id: int
    modus: p.Modus
    online: bool = False
    failures: int = 0
    torque_is: bool = False
    hold_target: int = 2048
    state: p.ServoZustand | None = None


@dataclass
class _RegJob:
    sender: tuple[str, int]
    seq: int
    write: bool
    servo_id: int
    address: int
    length: int
    data: bytes = b""


@dataclass
class SimNode:
    """Ein Knoten auf `host:port`. `start()` startet Netz- und Servo-Thread."""

    profile: str = "lekiwi"
    host: str = "127.0.0.1"
    port: int = p.UDP_PORT  # 0 = freien Port wählen (Tests)
    watchdog_ms: int = 300
    max_velocity: int = 3000
    name: str = "sim-knoten"
    bus: MockBackend | None = None
    servos: list[_Servo] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.profile not in PROFILES:
            raise ValueError(f"Profil '{self.profile}' unbekannt, erlaubt: {', '.join(PROFILES)}.")
        entries, self.read_only = PROFILES[self.profile]
        if self.bus is None:
            wheels = [i for i, m in entries if m == p.Modus.TEMPO]
            self.bus = MockBackend.for_ids([i for i, _ in entries], wheel_ids=wheels)
        self.bus.connect()
        self.servos = [_Servo(i, m) for i, m in entries]
        self._soll: dict[int, tuple[bool, int]] = {s.id: (False, 0) for s in self.servos}
        self._command_valid = False
        self._command_at = 0.0
        self._active: tuple[str, int] | None = None
        self._active_at = 0.0
        self._last_seq = 0
        self._ack = 0
        self._jobs: collections.deque[_RegJob] = collections.deque()
        self._results: collections.deque[tuple[_RegJob, bool, bytes]] = collections.deque()
        self._lock = threading.Lock()
        self._was_safe = True
        self._check_index = 0
        self._last_search = 0.0
        self._cycle_us = 0
        self._image: p.Zustand | None = None
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []
        self._sock: socket.socket | None = None
        self.started_at = time.monotonic()
        self.log: list[str] = []  # Ereignisse wie die serielle Ausgabe der Firmware

    # ------------------------------------------------------------ Start/Stopp

    def start(self) -> SimNode:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.bind((self.host, self.port))
        sock.settimeout(0.005)
        self._sock = sock
        self.port = sock.getsockname()[1]
        for servo in self.servos:
            if self._ping(servo.id):
                self._setup(servo)
        self._cycle()
        for target, name in ((self._net_loop, "sim-netz"), (self._servo_loop, "sim-servos")):
            thread = threading.Thread(target=target, name=name, daemon=True)
            thread.start()
            self._threads.append(thread)
        logger.info("ESP32-Simulation: Profil %s auf %s:%d", self.profile, self.host, self.port)
        return self

    def stop(self) -> None:
        self._stop.set()
        for thread in self._threads:
            thread.join(timeout=1)
        if self._sock is not None:
            self._sock.close()
            self._sock = None

    def __enter__(self) -> SimNode:
        return self.start()

    def __exit__(self, *exc) -> None:
        self.stop()

    # ------------------------------------------------------------ Servo-Bus (wie servobus.cpp)

    def _ping(self, servo_id: int) -> bool:
        return self.bus.ping(servo_id) is not None

    def _read_bytes(self, servo_id: int, address: int, length: int) -> bytes | None:
        try:
            values = self.bus._responding(servo_id).values
        except BackendError:
            return None
        memory = bytearray(256)
        for reg in regs.registers():
            raw = (
                reg.encode(values.get(reg.name, 0))
                if reg.min_value <= values.get(reg.name, 0) <= reg.max_value
                else 0
            )
            memory[reg.address : reg.address + reg.size] = raw.to_bytes(reg.size, "little")
        return bytes(memory[address : address + length])

    def _write_bytes(self, servo_id: int, address: int, data: bytes) -> bool:
        try:
            servo = self.bus._responding(servo_id)
        except BackendError:
            return False
        end = address + len(data)
        try:
            for reg in regs.registers():
                if address <= reg.address and reg.address + reg.size <= end:
                    raw = int.from_bytes(
                        data[reg.address - address : reg.address - address + reg.size], "little"
                    )
                    servo.write(reg.name, reg.decode(raw))
        except (BackendError, ValueError):
            return False
        return True

    def _write1(self, servo_id: int, name: str, value: int) -> bool:
        reg = regs.lookup(name)
        return self._write_bytes(servo_id, reg.address, reg.encode(value).to_bytes(reg.size, "little"))

    # ------------------------------------------------------------ Servo-Schleife (steuerung.cpp)

    def _setup(self, s: _Servo) -> None:
        """Servo (wieder) gefunden: Drehmoment aus, Betriebsart prüfen."""
        self._write1(s.id, "torque_enable", 0)
        mode = self._read_bytes(s.id, regs.lookup("operating_mode").address, 1)
        if mode is not None and mode[0] != s.modus:
            self._write1(s.id, "lock", 0)
            self._write1(s.id, "operating_mode", int(s.modus))
            self._write1(s.id, "lock", 1)
            self.log.append(f"ID {s.id}: Betriebsart {mode[0]} -> {int(s.modus)}")
        pos = self._read_bytes(s.id, 56, 2)
        if pos is not None:
            s.hold_target = regs.lookup("present_position").decode(int.from_bytes(pos, "little"))
        s.torque_is = False
        s.failures = 0
        s.online = True

    def _read_state(self, s: _Servo) -> bool:
        d = self._read_bytes(s.id, 56, 10)
        if d is None:
            return False
        dec = regs.lookup("present_position").decode
        load = regs.lookup("present_load").decode
        s.state = p.ServoZustand(
            id=s.id,
            flags=0,
            position=dec(int.from_bytes(d[0:2], "little")),
            velocity=dec(int.from_bytes(d[2:4], "little")),
            load=load(int.from_bytes(d[4:6], "little")),
            voltage_raw=d[6],
            temperature=d[7],
            status=d[9],
        )
        return True

    def _cycle(self) -> None:
        start = time.perf_counter()
        now = time.monotonic()
        with self._lock:
            soll = dict(self._soll)
            valid = self._command_valid
            command_at = self._command_at
        safe = not valid or (now - command_at) * 1000 > self.watchdog_ms
        if safe and not self._was_safe:
            for s in self.servos:
                if s.state is not None:
                    s.hold_target = s.state.position
            self.log.append("Watchdog: Räder stopp, Arm hält" if valid else "STOPP: Räder stopp, Arm hält")
        self._was_safe = safe

        # Drehmoment eines Servos reihum gegenprüfen
        if self.servos:
            s = self.servos[self._check_index]
            self._check_index = (self._check_index + 1) % len(self.servos)
            if s.online and (d := self._read_bytes(s.id, 40, 1)) is not None:
                s.torque_is = d[0] != 0

        # Ziele schreiben (Ziel vor Drehmoment), dann Drehmoment
        torque_soll: dict[int, bool] = {}
        positions: dict[int, int] = {}
        velocities: dict[int, int] = {}
        for s in self.servos:
            torque = not self.read_only and (s.torque_is if safe else soll[s.id][0])
            torque_soll[s.id] = torque
            if not s.online or not torque:
                continue
            if s.modus == p.Modus.TEMPO:
                velocities[s.id] = (
                    0 if safe else max(-self.max_velocity, min(self.max_velocity, soll[s.id][1]))
                )
            else:
                positions[s.id] = max(0, min(4095, s.hold_target if safe else soll[s.id][1]))
        if positions:
            self.bus._sync_write("goal_position", positions)
        if velocities:
            self.bus._sync_write("goal_velocity", velocities)
        for s in self.servos:
            if (
                s.online
                and torque_soll[s.id] != s.torque_is
                and self._write1(s.id, "torque_enable", int(torque_soll[s.id]))
            ):
                s.torque_is = torque_soll[s.id]

        # Istwerte lesen
        for s in self.servos:
            if not s.online:
                continue
            if self._read_state(s):
                s.failures = 0
            else:
                s.failures += 1
                if s.failures >= 3:
                    s.online = False
                    s.torque_is = False

        # Fehlende Servos suchen
        if now - self._last_search >= 2.0:
            self._last_search = now
            for s in self.servos:
                if not s.online and self._ping(s.id):
                    self._setup(s)

        # Höchstens ein Registerzugriff je Zyklus
        with self._lock:
            job = self._jobs.popleft() if self._jobs else None
        if job is not None:
            if job.write:
                ok, data = self._write_bytes(job.servo_id, job.address, job.data), b""
            else:
                read = self._read_bytes(job.servo_id, job.address, job.length)
                ok, data = read is not None, read or b""
            with self._lock:
                self._results.append((job, ok, data))

        # Abbild
        servos = {}
        for s in self.servos:
            st = s.state or p.ServoZustand(s.id, 0, 0, 0, 0, 0, 0, 0)
            flags = (
                (p.SERVO_ONLINE if s.online else 0)
                | (p.SERVO_DREHMOMENT if s.torque_is else 0)
                | (p.SERVO_TEMPOMODUS if s.modus == p.Modus.TEMPO else 0)
                | (p.SERVO_VERALTET if s.failures else 0)
            )
            servos[s.id] = p.ServoZustand(
                s.id, flags, st.position, st.velocity, st.load, st.voltage_raw, st.temperature, st.status
            )
        self._cycle_us = min(65535, int((time.perf_counter() - start) * 1e6))
        image = p.Zustand(
            0,
            int((now - self.started_at) * 1000),
            p.KNOTEN_WATCHDOG if safe else 0,
            0,
            self._cycle_us,
            0,
            servos,
        )
        with self._lock:
            self._image = image

    def _servo_loop(self) -> None:
        next_tick = time.monotonic()
        while not self._stop.is_set():
            try:
                self._cycle()
            except Exception:  # pragma: no cover - Simulation soll weiterlaufen
                logger.exception("ESP32-Simulation: Fehler im Zyklus")
            next_tick += CYCLE_S
            time.sleep(max(0.0, next_tick - time.monotonic()))

    # ------------------------------------------------------------ Netz (netz.cpp)

    def _source(self) -> int:
        if self._active is None or (time.monotonic() - self._active_at) * 1000 > self.watchdog_ms:
            return 0
        return 1  # UDP

    def _send(self, to: tuple[str, int], typ: int, seq: int, payload: bytes) -> None:
        if self._sock is not None:
            self._sock.sendto(p.build(typ, seq, payload), to)

    def _send_zustand(self, to: tuple[str, int], seq: int, extra: int = 0) -> None:
        with self._lock:
            image = self._image
            ack = self._ack
            source = self._source()
        flags = image.flags | extra | (p.KNOTEN_NUR_LESEN if self.read_only else 0)
        z = p.Zustand(ack, image.time_ms, flags, source, image.cycle_us, image.bus_errors, image.servos)
        self._send(to, p.Typ.ZUSTAND, seq, p.build_zustand(z))

    def info(self) -> p.Info:
        host = self.host if self.host not in ("", "0.0.0.0") else "127.0.0.1"
        return p.Info(
            name=self.name,
            profile=self.profile,
            mode="WLAN",
            channel=1,
            rssi=-50,
            ip=host,
            mac="02:00:00:00:00:01",
            firmware=FIRMWARE,
            watchdog_ms=self.watchdog_ms,
            servos=tuple((s.id, s.modus) for s in self.servos),
        )

    def _handle(self, raw: bytes, sender: tuple[str, int]) -> None:
        packet = p.parse(raw)
        if packet is None:
            return  # ungültig: stillschweigend verwerfen
        typ, seq, payload = packet
        if typ == p.Typ.PING:
            self._send(sender, p.Typ.INFO, seq, p.build_info(self.info()))
        elif typ == p.Typ.BEFEHL:
            self._command(seq, payload, sender)
        elif typ == p.Typ.ZUSTAND_ANFRAGE:
            self._send_zustand(sender, seq)
        elif typ == p.Typ.STOPP:
            with self._lock:
                self._command_valid = False
                self._active = None
            self._send_zustand(sender, seq)
        elif typ in (p.Typ.REG_LESEN, p.Typ.REG_SCHREIBEN):
            self._register(typ, seq, payload, sender)

    def _command(self, seq: int, payload: bytes, sender: tuple[str, int]) -> None:
        entries = p.parse_befehl(payload)
        if entries is None:
            return
        now = time.monotonic()
        with self._lock:
            same = self._active == sender
            free = self._active is None or (now - self._active_at) * 1000 > self.watchdog_ms
        if not same and not free:
            self._send_zustand(sender, seq, p.KNOTEN_BELEGT)
            return
        if same and not free and not p.newer(seq, self._last_seq):
            self._send_zustand(sender, seq)
            return
        with self._lock:
            if not same:
                self.log.append(f"Steuerung jetzt über UDP {sender[0]}:{sender[1]}")
            self._active = sender
            self._active_at = now
            self._last_seq = self._ack = seq
            for sid, value in entries.items():
                if sid in self._soll:  # IDs außerhalb des Profils werden ignoriert
                    self._soll[sid] = value
            self._command_valid = True
            self._command_at = now
        self._send_zustand(sender, seq)

    def _register(self, typ: int, seq: int, payload: bytes, sender: tuple[str, int]) -> None:
        if len(payload) < p.REG_ANFRAGE.size:
            return
        servo_id, address, length = p.REG_ANFRAGE.unpack_from(payload)
        write = typ == p.Typ.REG_SCHREIBEN
        if not 1 <= length <= p.MAX_REG_DATEN or len(payload) != p.REG_ANFRAGE.size + (
            length if write else 0
        ):
            return
        job = _RegJob(
            sender, seq, write, servo_id, address, length, payload[p.REG_ANFRAGE.size :] if write else b""
        )
        with self._lock:
            full = len(self._jobs) >= 2
            if not full:
                self._jobs.append(job)
        if full:
            self._send(sender, p.Typ.REG_ANTWORT, seq, p.REG_ANTWORT_KOPF.pack(servo_id, address, 0, 0))

    def _net_loop(self) -> None:
        while not self._stop.is_set():
            try:
                raw, sender = self._sock.recvfrom(512)
                self._handle(raw, sender)
            except TimeoutError:
                pass
            except OSError:
                if self._stop.is_set():
                    return
            with self._lock:
                results = list(self._results)
                self._results.clear()
            for job, ok, data in results:
                data = data if ok and not job.write else b""
                payload = p.REG_ANTWORT_KOPF.pack(job.servo_id, job.address, int(ok), len(data)) + data
                self._send(job.sender, p.Typ.REG_ANTWORT, job.seq, payload)
