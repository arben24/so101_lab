# Software-Architektur des Repos

Dieses Kapitel zeigt, wie `src/so101_lab/` aufgebaut ist und **warum**. Es hilft beim Lesen des Codes, bei den
Übungen und beim Erweitern (neues Gerät, neues Backend).

## Die Schichten

```text
Skripte (scripts/), Übungen (exercises/), CLI so101-lab, Web-App
        │   Gelenknamen, Grad/Prozent, Grenzen, Fahrbefehl (x, y, θ)
        ▼
   arm.Arm  ── Kalibrierung, Gelenkgrenzen, Sprungbegrenzung, sicheres Beenden
   kinematics (nur LeKiwi-Basis)   safety.open_arm (try/finally)
        │   Motor-IDs, Registernamen, **rohe Servo-Schritte**
        ▼
   backends.ArmBackend  ── gemeinsame Prüfungen: EEPROM-Weg, Whitelist, Bereiche
     ├ UsbBackend     rustypot.Bus, Adapter am eigenen Rechner
     ├ MockBackend    simulierte STS3215, keine Hardware
     ├ RemoteBackend  WebSocket → Web-App auf dem Pi → dort ein UsbBackend      (Kapitel 06)
     └ Esp32Backend   UDP → Sollwerttabelle eines ESP32-Knotens                (Kapitel 07)
```

Die wichtigste Entscheidung: **Die Backend-Schnittstelle arbeitet mit rohen Servo-Schritten.** Das können alle
vier Backends, auch der ESP32. Gelenknamen, Grad, Prozent, Kalibrierung und Grenzen liegen einmal darüber in
`Arm`. So gelten dieselben Sicherheitsregeln für jeden Weg zum Servo.

## Die Module

| Modul | Aufgabe |
|---|---|
| `devices/` | Gelenktabellen: welcher Motor hat welche ID, Betriebsart und Normierung (`so101.py`, `lekiwi.py`, `spec.py`) |
| `config.py`, `paths.py` | `config/arms.toml` lesen, wichtige Ordner |
| `registers.py`, `register_info.py` | Steuertabelle des STS3215 (zur Laufzeit aus `rustypot`), Einheit und Bedeutung je Register |
| `backends/` | die vier Backends und ihre gemeinsame Basis `base.py` |
| `arm.py` | `Arm`: ein Gerät über einem beliebigen Backend |
| `safety.py` | Begrenzen, Whitelist, `open_arm` (sicheres Beenden), SIGTERM |
| `calibration.py`, `calibrate.py`, `calibration_files.py` | Kalibrierdateien im LeRobot-Format, geführte Kalibrierung, Dateiverwaltung |
| `servo_setup.py` | ID-Vergabe mit Prüfung und Protokoll |
| `motion.py`, `trajectories.py` | Aufnehmen, Abspielen, Teleoperation, Umrechnen zwischen Armen, Dateiformat |
| `kinematics.py` | LeKiwi-Basis: Fahrbefehl ↔ Radtempo |
| `cameras.py`, `lab_dataset.py` | Kameras, LeRobot-Datasets |
| `web/` | FastAPI-App: `session.py` (eine Verbindung), `app.py` (REST und WebSocket), `bus_server.py`, `setup_session.py` |
| `esp32_sim.py`, `backends/esp32_protocol.py` | der nachgebaute ESP32-Knoten und das Funkprotokoll |
| `cli.py`, `doctor.py`, `service.py`, `script_support.py` | Kommandozeile, Umgebungsprüfung, systemd-Unit, Hilfen für die Skripte |

## Sicherheitsschichten

Von außen nach innen. Jede Schicht fängt etwas ab, was die äußere übersehen hat (Übung 5 löst sie nacheinander aus).

| Schicht | Was sie verhindert | Wo |
|---|---|---|
| **Hardware** | Netzteil-Schalter ist der echte Not-Aus. Die Servos melden Überlast, Temperatur, Strom und Spannung selbst (`status`-Bits) | Kapitel 01, 03 |
| **Eingabe** | Nur freigegebene RAM-Register (Whitelist), Werte im Registerbereich, EEPROM nur über `write_eeprom` (Drehmoment aus, Sperre öffnen, schreiben, zurücklesen), ID und Baudrate nur über eigene Abläufe | `safety.write_ram_register`, `ArmBackend` |
| **Bewegung** | Tempo und Beschleunigung vor dem ersten Ziel, Ziele auf kalibrierte Grenzen und auf höchstens `max_relative_target` Sprung begrenzt, beim Einschalten Ziel = Istposition (nichts springt), Radtempo auf `max_wheel_velocity` | `Arm.move_to`, `enable_torque`, `drive` |
| **Ablauf** | Jedes Programm setzt am Ende sicher zurück, auch bei Fehler oder Strg+C und SIGTERM: Räder zuerst, dann `safe_state` | `safety.open_arm`, `Arm.safe_shutdown` |
| **Web-App** | nur ein Client steuert, Watchdog (400 ms) entzieht das Steuerrecht, Fahren hat zusätzlich einen Totmann (300 ms), Not-Aus für alle Geräte, Token im Netz | `web/session.py`, `web/app.py` |
| **Netz** | Der Pi stoppt selbst, wenn der Laptop 500 ms schweigt. Der ESP32-Knoten stoppt die Räder nach seinem Watchdog (Standard 300 ms) und hält den Arm | `web/bus_server.py`, Firmware |

Was am Ende eines Programms mit dem Arm passiert, steht in `config/arms.toml` unter `safe_state`:
`torque_off` (Standard: der Arm sinkt ab) oder `hold` (er hält die Stellung).

## Entscheidungen, die man im Code wiedererkennt

- **Eigener Verbindungsweg statt LeRobots Roboterklassen.** `SOFollower.connect()` schreibt bei jedem Start Regelwerte
  ins EEPROM, `LeKiwi.configure()` schaltet das Drehmoment aller neun Motoren ein. Hier ist das ein
  sichtbarer, eigener Schritt (Kapitel 03). Außerdem kann nur `rustypot.Bus` direkt über IDs und Baudraten
  suchen. Auf dem veralteten `FeetechMotorsBus` baut nichts auf.
- **Kalibrierdateien im Format und in der Ordnerstruktur von LeRobot**, im Repo unter `calibration/`. Dieselbe Datei
  funktioniert mit `lerobot-calibrate` und `lerobot-record` (`--robot.calibration_dir`).
- **Ein Bus, ein Schloss, kein zweiter Thread.** In der Web-App liest ein Thread je Gerät im Takt von 30 Hz die
  Positionen. Aufnehmen, Abspielen, Teleoperation und der Fahr-Totmann laufen in diesem Takt mit (`DeviceSession`).
  Befehle der Oberfläche nehmen sich dasselbe Schloss. So überlagern sich nie zwei Pakete, und Tests kommen mit
  erfundenen Uhrzeiten aus.
- **Datasets immer lokal.** `LabDataset` kennt keinen Hub: `root` unter `recordings/datasets/`, `repo_id` `lab/<name>`.
- **Doku, Tabellen und Code bleiben gleich.** Gelenk-, Register- und API-Tabellen in der Doku vergleicht ein Test mit
  dem Code (`tests/test_docs.py`).

## Erweitern

**Ein neues Gerät** (anderer Arm, andere Basis): eine `DeviceSpec` mit den Gelenken in `devices/`, in
`DEVICE_TYPES` und `_get_registry()` (`devices/spec.py`) eintragen, ein Eintrag in `config/arms.toml`. Ein Test wie `tests/test_lekiwi_parts.py` zeigt, was
mindestens gelten muss.

**Ein neues Backend** (anderer Transport): `ArmBackend` ableiten. Abstrakt sind `connect`, `disconnect`,
`is_connected`, `scan`, `ping`, `_read`, `_write`, `_sync_read`, `_sync_write` und `set_torque`. Dazu kommt
`capabilities`: die `Capability`s, die es wirklich kann (`SCAN`, `REGISTERS`, `SYNC`, `EEPROM`, `WHEELS` …).
Was darüber liegt (EEPROM-Weg mit Rücklesen, Bereichsprüfung, `read_positions`, `read_state`), erbt es. Der
ESP32 ist das Beispiel für ein Backend, das nur einen Teil kann (kein `SYNC`, keine Baudrate).

**Neue Abläufe in der Web-App** gehören in `DeviceSession` und brauchen drei Dinge: Steuerrecht prüfen, bei
Verlust des Steuerrechts sauber enden (`_make_safe`, `emergency_stop`) und einen Eintrag in `docs/anhang.md`
(Test!).

## Tests

```bash
env -u PYTHONPATH uv run pytest -q       # alle Tests, keine Hardware nötig
uv run ruff check . && uv run ruff format --check .
```

Die Tests laufen ohne Hardware gegen `MockBackend`, den nachgebauten ESP32-Knoten und eine Web-App im Testclient. Zwei
Dinge prüfen sie gegen LeRobot selbst: die Umrechnung Rohwert ↔ Grad und die Kinematik der LeKiwi.
Das heißt nicht, dass die Hardware sich genauso verhält. Was am echten Gerät geprüft ist und was nicht, steht in
`PLAN.md` und in den Hinweisen der Kapitel.

!!! tip "`env -u PYTHONPATH`"
    Steht ROS 2 im `PYTHONPATH`, lädt pytest dessen Plugins und bricht ab (`No module named 'lark'`).
    `so101-lab doctor` warnt davor. Siehe Kapitel [Fehlersuche](10-fehlersuche.md).
