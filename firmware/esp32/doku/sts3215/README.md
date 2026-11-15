# Datenblatt Feetech STS3215

Unten steht ein Auszug auf Deutsch. Die Originale des Herstellers liegen **nicht im Repo**: Sie tragen
keinen Lizenz- oder Weitergabevermerk, das Urheberrecht liegt bei Feetech (geprüft am 15.11.2026).
Herunterladen über die Links in der Tabelle und unter gleichem Namen in den Unterordner `originale/`
legen. Der Ordner wird von git ignoriert.

| Datei (in `originale/`) | Inhalt | Quelle |
|---|---|---|
| `STS3215-C001_Datenblatt_Feetech.pdf` | Produktspezifikation STS3215, 7,4-V-Variante, 1:345, Ausgabe A/1 vom 23.06.2023, 8 Seiten (Chinesisch und Englisch) | Feetech über Seeed Studio, [files.seeedstudio.com/…/108090023_STS3215-C001_Datasheet.pdf](https://files.seeedstudio.com/products/Feetech/108090023_STS3215-C001_Datasheet.pdf) |
| `STS3215_Speichertabelle_V3.7.xls` | Speichertabelle (Register) STS3215, Version 3.7, Blatt 1 Chinesisch, Blatt 2 Englisch | Feetech über Waveshare, [files.waveshare.com/…/ST3215 memory register map-EN.xls](https://files.waveshare.com/upload/2/27/ST3215%20memory%20register%20map-EN.xls) |
| `Feetech_Communication_Protocol_Manual_V1.01.pdf` | Protokoll für alle Feetech-Bus-Servos: Paketaufbau, Befehle, Prüfsumme, Beispiele | Feetech über Seeed Studio, [files.seeedstudio.com/…/Communication_Protocol_Manual.pdf](https://files.seeedstudio.com/wiki/robotics/Actuator/feetech/Communication_Protocol_Manual.pdf) |

Abgerufen am 20.10.2026. Weitere Varianten (12 V, Leader-Übersetzungen 1:147 und
1:191) verlinkt das [Seeed-Wiki zu Feetech-Servos](https://wiki.seeedstudio.com/feetech_servo/).

Zwei Stolperfallen in den Originalen:

- Die Datei *Analysis of SCS Memory Table* aus dem Seeed-Wiki gilt für die
  **SCS-Serie** (10 Bit, hohes Byte zuerst, andere Adressen ab 48). Für den
  STS3215 gilt die Speichertabelle V3.7 hier im Ordner (12 Bit, **niedriges Byte
  zuerst**).
- Im Protokollhandbuch fehlt in Beispiel 4 ein Byte. Richtig ist
  `FF FF 01 09 03 2A 00 08 00 00 E8 03 D5` (mit `00 00` für die Zeit).

---

## Kennwerte (Datenblatt, Seiten 3 bis 5)

| Größe | bei 6 V | bei 7,4 V |
|---|---|---|
| Haltemoment (blockiert) | 16,5 kg·cm | 19,5 kg·cm |
| Leerlauftempo | 0,238 s/60° (42 U/min) | 0,192 s/60° (52 U/min) |
| Strom ohne Last | 130 mA | 150 mA |
| Strom blockiert | 2 A | 2,5 A |
| Strom im Stillstand | 21 mA | 21 mA |

| Größe | Wert |
|---|---|
| Spannungsbereich | 4 V bis 7,4 V |
| Maße, Gewicht | 45,2 × 24,7 × 35 mm, 55 g |
| Getriebe | 1 : 345, Zahnräder aus Kupfer(legierung) |
| **Getriebespiel** | **≤ 0,5°** |
| **Winkelsensor** | **12-Bit-Magnetencoder, 360°, Lebensdauer „unbegrenzt“** |
| **Auflösung** | **0,088° (360° / 4096)** |
| Mittelstellung | 180° = Rohwert 2048 |
| Drehrichtung | im Uhrzeigersinn steigt der Wert (0 → 4096) |
| Regler | PID |
| Rückmeldung | Position, Tempo, Last, Spannung, Strom, Temperatur |
| Schnittstelle | Halbduplex, asynchron seriell, 8 Datenbits, 1 Stoppbit, keine Parität |
| Baudrate | 38 400 bit/s bis 1 Mbit/s, ab Werk 1 Mbit/s |
| IDs | 0 bis 253, 254 = alle (Broadcast) |
| Stecker | 3-polig (5264-3P): 1 GND, 2 Versorgung, 3 Signal |

Schutzfunktionen (Seite 5):

| Schutz | Wann | Was passiert |
|---|---|---|
| Überlast | über 80 % des Blockiermoments länger als 2 s | Drehmoment wird begrenzt, neuer Sollwert löscht den Fehler |
| Überstrom | über 2 A länger als 2 s | Ausgang aus, neuer Sollwert löscht den Fehler |
| Spannung | über 8 V oder unter 4 V | Schutz, löst sich, wenn die Spannung wieder passt |
| Temperatur | über 70 °C | Drehmoment aus |

Betriebsarten (Seite 5, Register 33): 0 Position (Standard, 0 bis 360° absolut),
1 Tempo geregelt (so fahren die Räder der LeKiwi), 2 Tempo ungeregelt (PWM),
3 Schrittmodus. Im Mehrumdrehungsmodus zählt der Servo bis zu ±7 Umdrehungen,
**vergisst die Umdrehungen aber beim Ausschalten**. Der Encoder selbst ist nur
innerhalb einer Umdrehung absolut.

---

## Speichertabelle (Auszug, V3.7)

Der Servo ist von außen gesehen ein kleiner Speicher. Jede Zahl hat eine feste
Adresse. **EPROM** bleibt nach dem Ausschalten erhalten, **SRAM** nicht.
Zweibyte-Werte: niedriges Byte an der kleineren Adresse.

| Adr. | Hex | Name (LeRobot) | Bytes | Speicher | Zugriff | Einheit, Bedeutung |
|---|---|---|---|---|---|---|
| 3 | 0x03 | `Model_Number` | 2 | EPROM | lesen | 777 = STS3215 |
| 5 | 0x05 | `ID` | 1 | EPROM | lesen/schreiben | Adresse auf dem Bus, ab Werk 1 |
| 6 | 0x06 | `Baud_Rate` | 1 | EPROM | lesen/schreiben | 0 = 1 Mbit/s … 7 = 38 400 bit/s |
| 9 | 0x09 | `Min_Position_Limit` | 2 | EPROM | lesen/schreiben | kleinste Sollposition (LeRobot schreibt hier beim Kalibrieren) |
| 11 | 0x0B | `Max_Position_Limit` | 2 | EPROM | lesen/schreiben | größte Sollposition, ab Werk 4095 |
| 13 | 0x0D | `Max_Temperature_Limit` | 1 | EPROM | lesen/schreiben | °C, ab Werk 70 |
| 21–23 | 0x15 | `P/D/I_Coefficient` | je 1 | EPROM | lesen/schreiben | Positionsregler, ab Werk P 32, D 32, I 0 |
| 31 | 0x1F | `Homing_Offset` | 2 | EPROM | lesen/schreiben | Verschiebung der Nullstellung, ±2047, Bit 11 = Vorzeichen |
| 33 | 0x21 | `Operating_Mode` | 1 | EPROM | lesen/schreiben | 0 Position, 1 Tempo, 2 PWM, 3 Schritt |
| 40 | 0x28 | `Torque_Enable` | 1 | SRAM | lesen/schreiben | 0 aus, 1 an. **128 = aktuelle Stellung wird 2048** |
| 41 | 0x29 | `Acceleration` | 1 | SRAM | lesen/schreiben | 100 Schritt/s² je Einheit |
| 42 | 0x2A | `Goal_Position` | 2 | SRAM | lesen/schreiben | **Sollposition** in Schritten, Bit 15 = Vorzeichen |
| 44 | 0x2C | `Goal_Time` | 2 | SRAM | lesen/schreiben | nur im PWM-Modus |
| 46 | 0x2E | `Goal_Velocity` | 2 | SRAM | lesen/schreiben | Höchsttempo in Schritt/s, 0 = ohne Grenze, max. 3400 |
| 48 | 0x30 | `Torque_Limit` | 2 | SRAM | lesen/schreiben | Drehmomentgrenze, 1000 = 100 % |
| 55 | 0x37 | `Lock` | 1 | SRAM | lesen/schreiben | 0 = EPROM-Änderungen bleiben gespeichert |
| 56 | 0x38 | `Present_Position` | 2 | SRAM | lesen | **Istposition = Encoderwert** in Schritten |
| 58 | 0x3A | `Present_Velocity` | 2 | SRAM | lesen | Schritt/s, Bit 15 = Vorzeichen |
| 60 | 0x3C | `Present_Load` | 2 | SRAM | lesen | 0,1 % Tastverhältnis, Bit 10 = Vorzeichen |
| 62 | 0x3E | `Present_Voltage` | 1 | SRAM | lesen | 0,1 V |
| 63 | 0x3F | `Present_Temperature` | 1 | SRAM | lesen | °C |
| 65 | 0x41 | `Status` | 1 | SRAM | lesen | Bit 0 Spannung, 1 Sensor, 2 Temperatur, 3 Strom, 4 Winkel, 5 Überlast |
| 66 | 0x42 | `Moving` | 1 | SRAM | lesen | 1 = bewegt sich |
| 69 | 0x45 | `Present_Current` | 2 | SRAM | lesen | 6,5 mA je Einheit, max. 3250 mA |

Die Namen in der dritten Spalte benutzt LeRobot. Dieselben Adressen stehen in
LeRobots Treiber *rustypot* (Pollen Robotics), Datei `src/servo/feetech/sts3215.rs`.

**Vorzeichen:** Negative Werte speichert der STS3215 als Betrag mit gesetztem
Vorzeichenbit, nicht im Zweierkomplement. −300 als Tempo ist also
`300 + 2¹⁵ = 33068 = 0x812C`, auf dem Bus `2C 81`.

---

## Pakete (Protokollhandbuch, Abschnitt 1)

```
Befehl:   FF FF | ID | Länge | Befehl | Parameter 1 … N | Prüfsumme
Antwort:  FF FF | ID | Länge | Fehler | Parameter 1 … N | Prüfsumme

Länge     = Anzahl Parameter + 2
Prüfsumme = ~(ID + Länge + Befehl/Fehler + Parameter) & 0xFF
```

| Befehl | Code | Parameter |
|---|---|---|
| PING | 0x01 | keine |
| READ | 0x02 | Startadresse, Anzahl Bytes |
| WRITE | 0x03 | Startadresse, Daten … |
| REG_WRITE | 0x04 | wie WRITE, wird erst bei ACTION ausgeführt |
| ACTION | 0x05 | keine, löst gespeicherte REG_WRITEs aus |
| RESET | 0x06 | keine, Werkseinstellung |
| SYNC_WRITE | 0x83 | an ID 254: Startadresse, Länge, dann je Servo ID und Daten |

### Beispiel: Position von Servo 1 lesen (Handbuch, Beispiel 2)

```
PC → Servo:   FF FF 01 04 02 38 02 BE
              │     │  │  │  │  │  └ Prüfsumme ~(01+04+02+38+02) = ~41 = BE
              │     │  │  │  │  └ 2 Bytes lesen
              │     │  │  │  └ ab Adresse 0x38 = 56 = Present_Position
              │     │  │  └ READ
              │     │  └ 4 Bytes folgen
              │     └ Servo 1
              └ Kopf

Servo → PC:   FF FF 01 04 00 18 05 DD
                          │  └──┴ 0x18 + 0x05 · 256 = 1304
                          └ Fehler 0 = alles in Ordnung

1304 Schritte  →  (1304 − 2048) · 360° / 4096 = −65,4°
```

### Beispiel: Servo 1 auf 2048 fahren, Tempo 1000 (Handbuch, Beispiel 4, korrigiert)

```
PC → Servo:   FF FF 01 09 03 2A 00 08 00 00 E8 03 D5
                          │  │  └─┬─┘ └─┬─┘ └─┬─┘
                          │  │  2048  Zeit 0  Tempo 1000
                          │  └ ab Adresse 42 = Goal_Position
                          └ WRITE
Servo → PC:   FF FF 01 02 00 FC
```

Die Firmware baut diese Pakete in `src/knoten/servobus.cpp`.
