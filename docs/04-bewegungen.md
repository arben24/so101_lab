# Bewegungen: aufzeichnen, speichern, abspielen

Eine aufgezeichnete Bewegung (*Trajektorie*) ist nichts Geheimnisvolles: **eine Tabelle mit
Zeitstempeln und einer Position je Gelenk.** Aufzeichnen heißt, Positionen in gleichem Abstand zu
lesen und zu speichern. Abspielen heißt, sie zur richtigen Zeit wieder als Ziele zu schreiben.

Praxis dazu: `scripts/06_record_trajectory.py` und `scripts/07_replay_trajectory.py`. Dasselbe mit
Oberfläche, Bibliothek und Teleoperation: Web-App, Reiter **Aufnahmen** (Kapitel [Datensätze](05-datensaetze.md)).

## Aufzeichnen

```bash
uv run scripts/06_record_trajectory.py --name winken
uv run scripts/06_record_trajectory.py --name winken --device leader --dauer 15 --rate 30
```

Was das Skript tut:

1. **Drehmoment aus.** Der Arm lässt sich von Hand führen. Ab jetzt trägt der Servo den Arm nicht mehr.
2. Nach Enter wird mit fester Abtastrate (Standard 30 Hz) die Position **aller Gelenke** gelesen, mit
   einem Sync-Read je Abtastung (Kapitel [Servos verstehen](03-servos.md)).
3. Die Aufnahme endet nach `--dauer` Sekunden (Standard 10) oder früher mit **Strg+C**. Beides speichert.
4. Die Dateien landen in `recordings/trajectories/`. Dieser Ordner ist nicht im Git.

!!! danger "Follower sackt zusammen"
    Ein **Follower** hat bei ausgeschaltetem Drehmoment keine Haltekraft und fällt unter seinem Gewicht
    nach unten. Zum Aufnehmen den Arm festhalten. Üblicher ist, den **Leader** zu führen
    (`--device leader`, eigener Eintrag in `arms.toml`, eigenes Netzteil): Er ist dafür gebaut. Die
    Aufnahme lässt sich dann auf dem Follower abspielen, denn beide haben dieselben Gelenknamen
    (vorausgesetzt, beide sind kalibriert).

### Wie der Takt entsteht

Das Skript wartet nicht einfach `sleep(1/rate)` zwischen zwei Messungen. Dann würde jede Messung ihre
eigene Dauer zusätzlich kosten und die Zeit würde davonlaufen. Stattdessen gibt es einen **festen
Zeitplan**: Messung `i` ist bei `t0 + i / rate` fällig. Das Skript schläft bis zu diesem Zeitpunkt, liest,
und rechnet den nächsten Zeitpunkt vom Start aus. Eine einzelne Verzögerung gleicht sich so wieder aus.

Schafft der Bus die Rate nicht, hängt die Aufnahme hinterher. Das Skript zeigt am Ende die **tatsächlich
erreichte Abtastrate** und warnt, wenn sie mehr als 10 % unter dem Ziel liegt. Dann eine kleinere `--rate`
wählen. Wie viele Abtastungen je Sekunde der Bus liefert, hängt von Adapter, Kabel und Rechner ab. Die
gemessene Rate steht deshalb in jeder Aufnahme.

## Das Dateiformat

Jede Aufnahme besteht aus **zwei Dateien** mit demselben Namen:

**`winken.csv`** — Zeit und Rohwerte, eine Zeile je Abtastung:

```csv
t_s,shoulder_pan,shoulder_lift,elbow_flex,wrist_flex,wrist_roll,gripper
0.0000,2048,2048,2048,2048,2048,2048
0.1001,2048,2048,2048,2048,2048,2048
0.2001,2048,2048,2048,2048,2048,2048
```

**`winken.json`** — Beschreibung:

```json
{
  "format_version": 1,
  "name": "winken",
  "created": "2026-11-13T14:36:00+00:00",
  "unit": "raw_steps",
  "joints": ["shoulder_pan", "shoulder_lift", "elbow_flex", "wrist_flex", "wrist_roll", "gripper"],
  "samples": 11,
  "duration_s": 1.0001,
  "measured_rate_hz": 10.0,
  "device": "follower",
  "device_type": "so101_follower",
  "calibration_id": "follower_1",
  "calibration_file": "calibration/robots/so_follower/follower_1.json",
  "homing_offsets": {"shoulder_pan": -312, "…": "je Gelenk der Offset im Servo beim Aufnehmen"},
  "calibration": {
    "shoulder_pan": {"id": 1, "drive_mode": 0, "homing_offset": -312, "range_min": 815, "range_max": 3240},
    "…": "je Gelenk ein Eintrag"
  },
  "target_rate_hz": 10.0,
  "source": "von Hand geführt (Drehmoment aus)",
  "recorded_with": "skript_06"
}
```

Warum **Rohwerte** (Servo-Schritte, 4096 je Umdrehung) statt Grad?

- Eine Aufnahme geht auch **ohne Kalibrierung**.
- Beim Abspielen entstehen exakt dieselben Sollwerte, ohne Umrechnungsfehler.
- Die Kalibrierung steht im JSON: als Name (`calibration_id`, `calibration_file`) und mit ihren Werten
  (`calibration`). Damit lässt sich die Aufnahme später auf einem **anderen** Arm abspielen (siehe unten).

Namen dürfen Buchstaben, Ziffern, `_` und `-` enthalten. Eine vorhandene Aufnahme wird nie stillschweigend
überschrieben (`--ueberschreiben` erlaubt es ausdrücklich).

Die CSV-Datei lässt sich mit jedem Werkzeug öffnen (Tabellenkalkulation, `pandas`, `numpy.loadtxt`). Das
ist die Grundlage der Übung, eine Bewegung zu glätten.

## Abspielen

```bash
uv run scripts/07_replay_trajectory.py --name winken --speed 0.3     # erster Versuch: langsam
uv run scripts/07_replay_trajectory.py --name winken
```

Das Skript prüft zuerst, **bevor** sich etwas bewegt:

- Gehören die Gelenke der Aufnahme zum Gerät? (Eine LeKiwi-Aufnahme passt nicht auf einen Follower.)
- Liegen die Werte innerhalb der Gelenkgrenzen der Kalibrierung? Werte knapp außerhalb (z. B. die
  Ruhestellung am Anschlag) werden auf die Grenzen begrenzt und als Hinweis ausgegeben. Liegt schon die
  **Startpose** mehr als 400 Schritt außerhalb, bricht das Skript ab: Die Aufnahme passt nicht zum Arm.
- Haben sich die Homing-Offsets seit der Aufnahme geändert (neu kalibriert)? Dann wird das herausgerechnet
  (Kapitel [Datensätze](05-datensaetze.md)).

Dann zeigt es, wie lange die Anfahrt und das Abspielen dauern, und fragt nach einer Bestätigung.

### Von einem Arm auf einen anderen

Zwei Arme haben für **dieselbe Stellung verschiedene Rohwerte**: Jeder Servo sitzt etwas anders auf
seiner Achse, deshalb hat jeder Arm seine eigene Kalibrierung. Eine Leader-Aufnahme mit Rohwerten
1:1 auf dem Follower abzuspielen, ergäbe eine verschobene Bewegung.

Deshalb rechnet das Abspielen um, wie LeRobot bei der Teleoperation: **Rohwert → Grad** mit der
Kalibrierung, mit der aufgenommen wurde, dann **Grad → Rohwert** mit der Kalibrierung des Arms, der
abspielt. Für ein Gelenk in Grad gilt (Kapitel [Einrichtung](02-einrichtung.md), Kalibrierung):

```
Grad = (Roh − Mitte) · 360 / 4095        Mitte = (range_min + range_max) / 2
```

Beispiel: Beim Leader liegt die Mitte von `elbow_flex` bei 2000, beim Follower bei 2300. Steht der
Leader auf 2500, sind das (2500 − 2000) · 360 / 4095 ≈ 44°. Der Follower bekommt
2300 + 44° · 4095 / 360 = 2800. Der Greifer läuft in Prozent zwischen `range_min` und `range_max`.

Was das Skript (und die Web-App) daraus macht:

| Aufnahme | Arm, der abspielt | Umrechnung |
|---|---|---|
| gleiche Kalibrierung | — | keine, Rohwerte direkt |
| Kalibrierung im JSON | kalibriert | über Grad/Prozent |
| ohne Kalibrierung (oder Arm unkalibriert) | — | keine: Rohwerte unverändert, passt nur bei gleich zusammengebauten Armen |

Die Zeile „Umrechnung: …“ in der Ausgabe sagt, welcher Fall vorliegt. LeKiwi-Gelenke heißen
`arm_shoulder_pan` usw.; eine SO-101-Aufnahme lässt sich trotzdem auf dem LeKiwi-Arm abspielen.

### Reihenfolge

1. **Grenzen zuerst:** Tempo- und Beschleunigungsgrenze werden gesetzt, bevor ein Ziel geschrieben wird.
2. **Drehmoment ohne Sprung:** Beim Einschalten wird die aktuelle Position als Ziel geschrieben. Sonst würde
   der Servo zu dem Wert fahren, der noch im Zielregister steht.
3. **Sanfte Anfahrt** zur Startpose, dann
4. **Abspielen** nach den Zeitstempeln.

### Die sanfte Anfahrt

Der Arm steht irgendwo, die Aufnahme beginnt woanders. Ein direkter Sprung zur Startpose wäre ruckartig,
und bei großem Abstand gefährlich. Deshalb interpoliert das Skript von der aktuellen Position zur
Startpose, mindestens 2 s lang und mit einem mittleren Tempo von höchstens etwa 300 Schritt/s (rund
26°/s). Der Verlauf ist nicht linear, sondern *smoothstep*:

```
s(x) = x² · (3 − 2x)     für x von 0 bis 1
```

Die Funktion startet und endet mit Geschwindigkeit 0: Der Arm fährt weich an und kommt weich an. In der
Mitte ist sie 1,5-mal so schnell wie der Mittelwert. Ein linearer Verlauf würde am Anfang schlagartig
losfahren.

### Tempofaktor und Grenzen

`--speed 0.5` halbiert das Tempo (die Zeitstempel werden gedehnt), `--speed 2` verdoppelt es.
Erlaubt ist 0,1 bis 3. Jede Zeile der Aufnahme geht durch `move_to` und wird begrenzt:

- auf die **kalibrierten Gelenkgrenzen** (ohne Kalibrierung: 0 bis 4095),
- auf den **größten Sprung** je Sollwert (`max_relative_target` in `arms.toml`, Standard 400 Schritt).

Der Servo folgt außerdem seinem eigenen Tempolimit (`max_velocity`, Standard 1000 Schritt/s, oder
`--tempo`). Ist die aufgenommene Bewegung schneller, hinkt der Arm hinterher: Das sieht man, wenn man
`--speed` hoch stellt.

### Abbrechen

**Strg+C** bricht jederzeit ab. Das Skript setzt dann sicher zurück: erst Räder stopp (nur LeKiwi),
dann je nach `safe_state` das Drehmoment aus oder die Stellung halten (Kapitel
[Einrichtung](02-einrichtung.md)). Mit `torque_off` sinkt der Arm in diesem Moment ab. Wer das nicht
will, stellt für das Abspielen `safe_state = "hold"` ein.

!!! warning "Vor dem Abspielen"
    Arbeitsbereich freihalten, Hand am Netzteil-Schalter, beim ersten Mal `--speed 0.3`. Wenn der
    Arm etwas Unerwartetes tut: Strg+C, im Zweifel Netzteil aus.

## Zum Ausprobieren

1. Eine kurze Bewegung aufnehmen (3 bis 5 s) und mit `--speed 0.5`, `1` und `2` abspielen. Was passiert
   bei 2?
2. Die CSV in einer Tabellenkalkulation öffnen und die Position eines Gelenks über der Zeit plotten.
   Wie gleichmäßig sind die Zeitstempel? (Spalte `t_s`: Differenz zweier Zeilen.)
3. Mit `--rate 10` und `--rate 60` aufnehmen. Welche Rate schafft dein Aufbau tatsächlich
   (`measured_rate_hz` im JSON)?
4. Die Aufnahme mit dem Leader machen und auf dem Follower abspielen.
5. **Glätten:** Das Rauschen der Rohwerte entfernen (gleitender Mittelwert über 5 Abtastungen) und die
   geglättete Bewegung abspielen. Wie verändern sich Start- und Endpunkt?

## Grenzen dieses Kapitels

Die Skripte hier nehmen **ohne Kamera** auf. Aufnehmen, Abspielen, Teleoperation und eine Bibliothek
mit Kurvenansicht gibt es in der Web-App (Kapitel [Datensätze](05-datensaetze.md)). Aufnahmen mit
Kamera als LeRobot-Dataset folgen in einer späteren Ausbaustufe.
