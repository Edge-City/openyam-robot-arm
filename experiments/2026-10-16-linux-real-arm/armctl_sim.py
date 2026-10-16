"""armctl_sim: joint-space control of the OpenYAM arm through a running DimOS 0.0.14.

ONLY TESTED IN SIMULATION (mock_whole_body adapter). Not yet run against the real arm.

How it works
------------
A DimOS blueprint that contains a ``joint_trajectory`` task (``openyam-planner-coordinator``,
``coordinator-openyam``, ``keyboard-teleop-openyam-planner`` or ``armctl_blueprints``) runs
in one process. This module is a separate Python client: it finds that process's
``ControlCoordinator`` over DimOS RPC (zenoh by default in 0.0.14) and calls

* ``get_joint_positions()``             -> current joints (dict name -> rad)
* ``execute_trajectory(JointTrajectory)`` -> start a timed joint trajectory
* ``task_invoke("joint_trajectory", "get_status", {"t_now": None})`` -> progress
* ``cancel_trajectory()``               -> stop (holds the last commanded position)

Every target goes through a soft-limit box (URDF limits shrunk by ``margin``, default
0.1 rad, configurable per joint). Targets outside the box are REFUSED (``LimitError``),
never clamped. Every intermediate trajectory point is checked too. ``home()`` (all
zeros) is a special allowed target even though j2/j3 home sits on their lower limit
0: it is reached at ``home_speed`` (slower), and the box is only widened on j2/j3 down
to 0 for that move. A move that *starts* outside the box (e.g. leaving home) may only
travel monotonically from the start towards a target inside the box.

Bus isolation (safety)
----------------------
By default this module joins a PRIVATE zenoh discovery group (``ZENOH_SCOUT_ADDR =
224.0.0.123:7449``) so it can only see a simulation started with the same variable; a
real-arm ``dimos run`` on the default group (224.0.0.224:7446) is invisible to it. It
refuses to run on the default group.

Exact commands (from the repo root, two terminals)
--------------------------------------------------
1. Simulation (note --simulation, and NO --can-port)::

     ZENOH_SCOUT_ADDR=224.0.0.123:7449 .venv/bin/dimos --simulation --viewer none \
         run openyam-planner-coordinator

   or, for eef-twist + joint trajectory in one coordinator (no keyboard window)::

     ZENOH_SCOUT_ADDR=224.0.0.123:7449 .venv/bin/python -I \
         experiments/2026-10-16-linux-real-arm/armctl_blueprints.py

2. Demo (home -> ready -> home, limit refusals, jog)::

     .venv/bin/python -I experiments/2026-10-16-linux-real-arm/armctl_sim.py

Library use::

     from armctl_sim import ArmCtl
     arm = ArmCtl.connect()
     arm.state(); arm.ready(); arm.move_joints({"yam_joint1": 0.3}); arm.home()
"""

from __future__ import annotations

import math
import os
import sys
import time
from collections.abc import Mapping, Sequence

# --- bus isolation: must happen before any dimos import --------------------------
ISOLATED_SCOUT_ADDR = "224.0.0.123:7449"
DEFAULT_ZENOH_SCOUT_ADDRS = ("", "224.0.0.224:7446")
os.environ.setdefault("ZENOH_SCOUT_ADDR", ISOLATED_SCOUT_ADDR)
if os.environ["ZENOH_SCOUT_ADDR"].strip() in DEFAULT_ZENOH_SCOUT_ADDRS:
    raise SystemExit(
        "armctl_sim refuses the default zenoh bus (a real-arm DimOS may be on it). "
        f"Set ZENOH_SCOUT_ADDR={ISOLATED_SCOUT_ADDR} for both the sim and this client."
    )
os.environ.setdefault("DIMOS_TRANSPORT", "zenoh")

from dimos.msgs.trajectory_msgs.JointTrajectory import JointTrajectory  # noqa: E402
from dimos.msgs.trajectory_msgs.TrajectoryPoint import TrajectoryPoint  # noqa: E402
from dimos.msgs.trajectory_msgs.TrajectoryStatus import TrajectoryState  # noqa: E402
from dimos.porcelain.dimos import Dimos  # noqa: E402

JOINTS = [f"yam_joint{i}" for i in range(1, 7)]
# URDF limits (rad), yam_description/i2rt/yam.urdf. URDF velocity limit is 1 rad/s.
URDF_LIMITS: dict[str, tuple[float, float]] = {
    "yam_joint1": (-2.61799, 3.14159),
    "yam_joint2": (0.0, 3.66519),
    "yam_joint3": (0.0, 3.14159),
    "yam_joint4": (-1.69297, 1.5708),
    "yam_joint5": (-1.5708, 1.5708),
    "yam_joint6": (-2.0944, 2.0944),
}
HOME = {j: 0.0 for j in JOINTS}
POSES: dict[str, dict[str, float]] = {
    "home": HOME,
    "ready": {**HOME, "yam_joint2": 0.5, "yam_joint3": 0.8},
}
HARD_TOLERANCE = 0.05  # DimOS Damiao adapter latches a fault beyond limit + 0.05 rad


class LimitError(ValueError):
    """A target or trajectory point lies outside the soft-limit box. Nothing was sent."""


class MotionFailed(RuntimeError):
    """DimOS rejected, aborted or did not finish a trajectory."""


def _min_jerk(s: float) -> float:
    return 10 * s**3 - 15 * s**4 + 6 * s**5


class ArmCtl:
    def __init__(
        self,
        app: Dimos,
        *,
        margin: float | Mapping[str, float] = 0.1,
        speed: float = 0.3,
        home_speed: float = 0.15,
        rate_hz: float = 50.0,
    ) -> None:
        self._app = app
        self._coord = app.ControlCoordinator
        margins = {j: margin for j in JOINTS} if isinstance(margin, (int, float)) else {
            j: float(margin.get(j, 0.1)) for j in JOINTS
        }
        self.soft_limits = {
            j: (URDF_LIMITS[j][0] + margins[j], URDF_LIMITS[j][1] - margins[j]) for j in JOINTS
        }
        for j, (lo, hi) in self.soft_limits.items():
            if lo >= hi:
                raise ValueError(f"margin leaves no range for {j}")
        self.speed = speed  # peak joint speed, rad/s
        self.home_speed = home_speed
        self.rate_hz = rate_hz
        if speed > 1.0 or home_speed > 1.0:
            raise ValueError("speeds above the URDF 1 rad/s are not allowed")

    # ------------------------------------------------------------------ connect
    @classmethod
    def connect(cls, timeout: float = 5.0, **kwargs: object) -> ArmCtl:
        app = Dimos.connect(timeout=timeout)
        names = [d.rpc_name or d.class_name for d in app._source.list_module_descriptors()]  # type: ignore[union-attr]
        if "ControlCoordinator" not in names:
            raise RuntimeError(f"no ControlCoordinator on this bus; modules: {names}")
        arm = cls(app, **kwargs)  # type: ignore[arg-type]
        if "joint_trajectory" not in arm._coord.list_tasks():
            raise RuntimeError(
                "running blueprint has no joint_trajectory task (keyboard-teleop-openyam has "
                "none); use openyam-planner-coordinator or armctl_blueprints.py"
            )
        return arm

    def close(self) -> None:
        self._app.stop()  # closes the client only; the DimOS process keeps running

    # -------------------------------------------------------------------- state
    def state(self) -> dict[str, float]:
        pos = self._coord.get_joint_positions()
        return {j: float(pos[j]) for j in [*JOINTS, "arm/gripper"] if j in pos}

    def status(self):  # noqa: ANN201
        return self._coord.task_invoke("joint_trajectory", "get_status", {"t_now": None})

    # ------------------------------------------------------------------- limits
    def check_target(self, target: Mapping[str, float], *, allow_home: bool = False) -> None:
        for j, v in target.items():
            if j not in self.soft_limits:
                raise LimitError(f"unknown joint {j!r}")
            if not math.isfinite(v):
                raise LimitError(f"{j} target {v} is not finite")
            lo, hi = self.soft_limits[j]
            if allow_home and v == 0.0 and j in ("yam_joint2", "yam_joint3"):
                continue  # home sits on the URDF lower limit of j2/j3
            if not lo <= v <= hi:
                raise LimitError(
                    f"{j} target {v:+.3f} outside soft limits [{lo:+.3f}, {hi:+.3f}] "
                    f"(URDF {URDF_LIMITS[j]}, margin applied); refused, nothing sent"
                )

    def _move_box(self, start: Mapping[str, float], target: Mapping[str, float], allow_home: bool):
        """Per-joint interval every trajectory point must stay in."""
        box = {}
        for j in target:
            lo, hi = self.soft_limits[j]
            s, t = start[j], target[j]
            ulo, uhi = URDF_LIMITS[j]
            if not ulo - HARD_TOLERANCE <= s <= uhi + HARD_TOLERANCE:
                raise LimitError(f"{j} is at {s:+.3f}, outside URDF limits: recover by hand")
            if allow_home and t == 0.0 and j in ("yam_joint2", "yam_joint3"):
                lo = min(lo, 0.0)
            # leaving a start outside the box is allowed only towards the target
            box[j] = (min(lo, s, t), max(hi, s, t)) if not lo <= s <= hi else (lo, hi)
        return box

    # -------------------------------------------------------------------- moves
    def plan(
        self,
        target: Mapping[str, float],
        *,
        duration: float | None = None,
        speed: float | None = None,
        allow_home: bool = False,
    ) -> JointTrajectory:
        """Validate and build a min-jerk joint trajectory from the current state. Sends nothing."""
        self.check_target(target, allow_home=allow_home)
        start = self.state()
        names = [j for j in JOINTS if j in target]
        delta = max(abs(target[j] - start[j]) for j in names)
        v = speed or self.speed
        # min-jerk peak velocity = 1.875 * delta / T
        t_needed = 1.875 * delta / v
        T = max(duration or 0.0, t_needed, 0.5)
        n = max(2, int(math.ceil(T * self.rate_hz)) + 1)
        box = self._move_box(start, target, allow_home)
        points = []
        for k in range(n):
            s = _min_jerk(k / (n - 1))
            pos = [start[j] + (target[j] - start[j]) * s for j in names]
            for j, p in zip(names, pos):
                lo, hi = box[j]
                if not lo - 1e-9 <= p <= hi + 1e-9:
                    raise LimitError(f"point {k}: {j}={p:+.3f} leaves [{lo:+.3f}, {hi:+.3f}]")
            points.append(TrajectoryPoint(time_from_start=T * k / (n - 1), positions=pos))
        return JointTrajectory(joint_names=names, points=points)

    def move_joints(
        self,
        target: Mapping[str, float] | Sequence[float],
        *,
        duration: float | None = None,
        speed: float | None = None,
        wait: bool = True,
        tol: float = 0.01,
        allow_home: bool = False,
    ) -> dict[str, float]:
        if not isinstance(target, Mapping):
            if len(target) != 6:
                raise ValueError("give 6 joint values or a {joint: value} dict")
            target = dict(zip(JOINTS, (float(x) for x in target)))
        traj = self.plan(target, duration=duration, speed=speed, allow_home=allow_home)
        result = self._coord.execute_trajectory(traj)
        if result.status.name != "ACCEPTED":
            raise MotionFailed(f"execute_trajectory: {result.status.name} {result.message}")
        if not wait:
            return dict(target)
        deadline = time.monotonic() + traj.duration + 5.0
        while time.monotonic() < deadline:
            time.sleep(0.05)
            st = self.status()
            if st.state == TrajectoryState.COMPLETED:
                break
            if st.state in (TrajectoryState.ABORTED, TrajectoryState.FAULT):
                raise MotionFailed(f"trajectory {st.state.name}")
        else:
            self.stop()
            raise MotionFailed("trajectory did not complete in time; cancelled")
        now = self.state()
        err = max(abs(now[j] - target[j]) for j in target)
        if err > tol:
            raise MotionFailed(f"completed but max error {err:.4f} rad > {tol}")
        return now

    def go(self, name: str, **kw: object) -> dict[str, float]:
        return self.move_joints(POSES[name], allow_home=(name == "home"), **kw)  # type: ignore[arg-type]

    def home(self, **kw: object) -> dict[str, float]:
        kw.setdefault("speed", self.home_speed)
        return self.go("home", **kw)

    def ready(self, **kw: object) -> dict[str, float]:
        return self.go("ready", **kw)

    def jog(self, joint: str, delta: float, **kw: object) -> dict[str, float]:
        """Relative single-joint step; refused if the predicted end state leaves the box."""
        if abs(delta) > 0.5:
            raise LimitError("jog steps are limited to 0.5 rad")
        now = self.state()
        predicted = now[joint] + delta
        lo, hi = self.soft_limits[joint]
        if not lo <= predicted <= hi:
            raise LimitError(
                f"jog {joint} {delta:+.3f} would reach {predicted:+.3f}, outside "
                f"[{lo:+.3f}, {hi:+.3f}]; refused"
            )
        return self.move_joints({joint: predicted}, **kw)  # type: ignore[arg-type]

    def stop(self):  # noqa: ANN201
        return self._coord.cancel_trajectory()


# ------------------------------------------------------------------------- demo
def _fmt(st: Mapping[str, float]) -> str:
    return " ".join(f"{st[j]:+.3f}" for j in JOINTS)


def _demo() -> int:
    print(f"ZENOH_SCOUT_ADDR={os.environ['ZENOH_SCOUT_ADDR']}")
    arm = ArmCtl.connect()
    print("tasks:", arm._coord.list_tasks())
    print("soft limits:", {j: tuple(round(x, 3) for x in v) for j, v in arm.soft_limits.items()})
    print(f"{time.strftime('%H:%M:%S')} start   {_fmt(arm.state())}")
    for step in ("home", "ready", "home"):
        t0 = time.monotonic()
        st = arm.home() if step == "home" else arm.ready()
        print(f"{time.strftime('%H:%M:%S')} {step:7s} {_fmt(st)}  ({time.monotonic() - t0:.1f}s)")

    print("-- refusals (nothing is sent):")
    for label, fn in [
        ("j4=1.6 (URDF upper 1.571)", lambda: arm.move_joints({"yam_joint4": 1.6})),
        ("j4=1.5 (inside URDF, outside soft 1.471)", lambda: arm.move_joints({"yam_joint4": 1.5})),
        ("j3=-0.2", lambda: arm.move_joints({"yam_joint3": -0.2})),
        ("j2=0.05 (not home, below soft 0.1)", lambda: arm.move_joints({"yam_joint2": 0.05})),
    ]:
        try:
            fn()
            print(f"   {label}: NOT refused (bug)")
        except LimitError as e:
            print(f"   {label}: refused: {e}")

    arm.ready()
    st = arm.jog("yam_joint1", 0.3)
    print(f"{time.strftime('%H:%M:%S')} jog j1+ {_fmt(st)}")
    try:
        arm.jog("yam_joint5", 0.5)
        arm.jog("yam_joint5", 0.5)
        arm.jog("yam_joint5", 0.5)
        print("   jog j5 x3: NOT refused (bug)")
    except LimitError as e:
        print(f"   jog j5 third step: refused: {e}")
    print(f"{time.strftime('%H:%M:%S')} now     {_fmt(arm.state())}")
    st = arm.home()
    print(f"{time.strftime('%H:%M:%S')} home    {_fmt(st)}")
    arm.close()
    return 0


if __name__ == "__main__":
    sys.exit(_demo())
