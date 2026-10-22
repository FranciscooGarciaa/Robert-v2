#pragma once
#include <Arduino.h>

// Owns the decision of what the motors are allowed to do.
// Everything fails toward STOP: boot, watchdog expiry, controller loss,
// Wi-Fi loss and malformed commands all zero the motors. Forward motion is
// additionally vetoed locally while the ultrasonic sensor sees an obstacle;
// reverse and in-place rotation stay available to escape.

namespace safety {

enum class Failsafe {
  None,        // motion permitted
  Boot,        // no valid move received since boot
  Watchdog,    // controller stopped sending valid commands
  Disconnect,  // controller socket closed
  WifiLost,    // ESP32 lost Wi-Fi
  Handover,    // a new controller replaced the previous one
  Malformed,   // controller sent an invalid message
  Obstacle,    // forward motion vetoed by ultrasonic (not latched)
};

const char* name(Failsafe f);

// Called whenever a safety state changes (for telemetry events).
using EventSink = void (*)(Failsafe reason);
void setEventSink(EventSink sink);

// Valid, authenticated command received: refreshes the watchdog.
void feedWatchdog(uint32_t nowMs);

// Valid move: refreshes the watchdog and clears any latched failsafe.
void requestMove(int left, int right, uint32_t nowMs);

// Valid stop: refreshes the watchdog, zeroes motors immediately.
void requestStop(uint32_t nowMs);

// Immediate stop and latch the reason. Motion resumes only on a new move.
void trip(Failsafe reason);

// Call every loop: enforces the watchdog and obstacle veto, then hands the
// permitted speeds to the motor controller.
void update(uint32_t nowMs);

Failsafe current();
bool forwardBlocked();
int requestedLeft();
int requestedRight();

}  // namespace safety
