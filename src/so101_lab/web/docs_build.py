"""Die MkDocs-Doku beim Start der Web-App bauen, damit sie unter /docs erreichbar ist."""

from __future__ import annotations

import logging
import os
from pathlib import Path

from ..paths import project_root

logger = logging.getLogger(__name__)


def build_docs(target: Path | None = None) -> Path | None:
    """Baut `mkdocs.yml` nach `target` (Standard: `site/` im Projektordner).

    Liefert den Ordner oder `None`, wenn MkDocs fehlt (Gruppe `docs` nicht installiert)
    oder der Bau scheitert. Die Web-App läuft dann ohne Doku weiter.
    """
    config_file = project_root() / "mkdocs.yml"
    target = target or project_root() / "site"
    if not config_file.exists():
        logger.warning("Keine mkdocs.yml in %s, Doku wird nicht eingebunden.", project_root())
        return None
    os.environ.setdefault("NO_MKDOCS_2_WARNING", "1")
    try:
        from mkdocs.commands.build import build
        from mkdocs.config import load_config
    except ImportError:
        logger.warning("MkDocs ist nicht installiert (uv sync --group docs). Doku wird nicht eingebunden.")
        return None
    logging.getLogger("mkdocs").setLevel(logging.WARNING)
    try:
        build(load_config(str(config_file), site_dir=str(target)))
    except Exception as exc:
        logger.warning("Doku-Bau fehlgeschlagen (%s). Die Web-App läuft ohne Doku.", exc)
        return None
    return target
