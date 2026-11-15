# ESP32-Funkknoten für Feetech-Servos

Ein ESP32 steuert Feetech-Bus-Servos (STS3215 und verwandte) und ist vom PC aus
über **WLAN** oder **ESP-NOW** erreichbar. Der PC schickt Sollwerte (Position oder
Tempo je Servo) und bekommt Istwerte zurück (Position, Tempo, Last, Spannung,
Temperatur). Alles, was mit Kinematik oder Kalibrierung zu tun hat, bleibt auf
dem PC. Die Firmware selbst kennt nur Servo-IDs und rohe Servo-Einheiten.

Entstanden für einen Robotik-Workshop als Ersatz für den Raspberry Pi der
[LeKiwi](https://github.com/SIGRobotics-UIUC/LeKiwi) (fahrbare Plattform mit drei
Allseitenrädern und SO-101-Arm). Über **Profile** lässt sich dieselbe Firmware für
andere Aufbauten mit bis zu 12 Servos verwenden, ohne Netz- oder Protokollcode
anzufassen.

**Stand 12.11.2026**

| Was | Status |
|---|---|
| WLAN-Einrichtung über die Webseite, Datenübertragung, Räder fahren (LeKiwi, Servos 7, 8, 9) | am echten Gerät getestet |
| `wasd_lekiwi.py` | am echten Gerät getestet, Fahrtrichtungen noch nicht systematisch geprüft |
| `wasd_zweirad.py`, `wasd_schwenkkopf.py` | nur gegen einen nachgebauten Knoten am PC getestet |
| Gateway und ESP-NOW | gebaut, aber noch nicht am Gerät getestet |
| Profile `so101` und `so101_leader` | vorbereitet, nicht am Gerät getestet |

---

## Inhalt

| Pfad | Was |
|---|---|
| `platformio.ini` | zwei Programme: `knoten` (auf dem Roboter) und `gateway` (zweiter ESP32 am PC, nur für ESP-NOW) |
| `lib/protokoll/protokoll.h` | **Paketformat**, gemeinsam für Knoten und Gateway. Die Referenz für eigene PC-Programme |
| `src/knoten/main.cpp` | Start, Status-LED, BOOT-Taster |
| `src/knoten/steuerung.*` | Servo-Task mit 50 Hz: Sollwerte schreiben, Istwerte lesen, Watchdog |
| `src/knoten/servobus.*` | Feetech-Protokoll (PING, READ, WRITE, SYNC_WRITE) |
| `src/knoten/netz.*` | WLAN mit Rückfall, ESP-NOW, UDP, Steuerrecht, Baken |
| `src/knoten/webseite.*` | Einrichtung im Browser, Live-Zustand der Servos |
| `src/knoten/profile.*` | **welche Servo-IDs in welcher Betriebsart**. Hier kommt ein eigener Aufbau hinein |
| `src/knoten/einstellungen.*` | Einstellungen im Flash (NVS) |
| `src/gateway/main.cpp` | USB ↔ ESP-NOW mit automatischer Kanalsuche |
| `werkzeuge/knoten_test.py` | Python-Gegenstück zum Protokoll und Kommandozeilenwerkzeug |
| `werkzeuge/wasd_zweirad.py` | **Beispiel:** Zweirad (Differentialantrieb) per WASD, Tempomodus |
| `werkzeuge/wasd_schwenkkopf.py` | **Beispiel:** zwei Servos per WASD schwenken, Positionsmodus |
| `werkzeuge/wasd_lekiwi.py` | **Beispiel:** LeKiwi per WASD fahren (vorwärts, seitlich, drehen) |
| `werkzeuge/kiwi.py` | Kiwi-Kinematik für `wasd_lekiwi.py` |
| `doku/sts3215/` | deutscher Auszug aus Datenblatt, Registertabelle und Protokollhandbuch des STS3215, mit Links zu den Originalen (diese nicht im Repo) |

---

## Was man braucht

**Hardware**

- ein klassisches ESP32-DevKit (ESP32-WROOM-32, Board `esp32dev`)
- Feetech-Bus-Servos (getestet mit STS3215) und deren Versorgung
- einen Servo-Adapter, der den Halbduplex-Bus selbst umschaltet (siehe Verdrahtung)
- optional einen zweiten ESP32 als Gateway am PC, wenn kein WLAN-Router da ist

**Software am PC**

- VS Code mit der Erweiterung **PlatformIO** (oder pioarduino). Die Empfehlung liegt
  in `.vscode/extensions.json`, VS Code schlägt sie beim Öffnen des Ordners vor.
- [uv](https://docs.astral.sh/uv/) für die Python-Werkzeuge. uv installiert die
  Pakete (`pyserial`, `pygame-ce`) beim ersten Aufruf selbst, es ist kein
  `pip install` nötig. Python 3.10 bis 3.13.

---

## Schnellstart

```bash
# 1. Firmware auf den ESP32 im Roboter
pio run -e knoten -t upload

# 2. WLAN einrichten: mit dem WLAN "<name>-setup" verbinden (Passwort roboter123),
#    http://192.168.4.1/ öffnen, eigenes WLAN eintragen, speichern

# 3. Knoten im WLAN finden
uv run werkzeuge/knoten_test.py suchen

# 4. Zustand der Servos ansehen
uv run werkzeuge/knoten_test.py --ip lekiwi-3fa2.local zustand

# 5. Mit der Tastatur steuern
uv run werkzeuge/wasd_zweirad.py --ip lekiwi-3fa2.local
```

Alle Befehle laufen aus diesem Ordner. Der Name `lekiwi-3fa2` ist ein Beispiel. Der
echte Name steht in der Ausgabe von `suchen` und setzt sich aus dem Profil und den
letzten vier Stellen der MAC-Adresse zusammen.

---

## Verdrahtung

| ESP32-DevKit | Servo-Adapter |
|---|---|
| GPIO 16 (RX2) | TX des Adapters |
| GPIO 17 (TX2) | RX des Adapters |
| GND | GND (gemeinsame Masse mit der Servoversorgung) |
| VIN (5 V) | 5-V-Versorgung, **nicht** die Servospannung |

- Der Adapter muss den Halbduplex-Bus selbst umschalten, zum Beispiel der
  Waveshare Bus Servo Adapter mit Jumper auf **A** oder das Seeed-Board über seinen
  UART-Anschluss. Welcher Adapter mit welchen Pegeln am RX des ESP32 (3,3 V)
  sicher funktioniert, ist noch nicht abschließend geprüft. Vor dem Anschließen
  den Pegel am TX des Adapters nachmessen.
- Gibt der Adapter gesendete Bytes zurück (Echo), erkennt die Firmware das beim
  Start selbst und meldet es im seriellen Monitor.
- Antwortet kein Servo: RX und TX tauschen.
- Andere Pins, LED oder Taster: `build_flags` in `platformio.ini`.
- Der Bus läuft mit 1 Mbit/s, der Werkseinstellung des STS3215.

---

## Firmware hochladen

In VS Code den Ordner `esp32_funkknoten` öffnen, unten in der Statusleiste die
Umgebung `knoten` oder `gateway` wählen und auf Upload klicken. Oder im Terminal:

```bash
pio run -e knoten -t upload        # ESP32 im Roboter
pio run -e gateway -t upload       # zweiter ESP32 am PC (nur für ESP-NOW nötig)
pio device monitor -e knoten       # Meldungen des Knotens, 115200 Baud
```

Der serielle Monitor zeigt beim Start Name, Profil, Betriebsart und welche
Servos gefunden wurden. Das ist die erste Anlaufstelle, wenn etwas nicht geht.

---

## Einrichtung über die Webseite

1. Knoten einschalten. Ohne eingetragenes WLAN öffnet er das Einrichtungs-WLAN
   **`<name>-setup`**, zum Beispiel `lekiwi-3fa2-setup`, Passwort **`roboter123`**.
   Die LED blinkt schnell.
2. Mit Handy oder Laptop verbinden. Die Seite öffnet sich meist von selbst, sonst
   **http://192.168.4.1/** aufrufen.
3. Bis zu **drei WLANs** eintragen (etwa Labor, Büro, Zuhause). Der Knoten nimmt das
   stärkste, das er findet. „WLANs in der Nähe suchen“ füllt die Auswahl.
4. **Profil** wählen (welche Servos am Bus hängen), speichern. Der Knoten startet
   neu, verbindet sich und ist dann unter **http://`<name>`.local/** erreichbar
   (Anmeldung `admin` und das Setup-Passwort).

Auf der Webseite lassen sich außerdem einstellen: Gerätename, Betriebsart (WLAN
oder reines ESP-NOW), Funkkanal ohne Router, Watchdog-Zeit (100 bis 2000 ms),
höchstes Radtempo und das Setup-Passwort. Die Seite zeigt außerdem den Live-Zustand
aller Servos. Alle Einstellungen bleiben im Flash, ein neues WLAN braucht also keinen
neuen Code. Das Setup-Passwort sollte man bei der ersten Einrichtung ändern.

**Anderer Raum, anderes WLAN:** Ist das WLAN schon eingetragen, passiert nichts
weiter. Sonst findet der Knoten keinen Router, geht nach 15 s in den
**Notbetrieb** und öffnet wieder `<name>-setup`. Dort das neue Netz eintragen.

**BOOT-Taster:** 3 s halten und loslassen startet neu in die Einrichtung. 10 s
halten stellt die Werkseinstellungen her (alles gelöscht). Die LED flackert, sobald
die 3 s erreicht sind.

---

## Bedienung vom PC

### Kommandozeilenwerkzeug `knoten_test.py`

```bash
uv run werkzeuge/knoten_test.py suchen                                 # alle Knoten im WLAN
uv run werkzeuge/knoten_test.py --ip lekiwi-3fa2.local info            # Profil, Betrieb, Servos
uv run werkzeuge/knoten_test.py --ip lekiwi-3fa2.local zustand --dauer 5
uv run werkzeuge/knoten_test.py --ip lekiwi-3fa2.local raeder 300 300 300 --dauer 2
uv run werkzeuge/knoten_test.py --ip lekiwi-3fa2.local servo 1 2048 --dauer 2
uv run werkzeuge/knoten_test.py --ip lekiwi-3fa2.local drehmoment-aus
uv run werkzeuge/knoten_test.py --ip lekiwi-3fa2.local stopp
uv run werkzeuge/knoten_test.py --ip lekiwi-3fa2.local reg-lesen 1 56 2     # Register lesen
uv run werkzeuge/knoten_test.py --gateway COM5 suchen                  # über ESP-NOW
uv run werkzeuge/knoten_test.py --gateway COM5 gateway-info
```

`raeder` und `servo` wiederholen den Befehl mit 20 Hz und schicken am Ende STOPP.
Statt `--ip` geht überall `--gateway <Port>` (unter Linux etwa `/dev/ttyUSB0`).
Ohne `--mac` nimmt das Gateway den ersten Knoten, der antwortet.

Unter Windows muss die Firewall UDP 4210 und 4211 für Python durchlassen, sonst
findet `suchen` nichts. Funktioniert `name.local` nicht, die IP aus `suchen`
verwenden.

### WASD-Beispiele

Alle drei öffnen ein kleines Fenster. Nur solange es den Fokus hat, zählen die
Tasten. Klickt man woanders hin, stehen die Servos. Sie senden mit 20 Hz und
schicken beim Beenden STOPP.

| Skript | Aufbau | Tasten |
|---|---|---|
| `wasd_zweirad.py` | zwei Räder links und rechts, Tempomodus | W/S vor und zurück, A/D drehen, W+A usw. Kurve |
| `wasd_schwenkkopf.py` | zwei Servos, zum Beispiel Kamerakopf, Positionsmodus | A/D schwenken, W/S neigen, R zurück zur Startstellung |
| `wasd_lekiwi.py` | LeKiwi mit drei Allseitenrädern | W/S vor und zurück, A/D seitlich, Q/E drehen |

Gemeinsam: **1/2/3** Tempostufe, **Leertaste** STOPP, **Esc** beenden.

```bash
uv run werkzeuge/wasd_zweirad.py --ip lekiwi-3fa2.local
uv run werkzeuge/wasd_schwenkkopf.py --ip lekiwi-3fa2.local
uv run werkzeuge/wasd_lekiwi.py --ip lekiwi-3fa2.local
uv run werkzeuge/wasd_zweirad.py --gateway COM5         # jeweils auch über ESP-NOW
```

Oben in `wasd_zweirad.py` und `wasd_schwenkkopf.py` steht ein Block **„An den
eigenen Aufbau anpassen“** mit Servo-IDs, Drehrichtung und Tempostufen. Ohne
Änderung passen sie zum Profil `lekiwi`: Das Zweirad nimmt die Räder 7 und 9, der
Schwenkkopf die Servos 1 und 2. So lassen sich beide auf dem Tisch mit
einzelnen Servos ausprobieren, bevor ein eigenes Profil existiert.

`wasd_schwenkkopf.py` liest beim Start die aktuelle Stellung und fährt von dort
aus. Dadurch springt kein Servo, wenn das Drehmoment einschaltet. Jeder Servo darf
sich nur `SPIELRAUM` Schritte (Standard 600, etwa 53°) von der Startstellung
entfernen.

---

## Wie es funktioniert

```
 PC                                                 Roboter
 ┌──────────────────────┐   WLAN (UDP 4210)    ┌────────────────────────┐
 │ wasd_*.py            │ ◄──── Router ──────► │ ESP32 "knoten"         │
 │ knoten_test.py       │                      │  ├ Servo-Task 50 Hz    │── UART 1 Mbit/s ─► Servos
 │ eigenes Programm     │   USB      ESP-NOW   │  ├ Watchdog            │
 │                      │ ◄──► ESP32 ◄───────► │  └ Webseite :80        │
 └──────────────────────┘    "gateway"         └────────────────────────┘
```

### Aufteilung im Knoten

Die **Servo-Task** (`steuerung.cpp`) läuft als eigene FreeRTOS-Task mit 50 Hz auf
Kern 1 und ist der einzige Teil, der den Servo-Bus benutzt. In jedem Zyklus

1. holt sie die zuletzt empfangenen Sollwerte und prüft den Watchdog,
2. schreibt die Ziele mit je einem SYNC_WRITE für Positions- und Tempo-Servos,
3. schaltet das Drehmoment ein oder aus, wo nötig (Ziel immer vor dem
   Drehmoment, sonst springt der Servo zum alten Wert im Register),
4. liest von jedem Servo Position, Tempo, Last, Spannung, Temperatur und Status
   in einem Rutsch.

Das **Netz** (`netz.cpp`) nimmt Pakete über UDP und ESP-NOW an, legt Sollwerte ab
und antwortet mit dem letzten Abbild der Istwerte. Es wartet nie auf den Bus.
Hängt das WLAN, läuft die Servo-Task trotzdem weiter.

Beim Start schaltet die Firmware bei allen Servos das Drehmoment aus und setzt die
**Betriebsart** (Register 33) passend zum Profil. Dieses Register liegt im EPROM
des Servos und wird nur geschrieben, wenn es abweicht. Ein Servo, der dauerhaft
im Tempomodus läuft, dreht endlos und kennt keine Positionsgrenzen.

### Sicherheit

- **Watchdog** (Standard 300 ms): Kommt so lange kein Befehl, gehen die Räder auf
  Tempo 0 und Positions-Servos halten ihre aktuelle Stellung. Ein PC-Programm muss
  deshalb **ständig** senden, auch im Stand (die Beispiele tun das mit 20 Hz).
- **Ein Steuernder:** Es steuert immer nur eine Quelle. Eine zweite bekommt
  `BELEGT`, bis die erste länger als der Watchdog schweigt.
- **STOPP darf jeder** und gibt das Steuerrecht sofort frei.
- Das Radtempo ist auf der Webseite nach oben begrenzt (Standard 3000 Schritt/s).
- Nach dem Start ist das Drehmoment aus. Erst der erste Befehl mit gesetztem
  Drehmoment-Flag schaltet es ein.
- Das Profil `so101_leader` schaltet nie Drehmoment ein (Arm wird von Hand bewegt).

### Betriebsarten und LED

| Betrieb | Wann | Erreichbar über | LED |
|---|---|---|---|
| WLAN | Router gefunden (Standard) | UDP im Router-Netz, ESP-NOW auf dem Router-Kanal, Webseite `<name>.local` | blitzt alle 2 s, blinkt 1 Hz beim Wiederverbinden |
| ESP-NOW | auf der Webseite gewählt | ESP-NOW auf festem Kanal, Webseite im `<name>-setup` | blitzt doppelt |
| Notbetrieb | Router 15 s beim Start oder 60 s im Betrieb weg | wie ESP-NOW, sucht jede Minute wieder nach dem Router | blinkt schnell |
| Einrichtung | kein WLAN eingetragen oder BOOT-Taster | wie ESP-NOW | blinkt schnell |

Die LED leuchtet dauerhaft, solange jemand steuert. Der Knoten nimmt in **jeder**
Betriebsart Befehle über UDP **und** ESP-NOW an. ESP-NOW ist damit immer als
Rückfallebene da.

### Gateway

Das Gateway reicht Pakete unverändert zwischen USB (921600 Baud) und ESP-NOW
durch. Auf USB stecken sie in SLIP-Rahmen mit vorangestellter MAC-Adresse
(Einzelheiten oben in `src/gateway/main.cpp`). Einstellungen braucht es keine:
Hört es 3 s nichts, geht es die Kanäle 1 bis 13 durch und schickt jeweils ein
PING, bis ein Knoten antwortet. Mit `knoten_test.py --gateway COM5 gateway-kanal 6`
lässt sich der Kanal fest setzen (0 = wieder automatisch).

### Protokoll

Gleiche Pakete über UDP (Port 4210) und ESP-NOW, vollständig beschrieben in
`lib/protokoll/protokoll.h`. Alle Zahlen Little Endian.

```
Kopf (7 Byte)                              | Nutzdaten (0 bis 241 Byte) | CRC-16 (2 Byte)
magic 0x4B52 | version 1 | typ | seq (2) | länge
```

Die CRC ist CCITT-FALSE (Startwert 0xFFFF, Polynom 0x1021) über Kopf und
Nutzdaten. Antworten tragen die `seq` der Anfrage.

| Typ | Richtung | Inhalt | Antwort |
|---|---|---|---|
| `PING` 0x01 | PC → Knoten | nichts | `INFO` |
| `BEFEHL` 0x02 | PC → Knoten | Anzahl, dann je Servo: ID, Flags (Bit 0 = Drehmoment), Sollwert int16 | `ZUSTAND` |
| `ZUSTAND_ANFRAGE` 0x03 | PC → Knoten | nichts, übernimmt kein Steuerrecht | `ZUSTAND` |
| `STOPP` 0x04 | PC → Knoten | nichts | `ZUSTAND` |
| `REG_LESEN` 0x05, `REG_SCHREIBEN` 0x06 | PC → Knoten | Servo-ID, Adresse, Länge (, Daten) | `REG_ANTWORT` |
| `INFO` 0x81 | Knoten → PC | Name, Profil, Betrieb, Kanal, IP, MAC, Servo-IDs und Betriebsart | |
| `ZUSTAND` 0x82 | Knoten → PC | Watchdog, wer steuert, je Servo Position, Tempo, Last, Spannung, Temperatur, Status | |

**Einheiten** sind rohe Servo-Einheiten: Position in Schritten (0 bis 4095 = eine
Umdrehung), Tempo in Schritt/s mit Vorzeichen. Servos, die in einem BEFEHL nicht
vorkommen, behalten ihren letzten Sollwert. Zusätzlich schickt der Knoten jede
Sekunde ein `INFO` als Broadcast (UDP an Port 4211 und über ESP-NOW), damit PC und
Gateway ihn finden.

---

## An ein eigenes Projekt anpassen

### 1. Eigenes Profil anlegen

In `src/knoten/profile.cpp` eine Servo-Liste und einen Eintrag in `PROFILE[]`
ergänzen, zum Beispiel für ein Zweirad mit den Servo-IDs 1 und 2:

```cpp
const ServoProfil ZWEIRAD[] = {
    {1, MODUS_TEMPO, "rad_links"},
    {2, MODUS_TEMPO, "rad_rechts"},
};
```

```cpp
const Profil PROFILE[] = {
    {"lekiwi", ...},                       // bestehende Einträge bleiben
    ...
    {"zweirad", "zweirad", "Zweirad: Räder 1 und 2 (Tempo)", ZWEIRAD, 2, false},
};
```

Die Felder sind Kennung (wird gespeichert), Kurzname (Anfang des Gerätenamens,
höchstens 10 Zeichen), Beschreibung (für die Webseite), Servo-Liste, Anzahl und
`nurLesen` (true = nie Drehmoment). Höchstens 12 Servos. `MODUS_POSITION` für
Gelenke, `MODUS_TEMPO` für Räder. Neu hochladen, auf der Webseite das Profil
wählen, speichern. Dann in `wasd_zweirad.py` `LINKS_ID = 1` und `RECHTS_ID = 2`
setzen.

Unbekannte Kennungen fallen auf das erste Profil zurück. Steht das neue Profil
nicht an erster Stelle, ändert sich also für bestehende Geräte nichts.

### 2. Servo-IDs vergeben

Neue STS3215 haben ab Werk alle die ID 1. Damit mehrere am selben Bus hängen
können, braucht jeder eine eigene ID (Register 5, liegt im EPROM). Dafür
**nur den einen Servo** anschließen und zum Beispiel aus ID 1 die ID 2 machen:

```bash
uv run werkzeuge/knoten_test.py --ip <name>.local reg-schreiben 1 55 0    # EPROM entsperren
uv run werkzeuge/knoten_test.py --ip <name>.local reg-schreiben 1 5 2     # neue ID 2
uv run werkzeuge/knoten_test.py --ip <name>.local reg-schreiben 2 55 1    # wieder sperren
uv run werkzeuge/knoten_test.py --ip <name>.local reg-lesen 2 5 1         # prüfen
```

Dieser Ablauf folgt der Registertabelle in `doku/sts3215/`, ist mit dieser Firmware
aber noch nicht ausprobiert. Alternativ geht das Feetech-Programm FD oder
`lerobot-setup-motors` aus LeRobot.

### 3. Eigenes PC-Programm

`knoten_test.py` lässt sich als Modul benutzen. Das kleinste sinnvolle Programm:

```python
import time
from knoten_test import Knoten, UdpVerbindung

knoten = Knoten(UdpVerbindung("lekiwi-3fa2.local"))
print(knoten.info())

try:
    for _ in range(40):                                  # 2 s lang mit 20 Hz
        z = knoten.befehl({7: (True, 300), 9: (True, -300)})   # {ID: (Drehmoment, Sollwert)}
        time.sleep(0.05)
finally:
    knoten.stopp()
```

`befehl()` liefert den Zustand als Dictionary zurück (`z["servos"]` mit Position,
Tempo, Last, Spannung usw. je Servo, `z["belegt"]`, `z["watchdog"]`). Für ESP-NOW
statt `UdpVerbindung` eine `GatewayVerbindung(port, mac)` nehmen, siehe
`verbinden()` in den WASD-Beispielen.

Ein Programm in einer anderen Sprache braucht nur `protokoll.h` als Vorlage:
Kopf bauen, CRC anhängen, per UDP an Port 4210 schicken.

### 4. Andere Pins oder ein anderes Board

Pins für Servo-UART, LED und Taster stehen als `build_flags` in `platformio.ini`.
Getestet ist nur das klassische `esp32dev`. Andere ESP32-Varianten (S3, C3) sollten
mit angepasstem `board` gehen, sind aber nicht ausprobiert.

---

## Fehlersuche

| Beobachtung | Ursache und Abhilfe |
|---|---|
| `suchen` findet nichts | PC und Knoten im selben WLAN? Firewall für UDP 4210 und 4211 offen? Gästenetze blockieren oft Verkehr zwischen Geräten |
| Servo „fehlt“ im Zustand | Versorgung, Kabel, ID passt nicht zum Profil, RX und TX vertauscht. Serieller Monitor zeigt beim Start, welche IDs antworten |
| `BELEGT` | ein anderes Programm steuert noch. Dieses beenden oder STOPP senden |
| Servos bleiben ständig stehen | Programm sendet seltener als der Watchdog erlaubt. Mit mindestens 10 Hz senden oder Watchdog auf der Webseite erhöhen |
| Rad dreht falsch herum | `..._UMGEDREHT` im Beispiel umstellen |
| Knoten nach Raumwechsel nicht erreichbar | neues WLAN noch nicht eingetragen. Nach 15 s öffnet er `<name>-setup` |

---

## Quellen

- LeKiwi: https://github.com/SIGRobotics-UIUC/LeKiwi
- LeRobot (Kiwi-Formel in `lerobot/robots/lekiwi/lekiwi.py`): https://github.com/huggingface/lerobot
- Datenblätter STS3215: siehe `doku/sts3215/README.md`, dort mit Herkunft und Abrufdatum
