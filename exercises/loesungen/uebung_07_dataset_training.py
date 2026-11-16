"""Übung 7 – Ein Dataset ansehen und das Training vorbereiten (Musterlösung).

Aufruf:
    uv run exercises/loesungen/uebung_07_dataset_training.py --name wuerfel_greifen
    uv run exercises/loesungen/uebung_07_dataset_training.py --name wuerfel_greifen --starten

Ohne `--starten` wird nur der Trainingsbefehl ausgegeben. Training braucht die Gruppe `training`
(`uv sync --group training`, bringt accelerate mit) und ist rechenintensiv: Mit CPU-torch (Standard)
dauert es sehr lange, besser mit GPU (`uv sync --no-group cpu --group cuda --group training`, Kapitel 02).
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
    info_path = root / "meta" / "info.json"
    if not info_path.is_file():
        raise FileNotFoundError(f"{info_path} nicht gefunden: kein LeRobot-Dataset?")
    info = json.loads(info_path.read_text(encoding="utf-8"))
    features = info["features"]
    return {
        "episodes": info["total_episodes"],
        "frames": info["total_frames"],
        "fps": info["fps"],
        "seconds": round(info["total_frames"] / info["fps"], 1),
        "cameras": [
            k.removeprefix("observation.images.") for k in features if k.startswith("observation.images.")
        ],
        "state": features["observation.state"]["names"],
        "action": features["action"]["names"],
        "robot_type": info.get("robot_type"),
    }


def training_command(name: str, root: Path, steps: int, batch_size: int, device: str) -> list[str]:
    """Befehl für `lerobot-train` mit einer ACT-Policy, alles lokal (kein Hub)."""
    output = Path("outputs") / "train" / name
    return [
        "uv", "run", "--group", "training", "lerobot-train",
        f"--dataset.repo_id=lab/{name}",
        f"--dataset.root={root}",
        "--policy.type=act",
        f"--policy.device={device}",
        "--policy.push_to_hub=false",
        f"--output_dir={output}",
        f"--job_name=act_{name}",
        f"--steps={steps}",
        f"--batch_size={batch_size}",
    ]  # fmt: skip


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
