#pragma once
// Welche Servos hängen am Bus und in welcher Betriebsart?
// Das Profil wird auf der Einrichtungsseite gewählt, nicht im Code.

#include <protokoll.h>
#include <stddef.h>

struct ServoProfil {
  uint8_t id;
  uint8_t modus;     // protokoll::Modus
  const char* name;  // Gelenkname wie in LeRobot
};

struct Profil {
  const char* kennung;       // wird gespeichert, z. B. "lekiwi"
  const char* kurzname;      // Anfang des Gerätenamens, z. B. "lekiwi" -> lekiwi-3fa2
  const char* beschreibung;  // für die Webseite
  const ServoProfil* servos;
  uint8_t anzahl;
  bool nurLesen;  // nie Drehmoment einschalten (Leader-Arm, wird von Hand bewegt)
};

extern const Profil PROFILE[];
extern const size_t PROFIL_ANZAHL;

// Unbekannte Kennung -> erstes Profil (LeKiwi)
const Profil& profilSuchen(const char* kennung);
