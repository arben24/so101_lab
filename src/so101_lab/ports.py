"""Serielle USB-Ports auflisten.

Wird von `so101-lab ports`, `so101-lab scan`, `doctor` und später von der Web-App
(Auswahlliste) benutzt. Es gibt bewusst keine festen Gerätenamen über udev-Regeln:
Der Nutzer wählt den Port aus dieser Liste.
"""

from __future__ import annotations

from dataclasses import dataclass

# USB-Kennungen bekannter Servo-Adapter. Nur zur Anzeige, ausgewählt wird trotzdem von Hand.
KNOWN_ADAPTERS: dict[tuple[int, int], str] = {
    (0x1A86, 0x55D3): "QinHeng USB Single Serial (Servo-Adapter)",
}


@dataclass(frozen=True)
class SerialPortInfo:
    device: str  # z. B. /dev/ttyACM0, COM3, /dev/cu.usbmodem…
    vid: int | None
    pid: int | None
    description: str
    serial_number: str | None
    manufacturer: str | None

    @property
    def usb_id(self) -> str | None:
        if self.vid is None or self.pid is None:
            return None
        return f"{self.vid:04x}:{self.pid:04x}"

    @property
    def known_adapter(self) -> str | None:
        if self.vid is None or self.pid is None:
            return None
        return KNOWN_ADAPTERS.get((self.vid, self.pid))

    @property
    def label(self) -> str:
        """Text für Auswahllisten: Gerät, USB-ID und Beschreibung."""
        parts = [self.device]
        if self.usb_id:
            parts.append(f"({self.usb_id})")
        name = self.known_adapter or self.description
        if name and name != "n/a":
            parts.append(f"– {name}")
        if self.serial_number:
            parts.append(f"[SN {self.serial_number}]")
        return " ".join(parts)


def list_serial_ports(usb_only: bool = True) -> list[SerialPortInfo]:
    """Alle seriellen Ports. `usb_only` lässt fest eingebaute Schnittstellen (ohne USB-ID) weg.

    Funktioniert unter Linux (ttyACM*, ttyUSB*), Windows (COMx) und macOS (cu.usbmodem*).
    """
    from serial.tools import list_ports

    ports = [
        SerialPortInfo(
            device=p.device,
            vid=p.vid,
            pid=p.pid,
            description=p.description or "",
            serial_number=p.serial_number,
            manufacturer=p.manufacturer,
        )
        for p in list_ports.comports()
    ]
    if usb_only:
        ports = [p for p in ports if p.vid is not None]
    # macOS meldet jeden Port doppelt (tty.* und cu.*). cu.* ist der richtige zum Öffnen.
    devices = {p.device for p in ports}
    ports = [
        p
        for p in ports
        if not (p.device.startswith("/dev/tty.") and p.device.replace("/dev/tty.", "/dev/cu.") in devices)
    ]
    return sorted(ports, key=lambda p: p.device)


def adapter_serial(device: str | None) -> str | None:
    """Seriennummer des USB-Adapters an diesem Port (für Protokolle), sonst None."""
    if not device:
        return None
    try:
        return next((p.serial_number for p in list_serial_ports() if p.device == device), None)
    except Exception:  # Portliste ist nur Zusatzinfo
        return None
