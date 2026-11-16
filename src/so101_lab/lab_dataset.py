"""Episoden als LeRobot-Dataset aufnehmen (Format v3.0), immer lokal.

Ein LeRobot-Dataset ist das, was `lerobot-record` schreibt und `lerobot-train` liest: je
Zeitschritt eine **Beobachtung** (Gelenkwerte und Kamerabilder) und eine **Aktion** (was der
Arm tun soll), gruppiert in **Episoden** (eine Vorführung der Aufgabe) mit einer Aufgabe in
Worten (`task`). Gespeichert wird als Parquet-Tabellen und Videos unter
`recordings/datasets/<name>/`. Hochgeladen wird hier nie etwas (`push_to_hub` gibt es nicht).

Werte wie bei LeRobot (`SOFollower` mit `use_degrees=True`): Gelenke in Grad, Greifer in
Prozent, Schlüssel `<gelenk>.pos`. Dafür braucht es eine Kalibrierung.

LeKiwi mit Basis (`base=True`): zusätzlich `x.vel`, `y.vel` (m/s) und `theta.vel` (Grad/s), wie
LeRobots `LeKiwi`. Beobachtung = aus dem gemessenen Radtempo zurückgerechnet, Aktion = der
Fahrbefehl (0, wenn niemand fährt).

Woher die **Aktion** kommt:
- Teleoperation: die Stellung des Leaders (in Grad mit dessen Kalibrierung), wie bei `lerobot-record`.
- Abspielen einer Aufnahme: das Ziel, das gerade an den Arm geht.
- Von Hand geführt: die Beobachtung selbst (der Arm „will“ dorthin, wo er ist).
- Basis: der Fahrbefehl aus dem Reiter „Fahren“.

Die Aufnahme läuft in einem eigenen Thread mit fester Rate (`fps`). Sie liest nur die Werte,
die die Zustandsabfrage zuletzt gelesen hat, und das neueste Kamerabild. Sie greift selbst nie
auf den Bus zu.
"""

from __future__ import annotations

import json
import logging
import re
import shutil
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .cameras import LabCamera
from .kinematics import BODY_KEYS
from .paths import project_root

logger = logging.getLogger(__name__)

_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
REPO_PREFIX = "lab"  # repo_id = "lab/<name>": nur ein Name, es gibt keinen Hub-Nutzer
MAX_EPISODE_S = 600.0
MAX_FRAME_AGE_S = 0.5  # älter darf ein Kamerabild nicht sein, sonst wird der Zeitschritt ausgelassen


class DatasetError(ValueError):
    """Text für Nutzer auf Deutsch."""


def datasets_root() -> Path:
    return project_root() / "recordings" / "datasets"


def check_name(name: str) -> str:
    if not _NAME.match(name or ""):
        raise DatasetError(f"Name '{name}' ungültig. Erlaubt sind Buchstaben, Ziffern, '_' und '-'.")
    return name


def dataset_path(name: str) -> Path:
    return datasets_root() / check_name(name)


# Werte liefern: Beobachtung (Gelenk → Wert) und Aktion (Gelenk → Wert), oder None (noch nichts da)
ValueSource = Callable[[], tuple[dict[str, float], dict[str, float]] | None]


@dataclass
class EpisodeStats:
    index: int
    frames: int = 0
    skipped: int = 0  # Zeitschritte ohne frisches Kamerabild oder ohne Gelenkwerte
    started: float = 0.0
    duration_s: float = 0.0


class LabDataset:
    """Ein geöffnetes Dataset, in das Episoden aufgenommen werden."""

    def __init__(
        self,
        name: str,
        joints: list[str],
        cameras: dict[str, LabCamera],
        fps: int,
        robot_type: str,
        task: str,
        base: bool = False,
    ):
        from lerobot.datasets.lerobot_dataset import LeRobotDataset
        from lerobot.utils.feature_utils import combine_feature_dicts, hw_to_dataset_features

        self.name = check_name(name)
        if not task.strip():
            raise DatasetError(
                "Bitte die Aufgabe in Worten angeben (task), z. B. „Würfel in die Schale legen“."
            )
        if not 1 <= fps <= 60:
            raise DatasetError("fps muss zwischen 1 und 60 liegen.")
        self.joints = list(joints)
        self.base_keys = list(BODY_KEYS) if base else []  # x.vel, y.vel, theta.vel
        if not self.joints and not self.base_keys:
            raise DatasetError("Nichts aufzunehmen: Das Gerät hat weder Gelenke noch eine Basis.")
        self.cameras = dict(cameras)
        self.fps = fps
        self.task = task.strip()
        self.path = dataset_path(name)
        # Reihenfolge wie LeRobot: erst die Gelenke, dann die Basis
        motors = {**{f"{j}.pos": float for j in self.joints}, **dict.fromkeys(self.base_keys, float)}
        shapes = {}
        for cam_name, camera in self.cameras.items():
            frame = camera.latest(max_age_s=5.0)
            # Die tatsächliche Bildgröße zählt, nicht die gewünschte (Kameras runden um).
            shapes[cam_name] = tuple(frame.shape) if frame is not None else camera.spec.frame_shape
        features = combine_feature_dicts(
            hw_to_dataset_features(motors, "action", use_video=True),
            hw_to_dataset_features({**motors, **shapes}, "observation", use_video=True),
        )
        repo_id = f"{REPO_PREFIX}/{self.name}"
        threads = 4 * len(self.cameras)
        if (self.path / "meta" / "info.json").exists():
            info = json.loads((self.path / "meta" / "info.json").read_text(encoding="utf-8"))
            self._check_compatible(info, features)
            self.dataset = LeRobotDataset.resume(repo_id, root=self.path, image_writer_threads=threads)
            self.resumed = True
        else:
            if self.path.exists():
                raise DatasetError(f"{self.path} gibt es schon, ist aber kein Dataset. Anderen Namen wählen.")
            self.dataset = LeRobotDataset.create(
                repo_id,
                fps,
                root=self.path,
                robot_type=robot_type,
                features=features,
                use_videos=True,
                image_writer_threads=threads,
            )
            self.resumed = False
        self.episode: EpisodeStats | None = None
        self.saved_episodes = self.dataset.meta.total_episodes
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._source: ValueSource | None = None
        self.last_error: str | None = None

    def _check_compatible(self, info: dict[str, Any], features: dict[str, Any]) -> None:
        if info.get("fps") != self.fps:
            raise DatasetError(
                f"Das Dataset '{self.name}' hat {info.get('fps')} fps, gewählt sind {self.fps}."
            )
        old = {
            k: (v.get("dtype"), list(v.get("shape", [])), v.get("names")) for k, v in info["features"].items()
        }
        for key, ft in features.items():
            new = (ft["dtype"], list(ft["shape"]), ft.get("names"))
            if key not in old:
                raise DatasetError(
                    f"Das Dataset '{self.name}' hat kein Merkmal '{key}'. Anderen Namen wählen."
                )
            if old[key][2] != new[2] and isinstance(new[2], list) and isinstance(old[key][2], list):
                missing = [n for n in new[2] if n not in old[key][2]]
                surplus = [n for n in old[key][2] if n not in new[2]]
                raise DatasetError(
                    f"'{key}' passt nicht zum vorhandenen Dataset '{self.name}'"
                    + (f": dort fehlt {', '.join(missing)}" if missing else "")
                    + (f": dort zusätzlich {', '.join(surplus)}" if surplus else "")
                    + ". Anderes Gerät oder ohne Basis? Neuen Namen wählen."
                )
            if old[key][0] != new[0] or old[key][1] != new[1]:
                raise DatasetError(
                    f"'{key}' passt nicht zum vorhandenen Dataset '{self.name}' "
                    f"({old[key][1]} statt {new[1]}). Andere Kamera-Auflösung? Neuen Namen wählen."
                )
        extra = [
            k
            for k in old
            if k not in features
            and not k.startswith(("timestamp", "frame_index", "episode_index", "index", "task_index"))
        ]
        if extra:
            raise DatasetError(
                f"Das Dataset '{self.name}' erwartet auch {extra}. "
                "Gleiche Kameras verbinden oder neuen Namen wählen."
            )

    # ------------------------------------------------------------ Episoden

    @property
    def recording(self) -> bool:
        return self._thread is not None

    def start_episode(self, source: ValueSource) -> None:
        if self.recording:
            raise DatasetError("Es läuft schon eine Episode.")
        self._source = source
        self.episode = EpisodeStats(index=self.saved_episodes, started=time.monotonic())
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name=f"dataset-{self.name}", daemon=True)
        self._thread.start()

    def _loop(self) -> None:
        from lerobot.utils.feature_utils import build_dataset_frame

        period = 1.0 / self.fps
        next_due = time.monotonic()
        episode = self.episode
        assert episode is not None and self._source is not None
        while not self._stop.is_set():
            now = time.monotonic()
            if now - episode.started > MAX_EPISODE_S:
                break
            try:
                values = self._source()
                images = {n: c.latest(MAX_FRAME_AGE_S) for n, c in self.cameras.items()}
                if values is None or any(img is None for img in images.values()):
                    episode.skipped += 1
                else:
                    obs, act = values
                    obs_values = {f"{j}.pos": float(obs[j]) for j in self.joints}
                    act_values = {f"{j}.pos": float(act[j]) for j in self.joints}
                    for key in self.base_keys:
                        obs_values[key] = float(obs[key])
                        act_values[key] = float(act[key])
                    frame = {
                        **build_dataset_frame(self.dataset.features, {**obs_values, **images}, "observation"),
                        **build_dataset_frame(self.dataset.features, act_values, "action"),
                        "task": self.task,
                    }
                    self.dataset.add_frame(frame)
                    episode.frames += 1
            except Exception as exc:  # nie still sterben: Fehler merken, Episode endet
                logger.exception("Dataset-Aufnahme")
                self.last_error = str(exc)
                break
            episode.duration_s = time.monotonic() - episode.started
            next_due += period
            wait = next_due - time.monotonic()
            if wait < 0:
                next_due = time.monotonic()
            else:
                self._stop.wait(wait)

    def _join(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5.0)
        self._thread = None

    def stop_episode(self, keep: bool = True) -> EpisodeStats:
        """Episode beenden. `keep`: speichern (Video kodieren, kann etwas dauern), sonst verwerfen."""
        if self.episode is None:
            raise DatasetError("Es läuft keine Episode.")
        self._join()
        episode, self.episode = self.episode, None
        if keep and episode.frames >= 2:
            self.dataset.save_episode()
            self.saved_episodes += 1
        else:
            self.dataset.clear_episode_buffer()
        return episode

    def close(self) -> None:
        """Laufende Episode verwerfen, Dataset abschließen (Dateien vollständig schreiben)."""
        if self.episode is not None:
            self.stop_episode(keep=False)
        self.dataset.finalize()

    def status(self) -> dict[str, Any]:
        ep = self.episode
        return {
            "name": self.name,
            "path": str(self.path),
            "task": self.task,
            "fps": self.fps,
            "cameras": list(self.cameras),
            "base": bool(self.base_keys),
            "resumed": self.resumed,
            "saved_episodes": self.saved_episodes,
            "episode": None
            if ep is None
            else {
                "index": ep.index,
                "frames": ep.frames,
                "skipped": ep.skipped,
                "duration_s": round(ep.duration_s, 1),
                "running": self.recording,
            },
            "error": self.last_error,
        }


# ------------------------------------------------------------------ Bibliothek


def list_datasets() -> list[dict[str, Any]]:
    """Alle Datasets unter recordings/datasets/ mit den wichtigsten Angaben aus meta/info.json."""
    root = datasets_root()
    items = []
    for folder in sorted(root.iterdir()) if root.is_dir() else []:
        info_path = folder / "meta" / "info.json"
        if not folder.is_dir() or not _NAME.match(folder.name) or not info_path.exists():
            continue
        try:
            info = json.loads(info_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            items.append({"name": folder.name, "error": "meta/info.json nicht lesbar"})
            continue
        features = info.get("features", {})
        cameras = [
            k.removeprefix("observation.images.") for k in features if k.startswith("observation.images.")
        ]
        fps = info.get("fps") or 0
        frames = info.get("total_frames", 0)
        size = sum(p.stat().st_size for p in folder.rglob("*") if p.is_file())
        items.append(
            {
                "name": folder.name,
                "path": str(folder),
                "codebase_version": info.get("codebase_version"),
                "robot_type": info.get("robot_type"),
                "fps": fps,
                "episodes": info.get("total_episodes", 0),
                "frames": frames,
                "duration_s": round(frames / fps, 1) if fps else None,
                "cameras": cameras,
                "tasks": info.get("total_tasks"),
                "size_mb": round(size / 1e6, 1),
                "modified": info_path.stat().st_mtime,
            }
        )
    return sorted(items, key=lambda i: i.get("modified", 0), reverse=True)


def delete_dataset(name: str) -> None:
    path = dataset_path(name)
    if not (path / "meta" / "info.json").exists():
        raise DatasetError(f"Dataset '{name}' nicht gefunden.")
    shutil.rmtree(path)


def joint_values(arm_values: dict[str, float | None]) -> dict[str, float] | None:
    """Gelenkwerte ohne Lücken, sonst None (z. B. ohne Kalibrierung)."""
    if any(v is None for v in arm_values.values()):
        return None
    return {k: float(v) for k, v in arm_values.items() if v is not None}
