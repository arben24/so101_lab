#include "profile.h"

#include <string.h>

using protokoll::MODUS_POSITION;
using protokoll::MODUS_TEMPO;

namespace {
// IDs wie in LeRobot: Arm 1 bis 6, Räder 7 bis 9 am selben Bus.
const ServoProfil LEKIWI[] = {
    {1, MODUS_POSITION, "arm_shoulder_pan"}, {2, MODUS_POSITION, "arm_shoulder_lift"},
    {3, MODUS_POSITION, "arm_elbow_flex"},   {4, MODUS_POSITION, "arm_wrist_flex"},
    {5, MODUS_POSITION, "arm_wrist_roll"},   {6, MODUS_POSITION, "arm_gripper"},
    {7, MODUS_TEMPO, "base_left_wheel"},     {8, MODUS_TEMPO, "base_back_wheel"},
    {9, MODUS_TEMPO, "base_right_wheel"},
};

const ServoProfil SO101[] = {
    {1, MODUS_POSITION, "shoulder_pan"}, {2, MODUS_POSITION, "shoulder_lift"},
    {3, MODUS_POSITION, "elbow_flex"},   {4, MODUS_POSITION, "wrist_flex"},
    {5, MODUS_POSITION, "wrist_roll"},   {6, MODUS_POSITION, "gripper"},
};
}  // namespace

const Profil PROFILE[] = {
    {"lekiwi", "lekiwi", "LeKiwi: Arm 1 bis 6 (Position), Räder 7 bis 9 (Tempo)", LEKIWI, 9, false},
    {"so101", "so101", "SO-101 Folgearm: Gelenke 1 bis 6 (Position)", SO101, 6, false},
    {"so101_leader", "leader", "SO-101 Leader: Gelenke 1 bis 6, nur lesen, nie Drehmoment", SO101, 6, true},
};
const size_t PROFIL_ANZAHL = sizeof(PROFILE) / sizeof(PROFILE[0]);

const Profil& profilSuchen(const char* kennung) {
  for (size_t i = 0; i < PROFIL_ANZAHL; i++)
    if (strcmp(PROFILE[i].kennung, kennung) == 0) return PROFILE[i];
  return PROFILE[0];
}
