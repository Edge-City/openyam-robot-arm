"""armctl.fk: forward-kinematics table-clearance check (pinocchio on the yam URDF).

Same rule as experiments/2026-10-16-linux-real-arm/make_beat_dance.py: every sample keeps
the gripper tip >= MIN_TIP_Z above the base plane (the table is at or below 0) and every
link origin except the base and link1 >= MIN_LINK_Z. The URDF has no collision meshes, so
self-collision and cables are not modelled; human eyes are the backstop.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any, Callable, Iterable

URDF = Path.home() / ".local/share/dimos/repo/data/yam_description/i2rt/yam.urdf"
MIN_TIP_Z = 0.08   # m
MIN_LINK_Z = 0.03  # m


@lru_cache(maxsize=2)
def checker(urdf: str = str(URDF)) -> Callable[[Iterable[float]], tuple[float, tuple[float, str]]]:
    """-> check(q6) returning (tip z, (lowest link z, link name)). Raises if pinocchio/URDF are missing."""
    import pinocchio as pin
    m = pin.buildModelFromUrdf(urdf)
    d = m.createData()
    bodies = [(k, f.name) for k, f in enumerate(m.frames) if f.type == pin.FrameType.BODY and f.name != "base"]
    idx = [m.joints[m.getJointId(f"joint{i + 1}")].idx_q for i in range(6)]

    def check(q6):
        q = pin.neutral(m)
        for i, v in zip(idx, q6):
            q[i] = v
        pin.forwardKinematics(m, d, q)
        pin.updateFramePlacements(m, d)
        z = {n: float(d.oMf[k].translation[2]) for k, n in bodies}
        low = min((v, n) for n, v in z.items() if n != "link1")
        return z["gripper_tip"], low
    return check


def clearance(samples_q: list[list[float]], ts: list[float] | None = None) -> dict[str, Any]:
    """Check every sample. -> {available, ok, min_tip_z, min_link_z, lowest_link, t_min_tip, failures[...]}."""
    try:
        check = checker()
    except Exception as e:  # no pinocchio or no URDF: say so, don't pretend
        return {"available": False, "ok": None, "error": f"{type(e).__name__}: {e}"}
    best_tip, best_link, fails = (9.0, None), (9.0, "", None), []
    for k, q in enumerate(samples_q):
        tip, (lz, ln) = check(q)
        t = ts[k] if ts is not None else k
        if tip < best_tip[0]:
            best_tip = (tip, t)
        if lz < best_link[0]:
            best_link = (lz, ln, t)
        if tip < MIN_TIP_Z or lz < MIN_LINK_Z:
            fails.append({"t": t, "tip_z": round(tip, 4), "link": ln, "link_z": round(lz, 4)})
    return {"available": True, "ok": not fails, "min_tip_z": round(best_tip[0], 4), "t_min_tip": best_tip[1],
            "min_link_z": round(best_link[0], 4), "lowest_link": best_link[1],
            "limits": {"tip_z": MIN_TIP_Z, "link_z": MIN_LINK_Z}, "failures": fails[:20],
            "n_failures": len(fails)}
