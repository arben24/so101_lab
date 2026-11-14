"""Bewegungen aufnehmen, abspielen und von einem Arm auf einen anderen übertragen.

Hier steht nur Rechnung, ohne Buszugriff und ohne eigene Threads. Die Web-App ruft die
Klassen aus ihrer Zustandsabfrage (30 Hz) auf und schreibt die Ziele selbst über
`Arm.move_to` (Grenzen, größter Sprung). Tests rufen sie mit erfundenen Zeiten auf.

- `Recorder`: sammelt Rohpositionen mit Zeitstempel, ergibt eine `Trajectory`.
- `Player`: spielt eine Aufnahme ab. Erst **sanfte Anfahrt** von der Istposition zur
  Startpose (smoothstep, mindestens 2 s, im Mittel höchstens 300 Schritt/s), dann nach den
  Zeitstempeln mit Tempofaktor. Zwischen zwei Abtastungen wird linear interpoliert, deshalb
  muss die Abspielrate nicht zur Aufnahmerate passen.
- `TeleopFollow`: Der Follower folgt dem Leader. Auch hier erst sanfte Anfahrt.
- `Converter`: Rohwerte eines Arms → Rohwerte eines anderen. Zwei Arme haben für dieselbe
  Stellung **verschiedene Rohwerte** (anderer Zusammenbau, andere Kalibrierung). Deshalb
  wird wie bei LeRobot über Grad bzw. Prozent umgerechnet: Rohwert → Gelenkwert mit der
  Kalibrierung des einen Arms, Gelenkwert → Rohwert mit der Kalibrierung des anderen.
"""

from __future__ import annotations

from bisect import bisect_right
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from typing import Any

from .calibration import Calibration, MotorCalibration, joint_to_raw, raw_to_joint
from .devices.spec import DeviceSpec
from .trajectories import Trajectory

APPROACH_STEPS_PER_S = 300  # mittleres Anfahrtstempo, ca. 26°/s
APPROACH_MIN_S = 2.0
MAX_RECORDING_S = 600.0  # eine Aufnahme endet spätestens nach 10 Minuten
LEADER_TIMEOUT_S = 0.3  # so alt dürfen Leader-Werte höchstens sein, sonst stoppt die Teleoperation
START_TOLERANCE = 400  # so weit darf die Startpose außerhalb der Grenzen liegen (wird begrenzt)


def smoothstep(x: float) -> float:
    """0 → 1 mit weichem Anfang und Ende (Ableitung 0 an beiden Enden)."""
    x = max(0.0, min(1.0, x))
    return x * x * (3 - 2 * x)


def approach_duration(start: dict[str, int], goal: dict[str, int]) -> float:
    distance = max((abs(goal[j] - start[j]) for j in goal if j in start), default=0)
    return max(APPROACH_MIN_S, distance / APPROACH_STEPS_PER_S)


def blend(start: dict[str, int], goal: dict[str, int], s: float) -> dict[str, int]:
    return {j: round(start[j] + (goal[j] - start[j]) * s) for j in goal}


# ------------------------------------------------------------------ Umrechnen zwischen Armen


def _base(name: str) -> str:
    return name.removeprefix("arm_")  # LeKiwi heißt arm_shoulder_pan, der SO-101 shoulder_pan


def joint_mapping(src: DeviceSpec, dst: DeviceSpec) -> dict[str, str]:
    """Positionsgelenk des Ziels → passendes Gelenk der Quelle (gleicher Name, `arm_` zählt nicht)."""
    by_base = {_base(j.name): j.name for j in src.position_joints}
    return {j.name: by_base[_base(j.name)] for j in dst.position_joints if _base(j.name) in by_base}


def calibration_to_dict(cal: Calibration | None) -> dict[str, dict[str, int]] | None:
    return None if cal is None else {name: asdict(c) for name, c in cal.items()}


def calibration_from_dict(data: dict[str, Any] | None) -> Calibration | None:
    if not data:
        return None
    return {name: MotorCalibration(**{k: int(v) for k, v in c.items()}) for name, c in data.items()}


class Converter:
    """Rohwerte der Quelle → Rohwerte des Ziels.

    Mit beiden Kalibrierungen: über Grad/Prozent (`calibrated` ist True). Fehlt eine, werden
    Rohwerte unverändert übernommen. Das passt nur, wenn beide Arme gleich zusammengebaut
    und kalibriert sind.
    """

    def __init__(
        self,
        src: DeviceSpec,
        src_cal: Calibration | None,
        dst: DeviceSpec,
        dst_cal: Calibration | None,
    ):
        self.src, self.dst = src, dst
        self.mapping = joint_mapping(src, dst)
        self.src_cal, self.dst_cal = src_cal, dst_cal
        self.calibrated = bool(
            src_cal
            and dst_cal
            and all(s in src_cal for s in self.mapping.values())
            and all(d in dst_cal for d in self.mapping)
        )

    @property
    def identical(self) -> bool:
        """Gleiche Kalibrierung auf beiden Seiten: Die Rohwerte bleiben, wie sie sind."""
        if not self.calibrated:
            return False
        assert self.src_cal is not None and self.dst_cal is not None
        return all(self.src_cal[s] == self.dst_cal[d] and s == d for d, s in self.mapping.items())

    def __call__(self, src_positions: dict[str, int]) -> dict[str, int]:
        out: dict[str, int] = {}
        for dst_name, src_name in self.mapping.items():
            if src_name not in src_positions:
                continue
            raw = src_positions[src_name]
            if self.calibrated and not self.identical:
                assert self.src_cal is not None and self.dst_cal is not None
                value = raw_to_joint(raw, self.src.joint(src_name), self.src_cal[src_name])
                raw = joint_to_raw(value, self.dst.joint(dst_name), self.dst_cal[dst_name])
            out[dst_name] = raw
        return out

    def describe(self) -> str:
        if self.identical:
            return "gleiche Kalibrierung, Rohwerte werden direkt übernommen"
        if self.calibrated:
            return "Umrechnung über Grad/Prozent mit beiden Kalibrierungen"
        return (
            "ohne Kalibrierung auf beiden Seiten: Rohwerte werden unverändert übernommen "
            "(passt nur bei gleich zusammengebauten und kalibrierten Armen)"
        )


# ------------------------------------------------------------------ Aufnehmen


@dataclass
class Recorder:
    name: str
    joints: list[str]
    started: float
    max_s: float = MAX_RECORDING_S
    times: list[float] = field(default_factory=list)
    rows: list[tuple[int, ...]] = field(default_factory=list)

    def add(self, positions: dict[str, int], now: float) -> bool:
        """Eine Abtastung anhängen. False, wenn die Höchstdauer erreicht ist (dann nichts angehängt)."""
        t = now - self.started
        if t > self.max_s:
            return False
        self.times.append(t)
        self.rows.append(tuple(positions[j] for j in self.joints))
        return True

    def trajectory(self, meta: dict[str, Any]) -> Trajectory:
        return Trajectory(self.name, tuple(self.joints), list(self.times), list(self.rows), meta)

    def status(self, now: float) -> dict[str, Any]:
        return {
            "name": self.name,
            "elapsed_s": round(now - self.started, 1),
            "samples": len(self.times),
            "max_s": self.max_s,
        }


# ------------------------------------------------------------------ Abspielen


class Player:
    """Spielt umgerechnete Rohwerte ab: `targets(now)` liefert die Ziele für diesen Takt."""

    def __init__(
        self,
        name: str,
        joints: Sequence[str],
        times: Sequence[float],
        rows: Sequence[dict[str, int]],
        present: dict[str, int],
        now: float,
        speed: float = 1.0,
    ):
        if not 0.1 <= speed <= 3:
            raise ValueError("Tempofaktor muss zwischen 0.1 und 3 liegen.")
        self.name = name
        self.joints = list(joints)
        self.times = list(times)
        self.rows = list(rows)
        self.speed = speed
        self.present = {j: present[j] for j in self.joints}
        self.started = now
        self.approach_s = approach_duration(self.present, self.rows[0])
        self.play_s = self.times[-1] / speed
        self.finished = False

    @property
    def total_s(self) -> float:
        return self.approach_s + self.play_s

    def phase(self, now: float) -> str:
        if self.finished:
            return "fertig"
        return "anfahrt" if now - self.started < self.approach_s else "abspielen"

    def targets(self, now: float) -> dict[str, int]:
        t = now - self.started
        if t < self.approach_s:
            return blend(self.present, self.rows[0], smoothstep(t / self.approach_s))
        tp = (t - self.approach_s) * self.speed
        if tp >= self.times[-1]:
            self.finished = True
            return dict(self.rows[-1])
        i = max(0, bisect_right(self.times, tp) - 1)
        t0, t1 = self.times[i], self.times[i + 1]
        s = 0.0 if t1 <= t0 else (tp - t0) / (t1 - t0)
        return blend(self.rows[i], self.rows[i + 1], s)

    def status(self, now: float) -> dict[str, Any]:
        elapsed = min(now - self.started, self.total_s)
        return {
            "name": self.name,
            "phase": self.phase(now),
            "speed": self.speed,
            "elapsed_s": round(elapsed, 1),
            "approach_s": round(self.approach_s, 1),
            "total_s": round(self.total_s, 1),
        }


@dataclass
class PlaybackPlan:
    """Für ein Gerät vorbereitete Aufnahme: Zielgelenke, Rohwerte je Abtastung, Hinweise."""

    joints: list[str]
    rows: list[dict[str, int]]
    converter: Converter
    notes: list[str] = field(default_factory=list)


def prepare_playback(
    traj: Trajectory,
    spec: DeviceSpec,
    calibration: Calibration | None,
    limits,
    calibration_id: str | None = None,
    current_offsets: dict[str, int] | None = None,
) -> PlaybackPlan:
    """Aufnahme für ein Gerät vorbereiten: Gelenke zuordnen, umrechnen, auf die Grenzen begrenzen.

    - `limits(name)` liefert den erlaubten Rohbereich eines Gelenks des Zielgeräts,
      `calibration_id` die Kalibrier-ID des Zielgeräts.
    - `current_offsets`: Homing-Offsets, die **jetzt** in den Servos des Zielgeräts stehen.
      Der Servo meldet `Rohwert = tatsächliche Stellung − Homing-Offset`. Ändert sich der Offset
      (neu kalibriert), verschieben sich alle Rohwerte. Steht in der Aufnahme, mit welchen
      Offsets sie gemacht wurde (`homing_offsets`), wird das herausgerechnet.
    - Werte außerhalb der Gelenkgrenzen werden auf die Grenzen begrenzt (wie bei jedem
      `move_to`) und in `notes` gemeldet. Typisch: Die Aufnahme beginnt in der Ruhestellung am
      Anschlag, ein paar Schritte jenseits des Bereichs, den die Kalibrierung gesehen hat.
      Nur wenn die Startpose weit außerhalb liegt (mehr als `START_TOLERANCE`), passt die
      Aufnahme offensichtlich nicht zum Arm: dann ValueError.
    """
    from .devices import get_device_spec

    try:
        src_spec = get_device_spec(traj.meta.get("device_type") or spec.type)
    except KeyError:
        src_spec = spec
    src_cal = calibration_from_dict(traj.meta.get("calibration"))
    if (
        src_cal is None
        and src_spec.type == spec.type
        and calibration_id is not None
        and traj.meta.get("calibration_id") == calibration_id
    ):
        # Ältere Aufnahmen (Skript 06 vor Phase 6) nennen nur die ID: gleiche ID = gleiche Kalibrierung.
        src_cal = calibration
    converter = Converter(src_spec, src_cal, spec, calibration)
    used = {src: dst for dst, src in converter.mapping.items() if src in traj.joints}
    unknown = [j for j in traj.joints if j not in used]
    if unknown or not used:
        missing = unknown or list(traj.joints)
        raise ValueError(f"Die Aufnahme passt nicht zum Gerät {spec.type}: keine Gelenke für {missing}.")
    joints = [used[j] for j in traj.joints]
    notes: list[str] = []
    if calibration is None:
        notes.append(
            "Keine Kalibrierdatei für dieses Gerät: Grenzen sind 0…4095, Gelenke können an einen "
            "Anschlag fahren. Kalibrieren wird empfohlen."
        )
    if converter.calibrated and not converter.identical:
        notes.append("Andere Kalibrierung als bei der Aufnahme: Umrechnung über Grad/Prozent.")
    elif not converter.calibrated and traj.meta.get("device_type", spec.type) != spec.type:
        notes.append(f"Aufnahme von {traj.meta.get('device_type')} ohne Kalibrierung: Rohwerte unverändert.")

    # Homing-Offsets: nur nötig, wenn nicht ohnehin über die Kalibrierung umgerechnet wird.
    shift: dict[str, int] = {}
    rec_offsets = traj.meta.get("homing_offsets") or {}
    if current_offsets and rec_offsets and not (converter.calibrated and not converter.identical):
        for src, dst in used.items():
            if src in rec_offsets and dst in current_offsets:
                delta = int(rec_offsets[src]) - int(current_offsets[dst])
                if delta:
                    shift[dst] = delta
        if shift:
            listing = ", ".join(f"{n} {d:+d}" for n, d in shift.items())
            notes.append(
                f"Homing-Offsets haben sich seit der Aufnahme geändert, Rohwerte verschoben: {listing}."
            )
    elif current_offsets and not rec_offsets and not converter.calibrated:
        notes.append(
            "Die Aufnahme enthält keine Homing-Offsets (älteres Format). Wurde seitdem neu kalibriert, "
            "ist die Bewegung verschoben."
        )

    rows: list[dict[str, int]] = []
    clamped: dict[str, int] = {}
    worst_start: tuple[str, int, int, int] | None = None
    for index, raw_row in enumerate(traj.positions):
        row = converter(dict(zip(traj.joints, raw_row, strict=True)))
        for name in row:
            value = row[name] + shift.get(name, 0)
            low, high = limits(name)
            bounded = min(high, max(low, value))
            if bounded != value:
                clamped[name] = clamped.get(name, 0) + 1
                outside = abs(value - bounded)
                if index == 0 and (worst_start is None or outside > worst_start[1]):
                    worst_start = (name, outside, value, bounded)
            row[name] = bounded
        rows.append(row)
    if worst_start is not None and worst_start[1] > START_TOLERANCE:
        name, _, value, _ = worst_start
        low, high = limits(name)
        raise ValueError(
            f"Startpose: {name} = {value} liegt weit außerhalb der Gelenkgrenzen {low}…{high} "
            f"(mehr als {START_TOLERANCE} Schritt). Die Aufnahme passt nicht zu diesem Arm oder dieser "
            "Kalibrierung."
        )
    if clamped:
        total = len(traj.positions)
        listing = ", ".join(f"{n} ({count} von {total} Werten)" for n, count in clamped.items())
        notes.append(f"Auf die Gelenkgrenzen begrenzt: {listing}.")
    return PlaybackPlan(joints, rows, converter, notes)


# ------------------------------------------------------------------ Teleoperation


class TeleopFollow:
    """Follower folgt dem Leader, nach einer sanften Anfahrt zu dessen aktueller Stellung."""

    def __init__(self, leader: str, converter: Converter, present: dict[str, int], now: float):
        if not converter.calibrated:
            raise ValueError(
                "Teleoperation braucht die Kalibrierung von Leader und Follower: Zwei Arme haben für "
                "dieselbe Stellung verschiedene Rohwerte. Erst beide kalibrieren."
            )
        self.leader = leader
        self.converter = converter
        self.present = dict(present)
        self.started = now
        self.approach_s: float | None = None  # wird beim ersten Leader-Wert festgelegt

    def targets(self, leader_positions: dict[str, int], now: float) -> dict[str, int]:
        goal = self.converter(leader_positions)
        if self.approach_s is None:
            self.approach_s = approach_duration(self.present, goal)
            self.started = now
        t = now - self.started
        if t < self.approach_s:
            return blend({j: self.present[j] for j in goal}, goal, smoothstep(t / self.approach_s))
        return goal

    def phase(self, now: float) -> str:
        if self.approach_s is None or now - self.started < self.approach_s:
            return "anfahrt"
        return "folgt"

    def status(self, now: float) -> dict[str, Any]:
        return {"leader": self.leader, "phase": self.phase(now), "conversion": self.converter.describe()}
