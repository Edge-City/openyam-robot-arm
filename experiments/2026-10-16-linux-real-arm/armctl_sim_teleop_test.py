"""SIMULATION-ONLY: eef-twist jogging + joint-trajectory "go home" in one session.

Needs armctl_blueprints.py running on the private bus. Steps: ready (trajectory) ->
publish +z / +x twists on /ee_twist_command -> home (trajectory preempts twist task).

Run: .venv/bin/python -I experiments/2026-10-16-linux-real-arm/armctl_sim_teleop_test.py
"""

from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from armctl_sim import ArmCtl, _fmt  # noqa: E402  (sets the private bus first)

from dimos.core.transport_factory import make_transport  # noqa: E402
from dimos.msgs.geometry_msgs.TwistStamped import TwistStamped  # noqa: E402


def p(*a: object) -> None:
    print(time.strftime("%H:%M:%S"), *a, flush=True)


arm = ArmCtl.connect()
p("tasks", arm._coord.list_tasks(), "active", arm._coord.get_active_tasks())
tw = make_transport("/ee_twist_command", TwistStamped)


def twist(lin: list[float], seconds: float) -> None:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        tw.publish(TwistStamped(linear=lin, angular=[0.0, 0.0, 0.0]))
        time.sleep(0.02)


p("start ", _fmt(arm.state()))
p("twist +z 0.03 m/s for 1.5 s from HOME")
twist([0.0, 0.0, 0.03], 1.5)
p("      ", _fmt(arm.state()))
p("ready ", _fmt(arm.ready()))
for lin, label in (([0.0, 0.0, 0.03], "+z"), ([0.03, 0.0, 0.0], "+x"), ([0.0, 0.03, 0.0], "+y")):
    twist(lin, 1.5)
    p(f"twist {label} 1.5 s", _fmt(arm.state()), "active", arm._coord.get_active_tasks())
time.sleep(0.5)
p("home (trajectory, prio 20) while twist task idle:")
p("home  ", _fmt(arm.home()))
p("ready again, then home WHILE twists keep streaming (preemption):")
arm.ready()
import threading  # noqa: E402

stop = threading.Event()
threading.Thread(
    target=lambda: [tw.publish(TwistStamped(linear=[0.0, 0.0, 0.03], angular=[0.0, 0.0, 0.0]))
                    or time.sleep(0.02) for _ in iter(lambda: stop.is_set(), True)],
    daemon=True,
).start()
try:
    p("home  ", _fmt(arm.home()))
except Exception as e:  # noqa: BLE001
    p("home during streaming twist failed:", type(e).__name__, e)
stop.set()
time.sleep(0.5)
p("final ", _fmt(arm.state()))
arm.close()
