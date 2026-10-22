#pragma once
#include <Arduino.h>

// Differential drive output stage (Cytron MDD10A).
// Signed speeds: -255 (full reverse) .. +255 (full forward).
// All physical polarity knowledge lives in motor_controller.cpp.

namespace motors {

// Must be the first thing called in setup(): drives all outputs to STOP.
void begin();

// Desired speeds; clamped to +/-cfg::MOTOR_MAX_PWM. Reached via ramp.
void setTargets(int left, int right);

// Immediate stop: target and output to 0, bypassing the ramp.
void emergencyStop();

// Call every loop; advances the ramp at cfg::MOTOR_RAMP_INTERVAL_MS.
void update(uint32_t nowMs);

int targetLeft();
int targetRight();
int outputLeft();
int outputRight();

}  // namespace motors
