# Übungen

Sieben Übungen führen vom ersten Messwert bis zum Training einer Policy. Jede besteht aus einer **Vorlage** in
`exercises/` mit Lücken (`TODO`), die du ausfüllst, und einer **Musterlösung** in `exercises/loesungen/`.
Der Rest des Programms steht schon da und prüft dein Ergebnis.

```bash
uv run exercises/uebung_02_rohwert_grad.py     # Vorlage starten: Sie sagt, welche Funktion noch fehlt
# … Funktion schreiben, erneut starten, bis alles stimmt
uv run exercises/loesungen/uebung_02_rohwert_grad.py    # Musterlösung zum Vergleichen
```

Solange eine Funktion fehlt, meldet das Programm *„Noch zu lösen: …“* mit einem Hinweis und endet mit dem
Exit-Code 2. Es passiert dabei nichts am Arm.

| Nr. | Thema | Niveau | Richtwert | Hardware | Vorher lesen |
|---|---|---|---|---|---|
| 1 | [Messwerte lesen und bewerten](#uebung-1) | Einstieg | 20 min | Arm oder `demo` (nur lesen) | Kapitel 03, „Zustand und Fehler“ |
| 2 | [Rohwert ↔ Grad selbst rechnen](#uebung-2) | Einstieg | 20 min | keine | Kapitel 03, „Positionswerte“ |
| 3 | [P-Anteil und Sprungantwort](#uebung-3) | Fortgeschritten | 40 min | **echter Arm**, schreibt EEPROM | Kapitel 03, „Regelparameter“ |
| 4 | [Eine Bewegung glätten](#uebung-4) | Fortgeschritten | 40 min | keine | Kapitel 04 |
| 5 | [Schutzfunktionen herausfordern](#uebung-5) | Einstieg | 20 min | `demo` | Kapitel 01, „Sicherheit“ |
| 6 | [Kinematik der LeKiwi-Basis](#uebung-6) | Fortgeschritten | 45 min | keine (Fahren optional) | Kapitel 08, Abschnitt 3 |
| 7 | [Dataset ansehen, Training vorbereiten](#uebung-7) | Fortgeschritten | 60 min + Training | Dataset nötig, GPU empfohlen | Kapitel 05, Abschnitte 7 bis 9 |

Die Richtwerte sind Schätzungen, nicht gemessen.

!!! warning "Sicherheit"
    Die Übungen 3 und 6 (optional) bewegen echte Hardware, Übung 3 verändert außerdem das EEPROM. Es gelten
    die Regeln aus Kapitel [Aufbau](01-aufbau.md): Hand am Netzteil, Arbeitsbereich frei, erst klein und langsam.
    Alles andere läuft mit dem simulierten Gerät `demo` ohne Hardware.

!!! note "Für Betreuer"
    Wer die Musterlösungen nicht ausgeben will, entfernt vor der Ausgabe den Ordner `exercises/loesungen/`.
    Die Tests (`tests/test_exercises.py`) brauchen ihn, laufen also nur im vollständigen Repo.

<a id="uebung-1"></a>
## Übung 1: Messwerte lesen und bewerten

**Ziel:** den Zustand der Servos lesen und entscheiden, ob etwas auffällig ist.

**Aufgabe:** Schreibe `find_problems()` in `exercises/uebung_01_messen.py`. Je Gelenk soll eine Textzeile
entstehen, wenn

- die Spannung mehr als 10 % von der Sollspannung abweicht,
- die Temperatur über der Grenze liegt (Standard 50 °C, ein Lehrwert),
- ein Fehlerbit gesetzt ist (`state.errors`).

```bash
uv run exercises/uebung_01_messen.py --device demo
uv run exercises/uebung_01_messen.py --device demo --soll-volt 7.4     # „falsches Netzteil“: muss auffallen
uv run exercises/uebung_01_messen.py --soll-volt 12                    # am echten Arm
```

**Prüfen:** Mit `demo` und Standardwerten steht dort „Alles im grünen Bereich“ (Exit-Code 0). Mit
`--soll-volt 7.4` meldet jedes Gelenk die Spannung (Exit-Code 1).

**Zum Nachdenken:**

1. In welchen Registern stehen Spannung, Temperatur und Fehlerbits? (Kapitel 03, Registertabelle)
2. Warum liest das Programm nur und schaltet nichts ein? Was würde ein Fehlerbit „Überlast“ bedeuten?
3. Welche Sollspannung gehört zu deinem Arm? Das steht im Platzhalter in Kapitel 01, den dein Betreuer ausfüllt.
   Die Grenzen 10 % und 50 °C sind Lehrwerte, keine Herstellerangaben.

<a id="uebung-2"></a>
## Übung 2: Rohwert ↔ Grad selbst rechnen

**Ziel:** verstehen, wie aus einem Servo-Rohwert ein Winkel wird.

**Aufgabe:** Schreibe `raw_to_degrees()` und `degrees_to_raw()` in `exercises/uebung_02_rohwert_grad.py`.
0° ist die Mitte des kalibrierten Bereichs. Das Programm liest `calibration/robots/so_follower/follower_1.json`
(oder eine andere Datei mit `--kalibrierung`), zeigt für jedes Gelenk den Bereich in Grad und vergleicht deine
Funktion mit der des Repos (`so101_lab.calibration.raw_to_joint`).

```bash
uv run exercises/uebung_02_rohwert_grad.py
uv run exercises/uebung_02_rohwert_grad.py --kalibrierung lekiwi_1 --typ lekiwi
```

**Prüfen:** am Ende steht „Größte Abweichung … 0.000000°“.

**Zum Nachdenken:**

1. Ein Schritt sind 360°/4096 ≈ 0,088°. Warum teilt LeRobot trotzdem durch **4095**? (Kapitel 03)
2. Warum hat `wrist_roll` den Bereich ±180°, die anderen Gelenke nicht? (Kapitel 01, Tabelle der Gelenke, und Kapitel 02, Kalibrierung)
3. Der Greifer taucht in der Tabelle nicht auf. Warum nicht?

<a id="uebung-3"></a>
## Übung 3: P-Anteil und Sprungantwort

**Ziel:** sehen, was der **P-Anteil** des Servo-Reglers bewirkt.

!!! danger "Das schreibt ins EEPROM"
    `p_coefficient` (Register 21) bleibt im Servo, auch nach dem Ausschalten. Das Programm schreibt den
    ursprünglichen Wert am Ende zurück, auch bei einem Fehler oder Strg+C. Merke dir den Wert trotzdem
    (er steht in der ersten Ausgabezeile). Der Arm fährt 300 Schritt (etwa 26°) hin und zurück, Gelenk freihalten.
    Die Obergrenze für P ist 48, ein Lehrwert.

**Aufgabe:** Schreibe `step_metrics()` in `exercises/uebung_03_p_anteil.py`. Aus den Messpunkten einer
Sprungantwort (Zeit, Position) berechnest du

- `settle_s`: ab wann die Position dauerhaft im Toleranzband um das Ziel bleibt,
- `overshoot`: wie weit sie über das Ziel hinausschießt,
- `final_error`: den Rest-Abstand am Ende.

Messen, Schreiben des P-Anteils und Zurückschreiben sind schon fertig. Das Programm prüft deine Funktion,
**bevor** es etwas bewegt.

```bash
uv run exercises/uebung_03_p_anteil.py --gelenk wrist_flex --device demo          # Ablauf ohne Hardware
uv run exercises/uebung_03_p_anteil.py --gelenk wrist_flex --p 8 16 32            # am echten Arm
```

!!! note "Die Simulation kennt keinen Regler"
    Mit `--device demo` kommt für jeden P-Wert dasselbe heraus, weil die simulierten Servos kein P haben. Das
    ist ein guter Test für den Ablauf (P wird geschrieben und am Ende zurückgesetzt), aber nicht für die Physik.

**Prüfen (am Arm):** Die Tabelle zeigt je P-Wert Einschwingzeit, Überschwingen und Restfehler. Was du
siehst, hängt von Gelenk, Last und Spiel ab. Aus der Theorie: Ein größeres P reagiert schneller, kann aber
überschwingen. Ob das bei deinem Arm und diesen Werten so aussieht, ist die eigentliche Frage der Übung.
Ein Ergebnis aus der Praxis liegt dieser Doku nicht vor.

**Zum Nachdenken:**

1. Warum fährt das Programm vor jedem Sprung erst an den Startpunkt zurück?
2. LeRobot setzt P = 16, die Werkseinstellung ist 32 (Kapitel 03). Welchen Wert würdest du für einen
   Arm wählen, der etwas tragen soll, und warum?
3. Wozu ist der **D-Anteil** (Register 22) da, und was passiert, wenn du ihn nicht mitverstellst?

<a id="uebung-4"></a>
## Übung 4: Eine Bewegung glätten

**Ziel:** eine aufgezeichnete Bewegung mit einem einfachen Filter bearbeiten.

**Aufgabe:** Schreibe in `exercises/uebung_04_glaetten.py`

- `moving_average()`: gleitender Mittelwert über `window` Werte (ungerade). Der erste und der letzte Wert bleiben
  unverändert, damit sich Startpose und Endstellung nicht verschieben.
- `roughness()`: ein Maß für die Ruckeligkeit, der Mittelwert der Beträge der zweiten Differenz
  `v[i-1] − 2·v[i] + v[i+1]`.

```bash
uv run exercises/uebung_04_glaetten.py --beispiel                      # legt eine wackelige Beispielaufnahme an
uv run exercises/uebung_04_glaetten.py --name winken --fenster 9       # eine eigene Aufnahme (Kapitel 04)
uv run scripts/07_replay_trajectory.py --name beispiel_wackelig_glatt --speed 0.3 --device demo
```

**Prüfen:** Die Rauheit sinkt deutlich (bei der Beispielaufnahme mit Fenster 5 von etwa 28 auf etwa 5). Die
größte Abweichung zum Original bleibt klein.

**Zum Nachdenken:**

1. Probiere `--fenster 3`, `9`, `31`. Was passiert mit den Gipfeln der Bewegung bei einem sehr großen Fenster?
2. Die Aufnahme hat feste Abstände von 1/30 s. Was ginge schief, wenn die Zeitstempel ungleichmäßig wären?
3. Eine geglättete Aufnahme wird mit Skript 07 abgespielt, nicht mit dem Original. Was ändert sich für den
   Servo, und was gewinnst du damit?

<a id="uebung-5"></a>
## Übung 5: Schutzfunktionen herausfordern

**Ziel:** die Sicherheitsschichten des Repos kennenlernen, indem man sie auslöst. Erst raten, dann ausprobieren.

**Aufgabe:** Das Programm macht vier Versuche und fragt vor jedem nach deinem **Tipp**. Zwei Versuche
(1 und 2) sind fertig. Schreibe `experiment_3()` und `experiment_4()` in
`exercises/uebung_05_schutzfunktionen.py`: Schreibe `p_coefficient` und danach `acceleration = 9999` mit
`write_ram_register()`. Die Hilfsfunktion `try_it()` fängt die Ablehnung ab.

```bash
uv run exercises/uebung_05_schutzfunktionen.py --device demo
```

Das Drehmoment bleibt aus: Am echten Arm bewegt sich nichts, die Versuche zeigen nur, was **geschrieben**
würde.

**Prüfen:** Vergleiche deine Tipps mit den Ergebnissen. Welche Schicht hat jeweils eingegriffen?

| Versuch | Was passiert | Schicht |
|---|---|---|
| 1 Ziel hinter der Gelenkgrenze | gesendet wird die Grenze | `Arm.move_to` begrenzt auf die kalibrierten Grenzen |
| 2 Sprung von 2000 Schritt | gesendet wird höchstens `max_relative_target` (400) weiter | `Arm.move_to` begrenzt den Sprung |
| 3 `p_coefficient` über den RAM-Weg | abgelehnt, nicht freigegeben | Whitelist in `safety.write_ram_register` |
| 4 `acceleration = 9999` | abgelehnt, Wert außerhalb des Registerbereichs | Bereichsprüfung des Backends |

Ohne Kalibrierung (`demo`) ist die Gelenkgrenze 4095. Mit einer Kalibrierdatei gilt der kalibrierte Bereich.

**Zum Nachdenken:**

1. Warum steht `torque_enable` auf der Whitelist, aber `p_coefficient` nicht?
2. Welche Schicht würde dich vor einem Tippfehler im Skript schützen, welche vor einem falschen Netzteil?
   (Kapitel 09, „Sicherheitsschichten“)
3. Baue einen fünften Versuch: Schreibe `homing_offset` mit `write_register`. Was erwartest du?

<a id="uebung-6"></a>
## Übung 6: Kinematik der LeKiwi-Basis

**Ziel:** den Weg vom Fahrbefehl zum Radtempo selbst rechnen (Kapitel 08, Abschnitt 3).

**Aufgabe:** Schreibe in `exercises/uebung_06_kinematik.py` vier Funktionen:

1. `wheel_speeds(x, y, theta)`: Radtempi in Schritt/s, ohne Begrenzung.
2. `limit_speeds(speeds, max_raw)`: ist ein Rad zu schnell, alle drei im gleichen Verhältnis verkleinern.
3. `circle_command(radius_m, speed_ms)`: der Fahrbefehl für einen Kreis, bei dem die Basis immer nach vorn zeigt.
4. `max_forward_speed(max_raw)`: die größte Vorwärtsgeschwindigkeit, bei der kein Rad über `max_raw` kommt.

```bash
uv run exercises/uebung_06_kinematik.py
```

**Prüfen:** Das Programm vergleicht deine Räder mit `so101_lab.kinematics` und schreibt am Ende „Alles stimmt.“

**Zum Nachdenken:**

1. Bei reinem Seitwärtsfahren (nur y) stehen die Räder nicht still, aber sie drehen verschieden schnell. Wie
   verhalten sich die Beträge zueinander?
2. Warum ist `max_forward_speed()` kleiner als die schnellste Stufe von LeRobot (0,3 m/s)? Was macht die
   Software dann? (Kapitel 08)
3. **Optional, mit Hardware:** Die Basis aufbocken, dann
   `uv run scripts/09_drive_base.py --x 0.1 --theta 11.5 --dauer 3` (der Kreis von oben, r ≈ 0,5 m).
   Aufgebockt kann man nur die Raddrehung prüfen, nicht die Bahn. Das Fahren ist an einer echten LeKiwi noch
   nicht geprüft (Kapitel 08).

<a id="uebung-7"></a>
## Übung 7: Dataset ansehen und Training vorbereiten

**Ziel:** ein LeRobot-Dataset verstehen und den Befehl zum Training bauen.

**Voraussetzung:** ein Dataset aus Kapitel 05, Abschnitt 7, am besten mit mindestens 10 Episoden. Ohne Hardware
geht die Übung auch mit dem Gerät `demo` und seinem Testbild. Die Daten sind dann flach und zum Lernen
unbrauchbar, aber der Ablauf stimmt.

**Aufgabe:** Schreibe in `exercises/uebung_07_dataset_training.py`

- `describe_dataset()`: liest `meta/info.json` und liefert Episoden, Frames, fps, Kameras und die Namen von
  Zustand und Aktion,
- `training_command()`: baut die Argumentliste für `uv run --group training lerobot-train …`
  (ACT-Policy, lokal, ohne Hub).

```bash
uv run exercises/uebung_07_dataset_training.py --name wuerfel_greifen        # beschreibt das Dataset, gibt den Befehl aus
uv run lerobot-dataset-viz --repo-id lab/wuerfel_greifen --root recordings/datasets/wuerfel_greifen --episode-index 0
uv sync --group training                                                     # einmal, bringt accelerate mit
uv run exercises/uebung_07_dataset_training.py --name wuerfel_greifen --steps 2000 --device cuda --starten
```

**Prüfen:** Der ausgegebene Befehl enthält `--policy.push_to_hub=false` und dein `--dataset.root`. Beim Training
sinkt der Wert `loss` in der Ausgabe (im Test von 72,6 auf 47,0 in drei Schritten). Am Ende liegt der Checkpoint in
`outputs/train/<name>/checkpoints/last/pretrained_model/`.

!!! warning "Training ist hier nur im Kleinen geprüft"
    Ein Lauf mit 3 Schritten auf einem synthetischen Dataset ist getestet (Kapitel 05, Abschnitt 9). Ein echtes
    Training mit Aufnahmen vom Arm, seine Dauer und die Qualität der Policy sind **nicht** geprüft. Auf der CPU
    (Standard) ist Training sehr langsam. Besser: `uv sync --no-group cpu --group cuda --group training`.

**Zum Nachdenken:**

1. Was ist der Unterschied zwischen `observation.state` und `action`, und woher kommt die Aktion bei einer
   Aufnahme mit Teleoperation?
2. Warum sind 4 Episoden zu wenig? Was lernt die Policy aus einer einzigen Vorführung?
3. Warum steht in dem Befehl `--policy.push_to_hub=false`, und was würde ohne passieren?
