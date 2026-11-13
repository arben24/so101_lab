# Einrichtung: uv, Ports, Motor-IDs, Kalibrierung

Reihenfolge für einen neuen Arm: **Installation → Port finden → Servo-IDs vergeben → Arm
zusammenbauen → kalibrieren → prüfen.** Neue Servos haben alle die ID 1, deshalb kommen die IDs
*vor* dem Einbau.

## 1. Installation mit uv

Das Projekt braucht nur [uv](https://docs.astral.sh/uv/) (Installationsanleitung dort). uv legt
Python 3.12 und alle Pakete selbst an. Es gibt kein `pip`, kein Node und keine globale Installation.

```bash
git clone <Repository-URL> so101_lab && cd so101_lab
uv sync
uv run so101-lab doctor
```

- `uv sync` lädt beim ersten Mal rund zwei Gigabyte (LeRobot 0.7.0 und PyTorch). Dafür braucht es
  einmal Internet. Danach läuft alles **offline**.
- Standard ist PyTorch als **CPU-Variante**. Sie genügt für Ansteuerung, Kalibrierung, Aufnahme und
  Abspielen. Nur für das Training eigener Modelle (Übung 7) gibt es die CUDA-Variante:
  `uv sync --no-group cpu --group cuda`.
- Unter **Linux** wird das Paket `evdev` beim Installieren kompiliert. Fehlt der Compiler:
  `sudo apt install build-essential python3-dev`.

`so101-lab doctor` prüft Python-Version, LeRobot-Version, Pakete, Ports, Rechte, Kameras und
`config/arms.toml`. Jede Zeile ist `[ OK ]`, `[WARN]`, `[FEHL]` oder `[ .. ]` (Info). Bei `[FEHL]` steht
darunter, was zu tun ist. Der Befehl öffnet keine Ports und sendet nichts an die Servos.

## 2. Den Port finden

Der Adapter erscheint als serieller Port:

| System | Name |
|---|---|
| Linux | `/dev/ttyACM0` oder `/dev/ttyUSB0` |
| Windows | `COM3`, `COM4`, … |
| macOS | `/dev/cu.usbmodem…` |

```bash
uv run so101-lab ports                  # Liste aller USB-Seriell-Ports
uv run scripts/01_find_port.py          # dasselbe, mit Erklärung
uv run scripts/01_find_port.py --vergleich   # Adapter abziehen: der verschwundene Port ist der gesuchte
```

Der Eintrag enthält USB-Kennung und Seriennummer, zum Beispiel
`/dev/ttyACM0 (1a86:55d3) – QinHeng USB Single Serial (Servo-Adapter) [SN 5B90103144]`. Bei zwei Armen
unterscheidest du die Adapter an der Seriennummer.

**Der Port wird bei jedem Aufruf gewählt.** Es gibt bewusst keine festen Gerätenamen über udev-Regeln,
denn die Nummer (`ACM0`, `ACM1`) hängt von der Reihenfolge des Ansteckens ab. Drei Wege:

1. `--port /dev/ttyACM0` bei jedem Befehl oder Skript,
2. keine Angabe: bei mehreren Ports erscheint eine nummerierte Auswahl,
3. fester Eintrag `port = "…"` in `config/arms.toml` (siehe unten).

!!! note "Keine Zugriffsrechte unter Linux"
    Meldet das Programm *„Keine Rechte für /dev/ttyACM0“*, fehlt die Mitgliedschaft in der Gruppe
    `dialout`: `sudo usermod -aG dialout $USER`, danach **ab- und wieder anmelden**.
    Ob du schon in der Gruppe bist, zeigt `so101-lab doctor`.

!!! note "Port ist belegt"
    Ein Port kann nur von **einem** Programm gleichzeitig geöffnet sein. Läuft noch ein anderes Skript,
    die Web-App oder ein LeRobot-Befehl, meldet das Programm *„Port … ist belegt“*. Das andere
    Programm beenden.

## 3. Geräte in `config/arms.toml`

Jeder `[[device]]`-Block beschreibt einen Arm:

```toml
[[device]]
name = "follower"              # so rufst du ihn auf: --device follower
type = "so101_follower"        # so101_follower | so101_leader | lekiwi
backend = "usb"                # usb | mock (remote und esp32 folgen in späteren Kapiteln)
# port = "/dev/ttyACM0"        # optional; ohne Eintrag wird der Port gewählt
calibration_id = "follower_1"  # Name der Kalibrierdatei
safe_state = "torque_off"      # was beim Beenden passiert: torque_off oder hold
```

Optionale Bewegungsgrenzen: `max_velocity` (Schritt/s, Standard 1000), `acceleration` (Einheit
100 Schritt/s², Standard 20), `max_relative_target` (größter Sprung je Sollwert in Schritt, Standard
400). Für den Leader den auskommentierten Block `leader` aktivieren und `calibration_id` vergeben.

**`safe_state`** bestimmt, was beim Beenden eines Skripts oder bei einem Fehler mit dem Arm geschieht:

| Wert | Wirkung | Vor- und Nachteil |
|---|---|---|
| `torque_off` (Standard) | Drehmoment aus | Servos bleiben kalt, der Arm sinkt aber ab |
| `hold` | aktuelle Stellung halten | Arm bleibt stehen, Servos erwärmen sich |

Das lässt sich pro Gerät in dieser Datei umstellen. Das Gerät **`demo`** ist ein simulierter Arm ohne
Hardware: Alle Skripte laufen damit (`--device demo`).

## 4. Servo-IDs vergeben

Jeder Servo bekommt die ID seines Gelenks (Tabelle in Kapitel [Aufbau](01-aufbau.md)). Das ist ein
**einmaliger Schritt je Servo**; die ID bleibt dauerhaft im Servo gespeichert (EEPROM).

!!! danger "Immer nur ein Servo am Bus"
    Alle neuen Servos haben die ID 1. Hängen zwei am Bus, antworten beide gleichzeitig und die
    Antworten überlagern sich. `05_set_servo_id.py` bricht deshalb ab, wenn es nicht **genau einen**
    Servo findet.

Ablauf je Servo:

1. Netzteil aus. Genau **einen** Servo mit dem Adapter verbinden, **noch nicht** im Arm verbaut.
2. Netzteil an.
3. ID setzen, zum Beispiel für den Greifer:

   ```bash
   uv run scripts/05_set_servo_id.py --gelenk gripper
   ```

   Das Skript sucht den Servo, zeigt ihn an, nennt die geplante Änderung (`ID 1 → 6 (gripper)`) und fragt
   nach. Erst nach `j` wird geschrieben und anschließend **geprüft**: Antwortet der Servo unter der
   neuen ID, und ist die alte ID frei?
4. Netzteil aus, Servo abziehen, **beschriften** („6 gripper“), nächster Servo.

Reihenfolge wie bei LeRobot: erst Greifer (6), dann zurück bis zur Schulter (1). Erst wenn alle Servos
ihre ID haben, wird der Arm zusammengebaut.

Prüfen, wenn alle Servos am Bus hängen:

```bash
uv run so101-lab scan --port /dev/ttyACM0
```

Erwartet sind sechs Zeilen für die IDs 1 bis 6, jeweils `STS3215`, Modellnummer `777`, Baudrate
`1000000` und eine Firmware-Version. Findet der Scan nichts, hilft `--all-baudrates` (dauert etwa 8 s).

Das Skript ändert **nur die ID**, nicht die Baudrate. Neue Servos laufen ab Werk mit 1 Mbit/s, so wie es
der Bus braucht.

??? info "Dasselbe „in fertig“: lerobot-setup-motors"
    ```bash
    uv run lerobot-setup-motors --robot.type=so101_follower --robot.port=/dev/ttyACM0
    uv run lerobot-setup-motors --teleop.type=so101_leader  --teleop.port=/dev/ttyACM0
    ```
    Das Werkzeug führt durch alle Motoren nacheinander und setzt zusätzlich die Baudrate.

## 5. Kalibrierung

Die Servos liefern Rohwerte von 0 bis 4095. Welcher Wert am Arm „gerade nach oben“ bedeutet und wie weit
sich ein Gelenk bewegen darf, hängt vom Zusammenbau ab. Die **Kalibrierung** hält das fest:

- **Homing-Offset:** Verschiebung, damit die Mittelstellung des Gelenks bei etwa 2048 liegt.
- **Bewegungsbereich:** kleinster und größter Wert, den das Gelenk mechanisch erreicht
  (`range_min`, `range_max`). Die Software begrenzt später jedes Ziel auf diesen Bereich.

Ohne Kalibrierung gilt der volle Bereich 0 bis 4095. Dann **kann ein Ziel gegen einen mechanischen
Anschlag fahren**. Deshalb: erst kalibrieren, dann bewegen.

### In der Web-App (empfohlen)

<a class="lab-link" href="../index.html#kalibrierung">In der Web-App ausprobieren</a>

1. `uv run so101-lab web`, im Browser das Gerät wählen, Port wählen, **Verbinden**.
2. **Steuerung übernehmen**, Reiter **Kalibrierung**. Oben steht, welche Datei es schon gibt und
   **wohin die neue Datei geschrieben wird**.
3. **Kalibrierung starten:** Das Drehmoment aller Gelenke geht aus, **den Arm festhalten**. Die bisherigen
   Werte in den Servos werden gemerkt.
4. Alle Gelenke von Hand in die **Mitte ihres Bewegungsbereichs** stellen (Skizze auf der Seite:
   Oberarm senkrecht, Unterarm waagerecht, Handgelenk gerade), dann **Mitte übernehmen**. Der Homing-Offset
   wird so ins EEPROM geschrieben, dass diese Stellung den Wert 2047 ergibt.
5. Jedes Gelenk nacheinander langsam **bis zu beiden Anschlägen** bewegen. Die Tabelle zeigt live Minimum,
   Istwert und Maximum. `wrist_roll` dreht ganz und bekommt automatisch 0 bis 4095.
6. **Speichern:** Die Grenzen werden ins EEPROM geschrieben (jeweils zurückgelesen und geprüft), die Datei
   wird gespeichert und die Grenzen gelten sofort. Eine vorhandene Datei bleibt als `.json.bak` erhalten.

**Abbrechen** schreibt die alten Werte in die Servos zurück. Dasselbe passiert automatisch, wenn der Tab
geschlossen wird, der Watchdog anspricht oder jemand den Not-Aus drückt. So passen Servos und alte
Kalibrierdatei immer zusammen. Ein Gelenk, das nicht bewegt wurde, verhindert das Speichern.

Die Web-App schreibt dabei **nur** Homing-Offset und Positionsgrenzen, sonst nichts. Das Ergebnis ist
dasselbe wie bei `lerobot-calibrate` (gleiche Rechnung, gleiches Dateiformat). Für die LeKiwi werden nur
die Arm-Gelenke von Hand abgefahren, die Räder bekommen wie bei LeRobot Offset 0 und den vollen Bereich.

### Ablage und Format

Das Ergebnis liegt als JSON-Datei in `calibration/robots/so_follower/follower_1.json` (bzw. unter
`teleoperators/so_leader/` für den Leader und `robots/lekiwi/` für die LeKiwi). Der Dateiname ist die
`calibration_id` aus `arms.toml`. Die Ablage im Repo entspricht der Struktur von LeRobot, deshalb nutzen
LeRobot-Werkzeuge dieselbe Datei, wenn man ihnen `--robot.calibration_dir` mitgibt.

Format (ein Eintrag je Motor):

```json
{
    "shoulder_pan": {"id": 1, "drive_mode": 0, "homing_offset": -123, "range_min": 800, "range_max": 3200}
}
```

Die Software findet die Kalibrierung selbst: zuerst unter `calibration/…` im Repo, dann in der
Standardablage von LeRobot (`~/.cache/huggingface/lerobot/calibration/…`). Ob eine gefunden wurde, zeigt
`so101-lab state follower` in der ersten Zeile und die Web-App unter „Kalibrierung“.

### Dasselbe mit LeRobot

Ein Gerät je Aufruf, Follower **oder** Leader:

```bash
# Follower
uv run lerobot-calibrate --robot.type=so101_follower --robot.port=/dev/ttyACM0 \
    --robot.id=follower_1 --robot.calibration_dir=calibration/robots/so_follower

# Leader
uv run lerobot-calibrate --teleop.type=so101_leader --teleop.port=/dev/ttyACM0 \
    --teleop.id=leader_1 --teleop.calibration_dir=calibration/teleoperators/so_leader
```

Der Dialog im Terminal hat dieselben zwei Schritte (Mitte, Enter; Bereich abfahren, Enter).

!!! warning "Was LeRobot dabei zusätzlich in die Servos schreibt"
    `lerobot-calibrate` schreibt wie die Web-App Homing-Offset und Positionsgrenzen ins EEPROM. Außerdem
    schreibt LeRobot bei **jedem** Verbinden Regelparameter (P 16, I 0, D 32, Beschleunigung, Betriebsart
    `position`; am Greifer zusätzlich Drehmoment- und Stromgrenzen) dauerhaft in alle Servos. Das ist bei
    LeRobot so vorgesehen, aber nicht offensichtlich. Die Lernskripte und die Web-App dieses Repos
    schreiben beim Verbinden **nichts**.

## 6. Prüfen

```bash
uv run so101-lab scan --port /dev/ttyACM0            # IDs 1 bis 6 gefunden?
uv run so101-lab state follower --port /dev/ttyACM0  # Zustand lesen, verändert nichts
uv run scripts/02_read_servo.py --gelenk shoulder_pan --port /dev/ttyACM0
```

`state` zeigt je Gelenk: Rohwert, Wert in Grad (mit Kalibrierung), Tempo, Last, Spannung, Temperatur und
ob das Drehmoment an ist. Bewege den Arm bei ausgeschaltetem Drehmoment von Hand (`--watch`): Die Werte
müssen sich ändern. Ändert sich ein Wert nicht oder sind zwei vertauscht, stimmt eine ID nicht.
