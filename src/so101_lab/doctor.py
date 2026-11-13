"""`so101-lab doctor`: prüft, ob die Umgebung für den Arm bereit ist.

Der Befehl liest nur und schreibt nichts. Er öffnet keine seriellen Ports,
sendet also nichts an die Servos.
"""

from __future__ import annotations

import glob
import grp
import importlib.metadata as md
import os
import platform
import shutil
import sys
import tomllib
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from . import LEROBOT_PINNED_VERSION

OK, WARN, FAIL, INFO = "ok", "warn", "fail", "info"
SYMBOLS = {OK: "[ OK ]", WARN: "[WARN]", FAIL: "[FEHL]", INFO: "[ .. ]"}

# USB-Kennungen bekannter Servo-Adapter (siehe config/99-so101.rules)
KNOWN_ADAPTERS = {(0x1A86, 0x55D3): "QinHeng USB Single Serial"}


@dataclass
class Result:
    status: str
    text: str
    hint: str = ""


def check_python() -> list[Result]:
    v = sys.version_info
    if (v.major, v.minor) == (3, 12):
        return [Result(OK, f"Python {platform.python_version()}")]
    return [
        Result(
            FAIL,
            f"Python {platform.python_version()}",
            "Das Projekt braucht Python 3.12 (uv sync legt es an).",
        )
    ]


def check_lerobot() -> list[Result]:
    try:
        version = md.version("lerobot")
    except md.PackageNotFoundError:
        return [Result(FAIL, "LeRobot nicht installiert", "uv sync ausführen.")]
    if version != LEROBOT_PINNED_VERSION:
        return [Result(WARN, f"LeRobot {version}, erwartet {LEROBOT_PINNED_VERSION}", "uv sync ausführen.")]
    return [Result(OK, f"LeRobot {version}")]


def check_packages() -> list[Result]:
    results = []
    for name in ("rustypot", "pyserial", "opencv-python-headless", "pyzmq"):
        try:
            results.append(Result(OK, f"{name} {md.version(name)}"))
        except md.PackageNotFoundError:
            results.append(Result(FAIL, f"{name} fehlt", "uv sync ausführen."))
    try:
        torch_version = md.version("torch")
        kind = "CUDA-Build" if "cu" in torch_version.split("+")[-1] else "CPU-Build"
        if "+" not in torch_version:
            kind = "Standard-Build"
        results.append(Result(OK, f"torch {torch_version} ({kind})"))
    except md.PackageNotFoundError:
        results.append(Result(FAIL, "torch fehlt", "uv sync ausführen."))
    return results


def check_compiler() -> list[Result]:
    """evdev (Abhängigkeit von pynput) wird unter Linux beim Installieren kompiliert."""
    if sys.platform != "linux":
        return []
    try:
        md.version("evdev")
        return [Result(OK, "evdev installiert")]
    except md.PackageNotFoundError:
        pass
    if shutil.which("gcc") or shutil.which("cc"):
        return [Result(WARN, "evdev fehlt, Compiler ist aber vorhanden", "uv sync ausführen.")]
    return [
        Result(
            FAIL, "evdev fehlt und kein C-Compiler gefunden", "sudo apt install build-essential python3-dev"
        )
    ]


def list_serial_ports() -> list[tuple[str, int | None, int | None, str]]:
    """Alle seriellen Ports als (Gerät, vid, pid, Beschreibung)."""
    from serial.tools import list_ports

    ports = []
    for p in list_ports.comports():
        if sys.platform == "linux" and p.device.startswith("/dev/ttyS"):
            continue  # fest eingebaute Schnittstellen, nie ein Servo-Adapter
        ports.append((p.device, p.vid, p.pid, p.description or ""))
    return sorted(ports)


def check_ports() -> list[Result]:
    try:
        ports = list_serial_ports()
    except Exception as exc:  # pyserial fehlt oder Plattformproblem
        return [Result(FAIL, f"Portsuche fehlgeschlagen: {exc}")]
    if not ports:
        return [
            Result(WARN, "Kein serieller Port gefunden", "Adapter anstecken, Servo-Netzteil einschalten.")
        ]
    results = []
    for device, vid, pid, desc in ports:
        known = KNOWN_ADAPTERS.get((vid, pid)) if vid is not None and pid is not None else None
        ident = f"{vid:04x}:{pid:04x}" if vid is not None and pid is not None else "ohne USB-ID"
        label = f"{device} ({ident}, {known or desc})"
        if sys.platform in ("linux", "darwin") and not os.access(device, os.R_OK | os.W_OK):
            results.append(Result(FAIL, f"{label}: keine Zugriffsrechte", _permission_hint(device)))
        else:
            results.append(Result(OK if known else INFO, label))
    return results


def _permission_hint(device: str) -> str:
    if sys.platform != "linux":
        return "Zugriffsrechte für den Port prüfen."
    try:
        group = grp.getgrgid(os.stat(device).st_gid).gr_name
    except (KeyError, OSError):
        group = "dialout"
    return (
        f"Entweder udev-Regel installieren (config/99-so101.rules, siehe Kapitel 02) "
        f"oder 'sudo usermod -aG {group} $USER' und neu anmelden."
    )


def check_linux_groups() -> list[Result]:
    if sys.platform != "linux":
        return []
    try:
        names = {grp.getgrgid(g).gr_name for g in os.getgroups()}
    except KeyError:
        return []
    if "dialout" in names:
        return [Result(OK, "Benutzer ist in der Gruppe 'dialout'")]
    return [
        Result(
            INFO,
            "Benutzer ist nicht in der Gruppe 'dialout'",
            "Kein Problem, wenn die udev-Regel installiert ist.",
        )
    ]


def check_udev_rule() -> list[Result]:
    if sys.platform != "linux":
        return []
    if Path("/etc/udev/rules.d/99-so101.rules").exists():
        return [Result(OK, "udev-Regel 99-so101.rules ist installiert")]
    return [
        Result(
            INFO,
            "udev-Regel 99-so101.rules ist nicht installiert",
            "Siehe Kapitel 02 und config/99-so101.rules.",
        )
    ]


def check_cameras() -> list[Result]:
    """Nur eine leichte Suche. Eine Kamera zu öffnen, kann einen Treiber belegen."""
    if sys.platform == "linux":
        devices = sorted(glob.glob("/dev/video*"))
        if not devices:
            return [Result(INFO, "Keine Kamera (/dev/video*) gefunden")]
        unreadable = [d for d in devices if not os.access(d, os.R_OK)]
        results = [Result(OK, f"{len(devices)} Videogeräte: {', '.join(devices)}")]
        if unreadable:
            results.append(
                Result(WARN, f"Kein Zugriff auf {', '.join(unreadable)}", "Gruppe 'video' prüfen.")
            )
        return results
    return [Result(INFO, "Kamerasuche: 'uv run lerobot-find-cameras' ausführen")]


def check_config(config_path: str | None) -> list[Result]:
    path = Path(config_path) if config_path else Path("config/arms.toml")
    if not path.exists():
        return [Result(WARN, f"{path} nicht gefunden", "Im Projektordner ausführen oder --config angeben.")]
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        return [Result(FAIL, f"{path} ist kein gültiges TOML: {exc}")]
    devices = data.get("device", [])
    return [Result(OK, f"{path}: {len(devices)} Gerät(e) konfiguriert")]


CHECKS: list[tuple[str, Callable[..., list[Result]]]] = [
    ("System", check_python),
    ("LeRobot und Pakete", check_lerobot),
    ("", check_packages),
    ("", check_compiler),
    ("Serielle Ports", check_ports),
    ("", check_linux_groups),
    ("", check_udev_rule),
    ("Kameras", check_cameras),
]


def run_doctor(config_path: str | None = None) -> int:
    print(f"so101-lab doctor ({platform.system()} {platform.release()})")
    failed = warned = 0
    for title, check in CHECKS:
        if title:
            print(f"\n{title}")
        for r in check():
            print(f"  {SYMBOLS[r.status]} {r.text}")
            if r.hint:
                print(f"         -> {r.hint}")
            failed += r.status == FAIL
            warned += r.status == WARN
    print("\nKonfiguration")
    for r in check_config(config_path):
        print(f"  {SYMBOLS[r.status]} {r.text}")
        if r.hint:
            print(f"         -> {r.hint}")
        failed += r.status == FAIL
        warned += r.status == WARN
    print()
    if failed:
        print(f"{failed} Fehler, {warned} Warnungen.")
        return 1
    print(f"Keine Fehler, {warned} Warnungen.")
    return 0
