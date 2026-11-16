# so101-lab

Lehr-Repository für den SO-101-Roboterarm und die LeKiwi-Plattform (Hochschule, Elektrotechnik/Robotik).
Stand und Planung: [PLAN.md](PLAN.md). Die Doku (Kapitel 00–07) liegt in `docs/`.

```bash
uv sync                    # Python 3.12 und alle Pakete (CPU-torch)
uv run so101-lab doctor    # Umgebung, Ports, Kameras prüfen
uv run so101-lab ports     # USB-Seriell-Ports anzeigen
uv run so101-lab scan      # Servos am Bus suchen
uv run so101-lab cameras   # Kameras suchen, Eintrag für arms.toml vorschlagen
uv run so101-lab web       # Web-App starten, dann http://localhost:8000 im Browser öffnen
uv run so101-lab service   # systemd-Unit für den Raspberry Pi ausgeben (Kapitel 06)
uv run so101-lab esp32 suchen   # ESP32-Funkknoten im WLAN finden (Kapitel 07), esp32 sim: ohne Hardware
uv run mkdocs serve        # Doku lokal
uv run pytest              # Tests (ohne Hardware)
uv run ruff check . && uv run ruff format --check .
```

Lernskripte (jedes für sich lauffähig, mit `--device demo` auch ohne Hardware):

```bash
uv run scripts/01_find_port.py
uv run scripts/02_read_servo.py --gelenk shoulder_pan
uv run scripts/03_move_servo.py --gelenk wrist_flex
```

Weitere: `04_write_param.py`, `05_set_servo_id.py`, `06_record_trajectory.py`, `07_replay_trajectory.py`,
`09_drive_base.py` (LeKiwi-Basis fahren, ohne Hardware mit `--device demo_kiwi`).

Für Training mit GPU: `uv sync --no-group cpu --group cuda`.
