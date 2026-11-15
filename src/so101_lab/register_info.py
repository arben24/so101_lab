"""Einheit und Bedeutung der STS3215-Register (für Lernskript 02, Doku und Register-Explorer).

Quelle: Feetech-Speichertabelle V3.7 (`STS3215_Speichertabelle_V3.7.xls`, Link in
`firmware/esp32/doku/sts3215/README.md`),
Spalten „Unit“ und „Value Parsing“. Die Registernamen und Adressen kommen aus rustypot
(`registers.py`), hier steht nur der erklärende Text dazu.

Die Tabelle V3.7 beschreibt die 7,4-V-Variante. Startwerte und Spannungsgrenzen der
12-V-Variante können abweichen.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RegisterInfo:
    unit: str
    text: str


_STATUS_BITS = "Bit 0 Spannung, 1 Sensor, 2 Temperatur, 3 Strom, 4 Winkel, 5 Überlast"

INFO: dict[str, RegisterInfo] = {
    "firmware_major_version": RegisterInfo("", "Firmware, Hauptversion"),
    "firmware_minor_version": RegisterInfo("", "Firmware, Nebenversion"),
    "model_number": RegisterInfo(
        "", "Modellnummer, 777 = STS3215 (Feetech nennt Adresse 3 und 4 „Servo-Version“, 9.3)"
    ),
    "id": RegisterInfo("", "Adresse am Bus, 0–253, nie doppelt (254 = Broadcast). Ändern: change_id()"),
    "baud_rate": RegisterInfo("Code 0–7", "Baudrate als Code. Ändern: change_baudrate()"),
    "return_delay_time": RegisterInfo("2 µs", "Wartezeit des Servos vor der Antwort"),
    "response_status_level": RegisterInfo("", "0: nur Lesen und Ping werden beantwortet, 1: jeder Befehl"),
    "min_position_limit": RegisterInfo("Schritt", "untere Bewegungsgrenze (kleiner als die obere)"),
    "max_position_limit": RegisterInfo("Schritt", "obere Bewegungsgrenze"),
    "max_temperature_limit": RegisterInfo("°C", "Temperaturgrenze"),
    "max_voltage_limit": RegisterInfo("0,1 V", "obere Spannungsgrenze"),
    "min_voltage_limit": RegisterInfo("0,1 V", "untere Spannungsgrenze"),
    "max_torque_limit": RegisterInfo(
        "0,1 %", "größtes Drehmoment, wird beim Einschalten nach torque_limit kopiert"
    ),
    "phase": RegisterInfo("Bits", "Sonderfunktionen, nur mit Grund ändern"),
    "unloading_condition": RegisterInfo("Bitmaske", f"Schutz abschalten bei: {_STATUS_BITS}"),
    "led_alarm_condition": RegisterInfo("Bitmaske", f"LED blinkt bei: {_STATUS_BITS}"),
    "p_coefficient": RegisterInfo("", "Lageregelung, P-Anteil"),
    "d_coefficient": RegisterInfo("", "Lageregelung, D-Anteil"),
    "i_coefficient": RegisterInfo("", "Lageregelung, I-Anteil"),
    "minimum_startup_force": RegisterInfo("0,1 %", "kleinstes Anlaufmoment"),
    "cw_dead_zone": RegisterInfo("Schritt", "Unempfindlichkeitszone im Uhrzeigersinn"),
    "ccw_dead_zone": RegisterInfo("Schritt", "Unempfindlichkeitszone gegen den Uhrzeigersinn"),
    "protection_current": RegisterInfo("6,5 mA", "Stromgrenze"),
    "angular_resolution": RegisterInfo("", "Vergrößerungsfaktor der Winkelauflösung (1–3)"),
    "homing_offset": RegisterInfo("Schritt", "Positionskorrektur ±2047 (Betrag, Vorzeichen in Bit 11)"),
    "operating_mode": RegisterInfo("", "0 Position, 1 Geschwindigkeit, 2 PWM, 3 Schritt"),
    "protective_torque": RegisterInfo("1 %", "Moment nach dem Überlastschutz"),
    "protection_time": RegisterInfo("10 ms", "Dauer der Überlast, bis der Schutz greift"),
    "overload_torque": RegisterInfo("1 %", "Momentschwelle, ab der die Überlast gezählt wird"),
    "speed_closed_loop_p_coefficient": RegisterInfo("", "Geschwindigkeitsregelung (Modus 1), P-Anteil"),
    "over_current_protection_time": RegisterInfo("10 ms", "Dauer des Überstroms, bis der Schutz greift"),
    "velocity_closed_loop_i_coefficient": RegisterInfo("0,1", "Geschwindigkeitsregelung (Modus 1), I-Anteil"),
    "torque_enable": RegisterInfo("", "0 aus, 1 an. Der Wert 128 verstellt die Nullstellung: nie schreiben"),
    "acceleration": RegisterInfo("100 Schritt/s²", "Beschleunigung und Bremsen, 0–254"),
    "goal_position": RegisterInfo("Schritt", "Zielposition (Positionsmodus)"),
    "goal_time": RegisterInfo("0,1 %", "Laufzeit im PWM-Modus (Modus 2)"),
    "goal_velocity": RegisterInfo(
        "Schritt/s",
        "Tempo, höchstens 3400: Fahrtempo zum Ziel (Positionsmodus) bzw. Solltempo (Geschwindigkeitsmodus)",
    ),
    "torque_limit": RegisterInfo("0,1 %", "aktuelle Momentgrenze, 1000 = 100 %"),
    "lock": RegisterInfo("", "0: EEPROM-Änderungen bleiben nach dem Ausschalten erhalten, 1: gehen verloren"),
    "present_position": RegisterInfo("Schritt", "aktuelle Position, 4096 Schritt = 360°"),
    "present_velocity": RegisterInfo("Schritt/s", "aktuelles Tempo"),
    "present_load": RegisterInfo("0,1 %", "Tastverhältnis der Motoransteuerung, Vorzeichen = Richtung"),
    "present_voltage": RegisterInfo("0,1 V", "Versorgungsspannung am Servo"),
    "present_temperature": RegisterInfo("°C", "Temperatur im Servo"),
    "status": RegisterInfo("Bitmaske", f"Fehler, 1 = aufgetreten: {_STATUS_BITS}"),
    "moving": RegisterInfo("", "1 solange der Servo sich bewegt"),
    "present_current": RegisterInfo("6,5 mA", "aktueller Strom"),
}

_UNDOCUMENTED = RegisterInfo("", "in der Feetech-Tabelle V3.7 nicht beschrieben")


def info(name: str) -> RegisterInfo:
    """Einheit und Text zu einem Registernamen. Unbekannte Register liefern einen Hinweis."""
    return INFO.get(name, _UNDOCUMENTED)
