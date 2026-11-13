# so101-lab

Lehr-Repository für den **SO-101-Roboterarm** und die **LeKiwi**-Plattform: vom einzelnen
Servo-Register bis zum aufgezeichneten Datensatz.

## Schnellstart

Voraussetzung ist nur [uv](https://docs.astral.sh/uv/).

```bash
git clone <Repository-URL> so101_lab && cd so101_lab
uv sync                      # installiert Python 3.12 und alle Pakete
uv run so101-lab doctor      # prüft Umgebung, Ports und Kameras
uv run mkdocs serve          # diese Doku unter http://127.0.0.1:8000
```

!!! note "In Arbeit"
    Der Weg „in 5 Befehlen zum bewegten Arm“ entsteht mit den Phasen 2 und 3
    (siehe `PLAN.md`).

!!! warning "Sicherheit"
    Leader und Follower haben **unterschiedliche Netzteile**. Das Kapitel
    [Aufbau](01-aufbau.md) kommt vor dem ersten Einschalten.
