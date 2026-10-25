"""Autonomous modes, driven at the scan rate through the normal motion path.

Explore only runs while an operator keeps it armed: the PWA re-sends
{"type":"auto","mode":"explore"} like a dead-man. Follow is armed once
(PWA button or voice) and the followed person is the dead-man: losing them,
them going beyond FOLLOW_MAX_M, or the time limit ends it. In both modes any
STOP, a manual drive or a blind LiDAR disarms, and commands go through
MotionController, so the dead-man, the collision guard and the ESP32 safety
layers apply exactly as for manual driving.
"""

import logging
import math
import time
from collections.abc import Callable

from ..config import Settings
from ..robot.motion import MotionController
from ..robot.state import RobotState
from .explore import Explorer
from .follow import Follower, Person
from .guard import BLIND_LIMIT_DEG
from .model import RobotModel
from .planner import ArcPlanner
from .scan import ScanView

log = logging.getLogger("robert.auto")

MODES = ("explore",)      # heartbeat-armed modes; "follow" is started with start_follow()
FOLLOW_TURN_RAD = math.radians(35)   # farther round than this: turn in place to face them
FOLLOW_FACE_RAD = math.radians(10)   # standing still: re-face them past this
FOLLOW_GAIN = 1.2                    # m/s of speed per metre of extra distance (keeps up jogging)
PEOPLE_MAX_AGE_S = 0.6
TICK_S = 0.1
PROGRESS_FRACTION = 0.3   # forward speed (of max) that counts as making progress


class Autonomy:
    def __init__(self, settings: Settings, state: RobotState, motion: MotionController,
                 scan: ScanView, model: RobotModel, clock: Callable[[], float] = time.monotonic,
                 on_follow: Callable[[bool], None] = lambda on: None):
        self._nav = state.nav
        self._vision = state.vision
        self._s = settings
        self._on_follow = on_follow
        self.follower = Follower()
        self._follow_started = 0.0
        self._motion = motion
        self._scan = scan
        self._model = model
        self._clock = clock
        self._max_pwm = settings.auto_max_speed
        self._v_max = settings.auto_max_speed * model.mps_per_pwm
        self._w_spin = 2 * settings.auto_spin_speed * model.mps_per_pwm / model.track_eff_m
        self._arm_timeout_s = settings.auto_arm_timeout_ms / 1000
        self._stuck_s = settings.auto_stuck_s
        self.planner = ArcPlanner(model)
        self.explorer = Explorer(model)
        self._owner: str | None = None
        self._armed_at = self._last_tick = self._progress_at = 0.0
        self._last_w = 0.0
        motion.on_stop(self.disarm)  # any stop, from anywhere, ends autonomy

    @property
    def active(self) -> bool:
        return self._owner is not None

    def arm(self, mode: str, owner: str) -> None:
        if mode not in MODES:
            return
        now = self._clock()
        if self._owner is None:
            log.info("autonomy %s armed by %s", mode, owner)
            self.explorer.reset()
            self._nav.stuck = False
            self._last_tick = self._progress_at = now
            self._last_w = 0.0
        self._owner = owner
        self._armed_at = now
        self._nav.auto = mode

    def start_follow(self, owner: str) -> None:
        """Arms follow-me once; the person found in front becomes the dead-man."""
        if self._nav.auto == "follow":
            return
        self.disarm()
        log.info("follow armed by %s", owner)
        now = self._clock()
        self._owner = owner
        self._nav.auto = "follow"
        self._nav.stuck = False
        self._follow_started = now
        self.follower.start(now)
        self._publish_follow()
        self._on_follow(True)

    def disarm(self) -> None:
        if self._owner is not None:
            log.info("autonomy disarmed")
        was_following = self._nav.auto == "follow"
        self._owner = None
        self._nav.auto = None
        self._nav.goal_deg = None
        if was_following:
            self.follower.stop()
            self._publish_follow()
            self._on_follow(False)

    def release(self, owner: str) -> None:
        """The arming client went away (follow does not depend on it: the person does)."""
        if self._owner == owner and self._nav.auto != "follow":
            self._motion.stop("auto_disconnected")

    def tick(self) -> None:
        if self._owner is None:
            return
        now = self._clock()
        if self._nav.auto != "follow" and now - self._armed_at > self._arm_timeout_s:
            self._motion.stop("auto_not_armed")
            return
        points = self._scan.points()
        if points is None:
            self._motion.stop("auto_lidar_blind")
            return
        if self._nav.auto == "follow":
            self._tick_follow(points, now)
            return

        turned = self._last_w * (now - self._last_tick)
        self._last_tick = now
        goal = self.explorer.update(points, turned)
        reverse_ok = self._scan.blind_deg(180) < BLIND_LIMIT_DEG
        plan = self.planner.choose(points, goal, self._v_max, self._w_spin, reverse_ok)
        self._nav.goal_deg = round(math.degrees(goal))

        if abs(plan.v) >= PROGRESS_FRACTION * self._v_max:  # backing out of a corner counts too
            self._progress_at = now
        elif now - self._progress_at > self._stuck_s:
            self._nav.stuck = True
            self._motion.stop("auto_stuck")
            return

        self._drive(plan.v, plan.w, self._max_pwm)

    def _drive(self, v: float, w: float, max_pwm: int) -> None:
        left, right = self._model.to_lr(v, w)
        peak = max(abs(left), abs(right))
        if peak > max_pwm:
            left, right = round(left * max_pwm / peak), round(right * max_pwm / peak)
        self._last_w = self._model.to_vw(left, right)[1]
        self._motion.drive(left, right, owner=f"auto:{self._nav.auto}", mode="auto")

    # --- follow me ---------------------------------------------------------------

    def _people(self, now: float) -> list[Person]:
        v = self._vision
        if v.updated is None or now - v.updated > PEOPLE_MAX_AGE_S:
            return []
        return [Person(math.radians(p["bearing_deg"]), math.radians(p["half_width_deg"]))
                for p in v.people]

    def _publish_follow(self) -> None:
        f = self.follower
        d, b = f.distance, f.bearing
        self._nav.follow = {"state": f.state,
                            "distance_m": None if d is None else round(d, 2),
                            "bearing_deg": None if b is None else round(math.degrees(b))}

    def _tick_follow(self, points, now: float) -> None:
        s, f = self._s, self.follower
        f.update(points, self._people(now), now)
        self._publish_follow()
        reason = None
        if f.state == "failed":
            reason = "follow_nobody_in_front"
        elif f.state == "gone":
            reason = "follow_lost"
        elif f.distance is not None and f.distance > s.follow_max_m:
            reason = "follow_too_far"
        elif now - self._follow_started > s.follow_timeout_s:
            reason = "follow_timeout"
        if reason:
            self._motion.stop(reason)
            self._nav.follow = {**self._nav.follow, "state": "stopped", "reason": reason}
            return
        if f.state != "following":  # searching, or lost and hoping to see them again
            self._motion.drive(0, 0, owner="auto:follow", mode="auto")
            return

        d, bearing = f.distance, f.bearing
        self._nav.goal_deg = round(math.degrees(bearing))
        v_max = s.follow_max_speed * self._model.mps_per_pwm
        v = 0.0 if d < s.follow_stop_m else min(v_max, max(0.0, FOLLOW_GAIN * (d - s.follow_distance_m)))
        if abs(bearing) > FOLLOW_TURN_RAD or (v == 0 and abs(bearing) > FOLLOW_FACE_RAD):
            w = math.copysign(min(self._w_spin, 1.5 * abs(bearing)), bearing)
            self._drive(0.0, w, s.follow_max_speed)
            return
        if v == 0:
            self._motion.drive(0, 0, owner="auto:follow", mode="auto")
            return
        reverse_ok = self._scan.blind_deg(180) < BLIND_LIMIT_DEG
        plan = self.planner.choose(points, bearing, v, self._w_spin, reverse_ok)
        self._drive(plan.v, plan.w, s.follow_max_speed)
