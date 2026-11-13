"""Steuertabelle des Feetech STS3215.

Namen, Adressen, Größen und Kodierung kommen zur Laufzeit aus rustypot, also aus
demselben Treiber, den LeRobot benutzt. Nichts davon ist hier abgeschrieben.

Ergänzt wird nur, was rustypot nicht kennt:
- der Speicherbereich nach der Feetech-Speichertabelle V3.7 (Adressen 0–39 EEPROM,
  40–69 RAM, ab 70 nicht in V3.7 beschrieben),
- die Liste der RAM-Register, die Oberfläche und Lernskripte schreiben dürfen.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from functools import cache

MODEL = "sts3215"
EEPROM_END = 40  # Adressen darunter liegen im EEPROM (bleiben nach dem Ausschalten erhalten)
RAM_END = 70  # Adressen 40 bis 69 sind RAM laut Feetech V3.7

# RAM-Register, die ohne Expertenbestätigung geschrieben werden dürfen.
# `lock` fehlt absichtlich: Die EEPROM-Sperre bedienen nur die EEPROM-Funktionen.
RAM_WRITE_WHITELIST = frozenset(
    {"torque_enable", "acceleration", "goal_position", "goal_velocity", "torque_limit"}
)

# Erlaubte Werte für torque_enable. 128 würde die aktuelle Stellung dauerhaft auf 2048 setzen.
TORQUE_ENABLE_VALUES = frozenset({0, 1})


class Area(StrEnum):
    EEPROM = "EEPROM"
    RAM = "RAM"
    UNKNOWN = "unbekannt"


@dataclass(frozen=True)
class Register:
    name: str  # Name wie in LeRobot (kleingeschrieben)
    address: int
    size: int
    writable: bool
    encoding: str  # "unsigned" oder "sign_magnitude"
    sign_bit: int | None
    aliases: tuple[str, ...] = ()

    @property
    def area(self) -> Area:
        if self.address < EEPROM_END:
            return Area.EEPROM
        if self.address < RAM_END:
            return Area.RAM
        return Area.UNKNOWN

    @property
    def min_value(self) -> int:
        if self.sign_bit is not None:
            return -((1 << self.sign_bit) - 1)
        return 0

    @property
    def max_value(self) -> int:
        if self.sign_bit is not None:
            return (1 << self.sign_bit) - 1
        return (1 << (8 * self.size)) - 1

    def encode(self, value: int) -> int:
        """Zahl → Rohwert im Register (Betrag plus Vorzeichenbit, kein Zweierkomplement)."""
        if not self.min_value <= value <= self.max_value:
            raise ValueError(
                f"Wert {value} liegt außerhalb von {self.min_value}…{self.max_value} für '{self.name}'."
            )
        if self.sign_bit is not None and value < 0:
            return (-value) | (1 << self.sign_bit)
        return value

    def decode(self, raw: int) -> int:
        if self.sign_bit is not None:
            magnitude = raw & ((1 << self.sign_bit) - 1)
            return -magnitude if raw & (1 << self.sign_bit) else magnitude
        return raw


@cache
def definition():
    """rustypot-Definition des STS3215 (gleichzeitig Bus-Definition für das USB-Backend)."""
    import rustypot

    found = rustypot.find_model(MODEL)
    if found is None:  # pragma: no cover - nur bei kaputter Installation
        raise RuntimeError("rustypot kennt den STS3215 nicht. 'uv sync' ausführen.")
    return found[1]


@cache
def model_number() -> int:
    import rustypot

    return rustypot.find_model(MODEL)[0]


@cache
def model_names() -> dict[int, str]:
    """Modellnummer → Name aller Servos, die dieselbe Definition nutzen (777 → STS3215 usw.)."""
    return {number: name for name, number in definition().models.items()}


@cache
def baudrates() -> dict[int, int]:
    """Baudrate → Registerwert nach rustypot.

    Achtung: Für die Werte 5–7 widerspricht die Feetech-Tabelle V3.7 (siehe PLAN.md, 3.5).
    """
    return dict(definition().baudrates)


@cache
def registers() -> tuple[Register, ...]:
    """Alle Register, eins je Adresse. Bei mehreren Namen gilt der zuletzt deklarierte
    (das ist in rustypot jeweils der Name, den LeRobot benutzt)."""
    by_address: dict[int, list] = {}
    for r in definition().registers():
        by_address.setdefault(r.addr, []).append(r)
    result = []
    for address in sorted(by_address):
        entries = by_address[address]
        main = entries[-1]
        result.append(
            Register(
                name=main.name,
                address=address,
                size=main.size,
                writable="Write" in str(main.access),
                encoding=main.encoding,
                sign_bit=main.sign_bit,
                aliases=tuple(e.name for e in entries[:-1]),
            )
        )
    return tuple(result)


@cache
def _index() -> dict[str, Register]:
    index = {}
    for reg in registers():
        index[reg.name] = reg
        for alias in reg.aliases:
            index[alias] = reg
    return index


def lookup(name: str) -> Register:
    """Register über den Namen finden, Groß-/Kleinschreibung egal (wie LeRobot: "Goal_Position")."""
    try:
        return _index()[name.lower()]
    except KeyError:
        raise KeyError(f"Unbekanntes Register '{name}'.") from None


def by_address(address: int) -> Register:
    for reg in registers():
        if reg.address == address:
            return reg
    raise KeyError(f"Keine Register-Adresse {address}.")
