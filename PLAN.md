# PLAN.md – so101-lab

Übergabedatei für das Lehr-Repository „so101-lab“ (SO-101-Arm und LeKiwi).
**Zu Beginn jeder Sitzung zuerst lesen.** Hier stehen der Stand, die Phasen, die
geprüften Fakten, die Entscheidungen und die offenen Fragen.

Arbeitsweise: phasenweise nach Abschnitt 2. Nach jeder Phase anhalten und auf das
„weiter“ des Auftraggebers warten. Nichts aus dem Gedächtnis behaupten, was sich in
der installierten, gepinnten LeRobot-Version prüfen lässt.

---

## 1. Aktueller Stand

| | |
|---|---|
| Datum | 15.11.2026 |
| Phase | **7 – Netzwerk / Raspberry Pi umgesetzt** (gegen Mock-Geräte und eine zweite Web-App am selben Rechner geprüft; **an einem echten Pi und im WLAN ungeprüft**). Dazu LeKiwi als drei Gerätetypen (Frage 17). Phase 6 weiterhin am echten Arm nur teilweise geprüft. *Zwischenhalt nach Phase 7.* |
| Nächste Phase | 8 – ESP32 (Modell-Empfehlung: Stark). Vorher sinnvoll: Abnahme Phase 7 an einem Pi (Frage 26) |
| Projektcode | Backends `usb`/`mock`/`remote` (`backends/remote.py`), Bus-Freigabe auf dem Pi (`web/bus_server.py`, WebSocket `/ws/bus/<gerät>`), `service.py` (systemd-Unit, CLI `so101-lab service`), `Arm`, `calibrate.py` (Kalibrierung schrittweise, behält Einträge anderer Teile), `servo_setup.py` (ID-Vergabe), `motion.py` (Recorder, Abspieler, Teleoperation, Umrechnung zwischen Armen), `calibration_files.py` (Kalibrierdateien verwalten), `cameras.py` (Kameras, Testbild, Suche), `lab_dataset.py` (LeRobot-Dataset), Gerätetypen `so101_follower`, `so101_leader`, `lekiwi`, `lekiwi_arm`, `lekiwi_base`, Skripte 01–07, Web-App (`src/so101_lab/web/`: `app.py`, `session.py`, `setup_session.py`, `bus_server.py`, `docs_build.py`, `static/` mit Reitern Status, Einzelservo, Register, Kalibrierung, Kalibrierdateien, Aufnahmen, Datensätze, Einrichtung), CLI `so101-lab cameras`/`service`, 227 Tests |
| Doku | Kapitel 00–06 (06: Raspberry Pi nach dem Aufbau der LeKiwi-Anleitung, Betriebsarten A/B, Dienst, Sicherheit, Fehlersuche, LeRobot-Host als Alternative), Anhang: API-Referenz aller Routen und des Bus-Protokolls (per Test abgeglichen), LeKiwi-Typen in 01, Umrechnung zwischen Armen in 04, Web-App-Abschnitt in 00, Kalibrierung über die Web-App in 02, ID-Vergabe in 02, Register-Explorer in 03, Schaltflächen „In der Web-App ausprobieren“. Platzhalter für Netzteil- und Motorvarianten in Kapitel 01 noch offen (Frage 9) |
| Rechner | Seit 14.11.2026 auch `/home/nico/so101_lab` (Klon). uv 0.13.8 dort per Astral-Installer nach `~/.local/bin`, `uv sync` 2,1 GB. **Dort ist ROS 2 Humble im `PYTHONPATH`**: pytest lädt dann das ROS-Plugin `launch_testing` und bricht ab (`No module named 'lark'`). Abhilfe: `env -u PYTHONPATH uv run pytest` (Frage 16) |
| Im Repo | `PLAN.md`, `firmware/esp32/` (ESP32-Code, unverändert) |
| Hardware-Tests | 13.11.2026 (Phase 2): `/dev/ttyACM0` (Adapter SN 5B90103144), kein Servo antwortete. **13.11.2026 (Phase 4, nur lesend, durch Claude):** an `/dev/ttyACM0` (Adapter **SN 5AAF263183**, also ein anderer Adapter) antworten **alle sechs Follower-Servos**, 11,8–12,0 V, 29–32 °C, Drehmoment aus, keine Fehlerbits, noch keine Kalibrierdatei. Web-App-Abfrage hält 30 Hz. Gemessen (100 Wiederholungen): `read_positions` (ein Sync-Read, 6 Servos) Median 1,0 ms, max 1,6 ms; `read_state` (8 Sync-Reads) Median 9,0 ms, max 10,0 ms. **Bewegt wurde nichts**, Skripte 03–07 am echten Arm noch ungeprüft. **13.11.2026, Auftraggeber:** Teilschritt 4a am echten Arm funktioniert (Drehmoment an, Arm hält die Position, alle Werte lesbar). Teilschritt 4b (Einzelservo, Register, Kalibrierung) nur gegen Mock und im Browser (headless Chrome) geprüft, am echten Arm noch nicht. **14.11.2026, Auftraggeber:** 4b abgenommen (Kalibrierdatei `follower_1.json` kam aus der Web-App am Arm). **14.11.2026 (Phase 5, nur lesend, durch Claude):** an `/dev/ttyACM0` (SN 5AAF263183) findet die Suche über alle Baudraten **neun** Servos, IDs 1–9, alle STS3215 bei 1 Mbit/s, **Firmware 3.10**, 11,8 V, Drehmoment aus. Phase 4 hatte nur die IDs 1–6 angepingt (Frage 17). Suche über alle 8 Baudraten 8,0 s, nur 1 Mbit/s 0,80 s. Der Assistent lehnt das Schreiben ab („9 Servos“), Protokoll bleibt leer. **ID schreiben am echten Servo ist noch ungeprüft** (braucht einen einzelnen, losen Servo) |

---

## 2. Phasen

Modell-Empfehlung: **Stark** = Architektur, sicherheitskritische Hardwarezugriffe,
Protokolle, Netzwerk. **Mittel** = Umsetzung nach klarer Vorgabe. **Klein** =
Textarbeit, Boilerplate, Styling.

- [x] **0 – Erkundung** (Stark): LeRobot-Version, Bus-API, Steuertabelle, Gelenke, Kalibrierung, CLI, ESP32-Protokoll
- [x] **1 – Grundgerüst** (Mittel): uv-Projekt, `pyproject.toml` mit Gruppen, `config/arms.toml`, udev-Regel, CLI `so101-lab` mit `doctor`, ruff/pytest, MkDocs-Gerüst
- [x] **2 – Backend-Schicht** (Stark): `ArmBackend`, `UsbBackend`, `MockBackend`, Konfigurations-Lader, Sicherheitsfunktionen, Tests
- [x] **3 – Lernskripte + Grundlagen-Doku** (Mittel, Glättung Klein): Skripte 01–07, Kapitel 00–04
- [x] **4 – Web-App-Kern** (Stark, Styling Klein): FastAPI, Statusseite, Not-Aus, Einzelservo, Register-Explorer, WebSocket, Token, Steuer-Sperre, Watchdog, **Kalibrierungs-Button** (vorgezogen). *Nach erstem lauffähigen Teilschritt kurz anhalten.*
  - [x] 4a: Server, Statusseite mit Port-Dropdown, Live-Zustand per WebSocket, Not-Aus, Token, Steuer-Sperre, Watchdog, Doku unter `/docs`
  - [x] 4b: Einzelservo-Panel, Register-Explorer, Kalibrierungs-Assistent, Doku-Buttons
- [x] **5 – ID-Einrichtungsassistent** (Stark): Assistent in der Web-App, Ein-Servo-Pflicht, Verifikation, Protokoll, Baudrate. Skript 05 benutzt jetzt denselben Ablauf. *Abnahme am echten Servo offen.*
- [x] **6 – Trajektorien & Kamera** (Mittel, Dataset Stark): Recorder, Bibliothek, Abspielen, Kamera, LeRobot-Dataset, Kapitel 05 (Kalibrierung schon in Phase 4). *Zwischenhalt.*
  - [x] 6a: Recorder (von Hand / während Teleoperation), Teleoperation Leader → Follower, Bibliothek mit Kurvenansicht, CSV, Löschen, Abspielen mit Fortschritt in der Web-App; Umrechnung zwischen Armen auch in Skript 07. *Am echten Arm ungeprüft.*
  - [x] 6b: Kameras in `arms.toml` (Vorschau, Suche, Testbild), LeRobot-Dataset v3.0 lokal, Kapitel 05 Teil 2. *Mit echtem Arm ungeprüft.*
- [x] **7 – Netzwerk / Raspberry Pi** (Stark): `RemoteBackend`, Pi-Anleitung, systemd, API-Referenz, Kapitel 06. *Zwischenhalt. Am echten Pi ungeprüft.*
- [ ] **8 – ESP32** (Stark): `Esp32Backend`, Funktionsmatrix, Kapitel 07
- [ ] **9 – LeKiwi** (Stark, Doku Mittel): Räder, Kinematik, Fahrpanel, Skript 09, Kapitel 08. *Zwischenhalt.*
- [ ] **10 – Übungen & Feinschliff** (Klein, Musterlösungen Mittel)
- [ ] **11 – optional: Simulation** (Stark): `SimBackend` mit MuJoCo

---

## 3. Geprüfte Fakten zu LeRobot 0.7.0

Quelle, wenn nicht anders angegeben: Quellcode des installierten Pakets
(`.venv/lib/python3.12/site-packages/lerobot/…`), geprüft am 13.11.2026 in einer
Wegwerf-Umgebung. Pfade unten sind relativ zu diesem Paketordner.

### 3.1 Version und Installation

- Neueste Version auf PyPI: **lerobot 0.7.0** vom 05.11.2026, verlangt Python ≥ 3.12. Davor: 0.6.1 (03.08.2026), 0.6.0, 0.5.1.
- Testinstallation mit uv 0.9.8, Python 3.12.12, Linux x86_64:
  `dependencies = ["lerobot[feetech,lekiwi,core-scripts]==0.7.0"]`, `uv sync` läuft durch
  (97 Pakete aufgelöst, 87 installiert).
- Extras (aus den PyPI-Metadaten):
  - `feetech` → `serial-motors` → **rustypot** + pyserial
  - `lekiwi` → `feetech` + pyzmq
  - `core-scripts` → `dataset` + `hardware` + `viz` (datasets, pandas, pyarrow, av, torchcodec, pynput, pyserial, deepdiff, rerun-sdk, foxglove-sdk)
- Wichtige aufgelöste Versionen: torch 2.15.0, torchvision 0.29.2, numpy 2.2.6, rustypot 1.13.1, pyserial 3.5, pyzmq 27.3.0, draccus 0.11.6, datasets 5.2.0, opencv-python-headless 4.13.0.92, av 15.1.0, torchcodec 0.17.0, rerun-sdk 0.33.1, pynput 1.8.2, evdev 2.0.0.
- **Größe (Standard-Installation):** Die Umgebung belegt 6,3 GB. Davon 3,0 GB NVIDIA-CUDA-Bibliotheken und 1,2 GB torch. torch ist eine Basis-Abhängigkeit von LeRobot, also nicht abwählbar.
- **Wichtigste Änderung gegenüber 0.6.x:** In 0.6.1 hing `feetech` noch an `feetech-servo-sdk`. In 0.7.0 läuft der Feetech-Bus über **rustypot** (Pollen Robotics). Die Bus-Klasse heißt jetzt `SerialMotorsBus`, `FeetechMotorsBus` ist nur noch ein veralteter Alias (siehe 3.4).

**torch optional? (geprüft am 13.11.2026, Linux x86_64, lerobot 0.7.0)**

| Variante | Größe | Ergebnis |
|---|---|---|
| Standard (torch mit CUDA von PyPI) | 6,3 GB | alles funktioniert |
| `[tool.uv] exclude-dependencies = ["torch", "torchvision"]` | 379 MB | `lerobot.motors` importiert, aber `lerobot.robots` (Follower, LeKiwi, damit auch `lerobot-calibrate`, `-teleoperate`, `-record`) scheitert: `lerobot/lerobot_types.py` macht `import torch` |
| **CPU-torch** über `[[tool.uv.index]]` `https://download.pytorch.org/whl/cpu` (explicit) | **1,2 GB** | `torch 2.15.0+cpu`, `SO101Follower` und `LeKiwi` importieren |

- torch bleibt also eine Pflicht-Abhängigkeit, ein Weglassen ist nur für `lerobot.motors` möglich. Die CPU-Variante genügt für Ansteuerung, Kalibrierung, Aufnahme und Replay.
- `[tool.uv.sources]` wirkt **nur auf direkte Abhängigkeiten**: `torch` und `torchvision` müssen dafür selbst in `dependencies` stehen, sonst kommt weiter die CUDA-Version von PyPI.
- CUDA (Training, Übung 7) wird als optionaler Extra umgesetzt, der die Quelle auf PyPI umstellt. Dafür braucht uv `[tool.uv] conflicts` zwischen den Extras. Umsetzung und Test in Phase 1.
- Der Index-Eintrag gilt nur für Linux x86_64 (Marker). Windows, macOS und aarch64 bekommen ihre Standard-Räder (Windows 2.15.0 ohne CUDA-Pakete laut Auflösung, macOS 2.11.0).

### 3.2 Plattformen (Auflösung geprüft, nicht auf den Systemen installiert)

| Plattform | Ergebnis |
|---|---|
| Linux x86_64 | alles als Wheel, **außer `evdev`** (nur Quellpaket, wird beim Installieren kompiliert). Auf dem Entwicklungsrechner hat das funktioniert (gcc vorhanden) |
| Linux aarch64, glibc 2.36 (Raspberry Pi OS Bookworm, 64 bit) | wie oben: alles als Wheel außer `evdev`. rustypot hat Wheels für aarch64 |
| Windows x86_64 | löst vollständig mit Wheels auf |
| macOS arm64 | löst vollständig mit Wheels auf, dort aber torch 2.11.0 und torchcodec 0.11.1 (andere Versionen durch Plattform-Marker) |

`evdev` kommt über `pynput` (Extra `hardware`) und braucht unter Linux einen C-Compiler
und Kernel-Header.

### 3.3 CLI-Befehle (aus `entry_points.txt`)

`lerobot-find-port`, `lerobot-setup-motors`, `lerobot-calibrate`, `lerobot-teleoperate`,
`lerobot-record`, `lerobot-replay`, `lerobot-find-cameras`, `lerobot-find-joint-limits`,
`lerobot-dataset-viz`, `lerobot-edit-dataset`, `lerobot-info`, `lerobot-train`,
`lerobot-eval`, `lerobot-rollout`, `lerobot-policy-server`, `lerobot-env-server`,
`lerobot-setup-can`, `lerobot-imgtransform-viz`, `lerobot-annotate`,
`lerobot-train-tokenizer`, `lerobot-convert-dcp`, `lerobot-build-mp4-sidecar`.

- Gerätetypen für `--robot.type`: `so101_follower`, `so100_follower`, `bi_so_follower`, `lekiwi`, `lekiwi_client`. Für `--teleop.type`: `so101_leader`, `so100_leader`, `bi_so_leader`, `keyboard`.
- Beispielaufrufe aus den Docstrings der Skripte (`scripts/lerobot_*.py`):
  - `lerobot-find-port` (ohne Argumente, interaktiv: Kabel abziehen, Enter)
  - `lerobot-setup-motors --robot.type=so101_follower --robot.port=<Port>`
  - `lerobot-calibrate --robot.type=so101_follower --robot.port=<Port> --robot.id=<Name>`
  - `lerobot-teleoperate --robot.type=so101_follower --robot.port=… --robot.id=… --teleop.type=so101_leader --teleop.port=… --teleop.id=…` (Standard 60 fps)
  - `lerobot-record … --dataset.repo_id=<user>/<name> --dataset.num_episodes=2 --dataset.single_task="…"`
  - `lerobot-replay --robot.type=… --robot.port=… --robot.id=… --dataset.repo_id=… --dataset.episode=0`
  - `lerobot-dataset-viz --repo-id <…> --episode-index 0`
- `lerobot-record` ist nur noch für Datenaufnahme. Policies führt `lerobot-rollout` aus.
- `lerobot-teleoperate` importiert `lekiwi` nicht. LeKiwi wird über Host und Client betrieben (3.7).
- Der LeKiwi-Host hat keinen eigenen CLI-Befehl. Er ist das Modul `lerobot.robots.lekiwi.lekiwi_host` (Argumente `--robot.*`, `--host.*`).

### 3.4 Motorbus-API (`motors/motors_bus.py`, 1295 Zeilen)

```python
from lerobot.motors import Motor, MotorCalibration, MotorNormMode, SerialMotorsBus
```

- `SerialMotorsBus(port, motors: dict[str, Motor], calibration=None)`. `default_baudrate = 1_000_000`, `default_timeout = 50` (ms je Versuch).
- `Motor(id, model, norm_mode)`, `MotorNormMode`: `RANGE_0_100`, `RANGE_M100_100`, `DEGREES`.
- `MotorCalibration(id, drive_mode, homing_offset, range_min, range_max)`.
- Methoden: `connect(handshake=True)`, `disconnect(disable_torque=True)`, `ping(motor)` (liest `Model_Number`, liefert Modellnummer oder `None`), `read`/`write(data_name, motor, …, normalize=True, num_retry=0)`, `sync_read`/`sync_write`, `enable_torque`/`disable_torque(motors, num_retry)`, Kontextmanager `torque_disabled()`, `set_baudrate`, `set_operating_mode(mode: str, motors)`, `configure_motors(return_delay_time=0, maximum_acceleration=254, acceleration=254)`, `setup_motor(motor, initial_baudrate=None, initial_id=None)`, `setup_motors()`, `read_calibration`, `write_calibration`, `reset_calibration`, `set_half_turn_homings`, `record_ranges_of_motion`, `record_calibration`, `is_calibrated`, `resolution(model)`.
- Registernamen ohne Beachtung der Groß-/Kleinschreibung. Normalisiert werden nur `Goal_Position` und `Present_Position`.
- `sync_write` wartet auf keine Antwort (ein verlorenes Paket bleibt unbemerkt), `write` wird quittiert.
- Handshake beim Verbinden: jeder Motor muss mit der erwarteten Modellnummer antworten, alle Motoren einer Definition müssen dieselbe Firmware haben. Sonst `RuntimeError`.
- **Kein öffentlicher Bus-Scan** über IDs und Baudraten. Es gibt nur die privaten `_scan` und `_find_single_motor`. Der Scan liegt eine Ebene tiefer in rustypot:
  `rustypot.Bus(port, baudrate, timeout_s, {id: definition})` mit `scan(definition, ids=None)`, `broadcast_scan(definition, ids=None)`, `set_baudrate`, `change_id(definition, id, new_id)`, `change_baudrate(definition, id, baudrate)`, `set_torque(ids, enabled, retries)`, `read_register[_with_error](id, name)`, `write_register[_with_error]`, `sync_read_register[_with_error]`, `sync_write_register`, `set_timeout`, `close`, `is_open`.
- rustypot `Bus` adressiert Register **nur über Namen**, nicht über rohe Adressen.
- **EEPROM-Sperre folgt dem Drehmoment:** `Bus.set_torque(…, False)` öffnet auf Feetech-Servos das Register `Lock`, `set_torque(…, True)` schließt es. `change_id` schaltet vorher das Drehmoment aus und öffnet die Sperre.
- `setup_motor` (so macht es `lerobot-setup-motors`): Motor suchen (Standard-Baudrate zuerst, dann Werks-Baudrate, dann der Rest) → `change_id` → `change_baudrate` → Port zurück auf 1 Mbit/s → per `ping` unter der neuen ID prüfen.
- Ohne Hardware geprüft (Pseudo-Terminal): `scan` sendet je ID `FF FF <id> 04 02 03 02 <chk>` (liest `Model_Number`), `broadcast_scan` sendet `FF FF FE 02 01 FE`. Ohne Antwort liefert beides `{}`, `read_register` wirft `RuntimeError("Operation timed out")`, ein fehlender Port `OSError`.
- **Veraltet:** `lerobot.motors.feetech.FeetechMotorsBus` gibt eine `DeprecationWarning` aus und soll in einer künftigen Version entfallen. `OperatingMode` und `TorqueMode` liegen ebenfalls nur noch dort.

### 3.5 Steuertabelle STS3215

Quelle der Namen, Adressen, Größen und Kodierungen: `rustypot.find_model("sts3215")`
→ Modellnummer 777, Definition `STS3215`. rustypot kennt **keine Speicherbereiche**
(EEPROM/SRAM). Die Spalte „Speicher“ stammt aus der Feetech-Speichertabelle V3.7
(`firmware/esp32/doku/sts3215/STS3215_Speichertabelle_V3.7.xls`): Adressen 0–39
EPROM, 40–69 SRAM.

Eigenschaften der Definition: Protokoll 1, Auflösung 4096 Schritte je Umdrehung,
Werks-Baudrate 1 Mbit/s, Betriebsarten `position` 0, `velocity` 1, `pwm` 2, `step` 3,
Byte-Reihenfolge Little Endian, Broadcast-Ping und Sync-Read unterstützt, Fehlerbyte
meldet nur Alarme, Vorzeichen des Homing-Offsets −1 (`present = actual − offset`).
Dieselbe Definition deckt auch STS3250 (2825), STS3036 (521) und SM8512BL (11272) ab.

| Adr. | Name (weitere Namen) | Bytes | Zugriff | Kodierung | Speicher |
|---|---|---|---|---|---|
| 0 | firmware_major_version | 1 | R | | EPROM |
| 1 | firmware_minor_version | 1 | R | | EPROM |
| 3 | model_number (model) | 2 | R | | EPROM |
| 5 | id | 1 | RW | | EPROM |
| 6 | baud_rate (baudrate) | 1 | RW | | EPROM |
| 7 | return_delay_time | 1 | RW | | EPROM |
| 8 | response_status_level | 1 | RW | | EPROM |
| 9 | min_position_limit (min_angle_limit) | 2 | RW | | EPROM |
| 11 | max_position_limit (max_angle_limit) | 2 | RW | | EPROM |
| 13 | max_temperature_limit | 1 | RW | | EPROM |
| 14 | max_voltage_limit | 1 | RW | | EPROM |
| 15 | min_voltage_limit | 1 | RW | | EPROM |
| 16 | max_torque_limit | 2 | RW | | EPROM |
| 18 | phase | 1 | RW | | EPROM |
| 19 | unloading_condition | 1 | RW | | EPROM |
| 20 | led_alarm_condition | 1 | RW | | EPROM |
| 21 | p_coefficient | 1 | RW | | EPROM |
| 22 | d_coefficient | 1 | RW | | EPROM |
| 23 | i_coefficient | 1 | RW | | EPROM |
| 24 | minimum_startup_force | 2 | RW | | EPROM |
| 26 | cw_dead_zone | 1 | RW | | EPROM |
| 27 | ccw_dead_zone | 1 | RW | | EPROM |
| 28 | protection_current | 2 | RW | | EPROM |
| 30 | angular_resolution | 1 | RW | | EPROM |
| 31 | homing_offset (offset) | 2 | RW | Betrag + Vorzeichen, Bit 11 | EPROM |
| 33 | operating_mode (mode) | 1 | RW | | EPROM |
| 34 | protective_torque | 1 | RW | | EPROM |
| 35 | protection_time | 1 | RW | | EPROM |
| 36 | overload_torque | 1 | RW | | EPROM |
| 37 | speed_closed_loop_p_coefficient | 1 | RW | | EPROM |
| 38 | over_current_protection_time | 1 | RW | | EPROM |
| 39 | velocity_closed_loop_i_coefficient | 1 | RW | | EPROM |
| 40 | torque_enable | 1 | RW | | SRAM |
| 41 | acceleration | 1 | RW | | SRAM |
| 42 | goal_position | 2 | RW | Betrag + Vorzeichen, Bit 15 | SRAM |
| 44 | goal_time | 2 | RW | | SRAM |
| 46 | goal_velocity (goal_speed) | 2 | RW | Betrag + Vorzeichen, Bit 15 | SRAM |
| 48 | torque_limit | 2 | RW | | SRAM |
| 55 | lock | 1 | RW | | SRAM |
| 56 | present_position | 2 | R | Betrag + Vorzeichen, Bit 15 | SRAM |
| 58 | present_velocity (present_speed) | 2 | R | Betrag + Vorzeichen, Bit 15 | SRAM |
| 60 | present_load | 2 | R | Betrag + Vorzeichen, Bit 10 | SRAM |
| 62 | present_voltage | 1 | R | | SRAM |
| 63 | present_temperature | 1 | R | | SRAM |
| 65 | status | 1 | R | | SRAM |
| 66 | moving | 1 | R | | SRAM |
| 69 | present_current | 2 | R | | SRAM |
| 80 | moving_velocity_threshold | 1 | RW | | nicht in V3.7 |
| 81 | dts | 1 | RW | | nicht in V3.7 |
| 82 | velocity_unit_factor | 1 | RW | | nicht in V3.7 |
| 83 | hts | 1 | RW | | nicht in V3.7 |
| 84 | maximum_velocity_limit | 1 | RW | | nicht in V3.7 |
| 85 | maximum_acceleration | 1 | RW | | nicht in V3.7 |
| 86 | acceleration_multiplier | 1 | RW | | nicht in V3.7 |

Im Code wird diese Tabelle zur Laufzeit aus rustypot erzeugt
(`definition.registers()`), nicht aus dieser Datei abgeschrieben.

Aus der Feetech-Tabelle V3.7 (Einheiten, für die Doku): `torque_enable` 128 setzt die
aktuelle Stellung auf 2048 (nie schreiben). `acceleration` in 100 Schritt/s².
`goal_velocity` in Schritt/s, höchstens 3400. `lock` 0 = EPROM-Änderungen bleiben nach
dem Ausschalten erhalten, 1 = nicht. `status` Bit 0 Spannung, 1 Sensor, 2 Temperatur,
3 Strom, 4 Winkel, 5 Überlast. `present_voltage` in 0,1 V. `present_current` in 6,5 mA.
Die Tabelle V3.7 beschreibt die 7,4-V-Variante (Werkswert `max_voltage_limit` 80 = 8,0 V).

**Unstimmigkeit Baudraten-Register (Adresse 6):**

| Registerwert | rustypot 1.13.1 | Feetech-Tabelle V3.7 |
|---|---|---|
| 0–4 | 1 000 000, 500 000, 250 000, 128 000, 115 200 | gleich |
| 5 | 57 600 | 76 800 |
| 6 | 38 400 | 57 600 |
| 7 | 19 200 | 38 400 |

Welche Zuordnung stimmt, ist nicht geklärt. Für den Betrieb mit 1 Mbit/s spielt es
keine Rolle. Beim Scan über alle Baudraten und beim Umstellen der Baudrate schon.

**Ergänzungen (13.11.2026, Phase 3, aus V3.7, Protokollhandbuch und `firmware/esp32/doku/sts3215/README.md`):**

- Adresse 3 und 4 heißen in V3.7 „Servo major/minor version“ (Startwerte 9 und 3). rustypot liest Adresse 3 als 2-Byte-`model_number`: 0x0309 = 777.
- `goal_velocity` (Adresse 46) ist im Positionsmodus das **Fahrtempo zum Ziel**. Beleg: Protokollhandbuch Beispiel 4 („Servo 1 auf 2048, Tempo 1000“, Schreiben ab Adresse 42: Position, Zeit, Tempo). README des Auftraggebers: „0 = ohne Grenze“. `Arm.configure_motion` schreibt nie 0 (Untergrenze 1).
- Einheit von `torque_limit` (Adresse 48): V3.7 nennt „1 %“, Wertebereich 0–1000 und Startwert 1000 sprechen für 0,1 % (wie `max_torque_limit`). Im Code und in der Doku gilt 0,1 %.
- `lock` laut V3.7: 0 = EEPROM-Änderungen bleiben nach dem Ausschalten erhalten, 1 = gehen verloren.
- `lerobot-calibrate` verlangt genau ein Gerät (`--robot.*` **oder** `--teleop.*`), ruft `connect(calibrate=False)` (das führt `configure()` aus, also EEPROM-Schreiben) und danach `calibrate()`. Parameter `--robot.calibration_dir` ist ein Ordner, die Datei heißt `<id>.json`.
- `config_so_follower.py`: P 16, I 0, D 32 als Standard beim Verbinden; eine Begründung steht dort nicht.

**Motorvarianten:** Alle STS3215 melden die Modellnummer 777. rustypot kennt keine
Variantennamen (C001, C018, C044, C046, C047 ergeben keinen Treffer). Leader- und
Follower-Servos lassen sich per Software also nicht über die Modellnummer
unterscheiden.

### 3.6 SO-101 Follower und Leader

`robots/so_follower/so_follower.py`, `teleoperators/so_leader/so_leader.py`. SO-100
und SO-101 teilen sich eine Klasse (`SO101Follower = SO100Follower = SOFollower`,
`name = "so_follower"`, ebenso `SOLeader`, `name = "so_leader"`).

| ID | Gelenk | Modell | Normalisierung |
|---|---|---|---|
| 1 | `shoulder_pan` | sts3215 | Grad (bei `use_degrees=True`, Standard), sonst −100…100 |
| 2 | `shoulder_lift` | sts3215 | wie oben |
| 3 | `elbow_flex` | sts3215 | wie oben |
| 4 | `wrist_flex` | sts3215 | wie oben |
| 5 | `wrist_roll` | sts3215 | wie oben |
| 6 | `gripper` | sts3215 | 0…100 |

Leader und Follower haben dieselben Gelenknamen und IDs.

- **Follower `connect()`** führt `configure()` aus und schreibt dabei in jeden Motor: `Return_Delay_Time` 0, `Maximum_Acceleration` 254, `Acceleration` 254, Bit 4 von `Phase` löschen, Betriebsart `position`, `P_Coefficient` 16, `I_Coefficient` 0, `D_Coefficient` 32. Zusätzlich am Greifer: `Max_Torque_Limit` 500, `Protection_Current` 250, `Overload_Torque` 25. Das Drehmoment ist währenddessen aus, die EEPROM-Sperre also offen: **die meisten dieser Werte landen dauerhaft im EEPROM.**
- **Leader `configure()`**: Drehmoment aus, `configure_motors()`, Betriebsart `position`. Das Drehmoment bleibt aus.
- Weitere Konfiguration des Followers: `max_relative_target` (begrenzt den Sprung je Schritt, Standard aus), `disable_torque_on_disconnect=True`, `num_read_retries=2`.
- Beobachtung und Aktion: Schlüssel `<gelenk>.pos`.

### 3.7 LeKiwi

`robots/lekiwi/lekiwi.py`, `config_lekiwi.py`, `lekiwi_host.py`, `lekiwi_client.py`.

| ID | Motor | Betriebsart |
|---|---|---|
| 1–6 | `arm_shoulder_pan`, `arm_shoulder_lift`, `arm_elbow_flex`, `arm_wrist_flex`, `arm_wrist_roll`, `arm_gripper` | Position |
| 7 | `base_left_wheel` | Geschwindigkeit |
| 8 | `base_back_wheel` | Geschwindigkeit |
| 9 | `base_right_wheel` | Geschwindigkeit |

Alle neun Motoren hängen an **einem** Bus (`port`, Standard `/dev/ttyACM0`).

- `configure()`: Drehmoment aus, `configure_motors()`, Arm in `position` mit P 16, I 0, D 32, Räder in `velocity`, danach **Drehmoment für alle Motoren an**.
- Kinematik (`_body_to_wheel_raw`): Radradius 0,05 m, Abstand Mitte–Rad 0,125 m, Radwinkel `[240, 0, 120] − 90` Grad, also links 150°, hinten −90°, rechts 30°. Je Rad: `(cos a · x + sin a · y + R · θ) / r`. Umrechnung in Rohwerte mit 4096/360 Schritten je Grad. Übersteigt ein Rad 3000 Schritt/s, werden alle drei im gleichen Verhältnis verkleinert.
- Aktion und Beobachtung: `arm_*.pos`, `x.vel` und `y.vel` in m/s, `theta.vel` in Grad/s.
- **Host** (`python -m lerobot.robots.lekiwi.lekiwi_host`): ZMQ PULL auf Port 5555 für Befehle (JSON), ZMQ PUSH auf Port 5556 für Beobachtungen (JSON-Kopf plus ein JPEG je Kamera). Watchdog 500 ms, stoppt **nur die Räder**. Schleife mit höchstens 30 Hz. `connection_time_s = 30`: Der Host beendet sich in der Standardeinstellung nach 30 Sekunden.
- **Client** (`lekiwi_client`, `remote_ip`): Tasten w/s/a/d, z/x drehen, r/f Tempostufe, q beenden. Stufen 0,1/0,2/0,3 m/s und 30/60/90 Grad/s.
- Standardkameras: `front` `/dev/video0` 640×480, um 180° gedreht, `wrist` `/dev/video2` 480×640, um 90° gedreht, beide 30 fps, MJPG.
- `setup_motors()`-Reihenfolge: Arm von Greifer (6) bis Schulter (1), dann Räder 9, 8, 7.

### 3.8 Kalibrierung

- Ablage (`robots/robot.py`, `teleoperators/teleoperator.py`, `utils/constants.py`):
  - Roboter: `$HF_LEROBOT_CALIBRATION/robots/<name>/<id>.json`
  - Teleoperatoren: `$HF_LEROBOT_CALIBRATION/teleoperators/<name>/<id>.json`
  - `<name>` ist das Klassenattribut, also `so_follower`, `so_leader`, `lekiwi` (nicht `so101_follower`).
  - Standard für `HF_LEROBOT_CALIBRATION`: `~/.cache/huggingface/lerobot/calibration`. Überschreibbar über die Umgebungsvariable oder je Gerät über `calibration_dir` (`--robot.calibration_dir=…`).
- Format (JSON, über draccus geschrieben), ein Eintrag je Motor:

  ```json
  {
      "shoulder_pan": {
          "id": 1,
          "drive_mode": 0,
          "homing_offset": -123,
          "range_min": 800,
          "range_max": 3200
      }
  }
  ```

- Ablauf (`record_calibration`): Drehmoment aus → Betriebsart `position` → Gelenke in die Mitte stellen, Enter → `set_half_turn_homings` (setzt Kalibrierung zurück und schreibt `Homing_Offset` so, dass die aktuelle Stellung 2047 ergibt) → alle Gelenke durch den ganzen Bereich bewegen, Minimum und Maximum werden aufgezeichnet, Enter. `wrist_roll` gilt als Volldreher und bekommt 0…4095. Danach `write_calibration` (schreibt `Homing_Offset`, `Min_Position_Limit`, `Max_Position_Limit` **in das EEPROM des Servos**) und Speichern der JSON-Datei.
- `is_calibrated` vergleicht die Datei mit den Werten in den Motoren (Grenzen und Homing-Offset).
- Existiert eine Datei, fragt `calibrate()` interaktiv: Enter übernimmt die Datei und schreibt sie in die Motoren, `c` startet eine neue Kalibrierung. Die Kalibrierung ist durchgehend dialoggeführt (`input()`).
- Normalisierung (`_normalize`): Grad = `(roh − (min+max)/2) · 360 / 4095`. −100…100 und 0…100 linear zwischen `range_min` und `range_max`. `drive_mode` wird bei Feetech in Software angewendet.

### 3.9 Datasets

- `CODEBASE_VERSION = "v3.0"` (`datasets/dataset_metadata.py`).
- `DatasetRecordConfig` (`configs/dataset.py`), Standardwerte: `fps` 30, `episode_time_s` 60, `reset_time_s` 60, `num_episodes` 50, `video` True, **`push_to_hub` True**, `root` None (dann `$HF_LEROBOT_HOME/<repo_id>`, also `~/.cache/huggingface/lerobot/…`). An `repo_id` wird ein Zeitstempel angehängt, außer mit `no_stamp`.
- Für den Offline-Betrieb muss jeder Aufruf `--dataset.push_to_hub=false` und ein lokales `--dataset.root` setzen.

---

### 3.10 Kameras und Datasets (geprüft 14.11.2026, Rechner `/home/nico`)

- Webcam „Generic FULL HD 1080P Webcam“ (USB 0bda:58bb, Realtek). Zwei Knoten: `/dev/video0` liefert Bilder, `/dev/video1` lässt sich nicht öffnen (Metadaten). Fester Name `/dev/v4l/by-id/usb-Generic_FULL_HD_1080P_Webcam_200901010001-video-index0`. Zugriff über ACL der Sitzung (Nutzer nicht in Gruppe `video`).
- Bei Dunkelheit (Helligkeit ≈ 5/255) liefert sie in MJPG und YUYV bei 640×480 bis 1920×1080 nur **10 Bilder/s**, vermutlich wegen langer Belichtung. Mit Licht ungeprüft.
- LeRobot 0.7.0: `LeRobotDataset.create(repo_id, fps, root=…, robot_type, features, use_videos=True, image_writer_threads)`, `add_frame`, `save_episode`, `clear_episode_buffer`, `finalize`, `LeRobotDataset.resume(repo_id, root=…)`. Merkmale über `lerobot.utils.feature_utils.hw_to_dataset_features`/`combine_feature_dicts`/`build_dataset_frame` (so baut es `lerobot-record`). Offline mit `HF_HUB_OFFLINE=1` und lokalem `root` geprüft. Video-Codec: SVT-AV1.
- **`torchcodec` kann hier nicht dekodieren** (`libtorchcodec_image.so` lädt nicht), LeRobot fällt auf `pyav` zurück. Für Training langsamer, funktioniert. Ursache (fehlende FFmpeg-Bibliotheken?) ungeprüft.
- `lerobot-dataset-viz` kennt `--repo-id`, `--episode-index`, `--root`.

### 3.11 Raspberry Pi und LeKiwi-Anleitung (geprüft 15.11.2026)

- Offizielle LeKiwi-Anleitung (`huggingface.co/docs/lerobot/lekiwi`): Pi mit Raspberry Pi OS oder Ubuntu über den Imager, SSH, LeRobot nach der Installationsanleitung (conda/miniforge, Python 3.12) plus `pip install -e ".[lekiwi]"`, also **vollständiges LeRobot samt torch auf dem Pi**. Motoren einrichten (`lerobot-setup-motors --robot.type=lekiwi`) und Arm kalibrieren (`lerobot-calibrate --robot.type=lekiwi`) laufen auf dem Pi per SSH, der Leader wird am Laptop kalibriert. Host: `python -m lerobot.robots.lekiwi.lekiwi_host --robot.id=…`, Client am Laptop über `examples/lekiwi/teleoperate.py` mit `remote_ip`. Kabelgebundene LeKiwi: alles am Laptop. Keine Angaben zu Pi-Modell, RAM oder Kartengröße.
- Fehlersuche dort: `hostname -I`, `ping`, `ssh`, `sudo raspi-config` → SSH.
- LeRobot 0.7.0 `LeKiwiHostConfig`: `port_zmq_cmd` 5555, `port_zmq_observations` 5556, `connection_time_s` 30, `watchdog_timeout_ms` 500, `max_loop_freq_hz` 30. `LeKiwiConfig.port` Standard `/dev/ttyACM0`.
- **Lockfile vorher:** unter Linux aarch64 kam torch 2.15.0 von PyPI **mit CUDA-Paketen** (`cuda-toolkit`, `nvidia-cudnn-cu13`, `nccl`, `nvshmem`, `cusparselt`), weil der CPU-Index nur für x86_64 galt. Jetzt gilt er für Linux allgemein: Auflösung für `aarch64-unknown-linux-gnu` ergibt `torch 2.15.0+cpu`, `torchvision 0.29.2+cpu`, keine `nvidia-*`. Auf dem Pi installiert und gemessen ist das noch nicht.
- `websockets` 17.4: `websockets.sync.client.connect()` ohne `with` gibt seit 17.1 eine `DeprecationWarning`. Das RemoteBackend ruft `__enter__()` selbst auf und schließt in `disconnect()`.
- Umlaufzeit RemoteBackend über localhost (Mock, 9 Servos, 200 Wiederholungen): `read_positions` Median 0,13 ms (max 0,37), `read_state` Median 0,33 ms (max 1,1).

## 4. ESP32-Funkknoten: Protokollzusammenfassung

Quelle: ausschließlich `firmware/esp32/` (README, `lib/protokoll/protokoll.h`,
`src/knoten/*.cpp`, `src/gateway/main.cpp`, `werkzeuge/knoten_test.py`). Der Code ist
die Referenz und wird nicht geändert. Vollständig gelesen am 13.11.2026.

### 4.1 Überblick

- Ein ESP32 („Knoten“) hängt am Servo-Bus (UART2, fest **1 Mbit/s**) und ist über **UDP Port 4210** (WLAN) und über ESP-NOW erreichbar. Ein optionaler zweiter ESP32 („Gateway“) übersetzt USB (921 600 Baud, SLIP-Rahmen mit vorangestellter MAC) in ESP-NOW.
- Die Firmware kennt nur Servo-IDs und **rohe Servo-Einheiten**. Kinematik und Kalibrierung bleiben auf dem PC.
- Eine Servo-Task mit **50 Hz** ist der einzige Teil, der den Bus benutzt.
- Profile (auf der Webseite des Knotens gewählt): `lekiwi` (IDs 1–6 Position, 7–9 Tempo, Standard), `so101` (IDs 1–6 Position), `so101_leader` (IDs 1–6, nur lesen, nie Drehmoment). Höchstens 12 Servos.
- Python-Referenz: `werkzeuge/knoten_test.py` (Klassen `Knoten`, `UdpVerbindung`, `GatewayVerbindung`).

### 4.2 Paketformat

Alle Zahlen Little Endian, ohne Füllbytes.

```
Kopf (7 Byte)                                   | Nutzdaten (0…241) | CRC-16 (2)
magic u16 = 0x4B52 | version u8 = 1 | typ u8 | seq u16 | laenge u8
```

CRC-16/CCITT-FALSE (Start 0xFFFF, Polynom 0x1021) über Kopf und Nutzdaten.
Höchstens 250 Byte je Paket. Antworten tragen die `seq` der Anfrage. **Ungültige
Pakete werden stillschweigend verworfen**, es gibt keine Fehlerantwort.

| Typ | Code | Nutzdaten | Antwort |
|---|---|---|---|
| `PING` | 0x01 | keine | `INFO` |
| `BEFEHL` | 0x02 | `anzahl u8`, dann je Servo `id u8, flags u8, wert i16` | `ZUSTAND` |
| `ZUSTAND_ANFRAGE` | 0x03 | keine | `ZUSTAND` |
| `STOPP` | 0x04 | keine | `ZUSTAND` |
| `REG_LESEN` | 0x05 | `id u8, adresse u8, laenge u8` | `REG_ANTWORT` |
| `REG_SCHREIBEN` | 0x06 | `id, adresse, laenge`, dann `laenge` Datenbytes | `REG_ANTWORT` |
| `INFO` | 0x81 | 50 Byte Kopf + je Servo `id u8, modus u8` | |
| `ZUSTAND` | 0x82 | 13 Byte Kopf + je Servo 11 Byte | |
| `REG_ANTWORT` | 0x85 | `id, adresse, ok u8, laenge u8` (+ Daten beim Lesen) | |
| `GW_INFO_ANFRAGE`, `GW_KANAL`, `GW_INFO` | 0x10, 0x11, 0x90 | nur zwischen PC und Gateway | |

- `BEFEHL`: `flags` Bit 0 = Drehmoment. `wert` im Positionsmodus 0…4095 Schritte, im Tempomodus Schritt/s mit Vorzeichen. Nicht genannte Servos behalten ihren letzten Sollwert. IDs außerhalb des Profils werden ignoriert. Stimmt die Länge nicht, wird das Paket verworfen.
- `ZUSTAND`-Kopf (`<HIBBHHB`): `quittung` (seq des zuletzt angenommenen Befehls), `zeit_ms`, `flags` (0x01 Watchdog/sicherer Zustand, 0x02 BELEGT, 0x04 nur lesen, 0x08 WLAN), `quelle` (0 niemand, 1 UDP, 2 ESP-NOW), `zyklus_us`, `busfehler`, `anzahl`.
- `ZUSTAND` je Servo (`<BBhhhBBB`): `id`, `flags` (0x01 online, 0x02 Drehmoment, 0x04 Tempomodus, 0x08 veraltet), `position`, `tempo`, `last` (0,1 %), `spannung` (0,1 V), `temperatur` (°C), `fehler` (Register 65). **Kein Strom.**
- `INFO`-Kopf (`<16s16sBBb4s6sHHB`): Name, Profil, Betrieb, Kanal, RSSI, IP, MAC, Firmware, Watchdog in ms, Anzahl.
- Register: 1 bis 32 Byte je Zugriff. Antwort beim Schreiben `ok=1, laenge=0`. Bei Fehler `ok=0, laenge=0`. Die Servo-Task bearbeitet **einen Registerzugriff je 20-ms-Zyklus**, die Warteschlange fasst 2 Aufträge, ist sie voll, kommt sofort `ok=0`.
- Registerzugriffe gehen an **jede** Servo-ID, nicht nur an die des Profils.

### 4.3 Steuerrecht, Watchdog, STOPP

- Es steuert immer nur eine Quelle. Bei UDP ist die Quelle das Paar aus **IP und Absenderport**. Eine zweite Quelle bekommt `ZUSTAND` mit BELEGT, bis die erste länger als der Watchdog schweigt.
- Ein `BEFEHL` derselben Quelle mit nicht neuerer `seq` wird nicht ausgeführt, aber normal beantwortet. Ob ein Befehl angenommen wurde, zeigt nur `quittung == seq`.
- **Watchdog** (100–2000 ms, Standard 300 ms, auf der Webseite einstellbar): ohne `BEFEHL` gehen die Räder auf Tempo 0 und die Positions-Servos halten die aktuelle Stellung. `ZUSTAND_ANFRAGE` füttert den Watchdog nicht und übernimmt kein Steuerrecht.
- **STOPP** darf jeder, gibt das Steuerrecht frei und führt in denselben sicheren Zustand. **Das Drehmoment bleibt dabei an.** „Drehmoment aus“ ist ein `BEFEHL` mit `flags = 0` für jeden Servo und braucht das Steuerrecht.
- Nach dem Einschalten ist das Drehmoment aus. Die Firmware schreibt beim Finden eines Servos die Betriebsart (Register 33, EPROM) passend zum Profil, falls sie abweicht.
- Grenzen in der Firmware: Position auf 0…4095, Radtempo auf ± `maxTempo` (100–3400, Standard 3000).
- Suche: `INFO` als Broadcast jede Sekunde an UDP-Port 4211, `PING` als Broadcast an 4210, mDNS `<name>.local`.
- Webseite des Knotens: Port 80, im WLAN-Betrieb Basic Auth (`admin` und Setup-Passwort), `/status.json`.

### 4.4 Vorläufige Funktionsmatrix für `Esp32Backend`

| Operation | Über ESP32 | Bemerkung |
|---|---|---|
| Verbinden, Info | ja | `PING` → `INFO` |
| Gelenkzustand lesen | ja | Abbild der 50-Hz-Task: Position, Tempo, Last, Spannung, Temperatur, Status. Strom nur über `REG_LESEN` |
| Sollwerte setzen | ja | roh, muss ständig wiederholt werden (Watchdog) |
| Torque an/aus | ja | über das Flag im `BEFEHL`, nur für Servos des Profils |
| Register lesen/schreiben (einzeln) | ja | höchstens etwa 50 Zugriffe je Sekunde |
| Register synchron für mehrere Motoren | nein | nur als Folge einzelner Zugriffe nachbildbar |
| Bus scannen | eingeschränkt | je ID ein `REG_LESEN`, nur bei 1 Mbit/s. Alle 254 IDs dauern mehrere Sekunden |
| Scan über Baudraten | nein | Bus fest auf 1 Mbit/s |
| Servo-ID ändern | voraussichtlich ja | über `REG_SCHREIBEN` (55, 5, 55). Laut README mit dieser Firmware noch nicht ausprobiert |
| Baudrate ändern | nein | der Servo wäre danach für den ESP32 unerreichbar |
| Basisgeschwindigkeit (LeKiwi) | ja | Profil `lekiwi`, IDs 7–9 |
| Geschwindigkeits- und Beschleunigungsgrenze für Gelenke | nur über Register | die Firmware schreibt für Positions-Servos nur `Goal_Position`. Grenzen (Register 41, 46) muss der Adapter vorher per `REG_SCHREIBEN` setzen |

### 4.5 Widersprüche und Lücken

1. **Teststand:** Laut README sind die Profile `so101` und `so101_leader` „vorbereitet, nicht am Gerät getestet“. Am Gerät getestet sind WLAN-Einrichtung, Datenübertragung und die Räder 7–9. Der Auftrag sagt, der ESP32 könne den Arm bereits ansteuern (Frage 3).
2. **Sprunggefahr:** Ein `BEFEHL` mit Drehmoment-Flag und `wert = 0` fährt ein Gelenk ungebremst auf Rohposition 0. Der Adapter muss immer zuerst die Istposition lesen und nie einen Servo ohne ausdrücklichen Sollwert einschalten (so macht es `wasd_schwenkkopf.py`).
3. **Kein Schutz für Registerzugriffe:** `REG_SCHREIBEN` prüft kein Steuerrecht. Das UDP-Protokoll hat keine Anmeldung. Jeder im selben Netz kann Register schreiben, auch im EPROM.
4. **ID-Vergabe:** im README als ungetestet markiert.
5. **Datenblatt:** `doku/sts3215/` beschreibt die 7,4-V-Variante (C001, 1:345). Für die 12-V-Follower gelten andere Kennwerte.
6. **Baudraten-Tabelle:** siehe 3.5.

---

## 5. Folgen für die Architektur (Vorschläge, noch nicht entschieden)

- **Einheit an der Backend-Schnittstelle: rohe Servo-Schritte.** Das können alle Backends (USB mit `normalize=False`, ESP32 ohnehin). Grad und Prozent rechnet eine gemeinsame Schicht darüber mit dem LeRobot-Kalibrierformat um. Zu bestätigen in Phase 2.
- **`UsbBackend` verbindet ohne LeRobots `configure()`.** `SOFollower.connect()` schreibt bei jedem Verbinden ins EEPROM, `LeKiwi.configure()` schaltet zusätzlich das Drehmoment ein. Für ein Lehr-Repo soll das ein eigener, sichtbarer Schritt sein.
- **Scan und ID-Vergabe über `rustypot.Bus`**, weil `SerialMotorsBus` dafür nur private Methoden hat.
- **Registertabelle zur Laufzeit aus rustypot**, Speicherbereich über die Adresse (unter 40 EPROM). Register ab Adresse 80 gelten als nicht beschreibbar.
- **Nicht auf `FeetechMotorsBus` aufbauen** (veraltet).
- **Datasets immer lokal:** `push_to_hub=false`, `root` unter `recordings/`.
- **`so101-lab doctor`** prüft zusätzlich: Compiler für `evdev`, Gruppenrechte am Port, Busspannung passend zum Gerätetyp (Schwellen aus dem Netzteil-Platzhalter).

### Aufbau der Backend-Schicht (Stand Phase 2)

```
CLI / Skripte / Web-App
        │  Gelenknamen, Grad, Grenzen
   so101_lab.arm.Arm  ── safety.open_arm() (try/finally), clamp, Whitelist
        │  IDs, Registernamen, rohe Schritte
   backends.ArmBackend ── write_eeprom / change_id / change_baudrate (gemeinsam, mit Prüfung)
     ├ UsbBackend   rustypot.Bus (alle IDs 0–253), kein configure() von LeRobot
     ├ MockBackend  simulierte STS3215 (Register, EEPROM-Sperre, Bewegung, Baudrate)
     ├ RemoteBackend  WebSocket → /ws/bus/<gerät> der Web-App auf dem Pi → dort UsbBackend
     └ Esp32Backend (Phase 8)
```

- Registertabelle: `registers.py` erzeugt sie zur Laufzeit aus rustypot; kanonischer Name je Adresse = der zuletzt deklarierte (entspricht LeRobot).
- Gelenktabellen: `devices/so101.py`, `devices/lekiwi.py`.
- Portwahl: `ports.list_serial_ports()` liefert alle USB-Seriell-Ports mit USB-ID, Seriennummer und Text (`label`) für Auswahllisten.

---

## 6. Risiken

| Nr. | Risiko | Umgang |
|---|---|---|
| R1 | LeRobot 0.7.0 ist acht Tage alt und enthält den neuen rustypot-Bus. Fehler in der neuen Schicht sind möglich, die API kann sich weiter ändern | fest pinnen, eigene Schicht kapselt LeRobot, früh an Hardware messen (Phase 2) |
| R2 | Installationsgröße 6,3 GB unter Linux x86_64 (CUDA). Auf dem Raspberry Pi Platz, RAM und Installationsdauer unbekannt | CPU-torch jetzt auch für aarch64 (3.11). Messung auf dem Pi steht aus (Frage 26) |
| R3 | `evdev` braucht unter Linux einen Compiler | in Kapitel 02 und 06 dokumentieren, `doctor` prüft |
| R4 | LeRobot-Werkzeuge schreiben beim Verbinden ins EEPROM und schalten bei LeKiwi das Drehmoment ein | eigener Verbindungsweg, in der Doku erklären |
| R5 | ESP32: kein Schutz für Registerzugriffe, keine Tempogrenze für Gelenke in der Firmware, Sprunggefahr | Schutz im Adapter, deutliche Hinweise in Kapitel 07 |
| R6 | ESP32: Arm-Profile laut README ungetestet | Frage 3, in Phase 8 zuerst mit einem einzelnen Servo testen |
| R7 | Baudraten-Tabelle widersprüchlich | Scan zeigt die Rate an, bei der ein Servo antwortet. Umstellen nur auf 1 Mbit/s |
| R8 | Leader- und Follower-Servos per Software nicht unterscheidbar | Gerätetyp steht in `config/arms.toml`, Plausibilitätsprüfung über die Busspannung |
| R9 | ~~Datenrate 30–50 Hz über USB~~ **gemessen 13.11.2026:** Positionen 1,0 ms, voller Zustand 9 ms (6 Servos, 1 Mbit/s) | erledigt. Web-App liest Positionen jede Runde, den vollen Zustand jede 10. Runde |
| R10 | Offline-Betrieb: LeRobot greift an mehreren Stellen auf den Hugging Face Hub zu | Aufrufe mit lokalen Pfaden kapseln, in Phase 6 ohne Netz testen |
| R11 | Windows und macOS nur über die Auflösung geprüft | Abnahme durch Testpersonen in Phase 1 und 2 |
| R12 | Hersteller-PDFs liegen im Repo | Frage 8, am Ende klären (Repo privat) |
| R13 | Netzbetrieb: WLAN-Aussetzer lösen den Watchdog aus, mit `torque_off` sinkt der Arm ab. Der Watchdog hilft nicht, wenn der Pi selbst hängt | Watchdog einstellbar, `safe_state = "hold"` auf dem Pi wählbar, Hinweis auf Hardware-Schalter in Kapitel 06 |
| R14 | Token im Klartext über HTTP | nur im Labornetz, Hinweis in Kapitel 06 |

---

## 7. Entscheidungslog

| Datum | Entscheidung | Begründung |
|---|---|---|
| 13.11.2026 | Das Arbeitsverzeichnis `/home/student/so101_lab` ist die Wurzel des Repos, Branch `main` | dort lag der Anhang, kein zusätzlicher Unterordner nötig |
| 13.11.2026 | ESP32-Code liegt direkt unter `firmware/esp32/` (ohne den Zwischenordner `esp32_funkknoten/`), Inhalt byteweise unverändert (30 Dateien per SHA-256 gegen das ZIP geprüft). Das ZIP wurde danach gelöscht | Zielstruktur aus dem Auftrag, der Code bleibt Referenz |
| 13.11.2026 | LeRobot wird auf **0.7.0** gepinnt, Extras `feetech`, `lekiwi`, `core-scripts` (**vom Auftraggeber bestätigt**) | neueste stabile Version. 0.6.1 nutzt die Bus-Schicht, die gerade ersetzt wurde |
| 13.11.2026 | Repo-Wurzel bleibt `/home/student/so101_lab` (vom Auftraggeber bestätigt) | |
| 13.11.2026 | Standardmäßig **CPU-torch** (1,2 GB), CUDA als optionaler Extra für Training | Auftraggeber: erst geht es um die einfache Ansteuerung. Geprüft, dass torch nicht komplett entfallen kann (siehe 3.1) |
| 13.11.2026 | USB-Adapter: QinHeng Electronics „USB Single Serial“, **USB-ID 1a86:55d3**, erscheint als `/dev/ttyACM0` (Vorlage für die udev-Regel). Port war zuvor nicht vorhanden, taucht beim Anstecken auf | `lsusb` des Auftraggebers |
| 13.11.2026 | Es wird nicht auf `FeetechMotorsBus` aufgebaut | in 0.7.0 als veraltet markiert |
| 13.11.2026 | torch bleibt Pflicht (Auftraggeber). Umsetzung: `torch`/`torchvision` stehen in den uv-Gruppen `cpu` (Standard) und `cuda` (`uv sync --no-group cpu --group cuda`, Index cu132), mit `conflicts`. `uv sync` mit CPU: Umgebung 2,1 GB inkl. Doku und Dataset-Paketen | `[tool.uv.sources]` wirkt nur auf direkte Abhängigkeiten, Gruppen lösen das |
| 13.11.2026 | Doku offline: `theme.font: false`, `use_directory_urls: false`, `extra.generator: false`; Hinweis: mkdocs zeigt eine MkDocs-2.0-Warnung (abschaltbar mit `NO_MKDOCS_2_WARNING=1`) | keine externen Ressourcen |
| 13.11.2026 | CLI mit argparse statt typer | weniger Abhängigkeiten |
| 13.11.2026 | **Keine udev-Regeln** vorerst (Auftraggeber). `config/99-so101.rules` wieder entfernt. Der Port wird vom Nutzer gewählt: `--port`, Eintrag in `arms.toml` (optional) oder Auswahl aus `so101-lab ports`; in der Web-App (Phase 4) als Dropdown aller USB-/ACM-Geräte | spätere Ergänzung möglich (Seriennummer des Adapters bekannt) |
| 13.11.2026 | Backend-Schnittstelle arbeitet mit **rohen Servo-Einheiten**; Gelenknamen, Kalibrierung und Grenzen liegen in `Arm` | alle Backends (auch ESP32) können das |
| 13.11.2026 | `UsbBackend` direkt auf `rustypot.Bus`, nicht auf `SerialMotorsBus`/LeRobot-Roboterklassen | Scan über IDs/Baudraten nur dort; LeRobot schreibt beim Verbinden ins EEPROM |
| 13.11.2026 | EEPROM nur über `write_eeprom`/`change_id`/`change_baudrate` in der Basisklasse: Drehmoment aus → lock 0 → schreiben → lock 1 → zurücklesen/Ping. `write_register` lehnt EEPROM ab, `torque_enable` nur 0/1 | gleiche, nachvollziehbare Prozedur für alle Backends |
| 13.11.2026 | Baudraten-Werte nach **rustypot** (Widerspruch zu V3.7 bleibt dokumentiert) | rustypot ist der Treiber, der tatsächlich schreibt |
| 13.11.2026 | `safe_state` Standard **torque_off** (wie LeRobot `disable_torque_on_disconnect`), `hold` wählbar; Not-Aus schaltet immer das Drehmoment aus; Räder werden immer zuerst gestoppt | Bestätigung offen (Frage 10) |
| 13.11.2026 | Bewegungs-Standardwerte: 1000 Schritt/s, Beschleunigung 20 (2000 Schritt/s²), größter Sprung 400 Schritt (~35°), Radtempo ≤ 3000 | vorsichtige Lehrwerte, je Gerät in `arms.toml` änderbar |
| 13.11.2026 | Kalibrierdateien im Repo unter `calibration/<robots|teleoperators>/<so_follower|so_leader|lekiwi>/<id>.json` (LeRobot-Format und -Struktur), Fallback auf die LeRobot-Ablage | gleiche Datei für LeRobot über `--robot.calibration_dir` nutzbar |
| 13.11.2026 | Mock-Annahmen: 12,0 V, 25 °C, EEPROM-Schreiben nur ohne Drehmoment, lock nach dem Einschalten 0 (Werkswert V3.7) | nur Simulation, im Code dokumentiert |
| 13.11.2026 | Phase 0 hat nur eine Wegwerf-Umgebung außerhalb des Repos benutzt | „noch kein Projektcode“ |
| 13.11.2026 | **`safe_state` bleibt `torque_off`** (Frage 10 erledigt), `hold` je Gerät in `arms.toml` wählbar. Auftraggeber: „in einen Save State versetzen, aber leicht änderbar halten“. *Auslegung von Claude:* „safe state“ = Drehmoment aus. Stimmt das nicht, genügt `safe_state = "hold"` | Der Name `safe_state` und der Wert gehören schon zusammen, eine Umstellung braucht keine Codeänderung |
| 13.11.2026 | Kalibrierungs-Button in der Web-App mit Anzeige des Pfads der Kalibrierdatei (Auftraggeber) wird in die Web-App aufgenommen. Phase und Umfang: Frage 12 | Wunsch des Auftraggebers |
| 13.11.2026 | **Skript 05 (ID setzen, nur ID, kein Baudratenwechsel) schon in Phase 3.** Phase 5 ergänzt Assistenten, Protokoll und Baudrate | Phase 3 nennt Skripte 01–07, `change_id` samt Prüfung gab es seit Phase 2 |
| 13.11.2026 | Skripte nutzen die Optionsnamen auf Deutsch (`--gelenk`, `--wert`, `--dauer`, `--ohne-warten`), sonst gemeinsame Optionen `--device`, `--port`, `--config` aus `script_support.py` | Zielgruppe deutschsprachig, einheitliche Bedienung |
| 13.11.2026 | Skript 03 bewegt nur **relativ** zur Startposition (±`--delta`, Standard 200 Schritt) und nicht größer als `max_relative_target`. Skript 04 lehnt `torque_enable` und `goal_position` ab (stehen auf der Whitelist, hätten aber wegen `open_arm` keine bleibende Wirkung) und verweist auf Skript 03 | ohne Kalibrierung sind die Grenzen 0…4095. Relative Mini-Bewegung ist ungefährlich |
| 13.11.2026 | Aufnahmeformat: `recordings/trajectories/<name>.csv` (`t_s` plus Rohwerte je Gelenk) und `<name>.json` (Format-Version, Gerät, `calibration_id`, Kalibrierdatei, Soll- und gemessene Rate). Rohwerte, kein Überschreiben ohne `--ueberschreiben` | funktioniert ohne Kalibrierung, exakte Wiedergabe. Phase 6 baut Bibliothek und Recorder darauf auf |
| 13.11.2026 | Abspielen: Anfahrt mit smoothstep, mindestens 2 s, mittleres Tempo höchstens 300 Schritt/s; danach nach Zeitstempeln mit `--speed` 0,1…3, jede Zeile durch `move_to` | sanfte Anfahrt war Vorgabe, Zahlenwerte sind vorsichtige Lehrwerte (Frage 13) |
| 13.11.2026 | Registerbeschreibungen (Einheit, Bedeutung) in `register_info.py` aus V3.7. Tabellen in Kapitel 01 (Gelenke) und 03 (Register) werden per Test (`tests/test_docs.py`) mit dem Code abgeglichen | keine Abschreibfehler |
| 13.11.2026 | Kapitel 02 beschreibt die Kalibrierung mit `lerobot-calibrate` (Ablage im Repo über `--robot.calibration_dir`). Eine eigene Kalibrierung gibt es noch nicht | Phase 6/Frage 12 |
| 13.11.2026 | Ungültige Registerwerte beim Schreiben melden `BackendError` statt `ValueError` | einheitliche deutsche Fehlermeldung in Skripten und Web-App |
| 13.11.2026 | Offene Fragen 3–14 werden auf später verschoben, **außer dem Kalibrierungs-Button: kommt mit dem Web-App-Kern (Phase 4)** (Auftraggeber) | |
| 13.11.2026 | Neue Abhängigkeiten: `fastapi`, `uvicorn` (ohne `[standard]`), `websockets`; `httpx` nur in `dev` (TestClient). Versionen: fastapi 0.162.6, starlette 1.10.0, uvicorn 0.54.0, websockets 17.4. Starlette 1.x: Lifespan statt `on_startup` | schlank, kein Node, alles lokal |
| 13.11.2026 | Web-API: REST für Aktionen (`/api/…`), ein WebSocket `/ws` sendet 20× je Sekunde den Zustand aller Geräte und empfängt Heartbeats. Jeder WebSocket bekommt eine `client_id`; bewegende Aktionen tragen sie im JSON | dieselbe API kann später das `RemoteBackend` (Phase 7) benutzen |
| 13.11.2026 | Steuerrecht je Gerät, nur für Clients mit offenem WebSocket. Übernahme nur mit `force`. Beim Übernehmen werden Räder gestoppt. **Abgabe, Watchdog und Schließen des WebSockets führen zu `safe_shutdown`** (Räder stopp, dann `safe_state`). Not-Aus darf jeder (mit Token), unabhängig vom Steuerrecht, ignoriert `safe_state` | Vorgabe 5.2 und 8 |
| 13.11.2026 | Watchdog Standard **400 ms** (`--watchdog-ms`, 100–2000), Browser-Heartbeat alle 100 ms. Ein Tab im Hintergrund kann wegen Timer-Drosselung den Watchdog auslösen; das gilt als gewollt | Vorgabe 300–500 ms |
| 13.11.2026 | Token aus `--token` oder Umgebungsvariable `SO101_LAB_TOKEN`, **nicht** in `arms.toml` (die Datei liegt im Git). Pflicht, wenn `--host` nicht localhost ist, sonst Start abgelehnt. Seiten (HTML/JS/Doku) ohne Token, alle `/api/…` und `/ws` mit Token (`Authorization: Bearer` oder `?token=`), Vergleich mit `secrets.compare_digest` | Geheimnis nicht im Repo |
| 13.11.2026 | Doku wird beim Start von `so101-lab web` nach `site/` gebaut und unter `/docs/` eingebunden (`--no-docs` schaltet ab, ohne MkDocs läuft die App ohne Doku) | ein Befehl für Labor und Doku |
| 13.11.2026 | Esc-Taste löst im Browser den Not-Aus aus | schneller als die Maus |
| 13.11.2026 | **Kalibrierung als eigene Umsetzung in der Web-App** (Auftraggeber bestätigt Vorschlag aus Frage 12). `calibrate.CalibrationRun`: Start (Drehmoment aus, alte Werte merken) → Mitte (zurücksetzen auf Offset 0 und 0…4095, dann `homing_offset = Roh − 2047`, auf ±2047 begrenzt) → Bereich (Minimum/Maximum aus der laufenden Abfrage) → Speichern (Grenzen ins EEPROM, JSON im LeRobot-Format, alte Datei als `.json.bak`). Rechnung aus `SerialMotorsBus.set_half_turn_homings`/`record_ranges_of_motion` (LeRobot 0.7.0). Volldreher und LeKiwi-Räder: Offset 0 bzw. Homing wie LeRobot, Bereich 0…4095 | gleiche Dateien wie `lerobot-calibrate`, aber ohne dessen zusätzliche EEPROM-Schreibzugriffe |
| 13.11.2026 | Kalibrierung wird bei Abbrechen, Abgabe des Steuerrechts, Watchdog, Not-Aus und Trennen **zurückgerollt** (alte EEPROM-Werte zurückschreiben). EEPROM-Werte werden nur geschrieben, wenn sie sich ändern. Während der Kalibrierung sind Drehmoment an und Bewegungen gesperrt | Servo und Kalibrierdatei passen immer zusammen |
| 13.11.2026 | Register-Explorer: Lesen für alle; Schreiben mit Steuerrecht. RAM nur Whitelist (`acceleration`, `goal_velocity`, `torque_limit`); `torque_enable`/`goal_position` nur über Drehmoment-Knöpfe bzw. Schieberegler; EEPROM nur im Expertenmodus mit Rückfrage, über `write_eeprom`. Gesperrt: `id`, `baud_rate` (Phase 5), `homing_offset`, `min/max_position_limit` (Kalibrierung), Register ab Adresse 80 | Vorgabe 6.4, Kalibrierung nicht unterlaufen |
| 13.11.2026 | Einzelservo: Schieberegler innerhalb der Gelenkgrenzen, Sollwerte höchstens alle 60 ms, jeder über `move_to` (Grenzen, größter Sprung 400). Tempo/Beschleunigung gelten für alle Gelenke des Geräts | einfache, sichere Bedienung |
| 13.11.2026 | Doku-Schaltflächen `<a class="lab-link">` mit `docs/javascripts/lab.js`: aktiv nur, wenn `/api/info` antwortet (Doku über `so101-lab web`), sonst deaktiviert mit Hinweis | Vorgabe 5.3 |
| 13.11.2026 | Mock-Korrektur: Ziel und Grenzen in Koordinaten mit Homing-Offset | vorher falsche Simulation mit Kalibrierung |
| 13.11.2026 | **Keine blockierenden Browser-Dialoge** (`confirm`, `alert`, `prompt`) im Frontend, stattdessen `<dialog>`. Fehler vom Auftraggeber gefunden: Rückfrage vor „Kalibrierung starten“ hielt das Lebenszeichen an, der Watchdog entzog nach ~420 ms das Steuerrecht. Ein Test prüft `app.js` darauf | blockierende Dialoge stoppen alles JavaScript im Tab |
| 14.11.2026 | **ID-Vergabe (Phase 5) in `servo_setup.ServoSetup`**, gemeinsam für Web-App und Skript 05: Suche über alle Baudraten (1 Mbit/s zuerst) → genau ein Servo, bekanntes Modell → **Probe: 10× `present_position` lesen** (zwei Servos mit gleicher ID liefern kaputte Antworten; eine Suche bemerkt das nicht immer) → `change_id` bei der alten Baudrate, dann `change_baudrate` auf 1 Mbit/s (Reihenfolge wie LeRobot `setup_motor`) → Prüfung: `id`/`baud_rate` zurücklesen, Suche bei 1 Mbit/s findet nur die neue ID. Zielbaudrate immer 1 Mbit/s (R7) | ein Ablauf, eine Prüfung für beide Wege |
| 14.11.2026 | `UsbBackend` schreibt ID und Baudrate über **rustypot `Bus.change_id`/`change_baudrate`** (Einhängestellen `_write_id`/`_write_baudrate` in `ArmBackend`), Mock weiter über rohe Registerzugriffe. Danach wie bisher `lock` = 1 und Ping | der Weg, den LeRobot 0.7.0 benutzt. LeRobot: „A motor answers the id and baud rate writes before they take effect“, die Quittung kommt also unter der alten ID |
| 14.11.2026 | **Neustart-Probe** als eigener, empfohlener Schritt: nach Aus-/Einschalten suchen, ID 1 Mbit/s und neue ID müssen stimmen. In der Simulation wird `power_cycle()` nachgebildet | nur das zeigt, dass die Werte im EEPROM stehen (lock-Semantik, 3.5) |
| 14.11.2026 | **Protokoll** `logs/servo_ids.csv` (im Repo, nicht ignoriert): eine Zeile je Aktion (`id_gesetzt`, `unveraendert`, `neustart`), auch bei Fehlern, mit Zeit, Gerätetyp, Gelenk, alter/neuer ID und Baudrate, Modell, Firmware, Port, Adapter-SN, Meldung. Fehlende Vorbedingungen (kein/mehrere Servos) werden nicht protokolliert, weil nichts geschrieben wurde | Nachweis je Servo, Inventar im Labor |
| 14.11.2026 | Ein Servo, der in diesem Durchgang schon eingerichtet wurde, wird **nicht auf ein anderes Gelenk umgestellt** (`SetupError`, in der Oberfläche gesperrt). Gefunden beim Durchklicken: ohne Tausch neu suchen schlug sonst „ID 6 → 5“ vor | häufiger Bedienfehler, sonst Servo falsch beschriftet |
| 14.11.2026 | Web-App: Assistent ist eine eigene Sitzung (`web/setup_session.py`), **nicht** an ein Gerät aus `arms.toml` gebunden (bei der ID-Vergabe gibt es noch kein Gerät). Höchstens einer gleichzeitig, gehört dem startenden Tab (`client_id`), endet mit dessen WebSocket. Port gehört entweder einer Geräteverbindung oder dem Assistenten. Eine Aktion zur Zeit, eine zweite wird abgelehnt. Kein Watchdog nötig: Der Assistent schaltet nie das Drehmoment ein. API `/api/setup/…` (`types`, `start`, `scan`, `assign`, `verify`, `simulate`, `stop`), Zustand im WebSocket unter `setup` | sauber getrennt von Steuerrecht und Kalibrierung |
| 14.11.2026 | Port `mock` im Assistenten = Simulation mit Varianten „neu“, „baudrate“ (115 200) und „doppelt“ (zwei Servos mit ID 1) | ohne Hardware üben, auch den Fehlerfall |
| 14.11.2026 | **Modus „freie ID“** (Auftraggeber): Ziel ist eine beliebige ID 0–253 statt eines Gelenks, für einzelne Servos (Ersatz, eigene Aufbauten). Gleicher Ablauf (`ServoSetup.target(joint, servo_id)` → `Target`), Web-App: Auswahl „Ziel“ in Schritt 3, API `assign`/`verify` mit `id` statt `joint`, Skript 05 `--id`. Gehört die ID zu einem Gelenk des Gerätetyps, zählt sie als dieses Gelenk; sonst bleibt `gelenk` im Protokoll leer | Wunsch des Auftraggebers, ohne zweiten Schreibweg |
| 14.11.2026 | Skript 05 sucht jetzt immer über alle Baudraten, stellt auch die Baudrate um und schreibt ins Protokoll | gleiche Logik wie die Web-App |
| 14.11.2026 | **Phase 6 geteilt** (Auftraggeber): 6a ohne Kamera zuerst, 6b (Kamera, Dataset) später. Aufnahmequellen: **beides** (von Hand und Teleoperation Leader → Follower) | keine Kamera am Rechner, Recorder ist ohne Kamera nützlich |
| 14.11.2026 | Recorder, Abspielen und Teleoperation laufen **im Takt der Zustandsabfrage** (30 Hz) der Geräte-Sitzung (`DeviceSession._motion_tick`), Rechnung in `motion.py` ohne Bus und Threads. Kein zweiter Thread am Bus | Bus-Schloss bleibt einfach, Tests mit erfundenen Zeiten |
| 14.11.2026 | **Umrechnung zwischen Armen über Grad/Prozent** (`motion.Converter`, wie LeRobot-Teleoperation): Roh → Gelenkwert mit Quell-Kalibrierung → Roh mit Ziel-Kalibrierung. Aufnahmen speichern dafür die Kalibrierwerte im JSON (`calibration`). Ohne Kalibrierung: Rohwerte 1:1 (Hinweis), Teleoperation verlangt beide Kalibrierungen. Ältere Aufnahmen ohne `calibration` gelten als gleich kalibriert, wenn `calibration_id` und Gerätetyp gleich sind. LeKiwi `arm_*` wird auf SO-101-Namen abgebildet | vorher wurden Leader-Aufnahmen auf dem Follower mit Rohwerten abgespielt, das ergibt eine verschobene Bewegung (Kapitel 04 dazu korrigiert) |
| 14.11.2026 | Abspielen: Anfahrt wie Skript 07 (smoothstep, ≥ 2 s, ≤ 300 Schritt/s), danach **lineare Interpolation** zwischen Abtastungen, Tempo 0,1–3. Am Ende hält der Arm die Endstellung (Drehmoment an). Stoppen = Stellung halten. Teleoperation: erst dieselbe Anfahrt zur Leader-Stellung, Leader-Werte älter als 0,3 s → anhalten und halten | sanfte Übergänge, kein Sprung |
| 14.11.2026 | Aufnahme von Hand schaltet das Drehmoment des Geräts aus; während Teleoperation bleibt es an (aufgenommen wird der folgende Arm). Verlust des Steuerrechts **speichert** eine laufende Aufnahme bis dahin (`interrupted` im JSON), höchstens 10 min | eine lange Vorführung soll nicht verloren gehen |
| 14.11.2026 | Während Abspielen/Teleoperation sind Schieberegler, Kalibrierung, Register-Schreiben und ein zweiter Ablauf gesperrt; „Drehmoment aus“ und Not-Aus beenden sie | eine Quelle für Sollwerte |
| 14.11.2026 | Bibliothek-API `/api/trajectories` (Liste, Details mit ≤ 400 Punkten, CSV, Löschen). Namen werden mit `trajectories.check_name` geprüft (nur `[A-Za-z0-9_-]`), kein Pfad aus dem Browser | Pfad-Traversal ausgeschlossen |
| 14.11.2026 | **Fehler beim Abspielen (Auftraggeber, am Arm):** Aufnahmen ließen sich teils nicht abspielen („außerhalb der Gelenkgrenzen“). Ursache aus den echten Daten: `test2` beginnt in der Ruhestellung mit `elbow_flex` = 3069, kalibriert ist 856…3068. Die Ruhestellung am Anschlag liegt oft ein paar Schritte jenseits des beim Kalibrieren abgefahrenen Bereichs. Die Startposen-Prüfung lehnte hart ab. **Jetzt:** Werte außerhalb werden auf die Grenzen begrenzt (wie bei jedem `move_to`) und als Hinweis gemeldet, abgelehnt wird nur bei Startpose > 400 Schritt außerhalb (`motion.START_TOLERANCE`) | `move_to` begrenzt ohnehin, die Servos haben die Grenzen auch im EEPROM |
| 14.11.2026 | Aufnahmen speichern die **Homing-Offsets** der Servos (`homing_offsets` im JSON, Web und Skript 06). Abspielen liest die aktuellen Offsets und verschiebt die Rohwerte um die Differenz (`Roh = Stellung − Offset`), außer es wird ohnehin über beide Kalibrierungen umgerechnet. Ohne Kalibrierdatei: Abspielen geht mit Warnung (Grenzen 0…4095) | eine Neukalibrierung zwischen Aufnahme und Abspielen verschob sonst die Bewegung still |
| 14.11.2026 | Vor dem Abspielen fragt die Web-App `GET …/playback/check` (bewegt nichts) und zeigt Anfahrt, Dauer, Umrechnung und Hinweise in der Rückfrage. Warnfeld im Reiter „Aufnahmen“, wenn das Gerät keine Kalibrierdatei hat | Auftraggeber: Warnung statt Fehler |
| 14.11.2026 | **Reiter „Kalibrierdateien“** (Auftraggeber): Liste (Repo und LeRobot-Ablage, „benutzt von“ laut `arms.toml`, Inhaltsprüfung), Ansehen, JSON-Download, Löschen = Umbenennen in `.json.bak`, Wiederherstellen = Tauschen, Import (geprüft, Ersetzen nur ausdrücklich), Kopie aus LeRobot-Ablage ins Repo (LeRobot-Ablage sonst nur lesen), Abgleich Datei ↔ EEPROM (`homing_offset`, `min/max_position_limit`) und „Datei in die Servos schreiben“ (nur geänderte Werte, `write_eeprom`, Steuerrecht nötig). Verbundene Geräte laden nach jeder Dateiänderung neu. Pfadteile aus dem Browser werden gegen feste Listen bzw. `[A-Za-z0-9_.-]` geprüft | Kalibrierungen sichtbar und umkehrbar verwalten |
| 14.11.2026 | `calibration/**/*.json.bak` in `.gitignore` | Backups sind ein lokales Sicherheitsnetz, kein Repo-Inhalt |
| 14.11.2026 | **Kameras** gehören zu einem Gerät (`cameras = [...]` in `arms.toml`, geprüft in `cameras.parse_cameras`), Quelle Pfad/Nummer/`test`. Eigene Lese-Threads mit OpenCV (nicht LeRobots `OpenCVCamera`, um Rate und Fehler selbst anzuzeigen), neuestes Bild ohne Warteschlange (`CAP_PROP_BUFFERSIZE` 1). Geöffnet beim Verbinden; Fehler = Warnung, Gerät verbindet trotzdem. Vorschau als MJPEG-Strom (≤ 10 Bilder/s), Einzelbild `frame.jpg`. Suche `so101-lab cameras` bzw. `/api/cameras` schlägt by-id-Pfade vor | Kamera ist ein Teil des Aufbaus; feste Namen überleben Umstecken |
| 14.11.2026 | **LeRobot-Dataset v3.0** in `lab_dataset.LabDataset`: Merkmale wie LeRobots `SOFollower` (`<gelenk>.pos` in Grad/Prozent, `observation.images.<name>`), `robot_type` = LeRobot-Name, `repo_id` `lab/<name>`, `root` `recordings/datasets/<name>`, nie Hub. Gleicher Name = weiterschreiben (fps und Merkmale müssen passen). Eigener Thread mit fester Rate, liest nur letzte Positionen und neuestes Bild (≤ 0,5 s alt, sonst Zeitschritt ausgelassen und gezählt). Aktion: Leader (Teleoperation), Abspiel-Ziel oder Beobachtung. Ohne Kalibrierung abgelehnt. Steuerrecht verloren / Not-Aus = Episode **verworfen** (anders als Trajektorien) | Format exakt wie `lerobot-record`, damit `lerobot-train` es liest; abgebrochene Vorführungen sind schlechte Trainingsdaten |
| 14.11.2026 | `arms.toml`: `follower` bekommt die Webcam (by-id), `demo` eine Testbild-Kamera | sofort nutzbar, auch ohne Hardware |
| 14.11.2026 | LeKiwi im Dataset: nur die Arm-Gelenke (`arm_*.pos`), keine Basisgeschwindigkeiten (`x.vel` …) wie bei LeRobots `LeKiwi` | Basis folgt in Phase 9 |
| 15.11.2026 | **Frage 7 (Auftraggeber):** an der offiziellen LeKiwi-Anleitung orientieren, weil sie mit dem Pi nachweislich funktioniert. Also **vollständiges LeRobot samt torch auf dem Pi** (wie dort), Raspberry Pi OS 64 bit über den Imager, SSH, Host auf dem Pi, Client am Laptop. Statt conda/pip: `uv sync` mit dem Lockfile (gleiche Versionen wie am Laptop). Kartengröße/RAM: Empfehlung ≥ 16 GB (besser 32 GB), noch ungemessen | bewährter Weg, eine Installationsart für alle Rechner |
| 15.11.2026 | CPU-torch-Index gilt für **alle Linux-Plattformen** (vorher nur x86_64), CUDA-Gruppe bleibt x86_64 | sonst holt `uv sync` auf dem Pi mehrere GB CUDA-Pakete (3.11) |
| 15.11.2026 | **Frage 8 (Auftraggeber):** Feetech-PDFs bleiben vorerst im Repo, Klärung am Ende. Repo ist privat | |
| 15.11.2026 | **Frage 17 (Auftraggeber):** Die neun Servos sind ein LeKiwi. Drei Gerätetypen für dieselbe Hardware: `lekiwi` (1–9), `lekiwi_arm` (1–6), `lekiwi_base` (7–9). Alle mit `lerobot_name = "lekiwi"`, also **eine** Kalibrierdatei. Kalibrieren eines Teils übernimmt die Einträge der anderen Motoren aus der vorhandenen Datei (Reihenfolge bleibt), damit LeRobot die Datei weiter für den ganzen LeKiwi lesen kann. Der Kalibrierordner `robots/lekiwi` wird gegen `lekiwi` (alle neun) geprüft | Wunsch: Arm allein, Basis allein, beides zusammen |
| 15.11.2026 | `calibration/robots/lekiwi/lekiwi_1.json` **abgeleitet**: Arm-Einträge aus `follower_1.json` (am 14.11. an genau diesen Servos gemessen, Adapter SN 5AAF263183) mit Präfix `arm_`, Räder wie LeRobot (`homing_offset` 0, 0…4095). Einträge `lekiwi`, `lekiwi_arm`, `lekiwi_base` in `arms.toml` (USB, Port wählbar). `follower` bleibt unverändert | Die EEPROM-Werte der Arm-Servos stammen aus dieser Kalibrierung. Räder am Gerät mit „Abgleich“ prüfen (Frage 27) |
| 15.11.2026 | **RemoteBackend über die Web-App des Pi**, kein zweites Programm: Endpunkt `WS /ws/bus/<gerät>` in `so101-lab web`, Gerät aus der `arms.toml` des Pi (`usb` mit festem `port`, oder `mock`). Über das Netz nur IDs, Registernamen, Rohwerte; Kalibrierung und Grenzen bleiben am Laptop (`Arm`). JSON-Anfrage/Antwort, immer nur eine unterwegs. **Weitergereicht werden nur die öffentlichen, geprüften Backend-Methoden**, nicht die Rohzugriffe: `write_eeprom`, `change_id`, `change_baudrate`, `read_state` laufen am Stück auf dem Pi | wie LeRobots Host/Client, aber mit der vorhandenen Schicht; ein WLAN-Abbruch lässt keine EEPROM-Sperre offen; Prüfungen doppelt (Laptop und Pi) |
| 15.11.2026 | Sicherheit der Bus-Freigabe: Lebenszeichen alle 100 ms vom Client, **Watchdog auf dem Pi 500 ms** (`--bus-watchdog-ms`, wie LeRobot), Zeit während einer laufenden Anfrage zählt nicht. Watchdog oder Verbindungsabbruch → Räder stopp + `safe_state` **aus der arms.toml des Pi**. Ordentliches Ende (`close`) ändert nichts an den Servos (`open_arm` hat schon zurückgesetzt, und `so101-lab state` soll nur lesen). Not-Aus auf dem Pi → Not-Aus der Freigabe, Laptop wird getrennt. Je Gerät/Port nur ein Nutzer: Browser-Verbindung, Assistent oder ein Laptop. Laptop-Typ muss eine Teilmenge der IDs des Pi-Geräts haben | Pi schützt sich selbst, unabhängig vom Laptop |
| 15.11.2026 | Token am Laptop standardmäßig aus `SO101_REMOTE_TOKEN` (`token_env` änderbar), auf dem Pi wie bisher `SO101_LAB_TOKEN` | gleicher Name würde auch die Web-App am Laptop absichern |
| 15.11.2026 | Dienst: `so101-lab service` gibt die systemd-Unit aus (Benutzer, Repo-Pfad, uv-Pfad, `--host 0.0.0.0 --no-docs`, `SupplementaryGroups=dialout video`, Token aus `/etc/so101-lab.env` mit Rechten 600), Installation von Hand per `tee`. SIGTERM getestet: verbundener Client nach 0,1 s getrennt, Gerät sicher zurückgesetzt, Prozess beendet | weniger Abtippfehler als eine Vorlage mit Platzhaltern, sichtbar, was installiert wird |
| 15.11.2026 | Browser auf dem Pi zeigt „wird über das Netz benutzt (von <IP>)“ und sperrt „Verbinden“ (`remote` im Zustand von `/ws` und in `/api/devices`). In headless Chrome geprüft | sonst unklar, warum das Verbinden scheitert |
| 15.11.2026 | **Fehler behoben (Test, der manchmal fehlschlug):** `test_setup_belongs_to_one_tab_and_ends_with_it` schlug in 3 von 15 Läufen fehl. Ursache: Wird der `/ws`-Handler abgebrochen, schluckte `suppress(CancelledError)` den Abbruch, das folgende `await to_thread(client_gone)` wurde dann gleich wieder abgebrochen, das Aufräumen fiel aus. Jetzt startet das Aufräumen als eigene Aufgabe vor jedem `await` und wird mit `shield` abgewartet (ebenso bei `/ws/bus`). Danach 0 von 20 | dasselbe hätte bei einem Server-Ende das Steuerrecht nicht freigegeben |
| 15.11.2026 | API-Referenz im Anhang, ein Test prüft, dass jede Route der App dort steht und umgekehrt | wie bei Gelenk- und Registertabellen |
| 15.11.2026 | **Feetech-Originale aus dem Repo genommen** (Auftraggeber möchte das Repo veröffentlichen). Prüfung: In den PDFs und der XLS steht kein Lizenz- oder Weitergabevermerk (Metadaten nur „FEETECH“ bzw. ein Kürzel), Seeed, Waveshare und Händler nennen keine Bedingungen. Ohne Erlaubnis liegt das Urheberrecht bei Feetech, eine Weitergabe ist nicht abgedeckt. Dateien liegen lokal in `firmware/esp32/doku/sts3215/originale/` (`.gitignore`), das README dort nennt Quelle, Link und Abrufdatum. Verweise in `register_info.py`, Kapitel 03 und `firmware/esp32/README.md` angepasst. Ausnahme von „firmware/esp32 byteweise unverändert“: nur Doku-Dateien, kein Code | Historie siehe Frage 8 |
| 15.11.2026 | Kapitel 06 erweitert (Auftraggeber): WLAN-Grundlagen (gleiches Netz, mDNS `<hostname>.local`, Ports 22 und 8000, Client-Isolation), Erklärtext und zwei Diagramme zu den Betriebsarten, Pi per LAN-Kabel ins WLAN bringen (Router-Liste oder `raspberrypi.local`, `nmcli --ask device wifi connect`, Alternativen `nmtui`/`raspi-config`, WLAN-Land), VS Code Remote-SSH (Verbinden, Drag & Drop, Port-Weiterleitung ohne Token, SSH-Schlüssel), Kalibrierdateien übertragen, Klonen eines privaten Repos mit Lese-Token. Diagramme als SVG unter `docs/images/` (offline, im Browser gerendert und angesehen) | Wunsch des Auftraggebers |
| 15.11.2026 | **Kalibrierdateien Laptop → Pi:** empfohlen über die Web-App (Laptop: „JSON“ herunterladen, Pi-Web-App: „Importieren“). Der Weg war schon da, geprüft am 15.11. gegen eine zweite Web-App mit leerem Repo-Ordner: Import ohne Beanstandung, Gerät verbindet danach kalibriert. Alternativen git (`push`/`pull`) und VS Code Drag & Drop (ohne Prüfung) | Web-App prüft die Datei und ersetzt nur mit Haken |
| 13.11.2026 | Remote `origin` = `https://github.com/arben24/so101_lab.git`, Branch `main` verfolgt `origin/main`. Gepusht wird vom Auftraggeber selbst. Kalibrierdatei `calibration/robots/so_follower/follower_1.json` liegt im Repo | Vorgabe: Kalibrierdateien ins Repo exportierbar |

---

## 8. Offene Fragen

1. ~~LeRobot-Version~~ → 0.7.0 (entschieden).
2. ~~Repo-Wurzel~~ → bleibt (entschieden).
3. **ESP32-Teststand:** Wurde ein SO-101-Arm (Profil `so101`) schon über den ESP32 bewegt? Das README sagt nein.
4. **ESP32-Transport:** Reicht für `Esp32Backend` zunächst UDP über WLAN? Das Gateway (ESP-NOW über USB) ist laut README ungetestet. Vorschlag: UDP zuerst, Gateway als spätere Ergänzung.
5. ~~USB-Adapter~~ → 1a86:55d3 (siehe Entscheidungslog). Ports werden vom Nutzer gewählt (keine udev-Regeln). Bei zwei Armen hilft die Seriennummer in der Portliste beim Unterscheiden (dieser Adapter: SN 5B90103144).
6. ~~torch-Variante~~ → CPU als Standard, CUDA optional (entschieden).
7. ~~Raspberry Pi~~ → wie die offizielle LeKiwi-Anleitung: vollständiges LeRobot samt torch auf dem Pi (Auftraggeber, siehe Entscheidungslog). Speicherkarte/RAM: Messung offen (Frage 26).
8. **Veröffentlichung:** Die Feetech-Originale (2 PDF, 1 XLS) sind seit 15.11.2026 **nicht mehr im Arbeitsstand** (lokal unter `firmware/esp32/doku/sts3215/originale/`, von git ignoriert, Links im README daneben). **Sie stecken aber noch in der Git-Historie** (Commit `d26d830` und alle späteren Stände bis zum Entfernen). Wird das Repo öffentlich, sind sie über alte Commits abrufbar. Entweder privat lassen (Pi klont mit Lese-Token, Kapitel 06) oder Historie umschreiben (`git filter-repo --path firmware/esp32/doku/sts3215/<datei> --invert-paths`, danach Force-Push, alle Klone neu). Entscheidung Auftraggeber.
9. **Netzteil- und Motorvarianten:** Platzhalter in Kapitel 01, füllt der Auftraggeber aus (Phase 3).
10. ~~safe_state~~ → bleibt `torque_off`, per `arms.toml` umstellbar (siehe Entscheidungslog, Auslegung bitte bestätigen).
11. ~~Hardware-Abnahme Phase 2~~ → teilweise erledigt: alle sechs Servos antworten (Stand-Zeile). Bewegen am echten Arm steht noch aus.
12. ~~Kalibrierung in der Web-App~~ → umgesetzt in 4b als eigene Kalibrierung (Auftraggeber). Ursprüngliche Abwägung: Auftraggeber wünscht einen Kalibrierungs-Button mit Anzeige des Dateipfads. Vorschlag von Claude: eine **eigene** Kalibrierung (Mitte → `homing_offset`, Bereich → `range_min/max`, Schreiben über `write_eeprom`, Datei im LeRobot-Format) schon **in Phase 4/5** statt Phase 6 bauen, weil ohne Kalibrierung die Gelenkgrenzen 0…4095 sind und Skripte nur klein und relativ bewegen. Alternative: Web-App ruft `lerobot-calibrate` als Unterprozess auf (der Dialog läuft über `input()`, daher unhandlich, und schreibt beim Verbinden mehr ins EEPROM). Entscheidung offen.
13. **Lernwerte der Bewegung prüfen:** Anfahrt ≥ 2 s und ≤ 300 Schritt/s (≈ 26°/s), Standard `--delta` 200 Schritt (≈ 18°), Aufnahme 30 Hz. Sind sie für die Hardware passend (Follower mit 12 V)? Erst nach den Hardware-Tests zu beantworten.
14. **Übung 3 (P-Anteil ändern)** braucht ein EEPROM-Schreiben (`p_coefficient`, Adresse 21). Soll dafür ein eigenes, klar gekennzeichnetes Skript kommen (`write_eeprom` mit Rückfrage), oder wird in der Übung `write_eeprom` direkt im Code aufgerufen? Vorschlag: in Phase 10 entscheiden.
15. **Register ab Adresse 80 am echten Servo:** Ob sie lesbar sind oder ins Timeout laufen (dann dauert „Alle Register lesen“ länger), ist ungeprüft (Adapter war bei 4b nicht angesteckt).
16. **ROS im `PYTHONPATH`** (Rechner `/home/nico`): Soll `so101-lab doctor` davor warnen (fremde `site-packages` im `PYTHONPATH`) und Kapitel 10 die Abhilfe nennen? Vorschlag: ja, in Phase 10. Studierende mit ROS-Installation laufen sonst in denselben Fehler.
17. ~~Neun Servos am Adapter SN 5AAF263183~~ → LeKiwi mit genau 9 Servos (6 Arm + 3 Räder), bestätigt vom Auftraggeber am 15.11.2026. Typen `lekiwi`, `lekiwi_arm`, `lekiwi_base` angelegt.
18. **Abnahme Phase 5 am echten Servo:** einen einzelnen Servo anschließen und im Reiter „Einrichtung“ ID schreiben, prüfen, Neustart-Probe. Erst dann ist der rustypot-Weg (`change_id`/`change_baudrate`) an der Hardware bestätigt.
19. **Kamera für 6b:** Welche Kameras gibt es im Labor (USB-Webcam, Handgelenkkamera wie bei LeKiwi)? Am Rechner `/home/nico` ist keine angeschlossen (`/dev/video*` fehlt).
20. ~~Abnahme 6a am echten Arm~~ → Aufnahme und Abspielen vom Auftraggeber geprüft (14.11.2026), Fehler mit Grenzen behoben; nach der Korrektur erneut prüfen. Teleoperation mit zweitem Adapter noch offen.
21. **Volldreher `wrist_roll` mit Homing-Offset:** Bei Offset 1329 (follower_1) liegt die gemessene Stellung von `wrist_roll` bei `Stellung − 1329`. Ob der Servo dabei negative Rohwerte meldet oder bei 0/4095 umbricht, ist am echten Servo ungeprüft (Handgelenk weit drehen, im Status-Reiter Rohwert ansehen). Davon hängt ab, ob die Grenzen 0…4095 für Volldreher stimmen.
22. ~~Kamera für 6b~~ (Frage 19) → Webcam am Laborrechner; welche Kameras im Labor dauerhaft genutzt werden, ist noch offen, daher frei konfigurierbar.
23. **Kamera bei Licht prüfen:** 30 Bilder/s bei 640×480 MJPG erwartet; gemessen nur bei Dunkelheit (10 Bilder/s). Danach eine Dataset-Episode am echten Arm (von Hand und mit Teleoperation) aufnehmen und mit `lerobot-dataset-viz` ansehen.
24. **`torchcodec`:** Soll das Dekodieren repariert werden (FFmpeg-Bibliotheken), bevor trainiert wird (Übung 7)?
25. **Kameras am Pi in Betriebsart B:** Bilder gehen noch nicht über das RemoteBackend (nur Bus). Vorschlag: in Phase 9 (LeKiwi) einen Bildstrom vom Pi ergänzen (die Web-App hat schon `frame.jpg` und MJPEG je Gerät), damit Datasets mit LeKiwi-Kameras am Laptop entstehen können. Bis dahin Betriebsart A.
26. **Abnahme Phase 7 an einem echten Pi:** Welches Modell und wie viel RAM? Zu messen: Dauer und Größe von `uv sync` (aarch64, CPU-torch), RAM im Betrieb, Umlaufzeit und Aussetzer im Labor-WLAN, ob 30 Hz Abfrage am Laptop über das Netz halten, Verhalten bei WLAN-Abbruch (Watchdog). Ob `evdev` auf dem Pi mit `build-essential` baut.
27. **LeKiwi-Kalibrierung prüfen:** `lekiwi_1.json` ist aus `follower_1.json` abgeleitet. Am Gerät `lekiwi` im Reiter „Kalibrierdateien“ den Abgleich mit den Servos ansehen. Die Räder (IDs 7–9) haben vermutlich noch ihre eigenen EEPROM-Werte. Bei Abweichung „Datei in die Servos schreiben“ oder neu kalibrieren.
28. **Eintrag `follower` in `arms.toml`:** Er zeigt ebenfalls auf den LeKiwi-Arm (dort wurde `follower_1` kalibriert). Gibt es zusätzlich einen eigenständigen SO-101-Follower im Labor, oder soll `follower` entfallen, sobald `lekiwi_arm` am Arm geprüft ist?
