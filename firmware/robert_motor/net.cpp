#include "net.h"

#include <ArduinoOTA.h>
#include <ESPmDNS.h>
#include <WebSocketsServer.h>
#include <WiFi.h>
#include <WiFiMulti.h>

#include "config.h"
#include "secrets.h"

namespace net {
namespace {

WiFiMulti wifiMulti;
WebSocketsServer ws(cfg::WS_PORT);
TextHandler textHandler = nullptr;
LostHandler lostHandler = nullptr;
OtaStartHandler otaStartHandler = nullptr;

int controllerClient = NO_CONTROLLER;
bool wasConnected = false;
bool otaStarted = false;
uint32_t lastWifiAttemptMs = 0;

void onWsEvent(uint8_t num, WStype_t type, uint8_t* payload, size_t length) {
  switch (type) {
    case WStype_CONNECTED:
      Serial.printf("[CTRL] client %u connected from %s\n", num,
                    ws.remoteIP(num).toString().c_str());
      break;
    case WStype_DISCONNECTED:
      Serial.printf("[CTRL] client %u disconnected\n", num);
      if (num == controllerClient) {
        controllerClient = NO_CONTROLLER;
        if (lostHandler) lostHandler("disconnect");
      }
      break;
    case WStype_TEXT:
      if (textHandler) textHandler(num, payload, length);
      break;
    default:
      break;
  }
}

void onWifiUp() {
  Serial.printf("[WIFI] connected to %s, ip=%s rssi=%d\n", WiFi.SSID().c_str(),
                WiFi.localIP().toString().c_str(), WiFi.RSSI());
  MDNS.end();
  if (MDNS.begin(cfg::MDNS_HOSTNAME)) {
    MDNS.addService("ws", "tcp", cfg::WS_PORT);
    Serial.printf("[WIFI] mDNS %s.local\n", cfg::MDNS_HOSTNAME);
  } else {
    Serial.println("[WIFI] mDNS failed");
  }
  if (!otaStarted) {
    ArduinoOTA.begin();
    otaStarted = true;
    Serial.printf("[OTA] ready on port %u\n", cfg::OTA_PORT);
  }
}

void onWifiDown() {
  Serial.println("[WIFI] lost");
  controllerClient = NO_CONTROLLER;  // before disconnect: report wifi_lost
  if (lostHandler) lostHandler("wifi_lost");
  ws.disconnect();
}

}  // namespace

void begin(TextHandler onText, LostHandler onLost, OtaStartHandler onOtaStart) {
  textHandler = onText;
  lostHandler = onLost;
  otaStartHandler = onOtaStart;

  WiFi.mode(WIFI_STA);
  WiFi.setHostname(cfg::MDNS_HOSTNAME);
  WiFi.setSleep(false);  // lower, steadier latency
  WiFi.setAutoReconnect(true);
  for (const auto& n : WIFI_NETWORKS) wifiMulti.addAP(n.ssid, n.password);

  // Firmware updates over Wi-Fi. Motors are stopped before the transfer
  // starts (onOtaStart), and the device reboots into STOP afterwards.
  ArduinoOTA.setHostname(cfg::MDNS_HOSTNAME);
  ArduinoOTA.setPort(cfg::OTA_PORT);
  ArduinoOTA.setPassword(CONTROL_TOKEN);
  ArduinoOTA.setMdnsEnabled(false);  // mDNS is managed in onWifiUp()
  ArduinoOTA.onStart([] {
    Serial.println("[OTA] update starting");
    if (otaStartHandler) otaStartHandler();
  });
  ArduinoOTA.onEnd([] { Serial.println("[OTA] done, rebooting"); });
  ArduinoOTA.onError([](ota_error_t e) { Serial.printf("[OTA] error %u\n", e); });

  ws.begin();
  ws.onEvent(onWsEvent);
  // Detect silently dead sockets; the command watchdog is still the primary
  // protection and is much faster.
  ws.enableHeartbeat(1000, 1000, 2);
}

void update(uint32_t nowMs) {
  const bool connected = WiFi.status() == WL_CONNECTED;
  if (connected && !wasConnected) onWifiUp();
  if (!connected && wasConnected) onWifiDown();
  wasConnected = connected;

  if (!connected) {
    // Blocking scan/connect; motors are already stopped when we get here.
    if (nowMs - lastWifiAttemptMs >= cfg::WIFI_RETRY_MS) {
      lastWifiAttemptMs = nowMs;
      wifiMulti.run(3000);
    }
    return;
  }
  ws.loop();
  ArduinoOTA.handle();
}

bool wifiConnected() { return wasConnected; }

int controller() { return controllerClient; }

void setController(uint8_t client) {
  if (controllerClient != NO_CONTROLLER && controllerClient != client) {
    const uint8_t previous = controllerClient;
    controllerClient = client;  // set first so the disconnect is not "lost"
    ws.disconnect(previous);
  }
  controllerClient = client;
  Serial.printf("[CTRL] client %u is now the controller\n", client);
}

void send(uint8_t client, const char* msg, size_t len) {
  ws.sendTXT(client, msg, len);
}

void sendToController(const char* msg, size_t len) {
  if (controllerClient != NO_CONTROLLER) ws.sendTXT(controllerClient, msg, len);
}

void disconnect(uint8_t client) { ws.disconnect(client); }

}  // namespace net
