#include "protokoll.h"

#include <string.h>

namespace protokoll {

uint16_t crc16(const uint8_t* daten, size_t n) {
  uint16_t crc = 0xFFFF;
  for (size_t i = 0; i < n; i++) {
    crc ^= (uint16_t)daten[i] << 8;
    for (int bit = 0; bit < 8; bit++) crc = (crc & 0x8000) ? (uint16_t)((crc << 1) ^ 0x1021) : (uint16_t)(crc << 1);
  }
  return crc;
}

size_t bauen(uint8_t* puffer, uint8_t typ, uint16_t seq, const void* nutzdaten, size_t laenge) {
  if (laenge > MAX_NUTZDATEN) return 0;
  Kopf kopf{MAGIC, VERSION, typ, seq, (uint8_t)laenge};
  memcpy(puffer, &kopf, KOPF_GROESSE);
  if (laenge) memcpy(puffer + KOPF_GROESSE, nutzdaten, laenge);
  uint16_t crc = crc16(puffer, KOPF_GROESSE + laenge);
  puffer[KOPF_GROESSE + laenge] = crc & 0xFF;
  puffer[KOPF_GROESSE + laenge + 1] = crc >> 8;
  return KOPF_GROESSE + laenge + 2;
}

bool pruefen(const uint8_t* puffer, size_t n, Kopf& kopf, const uint8_t*& nutzdaten) {
  if (n < KOPF_GROESSE + 2 || n > MAX_PAKET) return false;
  memcpy(&kopf, puffer, KOPF_GROESSE);
  if (kopf.magic != MAGIC || kopf.version != VERSION) return false;
  if (KOPF_GROESSE + kopf.laenge + 2u != n) return false;
  uint16_t crc = puffer[n - 2] | (puffer[n - 1] << 8);
  if (crc != crc16(puffer, n - 2)) return false;
  nutzdaten = puffer + KOPF_GROESSE;
  return true;
}

}  // namespace protokoll
