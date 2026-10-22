#pragma once
#include <Arduino.h>

// JSON protocol v1 between the Raspberry Pi and the ESP32.
//
// Pi -> ESP32:
//   {"type":"hello","v":1,"token":"..."}
//   {"type":"move","left":-255..255,"right":-255..255}
//   {"type":"stop"}
//   {"type":"heartbeat"}
// ESP32 -> Pi:
//   {"type":"hello_ack",...}  {"type":"telemetry",...}
//   {"type":"event",...}      {"type":"error","error":"..."}

namespace protocol {

enum class CmdType { Invalid, Hello, Move, Stop, Heartbeat };

struct Command {
  CmdType type = CmdType::Invalid;
  int left = 0;
  int right = 0;
  int version = 0;
  char token[65] = {0};
  const char* error = nullptr;  // set when type == Invalid
};

Command parse(const uint8_t* payload, size_t len);

// Each builder writes a JSON document into buf and returns its length
// (0 if it did not fit).
size_t buildHelloAck(char* buf, size_t cap, const char* resetReason);
size_t buildTelemetry(char* buf, size_t cap);
size_t buildEvent(char* buf, size_t cap, const char* failsafe);
size_t buildError(char* buf, size_t cap, const char* error);

}  // namespace protocol
