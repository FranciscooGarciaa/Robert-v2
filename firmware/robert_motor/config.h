#pragma once
#include <Arduino.h>

// Robert V2 motor controller — compile-time configuration.

namespace cfg {

// ---------------------------------------------------------------------------
// VALIDATED PIN MAPPING — physically tested. Do not change without measurement.
// M1/M2 = the two MDD10A channels (two motors in parallel each).
// Which channel is left/right is defined below, from the robot front.
// ---------------------------------------------------------------------------
constexpr int PWM1 = 25;  // MDD10A PWM1 (M1)
constexpr int DIR1 = 26;  // MDD10A DIR1
constexpr int PWM2 = 27;  // MDD10A PWM2 (M2)
constexpr int DIR2 = 33;  // MDD10A DIR2

constexpr int TRIG_PIN = 32;  // HC-SR04 TRIG
constexpr int ECHO_PIN = 35;  // HC-SR04 ECHO via 1k/2k divider (input-only pin)

// Robot frame: FRONT = the HC-SR04 side (so the obstacle veto guards the
// direction of travel). Verified on the bench 2026-10-22: seen from that
// front, M2 drives the left wheels, M1 the right ones, and forward is
// DIR LOW on both (the original handoff described the robot from the other
// end, hence "M1 = left, forward = DIR HIGH" there).
constexpr int LEFT_PWM = PWM2;
constexpr int LEFT_DIR = DIR2;
constexpr int RIGHT_PWM = PWM1;
constexpr int RIGHT_DIR = DIR1;
constexpr uint8_t DIR_FORWARD_LEVEL = LOW;

// ---------------------------------------------------------------------------
// Motors
// ---------------------------------------------------------------------------
constexpr uint32_t PWM_FREQ_HZ = 20000;  // MDD10A supports up to 20 kHz
constexpr uint8_t PWM_RES_BITS = 8;      // duty 0..255
constexpr int MOTOR_MAX_PWM = 255;       // absolute clamp for any command

// Slew limiting: max duty change per ramp interval. 0 disables ramping.
constexpr int MOTOR_RAMP_STEP = 15;      // 0 -> 255 in ~170 ms
constexpr uint32_t MOTOR_RAMP_INTERVAL_MS = 10;

// ---------------------------------------------------------------------------
// Safety
// ---------------------------------------------------------------------------
constexpr uint32_t WATCHDOG_MS = 500;  // no valid command for this long -> STOP

// ---------------------------------------------------------------------------
// Ultrasonic
// ---------------------------------------------------------------------------
constexpr bool ULTRASONIC_ENABLED = true;
constexpr float ULTRASONIC_STOP_CM = 15.0f;       // block forward below this
constexpr float ULTRASONIC_CLEAR_HYST_CM = 5.0f;  // release above STOP + HYST
constexpr uint32_t ULTRASONIC_PERIOD_MS = 60;     // trigger period
constexpr uint32_t ULTRASONIC_ECHO_TIMEOUT_US = 30000;
constexpr float ULTRASONIC_MIN_CM = 2.0f;    // closer readings clamp to this
constexpr float ULTRASONIC_MAX_CM = 400.0f;  // farther readings = no echo

// ---------------------------------------------------------------------------
// Network / protocol
// ---------------------------------------------------------------------------
constexpr const char* MDNS_HOSTNAME = "robert-motor";  // robert-motor.local
constexpr uint16_t WS_PORT = 81;
constexpr int PROTOCOL_VERSION = 1;
constexpr uint32_t TELEMETRY_INTERVAL_MS = 200;  // 5 Hz
constexpr uint32_t WIFI_RETRY_MS = 2000;
constexpr uint16_t OTA_PORT = 3232;  // ArduinoOTA, password = CONTROL_TOKEN

}  // namespace cfg
