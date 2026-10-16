"""Read-only OpenYAM motor check over the gs_usb CAN adapter.

Asks each Damiao motor for its state (same connect + refresh path DimOS uses
before activation). Never enables torque or sends motion commands.

    .venv/bin/python tools/probe_motors.py [USB-SERIAL] [--watch SECONDS]

--watch prints all joint positions twice a second (still read-only), e.g. while
moving joints by hand with torque off.
"""

import sys
import time

import can_motor_control as cmc
from can_motor_control import damiao

# Same IDs and types as dimos/hardware/whole_body/openyam_damiao/adapter.py.
MOTORS = [
    ("joint1", damiao.MotorType.DM4340, 0x01, 0x11),
    ("joint2", damiao.MotorType.DM4340, 0x02, 0x12),
    ("joint3", damiao.MotorType.DM4340, 0x03, 0x13),
    ("joint4", damiao.MotorType.DM4310, 0x04, 0x14),
    ("joint5", damiao.MotorType.DM4310, 0x05, 0x15),
    ("joint6", damiao.MotorType.DM4310, 0x06, 0x16),
    ("gripper", damiao.MotorType.DM4310, 0x08, 0x18),
]

args = sys.argv[1:]
watch = 0.0
if "--watch" in args:
    i = args.index("--watch")
    watch = float(args[i + 1])
    del args[i:i + 2]
serial = args[0] if args else None
bus = cmc.GsUsbBus(vendor_id=0x1D50, product_id=0x606F, serial_number=serial,
                   index=None if serial else 0)
robot = (
    cmc.Robot.builder()
    .add_bus("can", bus, damiao.DamiaoCodec())
    .add_arm("arm", bus="can",
             motors=[cmc.MotorSpec(n, t, s, r) for n, t, s, r in MOTORS[:6]])
    .add_gripper("gripper", bus="can", motor=cmc.MotorSpec(*MOTORS[6]),
                 opening_direction="decreasing_position")
    .build()
)

robot.connect()
try:
    for _ in range(5):
        robot.refresh()
        robot.tick(5000)
        time.sleep(0.05)
    arm, gripper = robot["arm"], robot["gripper"]
    print(f"CAN frames sent={bus.tx_completed} received={bus.rx_received} dropped={bus.rx_dropped}")
    print(f"{'motor':8} {'id':>4} {'pos(rad)':>9} {'mos°C':>6} {'rotor°C':>8} {'enabled':>8} fault")
    for name, _, send_id, _ in MOTORS:
        m = gripper.motor if name == "gripper" else arm[name]
        print(f"{name:8} {send_id:#04x} {m.position:9.3f} {m.temperature_mos:6} "
              f"{m.temperature_rotor:8} {str(m.is_enabled):>8} {m.fault}")
    end = time.time() + watch
    while time.time() < end:
        robot.refresh()
        robot.tick(5000)
        motors = [gripper.motor if n == "gripper" else arm[n] for n, *_ in MOTORS]
        print(time.strftime("%H:%M:%S ") + " ".join(
            f"{n[-1] if n != 'gripper' else 'g'}={m.position:+.2f}"
            for (n, *_), m in zip(MOTORS, motors)), flush=True)
        time.sleep(0.5)
finally:
    for _ in range(5):  # disable must be flushed with tick() or it never leaves the queue
        robot.disable()
        robot.tick(5000)
        time.sleep(0.02)
