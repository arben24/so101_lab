"""systemd-Dienst für den Raspberry Pi: Web-App samt Bus-Freigabe startet beim Einschalten.

`so101-lab service` gibt eine Unit-Datei mit dem aktuellen Nutzer, dem Repo-Pfad und dem
Pfad zu `uv` aus. Installiert wird sie von Hand (Kapitel 06), damit sichtbar bleibt, was
passiert:

    uv run so101-lab service | sudo tee /etc/systemd/system/so101-lab.service

Das Token steht nicht in der Unit-Datei (die ist für alle lesbar), sondern in
`/etc/so101-lab.env` (nur root lesbar), Zeile `SO101_LAB_TOKEN=…`.

Beenden: systemd schickt SIGTERM, uvicorn fährt die Web-App herunter, dabei setzt
`LabServer.shutdown` alle Geräte sicher zurück (Räder stopp, `safe_state`).
"""

from __future__ import annotations

import getpass
import shutil
from pathlib import Path

from .paths import project_root

ENV_FILE = "/etc/so101-lab.env"
UNIT_NAME = "so101-lab.service"


def unit_text(
    *,
    user: str | None = None,
    root: Path | None = None,
    uv: str | None = None,
    port: int = 8000,
    docs: bool = False,
    config: str | None = None,
) -> str:
    user = user or getpass.getuser()
    root = Path(root or project_root()).resolve()
    uv = uv or shutil.which("uv") or str(Path.home() / ".local" / "bin" / "uv")
    command = [uv, "run", "--no-sync", "so101-lab", "web", "--host", "0.0.0.0", "--port", str(port)]
    if not docs:
        command.append("--no-docs")  # Start in 2 s statt Doku-Bau bei jedem Start
    if config:
        command += ["--config", str(Path(config).resolve())]
    return f"""\
# so101-lab: Web-App und Bus-Freigabe (RemoteBackend) auf diesem Rechner.
# Erzeugt mit `so101-lab service`. Installieren (Kapitel 06):
#   uv run so101-lab service | sudo tee /etc/systemd/system/{UNIT_NAME}
#   sudo systemctl daemon-reload && sudo systemctl enable --now so101-lab
# Token in {ENV_FILE} (nur root lesbar): SO101_LAB_TOKEN=<geheim>

[Unit]
Description=so101-lab Web-App und Bus-Freigabe
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User={user}
# Servo-Adapter (/dev/ttyACM*) und Kameras (/dev/video*)
SupplementaryGroups=dialout video
WorkingDirectory={root}
EnvironmentFile={ENV_FILE}
Environment=PYTHONUNBUFFERED=1
ExecStart={" ".join(command)}
# SIGTERM: Geräte werden sicher zurückgesetzt, dann endet der Dienst
KillSignal=SIGTERM
TimeoutStopSec=15
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
"""
