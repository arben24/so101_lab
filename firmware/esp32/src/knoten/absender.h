#pragma once
// Woher kam ein Paket und wohin geht die Antwort? Entweder IP und Port (UDP)
// oder MAC-Adresse (ESP-NOW). Einfacher Datentyp, darf in FreeRTOS-Queues kopiert werden.

#include <protokoll.h>
#include <string.h>

struct Absender {
  protokoll::Quelle quelle = protokoll::QUELLE_KEINE;
  uint32_t ip = 0;  // wie IPAddress intern, also Byte 0 = erste Zahl
  uint16_t port = 0;
  uint8_t mac[6] = {0};

  bool gleich(const Absender& o) const {
    if (quelle != o.quelle) return false;
    if (quelle == protokoll::QUELLE_UDP) return ip == o.ip && port == o.port;
    if (quelle == protokoll::QUELLE_ESPNOW) return memcmp(mac, o.mac, 6) == 0;
    return true;
  }
};
