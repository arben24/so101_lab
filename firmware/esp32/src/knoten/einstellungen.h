#pragma once
// Einstellungen, die im Flash (NVS) bleiben und über die Webseite geändert werden.
// Ein Raumwechsel braucht also nur ein neues WLAN auf der Webseite, keinen neuen Code.

#include <Arduino.h>

constexpr uint8_t MAX_NETZE = 3;  // z. B. Labor, Hörsaal, Seminarraum

struct Netz {
  String ssid;
  String passwort;
};

struct Einstellungen {
  String name;           // Gerätename, auch Hostname (name.local) und Name des Einrichtungs-WLANs
  String profil;         // Kennung aus profile.cpp
  uint8_t betrieb;       // protokoll::BETRIEB_WLAN oder BETRIEB_ESPNOW
  uint8_t kanal;         // Kanal ohne Router (ESP-NOW-Betrieb, Einrichtung, Notbetrieb)
  Netz netze[MAX_NETZE];
  String apPasswort;     // Einrichtungs-WLAN und Anmeldung an der Webseite (Benutzer admin)
  uint16_t watchdogMs;   // so lange ohne Befehl -> Räder stopp, Arm hält
  int16_t maxTempo;      // Obergrenze Radtempo in Schritt/s
};

void einstellungenLaden(Einstellungen& e);
void einstellungenSpeichern(const Einstellungen& e);
void einstellungenLoeschen();
String standardName(const char* profilKennung);
bool netzEingetragen(const Einstellungen& e);
