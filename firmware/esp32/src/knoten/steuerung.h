#pragma once
// Servo-Steuerung als eigene FreeRTOS-Task mit festem Takt (50 Hz).
//
// Die Task ist der einzige Teil, der den Servo-Bus benutzt. Das Netz legt nur
// Sollwerte ab und holt Istwerte ab. Auch wenn das Netz hängt (WLAN-Suche,
// Webseite), läuft der Watchdog weiter: Kommt länger als watchdogMs kein
// Befehl, stehen die Räder und der Arm hält seine Stellung.

#include <protokoll.h>

#include "absender.h"
#include "profile.h"

namespace steuerung {

struct Abbild {
  uint32_t zeit_ms;
  bool watchdog;  // sicherer Zustand aktiv
  uint16_t zyklus_us;
  uint16_t busfehler;
  uint8_t online;
  uint8_t anzahl;
  protokoll::ServoZustand servos[protokoll::MAX_SERVOS];
};

struct RegAuftrag {
  Absender absender;
  uint16_t seq;
  bool schreiben;
  uint8_t id, adresse, laenge;
  uint8_t daten[protokoll::MAX_REG_DATEN];
};
using RegErgebnis = RegAuftrag;  // gleicher Aufbau, daten = gelesene Bytes, laenge 0 bei Fehler

void starten(const Profil& profil, uint16_t watchdogMs, int16_t maxTempo);

void sollwerteSetzen(const protokoll::SollEintrag* eintraege, uint8_t anzahl);
void stopp();  // sofort sicherer Zustand, bis der nächste Befehl kommt
void abbildHolen(Abbild& a);

bool regAuftragStellen(const RegAuftrag& a);  // false = Warteschlange voll
bool regErgebnisHolen(RegErgebnis& e, bool& ok);

}  // namespace steuerung
