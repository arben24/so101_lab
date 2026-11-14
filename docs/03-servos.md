# Servos verstehen: Register, Positionen, EEPROM und RAM

Von außen ist ein Servo ein **kleiner Speicher mit Motor**. Jede Zahl, die man lesen oder schreiben kann,
liegt an einer festen **Adresse**. „Position fahren“, „Temperatur lesen“ und „ID ändern“ sind nichts
anderes als Lesen und Schreiben an bestimmten Adressen. Dieses Kapitel erklärt diese Speichertabelle.

Praxis dazu: `uv run scripts/02_read_servo.py` zeigt alle Register eines Servos, `04_write_param.py`
schreibt freigegebene RAM-Register. In der Web-App zeigt der Reiter **Register** dasselbe für jedes Gelenk
und der Reiter **Einzelservo** die Live-Werte samt Schieberegler.

<a class="lab-link" href="../index.html#register">Register-Explorer in der Web-App</a>
<a class="lab-link" href="../index.html#servo">Einzelservo in der Web-App</a>

## Register

Ein Register ist ein oder zwei Bytes groß. **Zweibyte-Werte** liegen mit dem niedrigen Byte an der
kleineren Adresse (*Little Endian*): `Present_Position` an Adresse 56 und 57 mit dem Wert 1304 ist
`0x18 0x05` (`0x18 + 0x05 · 256`).

**Vorzeichen** speichert der STS3215 nicht im Zweierkomplement, sondern als **Betrag mit einem
Vorzeichenbit**. Beispiel `goal_velocity` = −300: Betrag 300, Bit 15 gesetzt → `300 + 2¹⁵ = 33068 =
0x812C`, auf dem Bus als `2C 81`. Der Treiber (rustypot) übernimmt die Umrechnung. Welches Bit das
Vorzeichen trägt, steht in der Tabelle der Feetech-Dokumentation.

### Die Registertabelle des STS3215

Namen, Adressen und Größen stammen aus dem Treiber rustypot, den auch LeRobot benutzt. Einheiten und
Bedeutung stammen aus der Feetech-Speichertabelle V3.7
(`firmware/esp32/doku/sts3215/STS3215_Speichertabelle_V3.7.xls`). Die Tabelle wird im Code aus
rustypot erzeugt und nicht abgeschrieben (`so101-lab` prüft in den Tests, dass sie stimmt).

| Adr. | Name | Bytes | Speicher | Zugriff | Einheit | Bedeutung |
|---|---|---|---|---|---|---|
| 0 | `firmware_major_version` | 1 | EEPROM | R | – | Firmware, Hauptversion |
| 1 | `firmware_minor_version` | 1 | EEPROM | R | – | Firmware, Nebenversion |
| 3 | `model_number` | 2 | EEPROM | R | – | Modellnummer, 777 = STS3215 (Feetech nennt Adresse 3 und 4 „Servo-Version“, 9.3) |
| 5 | `id` | 1 | EEPROM | RW | – | Adresse am Bus, 0–253, nie doppelt (254 = Broadcast). Ändern: change_id() |
| 6 | `baud_rate` | 1 | EEPROM | RW | Code 0–7 | Baudrate als Code. Ändern: change_baudrate() |
| 7 | `return_delay_time` | 1 | EEPROM | RW | 2 µs | Wartezeit des Servos vor der Antwort |
| 8 | `response_status_level` | 1 | EEPROM | RW | – | 0: nur Lesen und Ping werden beantwortet, 1: jeder Befehl |
| 9 | `min_position_limit` | 2 | EEPROM | RW | Schritt | untere Bewegungsgrenze (kleiner als die obere) |
| 11 | `max_position_limit` | 2 | EEPROM | RW | Schritt | obere Bewegungsgrenze |
| 13 | `max_temperature_limit` | 1 | EEPROM | RW | °C | Temperaturgrenze |
| 14 | `max_voltage_limit` | 1 | EEPROM | RW | 0,1 V | obere Spannungsgrenze |
| 15 | `min_voltage_limit` | 1 | EEPROM | RW | 0,1 V | untere Spannungsgrenze |
| 16 | `max_torque_limit` | 2 | EEPROM | RW | 0,1 % | größtes Drehmoment, wird beim Einschalten nach torque_limit kopiert |
| 18 | `phase` | 1 | EEPROM | RW | Bits | Sonderfunktionen, nur mit Grund ändern |
| 19 | `unloading_condition` | 1 | EEPROM | RW | Bitmaske | Schutz abschalten bei: Bit 0 Spannung, 1 Sensor, 2 Temperatur, 3 Strom, 4 Winkel, 5 Überlast |
| 20 | `led_alarm_condition` | 1 | EEPROM | RW | Bitmaske | LED blinkt bei: Bit 0 Spannung, 1 Sensor, 2 Temperatur, 3 Strom, 4 Winkel, 5 Überlast |
| 21 | `p_coefficient` | 1 | EEPROM | RW | – | Lageregelung, P-Anteil |
| 22 | `d_coefficient` | 1 | EEPROM | RW | – | Lageregelung, D-Anteil |
| 23 | `i_coefficient` | 1 | EEPROM | RW | – | Lageregelung, I-Anteil |
| 24 | `minimum_startup_force` | 2 | EEPROM | RW | 0,1 % | kleinstes Anlaufmoment |
| 26 | `cw_dead_zone` | 1 | EEPROM | RW | Schritt | Unempfindlichkeitszone im Uhrzeigersinn |
| 27 | `ccw_dead_zone` | 1 | EEPROM | RW | Schritt | Unempfindlichkeitszone gegen den Uhrzeigersinn |
| 28 | `protection_current` | 2 | EEPROM | RW | 6,5 mA | Stromgrenze |
| 30 | `angular_resolution` | 1 | EEPROM | RW | – | Vergrößerungsfaktor der Winkelauflösung (1–3) |
| 31 | `homing_offset` | 2 | EEPROM | RW | Schritt | Positionskorrektur ±2047 (Betrag, Vorzeichen in Bit 11) |
| 33 | `operating_mode` | 1 | EEPROM | RW | – | 0 Position, 1 Geschwindigkeit, 2 PWM, 3 Schritt |
| 34 | `protective_torque` | 1 | EEPROM | RW | 1 % | Moment nach dem Überlastschutz |
| 35 | `protection_time` | 1 | EEPROM | RW | 10 ms | Dauer der Überlast, bis der Schutz greift |
| 36 | `overload_torque` | 1 | EEPROM | RW | 1 % | Momentschwelle, ab der die Überlast gezählt wird |
| 37 | `speed_closed_loop_p_coefficient` | 1 | EEPROM | RW | – | Geschwindigkeitsregelung (Modus 1), P-Anteil |
| 38 | `over_current_protection_time` | 1 | EEPROM | RW | 10 ms | Dauer des Überstroms, bis der Schutz greift |
| 39 | `velocity_closed_loop_i_coefficient` | 1 | EEPROM | RW | 0,1 | Geschwindigkeitsregelung (Modus 1), I-Anteil |
| 40 | `torque_enable` | 1 | RAM | RW | – | 0 aus, 1 an. Der Wert 128 verstellt die Nullstellung: nie schreiben |
| 41 | `acceleration` | 1 | RAM | RW | 100 Schritt/s² | Beschleunigung und Bremsen, 0–254 |
| 42 | `goal_position` | 2 | RAM | RW | Schritt | Zielposition (Positionsmodus) |
| 44 | `goal_time` | 2 | RAM | RW | 0,1 % | Laufzeit im PWM-Modus (Modus 2) |
| 46 | `goal_velocity` | 2 | RAM | RW | Schritt/s | Tempo, höchstens 3400: Fahrtempo zum Ziel (Positionsmodus) bzw. Solltempo (Geschwindigkeitsmodus) |
| 48 | `torque_limit` | 2 | RAM | RW | 0,1 % | aktuelle Momentgrenze, 1000 = 100 % |
| 55 | `lock` | 1 | RAM | RW | – | 0: EEPROM-Änderungen bleiben nach dem Ausschalten erhalten, 1: gehen verloren |
| 56 | `present_position` | 2 | RAM | R | Schritt | aktuelle Position, 4096 Schritt = 360° |
| 58 | `present_velocity` | 2 | RAM | R | Schritt/s | aktuelles Tempo |
| 60 | `present_load` | 2 | RAM | R | 0,1 % | Tastverhältnis der Motoransteuerung, Vorzeichen = Richtung |
| 62 | `present_voltage` | 1 | RAM | R | 0,1 V | Versorgungsspannung am Servo |
| 63 | `present_temperature` | 1 | RAM | R | °C | Temperatur im Servo |
| 65 | `status` | 1 | RAM | R | Bitmaske | Fehler, 1 = aufgetreten: Bit 0 Spannung, 1 Sensor, 2 Temperatur, 3 Strom, 4 Winkel, 5 Überlast |
| 66 | `moving` | 1 | RAM | R | – | 1 solange der Servo sich bewegt |
| 69 | `present_current` | 2 | RAM | R | 6,5 mA | aktueller Strom |
| 80 | `moving_velocity_threshold` | 1 | nicht in V3.7 | RW | – | in der Feetech-Tabelle V3.7 nicht beschrieben |
| 81 | `dts` | 1 | nicht in V3.7 | RW | – | in der Feetech-Tabelle V3.7 nicht beschrieben |
| 82 | `velocity_unit_factor` | 1 | nicht in V3.7 | RW | – | in der Feetech-Tabelle V3.7 nicht beschrieben |
| 83 | `hts` | 1 | nicht in V3.7 | RW | – | in der Feetech-Tabelle V3.7 nicht beschrieben |
| 84 | `maximum_velocity_limit` | 1 | nicht in V3.7 | RW | – | in der Feetech-Tabelle V3.7 nicht beschrieben |
| 85 | `maximum_acceleration` | 1 | nicht in V3.7 | RW | – | in der Feetech-Tabelle V3.7 nicht beschrieben |
| 86 | `acceleration_multiplier` | 1 | nicht in V3.7 | RW | – | in der Feetech-Tabelle V3.7 nicht beschrieben |

!!! note "Hinweise zur Tabelle"
    - Die Speichertabelle V3.7 beschreibt die **7,4-V-Variante**. Startwerte und Spannungsgrenzen der
      12-V-Variante können abweichen.
    - Die Register ab Adresse 80 stehen in rustypot, aber nicht in der Feetech-Tabelle V3.7. Sie werden
      hier nur gelesen, nie geschrieben.
    - Bei `torque_limit` nennt die Einheiten-Spalte der Feetech-Tabelle „1 %“. Wertebereich 0 bis 1000 und
      Startwert 1000 passen aber zu 0,1 % (wie bei `max_torque_limit`). Hier gilt 0,1 %.
    - **Baudraten-Code (Register 6):** Die Werte 0 bis 4 (1 000 000, 500 000, 250 000, 128 000, 115 200)
      stimmen in rustypot und Feetech überein. Für 5 bis 7 widersprechen sie sich (rustypot 57 600,
      38 400, 19 200; Feetech V3.7 76 800, 57 600, 38 400). Dieses Repo benutzt die Zuordnung von rustypot,
      denn rustypot schreibt tatsächlich. Im Betrieb mit 1 Mbit/s spielt das keine Rolle.

## Positionswerte

Der Winkelsensor teilt eine Umdrehung in **4096 Schritte**. Ein Schritt sind 360° / 4096 ≈ 0,088°.

| Rohwert | Winkel |
|---|---|
| 0 | 0° |
| 1024 | 90° |
| 2048 | 180° (Mittelstellung) |
| 3072 | 270° |
| 4095 | knapp 360° |

Ein Rohwert allein sagt noch nichts über die Armstellung, denn die Nullstellung hängt vom Einbau ab. Deshalb
gibt es:

- den **Homing-Offset** (Register 31): Der Servo zieht ihn vom Sensorwert ab. Bei diesem Treiber gilt
  `Present_Position = tatsächliche Position − Homing_Offset`. Der Wertebereich ist ±2047.
- die **Bewegungsgrenzen** (Register 9 und 11): kleinster und größter erlaubter Wert des Bewegungsbereichs.
- die **Kalibrierdatei** (Kapitel [Einrichtung](02-einrichtung.md)): Sie hält Offset und Bereich je Gelenk fest.

### Rohwert → Grad und Prozent

LeRobot rechnet mit der Kalibrierung so um (`so101-lab` macht dasselbe und prüft es im Test gegen LeRobot):

- **Grad:** `(roh − (range_min + range_max) / 2) · 360 / 4095`. Die Mitte des kalibrierten Bereichs ist 0°.
- **Greifer:** linear zwischen `range_min` (0) und `range_max` (100).

Ohne Kalibrierung zeigt `so101-lab state` nur Rohwerte, und Skript 02 rechnet roh · 360 / 4096.

## Gleichzeitig lesen und schreiben (Sync)

Einzelne Anfragen kosten Zeit: Pakete gehen nacheinander über **einen** Draht. Für Bewegungen, bei denen
alle sechs Gelenke gleichzeitig ein Ziel bekommen, gibt es zwei Befehle:

- **Sync Read:** eine Anfrage, sechs Antworten nacheinander. Eine Positionsabfrage aller Servos kostet
  so ein Anfragepaket, nicht sechs.
- **Sync Write:** ein Paket mit den Zielen aller Servos. Der Servo **antwortet nicht**. Geht ein Paket
  verloren, fällt das nicht auf. Deshalb liest man Positionen zurück, statt auf eine Quittung zu vertrauen.

## EEPROM und RAM

Die Register liegen in zwei Speicherbereichen:

| | EEPROM (Adresse 0 bis 39) | RAM (Adresse 40 bis 69) |
|---|---|---|
| Inhalt | ID, Baudrate, Grenzen, PID, Schutzwerte, Betriebsart | Drehmoment, Ziele, Istwerte, Sperre |
| Nach dem Ausschalten | **bleibt erhalten** | **weg**, Startwerte gelten wieder |
| Gefahr | Falsche Werte wirken dauerhaft | Folgen verschwinden beim Neustart |

Das ist der Grund für die **Whitelist** in Skript 04 und im Register-Explorer der Web-App: Nur RAM-Register sind freigegeben
(`torque_enable`, `acceleration`, `goal_position`, `goal_velocity`, `torque_limit`). Alles im EEPROM geht
ausschließlich durch eigene, klar benannte Funktionen, die danach **zurücklesen und vergleichen**:

| Funktion | Zweck |
|---|---|
| `write_eeprom(id, register, wert)` | beliebiges EEPROM-Register schreiben und prüfen |
| `change_id(alt, neu)` | ID ändern, Ping unter der neuen ID, alte ID muss frei sein |
| `change_baudrate(id, baudrate)` | Baudrate ändern, Port nachziehen, Ping |

Im **Register-Explorer** der Web-App lassen sich EEPROM-Register nur im Expertenmodus und nach einer
Rückfrage schreiben, danach wird zurückgelesen. Gesperrt bleiben dort ID und Baudrate (eigener Ablauf mit
genau einem Servo am Bus: Reiter **Einrichtung**, siehe [Einrichtung](02-einrichtung.md)) sowie Homing-Offset und Positionsgrenzen (gehören zur Kalibrierung, sonst passen
Servo und Kalibrierdatei nicht mehr zusammen).

### Die Schreibsperre (`lock`)

Das EEPROM ist durch das Register `lock` (Adresse 55) geschützt. Laut Feetech-Tabelle: **`lock` = 0**
heißt, geschriebene EEPROM-Werte bleiben auch nach dem Ausschalten erhalten; **`lock` = 1** heißt, sie
gehen verloren. Der Ablauf für eine dauerhafte Änderung ist deshalb immer derselbe:

1. Drehmoment ausschalten,
2. `lock` = 0,
3. Wert schreiben,
4. `lock` = 1,
5. Wert zurücklesen und vergleichen. Weicht er ab, gibt es einen Fehler (`VerificationError`).

Der Treiber koppelt die Sperre an das Drehmoment: Wer das Drehmoment ausschaltet, öffnet die Sperre,
wer es einschaltet, schließt sie. Dasselbe tut LeRobot.

!!! danger "torque_enable nie auf 128 setzen"
    Der Wert 128 setzt die **aktuelle Stellung als 2048**, also als neue Mitte. Danach stimmt die
    Kalibrierung nicht mehr. Die Software lässt für `torque_enable` nur 0 und 1 zu.

!!! warning "LeRobot schreibt beim Verbinden ins EEPROM"
    `SO101Follower.connect()` schreibt bei jedem Start Regelparameter dauerhaft in alle Servos
    (`Return_Delay_Time` 0, `Maximum_Acceleration` 254, `Acceleration` 254, `P_Coefficient` 16,
    `I_Coefficient` 0, `D_Coefficient` 32 und am Greifer `Max_Torque_Limit` 500, `Protection_Current` 250,
    `Overload_Torque` 25). Die Lernskripte hier tun das nicht. Wer anschließend LeRobot benutzt, überschreibt
    sie.

## Regelparameter

Der Servo regelt die Position selbst mit einem **PID-Regler**. Die drei Anteile stehen im EEPROM:

| Register | Name | Werkseinstellung (V3.7) | LeRobot setzt |
|---|---|---|---|
| 21 | `p_coefficient` (Proportional) | 32 | 16 |
| 22 | `d_coefficient` (Differential) | 32 | 32 |
| 23 | `i_coefficient` (Integral) | 0 | 0 |

Ein **größeres P** lässt den Servo stärker auf einen Positionsfehler reagieren: schneller, aber eher
Schwingen. LeRobot setzt für den Arm 16 statt der Werkseinstellung 32 (Vorgabe in
`config_so_follower.py`, eine Begründung steht dort nicht). **D** dämpft, **I** gleicht bleibende
Abweichungen aus (bei 0 ausgeschaltet). Wie sich P auf die Sprungantwort auswirkt, ist Thema einer Übung.

Das Tempo der Bewegung steuern RAM-Register, die sich gefahrlos ausprobieren lassen:

- `goal_velocity` (Schritt/s): im Positionsmodus das **Fahrtempo** zum Ziel. Das Protokollhandbuch zeigt
  das in seinem Beispiel („Servo auf 2048, Tempo 1000“). Höchstens 3400 Schritt/s.
- `acceleration` (Einheit 100 Schritt/s²): wie sanft der Servo anfährt und bremst.
- `torque_limit` (Einheit 0,1 %): begrenzt das Drehmoment. 1000 sind 100 %.

Deshalb setzt die Software **Tempo und Beschleunigung, bevor sie ein Ziel schreibt**: Ein Ziel würde sonst
mit dem Wert fahren, der gerade im Register steht, und das ist im Zweifel der letzte oder der Startwert.

## Betriebsarten (`operating_mode`, Register 33)

| Wert | Modus | Verwendung |
|---|---|---|
| 0 | Position | Gelenke des Arms (Standard) |
| 1 | Geschwindigkeit (geregelt) | Räder der LeKiwi |
| 2 | PWM (ungeregelt) | – |
| 3 | Schritt | – |

Im Geschwindigkeitsmodus bedeutet `goal_velocity` das **Solltempo** (mit Vorzeichen für die Richtung), nicht
mehr ein Fahrtempo zum Ziel. Der Modus steht im EEPROM. Ein Rad im Positionsmodus würde nicht wie erwartet fahren.

## Zustand und Fehler

Die „Present“-Register liefern den aktuellen Zustand: Position, Tempo, Last, Spannung, Temperatur, Strom.
Das Register `status` (65) ist eine **Bitmaske**. Ein gesetztes Bit meldet einen aufgetretenen Fehler:

| Bit | Bedeutung |
|---|---|
| 0 | Spannung |
| 1 | Sensor |
| 2 | Temperatur |
| 3 | Strom |
| 4 | Winkel |
| 5 | Überlast |

`so101-lab state` übersetzt die Bits in Wörter (Spalte „Fehler“). `present_load` ist das Tastverhältnis der
Motoransteuerung in 0,1 %, mit Vorzeichen für die Richtung. Es ist **kein** Strom: Der Strom steht in
`present_current` (Einheit 6,5 mA).

## Zum Ausprobieren

1. `uv run scripts/02_read_servo.py --gelenk elbow_flex`: Welche Register sind im EEPROM, welche im RAM?
   Welche Werte weichen von der Werkseinstellung ab?
2. Welcher Wert steht in `id`? Passt er zur Gelenktabelle aus [Aufbau](01-aufbau.md)?
3. `uv run scripts/02_read_servo.py --live`: Bewege den Arm von Hand. Welcher Rohwert gehört zu welcher
   Bewegung? Wie viele Schritte entsprechen 90°?
4. `uv run scripts/03_move_servo.py --gelenk wrist_flex --tempo 200` und danach `--tempo 2000`: Wie
   unterscheidet sich die Bewegung? Schreibe vorher mit Skript 04 eine andere `acceleration`. Warum merkt man
   davon in Skript 03 nichts? (Tipp: Was setzt Skript 03 als Erstes?)
5. Versuche, `p_coefficient` mit Skript 04 zu schreiben. Was meldet das Programm und warum?
