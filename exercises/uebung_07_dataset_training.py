"""Übung 7 – Ein Dataset ansehen und das Training vorbereiten (Vorlage).

Aufruf:
    uv run exercises/uebung_07_dataset_training.py --name wuerfel_greifen
    uv run exercises/uebung_07_dataset_training.py --name wuerfel_greifen --starten

Ohne `--starten` wird nur der Trainingsbefehl ausgegeben. Training braucht die Gruppe `training`
(`uv sync --group training`, bringt accelerate mit) und ist rechenintensiv: Mit CPU-torch (Standard)
dauert es sehr lange, besser mit GPU (`uv sync --no-group cpu --group cuda --group training`, Kapitel 02).

Deine Aufgabe: die mit TODO markierten Funktionen schreiben, dann das Programm starten.
Hängst du fest? Die Musterlösung steht in exercises/loesungen/uebung_07_dataset_training.py.
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
from pathlib import Path

from so101_lab.lab_dataset import dataset_path
from so101_lab.script_support import run_script


def describe_dataset(root: Path) -> dict:
    """Wichtige Angaben aus `meta/info.json`: Episoden, Frames, fps, Kameras, Werte von Zustand und Aktion."""
    # TODO: hier kommt dein Code hin
    raise NotImplementedError(
        "describe_dataset(): meta/info.json lesen (total_episodes, total_frames, fps, features)."
    )


def training_command(name: str, root: Path, steps: int, batch_size: int, device: str) -> list[str]:
    """Befehl für `lerobot-train` mit einer ACT-Policy, alles lokal (kein Hub)."""
    # TODO: hier kommt dein Code hin
    raise NotImplementedError(
        "training_command(): Argumentliste für `uv run lerobot-train …` bauen (Kapitel 05, "
        "Abschnitt „Training“)."
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Dataset beschreiben und Trainingsbefehl bauen.")
    parser.add_argument("--name", required=True, help="Dataset in recordings/datasets/")
    parser.add_argument("--steps", type=int, default=2000)
    parser.add_argument("--batch-size", type=int, default=8)
    parser.add_argument("--device", default="cpu", help="cpu, cuda oder mps")
    parser.add_argument("--starten", action="store_true", help="Training wirklich starten")
    args = parser.parse_args(argv)

    root = dataset_path(args.name)
    d = describe_dataset(root)
    print(f"Dataset {args.name} ({d['robot_type']}): {d['episodes']} Episoden, {d['frames']} Frames, "
          f"{d['seconds']} s bei {d['fps']} fps")  # fmt: skip
    print(f"  Kameras: {', '.join(d['cameras']) or 'keine'}")
    print(f"  Zustand und Aktion ({len(d['action'])} Werte): {', '.join(d['action'])}")
    if d["episodes"] < 10:
        print(f"  Hinweis: {d['episodes']} Episoden sind für ein Training sehr wenig (LeRobot nennt 50).")
    command = training_command(args.name, root, args.steps, args.batch_size, args.device)
    print("\nTrainingsbefehl:\n  " + shlex.join(command))
    if not args.starten:
        return 0
    print("\nStarte Training (Abbrechen mit Strg+C) …")
    return subprocess.call(command, env={**os.environ, "HF_HUB_OFFLINE": "1"})


if __name__ == "__main__":
    run_script(main)
