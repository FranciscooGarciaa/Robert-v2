from robert.robot.motion import MotionController
from robert.robot.state import RobotState


class FakeClock:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t


def make(max_speed=180, deadman_ms=300):
    clock = FakeClock()
    state = RobotState()
    return MotionController(state, max_speed, deadman_ms, clock), state, clock


def test_clamps_to_max_speed():
    m, state, _ = make(max_speed=150)
    m.drive(255, -999, "a")
    assert m.current_target() == (150, -150)


def test_deadman_stops_when_not_refreshed():
    m, state, clock = make(deadman_ms=300)
    m.drive(100, 100, "a")
    clock.t += 0.25
    assert m.current_target() == (100, 100)
    clock.t += 0.1
    assert m.current_target() == (0, 0)
    assert state.motion.last_stop_reason == "deadman"


def test_release_only_stops_for_current_driver():
    m, state, _ = make()
    m.drive(100, 100, "a")
    m.release("b")
    assert m.current_target() == (100, 100)
    m.release("a")
    assert m.current_target() == (0, 0)
    assert state.motion.last_stop_reason == "driver_disconnected"


def test_stop_notifies_listeners():
    m, _, _ = make()
    calls = []
    m.on_stop(lambda: calls.append(1))
    m.drive(50, 50, "a")
    m.stop("test")
    assert calls == [1]
    assert m.current_target() == (0, 0)
