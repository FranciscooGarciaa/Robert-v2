#include "safety.h"

#include "config.h"
#include "motor_controller.h"
#include "ultrasonic.h"

namespace safety {
namespace {

int reqLeft = 0, reqRight = 0;
uint32_t lastCommandMs = 0;
Failsafe latched = Failsafe::Boot;
bool blocked = false;
EventSink sink = nullptr;

void emit(Failsafe f) {
  if (sink) sink(f);
}

}  // namespace

const char* name(Failsafe f) {
  switch (f) {
    case Failsafe::None: return "none";
    case Failsafe::Boot: return "boot";
    case Failsafe::Watchdog: return "watchdog";
    case Failsafe::Disconnect: return "disconnect";
    case Failsafe::WifiLost: return "wifi_lost";
    case Failsafe::Handover: return "handover";
    case Failsafe::Malformed: return "malformed";
    case Failsafe::Obstacle: return "obstacle";
  }
  return "unknown";
}

void setEventSink(EventSink s) { sink = s; }

void feedWatchdog(uint32_t nowMs) { lastCommandMs = nowMs; }

void requestMove(int left, int right, uint32_t nowMs) {
  feedWatchdog(nowMs);
  left = constrain(left, -cfg::MOTOR_MAX_PWM, cfg::MOTOR_MAX_PWM);
  right = constrain(right, -cfg::MOTOR_MAX_PWM, cfg::MOTOR_MAX_PWM);
  if (left != reqLeft || right != reqRight) {
    Serial.printf("[MOVE] L=%d R=%d\n", left, right);
  }
  reqLeft = left;
  reqRight = right;
  if (latched != Failsafe::None) {
    latched = Failsafe::None;
    emit(Failsafe::None);
  }
}

void requestStop(uint32_t nowMs) {
  feedWatchdog(nowMs);
  if (reqLeft != 0 || reqRight != 0) Serial.println("[MOVE] STOP");
  reqLeft = reqRight = 0;
  motors::emergencyStop();
}

void trip(Failsafe reason) {
  reqLeft = reqRight = 0;
  motors::emergencyStop();
  if (latched != reason) {
    Serial.printf("[SAFE] %s -> STOP\n", name(reason));
    latched = reason;
    emit(reason);
  }
}

void update(uint32_t nowMs) {
  const bool moving = reqLeft != 0 || reqRight != 0;
  if (moving && nowMs - lastCommandMs > cfg::WATCHDOG_MS) {
    trip(Failsafe::Watchdog);
  }

  // Veto any command with a net forward component; reverse and pure
  // rotation (left + right <= 0) remain allowed.
  const bool nowBlocked = ultrasonic::obstacle() && (reqLeft + reqRight > 0);
  if (nowBlocked != blocked) {
    blocked = nowBlocked;
    if (blocked) {
      Serial.printf("[SAFE] obstacle %.1fcm -> STOP forward\n", ultrasonic::distanceCm());
      motors::emergencyStop();
    } else {
      Serial.println("[SAFE] obstacle cleared");
    }
    emit(blocked ? Failsafe::Obstacle : latched);
  }

  if (blocked) {
    motors::setTargets(0, 0);
  } else {
    motors::setTargets(reqLeft, reqRight);
  }
}

Failsafe current() { return blocked ? Failsafe::Obstacle : latched; }
bool forwardBlocked() { return blocked; }
int requestedLeft() { return reqLeft; }
int requestedRight() { return reqRight; }

}  // namespace safety
