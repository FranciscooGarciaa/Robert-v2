#include "ultrasonic.h"

#include <soc/gpio_reg.h>

#include "config.h"

namespace ultrasonic {
namespace {

static_assert(cfg::ECHO_PIN >= 32, "echo ISR reads GPIO_IN1_REG (pins 32-39)");

volatile uint32_t riseUs = 0;
volatile uint32_t pulseUs = 0;
volatile bool pulseDone = false;

bool waiting = false;
uint32_t triggerUs = 0;
uint32_t lastTriggerMs = 0;

float samples[3] = {INFINITY, INFINITY, INFINITY};  // INFINITY = no echo
uint8_t sampleIdx = 0;
float filtered = NAN;
bool obstacleFlag = false;
uint32_t timeouts = 0;

void IRAM_ATTR echoIsr() {
  const uint32_t now = micros();
  const bool high = (REG_READ(GPIO_IN1_REG) >> (cfg::ECHO_PIN - 32)) & 1;
  if (high) {
    riseUs = now;
  } else if (riseUs != 0) {
    pulseUs = now - riseUs;
    pulseDone = true;
  }
}

float median3(float a, float b, float c) {
  return max(min(a, b), min(max(a, b), c));
}

void pushSample(float cm) {
  samples[sampleIdx] = cm;
  sampleIdx = (sampleIdx + 1) % 3;
  const float m = median3(samples[0], samples[1], samples[2]);
  filtered = isinf(m) ? NAN : m;

  if (isnan(filtered)) {
    obstacleFlag = false;
  } else if (filtered < cfg::ULTRASONIC_STOP_CM) {
    obstacleFlag = true;
  } else if (filtered > cfg::ULTRASONIC_STOP_CM + cfg::ULTRASONIC_CLEAR_HYST_CM) {
    obstacleFlag = false;
  }
}

void trigger() {
  noInterrupts();
  riseUs = 0;
  pulseDone = false;
  interrupts();
  digitalWrite(cfg::TRIG_PIN, LOW);
  delayMicroseconds(2);
  digitalWrite(cfg::TRIG_PIN, HIGH);
  delayMicroseconds(10);
  digitalWrite(cfg::TRIG_PIN, LOW);
  triggerUs = micros();
  waiting = true;
}

}  // namespace

void begin() {
  pinMode(cfg::TRIG_PIN, OUTPUT);
  digitalWrite(cfg::TRIG_PIN, LOW);
  pinMode(cfg::ECHO_PIN, INPUT);
  if (cfg::ULTRASONIC_ENABLED) {
    attachInterrupt(digitalPinToInterrupt(cfg::ECHO_PIN), echoIsr, CHANGE);
  }
}

void update(uint32_t nowMs) {
  if (!cfg::ULTRASONIC_ENABLED) return;

  if (waiting) {
    if (pulseDone) {
      waiting = false;
      // Same conversion as the validated pulseIn() code.
      float cm = pulseUs * 0.0343f / 2.0f;
      if (cm > cfg::ULTRASONIC_MAX_CM) {
        cm = INFINITY;
        timeouts++;
      } else {
        // Very close echoes are kept (clamped) so they still count as obstacles.
        cm = max(cm, cfg::ULTRASONIC_MIN_CM);
        timeouts = 0;
      }
      pushSample(cm);
    } else if (micros() - triggerUs > cfg::ULTRASONIC_ECHO_TIMEOUT_US) {
      waiting = false;
      timeouts++;
      pushSample(INFINITY);
    }
    return;
  }

  if (nowMs - lastTriggerMs >= cfg::ULTRASONIC_PERIOD_MS) {
    lastTriggerMs = nowMs;
    trigger();
  }
}

float distanceCm() { return filtered; }
bool obstacle() { return obstacleFlag; }
uint32_t consecutiveTimeouts() { return timeouts; }

}  // namespace ultrasonic
