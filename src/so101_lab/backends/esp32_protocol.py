"""Funkprotokoll der ESP32-Knoten (firmware/esp32/lib/protokoll/protokoll.h), nur Pakete.

Keine Sockets, keine Threads: bauen und zerlegen. Benutzt von `Esp32Backend` (PC-Seite)
und `esp32_sim` (nachgebauter Knoten für Tests und zum Üben). Die Referenz auf der
PC-Seite ist `firmware/esp32/werkzeuge/knoten_test.py`; ein Test vergleicht die Bytes.

Aufbau, alle Zahlen Little Endian, ohne Füllbytes:

    Kopf (7 Byte) | Nutzdaten (0…241 Byte) | CRC-16/CCITT-FALSE (2 Byte)
    magic u16 = 0x4B52 | version u8 = 1 | typ u8 | seq u16 | laenge u8
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from enum import IntEnum

MAGIC = 0x4B52
VERSION = 1
MAX_PAKET = 250
UDP_PORT = 4210  # Befehle und Antworten
BAKEN_PORT = 4211  # INFO als Broadcast, einmal je Sekunde
MAX_SERVOS = 12
MAX_REG_DATEN = 32

KOPF = struct.Struct("<HBBHB")
SOLL = struct.Struct("<BBh")
INFO_KOPF = struct.Struct("<16s16sBBb4s6sHHB")
ZUSTAND_KOPF = struct.Struct("<HIBBHHB")
SERVO_ZUSTAND = struct.Struct("<BBhhhBBB")
REG_ANFRAGE = struct.Struct("<BBB")
REG_ANTWORT_KOPF = struct.Struct("<BBBB")
MAX_NUTZDATEN = MAX_PAKET - KOPF.size - 2


class Typ(IntEnum):
    PING = 0x01
    BEFEHL = 0x02
    ZUSTAND_ANFRAGE = 0x03
    STOPP = 0x04
    REG_LESEN = 0x05
    REG_SCHREIBEN = 0x06
    INFO = 0x81
    ZUSTAND = 0x82
    REG_ANTWORT = 0x85


class Modus(IntEnum):  # = Register 33 des STS3215
    POSITION = 0
    TEMPO = 1


SOLL_DREHMOMENT = 0x01
KNOTEN_WATCHDOG = 0x01  # kein gültiger Befehl, Knoten hält den sicheren Zustand
KNOTEN_BELEGT = 0x02  # dieser Befehl wurde abgelehnt, jemand anderes steuert
KNOTEN_NUR_LESEN = 0x04  # Profil ohne Drehmoment (Leader)
KNOTEN_WLAN = 0x08
SERVO_ONLINE = 0x01
SERVO_DREHMOMENT = 0x02
SERVO_TEMPOMODUS = 0x04
SERVO_VERALTET = 0x08  # letzter Lesezugriff fehlgeschlagen

BETRIEB = {0: "WLAN", 1: "ESP-NOW", 2: "Notbetrieb", 3: "Einrichtung"}
QUELLE = {0: "niemand", 1: "UDP", 2: "ESP-NOW"}


class ProtocolError(ValueError):
    """Paket kaputt oder unerwartet."""


def crc16(data: bytes) -> int:
    """CRC-16/CCITT-FALSE: Startwert 0xFFFF, Polynom 0x1021 (wie protokoll::crc16)."""
    crc = 0xFFFF
    for b in data:
        crc ^= b << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return crc


def build(typ: int, seq: int, payload: bytes = b"") -> bytes:
    if len(payload) > MAX_NUTZDATEN:
        raise ProtocolError(f"Nutzdaten zu lang ({len(payload)} > {MAX_NUTZDATEN}).")
    raw = KOPF.pack(MAGIC, VERSION, int(typ), seq & 0xFFFF, len(payload)) + payload
    return raw + struct.pack("<H", crc16(raw))


def parse(raw: bytes) -> tuple[int, int, bytes] | None:
    """→ (typ, seq, nutzdaten). `None` bei kaputtem Paket: Der Knoten verwirft solche Pakete
    stillschweigend, die PC-Seite tut dasselbe."""
    if not KOPF.size + 2 <= len(raw) <= MAX_PAKET:
        return None
    magic, version, typ, seq, length = KOPF.unpack_from(raw)
    if magic != MAGIC or version != VERSION or len(raw) != KOPF.size + length + 2:
        return None
    if struct.unpack_from("<H", raw, KOPF.size + length)[0] != crc16(raw[: KOPF.size + length]):
        return None
    return typ, seq, raw[KOPF.size : KOPF.size + length]


def newer(a: int, b: int) -> bool:
    """Ist seq a neuer als b? Mit Überlauf 65535 → 0 (wie protokoll::neuer)."""
    diff = (a - b) & 0xFFFF
    return diff != 0 and diff < 0x8000


# ------------------------------------------------------------------ Nutzdaten


@dataclass(frozen=True)
class Info:
    name: str
    profile: str
    mode: str  # Betrieb: WLAN, ESP-NOW, Notbetrieb, Einrichtung
    channel: int
    rssi: int
    ip: str
    mac: str
    firmware: int
    watchdog_ms: int
    servos: tuple[tuple[int, Modus], ...]  # (ID, Betriebsart) laut Profil

    @property
    def ids(self) -> list[int]:
        return [i for i, _ in self.servos]

    def modus(self, servo_id: int) -> Modus | None:
        return next((m for i, m in self.servos if i == servo_id), None)


@dataclass(frozen=True)
class ServoZustand:
    id: int
    flags: int
    position: int
    velocity: int
    load: int
    voltage_raw: int  # 0,1 V
    temperature: int
    status: int  # Register 65

    @property
    def online(self) -> bool:
        return bool(self.flags & SERVO_ONLINE)

    @property
    def torque(self) -> bool:
        return bool(self.flags & SERVO_DREHMOMENT)

    @property
    def velocity_mode(self) -> bool:
        return bool(self.flags & SERVO_TEMPOMODUS)


@dataclass(frozen=True)
class Zustand:
    ack: int  # seq des zuletzt angenommenen BEFEHL
    time_ms: int
    flags: int
    source: int  # 0 niemand, 1 UDP, 2 ESP-NOW
    cycle_us: int
    bus_errors: int
    servos: dict[int, ServoZustand] = field(default_factory=dict)

    @property
    def busy(self) -> bool:
        return bool(self.flags & KNOTEN_BELEGT)

    @property
    def safe_state(self) -> bool:
        return bool(self.flags & KNOTEN_WATCHDOG)

    @property
    def read_only(self) -> bool:
        return bool(self.flags & KNOTEN_NUR_LESEN)


def _text(raw: bytes) -> str:
    return raw.split(b"\0")[0].decode("utf-8", "replace")


def parse_info(payload: bytes) -> Info:
    try:
        name, profile, betrieb, kanal, rssi, ip, mac, fw, wd, n = INFO_KOPF.unpack_from(payload)
        servos = tuple(
            (sid, Modus(mode)) for sid, mode in struct.iter_unpack("<BB", payload[INFO_KOPF.size :][: 2 * n])
        )
    except (struct.error, ValueError) as exc:
        raise ProtocolError(f"INFO unlesbar: {exc}") from None
    if len(servos) != n:
        raise ProtocolError("INFO zu kurz.")
    return Info(
        name=_text(name),
        profile=_text(profile),
        mode=BETRIEB.get(betrieb, str(betrieb)),
        channel=kanal,
        rssi=rssi,
        ip=".".join(map(str, ip)),
        mac=":".join(f"{b:02X}" for b in mac),
        firmware=fw,
        watchdog_ms=wd,
        servos=servos,
    )


def build_info(info: Info) -> bytes:
    ip = bytes(int(x) for x in info.ip.split("."))
    mac = bytes(int(x, 16) for x in info.mac.split(":"))
    betrieb = {v: k for k, v in BETRIEB.items()}.get(info.mode, 0)
    head = INFO_KOPF.pack(
        info.name.encode()[:15],
        info.profile.encode()[:15],
        betrieb,
        info.channel,
        info.rssi,
        ip,
        mac,
        info.firmware,
        info.watchdog_ms,
        len(info.servos),
    )
    return head + b"".join(struct.pack("<BB", i, int(m)) for i, m in info.servos)


def parse_zustand(payload: bytes) -> Zustand:
    try:
        ack, time_ms, flags, source, cycle, errors, n = ZUSTAND_KOPF.unpack_from(payload)
        servos = {}
        for k in range(n):
            values = SERVO_ZUSTAND.unpack_from(payload, ZUSTAND_KOPF.size + k * SERVO_ZUSTAND.size)
            servos[values[0]] = ServoZustand(*values)
    except struct.error as exc:
        raise ProtocolError(f"ZUSTAND unlesbar: {exc}") from None
    return Zustand(ack, time_ms, flags, source, cycle, errors, servos)


def build_zustand(z: Zustand) -> bytes:
    head = ZUSTAND_KOPF.pack(
        z.ack, z.time_ms & 0xFFFFFFFF, z.flags, z.source, z.cycle_us, z.bus_errors, len(z.servos)
    )
    return head + b"".join(
        SERVO_ZUSTAND.pack(
            s.id, s.flags, s.position, s.velocity, s.load, s.voltage_raw, s.temperature, s.status
        )
        for s in z.servos.values()
    )


def build_befehl(setpoints: dict[int, tuple[bool, int]]) -> bytes:
    """{id: (drehmoment, wert)}. Positionsmodus: wert 0…4095, Tempomodus: Schritt/s mit Vorzeichen."""
    if len(setpoints) > MAX_SERVOS:
        raise ProtocolError(f"Höchstens {MAX_SERVOS} Servos je BEFEHL.")
    return bytes([len(setpoints)]) + b"".join(
        SOLL.pack(sid, SOLL_DREHMOMENT if torque else 0, value) for sid, (torque, value) in setpoints.items()
    )


def parse_befehl(payload: bytes) -> dict[int, tuple[bool, int]] | None:
    """Wie die Firmware: falsche Länge → None (Paket wird verworfen)."""
    if not payload or payload[0] > MAX_SERVOS or len(payload) != 1 + payload[0] * SOLL.size:
        return None
    return {
        sid: (bool(flags & SOLL_DREHMOMENT), value) for sid, flags, value in SOLL.iter_unpack(payload[1:])
    }


def build_reg_request(servo_id: int, address: int, length: int, data: bytes | None = None) -> bytes:
    if not 1 <= length <= MAX_REG_DATEN:
        raise ProtocolError(f"Register: 1 bis {MAX_REG_DATEN} Byte je Zugriff.")
    if data is not None and len(data) != length:
        raise ProtocolError("Register: Datenlänge passt nicht.")
    return REG_ANFRAGE.pack(servo_id, address, length) + (data or b"")


def parse_reg_answer(payload: bytes) -> tuple[int, int, bool, bytes]:
    """→ (id, adresse, ok, daten)."""
    try:
        sid, address, ok, n = REG_ANTWORT_KOPF.unpack_from(payload)
    except struct.error as exc:
        raise ProtocolError(f"REG_ANTWORT unlesbar: {exc}") from None
    return sid, address, bool(ok), payload[REG_ANTWORT_KOPF.size : REG_ANTWORT_KOPF.size + n]
