# /// script
# requires-python = ">=3.10"
# dependencies = ["pyserial"]
# ///
"""Testwerkzeug für die ESP32-Knoten.

Spricht dasselbe Protokoll wie lib/protokoll/protokoll.h, entweder über WLAN
(UDP) oder über den Gateway-ESP am USB-Port (ESP-NOW).

Aufruf aus dem Ordner esp32_funkknoten (uv installiert pyserial selbst):

  uv run werkzeuge/knoten_test.py suchen                          # alle Knoten im WLAN
  uv run werkzeuge/knoten_test.py --ip lekiwi-3fa2.local info
  uv run werkzeuge/knoten_test.py --ip 192.168.0.42 zustand --dauer 5
  uv run werkzeuge/knoten_test.py --ip 192.168.0.42 raeder 300 300 300 --dauer 2
  uv run werkzeuge/knoten_test.py --gateway COM5 suchen           # über ESP-NOW
  uv run werkzeuge/knoten_test.py --gateway /dev/ttyUSB0 raeder 0 0 500
  uv run werkzeuge/knoten_test.py --gateway COM5 gateway-info

Ohne --mac nimmt das Gateway den ersten Knoten, der auf PING antwortet.
"""

from __future__ import annotations

import argparse
import socket
import struct
import sys
import time

MAGIC = 0x4B52
VERSION = 1
UDP_PORT, BAKEN_PORT = 4210, 4211

PING, BEFEHL, ZUSTAND_ANFRAGE, STOPP, REG_LESEN, REG_SCHREIBEN = 0x01, 0x02, 0x03, 0x04, 0x05, 0x06
INFO, ZUSTAND, REG_ANTWORT = 0x81, 0x82, 0x85
GW_INFO_ANFRAGE, GW_KANAL, GW_INFO = 0x10, 0x11, 0x90

SOLL_DREHMOMENT = 0x01
KNOTEN_WATCHDOG, KNOTEN_BELEGT, KNOTEN_NUR_LESEN, KNOTEN_WLAN = 0x01, 0x02, 0x04, 0x08
SERVO_ONLINE, SERVO_DREHMOMENT, SERVO_TEMPOMODUS, SERVO_VERALTET = 0x01, 0x02, 0x04, 0x08

BETRIEB = {0: "WLAN", 1: "ESP-NOW", 2: "Notbetrieb", 3: "Einrichtung"}
QUELLE = {0: "niemand", 1: "UDP", 2: "ESP-NOW"}
MODUS = {0: "Position", 1: "Tempo"}

BROADCAST_MAC = b"\xff" * 6
GATEWAY_MAC = b"\x00" * 6

# Aufbau wie die Structs in protokoll.h (Little Endian, ohne Füllbytes)
KOPF = struct.Struct("<HBBHB")
INFO_KOPF = struct.Struct("<16s16sBBb4s6sHHB")
ZUSTAND_KOPF = struct.Struct("<HIBBHHB")
SERVO_ZUSTAND = struct.Struct("<BBhhhBBB")
GW_INFO_S = struct.Struct("<BBB6sHII")


# ─── Pakete ─────────────────────────────────────────────────────────────

def crc16(daten: bytes) -> int:
    """CRC-16/CCITT-FALSE, wie protokoll::crc16."""
    crc = 0xFFFF
    for b in daten:
        crc ^= b << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return crc


def bauen(typ: int, seq: int, nutzdaten: bytes = b"") -> bytes:
    roh = KOPF.pack(MAGIC, VERSION, typ, seq & 0xFFFF, len(nutzdaten)) + nutzdaten
    return roh + struct.pack("<H", crc16(roh))


def zerlegen(roh: bytes) -> tuple[int, int, bytes] | None:
    """→ (typ, seq, nutzdaten), None bei kaputtem Paket."""
    if len(roh) < KOPF.size + 2:
        return None
    magic, version, typ, seq, laenge = KOPF.unpack_from(roh)
    if magic != MAGIC or version != VERSION or len(roh) != KOPF.size + laenge + 2:
        return None
    if struct.unpack_from("<H", roh, KOPF.size + laenge)[0] != crc16(roh[:KOPF.size + laenge]):
        return None
    return typ, seq, roh[KOPF.size:KOPF.size + laenge]


def mac_text(mac: bytes) -> str:
    return ":".join(f"{b:02X}" for b in mac)


def mac_lesen(text: str) -> bytes:
    teile = text.replace("-", ":").split(":")
    if len(teile) != 6:
        raise argparse.ArgumentTypeError("MAC wie 24:6F:28:AA:BB:CC angeben")
    return bytes(int(t, 16) for t in teile)


def info_lesen(nd: bytes) -> dict:
    name, profil, betrieb, kanal, rssi, ip, mac, fw, wd, anzahl = INFO_KOPF.unpack_from(nd)
    servos = [struct.unpack_from("<BB", nd, INFO_KOPF.size + 2 * i) for i in range(anzahl)]
    return {
        "name": name.split(b"\0")[0].decode(), "profil": profil.split(b"\0")[0].decode(),
        "betrieb": BETRIEB.get(betrieb, betrieb), "kanal": kanal, "rssi": rssi, "ip": ".".join(map(str, ip)),
        "mac": mac_text(mac), "firmware": fw, "watchdog_ms": wd, "servos": servos,
    }


def zustand_lesen(nd: bytes) -> dict:
    quittung, zeit, flags, quelle, zyklus, busfehler, anzahl = ZUSTAND_KOPF.unpack_from(nd)
    servos = []
    for i in range(anzahl):
        sid, sf, pos, tempo, last, u, t, fehler = SERVO_ZUSTAND.unpack_from(nd, ZUSTAND_KOPF.size + i * SERVO_ZUSTAND.size)
        servos.append({"id": sid, "online": bool(sf & SERVO_ONLINE), "drehmoment": bool(sf & SERVO_DREHMOMENT),
                       "tempomodus": bool(sf & SERVO_TEMPOMODUS), "veraltet": bool(sf & SERVO_VERALTET),
                       "position": pos, "tempo": tempo, "last": last, "spannung": u / 10, "temperatur": t,
                       "fehler": fehler})
    return {"quittung": quittung, "zeit_ms": zeit, "watchdog": bool(flags & KNOTEN_WATCHDOG),
            "belegt": bool(flags & KNOTEN_BELEGT), "nur_lesen": bool(flags & KNOTEN_NUR_LESEN),
            "wlan": bool(flags & KNOTEN_WLAN), "quelle": QUELLE.get(quelle, quelle), "zyklus_us": zyklus,
            "busfehler": busfehler, "servos": servos}


# ─── Verbindungen ──────────────────────────────────────────────────────

class UdpVerbindung:
    def __init__(self, ziel: str | None):
        self.ziel = (socket.gethostbyname(ziel), UDP_PORT) if ziel else None
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        self.sock.bind(("", 0))

    def senden(self, paket: bytes, an_alle: bool = False):
        ziel = ("255.255.255.255", UDP_PORT) if an_alle else self.ziel
        if ziel is None:
            sys.exit("Ziel fehlt: --ip <Adresse> angeben oder erst 'suchen'")
        self.sock.sendto(paket, ziel)

    def empfangen(self, wartezeit: float) -> tuple[bytes, str] | None:
        self.sock.settimeout(max(wartezeit, 0.001))
        try:
            daten, (ip, port) = self.sock.recvfrom(512)
        except (socket.timeout, ConnectionResetError):
            return None
        return daten, f"{ip}:{port}"


class GatewayVerbindung:
    END, ESC, ESC_END, ESC_ESC = 0xC0, 0xDB, 0xDC, 0xDD

    def __init__(self, port: str, mac: bytes | None):
        import serial
        self.ser = serial.Serial()
        self.ser.port, self.ser.baudrate, self.ser.timeout = port, 921600, 0
        self.ser.dtr = self.ser.rts = False  # sonst startet manches DevKit beim Öffnen neu
        self.ser.open()
        time.sleep(0.3)
        self.ser.reset_input_buffer()
        self.mac = mac
        self.rahmen, self.esc = bytearray(), False

    def rahmen_senden(self, mac: bytes, paket: bytes):
        roh = (mac + paket).replace(b"\xdb", b"\xdb\xdd").replace(b"\xc0", b"\xdb\xdc")
        self.ser.write(b"\xc0" + roh + b"\xc0")

    def senden(self, paket: bytes, an_alle: bool = False):
        if not an_alle and self.mac is None:
            sys.exit("Kein Knoten bekannt: --mac angeben oder erst 'suchen'")
        self.rahmen_senden(BROADCAST_MAC if an_alle else self.mac, paket)

    def empfangen(self, wartezeit: float) -> tuple[bytes, str] | None:
        """Nächster vollständiger Rahmen → (paket, MAC des Absenders)."""
        ende = time.monotonic() + wartezeit
        while True:
            for b in self.ser.read(self.ser.in_waiting or 1):
                if b == self.END:
                    r, self.rahmen = bytes(self.rahmen), bytearray()
                    if len(r) > 6:
                        return r[6:], mac_text(r[:6])
                elif b == self.ESC:
                    self.esc = True
                else:
                    if self.esc:
                        b = {self.ESC_END: self.END, self.ESC_ESC: self.ESC}.get(b, b)
                        self.esc = False
                    self.rahmen.append(b)
            if time.monotonic() >= ende:
                return None
            time.sleep(0.001)


class Knoten:
    def __init__(self, verbindung):
        self.v = verbindung
        self.seq = 0

    def _naechste_seq(self) -> int:
        self.seq = (self.seq + 1) & 0xFFFF
        return self.seq

    def anfrage(self, typ: int, nutzdaten: bytes = b"", antwort: int | None = None,
                wartezeit: float = 0.5) -> tuple[int, bytes, str] | None:
        seq = self._naechste_seq()
        self.v.senden(bauen(typ, seq, nutzdaten))
        ende = time.monotonic() + wartezeit
        while (rest := ende - time.monotonic()) > 0:
            r = self.v.empfangen(rest)
            if r is None:
                break
            p = zerlegen(r[0])
            if p and p[1] == seq and (antwort is None or p[0] == antwort):
                return p[0], p[2], r[1]
        return None

    def suchen(self, wartezeit: float = 1.0) -> list[tuple[dict, str]]:
        """PING an alle, sammelt INFO-Antworten."""
        seq = self._naechste_seq()
        self.v.senden(bauen(PING, seq), an_alle=True)
        gefunden, ende = {}, time.monotonic() + wartezeit
        while (rest := ende - time.monotonic()) > 0:
            r = self.v.empfangen(rest)
            if r is None:
                break
            p = zerlegen(r[0])
            if p and p[0] == INFO:
                gefunden[r[1]] = info_lesen(p[2])
        return [(info, von) for von, info in gefunden.items()]

    def info(self) -> dict | None:
        r = self.anfrage(PING, antwort=INFO)
        return info_lesen(r[1]) if r else None

    def zustand(self) -> dict | None:
        r = self.anfrage(ZUSTAND_ANFRAGE, antwort=ZUSTAND)
        return zustand_lesen(r[1]) if r else None

    def befehl(self, sollwerte: dict[int, tuple[bool, int]]) -> dict | None:
        """sollwerte: {id: (drehmoment, wert)}"""
        nd = bytes([len(sollwerte)]) + b"".join(
            struct.pack("<BBh", sid, SOLL_DREHMOMENT if dm else 0, wert) for sid, (dm, wert) in sollwerte.items())
        r = self.anfrage(BEFEHL, nd, antwort=ZUSTAND, wartezeit=0.1)
        return zustand_lesen(r[1]) if r else None

    def stopp(self) -> dict | None:
        r = self.anfrage(STOPP, antwort=ZUSTAND)
        return zustand_lesen(r[1]) if r else None

    def register(self, sid: int, adresse: int, laenge: int, daten: bytes | None = None) -> bytes | None:
        typ = REG_LESEN if daten is None else REG_SCHREIBEN
        nd = bytes([sid, adresse, laenge]) + (daten or b"")
        r = self.anfrage(typ, nd, antwort=REG_ANTWORT)
        if not r:
            return None
        _, _, ok, n = struct.unpack_from("<BBBB", r[1])
        return r[1][4:4 + n] if ok else None


# ─── Ausgabe ───────────────────────────────────────────────────────────

def info_drucken(i: dict, von: str = ""):
    servos = ", ".join(f"{sid} {MODUS.get(m, m)}" for sid, m in i["servos"])
    print(f"{i['name']:<16} {i['profil']:<13} {i['betrieb']:<12} Kanal {i['kanal']:>2}  "
          f"IP {i['ip']:<15} MAC {i['mac']}  RSSI {i['rssi']} dBm  Watchdog {i['watchdog_ms']} ms"
          + (f"  [{von}]" if von else ""))
    print(f"  Servos: {servos}")


def zustand_drucken(z: dict):
    kopf = (f"Steuert: {z['quelle']}  {'SICHERER ZUSTAND' if z['watchdog'] else 'aktiv'}"
            f"{'  BELEGT' if z['belegt'] else ''}  Zyklus {z['zyklus_us']} µs  Busfehler {z['busfehler']}")
    print(kopf)
    print("  ID  Pos.   Tempo   Last    U/V   T/°C  DM  Fehler")
    for s in z["servos"]:
        if not s["online"]:
            print(f"  {s['id']:>2}  fehlt")
            continue
        print(f"  {s['id']:>2}  {s['position']:>5} {s['tempo']:>6} {s['last']:>6}  {s['spannung']:>5.1f}  "
              f"{s['temperatur']:>4}   {'x' if s['drehmoment'] else '-'}   {s['fehler'] or 'ok'}")


# ─── Befehle ───────────────────────────────────────────────────────────

def halten(knoten: Knoten, sollwerte: dict[int, tuple[bool, int]], dauer: float):
    """Befehl mit 20 Hz wiederholen (Watchdog), danach STOPP."""
    ende, letzte_ausgabe = time.monotonic() + dauer, 0.0
    try:
        while time.monotonic() < ende:
            z = knoten.befehl(sollwerte)
            if z and time.monotonic() - letzte_ausgabe > 0.5:
                letzte_ausgabe = time.monotonic()
                if z["belegt"]:
                    print("Knoten BELEGT: eine andere Quelle steuert gerade")
                werte = "  ".join(f"{s['id']}:{s['tempo'] if s['tempomodus'] else s['position']}"
                                  for s in z["servos"] if s["id"] in sollwerte)
                print(f"  {werte}")
            time.sleep(0.05)
    finally:
        knoten.stopp()
        print("STOPP gesendet")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    weg = ap.add_mutually_exclusive_group()
    weg.add_argument("--ip", help="Knoten im WLAN (IP oder name.local)")
    weg.add_argument("--gateway", help="serieller Port des Gateway-ESP (COM5, /dev/ttyUSB0)")
    ap.add_argument("--mac", type=mac_lesen, help="Knoten hinter dem Gateway, sonst der erste, der antwortet")
    unter = ap.add_subparsers(dest="befehl", required=True)
    unter.add_parser("suchen", help="alle Knoten finden (PING an alle)")
    unter.add_parser("info", help="INFO eines Knotens")
    p = unter.add_parser("zustand", help="Istwerte lesen, ohne zu steuern")
    p.add_argument("--dauer", type=float, default=0, help="so lange jede Sekunde neu lesen")
    p = unter.add_parser("raeder", help="LeKiwi-Räder 7, 8, 9 im Tempomodus drehen (Schritt/s)")
    p.add_argument("tempo", type=int, nargs=3)
    p.add_argument("--dauer", type=float, default=2.0)
    p = unter.add_parser("servo", help="einen Servo stellen (Position 0..4095 oder Tempo in Schritt/s)")
    p.add_argument("id", type=int)
    p.add_argument("wert", type=int)
    p.add_argument("--dauer", type=float, default=2.0)
    unter.add_parser("drehmoment-aus", help="alle Servos des Profils ohne Drehmoment")
    unter.add_parser("stopp", help="STOPP senden")
    p = unter.add_parser("reg-lesen", help="Servo-Register lesen")
    p.add_argument("id", type=int)
    p.add_argument("adresse", type=int)
    p.add_argument("laenge", type=int)
    p = unter.add_parser("reg-schreiben", help="Servo-Register schreiben (Bytes dezimal)")
    p.add_argument("id", type=int)
    p.add_argument("adresse", type=int)
    p.add_argument("bytes", type=int, nargs="+")
    unter.add_parser("gateway-info", help="Kanal und Zähler des Gateways")
    p = unter.add_parser("gateway-kanal", help="Kanal des Gateways fest setzen (0 = automatisch suchen)")
    p.add_argument("kanal", type=int, choices=range(0, 14))
    a = ap.parse_args()

    if a.gateway:
        v = GatewayVerbindung(a.gateway, a.mac)
    else:
        v = UdpVerbindung(a.ip)
    knoten = Knoten(v)

    if a.befehl in ("gateway-info", "gateway-kanal"):
        if not a.gateway:
            sys.exit("nur mit --gateway")
        nd = bytes([a.kanal]) if a.befehl == "gateway-kanal" else b""
        v.rahmen_senden(GATEWAY_MAC, bauen(GW_KANAL if nd else GW_INFO_ANFRAGE, 1, nd))
        ende = time.monotonic() + 1
        while (r := v.empfangen(max(ende - time.monotonic(), 0))) is not None:
            p = zerlegen(r[0])
            if p and p[0] == GW_INFO:
                kanal, auto, such, mac, fw, rx, fehler = GW_INFO_S.unpack(p[2])
                print(f"Gateway {mac_text(mac)}, Firmware {fw}: Kanal {kanal} "
                      f"({'automatisch' if auto else 'fest'}{', sucht' if such else ''}), "
                      f"{rx} Pakete empfangen, {fehler} Sendefehler")
                return
        sys.exit("Gateway antwortet nicht (Port, Baudrate 921600, richtige Firmware?)")

    if a.befehl == "suchen":
        gefunden = knoten.suchen(2.5 if a.gateway else 1.0)  # das Gateway muss evtl. erst den Kanal finden
        if not gefunden and not a.gateway:
            # Ohne Antwort auf PING: auf Baken lauschen (kommen jede Sekunde als Broadcast)
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind(("", BAKEN_PORT))
            s.settimeout(1.5)
            try:
                daten, (ip, _) = s.recvfrom(512)
                p = zerlegen(daten)
                if p and p[0] == INFO:
                    gefunden = [(info_lesen(p[2]), ip)]
            except socket.timeout:
                pass
        if not gefunden:
            sys.exit("Kein Knoten gefunden. Gleiches WLAN? Firewall für UDP 4210/4211 offen?")
        for info, von in gefunden:
            info_drucken(info, von)
        return

    if a.gateway and a.mac is None:
        gefunden = knoten.suchen(2.5)
        if not gefunden:
            sys.exit("Kein Knoten hinter dem Gateway gefunden")
        v.mac = mac_lesen(gefunden[0][1])
        print(f"Knoten {gefunden[0][0]['name']} ({gefunden[0][1]})")

    if a.befehl == "info":
        i = knoten.info()
        info_drucken(i) if i else sys.exit("keine Antwort")
    elif a.befehl == "zustand":
        ende = time.monotonic() + a.dauer
        while True:
            z = knoten.zustand()
            zustand_drucken(z) if z else print("keine Antwort")
            if time.monotonic() >= ende:
                break
            time.sleep(1)
    elif a.befehl == "raeder":
        halten(knoten, {7: (True, a.tempo[0]), 8: (True, a.tempo[1]), 9: (True, a.tempo[2])}, a.dauer)
    elif a.befehl == "servo":
        halten(knoten, {a.id: (True, a.wert)}, a.dauer)
    elif a.befehl == "drehmoment-aus":
        i = knoten.info()
        if not i:
            sys.exit("keine Antwort")
        z = knoten.befehl({sid: (False, 0) for sid, _ in i["servos"]})
        print("Drehmoment aus" if z and not z["belegt"] else "nicht angenommen")
    elif a.befehl == "stopp":
        z = knoten.stopp()
        zustand_drucken(z) if z else print("keine Antwort")
    elif a.befehl == "reg-lesen":
        d = knoten.register(a.id, a.adresse, a.laenge)
        print(" ".join(f"{b}" for b in d) if d is not None else "Fehler (Servo antwortet nicht?)")
    elif a.befehl == "reg-schreiben":
        d = knoten.register(a.id, a.adresse, len(a.bytes), bytes(a.bytes))
        print("ok" if d is not None else "Fehler")


if __name__ == "__main__":
    main()
