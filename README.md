# OpenYAM robot arm (Edge City India)

Setup notes and helper scripts for the Anvil Robotics **OpenYAM** arm at Edge City India, running on Dimensional's **DimOS** software.

- Interactive setup guide: https://claude.ai/artifact/CreHEQvaLaHGjhm7SYRQ4p
- DimOS install docs: https://docs.dimensional.org/installation/
- DimOS OpenYAM integration: https://docs.dimensional.org/capabilities/manipulation/openyam_integration/
- DimOS source: https://github.com/dimensionalOS/dimos
- Help: the DimOS Discord (https://discord.gg/dimos). Tule (`1tule`) offered a tested setup blueprint for our unit; ask him for it.

> **Safety first.** Every `dimos ... run` command **without** `--simulation` selects the real arm and switches the motors on. Use the simulation until the hardware session is planned.

## 1. What you need

- Ubuntu 24.04 (preferred) or macOS (Apple Silicon tested).
- About 16 GB RAM and at least 10 GB free disk. No GPU is needed for arm control.
- For the physical arm only: a gs_usb/candleLight-compatible USB-to-CAN adapter (USB ID `1d50:606f`), CAN wiring, motor power and correct bus termination. macOS needs no extra USB or CAN drivers.

## 2. Install DimOS

Clone this repo, then run the official installer **inside the repo folder** so it creates `.venv` here:

```sh
git clone https://github.com/Edge-City/openyam-robot-arm.git
cd openyam-robot-arm
curl -fsSL https://raw.githubusercontent.com/dimensionalOS/dimos/main/scripts/install.sh | bash -s -- --mode library --extras manipulation --project-dir "$PWD"
```

Tested with DimOS **0.0.14** (Python 3.12). `requirements-tested.txt` lists every package version from the working laptop install. If you installed DimOS somewhere else, point the scripts at it with `export OPENYAM_VENV=/path/to/that/.venv`.

### macOS only: keyboard window fix

On macOS the stock 0.0.14 keyboard window fails with `pygame.error: x11 not available`. Apply the local fix once after installing:

```sh
.venv/bin/python macos-keyboard-fix/apply.py
```

It only patches the exact 0.0.14 release file (checked by SHA-256) and refuses anything else. It runs the keyboard window in a child process using Cocoa, stops jogging when the window loses focus or input goes stale, and cleans up on shutdown. Details: `macos-keyboard-fix/macos-keyboard.patch`. Linux does not need it.

## 3. Run the virtual arm

Double-click `scripts/launch-simulation.command` (macOS), or run:

```sh
./scripts/launch-simulation.command
```

It runs exactly `dimos --simulation run keyboard-teleop-openyam` and takes a few seconds to start.

1. Open http://127.0.0.1:8095 to see the arm. It may look small at first; zoom toward the center of the grid.
2. Click the small **OpenYAM Keyboard Teleop** window to focus it, then:

| Keys | Motion |
|---|---|
| W / S | forward / back |
| A / D | left / right |
| Q / E | up / down |
| R / F | roll |
| T / G | pitch |
| Y / H | yaw |
| `[` / `]` | open / close gripper |

Release the key (or click away from the window) to stop moving. Esc closes the keyboard window.

**To stop:** Ctrl-C in the terminal, or double-click `scripts/stop-simulation.command`. The stop script only stops this repo's OpenYAM simulation.

Quick check before a demo: launch, click the keyboard window, hold D and watch the arm move in the browser. Restart the demo if the laptop went to sleep.

The simulation is an in-memory arm model with a 3D view. It does not model contact or gravity. Only keyboard jogging has been tested; planning and Execute workflows have not.

## 4. The physical arm (not yet tested)

Arm facts from the DimOS docs:

- 6 joints plus a gripper.
- Motors: joints 1-3 are Damiao DM4340 (CAN IDs 0x01-0x03), joints 4-6 are DM4310 (0x04-0x06), the gripper is a DM4310 (send 0x08, reply 0x18). Replies come back on send ID + 0x10.
- Classical CAN at 1 Mbit/s.
- Opening the gripper decreases the motor position.

Commands, once Tule has confirmed our configuration:

```sh
dimos hardware can list                      # read-only: prints the adapter's USB serial
dimos --can-port <USB-SERIAL> run coordinator-openyam
```

Before first activation:

- Keep the workspace clear and the emergency stop within reach.
- Startup can calibrate the gripper, so expect it to move.
- Deactivating drops torque. Support the arm before stopping it.

Not confirmed yet (no manual found): power supply voltage, what's in the box, mounting hardware, whether an e-stop is included, and reach. Check the label on the arm and the card in the box.

## Known warnings (harmless in the simulation)

- A Pink joint-limit warning at the zero home pose on startup.
- Stale model state for a moment before streams arrive.
- Collision-mesh display unavailable.
- On shutdown, a message about a missing `joint_trajectory` task (the keyboard blueprint doesn't have one).

## Repo layout

```
scripts/              launch, stop and environment scripts (simulation only)
macos-keyboard-fix/   macOS keyboard window patch, its helper, tests, and the original release file
requirements-tested.txt   package versions from the working install
```

`macos-keyboard-fix/test_keyboard_bridge.py` exercises the keyboard bridge's watchdog and cleanup without any robotics stack: `cd macos-keyboard-fix && python3 -m unittest test_keyboard_bridge`.

DimOS is Apache-2.0 licensed by Dimensional Inc. `macos-keyboard-fix/keyboard_teleop_module.release-0.0.14.py` is an unmodified copy of their release file, kept so the fix can verify what it patches.
