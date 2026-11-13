# Aufbau: Mechanik, Servos, Bus, Stromversorgung

## Der Arm im Überblick

Der SO-101 ist ein 3D-gedruckter Arm mit **sechs Gelenken**. Jedes Gelenk wird von einem
Feetech-Servo **STS3215** bewegt. Alle sechs hängen an **einem gemeinsamen Datenkabel** (dem Bus).
Ein USB-Adapter verbindet diesen Bus mit dem Rechner.

Es gibt zwei Sorten Arm, die gleich aussehen, aber verschieden eingesetzt werden:

| | Follower | Leader |
|---|---|---|
| Aufgabe | führt Bewegungen aus (Folgearm) | wird von Hand geführt und liefert die Sollbewegung |
| Drehmoment | an, der Arm trägt sich selbst | meist aus, leichtgängig |
| Servo-Variante und Spannung | siehe Platzhalter unten | siehe Platzhalter unten |
| Netzteil | **eigenes Netzteil** | **anderes Netzteil** |

!!! danger "Leader und Follower haben unterschiedliche Netzteile"
    Die Follower benutzen die 12-V-Variante des STS3215, die Leader eine Variante mit niedrigerer
    Spannung und eigener Übersetzung. **Ein Netzteil am falschen Arm kann die Servos zerstören.**
    Netzteile und Arme beschriften, Spannung vor dem Anschließen mit einem Messgerät kontrollieren.

### Platzhalter: Netzteil- und Motorvarianten (vom Betreuer auszufüllen)

!!! warning "TODO – hier fehlen die Angaben des Betreuers"
    Diese Werte stehen bewusst nicht in der Doku, weil sie vom konkreten Aufbau abhängen. Bitte
    die Tabelle für die vorhandene Hardware ausfüllen. Die Software prüft später die Busspannung
    gegen diese Angaben.

| | Follower | Leader |
|---|---|---|
| Servo-Typ (Bestellbezeichnung) | **[AUSFÜLLEN]** | **[AUSFÜLLEN]** |
| Nennspannung der Servos | **[AUSFÜLLEN]** V | **[AUSFÜLLEN]** V |
| Netzteil (Typ, Spannung, größter Strom) | **[AUSFÜLLEN]** | **[AUSFÜLLEN]** |
| Stecker am Netzteil und am Adapter | **[AUSFÜLLEN]** | **[AUSFÜLLEN]** |
| Übersetzung der Servos je Gelenk | **[AUSFÜLLEN]** | **[AUSFÜLLEN]** |
| Kennzeichnung (Aufkleber, Farbe) | **[AUSFÜLLEN]** | **[AUSFÜLLEN]** |

Ob die gemessene Spannung passt, zeigt jeder Servo selbst: `uv run so101-lab state follower` listet
die Spannung am Servo (Spalte `U/V`). Ein Wert weit neben der Nennspannung bedeutet: Netzteil prüfen,
nicht weiterarbeiten.

## Gelenke und Motor-IDs

Jeder Servo am Bus hat eine **ID** (0 bis 253). Software und Hardware müssen sich über sie einig sein:
Gelenk „Ellbogen“ ist ID 3, nicht „der dritte Servo im Kabel“. Die Namen und IDs hier stammen aus dem
Quellcode von LeRobot 0.7.0 (`so_follower.py`, `so_leader.py`, `lekiwi.py`). Leader und Follower haben
dieselben Namen und IDs.

### SO-101 (Follower und Leader)

| ID | Gelenk | Bewegung | Einheit in LeRobot |
|---|---|---|---|
| 1 | `shoulder_pan` | Schulter drehen (Basis) | Grad |
| 2 | `shoulder_lift` | Schulter heben | Grad |
| 3 | `elbow_flex` | Ellbogen beugen | Grad |
| 4 | `wrist_flex` | Handgelenk beugen | Grad |
| 5 | `wrist_roll` | Handgelenk drehen (volle Umdrehung) | Grad |
| 6 | `gripper` | Greifer | 0 bis 100 (Prozent geöffnet) |

`wrist_roll` darf sich beliebig weit drehen und bekommt deshalb bei der Kalibrierung den vollen
Encoderbereich. Alle anderen Gelenke haben mechanische Anschläge, die bei der Kalibrierung gemessen werden.

### LeKiwi (Arm plus Fahrbasis)

Alle neun Motoren hängen an **einem** Bus. Die Arm-Gelenke heißen mit dem Präfix `arm_`. Die drei
Räder laufen im Geschwindigkeitsmodus.

| ID | Motor | Betriebsart |
|---|---|---|
| 1 | `arm_shoulder_pan` | Position |
| 2 | `arm_shoulder_lift` | Position |
| 3 | `arm_elbow_flex` | Position |
| 4 | `arm_wrist_flex` | Position |
| 5 | `arm_wrist_roll` | Position |
| 6 | `arm_gripper` | Position |
| 7 | `base_left_wheel` | Geschwindigkeit |
| 8 | `base_back_wheel` | Geschwindigkeit |
| 9 | `base_right_wheel` | Geschwindigkeit |

Die Basis wird in einem späteren Kapitel behandelt (LeKiwi).

## Der Servo STS3215

Ein Servo ist ein Motor mit Getriebe, einem Winkelsensor und einer kleinen Elektronik. Die Elektronik
regelt die Position selbst: Du sagst „fahre auf 2048“, der Servo regelt dorthin. Aus dem Datenblatt der
7,4-V-Variante STS3215-C001 (Auszug in `firmware/esp32/doku/sts3215/README.md`):

| Größe | Wert (C001, 7,4 V) |
|---|---|
| Winkelsensor | 12-Bit-Magnetencoder, 360°, 4096 Schritte |
| Auflösung | 0,088° je Schritt |
| Getriebe | 1 : 345 |
| Getriebespiel | höchstens 0,5° |
| Regler | PID |
| Rückmeldung | Position, Tempo, Last, Spannung, Strom, Temperatur |
| Schnittstelle | serieller Halbduplex-Bus, 8 Datenbits, 1 Stoppbit, keine Parität |
| Baudrate | 38 400 bit/s bis 1 Mbit/s, ab Werk 1 Mbit/s |

!!! note "Datenblatt gilt für die 7,4-V-Variante"
    Spannung, Drehmoment und Strom hängen von der Variante ab. Die Werte der 12-V-Follower und der
    Leader-Servos stehen im Platzhalter oben, nicht in dieser Tabelle.

Schutzfunktionen laut Datenblatt (7,4-V-Variante):

| Schutz | Wann | Was passiert |
|---|---|---|
| Überlast | über 80 % des Blockiermoments länger als 2 s | Drehmoment wird begrenzt |
| Überstrom | über 2 A länger als 2 s | Ausgang aus |
| Spannung | außerhalb des erlaubten Bereichs | Schutz, löst sich bei passender Spannung |
| Temperatur | über 70 °C | Drehmoment aus |

Ob und wann ein Schutz angesprochen hat, steht im Register `status` (Kapitel [Servos verstehen](03-servos.md)).

## Der Bus

Alle Servos hängen **in Reihe** am selben dreipoligen Kabel: Masse, Versorgung, Signal. Das Signal
läuft auf **einer** Leitung in beide Richtungen (Halbduplex). Deshalb darf zu jedem Zeitpunkt nur einer
senden: Der Rechner schickt eine Anfrage mit der ID des gewünschten Servos, nur dieser antwortet.

```
 Rechner ── USB ── Adapter ── Servo 1 ─ Servo 2 ─ Servo 3 ─ Servo 4 ─ Servo 5 ─ Servo 6
                      └──────── ein gemeinsames Kabel (GND, Versorgung, Signal) ────────┘
```

Daraus folgt:

- **IDs müssen eindeutig sein.** Neue Servos haben ab Werk alle die ID 1. Zwei Servos mit gleicher ID
  antworten gleichzeitig, die Antworten überlagern sich. Deshalb werden die IDs einzeln vergeben
  (Kapitel [Einrichtung](02-einrichtung.md)).
- **Alle Servos am Bus nutzen dieselbe Baudrate** (1 Mbit/s).
- **Die Servos werden nicht über USB versorgt**, sondern über ihr eigenes Netzteil (Anschluss siehe
  Platzhalter oben). Der Adapter wird auch ohne Servo-Netzteil als Port erkannt; Servos antworten aber
  erst, wenn ihr Netzteil eingeschaltet ist.

Der Adapter dieser Laborausstattung meldet sich mit der USB-Kennung `1a86:55d3` (QinHeng „USB Single
Serial“) und erscheint unter Linux als `/dev/ttyACM0`.

## Sicherheit im Labor

!!! danger "Das gilt immer"
    - **Quetschgefahr.** Die Servos sind stark genug, einen Finger zwischen zwei Armteilen einzuklemmen.
      Nie zwischen die Gelenke greifen, solange das Netzteil an ist.
    - **Arbeitsbereich freihalten.** Um den Arm herum eine Armlänge Platz lassen. Keine Kabel, Tassen
      oder Hände im Schwenkbereich.
    - **Der Arm sinkt ab**, sobald das Drehmoment ausgeschaltet wird (auch am Ende jedes Skripts). Mit
      einer Hand abstützen, bis du die Reaktion kennst.
    - **Hand am Netzteil-Schalter.** Das ist der Not-Aus: Netzteil aus, dann sind alle Servos kraftlos.
    - **Richtiges Netzteil am richtigen Arm.** Leader und Follower nie vertauschen, Spannung vor dem
      Anschließen kontrollieren (Platzhalter oben).
    - **Nie unter Spannung umstecken.** Servo-Kabel nur bei ausgeschaltetem Netzteil stecken oder abziehen.
    - **Nicht gegen Anschläge fahren.** Erst kalibrieren (Kapitel [Einrichtung](02-einrichtung.md)),
      dann sind die Gelenkgrenzen bekannt. Bis dahin Bewegungen klein halten.
    - **Erstes Mal langsam.** Neue Abläufe zuerst mit kleinem Tempo und kleinen Wegen ausprobieren.

Was die Software dafür tut:

- Geschwindigkeits- und Beschleunigungsgrenzen werden **vor** der ersten Zielposition gesetzt.
- Zielpositionen werden auf die kalibrierten Gelenkgrenzen und auf einen größten Sprung begrenzt
  (`max_relative_target`, Standard 400 Schritt, etwa 35°).
- Beim Einschalten des Drehmoments wird die aktuelle Position als Ziel gesetzt, damit nichts springt.
- Jedes Skript setzt beim Beenden oder bei einem Fehler sicher zurück (Strg+C eingeschlossen).
  Was dabei passiert, steht in `config/arms.toml` unter `safe_state`: `torque_off` (Standard, Arm sinkt
  ab) oder `hold` (Stellung halten).

Diese Software ersetzt **nicht** den Netzschalter. Ein Programmfehler kann nie ausgeschlossen werden.
