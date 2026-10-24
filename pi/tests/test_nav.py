import math

import numpy as np

from navsim import MODEL, FakeClock, bins_for, scan_view, wall
from robert.config import Settings
from robert.nav import geometry
from robert.nav.autonomy import Autonomy
from robert.nav.explore import Explorer
from robert.nav.guard import CollisionGuard
from robert.nav.planner import ArcPlanner
from robert.nav.scan import clearances
from robert.robot.motion import MotionController
from robert.robot.state import NavState, RobotState

HL, HW = MODEL.half_safe  # 0.275, 0.25


def guard_for(boxes, **kw):
    view, lidar, clock = scan_view(boxes)
    nav = NavState()
    return CollisionGuard(MODEL, view, nav, slow_extra_m=0.4, blind_max_speed=80, **kw), nav, lidar, clock


# --- geometry ---------------------------------------------------------------

def test_free_travel_straight_to_wall():
    view, _, _ = scan_view([wall(1.0)])
    free = geometry.free_travel(view.points(), geometry.arc_poses(np.array([0.0]), np.array([1]), 2.0),
                                MODEL.half_safe)
    assert abs(free[0] - (1.0 - HL)) <= geometry.STEP_M


def test_free_travel_ignores_obstacles_behind_when_moving_forward():
    view, _, _ = scan_view([wall(-0.4)])
    free = geometry.free_travel(view.points(), geometry.arc_poses(np.array([0.0]), np.array([1]), 2.0),
                                MODEL.half_safe)
    assert free[0] == 2.0


def test_robot_can_back_away_from_something_inside_the_margin():
    view, _, _ = scan_view([wall(0.22)])  # body ends at 0.175: 4.5 cm gap < 10 cm margin
    pts = view.points()
    fwd, back = (geometry.free_travel(pts, geometry.arc_poses(np.array([0.0]), np.array([s]), 1.0),
                                      MODEL.half_safe)[0] for s in (1, -1))
    assert fwd == 0.0 and back == 1.0


def test_self_returns_inside_body_are_dropped():
    view, _, _ = scan_view([(-0.1, 0.1, -0.05, 0.05)])  # "mast" inside the body
    assert view.points().shape[1] == 0


# --- guard -------------------------------------------------------------------

def test_guard_passes_when_clear_and_blocks_only_towards_the_wall():
    g, nav, _, _ = guard_for([wall(0.40)])
    assert g.limit(100, 100, autonomous=False) == (0, 0)
    assert nav.guard == "blocked" and nav.limited_by.startswith("delante")
    assert g.limit(-100, -100, autonomous=False) == (-100, -100)
    assert g.limit(-80, 80, autonomous=False) == (-80, 80)  # spin is free


def test_guard_slows_in_between():
    g, nav, _, _ = guard_for([wall(1.0)])
    left, right = g.limit(150, 150, autonomous=False)
    assert 0 < left < 150 and left == right and nav.guard == "slow"
    g2, _, _, _ = guard_for([wall(3.0)])
    assert g2.limit(150, 150, autonomous=False) == (150, 150)


def test_guard_blocks_spin_with_something_at_the_corner():
    g, nav, _, _ = guard_for([(0.20, 0.30, 0.20, 0.30)])  # post just off the front-left corner
    assert g.limit(-80, 80, autonomous=False) == (0, 0)
    assert nav.limited_by.startswith("giro")


def test_guard_blind_caps_manual_and_stops_autonomous():
    g, nav, lidar, clock = guard_for([])
    clock.t += 1.0  # scan is now stale
    assert g.limit(150, -150, autonomous=False) == (80, -80)
    assert g.limit(150, 150, autonomous=True) == (0, 0)
    assert nav.guard == "blind"


def test_motion_controller_applies_guard():
    view, lidar, clock = scan_view([wall(0.40)])
    state = RobotState()
    state.lidar = lidar
    guard = CollisionGuard(MODEL, view, state.nav, 0.4, 80)
    motion = MotionController(state, 180, 300, clock=clock, guard=guard)
    motion.drive(120, 120, "pwa-1")
    assert motion.current_target() == (0, 0)
    assert state.motion.target_left == 120  # the request is kept, only the output is cut
    motion.drive(-120, -120, "pwa-1")
    assert motion.current_target() == (-120, -120)


def test_clearances():
    view, _, _ = scan_view([wall(1.0), (-0.1, 0.1, 0.5, 0.6)])
    c = clearances(view.points(), MODEL)
    assert abs(c["front"] - (1.0 - 0.175)) < 0.02
    assert abs(c["left"] - (0.5 - 0.15)) < 0.02
    assert c["back"] is None and c["right"] is None


# --- planner / explore ------------------------------------------------------

V_MAX, W_SPIN = 0.4, 1.8


def test_planner_goes_straight_in_open_space():
    view, _, _ = scan_view([])
    plan = ArcPlanner(MODEL).choose(view.points(), 0.0, V_MAX, W_SPIN)
    assert plan.v == V_MAX and abs(plan.w) < 1e-9


def test_planner_steers_into_the_open_side():
    # Wall ahead with an opening on the left.
    view, _, _ = scan_view([(0.8, 0.9, -3, 0.2), (0.8, 0.9, 1.4, 3)])
    plan = ArcPlanner(MODEL).choose(view.points(), math.radians(40), V_MAX, W_SPIN)
    assert plan.v > 0 and plan.w > 0


def test_planner_turns_in_place_when_boxed_in_front():
    view, _, _ = scan_view([wall(0.35), (-3, 3, 1.2, 1.3), (-3, 3, -1.3, -1.2)])
    plan = ArcPlanner(MODEL).choose(view.points(), 0.0, V_MAX, W_SPIN)
    assert plan.v == 0 and abs(plan.w) == W_SPIN


def test_planner_stops_when_nothing_is_possible():
    tight = [wall(0.22), wall(-0.22), (-1, 1, 0.20, 0.3), (-1, 1, -0.3, -0.20)]
    view, _, _ = scan_view(tight)
    plan = ArcPlanner(MODEL).choose(view.points(), 0.0, V_MAX, W_SPIN)
    assert plan.v == 0 and plan.w == 0


def test_explorer_picks_the_open_corridor():
    # Boxed in except for a corridor to the right (-90 deg).
    boxes = [wall(0.8), wall(-0.8), (-3, 3, 0.8, 0.9), (-3, -0.3, -0.9, -0.8), (0.3, 3, -0.9, -0.8)]
    view, _, _ = scan_view(boxes)
    assert abs(math.degrees(Explorer(MODEL).update(view.points(), 0.0)) + 90) <= 10


def test_explorer_sticks_with_its_choice_between_equal_gaps():
    # Symmetric corridors left and right; front and back closed.
    boxes = [wall(0.8), wall(-0.8), (-3, -0.3, 0.8, 0.9), (0.3, 3, 0.8, 0.9),
             (-3, -0.3, -0.9, -0.8), (0.3, 3, -0.9, -0.8)]
    view, _, _ = scan_view(boxes)
    ex = Explorer(MODEL)
    first = ex.update(view.points(), 0.0)
    assert abs(abs(math.degrees(first)) - 90) <= 10
    for _ in range(5):
        assert ex.update(view.points(), 0.0) == first


# --- autonomy ---------------------------------------------------------------

def make_autonomy(boxes):
    view, lidar, clock = scan_view(boxes)
    state = RobotState()
    state.lidar = lidar
    s = Settings()
    guard = CollisionGuard(MODEL, view, state.nav, 0.4, 80)
    motion = MotionController(state, 180, 300, clock=clock, guard=guard)
    auto = Autonomy(s, state, motion, view, MODEL, clock=clock)
    return auto, motion, state, clock, lidar


def test_autonomy_drives_only_while_armed():
    auto, motion, state, clock, lidar = make_autonomy([])
    auto.tick()
    assert state.motion.mode == "idle"
    auto.arm("explore", "pwa-1")
    auto.tick()
    assert state.motion.mode == "auto" and motion.current_target()[0] > 0
    clock.t += 0.5  # operator stopped re-arming
    lidar.last_scan = clock.t
    auto.tick()
    assert not auto.active and state.motion.last_stop_reason == "auto_not_armed"
    assert motion.current_target() == (0, 0)


def test_autonomy_disarms_on_stop_and_on_disconnect():
    auto, motion, state, _, _ = make_autonomy([])
    auto.arm("explore", "pwa-1")
    motion.stop("pwa-1_stop")
    assert not auto.active
    auto.arm("explore", "pwa-2")
    auto.release("pwa-9")
    assert auto.active
    auto.release("pwa-2")
    assert not auto.active and state.motion.last_stop_reason == "auto_disconnected"


def test_autonomy_stops_when_blind_or_stuck():
    auto, motion, state, clock, lidar = make_autonomy([])
    auto.arm("explore", "pwa-1")
    clock.t += 0.36  # scan goes stale
    auto.arm("explore", "pwa-1")
    auto.tick()
    assert state.motion.last_stop_reason == "auto_lidar_blind"

    tight = [wall(0.22), wall(-0.22), (-1, 1, 0.20, 0.3), (-1, 1, -0.3, -0.20)]
    auto, motion, state, clock, lidar = make_autonomy(tight)
    auto.arm("explore", "pwa-1")
    for _ in range(100):
        clock.t += 0.1
        lidar.last_scan = clock.t
        auto.arm("explore", "pwa-1")
        auto.tick()
        if not auto.active:
            break
    assert state.nav.stuck and state.motion.last_stop_reason == "auto_stuck"


# --- replay of real scans (tests/data/room.jsonl, robot standing in a room) ---

def test_real_scans_run_through_guard_planner_and_explorer():
    import json
    import pathlib
    import time

    scans = [json.loads(line) for line in
             (pathlib.Path(__file__).parent / "data" / "room.jsonl").read_text().splitlines()]
    view, lidar, clock = scan_view([])
    nav = NavState()
    guard = CollisionGuard(MODEL, view, nav, 0.4, 80)
    planner, explorer = ArcPlanner(MODEL), Explorer(MODEL)
    worst = 0.0
    for bins in scans:
        lidar.bins = bins
        t0 = time.perf_counter()
        pts = view.points()
        goal = explorer.update(pts, 0.0)
        plan = planner.choose(pts, goal, V_MAX, W_SPIN)
        guard.limit(*MODEL.to_lr(plan.v, plan.w), autonomous=True)
        worst = max(worst, time.perf_counter() - t0)
        assert pts.shape[1] > 100
        assert abs(plan.v) <= V_MAX and math.isfinite(plan.w)
    assert worst < 0.05  # one full nav tick; ~10x margin on the Pi 4 budget


def test_guard_observe_reports_clearance_without_driving():
    g, nav, lidar, clock = guard_for([wall(1.0)])
    g.observe()
    assert abs(nav.clearance["front"] - (1.0 - MODEL.half_body[0])) < 0.02
    clock.t += 1.0  # stale scan
    g.observe()
    assert nav.clearance == {}


# --- calibration, blind sectors, reversing ------------------------------------

def test_calib_offset_and_direction():
    from robert.nav.calib import direction_fix, nearest_bin, offset_fix

    bins = bins_for([(0.30, 0.40, -0.05, 0.05)])           # box straight ahead...
    rotated = bins[-20:] + bins[:-20]                        # ...seen 20 deg off by the sensor
    assert nearest_bin(rotated) in (19, 20, 21)
    off = offset_fix(nearest_bin(rotated), 0.0)
    assert abs(((off + 180) % 360) - 180 + 20) <= 1          # -20 deg
    left_ok = nearest_bin(bins_for([(-0.05, 0.05, 0.30, 0.40)]))  # box on the left
    assert direction_fix(left_ok, off, True) == (off % 360, True)
    mirrored = (360 - left_ok) % 360                          # a mirrored sensor shows it on the right
    assert direction_fix(mirrored, off, True) == ((-off) % 360, False)


def test_self_mask_keeps_only_what_moves_with_the_robot():
    from robert.nav.calib import sectors, self_mask

    shell = (-0.30, -0.18, -0.12, 0.12)                       # a hood behind the LiDAR
    rooms = [[wall(0.9), shell], [(-3, 3, 0.7, 0.8), shell], [(0.4, 3, -3, -0.6), shell]]
    mask = self_mask([bins_for(r) for r in rooms])
    runs = sectors(mask)
    assert len(runs) == 1 and 140 <= runs[0][0] <= 155 and 205 <= runs[0][1] <= 220


def test_mask_drops_self_returns_and_guard_refuses_blind_reverse():
    from robert.nav.calib import self_mask

    shell = (-0.30, -0.18, -0.12, 0.12)
    mask = self_mask([bins_for([shell, wall(1.5)])] * 3)
    view, lidar, clock = scan_view([shell, wall(1.5)])
    view.mask = mask
    view._bins = None
    assert clearances(view.points(), MODEL)["back"] is None   # the hood is gone
    nav = NavState()
    g = CollisionGuard(MODEL, view, nav, 0.4, 80)
    assert g.limit(-100, -100, autonomous=True) == (0, 0) and "sin visión" in nav.limited_by
    assert g.limit(-150, -150, autonomous=False) == (-80, -80)  # a person may, slowly
    assert g.limit(100, 100, autonomous=True) == (100, 100)    # forward is seen


def test_planner_backs_out_of_a_dead_end():
    # Boxed in front and on both sides, open behind: nothing forward fits, turning is blocked.
    # (test footprint incl. margin: +-0.275 x +-0.25 m)
    tight = [wall(0.30), (-0.1, 0.35, 0.26, 0.3), (-0.1, 0.35, -0.3, -0.26)]
    view, _, _ = scan_view(tight)
    p = ArcPlanner(MODEL)
    plan = p.choose(view.points(), 0.0, V_MAX, W_SPIN)
    assert plan.v < 0
    # It commits for a while even if forward would now be possible...
    view2, _, _ = scan_view([])
    assert p.choose(view2.points(), 0.0, V_MAX, W_SPIN).v < 0
    # ...but never backs up blind.
    p2 = ArcPlanner(MODEL)
    assert p2.choose(view.points(), 0.0, V_MAX, W_SPIN, reverse_ok=False).v == 0


def test_planner_does_not_reverse_when_forward_is_free():
    view, _, _ = scan_view([wall(-0.5)])
    assert ArcPlanner(MODEL).choose(view.points(), 0.0, V_MAX, W_SPIN).v > 0


def test_mask_file_follows_a_later_frame_change(tmp_path):
    import numpy as np

    from robert.nav.calib import load_mask, save_mask

    mask = np.zeros(360, dtype=bool)
    mask[150:211] = True                       # behind, recorded with offset 0, clockwise
    path = tmp_path / "mask.json"
    save_mask(mask, path, 0.0, True)
    assert (load_mask(path, 0.0, True) == mask).all()
    shifted = load_mask(path, 20.0, True)      # front recalibrated by +20 deg
    assert shifted[170] and shifted[230] and not shifted[150]
    mirrored = load_mask(path, 0.0, False)     # sensor turned out mirrored
    assert mirrored[150] and mirrored[210]     # symmetric about 180: same bins
