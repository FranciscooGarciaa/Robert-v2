#include "motor_controller.h"

#include "config.h"

namespace motors {
namespace {

int tLeft = 0, tRight = 0;  // targets
int oLeft = 0, oRight = 0;  // currently applied outputs
uint32_t lastRampMs = 0;

int clampSpeed(int v) {
  return constrain(v, -cfg::MOTOR_MAX_PWM, cfg::MOTOR_MAX_PWM);
}

// The only place that maps a signed speed to DIR/PWM pins.
void applySide(int pwmPin, int dirPin, int speed) {
  const uint8_t reverseLevel = cfg::DIR_FORWARD_LEVEL == HIGH ? LOW : HIGH;
  if (speed == 0) {
    ledcWrite(pwmPin, 0);
    return;
  }
  digitalWrite(dirPin, speed > 0 ? cfg::DIR_FORWARD_LEVEL : reverseLevel);
  ledcWrite(pwmPin, abs(speed));
}

void apply() {
  applySide(cfg::LEFT_PWM, cfg::LEFT_DIR, oLeft);
  applySide(cfg::RIGHT_PWM, cfg::RIGHT_DIR, oRight);
}

// Move current toward target by at most step; never crosses zero in one step
// so direction only changes once the side has come to rest.
int stepToward(int current, int target, int step) {
  if (step <= 0) return target;
  if (current != 0 && target != 0 && (current > 0) != (target > 0)) {
    target = 0;
  }
  if (current < target) return min(current + step, target);
  if (current > target) return max(current - step, target);
  return current;
}

}  // namespace

void begin() {
  // Hold every control line LOW before PWM is attached, so the driver never
  // sees a floating input during boot.
  const int pins[] = {cfg::PWM1, cfg::DIR1, cfg::PWM2, cfg::DIR2};
  for (int p : pins) {
    pinMode(p, OUTPUT);
    digitalWrite(p, LOW);
  }
  ledcAttach(cfg::PWM1, cfg::PWM_FREQ_HZ, cfg::PWM_RES_BITS);
  ledcAttach(cfg::PWM2, cfg::PWM_FREQ_HZ, cfg::PWM_RES_BITS);
  emergencyStop();
}

void setTargets(int left, int right) {
  tLeft = clampSpeed(left);
  tRight = clampSpeed(right);
}

void emergencyStop() {
  tLeft = tRight = 0;
  oLeft = oRight = 0;
  ledcWrite(cfg::PWM1, 0);
  ledcWrite(cfg::PWM2, 0);
}

void update(uint32_t nowMs) {
  if (nowMs - lastRampMs < cfg::MOTOR_RAMP_INTERVAL_MS) return;
  lastRampMs = nowMs;
  const int nl = stepToward(oLeft, tLeft, cfg::MOTOR_RAMP_STEP);
  const int nr = stepToward(oRight, tRight, cfg::MOTOR_RAMP_STEP);
  if (nl == oLeft && nr == oRight) return;
  oLeft = nl;
  oRight = nr;
  apply();
}

int targetLeft() { return tLeft; }
int targetRight() { return tRight; }
int outputLeft() { return oLeft; }
int outputRight() { return oRight; }

}  // namespace motors
