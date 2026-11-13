#pragma once
// WLAN, ESP-NOW und UDP.
//
// Betriebsarten (protokoll::Betrieb):
//   WLAN         mit dem Router verbunden. ESP-NOW läuft zusätzlich auf dem Kanal des Routers.
//   ESPNOW       bewusst ohne Router. Fester Kanal, Einrichtungs-WLAN offen.
//   NOTBETRIEB   Router nicht erreichbar. Wie ESPNOW, sucht aber jede Minute nach bekannten Netzen.
//   EINRICHTUNG  kein Netz eingetragen oder BOOT-Taster 3 s gedrückt. Wie ESPNOW.
//
// In jeder Betriebsart nimmt der Knoten Befehle über UDP und ESP-NOW an. Es steuert
// immer nur eine Quelle. Eine andere kommt erst dran, wenn die erste länger als der
// Watchdog schweigt oder jemand STOPP schickt.

#include <Arduino.h>
#include <IPAddress.h>
#include <protokoll.h>

#include "einstellungen.h"
#include "profile.h"

namespace netz {

constexpr uint16_t FIRMWARE = 1;

void starten(Einstellungen& e, const Profil& profil, bool einrichtung);
void bearbeiten();  // aus loop()

protokoll::Betrieb betrieb();
const char* betriebText();
bool wlanVerbunden();
bool apAktiv();
uint8_t kanal();
IPAddress ip();
String apName();
protokoll::Quelle steuerndeQuelle();  // QUELLE_KEINE, wenn niemand innerhalb des Watchdogs geschickt hat
String steuerndeQuelleText();

}  // namespace netz
