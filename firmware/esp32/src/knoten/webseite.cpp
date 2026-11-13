#include "webseite.h"

#include <DNSServer.h>
#include <WebServer.h>
#include <WiFi.h>

#include "netz.h"
#include "steuerung.h"

using namespace protokoll;

namespace webseite {
namespace {

WebServer server(80);
DNSServer dns;
bool dnsLaeuft = false;
Einstellungen* cfg = nullptr;
const Profil* profil_ = nullptr;
uint32_t neustartUm = 0;

const char KOPF[] PROGMEM = R"(<!doctype html><html lang="de"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Roboter-Knoten</title><style>
body{font-family:system-ui,sans-serif;margin:0 auto;max-width:760px;padding:12px 16px;background:#f6f7f9;color:#1d2330}
h1{font-size:1.4em;margin:.3em 0}h2{font-size:1.1em;margin:1.2em 0 .4em}
section{background:#fff;border:1px solid #d8dce3;border-radius:8px;padding:10px 14px;margin:10px 0}
label{display:block;margin:.5em 0 .15em;font-weight:600}input,select{width:100%;box-sizing:border-box;padding:6px;font-size:1em}
.reihe{display:flex;gap:8px}.reihe>div{flex:1}.klein{font-size:.85em;color:#5a6270}
button{padding:8px 14px;font-size:1em;margin:8px 6px 0 0;border-radius:6px;border:1px solid #2a5bd7;background:#2a5bd7;color:#fff}
button.neben{background:#fff;color:#2a5bd7}button.gefahr{border-color:#b3261e;background:#fff;color:#b3261e}
table{border-collapse:collapse;width:100%;font-size:.85em}td,th{border-bottom:1px solid #e3e6eb;padding:3px 4px;text-align:right}
td:nth-child(2),th:nth-child(2){text-align:left}.aus{color:#9aa1ac}.warn{color:#b3261e;font-weight:600}
.radio label{display:inline;font-weight:400;margin-right:14px}.radio input{width:auto}
</style></head><body>)";

String html(const String& s) {
  String r;
  r.reserve(s.length() + 8);
  for (char c : s) {
    switch (c) {
      case '&': r += "&amp;"; break;
      case '<': r += "&lt;"; break;
      case '>': r += "&gt;"; break;
      case '"': r += "&quot;"; break;
      case '\'': r += "&#39;"; break;
      default: r += c;
    }
  }
  return r;
}

String json(const String& s) {
  String r = "\"";
  for (char c : s) {
    if (c == '"' || c == '\\') {
      r += '\\';
      r += c;
    } else if ((uint8_t)c < 0x20) {
      char t[8];
      snprintf(t, sizeof t, "\\u%04x", c);
      r += t;
    } else {
      r += c;
    }
  }
  return r + "\"";
}

// Im Einrichtungs-WLAN schützt das WPA2-Passwort, im Router-WLAN die Anmeldung.
bool erlaubt() {
  if (netz::betrieb() != BETRIEB_WLAN) return true;
  if (server.authenticate("admin", cfg->apPasswort.c_str())) return true;
  server.requestAuthentication(BASIC_AUTH, "Roboter-Knoten", "Anmeldung: admin und Setup-Passwort");
  return false;
}

void meldung(int code, const String& titel, const String& text) {
  String s = FPSTR(KOPF);
  s += "<h1>" + html(titel) + "</h1><section><p>" + text + "</p><p><a href=\"/\">zurück</a></p></section></body></html>";
  server.send(code, "text/html; charset=utf-8", s);
}

void startseite() {
  if (!erlaubt()) return;
  String s = FPSTR(KOPF);
  s.reserve(9000);
  s += "<h1>" + html(cfg->name) + "</h1><div class=klein>Firmware " + String(netz::FIRMWARE) +
       " &middot; Profil " + html(profil_->kennung) + "</div>";

  s += F("<section><h2>Zustand</h2><div id=z>lade ...</div><table id=t></table></section>");

  s += F("<form method=post action=/speichern><section><h2>Gerät</h2>");
  s += "<label>Name</label><input name=name maxlength=15 pattern=\"[a-z0-9-]{1,15}\" value=\"" + html(cfg->name) +
       "\"><div class=klein>Kleinbuchstaben, Ziffern, Bindestrich. Erreichbar als <i>name</i>.local, "
       "Einrichtungs-WLAN heißt <i>name</i>-setup.</div>";
  s += F("<label>Profil</label><select name=profil>");
  for (size_t i = 0; i < PROFIL_ANZAHL; i++) {
    s += "<option value=\"" + String(PROFILE[i].kennung) + "\"" + (cfg->profil == PROFILE[i].kennung ? " selected" : "") +
         ">" + html(PROFILE[i].beschreibung) + "</option>";
  }
  s += F("</select></section>");

  s += F("<section><h2>Verbindung</h2><div class=radio>");
  s += "<label><input type=radio name=betrieb value=wlan" + String(cfg->betrieb == BETRIEB_WLAN ? " checked" : "") +
       "> WLAN über Router (Standard)</label>";
  s += "<label><input type=radio name=betrieb value=espnow" +
       String(cfg->betrieb == BETRIEB_ESPNOW ? " checked" : "") + "> nur ESP-NOW (Gateway am PC)</label></div>";
  s += F("<div class=klein>Im WLAN-Betrieb ist ESP-NOW zusätzlich auf dem Kanal des Routers erreichbar. "
         "Findet der Knoten keinen Router, geht er in den Notbetrieb auf dem Kanal unten.</div>");
  s += "<label>Kanal ohne Router (1 bis 13)</label><input name=kanal type=number min=1 max=13 value=" +
       String(cfg->kanal) + ">";

  s += F("<h2>Bekannte WLANs</h2><div class=klein>Der Knoten nimmt das stärkste Netz, das er davon findet. "
         "Passwort leer lassen = unverändert. SSID leeren = Eintrag löschen.</div>"
         "<datalist id=netze></datalist>");
  for (uint8_t i = 0; i < MAX_NETZE; i++) {
    const Netz& n = cfg->netze[i];
    s += "<div class=reihe><div><label>SSID " + String(i + 1) + "</label><input name=ssid" + String(i) +
         " list=netze maxlength=32 value=\"" + html(n.ssid) + "\"></div>";
    s += "<div><label>Passwort</label><input name=pw" + String(i) + " type=password maxlength=63 placeholder=\"" +
         (n.passwort.length() ? "unverändert" : "kein Passwort") + "\"></div></div>";
  }
  s += F("<button type=button class=neben onclick=suchen()>WLANs in der Nähe suchen</button><span id=s class=klein></span>");
  s += F("</section>");

  s += F("<section><h2>Sicherheit</h2><div class=reihe>");
  s += "<div><label>Watchdog in ms</label><input name=watchdog type=number min=100 max=2000 value=" +
       String(cfg->watchdogMs) + "></div>";
  s += "<div><label>Höchstes Radtempo (Schritt/s)</label><input name=maxtempo type=number min=100 max=3400 value=" +
       String(cfg->maxTempo) + "></div></div>";
  s += F("<div class=klein>Kommt so lange kein Befehl, stehen die Räder und der Arm hält seine Stellung.</div>"
         "<label>Setup-Passwort (mind. 8 Zeichen)</label><input name=appw type=password minlength=8 maxlength=63 "
         "placeholder=unverändert><div class=klein>Für das Einrichtungs-WLAN und die Anmeldung (Benutzer admin).</div>"
         "</section><button>Speichern und neu starten</button></form>");

  s += F("<section><h2>Wartung</h2>"
         "<form method=post action=/neustart style=display:inline><button class=neben>Neu starten</button></form>"
         "<form method=post action=/werkseinstellungen style=display:inline "
         "onsubmit=\"return confirm('Alle Einstellungen löschen?')\"><button class=gefahr>Werkseinstellungen</button></form>"
         "<div class=klein>BOOT-Taster 3 s: Einrichtung. 10 s: Werkseinstellungen.</div></section>");

  s += F(R"(<script>
const esc=t=>String(t).replace(/[&<>"']/g,c=>'&#'+c.charCodeAt(0)+';');
async function zustand(){try{const z=await(await fetch('/status.json')).json();
document.getElementById('z').innerHTML=`<b>${z.betrieb}</b> &middot; IP ${z.ip} &middot; Kanal ${z.kanal}`+
(z.rssi?` &middot; ${z.rssi} dBm`:'')+`<br>Steuert: ${z.quelle} &middot; `+
(z.watchdog?'<span class=warn>sicherer Zustand</span>':'aktiv')+` &middot; Zyklus ${z.zyklus_us} µs &middot; Busfehler ${z.busfehler} &middot; läuft seit ${z.laufzeit_s} s`;
let h='<tr><th>ID</th><th>Gelenk</th><th>Position</th><th>Tempo</th><th>Last</th><th>U/V</th><th>T/°C</th><th>Status</th></tr>';
for(const s of z.servos){h+=s.online?`<tr><td>${s.id}</td><td>${s.name}${s.drehmoment?' &#9679;':''}</td><td>${s.position}</td><td>${s.tempo}</td><td>${s.last}</td><td>${s.spannung.toFixed(1)}</td><td>${s.temperatur}</td><td${s.fehler?' class=warn':''}>${s.fehler?s.fehler:'ok'}</td></tr>`
:`<tr class=aus><td>${s.id}</td><td>${s.name}</td><td colspan=6>fehlt</td></tr>`}
document.getElementById('t').innerHTML=h+'<tr><td></td><td class=klein colspan=7>&#9679; = Drehmoment an</td></tr>';}catch(e){}
setTimeout(zustand,1000)}
async function suchen(){const s=document.getElementById('s');s.textContent=' suche ...';
for(let i=0;i<30;i++){const r=await(await fetch('/scan')).json();if(!r.laeuft){
document.getElementById('netze').innerHTML=r.netze.map(n=>`<option value="${esc(n.ssid)}">${n.rssi} dBm, Kanal ${n.kanal}</option>`).join('');
s.textContent=' '+r.netze.length+' Netze gefunden, Auswahl im SSID-Feld';return}await new Promise(t=>setTimeout(t,500))}s.textContent=' keine Antwort'}
zustand();
</script></body></html>)");
  server.send(200, "text/html; charset=utf-8", s);
}

void status() {
  if (!erlaubt()) return;
  steuerung::Abbild a;
  steuerung::abbildHolen(a);
  String s;
  s.reserve(1600);
  s += "{\"name\":" + json(cfg->name) + ",\"profil\":" + json(profil_->kennung);
  s += ",\"betrieb\":" + json(netz::betriebText()) + ",\"ip\":" + json(netz::ip().toString());
  s += ",\"kanal\":" + String(netz::kanal()) + ",\"rssi\":" + String(netz::wlanVerbunden() ? WiFi.RSSI() : 0);
  s += ",\"quelle\":" + json(netz::steuerndeQuelleText()) + ",\"watchdog\":" + String(a.watchdog ? "true" : "false");
  s += ",\"zyklus_us\":" + String(a.zyklus_us) + ",\"busfehler\":" + String(a.busfehler);
  s += ",\"laufzeit_s\":" + String(millis() / 1000) + ",\"servos\":[";
  for (uint8_t i = 0; i < a.anzahl; i++) {
    const ServoZustand& z = a.servos[i];
    if (i) s += ',';
    s += "{\"id\":" + String(z.id) + ",\"name\":" + json(profil_->servos[i].name);
    s += ",\"online\":" + String(z.flags & SERVO_ONLINE ? "true" : "false");
    s += ",\"drehmoment\":" + String(z.flags & SERVO_DREHMOMENT ? "true" : "false");
    s += ",\"position\":" + String(z.position) + ",\"tempo\":" + String(z.tempo) + ",\"last\":" + String(z.last);
    s += ",\"spannung\":" + String(z.spannung / 10.0f, 1) + ",\"temperatur\":" + String(z.temperatur);
    s += ",\"fehler\":" + String(z.fehler) + "}";
  }
  s += "]}";
  server.send(200, "application/json", s);
}

// Asynchron, damit Netz und Webseite nicht sekundenlang stehen
void scan() {
  if (!erlaubt()) return;
  int n = WiFi.scanComplete();
  if (n == WIFI_SCAN_FAILED) {
    WiFi.scanNetworks(true, false);
    n = WIFI_SCAN_RUNNING;
  }
  if (n == WIFI_SCAN_RUNNING) {
    server.send(200, "application/json", "{\"laeuft\":true}");
    return;
  }
  String s = "{\"laeuft\":false,\"netze\":[";
  for (int i = 0; i < n; i++) {
    if (i) s += ',';
    s += "{\"ssid\":" + json(WiFi.SSID(i)) + ",\"rssi\":" + String(WiFi.RSSI(i)) +
         ",\"kanal\":" + String(WiFi.channel(i)) + "}";
  }
  s += "]}";
  WiFi.scanDelete();
  server.send(200, "application/json", s);
}

bool zahl(const char* feld, long min, long max, long& wert) {
  String t = server.arg(feld);
  t.trim();
  if (t.isEmpty()) return false;
  for (char c : t)
    if (!isDigit(c)) return false;
  wert = t.toInt();
  return wert >= min && wert <= max;
}

void speichern() {
  if (!erlaubt()) return;
  Einstellungen neu = *cfg;

  String name = server.arg("name");
  name.trim();
  name.toLowerCase();
  bool nameOk = name.length() >= 1 && name.length() <= 15;
  for (char c : name) nameOk = nameOk && (isLowerCase(c) || isDigit(c) || c == '-');
  if (!nameOk) return meldung(400, "Nicht gespeichert", "Name: 1 bis 15 Zeichen, nur a bis z, 0 bis 9 und Bindestrich.");
  neu.name = name;

  String profil = server.arg("profil");
  bool profilOk = false;
  for (size_t i = 0; i < PROFIL_ANZAHL; i++) profilOk = profilOk || profil == PROFILE[i].kennung;
  if (!profilOk) return meldung(400, "Nicht gespeichert", "Unbekanntes Profil.");
  neu.profil = profil;

  neu.betrieb = server.arg("betrieb") == "espnow" ? BETRIEB_ESPNOW : BETRIEB_WLAN;

  long wert;
  if (!zahl("kanal", 1, 13, wert)) return meldung(400, "Nicht gespeichert", "Kanal: 1 bis 13.");
  neu.kanal = wert;
  if (!zahl("watchdog", 100, 2000, wert)) return meldung(400, "Nicht gespeichert", "Watchdog: 100 bis 2000 ms.");
  neu.watchdogMs = wert;
  if (!zahl("maxtempo", 100, 3400, wert)) return meldung(400, "Nicht gespeichert", "Radtempo: 100 bis 3400 Schritt/s.");
  neu.maxTempo = wert;

  for (uint8_t i = 0; i < MAX_NETZE; i++) {
    String ssid = server.arg("ssid" + String(i));
    String pw = server.arg("pw" + String(i));
    ssid.trim();
    if (ssid.length() > 32) return meldung(400, "Nicht gespeichert", "SSID höchstens 32 Zeichen.");
    if (ssid.isEmpty()) {
      neu.netze[i] = Netz{};
      continue;
    }
    // Gleiche SSID und leeres Feld: altes Passwort behalten
    if (pw.isEmpty() && ssid == cfg->netze[i].ssid) pw = cfg->netze[i].passwort;
    if (pw.length() && (pw.length() < 8 || pw.length() > 63))
      return meldung(400, "Nicht gespeichert", "WLAN-Passwort für " + html(ssid) + ": 8 bis 63 Zeichen oder leer.");
    neu.netze[i] = Netz{ssid, pw};
  }
  if (neu.betrieb == BETRIEB_WLAN && !netzEingetragen(neu))
    return meldung(400, "Nicht gespeichert", "Für den WLAN-Betrieb mindestens ein WLAN eintragen.");

  String appw = server.arg("appw");
  if (appw.length()) {
    if (appw.length() < 8 || appw.length() > 63)
      return meldung(400, "Nicht gespeichert", "Setup-Passwort: 8 bis 63 Zeichen.");
    neu.apPasswort = appw;
  }

  einstellungenSpeichern(neu);
  Serial.println("[web] Einstellungen gespeichert, Neustart");
  String ziel = neu.betrieb == BETRIEB_WLAN
                    ? "Der Knoten verbindet sich mit dem Router und ist dann unter <b>http://" + html(neu.name) +
                          ".local/</b> erreichbar. Klappt das nicht, öffnet er wieder <b>" + html(neu.name) +
                          "-setup</b>."
                    : "Der Knoten startet im ESP-NOW-Betrieb auf Kanal " + String(neu.kanal) +
                          ". Die Einstellungen bleiben über <b>" + html(neu.name) + "-setup</b> erreichbar.";
  meldung(200, "Gespeichert", "Neustart in einer Sekunde. " + ziel);
  neustartUm = millis() + 1000;
}

void neustart() {
  if (!erlaubt()) return;
  meldung(200, "Neustart", "Der Knoten startet neu.");
  neustartUm = millis() + 500;
}

void werkseinstellungen() {
  if (!erlaubt()) return;
  einstellungenLoeschen();
  Serial.println("[web] Werkseinstellungen, Neustart");
  meldung(200, "Werkseinstellungen",
          "Alle Einstellungen gelöscht. Nach dem Neustart öffnet der Knoten das Einrichtungs-WLAN.");
  neustartUm = millis() + 500;
}

void nichtGefunden() {
  // Captive Portal: Handys und Laptops fragen nach Testseiten, die gibt es auf die Startseite
  if (netz::apAktiv()) {
    server.sendHeader("Location", "http://" + WiFi.softAPIP().toString() + "/", true);
    server.send(302, "text/plain", "");
    return;
  }
  server.send(404, "text/plain", "nicht gefunden");
}

}  // namespace

void starten(Einstellungen& e, const Profil& profil) {
  cfg = &e;
  profil_ = &profil;
  server.on("/", HTTP_GET, startseite);
  server.on("/status.json", HTTP_GET, status);
  server.on("/scan", HTTP_GET, scan);
  server.on("/speichern", HTTP_POST, speichern);
  server.on("/neustart", HTTP_POST, neustart);
  server.on("/werkseinstellungen", HTTP_POST, werkseinstellungen);
  server.onNotFound(nichtGefunden);
  server.begin();
}

void bearbeiten() {
  // DNS nur, solange das Einrichtungs-WLAN offen ist
  if (netz::apAktiv() && !dnsLaeuft) {
    dnsLaeuft = dns.start(53, "*", WiFi.softAPIP());
  } else if (!netz::apAktiv() && dnsLaeuft) {
    dns.stop();
    dnsLaeuft = false;
  }
  if (dnsLaeuft) dns.processNextRequest();
  server.handleClient();
  if (neustartUm && (int32_t)(millis() - neustartUm) > 0) ESP.restart();
}

}  // namespace webseite
