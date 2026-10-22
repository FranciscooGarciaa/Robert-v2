#pragma once

// Copy to secrets.h (gitignored) and fill in.
// Several networks may be listed: the strongest known one is used
// (e.g. home Wi-Fi during development, the Raspberry Pi hotspot later).

struct WifiCredential {
  const char* ssid;
  const char* password;
};

inline const WifiCredential WIFI_NETWORKS[] = {
    {"your-home-ssid", "your-home-password"},
    // {"robert-ap", "robert-ap-password"},
};

// Shared secret the Raspberry Pi must present in its "hello" message.
// Must match ESP32_TOKEN in pi/.env.
inline const char* CONTROL_TOKEN = "change-me";
