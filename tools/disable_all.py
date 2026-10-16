"""Send Damiao disable (torque off) to all 7 OpenYAM motors and flush it to the bus.

    .venv/bin/python -I tools/disable_all.py [USB-SERIAL]

The arm goes limp: support it first.
"""
import sys
import time

import can_motor_control as cmc
from can_motor_control import damiao

MOTORS = [
    ("joint1", damiao.MotorType.DM4340, 0x01, 0x11),
    ("joint2", damiao.MotorType.DM4340, 0x02, 0x12),
    ("joint3", damiao.MotorType.DM4340, 0x03, 0x13),
    ("joint4", damiao.MotorType.DM4310, 0x04, 0x14),
    ("joint5", damiao.MotorType.DM4310, 0x05, 0x15),
    ("joint6", damiao.MotorType.DM4310, 0x06, 0x16),
    ("gripper", damiao.MotorType.DM4310, 0x08, 0x18),
]
serial = sys.argv[1] if len(sys.argv) > 1 else None
bus = cmc.GsUsbBus(vendor_id=0x1D50, product_id=0x606F, serial_number=serial,
                   index=None if serial else 0)
robot = (
    cmc.Robot.builder()
    .add_bus("can", bus, damiao.DamiaoCodec())
    .add_arm("arm", bus="can", motors=[cmc.MotorSpec(*m) for m in MOTORS[:6]])
    .add_gripper("gripper", bus="can", motor=cmc.MotorSpec(*MOTORS[6]),
                 opening_direction="decreasing_position")
    .build()
)
robot.connect()
for _ in range(10):
    robot.disable()
    robot.tick(5000)
    time.sleep(0.05)
for _ in range(5):
    robot.refresh()
    robot.tick(5000)
    time.sleep(0.05)
arm, gripper = robot["arm"], robot["gripper"]
print(f"CAN frames sent={bus.tx_completed} received={bus.rx_received}")
for name, *_ in MOTORS:
    m = gripper.motor if name == "gripper" else arm[name]
    print(f"{name:8} pos={m.position:+.3f} enabled={m.is_enabled} fault={m.fault} "
          f"mos={m.temperature_mos}C rotor={m.temperature_rotor}C")
