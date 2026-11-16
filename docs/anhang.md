# Anhang: API, Register- und Gelenktabellen

Die Registertabelle steht in Kapitel [Servos verstehen](03-servos.md), die Gelenk- und ID-Tabellen für
SO-101 und LeKiwi in Kapitel [Aufbau](01-aufbau.md). Beide werden per Test mit dem Code abgeglichen.
Dieser Anhang beschreibt die Schnittstelle der Web-App. Auch diese Liste prüft ein Test
(`tests/test_docs.py`): Jede Route der App muss hier stehen.

Eine automatisch erzeugte, ausprobierbare Fassung (OpenAPI) liefert die laufende App unter
`/api/docs`.

## Grundregeln

- **Token:** Ist eines gesetzt (`--token` oder `SO101_LAB_TOKEN`, Pflicht bei `--host 0.0.0.0`),
  braucht jede Anfrage an `/api/…` und `/ws…` die Kopfzeile `Authorization: Bearer <token>` oder
  `?token=<token>`. Ohne Token gehen nur `/api/info`, die Seiten und die Doku.
- **`client_id`:** Der Browser bekommt sie beim Öffnen von `/ws`. Aktionen, die etwas bewegen oder
  schreiben, schicken sie im JSON mit. Nur der steuernde Client darf sie ausführen.
- **Fehler** kommen als `{"detail": "<Text>"}`: 400 Eingabe oder Gerät, 401 Token, 404 unbekannt,
  409 Zustand passt nicht (kein Steuerrecht, schon verbunden, Port belegt).
- **Werte** an der API sind Rohwerte (Schritte 0…4095), außer wo Grad/Prozent angegeben sind.

## Allgemein

| Route | Zweck |
|---|---|
| `GET /api/info` | Version, ob ein Token nötig ist, ob die Doku eingebunden ist (ohne Token) |
| `GET /api/ports` | USB-Seriell-Ports mit USB-ID, Seriennummer und Text für die Auswahl |
| `GET /api/devices` | Geräte aus `arms.toml` mit Typ, Backend und ob verbunden |
| `POST /api/estop` | **Not-Aus** für alle verbundenen Geräte und alle Bus-Freigaben. Ohne Steuerrecht |

## Gerät verbinden und steuern

| Route | Zweck |
|---|---|
| `POST /api/devices/{name}/connect` | verbinden, JSON `{"port": …}` (nur `usb`, sonst aus `arms.toml`) |
| `POST /api/devices/{name}/disconnect` | sicher zurücksetzen und trennen |
| `GET /api/devices/{name}/state` | derselbe Zustand, den `/ws` laufend schickt |
| `POST /api/devices/{name}/control` | Steuerrecht übernehmen, `{"client_id", "force"}` |
| `POST /api/devices/{name}/control/release` | Steuerrecht abgeben (führt zu `safe_state`) |
| `POST /api/devices/{name}/torque` | Drehmoment, `{"client_id", "enabled", "joints"}` (`joints` leer = alle) |
| `POST /api/devices/{name}/motion` | Tempo und Beschleunigung, `{"client_id", "velocity", "acceleration"}` |
| `POST /api/devices/{name}/move` | Zielpositionen, `{"client_id", "targets": {gelenk: roh}}`, begrenzt wie `Arm.move_to` |
| `POST /api/devices/{name}/drive` | LeKiwi-Basis fahren, `{"client_id", "x", "y", "theta"}` in m/s bzw. Grad/s (Kapitel 08). Alle 100 ms wiederholen, sonst halten die Räder nach 300 ms an. Alles 0 = anhalten. Antwort: gesendete Radtempi |

## Register und Kalibrierung

| Route | Zweck |
|---|---|
| `GET /api/devices/{name}/registers/{joint}` | alle Register eines Gelenks lesen |
| `POST /api/devices/{name}/registers` | ein Register schreiben, `{"client_id", "joint", "register_name", "value", "confirm_eeprom"}` |
| `POST /api/devices/{name}/calibration/{step}` | Kalibrierung: `start`, `middle`, `finish`, `cancel`; `apply` (Datei → EEPROM), `reload` (Datei neu laden) |
| `GET /api/devices/{name}/calibration/compare` | Datei gegen EEPROM vergleichen (nur lesen) |
| `GET /api/calibrations` | alle Kalibrierdateien (Repo und LeRobot-Ablage) |
| `GET /api/calibrations/{source}/{kind}/{folder}/{cal_id}` | eine Datei mit Prüfergebnis, `?backup=true` für `.json.bak` |
| `GET /api/calibrations/{source}/{kind}/{folder}/{cal_id}/json` | Datei herunterladen |
| `POST /api/calibrations/{source}/{kind}/{folder}/{cal_id}/{action}` | `delete` (→ `.json.bak`), `restore`, `copy` (LeRobot-Ablage → Repo) |
| `POST /api/calibrations/import` | Datei hochladen, `{"device_type", "cal_id", "content", "overwrite"}` |

`source` ist `repo` oder `lerobot`, `kind` `robots` oder `teleoperators`, `folder` `so_follower`,
`so_leader` oder `lekiwi`.

## Aufnahmen, Abspielen, Teleoperation

| Route | Zweck |
|---|---|
| `POST /api/devices/{name}/recording/start` | Aufnahme starten, `{"client_id", "name", "overwrite"}` |
| `POST /api/devices/{name}/recording/stop` | stoppen und speichern |
| `GET /api/devices/{name}/playback/check` | Abspielen prüfen, ohne zu bewegen, `?trajectory=…&speed=…` |
| `POST /api/devices/{name}/playback/start` | abspielen, `{"client_id", "trajectory", "speed"}` |
| `POST /api/devices/{name}/playback/stop` | anhalten (Stellung halten) |
| `POST /api/devices/{name}/teleop/start` | folgen, `{"client_id", "leader"}` |
| `POST /api/devices/{name}/teleop/stop` | Folgen beenden |
| `GET /api/trajectories` | Bibliothek |
| `GET /api/trajectories/{traj_name}` | Details mit höchstens 400 Punkten für die Kurven |
| `GET /api/trajectories/{traj_name}/csv` | CSV herunterladen |
| `DELETE /api/trajectories/{traj_name}` | löschen, `?client_id=…` |

## Kameras und Datasets

| Route | Zweck |
|---|---|
| `GET /api/cameras` | Kameras an diesem Rechner suchen (ohne die schon geöffneten) |
| `GET /api/devices/{name}/cameras/{cam}/frame.jpg` | neuestes Bild |
| `GET /api/devices/{name}/cameras/{cam}/stream` | MJPEG-Strom für die Vorschau, `?fps=…` (Standard 10, 1 bis 30) |
| `POST /api/devices/{name}/dataset/open` | Dataset öffnen oder weiterschreiben, `{"client_id", "name", "task", "fps"}` |
| `POST /api/devices/{name}/dataset/episode/start` | Episode starten |
| `POST /api/devices/{name}/dataset/episode/stop` | Episode beenden, `{"client_id", "keep"}` (`false` verwirft) |
| `POST /api/devices/{name}/dataset/close` | Dataset abschließen |
| `GET /api/datasets` | vorhandene Datasets |
| `DELETE /api/datasets/{ds_name}` | löschen, `?client_id=…` |

## Einrichtungsassistent (ID-Vergabe)

| Route | Zweck |
|---|---|
| `GET /api/setup/types` | Gerätetypen mit Gelenken in Einrichtungsreihenfolge, simulierte Varianten |
| `GET /api/setup` | Zustand des Assistenten (oder `null`) |
| `POST /api/setup/start` | starten, `{"client_id", "port", "device_type"}` (`port` `"mock"` = Simulation) |
| `POST /api/setup/scan` | den einen Servo suchen (alle Baudraten) |
| `POST /api/setup/assign` | ID schreiben, `{"client_id", "joint"}` oder `{"client_id", "id"}` |
| `POST /api/setup/verify` | prüfen (auch Neustart-Probe), gleiche Felder |
| `POST /api/setup/simulate` | Simulation: anderen Servo „einstecken“, `{"client_id", "variant"}` |
| `POST /api/setup/stop` | Assistent beenden |

## WebSockets

| Route | Zweck |
|---|---|
| `WS /ws` | Live-Verbindung des Browsers: erst `{"type": "hello", "client_id"}`, dann 20× je Sekunde `{"type": "state", "devices", "setup", "remote"}`. Der Browser schickt alle 100 ms `{"type": "heartbeat"}` |
| `WS /ws/bus/{name}` | Bus-Freigabe für das `RemoteBackend` eines anderen Rechners (unten) |

### Bus-Freigabe `/ws/bus/{name}`

`{name}` ist ein Gerät aus der `arms.toml` **dieses** Rechners (`usb` mit festem `port`, oder `mock`).
Ablauf und Sicherheit stehen in Kapitel [Raspberry Pi](06-raspberry-pi.md). Nachrichten sind JSON-Texte.

Zuerst schickt der Server:

```json
{"type": "hello", "protocol": 1, "version": "0.1.0", "device": "lekiwi", "device_type": "lekiwi",
 "ids": [1, 2, 3, 4, 5, 6, 7, 8, 9], "capabilities": ["eeprom", "registers", "..."],
 "baudrate": 1000000, "safe_state": "torque_off", "watchdog_ms": 500, "host": "lekiwi"}
```

oder `{"type": "error", "message": "…"}` und schließt (Token falsch, Gerät unbekannt oder belegt).

Danach je Anfrage genau eine Antwort, in derselben Reihenfolge:

```json
{"id": 7, "op": "sync_read", "args": {"ids": [1, 2], "name": "present_position"}}
{"id": 7, "ok": true, "result": {"1": 2047, "2": 1980}}
{"id": 8, "ok": false, "error": "ServoNotRespondingError", "message": "Kein Servo unter ID 42."}
```

| `op` | `args` | `result` |
|---|---|---|
| `heartbeat` | – | `null`, hält den Watchdog an |
| `close` | – | `null`, ordentliches Ende ohne Zurücksetzen |
| `scan` | `ids`, `baudrates` (je `null` = alle bzw. aktuelle) | Liste `{id, model_number, model_name, baudrate, firmware}` |
| `ping` | `id` | Modellnummer oder `null` |
| `read_register` | `id`, `name` | Wert |
| `write_register` | `id`, `name`, `value` | `null` (nur RAM-Register) |
| `sync_read` | `ids`, `name` | `{"id": wert}` |
| `sync_write` | `name`, `values` (`{"id": wert}`) | `null` |
| `set_torque` | `ids`, `enabled` | `null` |
| `read_positions` | `ids` | `{"id": roh}` |
| `read_state` | `ids` | `{"id": {position, velocity, load, voltage, temperature, status, torque_enabled, current_ma}}` |
| `write_eeprom` | `id`, `name`, `value` | `null`, am Stück mit Sperre und Zurücklesen |
| `change_id` | `old_id`, `new_id` | `null`, mit Prüfung per Ping |
| `change_baudrate` | `id`, `baudrate` | neue Baudrate des Ports |
| `set_port_baudrate` | `baudrate` | neue Baudrate des Ports (Servos unverändert) |

`error` ist der Name der Fehlerklasse (`BackendError`, `NotSupportedError`, `ServoNotRespondingError`,
`VerificationError`), das `RemoteBackend` wirft dieselbe Klasse. Kommt länger als `watchdog_ms` keine
Nachricht, setzt der Server das Gerät sicher zurück und schickt `{"type": "error", …}`.
