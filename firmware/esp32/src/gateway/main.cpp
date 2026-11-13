// ESP-NOW-Gateway: zweiter ESP32 am USB-Port des PCs.
//
// Reicht Pakete unverändert zwischen USB und ESP-NOW durch. Auf USB laufen sie in
// SLIP-Rahmen (RFC 1055), weil eine serielle Leitung keine Paketgrenzen kennt:
//
//   PC -> Gateway:  C0 | Ziel-MAC (6) | Paket | C0      Ziel FF:FF:FF:FF:FF:FF = an alle
//   Gateway -> PC:  C0 | Quell-MAC (6) | Paket | C0
//   Ziel/Quelle 00:00:00:00:00:00 = das Gateway selbst (GW_INFO_ANFRAGE, GW_KANAL)
//
// Im Rahmen werden C0 als DB DC und DB als DB DD geschrieben.
//
// Kanal: ESP-NOW funkt nur auf einem Kanal. Ein Knoten im WLAN-Betrieb sitzt auf
// dem Kanal seines Routers. Das Gateway sucht ihn deshalb selbst: Hört es 3 s
// nichts, springt es reihum über die Kanäle 1 bis 13 und schickt jeweils ein PING,
// bis ein Knoten antwortet. Über GW_KANAL lässt sich ein Kanal auch fest einstellen.

#include <Arduino.h>
#include <WiFi.h>
#include <esp_mac.h>
#include <esp_now.h>
#include <esp_wifi.h>
#include <protokoll.h>

using namespace protokoll;

namespace {

constexpr uint32_t BAUD = 921600;
constexpr uint16_t FIRMWARE = 1;
constexpr uint32_t STILLE_MS = 3000;       // so lange nichts gehört -> Kanal suchen
constexpr uint32_t SUCHSCHRITT_MS = 150;   // Verweildauer je Kanal bei der Suche
constexpr uint8_t MAX_PEERS = 8;

constexpr uint8_t SLIP_END = 0xC0, SLIP_ESC = 0xDB, SLIP_ESC_END = 0xDC, SLIP_ESC_ESC = 0xDD;
constexpr uint8_t BROADCAST_MAC[6] = {0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF};
constexpr uint8_t GATEWAY_MAC[6] = {0, 0, 0, 0, 0, 0};

struct EspNowPaket {
  uint8_t mac[6];
  uint8_t laenge;
  uint8_t daten[MAX_PAKET];
};

QueueHandle_t eingang;
uint8_t kanal_ = 1;
bool automatisch = true, suchend = true;
uint32_t letzterEmpfangMs = 0, letzterSchrittMs = 0, letzterVerkehrMs = 0;
uint32_t empfangen = 0, sendefehler = 0;
uint16_t seq = 0;
uint8_t peers[MAX_PEERS][6];
uint8_t peerAnzahl = 0, peerNaechster = 0;

void beiEmpfang(const uint8_t* mac, const uint8_t* daten, int laenge) {
  if (laenge <= 0 || laenge > (int)MAX_PAKET) return;
  EspNowPaket p;
  memcpy(p.mac, mac, 6);
  p.laenge = (uint8_t)laenge;
  memcpy(p.daten, daten, laenge);
  xQueueSend(eingang, &p, 0);
}

void beiGesendet(const uint8_t*, esp_now_send_status_t status) {
  if (status != ESP_NOW_SEND_SUCCESS) sendefehler++;  // nur bei Unicast aussagekräftig
}

bool peerSicherstellen(const uint8_t* mac) {
  if (esp_now_is_peer_exist(mac)) return true;
  if (peerAnzahl == MAX_PEERS) {
    esp_now_del_peer(peers[peerNaechster]);
  } else {
    peerAnzahl++;
  }
  memcpy(peers[peerNaechster], mac, 6);
  peerNaechster = (peerNaechster + 1) % MAX_PEERS;
  esp_now_peer_info_t info = {};
  memcpy(info.peer_addr, mac, 6);
  info.channel = 0;
  info.ifidx = WIFI_IF_STA;
  return esp_now_add_peer(&info) == ESP_OK;
}

void kanalSetzen(uint8_t k) {
  esp_wifi_set_promiscuous(true);
  esp_wifi_set_channel(k, WIFI_SECOND_CHAN_NONE);
  esp_wifi_set_promiscuous(false);
  kanal_ = k;
}

void funken(const uint8_t* mac, const uint8_t* paket, size_t n) {
  letzterVerkehrMs = millis();
  if (!peerSicherstellen(mac) || esp_now_send(mac, paket, n) != ESP_OK) sendefehler++;
}

// ------------------------------------------------------------- SLIP / USB

void rahmenSenden(const uint8_t* mac, const uint8_t* paket, size_t n) {
  uint8_t puffer[2 * (6 + MAX_PAKET) + 2];
  size_t m = 0;
  puffer[m++] = SLIP_END;
  auto schreiben = [&](uint8_t b) {
    if (b == SLIP_END) {
      puffer[m++] = SLIP_ESC;
      puffer[m++] = SLIP_ESC_END;
    } else if (b == SLIP_ESC) {
      puffer[m++] = SLIP_ESC;
      puffer[m++] = SLIP_ESC_ESC;
    } else {
      puffer[m++] = b;
    }
  };
  for (int i = 0; i < 6; i++) schreiben(mac[i]);
  for (size_t i = 0; i < n; i++) schreiben(paket[i]);
  puffer[m++] = SLIP_END;
  Serial.write(puffer, m);
}

void gwInfoSenden(uint16_t antwortSeq) {
  GwInfo g{};
  g.kanal = kanal_;
  g.automatisch = automatisch;
  g.suchend = suchend;
  esp_read_mac(g.mac, ESP_MAC_WIFI_STA);
  g.firmware = FIRMWARE;
  g.empfangen = empfangen;
  g.sendefehler = sendefehler;
  uint8_t paket[MAX_PAKET];
  size_t n = bauen(paket, GW_INFO, antwortSeq, &g, sizeof g);
  rahmenSenden(GATEWAY_MAC, paket, n);
}

void rahmenVerarbeiten(const uint8_t* rahmen, size_t n) {
  if (n < 6 + KOPF_GROESSE + 2 || n > 6 + MAX_PAKET) return;
  const uint8_t* mac = rahmen;
  const uint8_t* paket = rahmen + 6;
  size_t laenge = n - 6;

  if (memcmp(mac, GATEWAY_MAC, 6) == 0) {  // an das Gateway selbst
    Kopf k;
    const uint8_t* nd;
    if (!pruefen(paket, laenge, k, nd)) return;
    if (k.typ == GW_KANAL && k.laenge == sizeof(GwKanal)) {
      uint8_t c = nd[0];
      if (c == 0) {
        automatisch = true;
        suchend = true;
      } else if (c <= 13) {
        automatisch = false;
        suchend = false;
        kanalSetzen(c);
      }
    }
    if (k.typ == GW_INFO_ANFRAGE || k.typ == GW_KANAL) gwInfoSenden(k.seq);
    return;
  }
  funken(mac, paket, laenge);
}

void usbLesen() {
  static uint8_t rx[6 + MAX_PAKET + 8];
  static size_t n = 0;
  static bool esc = false, ueberlauf = false;
  while (Serial.available()) {
    uint8_t b = Serial.read();
    if (b == SLIP_END) {
      if (n && !ueberlauf) rahmenVerarbeiten(rx, n);
      n = 0;
      esc = ueberlauf = false;
      continue;
    }
    if (b == SLIP_ESC) {
      esc = true;
      continue;
    }
    if (esc) {
      b = b == SLIP_ESC_END ? SLIP_END : b == SLIP_ESC_ESC ? SLIP_ESC : b;
      esc = false;
    }
    if (n < sizeof rx) {
      rx[n++] = b;
    } else {
      ueberlauf = true;
    }
  }
}

// ------------------------------------------------------------- Funk

void funkLesen() {
  EspNowPaket p;
  while (xQueueReceive(eingang, &p, 0) == pdTRUE) {
    Kopf k;
    const uint8_t* nd;
    if (!pruefen(p.daten, p.laenge, k, nd)) continue;  // fremde ESP-NOW-Geräte ausblenden
    empfangen++;
    letzterEmpfangMs = letzterVerkehrMs = millis();
    suchend = false;
    rahmenSenden(p.mac, p.daten, p.laenge);
  }
}

void kanalSuchen() {
  if (!automatisch) return;
  uint32_t jetzt = millis();
  if (!suchend && jetzt - letzterEmpfangMs > STILLE_MS) suchend = true;
  if (!suchend || jetzt - letzterSchrittMs < SUCHSCHRITT_MS) return;
  letzterSchrittMs = jetzt;
  kanalSetzen(kanal_ % 13 + 1);
  uint8_t paket[MAX_PAKET];
  size_t n = bauen(paket, PING, seq++, nullptr, 0);
  funken(BROADCAST_MAC, paket, n);
}

void ledBearbeiten() {
  uint32_t t = millis();
  bool an = suchend ? (t / 100) % 2 : !(t - letzterVerkehrMs < 30);  // suchen: blinkt, sonst an mit Flackern
  digitalWrite(STATUS_LED_PIN, an);
}

}  // namespace

void setup() {
  Serial.setRxBufferSize(2048);
  Serial.begin(BAUD);
  pinMode(STATUS_LED_PIN, OUTPUT);
  eingang = xQueueCreate(16, sizeof(EspNowPaket));

  WiFi.persistent(false);
  WiFi.mode(WIFI_STA);
  WiFi.disconnect();
  WiFi.setSleep(false);
  esp_now_init();
  esp_now_register_recv_cb(beiEmpfang);
  esp_now_register_send_cb(beiGesendet);
  kanalSetzen(1);
  // Keine Textausgaben: Die serielle Leitung gehört dem Binärprotokoll.
}

void loop() {
  usbLesen();
  funkLesen();
  kanalSuchen();
  ledBearbeiten();
  delay(1);
}
