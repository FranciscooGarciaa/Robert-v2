import math

from navsim import scan_view, wall
from robert.nav.follow import Follower, Person, clusters, person_sized


def person_at(x, y, size=0.3):
    h = size / 2
    return (x - h, x + h, y - h, y + h)


def pts(boxes):
    view, _, _ = scan_view(boxes)
    return view.points()


AHEAD = [Person(0.0, math.radians(8))]


def test_clusters_split_people_from_walls():
    cs = clusters(pts([person_at(1.5, 0.0), wall(3.0)]))
    people = [c for c in cs if person_sized(c)]
    assert len(people) == 1 and abs(people[0].x - 1.35) < 0.1


def test_acquires_only_a_person_the_camera_also_sees():
    f = Follower()
    f.start(0.0)
    f.update(pts([person_at(1.5, 0.0)]), [], 0.1)           # LiDAR only: not yet
    assert f.state == "searching"
    f.update(pts([person_at(1.5, 0.0)]), AHEAD, 0.2)
    assert f.state == "following" and abs(f.distance - 1.35) < 0.15


def test_fails_when_nobody_is_in_front():
    f = Follower()
    f.start(0.0)
    f.update(pts([person_at(-1.5, 0.0)]), AHEAD, 1.0)       # behind the robot
    f.update(pts([person_at(-1.5, 0.0)]), AHEAD, 3.5)
    assert f.state == "failed"


def test_tracks_a_walking_person_out_of_camera_view():
    f = Follower()
    f.start(0.0)
    f.update(pts([person_at(1.5, 0.0)]), AHEAD, 0.0)
    t, y = 0.0, 0.0
    for _ in range(30):                                       # walks left at 0.5 m/s
        t += 0.1
        y += 0.05
        f.update(pts([person_at(1.5, y), wall(4.0)]), [], t)  # no camera help
    assert f.state == "following"
    assert abs(math.degrees(f.bearing) - math.degrees(math.atan2(y, 1.35))) < 6


def test_lost_then_reacquired_then_gone():
    f = Follower()
    f.start(0.0)
    f.update(pts([person_at(1.5, 0.0)]), AHEAD, 0.0)
    for t in (0.5, 1.0, 2.1):
        f.update(pts([]), [], t)
    assert f.state == "lost"
    f.update(pts([person_at(1.6, 0.1)]), [], 2.5)             # back near the last spot
    assert f.state == "following"
    for t in (3.0, 5.0, 9.0):
        f.update(pts([]), [], t)
    assert f.state == "gone"


def test_does_not_jump_to_a_far_person():
    f = Follower()
    f.start(0.0)
    f.update(pts([person_at(1.5, 0.0)]), AHEAD, 0.0)
    f.update(pts([person_at(1.5, 1.5)]), [], 0.1)            # someone else, 1.5 m away
    assert abs(f.bearing) < math.radians(5)


# --- follow mode in the autonomy controller ------------------------------------

from navsim import MODEL, FakeClock  # noqa: E402
from robert.config import Settings  # noqa: E402
from robert.nav.autonomy import Autonomy  # noqa: E402
from robert.nav.guard import CollisionGuard  # noqa: E402
from robert.nav.scan import ScanView  # noqa: E402
from robert.robot.motion import MotionController  # noqa: E402
from robert.robot.state import LidarState, RobotState  # noqa: E402
from navsim import bins_for  # noqa: E402


class Rig:
    def __init__(self, boxes):
        self.clock = FakeClock()
        self.state = RobotState()
        self.state.lidar = LidarState(connected=True, bins=bins_for(boxes), last_scan=self.clock.t)
        view = ScanView(self.state.lidar, MODEL, 0.35, clock=self.clock)
        guard = CollisionGuard(MODEL, view, self.state.nav, 0.4, 80)
        self.motion = MotionController(self.state, 180, 300, clock=self.clock, guard=guard)
        self.vision_calls = []
        self.auto = Autonomy(Settings(), self.state, self.motion, view, MODEL, clock=self.clock,
                             on_follow=self.vision_calls.append)

    def see(self, boxes, people_deg=(0.0,)):
        self.clock.t += 0.1
        self.state.lidar.bins = bins_for(boxes)
        self.state.lidar.last_scan = self.clock.t
        self.state.vision.people = [{"bearing_deg": b, "half_width_deg": 8, "score": 0.9} for b in people_deg]
        self.state.vision.updated = self.clock.t
        self.auto.tick()
        return self.motion.current_target()


def test_follow_drives_towards_a_far_person_and_stops_near():
    rig = Rig([])
    rig.auto.start_follow("voice")
    assert rig.vision_calls == [True]
    rig.see([person_at(2.2, 0.0)])                     # acquire
    left, right = rig.see([person_at(2.2, 0.0)])
    assert left > 0 and right > 0                      # 2 m away: come closer
    x = 2.2
    while x > 1.05:                                    # they walk up to the robot
        x -= 0.1
        left, right = rig.see([person_at(x, 0.0)])
    assert (left, right) == (0, 0)                     # within 1 m: stand
    assert rig.state.nav.follow["state"] == "following"


def test_follow_turns_to_face_a_person_to_the_side():
    rig = Rig([])
    rig.auto.start_follow("pwa-1")
    rig.see([person_at(1.6, 0.0)])
    y = 0.0
    for _ in range(12):                                 # steps to the left, out of camera view
        y += 0.1
        left, right = rig.see([person_at(1.6, y)], people_deg=())
    assert right > left                                # turning left towards them


def test_follow_stops_when_nobody_or_lost_or_far():
    rig = Rig([])
    rig.auto.start_follow("voice")
    for _ in range(35):
        rig.see([], people_deg=())
    assert rig.state.motion.last_stop_reason == "follow_nobody_in_front"
    assert rig.vision_calls == [True, False] and not rig.auto.active

    rig = Rig([])
    rig.auto.start_follow("voice")
    rig.see([person_at(1.5, 0.0)])
    for _ in range(65):
        rig.see([], people_deg=())
    assert rig.state.motion.last_stop_reason == "follow_lost"

    rig = Rig([])
    rig.auto.start_follow("voice")
    rig.see([person_at(2.0, 0.0)])
    x = 2.0
    while rig.auto.active and x < 5:
        x += 0.04
        rig.see([person_at(x, 0.0)])
    assert rig.state.motion.last_stop_reason == "follow_too_far"


def test_follow_survives_the_pwa_closing_but_not_a_stop():
    rig = Rig([])
    rig.auto.start_follow("pwa-1")
    rig.see([person_at(2.0, 0.0)])
    rig.auto.release("pwa-1")                          # page closed: the person is the dead-man
    assert rig.auto.active
    rig.motion.stop("pwa-2_stop")
    assert not rig.auto.active and rig.vision_calls == [True, False]
