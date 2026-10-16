"""One armctl step at a time on the REAL arm, through a running DimOS coordinator.

Uses ArmCtl from armctl_sim.py (soft-limit box, every trajectory point checked,
min-jerk profile). Real-arm differences: slower default speeds and a looser arrival
tolerance (gravity on j2/j3 under the default gains).

Start the coordinator first, on the PRIVATE zenoh bus so nothing else can attach:

    ZENOH_SCOUT_ADDR=224.0.0.123:7449 .venv/bin/dimos --can-port can0 --viewer none run coordinator-openyam

Then, from the repo root, one step per command:

    .venv/bin/python -I experiments/2026-10-16-linux-real-arm/armctl_real.py state
    .venv/bin/python -I experiments/2026-10-16-linux-real-arm/armctl_real.py jog yam_joint1 0.1
    .venv/bin/python -I experiments/2026-10-16-linux-real-arm/armctl_real.py ready
    .venv/bin/python -I experiments/2026-10-16-linux-real-arm/armctl_real.py home
    .venv/bin/python -I experiments/2026-10-16-linux-real-arm/armctl_real.py stop

Every call appends to logs/armctl-real.log.
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))  # -I drops the script dir
from armctl_sim import JOINTS, ArmCtl, LimitError, MotionFailed  # noqa: E402

SPEED = 0.15       # rad/s peak, normal moves
HOME_SPEED = 0.1   # rad/s peak, final approach onto the j2/j3 stops
TOL = 0.1          # rad, arrival tolerance on hardware (PD sag: ~0.03 on j2/j3, ~0.06 on j4)
LOG = HERE / "logs" / "armctl-real.log"


def fmt(st: dict[str, float]) -> str:
    return " ".join(f"{j.replace('yam_joint', 'j')}={v:+.3f}" for j, v in st.items())


def log(line: str) -> None:
    stamp = time.strftime("%Y-%m-%d %H:%M:%S")
    with LOG.open("a") as f:
        f.write(f"{stamp} {line}\n")
    print(line)


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 2
    cmd, args = argv[0], argv[1:]
    arm = ArmCtl.connect(speed=SPEED, home_speed=HOME_SPEED)
    try:
        before = arm.state()
        log(f"[{os.environ['ZENOH_SCOUT_ADDR']}] {cmd} {' '.join(args)} | before {fmt(before)}")
        if cmd == "state":
            log(f"tasks {arm._coord.list_tasks()}")
            return 0
        if cmd == "stop":
            log(f"stop -> {arm.stop()}")
            return 0
        t0 = time.monotonic()
        if cmd == "jog":
            joint, delta = args[0], float(args[1])
            if joint not in JOINTS:
                raise LimitError(f"unknown joint {joint}; use one of {JOINTS}")
            after = arm.jog(joint, delta, tol=TOL)
        elif cmd == "ready":
            after = arm.ready(tol=TOL)
        elif cmd == "home":
            after = arm.home(tol=TOL)
        else:
            print(f"unknown command {cmd}")
            return 2
        log(f"{cmd} done in {time.monotonic() - t0:.1f}s | after {fmt(after)}")
        return 0
    except (LimitError, MotionFailed) as e:
        log(f"{cmd} REFUSED/FAILED: {e}")
        return 1
    finally:
        arm.close()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
