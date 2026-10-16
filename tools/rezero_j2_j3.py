"""Re-zero OpenYAM joints 2 and 3 at the folded rest pose.

Fixes the constant -2*pi offset on joints 2 and 3: in the folded pose they read
about -6.28 rad while the DimOS model (yam.urdf) expects 0 there.

    .venv/bin/python -I tools/rezero_j2_j3.py [USB-SERIAL | can0]          # dry run
    .venv/bin/python -I tools/rezero_j2_j3.py [USB-SERIAL | can0] --write  # set zero

Only motors 0x02 and 0x03 are on the bus object, so no other motor is touched.
Never enables torque. Refuses to write unless both joints sit still at the
folded pose with the known -2*pi offset. Re-run tools/probe_motors.py afterwards,
then power-cycle the 24 V supply and probe again to confirm the zero persists.
"""

import math
import os
import sys
import time

import can_motor_control as cmc
from can_motor_control import damiao

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # -I drops the script dir
from openyam_bus import bus_stats, open_bus, rx_count

TARGETS = [
    ("joint2", damiao.MotorType.DM4340, 0x02, 0x12),
    ("joint3", damiao.MotorType.DM4340, 0x03, 0x13),
]
EXPECTED = -2 * math.pi  # folded-pose reading with the offset
POSE_TOL = 0.05          # rad (~3 deg) from the folded pose
ZERO_TOL = 0.05          # rad: already zeroed
STILL_TOL = 0.05         # rad/s: arm must be at rest

args = sys.argv[1:]
write = "--write" in args
args = [a for a in args if a != "--write"]
serial = args[0] if args else None

bus = open_bus(serial)
robot = (
    cmc.Robot.builder()
    .add_bus("can", bus, damiao.DamiaoCodec())
    .add_arm("j23", bus="can", motors=[cmc.MotorSpec(*m) for m in TARGETS])
    .build()
)


def read(arm, rounds=5):
    for _ in range(rounds):
        robot.refresh()
        robot.tick(5000)
        time.sleep(0.05)
    return {n: arm[n] for n, *_ in TARGETS}


def show(motors, title):
    print(title)
    for n, m in motors.items():
        print(f"  {n}: pos={m.position:+.4f} rad  vel={m.velocity:+.3f}  "
              f"enabled={m.is_enabled}  fault={m.fault}")


robot.connect()
try:
    arm = robot["j23"]
    motors = read(arm)
    show(motors, "Before:")
    if rx_count(bus, serial) == 0:
        sys.exit("No replies from motors 0x02/0x03. Check 24 V power and CAN wiring.")

    if all(abs(m.position) < ZERO_TOL for m in motors.values()):
        print("Joints 2 and 3 already read ~0 at this pose. Nothing to do.")
        sys.exit(0)

    problems = []
    for n, m in motors.items():
        if m.fault is not None:
            problems.append(f"{n} reports fault {m.fault}")
        if m.is_enabled:
            problems.append(f"{n} is enabled (torque on)")
        if abs(m.velocity) > STILL_TOL:
            problems.append(f"{n} is moving ({m.velocity:+.3f} rad/s)")
        if abs(m.position - EXPECTED) > POSE_TOL:
            problems.append(f"{n} reads {m.position:+.4f}, not within {POSE_TOL} of "
                            f"{EXPECTED:+.4f}: arm is not in the folded rest pose")
    if problems:
        print("Refusing to set zero:")
        for p in problems:
            print("  - " + p)
        sys.exit(1)

    if not write:
        print("Dry run OK: arm is folded and still. Re-run with --write to set zero on 0x02 and 0x03.")
        sys.exit(0)

    print("Setting current position as zero on joint2 (0x02) and joint3 (0x03)...")
    arm.set_zero_all()
    robot.tick(5000)
    time.sleep(0.5)
    motors = read(arm, rounds=10)
    show(motors, "After:")
    ok = all(abs(m.position) < ZERO_TOL for m in motors.values())
    print("Zero set." if ok else "WARNING: readings are not ~0 after set-zero.")
    sys.exit(0 if ok else 1)
finally:
    robot.disable()  # no-op for disabled motors; guarantees torque stays off
