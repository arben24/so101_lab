# so101-lab

Lehr-Repository für den SO-101-Roboterarm und die LeKiwi-Plattform (Hochschule, Elektrotechnik/Robotik).
Stand und Planung: [PLAN.md](PLAN.md).

```bash
uv sync                    # Python 3.12 und alle Pakete (CPU-torch)
uv run so101-lab doctor    # Umgebung, Ports, Kameras prüfen
uv run mkdocs serve        # Doku lokal
uv run pytest              # Tests (ohne Hardware)
uv run ruff check . && uv run ruff format --check .
```

Für Training mit GPU: `uv sync --no-group cpu --group cuda`.
