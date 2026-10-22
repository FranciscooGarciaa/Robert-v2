#pragma once
#include <Arduino.h>

// Wi-Fi (multi-network, auto-reconnect), mDNS and the WebSocket server.
// Tracks which single client is the authenticated controller.

namespace net {

constexpr int NO_CONTROLLER = -1;

using TextHandler = void (*)(uint8_t client, const uint8_t* payload, size_t len);
// Called when the controller is lost: "disconnect" or "wifi_lost".
using LostHandler = void (*)(const char* reason);
// Called right before a firmware update over Wi-Fi begins.
using OtaStartHandler = void (*)();

void begin(TextHandler onText, LostHandler onLost, OtaStartHandler onOtaStart);
void update(uint32_t nowMs);

bool wifiConnected();

int controller();
// Make `client` the controller; any previous controller is disconnected.
void setController(uint8_t client);

void send(uint8_t client, const char* msg, size_t len);
void sendToController(const char* msg, size_t len);
void disconnect(uint8_t client);

}  // namespace net
