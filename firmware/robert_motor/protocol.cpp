#include "protocol.h"

#include <ArduinoJson.h>
#include <WiFi.h>

#include "config.h"
#include "motor_controller.h"
#include "safety.h"
#include "ultrasonic.h"

namespace protocol {
namespace {

Command invalid(const char* why) {
  Command c;
  c.error = why;
  return c;
}

// Accept only JSON integers (not floats, strings or bools) for speeds.
bool readSpeed(JsonVariantConst v, int& out) {
  if (!v.is<long>()) return false;
  out = constrain(v.as<long>(), -cfg::MOTOR_MAX_PWM, cfg::MOTOR_MAX_PWM);
  return true;
}

size_t finish(JsonDocument& doc, char* buf, size_t cap) {
  if (measureJson(doc) >= cap) return 0;
  return serializeJson(doc, buf, cap);
}

void putDistance(JsonDocument& doc) {
  const float d = ultrasonic::distanceCm();
  if (isnan(d)) {
    doc["distance_cm"] = nullptr;
  } else {
    doc["distance_cm"] = roundf(d * 10.0f) / 10.0f;
  }
}

}  // namespace

Command parse(const uint8_t* payload, size_t len) {
  if (len == 0 || len > 256) return invalid("bad_length");

  JsonDocument doc;
  if (deserializeJson(doc, payload, len)) return invalid("bad_json");

  const char* type = doc["type"];
  if (!type) return invalid("missing_type");

  Command c;
  if (strcmp(type, "move") == 0) {
    if (!readSpeed(doc["left"], c.left) || !readSpeed(doc["right"], c.right)) {
      return invalid("bad_speed");
    }
    c.type = CmdType::Move;
  } else if (strcmp(type, "stop") == 0) {
    c.type = CmdType::Stop;
  } else if (strcmp(type, "heartbeat") == 0) {
    c.type = CmdType::Heartbeat;
  } else if (strcmp(type, "hello") == 0) {
    const char* token = doc["token"];
    if (!doc["v"].is<int>() || !token) return invalid("bad_hello");
    c.version = doc["v"];
    strlcpy(c.token, token, sizeof(c.token));
    c.type = CmdType::Hello;
  } else {
    return invalid("unknown_type");
  }
  return c;
}

size_t buildHelloAck(char* buf, size_t cap, const char* resetReason) {
  JsonDocument doc;
  doc["type"] = "hello_ack";
  doc["v"] = cfg::PROTOCOL_VERSION;
  doc["watchdog_ms"] = cfg::WATCHDOG_MS;
  doc["max_pwm"] = cfg::MOTOR_MAX_PWM;
  doc["obstacle_stop_cm"] = cfg::ULTRASONIC_STOP_CM;
  doc["reset_reason"] = resetReason;
  doc["uptime_ms"] = millis();
  return finish(doc, buf, cap);
}

size_t buildTelemetry(char* buf, size_t cap) {
  JsonDocument doc;
  doc["type"] = "telemetry";
  putDistance(doc);
  doc["obstacle"] = ultrasonic::obstacle();
  doc["us_timeouts"] = ultrasonic::consecutiveTimeouts();
  doc["left_target"] = safety::requestedLeft();
  doc["right_target"] = safety::requestedRight();
  doc["left_out"] = motors::outputLeft();
  doc["right_out"] = motors::outputRight();
  doc["failsafe"] = safety::name(safety::current());
  doc["wifi_rssi"] = WiFi.RSSI();
  doc["uptime_ms"] = millis();
  return finish(doc, buf, cap);
}

size_t buildEvent(char* buf, size_t cap, const char* failsafe) {
  JsonDocument doc;
  doc["type"] = "event";
  doc["failsafe"] = failsafe;
  putDistance(doc);
  doc["uptime_ms"] = millis();
  return finish(doc, buf, cap);
}

size_t buildError(char* buf, size_t cap, const char* error) {
  JsonDocument doc;
  doc["type"] = "error";
  doc["error"] = error;
  return finish(doc, buf, cap);
}

}  // namespace protocol
