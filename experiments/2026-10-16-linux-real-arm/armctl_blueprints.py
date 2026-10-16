"""SIMULATION-ONLY launcher: OpenYAM coordinator with BOTH eef-twist jogging and a joint
trajectory task (so "go home" works inside a teleop session), plus the planner.

Why: the stock ``keyboard-teleop-openyam-planner`` in DimOS 0.0.14 fails at startup with
``ValueError: task 'eef_twist_arm' (type 'eef_twist'): this coordinator has no input port
'ee_twist_command'`` because it uses the plain ControlCoordinator. This composes the same
tasks on ``ArmTwistCoordinator`` (which adds the ``ee_twist_command`` port). The keyboard
window is left out (headless); twists can be published on ``/ee_twist_command``.

Priorities: eef_twist 10 < joint_trajectory 20, so a trajectory (home/ready) preempts jogging.
``IK_MARGIN`` env (default 0.1 rad) sets the eef-twist task's ``command_limit_margin``.

This file forces simulation: it sets SIMULATION=1, refuses if CAN_PORT is set, and checks the
adapter is ``mock_whole_body`` before starting. It also defaults to the private zenoh bus.

Run (repo root):
    ZENOH_SCOUT_ADDR=224.0.0.123:7449 .venv/bin/python -I \
        experiments/2026-10-16-linux-real-arm/armctl_blueprints.py
Stop with Ctrl-C.
"""

from __future__ import annotations

import os
import sys

os.environ["SIMULATION"] = "1"
if os.environ.get("CAN_PORT"):
    raise SystemExit("CAN_PORT is set; this launcher is simulation-only")
os.environ.setdefault("ZENOH_SCOUT_ADDR", "224.0.0.123:7449")
if os.environ["ZENOH_SCOUT_ADDR"].strip() in ("", "224.0.0.224:7446"):
    raise SystemExit("refusing the default zenoh bus")

from dimos.core.global_config import global_config  # noqa: E402

assert global_config.simulation and global_config.can_port is None, "not in simulation"

from dimos.control.coordinator import TaskConfig  # noqa: E402
from dimos.control.tasks.trajectory_task.trajectory_task import joint_trajectory_task  # noqa: E402
from dimos.core.coordination.blueprints import autoconnect  # noqa: E402
from dimos.core.coordination.module_coordinator import ModuleCoordinator  # noqa: E402
from dimos.robot.manipulators.common.blueprints import planner  # noqa: E402
from dimos.robot.manipulators.common.coordinators import ArmTwistCoordinator  # noqa: E402
from dimos.robot.manipulators.common.topics import EEF_TWIST_TASK_NAME  # noqa: E402
from dimos.robot.manipulators.openyam.config import (  # noqa: E402
    OPENYAM_ARM_JOINTS,
    OPENYAM_GRIPPER_JOINT,
    OPENYAM_HARDWARE_ID,
    OPENYAM_JOINTS,
    make_openyam_model_config,
    openyam_hardware,
)

hw = openyam_hardware()
assert hw.adapter_type == "mock_whole_body", f"adapter is {hw.adapter_type!r}, not the mock"
model = make_openyam_model_config()
ik_margin = float(os.environ.get("IK_MARGIN", "0.1"))

armctl_teleop_sim = autoconnect(
    planner(model=model),
    ArmTwistCoordinator.blueprint(
        instance_name="ControlCoordinator",
        hardware=[hw],
        tasks=[
            TaskConfig(
                name=EEF_TWIST_TASK_NAME,
                type="eef_twist",
                joint_names=list(OPENYAM_ARM_JOINTS),
                priority=10,
                params={
                    "robot_model": model,
                    "target_frame": "gripper_tip",
                    "command_limit_margin": ik_margin,
                    "max_joint_velocity_rad_s": 0.5,
                },
            ),
            TaskConfig(
                name=f"{OPENYAM_HARDWARE_ID}_gripper",
                type="gripper",
                joint_names=[OPENYAM_GRIPPER_JOINT],
                priority=20,
            ),
            joint_trajectory_task(OPENYAM_JOINTS, priority=20),
        ],
    ),
)

if __name__ == "__main__":
    print(f"sim={global_config.simulation!r} adapter={hw.adapter_type} "
          f"bus={os.environ['ZENOH_SCOUT_ADDR']} ik_margin={ik_margin}", flush=True)
    # build().loop() serves the Coordinator RPC (needed for Dimos.connect) and blocks
    # until Ctrl-C, then stops every module (plain Dimos().run() does not serve RPC).
    ModuleCoordinator.build(armctl_teleop_sim).loop()
    sys.exit(0)
