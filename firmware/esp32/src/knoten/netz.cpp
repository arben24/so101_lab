#include "netz.h"

#include <ESPmDNS.h>
#include <WiFi.h>
#include <WiFiUdp.h>
#include <esp_mac.h>
#include <esp_now.h>
#include <esp_wifi.h>

#include "absender.h"
#include "steuerung.h"

using namespace protokoll;

namespace netz {
namespace {

constexpr uint32_t BAKE_MS = 1000;              // INFO als Broadcast, damit PC und Gateway den Knoten finden
constexpr uint32_t WLAN_VERBINDEN_MS = 15000;   // Zeit für den ersten Verbindungsaufbau
constexpr uint32_t WLAN_VERLOREN_MS = 60000;    // so lange ohne Router -> Notbetrieb
constexpr uint32_t WLAN_NEUVERSUCH_MS = 60000;  // im Notbetrieb so oft nach bekannten Netzen suchen
constexpr uint32_t ESPNOW_RUHE_MS = 10000;      // Kanal nur wechseln, wenn ESP-NOW so lange still war
constexpr uint8_t MAX_PEERS = 8;
constexpr uint8_t BROADCAST_MAC[6] = {0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF};

struct EspNowPaket {
  uint8_t mac[6];
  uint8_t laenge;
  uint8_t daten[MAX_PAKET];
};

Einstellungen* cfg = nullptr;
const Profil* profil_ = nullptr;
Betrieb betrieb_ = BETRIEB_EINRICHTUNG;
WiFiUDP udp;
QueueHandle_t espnowEingang = nullptr;

uint8_t peers[MAX_PEERS][6];
uint8_t peerAnzahl = 0, peerNaechster = 0;

Absender aktive;  // wer zuletzt steuern durfte
uint32_t aktiveMs = 0;
uint16_t letzteSeq = 0, quittung = 0, bakenSeq = 0;

uint32_t letzteBakeMs = 0, espnowEmpfangMs = 0, wlanWegSeitMs = 0, letzterVersuchMs = 0;
uint8_t eingang[MAX_PAKET + 1];
uint8_t ausgang[MAX_PAKET];

// ---------------------------------------------------------------- ESP-NOW

// Läuft in der WLAN-Task: nur kopieren, die Arbeit macht bearbeiten()
void beiEspNowEmpfang(const uint8_t* mac, const uint8_t* daten, int laenge) {
  if (laenge <= 0 || laenge > (int)MAX_PAKET) return;
  EspNowPaket p;
  memcpy(p.mac, mac, 6);
  p.laenge = (uint8_t)laenge;
  memcpy(p.daten, daten, laenge);
  xQueueSend(espnowEingang, &p, 0);
}

bool peerSicherstellen(const uint8_t* mac) {
  if (esp_now_is_peer_exist(mac)) return true;
  if (peerAnzahl == MAX_PEERS) {
    esp_now_del_peer(peers[peerNaechster]);  // ältesten Eintrag verdrängen
  } else {
    peerAnzahl++;
  }
  memcpy(peers[peerNaechster], mac, 6);
  peerNaechster = (peerNaechster + 1) % MAX_PEERS;
  esp_now_peer_info_t info = {};
  memcpy(info.peer_addr, mac, 6);
  info.channel = 0;  // 0 = immer der aktuelle Kanal
  info.ifidx = WIFI_IF_STA;
  info.encrypt = false;
  return esp_now_add_peer(&info) == ESP_OK;
}

void espnowStarten() {
  if (esp_now_init() != ESP_OK) {
    Serial.println("[netz] ESP-NOW startet nicht");
    return;
  }
  esp_now_register_recv_cb(beiEspNowEmpfang);
  uint8_t mac[6];
  esp_read_mac(mac, ESP_MAC_WIFI_STA);
  Serial.printf("[netz] ESP-NOW bereit, Adresse %02X:%02X:%02X:%02X:%02X:%02X\n", mac[0], mac[1], mac[2], mac[3],
                mac[4], mac[5]);
}

// ---------------------------------------------------------------- Senden

void senden(const Absender& an, uint8_t typ, uint16_t seq, const void* nutzdaten, size_t laenge) {
  size_t n = bauen(ausgang, typ, seq, nutzdaten, laenge);
  if (!n) return;
  if (an.quelle == QUELLE_UDP) {
    udp.beginPacket(IPAddress(an.ip), an.port);
    udp.write(ausgang, n);
    udp.endPacket();
  } else if (an.quelle == QUELLE_ESPNOW) {
    if (peerSicherstellen(an.mac)) esp_now_send(an.mac, ausgang, n);
  }
}

void infoSenden(const Absender& an, uint16_t seq) {
  uint8_t nd[MAX_NUTZDATEN];
  InfoKopf k{};
  strncpy(k.name, cfg->name.c_str(), sizeof k.name - 1);
  strncpy(k.profil, profil_->kennung, sizeof k.profil - 1);
  k.betrieb = betrieb_;
  k.kanal = kanal();
  k.rssi = wlanVerbunden() ? (int8_t)WiFi.RSSI() : 0;
  IPAddress a = ip();
  for (int i = 0; i < 4; i++) k.ip[i] = a[i];
  esp_read_mac(k.mac, ESP_MAC_WIFI_STA);
  k.firmware = FIRMWARE;
  k.watchdog_ms = cfg->watchdogMs;
  k.anzahl = min<uint8_t>(profil_->anzahl, MAX_SERVOS);
  memcpy(nd, &k, sizeof k);
  ServoInfo* si = reinterpret_cast<ServoInfo*>(nd + sizeof k);
  for (uint8_t i = 0; i < k.anzahl; i++) si[i] = {profil_->servos[i].id, profil_->servos[i].modus};
  senden(an, INFO, seq, nd, sizeof k + k.anzahl * sizeof(ServoInfo));
}

void zustandSenden(const Absender& an, uint16_t seq, uint8_t zusatzFlags) {
  steuerung::Abbild ab;
  steuerung::abbildHolen(ab);
  uint8_t nd[MAX_NUTZDATEN];
  ZustandKopf k{};
  k.quittung = quittung;
  k.zeit_ms = ab.zeit_ms;
  k.flags = zusatzFlags | (ab.watchdog ? KNOTEN_WATCHDOG : 0) | (profil_->nurLesen ? KNOTEN_NUR_LESEN : 0) |
            (wlanVerbunden() ? KNOTEN_WLAN : 0);
  k.quelle = steuerndeQuelle();
  k.zyklus_us = ab.zyklus_us;
  k.busfehler = ab.busfehler;
  k.anzahl = ab.anzahl;
  memcpy(nd, &k, sizeof k);
  memcpy(nd + sizeof k, ab.servos, ab.anzahl * sizeof(ServoZustand));
  senden(an, ZUSTAND, seq, nd, sizeof k + ab.anzahl * sizeof(ServoZustand));
}

void regAntwortSenden(const steuerung::RegErgebnis& e, bool ok) {
  uint8_t nd[sizeof(RegAntwortKopf) + MAX_REG_DATEN];
  RegAntwortKopf k{e.id, e.adresse, (uint8_t)ok, e.laenge};
  memcpy(nd, &k, sizeof k);
  memcpy(nd + sizeof k, e.daten, e.laenge);
  senden(e.absender, REG_ANTWORT, e.seq, nd, sizeof k + e.laenge);
}

// ---------------------------------------------------------------- Empfangen

void befehl(const Kopf& kopf, const uint8_t* nd, const Absender& von) {
  if (kopf.laenge < 1) return;
  uint8_t anzahl = nd[0];
  if (anzahl > MAX_SERVOS || kopf.laenge != 1 + anzahl * sizeof(SollEintrag)) return;

  uint32_t jetzt = millis();
  bool selbe = aktive.gleich(von);
  bool frei = aktive.quelle == QUELLE_KEINE || jetzt - aktiveMs > cfg->watchdogMs;
  if (!selbe && !frei) {
    zustandSenden(von, kopf.seq, KNOTEN_BELEGT);
    return;
  }
  // UDP darf Pakete vertauschen. Ein älterer Befehl derselben Quelle wird verworfen.
  if (selbe && !frei && !neuer(kopf.seq, letzteSeq)) {
    zustandSenden(von, kopf.seq, 0);
    return;
  }
  if (!selbe) {
    Serial.printf("[netz] Steuerung jetzt über %s\n", von.quelle == QUELLE_UDP ? "UDP" : "ESP-NOW");
  }
  aktive = von;
  aktiveMs = jetzt;
  letzteSeq = quittung = kopf.seq;
  SollEintrag eintraege[MAX_SERVOS];
  memcpy(eintraege, nd + 1, anzahl * sizeof(SollEintrag));
  steuerung::sollwerteSetzen(eintraege, anzahl);
  zustandSenden(von, kopf.seq, 0);
}

void registerAnfrage(const Kopf& kopf, const uint8_t* nd, const Absender& von) {
  if (kopf.laenge < sizeof(RegAnfrage)) return;
  RegAnfrage r;
  memcpy(&r, nd, sizeof r);
  bool schreiben = kopf.typ == REG_SCHREIBEN;
  if (r.laenge == 0 || r.laenge > MAX_REG_DATEN) return;
  if (kopf.laenge != sizeof r + (schreiben ? r.laenge : 0)) return;
  steuerung::RegAuftrag a{};
  a.absender = von;
  a.seq = kopf.seq;
  a.schreiben = schreiben;
  a.id = r.id;
  a.adresse = r.adresse;
  a.laenge = r.laenge;
  if (schreiben) memcpy(a.daten, nd + sizeof r, r.laenge);
  if (!steuerung::regAuftragStellen(a)) {
    a.laenge = 0;
    regAntwortSenden(a, false);
  }
}

void verarbeiten(const uint8_t* daten, size_t n, const Absender& von) {
  Kopf kopf;
  const uint8_t* nd;
  if (!pruefen(daten, n, kopf, nd)) return;
  switch (kopf.typ) {
    case PING:
      infoSenden(von, kopf.seq);
      break;
    case BEFEHL:
      befehl(kopf, nd, von);
      break;
    case ZUSTAND_ANFRAGE:
      zustandSenden(von, kopf.seq, 0);
      break;
    case STOPP:
      steuerung::stopp();
      aktive = Absender{};
      Serial.printf("[netz] STOPP über %s\n", von.quelle == QUELLE_UDP ? "UDP" : "ESP-NOW");
      zustandSenden(von, kopf.seq, 0);
      break;
    case REG_LESEN:
    case REG_SCHREIBEN:
      registerAnfrage(kopf, nd, von);
      break;
    default:
      break;  // z. B. Baken anderer Knoten
  }
}

// ---------------------------------------------------------------- WLAN

void apStarten() {
  WiFi.mode(WIFI_AP_STA);  // STA bleibt an, aber unverbunden: ESP-NOW sendet darüber
  WiFi.setAutoReconnect(false);
  WiFi.disconnect(false);
  String ssid = apName();
  if (!WiFi.softAP(ssid.c_str(), cfg->apPasswort.c_str(), cfg->kanal)) {
    Serial.println("[netz] Einrichtungs-WLAN mit Passwort geht nicht, starte offen");
    WiFi.softAP(ssid.c_str(), nullptr, cfg->kanal);
  }
  WiFi.setSleep(false);
  Serial.printf("[netz] Einrichtungs-WLAN \"%s\" auf Kanal %u, Webseite http://%s/\n", ssid.c_str(), cfg->kanal,
                WiFi.softAPIP().toString().c_str());
}

// Sucht unter den eingetragenen Netzen das stärkste in Reichweite
bool bekanntesNetzSuchen(int& index, int32_t& kanalNr, uint8_t bssid[6]) {
  Serial.println("[netz] suche WLAN ...");
  int n = WiFi.scanNetworks(false, false);
  index = -1;
  int32_t besterRssi = -1000;
  for (int i = 0; i < n; i++) {
    for (int j = 0; j < MAX_NETZE; j++) {
      const String& ssid = cfg->netze[j].ssid;
      if (ssid.length() && WiFi.SSID(i) == ssid && WiFi.RSSI(i) > besterRssi) {
        besterRssi = WiFi.RSSI(i);
        index = j;
        kanalNr = WiFi.channel(i);
        memcpy(bssid, WiFi.BSSID(i), 6);
      }
    }
  }
  WiFi.scanDelete();
  if (index < 0) Serial.printf("[netz] %d Netze gefunden, keins davon eingetragen\n", n);
  return index >= 0;
}

bool wlanVerbinden(uint32_t wartezeitMs) {
  int index;
  int32_t kanalNr = 0;
  uint8_t bssid[6];
  if (!bekanntesNetzSuchen(index, kanalNr, bssid)) return false;
  const Netz& netz = cfg->netze[index];
  Serial.printf("[netz] verbinde mit \"%s\" (Kanal %ld) ...\n", netz.ssid.c_str(), (long)kanalNr);
  // Kanal und BSSID fest: Beim Wiederverbinden sucht der ESP dann nur auf diesem
  // Kanal und ESP-NOW bleibt erreichbar.
  WiFi.setAutoReconnect(true);
  WiFi.begin(netz.ssid.c_str(), netz.passwort.c_str(), kanalNr, bssid);
  uint32_t start = millis();
  while (WiFi.status() != WL_CONNECTED && millis() - start < wartezeitMs) delay(100);
  if (WiFi.status() == WL_CONNECTED) {
    Serial.printf("[netz] verbunden, IP %s, %d dBm\n", WiFi.localIP().toString().c_str(), WiFi.RSSI());
    return true;
  }
  Serial.println("[netz] Verbindung fehlgeschlagen (Passwort?)");
  WiFi.disconnect(false);
  return false;
}

void wlanBetriebAufnehmen() {
  betrieb_ = BETRIEB_WLAN;
  wlanWegSeitMs = 0;
  MDNS.end();
  if (MDNS.begin(cfg->name.c_str())) {
    MDNS.addService("roboter", "udp", UDP_PORT);
    MDNS.addService("http", "tcp", 80);
    Serial.printf("[netz] erreichbar als %s.local\n", cfg->name.c_str());
  }
}

void notbetriebAufnehmen() {
  MDNS.end();
  betrieb_ = BETRIEB_NOTBETRIEB;
  apStarten();
  letzterVersuchMs = millis();
  Serial.println("[netz] Notbetrieb: ESP-NOW und Einrichtungs-WLAN, suche jede Minute nach dem Router");
}

void wlanPflegen() {
  uint32_t jetzt = millis();
  if (betrieb_ == BETRIEB_WLAN) {
    if (WiFi.status() == WL_CONNECTED) {
      if (wlanWegSeitMs) Serial.println("[netz] WLAN wieder da");
      wlanWegSeitMs = 0;
      return;
    }
    if (!wlanWegSeitMs) {
      wlanWegSeitMs = jetzt ? jetzt : 1;
      Serial.println("[netz] WLAN weg, verbinde neu ...");
      return;
    }
    // Läuft die Steuerung gerade über ESP-NOW, den Kanal nicht wechseln
    if (jetzt - wlanWegSeitMs > WLAN_VERLOREN_MS && jetzt - espnowEmpfangMs > ESPNOW_RUHE_MS) {
      WiFi.disconnect(false);
      notbetriebAufnehmen();
    }
  } else if (betrieb_ == BETRIEB_NOTBETRIEB) {
    if (jetzt - letzterVersuchMs < WLAN_NEUVERSUCH_MS) return;
    letzterVersuchMs = jetzt;
    // Die Suche wechselt die Kanäle. Nur wenn niemand da ist, der dadurch gestört würde.
    if (WiFi.softAPgetStationNum() > 0 || jetzt - espnowEmpfangMs < ESPNOW_RUHE_MS ||
        steuerndeQuelle() != QUELLE_KEINE)
      return;
    if (wlanVerbinden(10000)) {
      WiFi.softAPdisconnect(true);
      WiFi.mode(WIFI_STA);
      WiFi.setSleep(false);
      wlanBetriebAufnehmen();
    } else {
      esp_wifi_set_channel(cfg->kanal, WIFI_SECOND_CHAN_NONE);  // zurück auf den eigenen Kanal
    }
  }
}

void bakeSenden() {
  Absender an;
  if (wlanVerbunden() || apAktiv()) {
    an.quelle = QUELLE_UDP;
    an.ip = (uint32_t)IPAddress(255, 255, 255, 255);
    an.port = UDP_BAKEN_PORT;
    infoSenden(an, bakenSeq);
  }
  an.quelle = QUELLE_ESPNOW;
  memcpy(an.mac, BROADCAST_MAC, 6);
  infoSenden(an, bakenSeq);
  bakenSeq++;
}

}  // namespace

// ---------------------------------------------------------------- öffentlich

void starten(Einstellungen& e, const Profil& profil, bool einrichtung) {
  cfg = &e;
  profil_ = &profil;
  espnowEingang = xQueueCreate(8, sizeof(EspNowPaket));

  WiFi.persistent(false);  // WLAN-Daten nicht zusätzlich im SDK-Speicher ablegen
  WiFi.setHostname(e.name.c_str());

  if (einrichtung || !netzEingetragen(e)) {
    betrieb_ = BETRIEB_EINRICHTUNG;
    Serial.println(einrichtung ? "[netz] Einrichtung angefordert" : "[netz] noch kein WLAN eingetragen");
    apStarten();
  } else if (e.betrieb == BETRIEB_ESPNOW) {
    betrieb_ = BETRIEB_ESPNOW;
    apStarten();
  } else {
    WiFi.mode(WIFI_STA);
    WiFi.setSleep(false);  // Energiesparen aus, sonst Latenzspitzen von 100 ms und mehr
    if (wlanVerbinden(WLAN_VERBINDEN_MS)) {
      wlanBetriebAufnehmen();
    } else {
      notbetriebAufnehmen();
    }
  }
  espnowStarten();
  udp.begin(UDP_PORT);
  Serial.printf("[netz] %s, Kanal %u, UDP-Port %u\n", betriebText(), kanal(), UDP_PORT);
}

void bearbeiten() {
  // UDP, höchstens 8 Pakete je Durchlauf, damit nichts anderes verhungert
  for (int i = 0; i < 8; i++) {
    int groesse = udp.parsePacket();
    if (groesse <= 0) break;
    int n = udp.read(eingang, sizeof eingang);
    if (groesse > (int)MAX_PAKET || n <= 0) continue;
    Absender von;
    von.quelle = QUELLE_UDP;
    von.ip = (uint32_t)udp.remoteIP();
    von.port = udp.remotePort();
    verarbeiten(eingang, n, von);
  }

  EspNowPaket p;
  for (int i = 0; i < 8 && xQueueReceive(espnowEingang, &p, 0) == pdTRUE; i++) {
    espnowEmpfangMs = millis();
    Absender von;
    von.quelle = QUELLE_ESPNOW;
    memcpy(von.mac, p.mac, 6);
    verarbeiten(p.daten, p.laenge, von);
  }

  steuerung::RegErgebnis e;
  bool ok;
  while (steuerung::regErgebnisHolen(e, ok)) regAntwortSenden(e, ok);

  if (millis() - letzteBakeMs >= BAKE_MS) {
    letzteBakeMs = millis();
    bakeSenden();
  }

  wlanPflegen();
}

Betrieb betrieb() { return betrieb_; }

const char* betriebText() {
  switch (betrieb_) {
    case BETRIEB_WLAN:
      return wlanVerbunden() ? "WLAN" : "WLAN (verbindet neu)";
    case BETRIEB_ESPNOW:
      return "ESP-NOW";
    case BETRIEB_NOTBETRIEB:
      return "Notbetrieb (Router fehlt)";
    default:
      return "Einrichtung";
  }
}

bool wlanVerbunden() { return betrieb_ == BETRIEB_WLAN && WiFi.status() == WL_CONNECTED; }

bool apAktiv() { return betrieb_ != BETRIEB_WLAN; }

uint8_t kanal() {
  uint8_t k = 0;
  wifi_second_chan_t zweiter;
  esp_wifi_get_channel(&k, &zweiter);
  return k;
}

IPAddress ip() { return wlanVerbunden() ? WiFi.localIP() : WiFi.softAPIP(); }

String apName() { return cfg->name + "-setup"; }

Quelle steuerndeQuelle() {
  if (aktive.quelle == QUELLE_KEINE || millis() - aktiveMs > cfg->watchdogMs) return QUELLE_KEINE;
  return aktive.quelle;
}

String steuerndeQuelleText() {
  switch (steuerndeQuelle()) {
    case QUELLE_UDP:
      return "UDP " + IPAddress(aktive.ip).toString() + ":" + String(aktive.port);
    case QUELLE_ESPNOW: {
      char t[32];
      snprintf(t, sizeof t, "ESP-NOW %02X:%02X:%02X:%02X:%02X:%02X", aktive.mac[0], aktive.mac[1], aktive.mac[2],
               aktive.mac[3], aktive.mac[4], aktive.mac[5]);
      return t;
    }
    default:
      return "niemand";
  }
}

}  // namespace netz
