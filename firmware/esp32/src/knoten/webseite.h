#pragma once
// Einrichtungs- und Statusseite auf Port 80.
//
// Im Einrichtungs-WLAN (<name>-setup) unter http://192.168.4.1/, dort leitet ein
// DNS-Server jede Adresse auf die Seite um (Captive Portal). Im Router-WLAN unter
// http://<name>.local/ oder der IP, dann mit Anmeldung admin + Setup-Passwort.

#include "einstellungen.h"
#include "profile.h"

namespace webseite {
void starten(Einstellungen& e, const Profil& profil);
void bearbeiten();  // aus loop()
}  // namespace webseite
