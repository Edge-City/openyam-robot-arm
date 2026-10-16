"""OpenYAM keyboard teleop with the macOS-safe keyboard module.

Same composition as dimos.robot.manipulators.openyam.blueprints.teleop
:keyboard_teleop_openyam; `--simulation` still selects the mock adapter and
omitting it still drives real hardware over CAN.
"""

from __future__ import annotations

from dimos.core.coordination.blueprints import autoconnect
from dimos.manipulation.manipulation_module import ManipulationModule
from dimos.robot.manipulators.common.coordinators import ArmTwistCoordinator
from dimos.robot.manipulators.openyam.blueprints.teleop import (
    _eef_twist_task,
    _gripper_task,
    _openyam_model,
)
from dimos.robot.manipulators.openyam.config import openyam_hardware

from yammac.teleop_module import SubprocessKeyboardTeleopModule

keyboard_teleop_openyam_mac = autoconnect(
    SubprocessKeyboardTeleopModule.blueprint(),
    ArmTwistCoordinator.blueprint(
        instance_name="ControlCoordinator",
        hardware=[openyam_hardware()],
        tasks=[
            _eef_twist_task(),
            _gripper_task(),
        ],
    ),
    ManipulationModule.blueprint(
        model=_openyam_model,
        visualization={"backend": "viser"},
    ),
)
