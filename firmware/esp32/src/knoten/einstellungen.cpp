#include "einstellungen.h"

#include <Preferences.h>
#include <esp_mac.h>
#include <protokoll.h>

#include "profile.h"

namespace {
constexpr const char* BEREICH = "knoten";
const char* const SSID_SCHLUESSEL[MAX_NETZE] = {"ssid0", "ssid1", "ssid2"};
const char* const PW_SCHLUESSEL[MAX_NETZE] = {"pw0", "pw1", "pw2"};
}  // namespace

String standardName(const char* profilKennung) {
  uint8_t mac[6];
  esp_read_mac(mac, ESP_MAC_WIFI_STA);
  char name[16];
  snprintf(name, sizeof name, "%s-%02x%02x", profilSuchen(profilKennung).kurzname, mac[4], mac[5]);
  return String(name);
}

bool netzEingetragen(const Einstellungen& e) {
  for (const Netz& n : e.netze)
    if (n.ssid.length()) return true;
  return false;
}

void einstellungenLaden(Einstellungen& e) {
  Preferences p;
  p.begin(BEREICH, false);
  e.profil = p.getString("profil", "lekiwi");
  e.name = p.getString("name", "");
  if (e.name.isEmpty()) e.name = standardName(e.profil.c_str());
  e.betrieb = p.getUChar("betrieb", protokoll::BETRIEB_WLAN);
  e.kanal = p.getUChar("kanal", 1);
  for (uint8_t i = 0; i < MAX_NETZE; i++) {
    e.netze[i].ssid = p.getString(SSID_SCHLUESSEL[i], "");
    e.netze[i].passwort = p.getString(PW_SCHLUESSEL[i], "");
  }
  e.apPasswort = p.getString("appw", "roboter123");
  e.watchdogMs = p.getUShort("watchdog", 300);
  e.maxTempo = p.getShort("maxtempo", 3000);
  p.end();

  // Unsinnige Werte abfangen, falls der Speicher von einer anderen Firmware stammt
  if (e.betrieb > protokoll::BETRIEB_ESPNOW) e.betrieb = protokoll::BETRIEB_WLAN;
  if (e.kanal < 1 || e.kanal > 13) e.kanal = 1;
  if (e.watchdogMs < 100 || e.watchdogMs > 2000) e.watchdogMs = 300;
  if (e.maxTempo < 100 || e.maxTempo > 3400) e.maxTempo = 3000;
  if (e.apPasswort.length() < 8) e.apPasswort = "roboter123";
}

void einstellungenSpeichern(const Einstellungen& e) {
  Preferences p;
  p.begin(BEREICH, false);
  p.putString("profil", e.profil);
  p.putString("name", e.name);
  p.putUChar("betrieb", e.betrieb);
  p.putUChar("kanal", e.kanal);
  for (uint8_t i = 0; i < MAX_NETZE; i++) {
    p.putString(SSID_SCHLUESSEL[i], e.netze[i].ssid);
    p.putString(PW_SCHLUESSEL[i], e.netze[i].passwort);
  }
  p.putString("appw", e.apPasswort);
  p.putUShort("watchdog", e.watchdogMs);
  p.putShort("maxtempo", e.maxTempo);
  p.end();
}

void einstellungenLoeschen() {
  Preferences p;
  p.begin(BEREICH, false);
  p.clear();
  p.end();
}
