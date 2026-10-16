import os, sys, time
sys.path.insert(0, "/home/naman/openyam-robot-arm/experiments/2026-10-16-linux-real-arm")
from armctl_sim import ArmCtl, _fmt, URDF_LIMITS, JOINTS
from dimos.core.transport_factory import make_transport
from dimos.msgs.geometry_msgs.TwistStamped import TwistStamped
arm = ArmCtl.connect(); tw = make_transport("/ee_twist_command", TwistStamped)
def twist(lin, ang, s):
    e = time.monotonic() + s; lo = {j: 9.0 for j in JOINTS}; hi = {j: -9.0 for j in JOINTS}
    while time.monotonic() < e:
        tw.publish(TwistStamped(linear=lin, angular=ang)); time.sleep(0.02)
        st = arm.state()
        for j in JOINTS: lo[j] = min(lo[j], st[j]); hi[j] = max(hi[j], st[j])
    return lo, hi
print("IK_MARGIN env of sim:", sys.argv[1], flush=True)
print("start", _fmt(arm.state()))
twist([0, 0, 0.05], [0, 0, 0], 3.0); print("twist +z 3 s from HOME ->", _fmt(arm.state()), flush=True)
arm.home() if any(abs(v) > 1e-3 for v in arm.state().values()) else None
arm.ready(); print("ready", _fmt(arm.state()))
for lin, ang, lab in [([0, 0, 0], [0, 0.6, 0], "pitch +0.6 rad/s 8 s"), ([0, 0, 0.08], [0, 0, 0], "+z 0.08 m/s 8 s"), ([-0.08, 0, 0], [0, 0, 0], "-x 0.08 m/s 8 s")]:
    lo, hi = twist(lin, ang, 8.0)
    print(lab, "->", _fmt(arm.state()))
    for j in JOINTS:
        ulo, uhi = URDF_LIMITS[j]
        print(f"    {j}: min {lo[j]:+.3f} (URDF lo {ulo:+.3f}, gap {lo[j]-ulo:.3f})  max {hi[j]:+.3f} (URDF hi {uhi:+.3f}, gap {uhi-hi[j]:.3f})")
    arm.ready()
arm.home(); print("home", _fmt(arm.state())); arm.close()
