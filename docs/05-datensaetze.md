# Datensätze mit und ohne Kamera

Es gibt zwei Arten, Bewegungen festzuhalten:

| | Aufnahme (Trajektorie) | LeRobot-Dataset |
|---|---|---|
| Inhalt | Zeit und Rohposition je Gelenk | Beobachtung (Gelenke, Kamerabilder) und Aktion je Zeitschritt, in Episoden |
| Dateien | `recordings/trajectories/<name>.csv` und `.json` | Parquet-Tabellen und Videos (Format v3.0) |
| Wozu | ansehen, auswerten, abspielen, glätten | Policies trainieren (`lerobot-train`) |
| Kamera | nein | ja |
| Wo | Reiter **Aufnahmen**, Skripte 06/07 | Reiter **Datensätze** |

Dieses Kapitel beschreibt zuerst Aufnahmen in der Web-App: von Hand geführt, mit Teleoperation, die
Bibliothek und das Abspielen. Das Dateiformat und die Rechnung dahinter stehen in Kapitel
[Bewegungen](04-bewegungen.md). Danach kommen Kameras und LeRobot-Datasets (Abschnitte 6 bis 8).

## 1. Der Reiter „Aufnahmen“

<a class="lab-link" href="../index.html#aufnahmen">In der Web-App ausprobieren</a>

`uv run so101-lab web`, Gerät oben verbinden, **Steuerung übernehmen**, Reiter **Aufnahmen**. Ohne
Hardware gehen Aufnehmen und Abspielen mit dem Gerät `demo` (die Simulation bewegt sich beim Aufnehmen
nicht, die Kurven bleiben flach). Für die Teleoperation braucht es zwei kalibrierte Geräte.

Recorder, Abspielen und Teleoperation laufen im Takt der Zustandsabfrage mit (30 Hz). Sie brauchen das
Steuerrecht. Verliert der Browser es (Tab zu, Watchdog, Not-Aus, „Steuerung abgeben“), endet alles
sofort: Abspielen und Folgen hören auf, eine laufende Aufnahme wird **bis dahin gespeichert** und im JSON
als unterbrochen markiert (`interrupted`).

## 2. Von Hand aufnehmen

1. Namen eingeben (Buchstaben, Ziffern, `_`, `-`). Gibt es den Namen schon, wird abgelehnt, außer
   „vorhandene ersetzen“ ist angehakt.
2. **Aufnahme starten.** Das Drehmoment des Geräts geht aus.
3. Den Arm von Hand führen. Die Zeile unter den Knöpfen zeigt Dauer und Anzahl der Abtastungen.
4. **Stoppen und speichern.** Die Aufnahme erscheint in der Bibliothek.

!!! danger "Follower sackt zusammen"
    Ohne Drehmoment hat ein Follower keine Haltekraft. Beim Start der Aufnahme festhalten. Bequemer ist
    es, den **Leader** aufzunehmen: oben das Leader-Gerät wählen und dort aufnehmen.

Eine Aufnahme endet spätestens nach 10 Minuten.

## 3. Teleoperation: Leader führt, Follower folgt

So arbeitet auch `lerobot-teleoperate`: Man führt den Leader von Hand, der Follower macht dasselbe.

1. Oben beide Geräte nacheinander verbinden (zwei Adapter, zwei Ports). Beide brauchen eine
   Kalibrierung.
2. Oben das **folgende** Gerät auswählen, **Steuerung übernehmen**.
3. Im Reiter „Aufnahmen“ unter Teleoperation den Leader wählen, **Folgen starten**.
4. Der Follower fährt erst in mindestens 2 s sanft zur Stellung des Leaders (gleiche Anfahrt wie beim
   Abspielen) und folgt ihm dann.

Umgerechnet wird über Grad/Prozent mit beiden Kalibrierungen (Kapitel [Bewegungen](04-bewegungen.md),
„Von einem Arm auf einen anderen“). Ohne Kalibrierung startet die Teleoperation nicht: Die Rohwerte
zweier Arme passen nicht zueinander.

Der Leader wird nur gelesen, an ihm ändert sich nichts. Kommen von ihm länger als 0,3 s keine
Positionen (getrennt, Lesefehler), hält der Follower an und bleibt in seiner Stellung.

**Aufnehmen während der Teleoperation:** Läuft die Teleoperation, bleibt beim Start der Aufnahme das
Drehmoment an. Aufgenommen wird der folgende Arm. Das JSON nennt als Quelle den Leader.

## 4. Bibliothek

Die Tabelle zeigt alle Aufnahmen aus `recordings/trajectories/`, die neueste oben, auch solche aus
Skript 06.

- **Ansehen** zeigt die Rohwerte aller Gelenke über der Zeit. Gut zum Prüfen, ob die Aufnahme das
  enthält, was man erwartet, und um Rauschen zu sehen.
- **CSV** lädt die Datei herunter, etwa für eine Tabellenkalkulation oder `pandas`.
- **Löschen** entfernt CSV und JSON nach einer Rückfrage.

Namen kommen nur aus dem Ordner selbst und werden geprüft: Ein Name aus dem Browser kann keinen Pfad
außerhalb von `recordings/trajectories/` ergeben.

## 5. Abspielen

Tempo (0,1 bis 3) einstellen, in der Bibliothek **Abspielen**, Rückfrage bestätigen. Der Ablauf ist
derselbe wie bei Skript 07:

1. Prüfen (die Rückfrage zeigt das Ergebnis): passende Gelenke, Umrechnung zwischen den Armen, Werte
   außerhalb der Gelenkgrenzen. Solche Werte werden **auf die Grenzen begrenzt** und als Hinweis genannt.
   Typisch ist eine Aufnahme, die in der Ruhestellung am Anschlag beginnt: Der Arm liegt dort oft ein
   paar Schritte jenseits des Bereichs, den die Kalibrierung beim Abfahren gesehen hat. Abgelehnt wird nur,
   wenn schon die Startpose mehr als 400 Schritt außerhalb liegt: Dann passt die Aufnahme nicht zum Arm.
2. Bewegungsgrenzen setzen, Drehmoment ohne Sprung einschalten.
3. Sanfte Anfahrt zur Startpose, dann Abspielen. Zwischen zwei Abtastungen wird linear interpoliert.
4. Am Ende hält der Arm die Endstellung (Drehmoment bleibt an, bis die Steuerung abgegeben wird).

Fortschrittsbalken und Meldungen zeigen Anfahrt und Abspielen. **Abspielen stoppen** hält den Arm in der
aktuellen Stellung an. Während des Abspielens sind Schieberegler, Kalibrierung und Teleoperation gesperrt.

**Ohne Kalibrierung** gehen Aufnehmen und Abspielen auch. Die Grenzen sind dann der volle Bereich 0…4095,
ein Gelenk kann also gegen einen Anschlag fahren. Ein Warnfeld oben im Reiter weist darauf hin.

**Neu kalibriert seit der Aufnahme?** Der Servo meldet `Rohwert = tatsächliche Stellung − Homing-Offset`.
Eine neue Kalibrierung ändert den Offset und damit alle Rohwerte. Jede Aufnahme speichert deshalb die
Offsets, die beim Aufnehmen im Servo standen (`homing_offsets` im JSON), und das Abspielen rechnet einen
geänderten Offset heraus. Ältere Aufnahmen ohne diese Angabe bekommen einen Hinweis.

!!! warning "Vor dem Abspielen"
    Arbeitsbereich freihalten, beim ersten Mal Tempo 0,3. Esc löst im Browser den Not-Aus aus.

## 6. Kameras

Kameras gehören zu einem Gerät und stehen in `config/arms.toml`:

```toml
[[device]]
name = "follower"
# …
cameras = [
  { name = "front", source = "/dev/v4l/by-id/usb-Generic_FULL_HD_1080P_Webcam_200901010001-video-index0" },
]
```

| Schlüssel | Bedeutung | Standard |
|---|---|---|
| `name` | Name im Dataset (`observation.images.<name>`), Kleinbuchstaben, Ziffern, `_` | – |
| `source` | Gerätepfad, Nummer (Windows/macOS) oder `test` (Testbild ohne Hardware) | – |
| `width`, `height`, `fps` | gewünschtes Format | 640, 480, 30 |
| `fourcc` | Bildformat der Kamera, `"MJPG"` schafft über USB 2.0 höhere Raten als `"YUYV"` | `"MJPG"` |
| `rotation` | 0, 90, 180 oder 270 Grad | 0 |

Den passenden Eintrag findet der Befehl

```bash
uv run so101-lab cameras
```

Er öffnet jede Kamera kurz und zeigt, ob sie Bilder liefert. Unter Linux erscheint eine USB-Kamera oft
**zweimal** (`/dev/video0` und `/dev/video1`): Nur einer der beiden liefert Bilder, der andere ist für
Metadaten. Der Befehl schlägt den **festen Namen** unter `/dev/v4l/by-id/` vor. Der bleibt gleich, auch
wenn sich die Nummer beim Umstecken ändert.

Beim Verbinden des Geräts werden seine Kameras geöffnet. Fehlt eine, verbindet das Gerät trotzdem, mit
einer Warnung. Der Reiter **Datensätze** zeigt eine Live-Vorschau (höchstens 10 Bilder/s, um Browser und
Netz zu schonen) und unter jedem Bild die tatsächliche Größe und Bildrate. Die Suche gibt es dort auch.

!!! tip "Wenig Licht = wenig Bilder"
    Viele Webcams verlängern bei wenig Licht die Belichtungszeit und liefern dann nur noch 10 statt
    30 Bilder je Sekunde. Die Vorschau warnt dann. Für Datasets: gut beleuchten. Ein Dataset mit 30 fps
    wiederholt sonst jedes Bild dreimal.

## 7. LeRobot-Datasets aufnehmen

<a class="lab-link" href="../index.html#datensaetze">In der Web-App ausprobieren</a>

Ein **LeRobot-Dataset** ist das, was `lerobot-record` schreibt und `lerobot-train` liest. Es besteht aus
**Episoden**: Jede Episode ist eine Vorführung der Aufgabe. Je Zeitschritt (bei 30 fps also 30-mal je
Sekunde) enthält es

| Merkmal | Inhalt |
|---|---|
| `observation.state` | Gelenkwerte des Arms: Grad, Greifer in Prozent (wie LeRobot, Namen `shoulder_pan.pos` …) |
| `observation.images.<kamera>` | ein Bild je Kamera (gespeichert als Video) |
| `action` | was der Arm tun soll, in denselben Einheiten |
| `task` | die Aufgabe in Worten, z. B. „Den Würfel in die Schale legen“ |

Woher die **Aktion** kommt:

- **Teleoperation** (Reiter Aufnahmen): die Stellung des Leaders, so wie bei `lerobot-record`. Das ist der
  übliche Weg für Trainingsdaten: Die Policy lernt, aus Bild und Zustand die nächste Leader-Stellung
  vorherzusagen.
- **Abspielen** einer Aufnahme: das Ziel, das gerade an den Arm geht.
- **Von Hand geführt**: die Beobachtung selbst.

Ablauf im Reiter **Datensätze** (Steuerrecht und Kalibrierung nötig):

1. Name und Aufgabe eingeben, **Dataset öffnen**. Gibt es den Namen schon, wird das Dataset
   weitergeschrieben (gleiche Kameras und fps vorausgesetzt).
2. Falls gewünscht, im Reiter Aufnahmen die Teleoperation starten.
3. **Episode starten**, die Aufgabe einmal vorführen, **Episode speichern** (oder **verwerfen**, wenn
   etwas schiefging). Beim Speichern wird das Video kodiert, das dauert einen Moment.
4. Für jede weitere Vorführung Schritt 3 wiederholen. Für das Training braucht es viele Episoden
   (LeRobot nennt 50 als Standard).
5. **Dataset abschließen**. Erst dann sind alle Dateien vollständig geschrieben.

Verliert der Browser das Steuerrecht während einer Episode, wird sie **verworfen**: Eine abgebrochene
Vorführung wäre ein schlechtes Trainingsbeispiel. Das Dataset bleibt offen.

Ohne Kalibrierung geht es nicht: Das Dataset speichert Grad und Prozent wie LeRobot, nicht Rohwerte.

!!! note "Nur lokal"
    Gespeichert wird unter `recordings/datasets/<name>/` (nicht im Git). Die Web-App lädt nie etwas
    hoch. Die `repo_id` ist `lab/<name>`: nur ein Name, es gibt keinen Hub-Nutzer.

## 8. Datasets ansehen und benutzen

Die Tabelle im Reiter Datensätze zeigt alle Datasets mit Episoden, Frames, Dauer, Kameras und Größe.
**Befehle** zeigt, wie man ein Dataset ansieht und in Python lädt:

```bash
uv run lerobot-dataset-viz --repo-id lab/<name> --root recordings/datasets/<name> --episode-index 0
```

```python
from lerobot.datasets.lerobot_dataset import LeRobotDataset

ds = LeRobotDataset("lab/<name>", root="recordings/datasets/<name>")
print(ds.num_episodes, ds.num_frames, ds.features.keys())
frame = ds[0]  # observation.state, action, observation.images.front (3×H×W), task …
```

Aufbau auf der Platte (Format v3.0):

```
recordings/datasets/<name>/
  meta/info.json            fps, Merkmale, Anzahl Episoden und Frames
  meta/tasks.parquet        die Aufgaben
  meta/episodes/…           Beginn und Ende jeder Episode
  meta/stats.json           Mittelwerte und Streuung (für das Training)
  data/chunk-000/…parquet   Gelenkwerte und Aktionen je Frame
  videos/observation.images.<kamera>/chunk-000/…mp4   Bilder als Video (AV1)
```

??? info "Dasselbe „in fertig“: lerobot-record"
    ```bash
    uv run lerobot-record --robot.type=so101_follower --robot.port=/dev/ttyACM0 --robot.id=follower_1 \
      --robot.calibration_dir=calibration/robots/so_follower \
      --robot.cameras='{front: {type: opencv, index_or_path: /dev/video0, width: 640, height: 480, fps: 30}}' \
      --teleop.type=so101_leader --teleop.port=/dev/ttyACM1 --teleop.id=leader_1 \
      --teleop.calibration_dir=calibration/teleoperators/so_leader \
      --dataset.repo_id=lab/wuerfel --dataset.single_task="Den Würfel in die Schale legen" \
      --dataset.root=recordings/datasets/wuerfel --dataset.push_to_hub=false --dataset.num_episodes=5
    ```
    Wichtig: `lerobot-record` lädt in der Standardeinstellung zum Hugging Face Hub hoch. Im Labor
    immer `--dataset.push_to_hub=false` und ein lokales `--dataset.root` angeben. Außerdem schreibt
    LeRobot beim Verbinden Regelwerte ins EEPROM (Kapitel [Einrichtung](02-einrichtung.md)).
