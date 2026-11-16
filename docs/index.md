# so101-lab

Lehr-Repository für den **SO-101-Roboterarm** und die **LeKiwi**-Plattform. Es führt vom einzelnen
Servo-Register bis zum aufgezeichneten Datensatz. Du lernst, **was unter der Haube passiert**, und
nicht nur, welcher Befehl den Arm bewegt.

!!! warning "Sicherheit zuerst"
    Leader und Follower haben **unterschiedliche Netzteile**. Ein falsches Netzteil kann Servos
    zerstören. Lies das Kapitel [Aufbau](01-aufbau.md), bevor du etwas einschaltest. Der Arm sinkt
    ab, wenn das Drehmoment ausgeschaltet wird, und kann Finger quetschen.

## Schnellstart: in fünf Befehlen zum bewegten Arm

Voraussetzung: [uv](https://docs.astral.sh/uv/) ist installiert, der Arm ist aufgebaut und
angeschlossen (Kapitel [Aufbau](01-aufbau.md)), die Servos haben ihre IDs
(Kapitel [Einrichtung](02-einrichtung.md)).

```bash
git clone <Repository-URL> so101_lab && cd so101_lab
uv sync                                                    # 1. Python und alle Pakete installieren
uv run so101-lab doctor                                    # 2. Umgebung prüfen
uv run so101-lab scan                                      # 3. Servos am Bus suchen
uv run scripts/02_read_servo.py --gelenk shoulder_pan      # 4. einen Servo auslesen
uv run scripts/03_move_servo.py --gelenk wrist_flex        # 5. ein Gelenk bewegen
```

Bei 3 bis 5 fragt das Programm nach dem Port, wenn mehrere angeschlossen sind. Mit `--port` gibst
du ihn direkt an (Linux `/dev/ttyACM0`, Windows `COM3`, macOS `/dev/cu.usbmodem…`).

!!! tip "Ohne Hardware ausprobieren"
    Das Gerät `demo` ist ein simulierter Arm. Alle Skripte laufen damit, zum Beispiel
    `uv run scripts/03_move_servo.py --gelenk wrist_flex --device demo`.

Schritt 5 fragt vor der Bewegung nach einer Bestätigung. Das Gelenk fährt nur ein kleines Stück
(±200 Schritt, etwa ±18°) und das Drehmoment ist danach wieder aus.

## Was es hier gibt

| Was | Wo | Wofür |
|---|---|---|
| Lernskripte | `scripts/01…07`, `09` | kurze, kommentierte Programme, jedes für sich lauffähig |
| Übungen | `exercises/` | Vorlagen mit Lücken und Musterlösungen (Kapitel [Übungen](uebungen.md)) |
| Befehl `so101-lab` | `doctor`, `ports`, `scan`, `cameras`, `state`, `web`, `esp32`, `service` | Umgebung prüfen, Servos und Kameras suchen, Zustand lesen, Web-App, ESP32-Knoten, Pi-Dienst |
| Konfiguration | `config/arms.toml` | welche Geräte es gibt, Port, Kalibrierung, Bewegungsgrenzen |
| Kalibrierung | `calibration/` | Kalibrierdateien je Arm im LeRobot-Format (in der Web-App verwaltbar, Reiter „Kalibrierdateien“) |
| Aufnahmen | `recordings/` | Bewegungen als CSV und JSON, LeRobot-Datasets (nicht im Git) |
| ESP32-Firmware | `firmware/esp32/` | Referenz für die Funkanbindung (Kapitel 07) |

## Die Web-App

```bash
uv run so101-lab web            # dann im Browser: http://127.0.0.1:8000/
```

Die Web-App zeigt alle Geräte aus `config/arms.toml`. Für USB-Geräte wählst du den Port aus einer
Liste aller USB- und ACM-Ports. Nach dem Verbinden siehst du je Gelenk live Position, Spannung,
Temperatur, Strom, Drehmoment und Fehler. Diese Doku ist unter `/docs/` eingebunden.

- **NOT-AUS** oben rechts (oder die Taste **Esc**) ist immer erreichbar: Räder stopp, Drehmoment aus,
  für alle verbundenen Geräte. Jeder darf ihn auslösen.
- **Steuerrecht:** Mehrere Browser dürfen zuschauen, aber nur einer steuert. Übernehmen geht nur
  ausdrücklich.
- **Watchdog:** Der steuernde Browser schickt alle 100 ms ein Lebenszeichen. Bleibt es länger als
  400 ms aus (Tab geschlossen, Netz weg; auch ein Tab im Hintergrund kann dafür zu langsam werden),
  verliert er das Steuerrecht und das Gerät geht in den sicheren Zustand (`safe_state`).
- **Beenden** mit Strg+C setzt alle Geräte sicher zurück.
- **Im Netzwerk** (z. B. auf einem Raspberry Pi): `so101-lab web --host 0.0.0.0 --token <geheim>`.
  Ohne Token startet der Server dann nicht. Im Browser `http://<adresse>:8000/?token=<geheim>` öffnen.

Reiter der Web-App:

| Reiter | Was |
|---|---|
| Status | alle Gelenke live, Meldungen |
| Einzelservo | ein Gelenk: Live-Werte, Tempo- und Beschleunigungsgrenze, Schieberegler, Drehmoment |
| Fahren | LeKiwi-Basis mit den Tasten W/A/S/D/Z/X fahren, Episoden aufnehmen (Kapitel 08) |
| Register | alle Register eines Servos lesen, freigegebene schreiben (EEPROM nur mit Bestätigung) |
| Kalibrierung | geführte Kalibrierung mit Anzeige, wo die Datei gespeichert wird |
| Kalibrierdateien | Dateien ansehen, löschen (mit Backup), wiederherstellen, importieren; Abgleich mit den Servos |
| Aufnahmen | von Hand aufnehmen, Teleoperation Leader → Follower, Bibliothek mit Kurven, Abspielen |
| Datensätze | Kamera-Vorschau und -Suche, Episoden als LeRobot-Dataset aufnehmen, Datasets verwalten |
| Einrichtung | Assistent für neue Servos: ID und Baudrate vergeben, prüfen, protokollieren (genau ein Servo am Bus) |


<a class="lab-link" href="../index.html#status">Web-App öffnen</a>

## Die Lernskripte

Jedes Skript beginnt mit einem Kopfkommentar: was passiert, was es verändert, welches
LeRobot-Werkzeug dasselbe „in fertig“ macht.

| Skript | Zweck | Verändert etwas am Servo? |
|---|---|---|
| `01_find_port.py` | Port des Adapters finden | nein |
| `02_read_servo.py` | alle Register eines Servos lesen, Live-Positionen | nein |
| `03_move_servo.py` | ein Gelenk kontrolliert bewegen | Torque und Ziel (RAM), danach aus |
| `04_write_param.py` | ein RAM-Register schreiben (Whitelist) | RAM, bis zum Ausschalten |
| `05_set_servo_id.py` | neuem Servo ID und 1 Mbit/s geben, prüfen, protokollieren | **EEPROM, dauerhaft** |
| `06_record_trajectory.py` | Bewegung von Hand führen und aufzeichnen | Torque aus |
| `07_replay_trajectory.py` | Aufnahme sanft anfahren und abspielen | Torque und Ziele (RAM), danach aus |
| `09_drive_base.py` | LeKiwi-Basis: Fahrbefehl in Radtempi umrechnen und kurz fahren | Torque und Radtempo (RAM), danach aus |

## So liest du diese Doku

1. [Aufbau](01-aufbau.md): Mechanik, Servos, Bus, Stromversorgung, Sicherheit im Labor.
2. [Einrichtung](02-einrichtung.md): Installation, Ports, Servo-IDs, Kalibrierung.
3. [Servos verstehen](03-servos.md): Register, Positionswerte, EEPROM und RAM, Regelparameter.
4. [Bewegungen](04-bewegungen.md): aufzeichnen, speichern, abspielen.
5. [Datensätze](05-datensaetze.md): Aufnahmen in der Web-App, Kameras, LeRobot-Datasets, Training.
6. [Raspberry Pi](06-raspberry-pi.md) und 7. [ESP32](07-esp32.md): der Arm über das Netz.
8. [LeKiwi](08-lekiwi.md): die fahrbare Basis, Kinematik, Fahren, Aufnahme mit Basis.
9. [Architektur](09-architektur.md): wie der Code aufgebaut ist und welche Sicherheitsschichten es gibt.
10. [Fehlersuche](10-fehlersuche.md): was am häufigsten schiefgeht.
11. [Übungen](uebungen.md): sieben Aufgaben mit Vorlage und Musterlösung.

Eine Übersicht, was am echten Gerät geprüft ist und was nur in der Simulation, steht in `PLAN.md`.
