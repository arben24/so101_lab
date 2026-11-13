#include "steuerung.h"

#include <Arduino.h>

#include "servobus.h"

using namespace protokoll;

namespace steuerung {
namespace {

constexpr uint32_t ZYKLUS_MS = 20;     // 50 Hz
constexpr uint32_t SUCHE_MS = 2000;    // fehlende Servos so oft anpingen
constexpr uint8_t MAX_FEHLVERSUCHE = 3;  // so viele Lesefehler hintereinander -> offline

struct Servo {
  uint8_t id = 0;
  uint8_t modus = MODUS_POSITION;
  bool online = false;
  uint8_t fehlversuche = 0;
  bool drehmomentIst = false;  // was im Servo steht (soweit bekannt)
  int16_t halteziel = 2048;    // Position, die im sicheren Zustand gehalten wird
  ServoZustand ist{};
};

struct Soll {
  bool drehmoment = false;
  int16_t wert = 0;
};

struct RegEintrag {
  RegAuftrag a;
  bool ok;
};

ServoBus bus;
const Profil* profil_ = nullptr;
uint16_t watchdogMs_ = 300;
int16_t maxTempo_ = 3000;
Servo servos_[MAX_SERVOS];
uint8_t anzahl_ = 0;

// Gemeinsam mit dem Netz benutzt, nur unter mux_ anfassen
portMUX_TYPE mux_ = portMUX_INITIALIZER_UNLOCKED;
Soll soll_[MAX_SERVOS];
bool befehlGueltig_ = false;
uint32_t befehlMs_ = 0;
Abbild abbild_{};

QueueHandle_t regAuftraege_ = nullptr;
QueueHandle_t regErgebnisse_ = nullptr;

// Servo (wieder) gefunden: Drehmoment aus, Betriebsart prüfen
void einrichten(Servo& s) {
  bus.schreiben1(s.id, sts::REG_DREHMOMENT, 0);
  uint8_t modus;
  if (bus.lesen(s.id, sts::REG_MODUS, 1, &modus) && modus != s.modus) {
    // Register 33 liegt im EPROM. Nur schreiben, wenn nötig, das schont den Speicher.
    bus.schreiben1(s.id, sts::REG_LOCK, 0);
    bus.schreiben1(s.id, sts::REG_MODUS, s.modus);
    bus.schreiben1(s.id, sts::REG_LOCK, 1);
    Serial.printf("[servo] ID %u: Betriebsart %u -> %u\n", s.id, modus, s.modus);
  }
  uint8_t d[2];
  if (bus.lesen(s.id, sts::REG_IST_POSITION, 2, d)) s.halteziel = sts::vorzeichenDekodieren(d[0] | d[1] << 8, 15);
  s.drehmomentIst = false;
  s.fehlversuche = 0;
  s.online = true;
}

// Register 56 bis 65 in einem Rutsch: Position, Tempo, Last, Spannung, Temperatur, -, Status
bool zustandLesen(Servo& s) {
  uint8_t d[10];
  if (!bus.lesen(s.id, sts::REG_IST_POSITION, sizeof d, d)) return false;
  s.ist.position = sts::vorzeichenDekodieren(d[0] | d[1] << 8, 15);
  s.ist.tempo = sts::vorzeichenDekodieren(d[2] | d[3] << 8, 15);
  s.ist.last = sts::vorzeichenDekodieren(d[4] | d[5] << 8, 10);
  s.ist.spannung = d[6];
  s.ist.temperatur = d[7];
  s.ist.fehler = d[9];
  return true;
}

void registerAuftragBearbeiten() {
  RegEintrag e;
  if (xQueueReceive(regAuftraege_, &e.a, 0) != pdTRUE) return;
  if (e.a.schreiben) {
    e.ok = bus.schreiben(e.a.id, e.a.adresse, e.a.daten, e.a.laenge);
    e.a.laenge = 0;
  } else {
    e.ok = bus.lesen(e.a.id, e.a.adresse, e.a.laenge, e.a.daten);
    if (!e.ok) e.a.laenge = 0;
  }
  xQueueSend(regErgebnisse_, &e, 0);
}

struct TaskZustand {
  uint32_t letzteSucheMs = 0;
  bool warSicher = true;
  uint8_t pruefIndex = 0;
};

void zyklus(TaskZustand& t) {
  uint32_t startUs = micros();
  uint32_t jetzt = millis();

  // 1. Sollwerte abholen und Watchdog prüfen
  Soll soll[MAX_SERVOS];
  bool gueltig;
  uint32_t befehlMs;
  portENTER_CRITICAL(&mux_);
  memcpy(soll, soll_, sizeof soll);
  gueltig = befehlGueltig_;
  befehlMs = befehlMs_;
  portEXIT_CRITICAL(&mux_);

  bool sicher = !gueltig || (jetzt - befehlMs > watchdogMs_);
  if (sicher && !t.warSicher) {
    for (uint8_t i = 0; i < anzahl_; i++) servos_[i].halteziel = servos_[i].ist.position;
    Serial.println(gueltig ? "[servo] Watchdog: kein Befehl mehr, Räder stopp, Arm hält"
                           : "[servo] STOPP: Räder stopp, Arm hält");
  }
  t.warSicher = sicher;

  // 2. Drehmoment eines Servos gegenprüfen (reihum), falls er zwischendurch neu gestartet ist
  if (anzahl_) {
    Servo& s = servos_[t.pruefIndex];
    t.pruefIndex = (t.pruefIndex + 1) % anzahl_;
    uint8_t dm;
    if (s.online && bus.lesen(s.id, sts::REG_DREHMOMENT, 1, &dm)) s.drehmomentIst = dm != 0;
  }

  // 3. Ziele schreiben, ein SYNC_WRITE je Betriebsart. Ziel vor Drehmoment, sonst springt der Servo
  //    beim Einschalten zum alten Wert im Register.
  bool dmSoll[MAX_SERVOS];
  uint8_t posIds[MAX_SERVOS], posDaten[2 * MAX_SERVOS], np = 0;
  uint8_t tmpIds[MAX_SERVOS], tmpDaten[2 * MAX_SERVOS], nt = 0;
  for (uint8_t i = 0; i < anzahl_; i++) {
    Servo& s = servos_[i];
    bool dm = !profil_->nurLesen && (sicher ? s.drehmomentIst : soll[i].drehmoment);
    dmSoll[i] = dm;
    if (!s.online || !dm) continue;
    if (s.modus == MODUS_TEMPO) {
      int32_t v = sicher ? 0 : constrain((int32_t)soll[i].wert, -maxTempo_, maxTempo_);
      uint16_t roh = sts::vorzeichenKodieren(v, 15);
      tmpIds[nt] = s.id;
      tmpDaten[2 * nt] = roh & 0xFF;
      tmpDaten[2 * nt + 1] = roh >> 8;
      nt++;
    } else {
      int32_t p = constrain((int32_t)(sicher ? s.halteziel : soll[i].wert), 0, 4095);
      posIds[np] = s.id;
      posDaten[2 * np] = p & 0xFF;
      posDaten[2 * np + 1] = p >> 8;
      np++;
    }
  }
  if (np) bus.syncSchreiben(sts::REG_ZIEL_POSITION, 2, posIds, posDaten, np);
  if (nt) bus.syncSchreiben(sts::REG_ZIEL_TEMPO, 2, tmpIds, tmpDaten, nt);

  for (uint8_t i = 0; i < anzahl_; i++) {
    Servo& s = servos_[i];
    if (s.online && dmSoll[i] != s.drehmomentIst && bus.schreiben1(s.id, sts::REG_DREHMOMENT, dmSoll[i]))
      s.drehmomentIst = dmSoll[i];
  }

  // 4. Istwerte lesen
  for (uint8_t i = 0; i < anzahl_; i++) {
    Servo& s = servos_[i];
    if (!s.online) continue;
    if (zustandLesen(s)) {
      s.fehlversuche = 0;
    } else if (++s.fehlversuche >= MAX_FEHLVERSUCHE) {
      s.online = false;
      s.drehmomentIst = false;
      Serial.printf("[servo] ID %u antwortet nicht mehr\n", s.id);
    }
  }

  // 5. Fehlende Servos suchen (Stecker locker, Akku getauscht ...)
  if (jetzt - t.letzteSucheMs >= SUCHE_MS) {
    t.letzteSucheMs = jetzt;
    for (uint8_t i = 0; i < anzahl_; i++) {
      Servo& s = servos_[i];
      if (!s.online && bus.ping(s.id)) {
        einrichten(s);
        Serial.printf("[servo] ID %u (%s) gefunden\n", s.id, profil_->servos[i].name);
      }
    }
  }

  // 6. Höchstens ein Registerzugriff vom PC je Zyklus
  registerAuftragBearbeiten();

  // 7. Abbild für das Netz
  Abbild a{};
  a.zeit_ms = jetzt;
  a.watchdog = sicher;
  a.zyklus_us = (uint16_t)min<uint32_t>(micros() - startUs, 65535);
  a.busfehler = (uint16_t)min<uint32_t>(bus.fehler(), 65535);
  a.anzahl = anzahl_;
  for (uint8_t i = 0; i < anzahl_; i++) {
    const Servo& s = servos_[i];
    a.servos[i] = s.ist;
    a.servos[i].id = s.id;
    a.servos[i].flags = (s.online ? SERVO_ONLINE : 0) | (s.drehmomentIst ? SERVO_DREHMOMENT : 0) |
                        (s.modus == MODUS_TEMPO ? SERVO_TEMPOMODUS : 0) | (s.fehlversuche ? SERVO_VERALTET : 0);
    if (s.online) a.online++;
  }
  portENTER_CRITICAL(&mux_);
  abbild_ = a;
  portEXIT_CRITICAL(&mux_);
}

void task(void*) {
  for (uint8_t i = 0; i < anzahl_; i++) {
    Servo& s = servos_[i];
    if (bus.ping(s.id)) {
      einrichten(s);
      Serial.printf("[servo] ID %u (%s) ok\n", s.id, profil_->servos[i].name);
    } else {
      Serial.printf("[servo] ID %u (%s) fehlt, suche weiter\n", s.id, profil_->servos[i].name);
    }
  }
  TaskZustand t;
  t.letzteSucheMs = millis();
  TickType_t takt = xTaskGetTickCount();
  for (;;) {
    zyklus(t);
    vTaskDelayUntil(&takt, pdMS_TO_TICKS(ZYKLUS_MS));
  }
}

}  // namespace

void starten(const Profil& profil, uint16_t watchdogMs, int16_t maxTempo) {
  profil_ = &profil;
  watchdogMs_ = watchdogMs;
  maxTempo_ = maxTempo;
  anzahl_ = min<uint8_t>(profil.anzahl, MAX_SERVOS);
  for (uint8_t i = 0; i < anzahl_; i++) {
    servos_[i].id = profil.servos[i].id;
    servos_[i].modus = profil.servos[i].modus;
    servos_[i].ist.id = profil.servos[i].id;
  }
  regAuftraege_ = xQueueCreate(2, sizeof(RegAuftrag));
  regErgebnisse_ = xQueueCreate(2, sizeof(RegEintrag));
  bus.begin(Serial2, SERVO_RX_PIN, SERVO_TX_PIN, 1000000);
  // Kern 1 wie loop(), aber höhere Priorität: der Takt hat Vorrang vor Netz und Webseite.
  // WLAN und ESP-NOW laufen auf Kern 0.
  xTaskCreatePinnedToCore(task, "servos", 6144, nullptr, 5, nullptr, 1);
}

void sollwerteSetzen(const SollEintrag* eintraege, uint8_t anzahl) {
  portENTER_CRITICAL(&mux_);
  for (uint8_t k = 0; k < anzahl; k++) {
    for (uint8_t i = 0; i < anzahl_; i++) {
      if (servos_[i].id == eintraege[k].id) {
        soll_[i].drehmoment = eintraege[k].flags & SOLL_DREHMOMENT;
        soll_[i].wert = eintraege[k].wert;
      }
    }
  }
  befehlGueltig_ = true;
  befehlMs_ = millis();
  portEXIT_CRITICAL(&mux_);
}

void stopp() {
  portENTER_CRITICAL(&mux_);
  befehlGueltig_ = false;
  portEXIT_CRITICAL(&mux_);
}

void abbildHolen(Abbild& a) {
  portENTER_CRITICAL(&mux_);
  a = abbild_;
  portEXIT_CRITICAL(&mux_);
}

bool regAuftragStellen(const RegAuftrag& a) { return xQueueSend(regAuftraege_, &a, 0) == pdTRUE; }

bool regErgebnisHolen(RegErgebnis& e, bool& ok) {
  RegEintrag r;
  if (xQueueReceive(regErgebnisse_, &r, 0) != pdTRUE) return false;
  e = r.a;
  ok = r.ok;
  return true;
}

}  // namespace steuerung
