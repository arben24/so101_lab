# Fehlersuche

Hier steht, was am häufigsten schiefgeht, nach Beschwerde sortiert. Für Netz, ESP32 und LeKiwi gibt es eigene
Tabellen in den jeweiligen Kapiteln, auf die unten verwiesen wird. Fängst du ganz vorne an, hilft zuerst:

```bash
uv run so101-lab doctor        # prüft Python, Pakete, Ports, Rechte, Kameras, Konfiguration (schreibt nichts)
uv run so101-lab ports         # welche USB-Seriell-Ports gibt es
uv run so101-lab scan          # welche Servos antworten
```

!!! danger "Bei jedem unklaren Verhalten des Arms"
    **Netzteil ausschalten.** Erst dann suchen. Die Software kann einen Arm nicht anhalten, dessen Servos
    kein Kommando mehr erreichen (Kabel, Netz, hängender Rechner).

## Installation und Umgebung

| Beobachtung | Ursache und Abhilfe |
|---|---|
| `warning: VIRTUAL_ENV=… does not match the project environment path .venv and will be ignored` | Eine andere Umgebung (z. B. conda `base`) ist aktiv. **Harmlos**: uv benutzt `.venv` des Projekts. Verschwindet nach `conda deactivate` |
| `uv sync` dauert lange und lädt rund 2 GB | normal beim ersten Mal (LeRobot und PyTorch, Kapitel 02). Danach läuft alles offline |
| Fehler beim Bauen von `evdev` (Linux) | Compiler fehlt: `sudo apt install build-essential python3-dev` |
| `pytest` bricht ab: `No module named 'lark'` oder ein ROS-Plugin (`launch_testing`) lädt | ROS 2 steht im `PYTHONPATH`. `so101-lab doctor` warnt davor. Ohne starten: `env -u PYTHONPATH uv run pytest` oder `unset PYTHONPATH` |
| `'torchcodec' is installed but cannot decode videos … Falling back to 'pyav'` | **Harmlos**, LeRobot decodiert dann mit `pyav`. Das PyPI-Paket ist für CUDA gebaut, hier läuft CPU-PyTorch (Kapitel 05, Abschnitt 9) |
| `lerobot-train`: `'accelerate' is required but not installed` | Training braucht eine eigene Gruppe: `uv sync --group training` (Übung 7) |
| `ModuleNotFoundError: so101_lab` | im Projektordner mit `uv run …` starten, nicht mit dem System-Python |

## Port und Verbindung

| Beobachtung | Ursache und Abhilfe |
|---|---|
| `Keine Rechte für /dev/ttyACM0` | Nutzer nicht in der Gruppe `dialout`: `sudo usermod -aG dialout $USER`, danach **neu anmelden** (Kapitel 02) |
| `Port … ist belegt` | ein anderes Programm hat ihn offen (Web-App, Skript, LeRobot-Befehl). Beenden |
| `Kein USB-Seriell-Port gefunden` | Adapter nicht angesteckt (oder ein USB-Kabel ohne Datenleitungen). `so101-lab ports` nach dem Anstecken |
| `Für '…' ist kein Port gewählt` | `--port` angeben, `port = "…"` in `arms.toml` eintragen oder aus der Liste wählen. Die Nummer (`ACM0`/`ACM1`) hängt von der Reihenfolge des Ansteckens ab. Bei zwei Armen an der Seriennummer unterscheiden |
| `Kein Servo antwortet. Servo-Netzteil an? Kabel gesteckt? Richtiger Port?` | Der Adapter erscheint auch ohne Servo-Netzteil als Port. Netzteil prüfen, Kabel nur bei ausgeschaltetem Netzteil stecken, richtigen Adapter wählen. Dann `so101-lab scan` (sucht über alle Baudraten) |
| Nur ein Teil der Servos antwortet („Keine Antwort von: …“) | ein Kabel locker oder defekt (die Servos hängen in Reihe an einem gemeinsamen Kabel, Kapitel 01), doppelte oder falsche ID. Im Reiter „Einrichtung“ der Web-App mit **genau einem** Servo am Bus prüfen |
| Servo antwortet nach der ID-Vergabe nicht mehr | Die Neustart-Probe zeigt, ob die Werte im EEPROM stehen. Suche über alle Baudraten, auch 1 Mbit/s (Kapitel 02) |

## Servos und Kalibrierung

| Beobachtung | Ursache und Abhilfe |
|---|---|
| Spalte „Fehler“ zeigt `Überlast`, `Temperatur`, `Spannung` … | Das Register `status` meldet, was der Servo selbst erkannt hat (Kapitel 03). Last verringern, abkühlen lassen, Netzteil und Spannung prüfen. Bei `Spannung` zuerst das Netzteil: Leader und Follower haben verschiedene |
| Der Arm sinkt ab | Das Drehmoment ist aus (Standard bei `safe_state = "torque_off"`). Das ist so gewollt. Mit `hold` hält er die Stellung (Kapitel 01) |
| `Keine Kalibrierung für '…' gefunden: Grenzen sind 0…4095` | Es gibt keine Kalibrierdatei mit dieser `calibration_id` (Ordner `calibration/`). Erst kalibrieren (Kapitel 02). Bis dahin klein und langsam bewegen |
| Datasets und Teleoperation lehnen ab: Kalibrierung nötig | Beide rechnen in Grad und Prozent wie LeRobot. Kalibrieren, bei Teleoperation beide Arme |
| Abspielen: „Startpose … liegt weit außerhalb der Gelenkgrenzen“ | Die Aufnahme passt nicht zu diesem Arm oder seiner Kalibrierung (mehr als 400 Schritt daneben). Mit dem richtigen Arm abspielen. Knapp außerhalb wird nur begrenzt (Kapitel 04) |
| Abspielen läuft „verschoben“ nach einer Neukalibrierung | Aufnahmen merken sich die Homing-Offsets und rechnen sie heraus. Ältere Aufnahmen ohne diese Angabe nicht (Kapitel 04) |
| `Register '…' ist nicht freigegeben` | Nur Register der Whitelist lassen sich im RAM schreiben. EEPROM nur mit `write_eeprom` oder im Expertenmodus der Web-App (Kapitel 03) |
| Ein Servo bewegt sich nicht, obwohl er ein Ziel hat | Drehmoment aus? Ziel hinter der Grenze (wird auf die Grenze begrenzt)? Im Reiter „Einzelservo“ stehen Drehmoment und Grenzen |

## Web-App

| Beobachtung | Ursache und Abhilfe |
|---|---|
| Steuerrecht verschwindet sofort nach „Steuerung übernehmen“ | Der Browser schickt kein Lebenszeichen mehr (Watchdog 400 ms). Häufig: Tab im Hintergrund (Timer werden gedrosselt), Laptop im Ruhezustand, Netz zu langsam. Tab im Vordergrund lassen. Das Repo benutzt bewusst keine blockierenden Browser-Dialoge, weil sie das Lebenszeichen anhalten |
| `Dafür ist das Steuerrecht nötig. Erst „Steuerung übernehmen“.` | Nur ein Client steuert, alle anderen beobachten. Oben auf „Steuerung übernehmen“ |
| `Ein anderer Client steuert dieses Gerät.` | „Übernehmen erzwingen“ geht, wenn du sicher bist, dass es kein anderer Mensch ist |
| `Token fehlt oder ist falsch` (401) | Im Netz (`--host 0.0.0.0`) ist ein Token Pflicht: `?token=…` an die Adresse oder im Dialog eingeben |
| Gerät lässt sich nicht verbinden: „wird über das Netz benutzt“ | Ein anderer Laptop oder die Web-App auf dem Pi hat es schon (Kapitel 06) |
| Kamera „nicht verfügbar“ | Gerätepfad falsch oder Kamera belegt. Gerät verbindet trotzdem. `so101-lab cameras` schlägt feste Namen vor (Kapitel 05) |
| Kamera liefert nur 10 Bilder/s | zu wenig Licht (lange Belichtung). Besser beleuchten (Kapitel 05) |
| Eine Episode ist verschwunden | Verlust des Steuerrechts verwirft die laufende Episode (Absicht, Kapitel 05). Eine abgebrochene Aufnahme (Reiter „Aufnahmen“) bleibt dagegen bis dahin gespeichert |
| Esc löst den Not-Aus aus, obwohl man nur ein Feld verlassen wollte | Esc ist der Not-Aus (Absicht). Danach neu übernehmen |

## Netz, ESP32 und LeKiwi

- **Raspberry Pi, WLAN, SSH, Token, Watchdog:** Kapitel [Raspberry Pi](06-raspberry-pi.md), Abschnitt „Fehlersuche“.
- **ESP32-Knoten:** Kapitel [ESP32](07-esp32.md), Abschnitt „Fehlersuche“.
- **Räder, Fahren, Kinematik:** Kapitel [LeKiwi](08-lekiwi.md), Abschnitt „Fehlersuche“.

## Wenn du nicht weiterkommst

1. `so101-lab doctor` und den Wortlaut der Meldung notieren (die Texte sind deutsch und sagen meist, was zu tun ist).
2. In der Web-App den Reiter „Status“, Abschnitt „Meldungen“ ansehen: Watchdog, Not-Aus und Kameraprobleme stehen dort.
3. Ohne Hardware nachstellen: Das Gerät `demo` (und `demo_kiwi`) verhält sich wie ein Arm, nur ohne Physik. Tritt der Fehler
   dort nicht auf, liegt es an Kabel, Netzteil oder Servo, nicht an der Software.
4. Was an echter Hardware geprüft ist und was nicht, steht in `PLAN.md`.
