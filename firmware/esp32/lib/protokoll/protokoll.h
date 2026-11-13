#pragma once
// Funkprotokoll zwischen PC und ESP32-Knoten.
//
// Dieselben Pakete laufen über WLAN (UDP, Port 4210) und über ESP-NOW. Der
// Gateway-ESP reicht sie unverändert zwischen USB und ESP-NOW durch. Die
// Python-Seite steht in werkzeuge/knoten_test.py und muss zu dieser Datei passen.
//
// Aufbau jedes Pakets, alle Zahlen Little Endian:
//
//   Kopf (7 Byte) | Nutzdaten (0 bis 241 Byte) | CRC-16 (2 Byte)
//
// Die CRC (CCITT-FALSE: Startwert 0xFFFF, Polynom 0x1021) läuft über Kopf und
// Nutzdaten. UDP und ESP-NOW prüfen zwar selbst, über USB aber nicht, und so
// fällt auch ein Paket auf, das aus einer anderen Quelle stammt.
//
// Antworten tragen die seq der Anfrage. Baken (INFO ohne Anfrage) zählen eigene seq hoch.

#include <stddef.h>
#include <stdint.h>

namespace protokoll {

constexpr uint16_t MAGIC = 0x4B52;  // auf dem Draht 'R' 'K'
constexpr uint8_t VERSION = 1;
constexpr size_t MAX_PAKET = 250;  // Grenze von ESP-NOW
constexpr size_t KOPF_GROESSE = 7;
constexpr size_t MAX_NUTZDATEN = MAX_PAKET - KOPF_GROESSE - 2;
constexpr uint16_t UDP_PORT = 4210;        // Befehle und Antworten
constexpr uint16_t UDP_BAKEN_PORT = 4211;  // Baken als Broadcast an alle PCs
constexpr uint8_t MAX_SERVOS = 12;
constexpr uint8_t MAX_REG_DATEN = 32;

enum Typ : uint8_t {
  // PC -> Knoten
  PING = 0x01,             // Wer bist du? Antwort INFO
  BEFEHL = 0x02,           // Sollwerte setzen, Antwort ZUSTAND
  ZUSTAND_ANFRAGE = 0x03,  // nur lesen, ohne Steuerrecht. Antwort ZUSTAND
  STOPP = 0x04,            // sofort sicherer Zustand, darf jeder. Antwort ZUSTAND
  REG_LESEN = 0x05,        // Servo-Register lesen, Antwort REG_ANTWORT
  REG_SCHREIBEN = 0x06,    // Servo-Register schreiben, Antwort REG_ANTWORT
  // Knoten -> PC
  INFO = 0x81,
  ZUSTAND = 0x82,
  REG_ANTWORT = 0x85,
  // PC <-> Gateway selbst (nur über USB, Ziel-MAC 00:00:00:00:00:00)
  GW_INFO_ANFRAGE = 0x10,
  GW_KANAL = 0x11,
  GW_INFO = 0x90,
};

enum Quelle : uint8_t { QUELLE_KEINE = 0, QUELLE_UDP = 1, QUELLE_ESPNOW = 2 };

enum Betrieb : uint8_t {
  BETRIEB_WLAN = 0,         // mit Router verbunden, ESP-NOW zusätzlich auf dem Router-Kanal
  BETRIEB_ESPNOW = 1,       // bewusst ohne Router, eigener Kanal, Einrichtungs-WLAN offen
  BETRIEB_NOTBETRIEB = 2,   // Router nicht erreichbar, wie ESPNOW, sucht regelmäßig neu
  BETRIEB_EINRICHTUNG = 3,  // kein Netz eingetragen oder BOOT-Taster gedrückt
};

enum Modus : uint8_t { MODUS_POSITION = 0, MODUS_TEMPO = 1 };  // = Register 33 des STS3215

#pragma pack(push, 1)

struct Kopf {
  uint16_t magic;
  uint8_t version;
  uint8_t typ;
  uint16_t seq;
  uint8_t laenge;  // Anzahl Nutzdatenbytes
};

// --- BEFEHL: BefehlKopf, dann anzahl x SollEintrag ---------------------------
// Servos, die nicht genannt werden, behalten ihren letzten Sollwert.
constexpr uint8_t SOLL_DREHMOMENT = 0x01;
struct BefehlKopf {
  uint8_t anzahl;
};
struct SollEintrag {
  uint8_t id;
  uint8_t flags;  // SOLL_DREHMOMENT
  int16_t wert;   // Positionsmodus: Schritte 0..4095. Tempomodus: Schritt/s mit Vorzeichen
};

// --- ZUSTAND: ZustandKopf, dann anzahl x ServoZustand -----------------------
constexpr uint8_t KNOTEN_WATCHDOG = 0x01;   // kein gültiger Befehl, Knoten hält sicheren Zustand
constexpr uint8_t KNOTEN_BELEGT = 0x02;     // dieser Befehl wurde abgelehnt, jemand anderes steuert
constexpr uint8_t KNOTEN_NUR_LESEN = 0x04;  // Profil ohne Drehmoment (Leader)
constexpr uint8_t KNOTEN_WLAN = 0x08;       // mit Router verbunden

constexpr uint8_t SERVO_ONLINE = 0x01;
constexpr uint8_t SERVO_DREHMOMENT = 0x02;
constexpr uint8_t SERVO_TEMPOMODUS = 0x04;
constexpr uint8_t SERVO_VERALTET = 0x08;  // letzter Lesezugriff fehlgeschlagen

struct ZustandKopf {
  uint16_t quittung;  // seq des zuletzt angenommenen BEFEHL
  uint32_t zeit_ms;   // Laufzeit des Knotens
  uint8_t flags;      // KNOTEN_*
  uint8_t quelle;     // wer gerade steuert (Quelle)
  uint16_t zyklus_us; // Dauer des letzten Servo-Zyklus
  uint16_t busfehler; // fehlgeschlagene Servo-Zugriffe seit dem Start
  uint8_t anzahl;
};
struct ServoZustand {
  uint8_t id;
  uint8_t flags;       // SERVO_*
  int16_t position;    // Schritte, 0..4095 = eine Umdrehung
  int16_t tempo;       // Schritt/s
  int16_t last;        // 0,1 % PWM, Vorzeichen = Richtung
  uint8_t spannung;    // 0,1 V
  uint8_t temperatur;  // °C
  uint8_t fehler;      // Register 65 (Status) des Servos
};

// --- INFO: InfoKopf, dann anzahl x ServoInfo --------------------------------
struct InfoKopf {
  char name[16];    // nullterminiert
  char profil[16];  // nullterminiert
  uint8_t betrieb;  // Betrieb
  uint8_t kanal;    // aktueller WLAN-/ESP-NOW-Kanal
  int8_t rssi;      // Empfangsstärke zum Router, 0 ohne Router
  uint8_t ip[4];
  uint8_t mac[6];   // ESP-NOW-Adresse (Station-MAC)
  uint16_t firmware;
  uint16_t watchdog_ms;
  uint8_t anzahl;
};
struct ServoInfo {
  uint8_t id;
  uint8_t modus;  // Modus
};

// --- REG_LESEN / REG_SCHREIBEN: RegAnfrage, beim Schreiben laenge Datenbytes dahinter
struct RegAnfrage {
  uint8_t id;
  uint8_t adresse;
  uint8_t laenge;
};
// --- REG_ANTWORT: RegAntwortKopf, beim Lesen laenge Datenbytes dahinter
struct RegAntwortKopf {
  uint8_t id;
  uint8_t adresse;
  uint8_t ok;
  uint8_t laenge;
};

// --- Gateway ---------------------------------------------------------------
struct GwKanal {
  uint8_t kanal;  // 1..13 fest, 0 = automatisch suchen
};
struct GwInfo {
  uint8_t kanal;
  uint8_t automatisch;
  uint8_t suchend;
  uint8_t mac[6];
  uint16_t firmware;
  uint32_t empfangen;
  uint32_t sendefehler;
};

#pragma pack(pop)

static_assert(sizeof(Kopf) == KOPF_GROESSE, "Kopf muss 7 Byte haben");
static_assert(sizeof(SollEintrag) == 4, "SollEintrag");
static_assert(sizeof(ZustandKopf) == 13, "ZustandKopf");
static_assert(sizeof(ServoZustand) == 11, "ServoZustand");
static_assert(sizeof(InfoKopf) == 50, "InfoKopf");
static_assert(sizeof(GwInfo) == 19, "GwInfo");
static_assert(sizeof(ZustandKopf) + MAX_SERVOS * sizeof(ServoZustand) <= MAX_NUTZDATEN, "ZUSTAND zu groß");
static_assert(sizeof(InfoKopf) + MAX_SERVOS * sizeof(ServoInfo) <= MAX_NUTZDATEN, "INFO zu groß");

uint16_t crc16(const uint8_t* daten, size_t n);

// Baut ein Paket in puffer (mind. MAX_PAKET Byte). Liefert die Länge, 0 bei zu vielen Nutzdaten.
size_t bauen(uint8_t* puffer, uint8_t typ, uint16_t seq, const void* nutzdaten, size_t laenge);

// Prüft Länge, Magic, Version und CRC. Bei Erfolg zeigt nutzdaten in den puffer.
bool pruefen(const uint8_t* puffer, size_t n, Kopf& kopf, const uint8_t*& nutzdaten);

// Ist seq a neuer als b? Rechnet den Überlauf von 65535 auf 0 richtig.
inline bool neuer(uint16_t a, uint16_t b) { return (int16_t)(uint16_t)(a - b) > 0; }

}  // namespace protokoll
