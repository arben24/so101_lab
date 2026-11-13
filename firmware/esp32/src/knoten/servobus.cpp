#include "servobus.h"

namespace sts {
uint16_t vorzeichenKodieren(int32_t wert, uint8_t bit) {
  uint16_t grenze = (1u << bit) - 1;
  uint16_t betrag = (uint16_t)min<int32_t>(abs(wert), grenze);
  return wert < 0 ? (betrag | (1u << bit)) : betrag;
}

int16_t vorzeichenDekodieren(uint16_t roh, uint8_t bit) {
  int16_t betrag = roh & ((1u << bit) - 1);
  return (roh & (1u << bit)) ? -betrag : betrag;
}
}  // namespace sts

void ServoBus::begin(HardwareSerial& ser, int rxPin, int txPin, uint32_t baud) {
  ser_ = &ser;
  ser_->setRxBufferSize(512);
  ser_->begin(baud, SERIAL_8N1, rxPin, txPin);
  delay(10);

  // Echo erkennen: Manche Halbduplex-Adapter geben jedes gesendete Byte auch auf
  // RX aus. Dazu ein PING an ID 252, die kein Servo haben sollte. Kommt genau
  // unser Paket zurück, ist es ein Echo.
  const uint8_t ping[6] = {0xFF, 0xFF, 0xFC, 0x02, sts::BEF_PING, (uint8_t)~(0xFC + 0x02 + sts::BEF_PING)};
  while (ser_->available()) ser_->read();
  ser_->write(ping, sizeof ping);
  ser_->flush();
  delay(3);
  uint8_t zurueck[6];
  size_t n = 0;
  while (ser_->available() && n < sizeof zurueck) zurueck[n++] = ser_->read();
  echo_ = (n == sizeof ping && memcmp(zurueck, ping, sizeof ping) == 0);
  while (ser_->available()) ser_->read();
  Serial.printf("[servo] UART2 RX=%d TX=%d, %lu bit/s, Echo %s\n", rxPin, txPin, (unsigned long)baud,
                echo_ ? "erkannt (wird verworfen)" : "keins");
}

void ServoBus::senden(uint8_t id, uint8_t befehl, const uint8_t* parameter, uint8_t n) {
  uint8_t paket[6 + MAX_PARAMETER];
  if (n > MAX_PARAMETER) return;
  paket[0] = paket[1] = 0xFF;
  paket[2] = id;
  paket[3] = n + 2;
  paket[4] = befehl;
  uint8_t summe = id + paket[3] + befehl;
  for (uint8_t i = 0; i < n; i++) {
    paket[5 + i] = parameter[i];
    summe += parameter[i];
  }
  paket[5 + n] = ~summe;

  while (ser_->available()) ser_->read();  // Reste einer verspäteten Antwort weg
  ser_->write(paket, 6 + n);
  ser_->flush();  // wartet, bis das letzte Bit draußen ist

  if (echo_) {
    uint32_t bis = micros() + 1000 + (6 + n) * 12;
    uint8_t b;
    for (uint8_t i = 0; i < 6 + n && byteLesen(b, bis); i++) {
    }
  }
}

bool ServoBus::byteLesen(uint8_t& b, uint32_t bisUs) {
  while (!ser_->available()) {
    if ((int32_t)(micros() - bisUs) > 0) return false;
  }
  b = ser_->read();
  return true;
}

// Antwort: FF FF | ID | Länge | Fehler | Parameter ... | Prüfsumme
bool ServoBus::antwort(uint8_t id, uint8_t* parameter, uint8_t n) {
  // 3 ms reichen auch für die Antwortverzögerung ab Werk (Register 7, bis 500 µs)
  uint32_t bis = micros() + 3000 + (6 + n) * 12;
  uint8_t b, vorher = 0;
  for (;;) {  // Anfang suchen
    if (!byteLesen(b, bis)) return false;
    if (vorher == 0xFF && b == 0xFF) break;
    vorher = b;
  }
  uint8_t rid;
  do {
    if (!byteLesen(rid, bis)) return false;
  } while (rid == 0xFF);
  uint8_t laenge, fehler;
  if (!byteLesen(laenge, bis) || !byteLesen(fehler, bis)) return false;
  if (rid != id || laenge != n + 2) return false;
  uint8_t summe = rid + laenge + fehler;
  for (uint8_t i = 0; i < n; i++) {
    if (!byteLesen(b, bis)) return false;
    if (parameter) parameter[i] = b;
    summe += b;
  }
  uint8_t pruef;
  if (!byteLesen(pruef, bis)) return false;
  // Das Fehlerbyte (Überlast, Temperatur ...) ist kein Übertragungsfehler, die
  // Daten stimmen trotzdem. Es steht ausführlicher im Status-Register 65.
  return pruef == (uint8_t)~summe;
}

bool ServoBus::ping(uint8_t id) {
  senden(id, sts::BEF_PING, nullptr, 0);
  return antwort(id, nullptr, 0);
}

bool ServoBus::lesen(uint8_t id, uint8_t adresse, uint8_t anzahl, uint8_t* ziel) {
  const uint8_t p[2] = {adresse, anzahl};
  senden(id, sts::BEF_READ, p, 2);
  if (antwort(id, ziel, anzahl)) return true;
  fehler_++;
  return false;
}

bool ServoBus::schreiben(uint8_t id, uint8_t adresse, const uint8_t* daten, uint8_t anzahl) {
  if (anzahl + 1 > MAX_PARAMETER) return false;
  uint8_t p[MAX_PARAMETER];
  p[0] = adresse;
  memcpy(p + 1, daten, anzahl);
  senden(id, sts::BEF_WRITE, p, anzahl + 1);
  if (id == sts::BROADCAST || antwort(id, nullptr, 0)) return true;
  fehler_++;
  return false;
}

void ServoBus::syncSchreiben(uint8_t adresse, uint8_t breite, const uint8_t* ids, const uint8_t* daten,
                             uint8_t anzahl) {
  uint8_t p[MAX_PARAMETER];
  size_t n = 2 + (size_t)anzahl * (1 + breite);
  if (n > MAX_PARAMETER) return;
  p[0] = adresse;
  p[1] = breite;
  uint8_t* z = p + 2;
  for (uint8_t i = 0; i < anzahl; i++) {
    *z++ = ids[i];
    memcpy(z, daten + i * breite, breite);
    z += breite;
  }
  senden(sts::BROADCAST, sts::BEF_SYNC_WRITE, p, (uint8_t)n);
}
