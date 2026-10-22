#pragma once
#include <Arduino.h>

// Non-blocking HC-SR04 reader. ECHO edges are timed by an interrupt so the
// main loop (Wi-Fi, watchdog, ramp) never stalls waiting for an echo.

namespace ultrasonic {

void begin();

// Call every loop. Triggers a ping every cfg::ULTRASONIC_PERIOD_MS and
// collects the result.
void update(uint32_t nowMs);

// Median of the last 3 samples; NAN when there is no echo (nothing in range
// or sensor not answering).
float distanceCm();

// True while a confirmed obstacle is closer than cfg::ULTRASONIC_STOP_CM
// (released with hysteresis).
bool obstacle();

// Consecutive pings that got no echo.
uint32_t consecutiveTimeouts();

}  // namespace ultrasonic
