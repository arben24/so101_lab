# Übungen

Sieben Übungen zu so101-lab. Die Aufgaben stehen in der Doku (Kapitel „Übungen“, `docs/uebungen.md`, oder
`uv run so101-lab web` und dann `/docs/uebungen.html`).

| Datei | Thema |
|---|---|
| `uebung_01_messen.py` | Messwerte lesen und bewerten |
| `uebung_02_rohwert_grad.py` | Rohwert ↔ Grad selbst rechnen |
| `uebung_03_p_anteil.py` | P-Anteil und Sprungantwort (echter Arm, schreibt EEPROM) |
| `uebung_04_glaetten.py` | eine Bewegung glätten |
| `uebung_05_schutzfunktionen.py` | Schutzfunktionen herausfordern |
| `uebung_06_kinematik.py` | Kinematik der LeKiwi-Basis |
| `uebung_07_dataset_training.py` | Dataset ansehen, Training vorbereiten |

Jede Datei ist eine **Vorlage**: Funktionen mit `TODO` ausfüllen, dann starten. Solange etwas fehlt, meldet das
Programm *„Noch zu lösen: …“* und tut nichts am Arm. Die **Musterlösungen** liegen in `loesungen/`.

```bash
uv run exercises/uebung_02_rohwert_grad.py            # Vorlage
uv run exercises/loesungen/uebung_02_rohwert_grad.py  # Musterlösung
```

Übungen, die auf den Arm zugreifen (1, 3, 5), laufen ohne Hardware mit `--device demo`. Die Übungen 2, 4, 6 und 7 brauchen keinen Arm.

**Für Betreuer:** Die Vorlagen werden aus den Musterlösungen erzeugt (Funktionskörper durch `TODO` ersetzt). Ein Test
(`tests/test_exercises.py`) stellt sicher, dass Vorlage und Lösung außerhalb der Lücken gleich bleiben. Wer die Lösungen
nicht ausgeben will, löscht `loesungen/` vor der Ausgabe.
