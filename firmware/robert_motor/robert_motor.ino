// Robert V2 — ESP32 real-time motor & safety controller.
//
// Boot order matters: motor outputs are forced to STOP before anything else,
// and the motors stay stopped until an authenticated controller sends a
// valid move command. See README.md for the safety rules.

#if !__has_include("secrets.h")
#error "Copy secrets.example.h to secrets.h and fill in Wi-Fi + token"
#endif

#include <esp_system.h>

#include "config.h"
#include "motor_controller.h"
#include "net.h"
#include "protocol.h"
#include "safety.h"
#include "secrets.h"
#include "ultrasonic.h"

namespace {

char txBuf[384];
uint32_t lastTelemetryMs = 0;

const char* resetReasonName() {
  switch (esp_reset_reason()) {
    case ESP_RST_POWERON: return "poweron";
    case ESP_RST_SW: return "software";
    case ESP_RST_PANIC: return "panic";
    case ESP_RST_INT_WDT: return "int_wdt";
    case ESP_RST_TASK_WDT: return "task_wdt";
    case ESP_RST_WDT: return "wdt";
    case ESP_RST_BROWNOUT: return "brownout";
    case ESP_RST_EXT: return "external";
    case ESP_RST_DEEPSLEEP: return "deepsleep";
    default: return "other";
  }
}

void sendError(uint8_t client, const char* error) {
  const size_t n = protocol::buildError(txBuf, sizeof(txBuf), error);
  if (n) net::send(client, txBuf, n);
}

void onSafetyEvent(safety::Failsafe reason) {
  const size_t n = protocol::buildEvent(txBuf, sizeof(txBuf), safety::name(reason));
  if (n) net::sendToController(txBuf, n);
}

void onControllerLost(const char* reason) {
  safety::trip(strcmp(reason, "wifi_lost") == 0 ? safety::Failsafe::WifiLost
                                                : safety::Failsafe::Disconnect);
}

void onText(uint8_t client, const uint8_t* payload, size_t len) {
  const uint32_t now = millis();
  const protocol::Command cmd = protocol::parse(payload, len);
  const bool isController = net::controller() == client;

  if (cmd.type == protocol::CmdType::Invalid) {
    Serial.printf("[PROTO] client %u: %s\n", client, cmd.error);
    sendError(client, cmd.error);
    // Never refresh the watchdog on bad input; from the controller, stop.
    if (isController) safety::trip(safety::Failsafe::Malformed);
    return;
  }

  if (cmd.type == protocol::CmdType::Hello) {
    if (cmd.version != cfg::PROTOCOL_VERSION) {
      Serial.printf("[CTRL] client %u: protocol v%d unsupported\n", client, cmd.version);
      sendError(client, "bad_version");
      net::disconnect(client);
      return;
    }
    if (strcmp(cmd.token, CONTROL_TOKEN) != 0) {
      Serial.printf("[CTRL] client %u: bad token\n", client);
      sendError(client, "bad_token");
      net::disconnect(client);
      return;
    }
    if (net::controller() != net::NO_CONTROLLER && !isController) {
      safety::trip(safety::Failsafe::Handover);
    }
    net::setController(client);
    const size_t n = protocol::buildHelloAck(txBuf, sizeof(txBuf), resetReasonName());
    if (n) net::send(client, txBuf, n);
    return;
  }

  if (!isController) {
    sendError(client, "not_controller");
    return;
  }

  switch (cmd.type) {
    case protocol::CmdType::Move:
      safety::requestMove(cmd.left, cmd.right, now);
      break;
    case protocol::CmdType::Stop:
      safety::requestStop(now);
      break;
    case protocol::CmdType::Heartbeat:
      safety::feedWatchdog(now);
      break;
    default:
      break;
  }
}

}  // namespace

void setup() {
  motors::begin();  // FIRST: outputs to STOP

  Serial.begin(115200);
  delay(50);
  Serial.printf("\n[BOOT] Robert motor controller, reset=%s\n", resetReasonName());

  ultrasonic::begin();
  safety::setEventSink(onSafetyEvent);
  net::begin(onText, onControllerLost);
  Serial.println("[BOOT] ready, motors STOPPED until a controller sends move");
}

void loop() {
  const uint32_t now = millis();

  net::update(now);
  ultrasonic::update(now);
  safety::update(now);
  motors::update(now);

  if (now - lastTelemetryMs >= cfg::TELEMETRY_INTERVAL_MS) {
    lastTelemetryMs = now;
    const size_t n = protocol::buildTelemetry(txBuf, sizeof(txBuf));
    if (n) net::sendToController(txBuf, n);
  }
}
