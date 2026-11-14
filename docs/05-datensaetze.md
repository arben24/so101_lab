# Datensätze mit und ohne Kamera

Es gibt zwei Arten, Bewegungen festzuhalten:

| | Aufnahme (Trajektorie) | LeRobot-Dataset |
|---|---|---|
| Inhalt | Zeit und Rohposition je Gelenk | Beobachtung (Gelenke, Kamerabilder) und Aktion je Zeitschritt, in Episoden |
| Dateien | `recordings/trajectories/<name>.csv` und `.json` | Parquet-Tabellen und Videos (Format v3.0) |
| Wozu | ansehen, auswerten, abspielen, glätten | Policies trainieren (`lerobot-train`) |
| Kamera | nein | ja |
| Stand | fertig (Kapitel [Bewegungen](04-bewegungen.md) und hier) | folgt in Ausbaustufe 6b |

Dieses Kapitel beschreibt zuerst Aufnahmen in der Web-App: von Hand geführt, mit Teleoperation, die
Bibliothek und das Abspielen. Das Dateiformat und die Rechnung dahinter stehen in Kapitel
[Bewegungen](04-bewegungen.md).

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

## 6. LeRobot-Datasets mit Kamera

!!! note "Folgt in Ausbaustufe 6b"
    Aufnahmen mit Kamera als LeRobot-Dataset (Format v3.0) kommen, sobald eine Kamera am Laborrechner
    angeschlossen ist. Wichtig schon jetzt, wenn man `lerobot-record` direkt benutzt: Es lädt in der
    Standardeinstellung zum Hugging Face Hub hoch. Für den Laborbetrieb immer
    `--dataset.push_to_hub=false` und ein lokales `--dataset.root=recordings/datasets/<name>` angeben.
