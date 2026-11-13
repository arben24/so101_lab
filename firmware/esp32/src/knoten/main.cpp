// Funkknoten für LeKiwi und SO-101: Servos am Feetech-Bus, Befehle über WLAN oder ESP-NOW.
//
// Aufbau:
//   steuerung  eigene Task, 50 Hz, einziger Zugriff auf den Servo-Bus, Watchdog
//   netz       WLAN / ESP-NOW / UDP, Steuerrecht, Baken
//   webseite   Einrichtung und Status im Browser
//   main       Start, Status-LED, BOOT-Taster

#include <Arduino.h>
#include <esp_attr.h>
#include <esp_system.h>

#include "einstellungen.h"
#include "netz.h"
#include "profile.h"
#include "steuerung.h"
#include "webseite.h"

namespace {

// Überlebt einen Software-Neustart: So weiß der Knoten nach dem Taster, dass er
// in die Einrichtung soll.
RTC_NOINIT_ATTR uint32_t einrichtungMerker;
constexpr uint32_t EINRICHTUNG_MAGIC = 0x5E7A9B01;

Einstellungen cfg;

void ledBearbeiten(uint32_t tasterGedruecktMs) {
  uint32_t t = millis();
  bool an;
  if (tasterGedruecktMs >= 3000) {
    an = (t / 50) % 2;  // flackert: loslassen startet die Einrichtung, weiter halten löscht alles
  } else if (netz::steuerndeQuelle() != protokoll::QUELLE_KEINE) {
    an = true;  // jemand steuert
  } else {
    switch (netz::betrieb()) {
      case protokoll::BETRIEB_EINRICHTUNG:
      case protokoll::BETRIEB_NOTBETRIEB:
        an = (t / 100) % 2;  // schnell: Einrichtungs-WLAN offen
        break;
      case protokoll::BETRIEB_WLAN:
        an = netz::wlanVerbunden() ? (t % 2000) < 60 : (t / 500) % 2;  // kurz blitzen: bereit
        break;
      default:
        an = (t % 2000) < 60 || ((t % 2000) > 200 && (t % 2000) < 260);  // doppelt blitzen: ESP-NOW
    }
  }
  digitalWrite(STATUS_LED_PIN, an);
}

uint32_t tasterBearbeiten() {
  static uint32_t gedruecktSeit = 0;
  bool gedrueckt = digitalRead(TASTER_PIN) == LOW;
  if (gedrueckt) {
    if (!gedruecktSeit) gedruecktSeit = millis() ? millis() : 1;
    uint32_t dauer = millis() - gedruecktSeit;
    if (dauer >= 10000) {
      Serial.println("[taster] 10 s: Werkseinstellungen");
      einstellungenLoeschen();
      delay(100);
      ESP.restart();
    }
    return dauer;
  }
  if (gedruecktSeit) {
    uint32_t dauer = millis() - gedruecktSeit;
    gedruecktSeit = 0;
    if (dauer >= 3000) {
      Serial.println("[taster] 3 s: Neustart in die Einrichtung");
      einrichtungMerker = EINRICHTUNG_MAGIC;
      delay(100);
      ESP.restart();
    }
  }
  return 0;
}

}  // namespace

void setup() {
  Serial.begin(115200);
  delay(200);
  pinMode(STATUS_LED_PIN, OUTPUT);
  pinMode(TASTER_PIN, INPUT_PULLUP);

  bool einrichtung = esp_reset_reason() == ESP_RST_SW && einrichtungMerker == EINRICHTUNG_MAGIC;
  einrichtungMerker = 0;

  einstellungenLaden(cfg);
  const Profil& profil = profilSuchen(cfg.profil.c_str());
  Serial.printf("\n=== Roboter-Knoten, Firmware %u ===\n", netz::FIRMWARE);
  Serial.printf("Name %s, Profil %s, Betrieb %s, Kanal %u, Watchdog %u ms\n", cfg.name.c_str(), profil.kennung,
                cfg.betrieb == protokoll::BETRIEB_ESPNOW ? "ESP-NOW" : "WLAN", cfg.kanal, cfg.watchdogMs);

  steuerung::starten(profil, cfg.watchdogMs, cfg.maxTempo);
  netz::starten(cfg, profil, einrichtung);
  webseite::starten(cfg, profil);
}

void loop() {
  netz::bearbeiten();
  webseite::bearbeiten();
  ledBearbeiten(tasterBearbeiten());
  delay(1);
}
