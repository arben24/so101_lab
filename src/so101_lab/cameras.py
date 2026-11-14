"""Kameras: aus arms.toml lesen, öffnen, laufend das neueste Bild bereithalten, suchen.

Eintrag je Kamera in `config/arms.toml` (beim Gerät, Liste `cameras`):

    cameras = [
      { name = "front", source = "/dev/v4l/by-id/usb-…-video-index0", width = 640, height = 480, fps = 30 },
      { name = "sim", source = "test" },
    ]

- `name`: Schlüssel im Dataset (`observation.images.<name>`), nur Kleinbuchstaben, Ziffern, `_`.
- `source`: Gerätepfad (unter Linux am besten `/dev/v4l/by-id/…`, der bleibt beim Umstecken
  gleich), eine Nummer (Windows/macOS: 0, 1, …) oder `test` für ein Testbild ohne Hardware.
- `width`, `height`, `fps`: gewünschtes Format (Standard 640×480, 30 Bilder/s). Die Kamera
  liefert nicht immer, was man verlangt: Tatsächliche Größe und gemessene Rate stehen im Status.
- `fourcc`: Bildformat der Kamera, z. B. `"MJPG"` (Standard, schafft bei USB 2.0 höhere Raten)
  oder `"YUYV"` (unkomprimiert).
- `rotation`: 0, 90, 180 oder 270 Grad.

Jede Kamera liest in einem eigenen Thread und hält nur das **neueste** Bild (RGB, uint8). Wer
ein Bild braucht (Vorschau, Dataset), nimmt sich dieses Bild, ohne zu warten. So bremst eine
langsame Kamera weder den Servo-Bus noch den Browser.

LeRobot-Werkzeug dazu: `lerobot-find-cameras opencv` und `lerobot.cameras.opencv.OpenCVCamera`
(benutzt dieselbe OpenCV-Schnittstelle).
"""

from __future__ import annotations

import abc
import logging
import platform
import re
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)

_NAME = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
ROTATIONS = (0, 90, 180, 270)
TEST_SOURCE = "test"


class CameraError(ValueError):
    """Text für Nutzer auf Deutsch."""


@dataclass(frozen=True)
class CameraSpec:
    name: str
    source: str  # Gerätepfad, Nummer oder "test"
    width: int = 640
    height: int = 480
    fps: int = 30
    fourcc: str | None = "MJPG"
    rotation: int = 0

    @property
    def is_test(self) -> bool:
        return self.source == TEST_SOURCE

    @property
    def frame_shape(self) -> tuple[int, int, int]:
        """(Höhe, Breite, 3) nach der Drehung, so wie das Bild im Dataset landet."""
        if self.rotation in (90, 270):
            return (self.width, self.height, 3)
        return (self.height, self.width, 3)


_KEYS = {"name", "source", "width", "height", "fps", "fourcc", "rotation"}


def parse_camera(raw: Any, where: str) -> CameraSpec:
    """Einen Eintrag aus arms.toml prüfen. Fehlertexte nennen die Stelle in der Datei."""
    if not isinstance(raw, dict):
        raise CameraError(
            f'{where}: Jede Kamera ist eine Tabelle, z. B. {{ name = "front", source = "/dev/video0" }}.'
        )
    if unknown := set(raw) - _KEYS:
        raise CameraError(
            f"{where}: unbekannte Schlüssel {sorted(unknown)} (erlaubt: {', '.join(sorted(_KEYS))})."
        )
    name = raw.get("name")
    if not isinstance(name, str) or not _NAME.match(name):
        raise CameraError(
            f"{where}: name '{name}' ungültig (Kleinbuchstaben, Ziffern, '_', z. B. \"front\")."
        )
    source = raw.get("source")
    if isinstance(source, int) and not isinstance(source, bool):
        source = str(source)
    if not isinstance(source, str) or not source:
        raise CameraError(f"{where}: Kamera '{name}' braucht source (Pfad, Nummer oder \"test\").")
    values = {}
    for key, low, high, default in (("width", 16, 4096, 640), ("height", 16, 4096, 480), ("fps", 1, 120, 30)):
        value = raw.get(key, default)
        if not isinstance(value, int) or isinstance(value, bool) or not low <= value <= high:
            raise CameraError(
                f"{where}: Kamera '{name}': {key} muss eine ganze Zahl von {low} bis {high} sein."
            )
        values[key] = value
    fourcc = raw.get("fourcc", "MJPG")
    if fourcc is not None and (not isinstance(fourcc, str) or len(fourcc) != 4):
        raise CameraError(f"{where}: Kamera '{name}': fourcc muss 4 Zeichen haben, z. B. \"MJPG\".")
    rotation = raw.get("rotation", 0)
    if rotation not in ROTATIONS:
        raise CameraError(f"{where}: Kamera '{name}': rotation muss 0, 90, 180 oder 270 sein.")
    return CameraSpec(name, source, fourcc=fourcc, rotation=rotation, **values)


def parse_cameras(raw_list: Any, where: str) -> list[CameraSpec]:
    if not isinstance(raw_list, list):
        raise CameraError(f"{where}: cameras muss eine Liste sein, z. B. cameras = [].")
    specs = [parse_camera(raw, f"{where}, Kamera Nr. {i + 1}") for i, raw in enumerate(raw_list)]
    names = [s.name for s in specs]
    if len(set(names)) != len(names):
        raise CameraError(f"{where}: Kameranamen müssen verschieden sein ({', '.join(names)}).")
    return specs


# ------------------------------------------------------------------ Kameras


class LabCamera(abc.ABC):
    """Eine geöffnete Kamera mit Lese-Thread. `latest()` liefert das neueste Bild ohne zu warten."""

    def __init__(self, spec: CameraSpec):
        self.spec = spec
        self._frame: np.ndarray | None = None
        self._frame_time = 0.0
        self._frame_count = 0
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._rate = 0.0
        self.error: str | None = None

    def open(self) -> None:
        self._open()
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name=f"camera-{self.spec.name}", daemon=True)
        self._thread.start()

    def close(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None
        self._close()

    def latest(self, max_age_s: float = 1.0) -> np.ndarray | None:
        """Neuestes Bild (RGB), oder None, wenn es keins gibt oder es älter als `max_age_s` ist."""
        with self._lock:
            if self._frame is None or time.monotonic() - self._frame_time > max_age_s:
                return None
            return self._frame

    def _loop(self) -> None:
        window_start, count = time.monotonic(), 0
        while not self._stop.is_set():
            try:
                frame = self._grab()
            except Exception as exc:  # Kamera abgezogen o. Ä.: melden, weiter versuchen
                if self.error != str(exc):
                    logger.warning("Kamera %s: %s", self.spec.name, exc)
                self.error = str(exc)
                self._stop.wait(0.5)
                continue
            if frame is None:
                continue
            frame = _rotate(frame, self.spec.rotation)
            now = time.monotonic()
            with self._lock:
                self._frame, self._frame_time = frame, now
                self._frame_count += 1
            self.error = None
            count += 1
            if now - window_start >= 1.0:
                self._rate = count / (now - window_start)
                window_start, count = now, 0

    def status(self) -> dict[str, Any]:
        frame = self._frame
        return {
            "name": self.spec.name,
            "source": self.spec.source,
            "requested": f"{self.spec.width}×{self.spec.height} @ {self.spec.fps}",
            "size": None if frame is None else [int(frame.shape[1]), int(frame.shape[0])],
            "rate_hz": round(self._rate, 1),
            "frames": self._frame_count,
            "error": self.error,
        }

    @abc.abstractmethod
    def _open(self) -> None: ...

    @abc.abstractmethod
    def _grab(self) -> np.ndarray | None:
        """Nächstes Bild (RGB), blockiert bis es da ist."""

    def _close(self) -> None:  # noqa: B027 - optional
        pass


def _rotate(frame: np.ndarray, rotation: int) -> np.ndarray:
    if rotation == 0:
        return frame
    return np.ascontiguousarray(np.rot90(frame, k={90: 3, 180: 2, 270: 1}[rotation]))


class OpenCVLabCamera(LabCamera):
    """Echte Kamera über OpenCV (V4L2 unter Linux)."""

    def __init__(self, spec: CameraSpec):
        super().__init__(spec)
        self._cap = None

    def _open(self) -> None:
        import cv2

        source: str | int = int(self.spec.source) if self.spec.source.isdigit() else self.spec.source
        if isinstance(source, str) and not Path(source).exists():
            raise CameraError(
                f"Kamera '{self.spec.name}': {source} gibt es nicht. Angesteckt? Kameras: so101-lab cameras"
            )
        cap = cv2.VideoCapture(source)
        if not cap.isOpened():
            raise CameraError(
                f"Kamera '{self.spec.name}' ({source}) lässt sich nicht öffnen. "
                "Benutzt sie ein anderes Programm? Rechte (Gruppe video)?"
            )
        if self.spec.fourcc:
            cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*self.spec.fourcc))
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.spec.width)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.spec.height)
        cap.set(cv2.CAP_PROP_FPS, self.spec.fps)
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)  # immer das neueste Bild, keine Warteschlange
        self._cap = cap

    def _grab(self) -> np.ndarray | None:
        import cv2

        ok, frame = self._cap.read()
        if not ok:
            raise CameraError(f"Kamera '{self.spec.name}' liefert kein Bild (abgezogen?).")
        return cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)

    def _close(self) -> None:
        if self._cap is not None:
            self._cap.release()
            self._cap = None


class TestPatternCamera(LabCamera):
    """Testbild ohne Hardware: Farbbalken, ein wandernder Streifen und ein Zähler."""

    __test__ = False  # kein pytest-Test, trotz des Namens

    def _open(self) -> None:
        h, w = self.spec.height, self.spec.width
        x = np.linspace(0, 255, w, dtype=np.uint8)
        y = np.linspace(0, 255, h, dtype=np.uint8)
        self._base = np.stack(
            [np.broadcast_to(x, (h, w)), np.broadcast_to(y[:, None], (h, w)), np.full((h, w), 96, np.uint8)],
            axis=-1,
        ).copy()
        self._next = time.monotonic()
        self._n = 0

    def _grab(self) -> np.ndarray | None:
        self._next += 1.0 / self.spec.fps
        wait = self._next - time.monotonic()
        if wait > 0:
            self._stop.wait(wait)
        else:
            self._next = time.monotonic()
        frame = self._base.copy()
        w = self.spec.width
        col = (self._n * 4) % w
        frame[:, col : col + max(2, w // 40)] = 255
        self._n += 1
        try:
            import cv2

            cv2.putText(frame, f"TEST {self._n}", (8, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 255), 2)
        except ImportError:
            pass
        return frame


def open_camera(spec: CameraSpec) -> LabCamera:
    camera: LabCamera = TestPatternCamera(spec) if spec.is_test else OpenCVLabCamera(spec)
    camera.open()
    return camera


def to_jpeg(frame: np.ndarray, quality: int = 80) -> bytes:
    import cv2

    ok, data = cv2.imencode(
        ".jpg", cv2.cvtColor(frame, cv2.COLOR_RGB2BGR), [cv2.IMWRITE_JPEG_QUALITY, quality]
    )
    if not ok:
        raise CameraError("Bild ließ sich nicht als JPEG kodieren.")
    return data.tobytes()


# ------------------------------------------------------------------ Suchen


def _by_id_links() -> dict[str, str]:
    """/dev/videoN → /dev/v4l/by-id/… (Linux). Der by-id-Name bleibt beim Umstecken gleich."""
    folder = Path("/dev/v4l/by-id")
    links: dict[str, str] = {}
    if folder.is_dir():
        for link in sorted(folder.iterdir()):
            try:
                links.setdefault(str(link.resolve()), str(link))
            except OSError:
                continue
    return links


def find_cameras(skip: set[str] | None = None, probe: bool = True) -> list[dict[str, Any]]:
    """Kameras dieses Rechners. Unter Linux /dev/video*, sonst die Nummern 0–9.

    `skip`: Quellen, die schon geöffnet sind (nicht noch einmal öffnen). Mit `probe` wird jede
    Kamera kurz geöffnet und ein Bild gelesen: Nur dann ist klar, ob sie Bilder liefert
    (bei USB-Kameras gibt es oft einen zweiten Knoten nur für Metadaten).
    """
    import cv2

    skip = {str(Path(s).resolve()) if s.startswith("/") else s for s in (skip or set())}
    links = _by_id_links()
    if platform.system() == "Linux":
        targets: list[str] = [
            str(p) for p in sorted(Path("/dev").glob("video*"), key=lambda p: (len(p.name), p.name))
        ]
    else:
        targets = [str(i) for i in range(10)]
    found = []
    for target in targets:
        stable = links.get(str(Path(target).resolve()), target) if target.startswith("/") else target
        resolved = str(Path(target).resolve()) if target.startswith("/") else target
        item: dict[str, Any] = {"device": target, "source": stable, "in_use": resolved in skip}
        if item["in_use"] or not probe:
            found.append(item)
            continue
        cap = cv2.VideoCapture(int(target) if target.isdigit() else target)
        try:
            if not cap.isOpened():
                if not target.isdigit():
                    item["error"] = "lässt sich nicht öffnen"
                    found.append(item)
                continue
            ok, frame = cap.read()
            fourcc = int(cap.get(cv2.CAP_PROP_FOURCC)).to_bytes(4, "little").decode(errors="replace")
            item.update(
                {
                    "delivers_frames": bool(ok),
                    "width": int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)),
                    "height": int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)),
                    "fps": round(cap.get(cv2.CAP_PROP_FPS), 1),
                    "fourcc": fourcc.strip("\x00"),
                }
            )
            found.append(item)
        finally:
            cap.release()
    return found
