#pragma once
// Feetech-STS-Protokoll über Halbduplex-UART (1 Mbit/s).
// Paketaufbau, Register und Vorzeichen wie in
// doku/sts3215/README.md beschrieben.

#include <Arduino.h>

namespace sts {
constexpr uint8_t BROADCAST = 0xFE;

constexpr uint8_t BEF_PING = 0x01;
constexpr uint8_t BEF_READ = 0x02;
constexpr uint8_t BEF_WRITE = 0x03;
constexpr uint8_t BEF_SYNC_WRITE = 0x83;

constexpr uint8_t REG_MODUS = 33;          // Operating_Mode, EPROM
constexpr uint8_t REG_DREHMOMENT = 40;     // Torque_Enable (nie 128 schreiben!)
constexpr uint8_t REG_ZIEL_POSITION = 42;  // Goal_Position
constexpr uint8_t REG_ZIEL_TEMPO = 46;     // Goal_Velocity
constexpr uint8_t REG_LOCK = 55;           // 0 = EPROM-Änderungen bleiben gespeichert
constexpr uint8_t REG_IST_POSITION = 56;   // ab hier: Position, Tempo, Last, Spannung, Temperatur, -, Status

// Der STS3215 speichert negative Zahlen als Betrag plus Vorzeichenbit.
uint16_t vorzeichenKodieren(int32_t wert, uint8_t bit);
int16_t vorzeichenDekodieren(uint16_t roh, uint8_t bit);
}  // namespace sts

class ServoBus {
 public:
  void begin(HardwareSerial& ser, int rxPin, int txPin, uint32_t baud);

  bool ping(uint8_t id);
  bool lesen(uint8_t id, uint8_t adresse, uint8_t anzahl, uint8_t* ziel);
  bool schreiben(uint8_t id, uint8_t adresse, const uint8_t* daten, uint8_t anzahl);
  bool schreiben1(uint8_t id, uint8_t adresse, uint8_t wert) { return schreiben(id, adresse, &wert, 1); }

  // Ein Paket für viele Servos, ohne Antwort. Servo ids[i] bekommt daten[i*breite ...].
  void syncSchreiben(uint8_t adresse, uint8_t breite, const uint8_t* ids, const uint8_t* daten, uint8_t anzahl);

  uint32_t fehler() const { return fehler_; }
  bool echo() const { return echo_; }

 private:
  static constexpr uint8_t MAX_PARAMETER = 64;
  void senden(uint8_t id, uint8_t befehl, const uint8_t* parameter, uint8_t n);
  bool antwort(uint8_t id, uint8_t* parameter, uint8_t n);
  bool byteLesen(uint8_t& b, uint32_t bisUs);

  HardwareSerial* ser_ = nullptr;
  bool echo_ = false;  // gibt der Adapter gesendete Bytes auf RX zurück?
  uint32_t fehler_ = 0;
};
