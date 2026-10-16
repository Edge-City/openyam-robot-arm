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

It runs exactly `dimos --simulation run keyboard-teleop-openyam` and takes a few seconds to start. With the macOS fix applied, typing that command yourself (in the activated `.venv`) works too.

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

## TODO: real arm on macOS (first tested 2026-10-16)

The steps above are not enough to run the physical arm from a Mac. Found during the first hardware session. Ticked items are now in this repo; the rest still need work.

What worked on 2026-10-16 (support the arm whenever torque may drop):

```sh
uv pip install --python .venv/bin/python -e ./yammac   # once: macOS keyboard that also works on hardware
dimos hardware can list                                 # note the adapter's USB serial
.venv/bin/python -I tools/probe_motors.py <USB-SERIAL>  # read-only; folded pose should read ~0 on every joint
dimos --can-port <USB-SERIAL> run yammac.keyboard-teleop-openyam
# ...jog, return toward folded, hold the arm, Ctrl-C, then:
.venv/bin/python -I tools/disable_all.py <USB-SERIAL>   # force torque off; check every LED is steady red
```

- [x] **Keyboard teleop on the real arm.** `macos-keyboard-fix` is simulation-only: on hardware it raises `Local macOS keyboard bridge is simulation-only`, so there is no keyboard. `yammac/` (vendored here) runs the keyboard window in its own process and adds the blueprint `yammac.keyboard-teleop-openyam`, which works in simulation and on hardware. Still open: decide whether to keep two macOS keyboard fixes or merge them.
- [ ] **Wiring notes.** Plug the MKS CANable V2.0 (gs_usb `1d50:606f`) straight into the Mac; through a hub or a charge-only cable it is not detected. On the base cable, thin red wire to CANH, black to CANL, GND empty. Adapter switches: 120 Ω on, BOOT off. Motor supply is 24 V (round DC socket on the base cable).
- [x] **Read-only motor probe.** `tools/probe_motors.py` connects over gs_usb and reads all 7 motors (position, temperature, fault) without enabling torque. Run it before every start.
- [x] **Joint zero check.** In the folded rest pose (upper arm flat pointing back, forearm folded on top, gripper level, joints 2 and 3 on their stops) every joint must read about 0, which is the DimOS model's zero and home pose. Our unit read about -2π on joints 2 and 3, so DimOS refused to drive it. Fixed by a Damiao set-zero on motors 2 and 3 at the folded pose (`tools/rezero_j2_j3.py`, dry run by default). The zero survives a power cycle. Script added; the check is in the commands above.
- [ ] **Joint 1 orientation.** Joint 1 reads 0 when the folded arm faces model +x (gripper pointing forward). Positive is counterclockwise seen from above. The range is -150° to +180°, and beyond it DimOS refuses to drive.
- [x] **Stopping does not reliably remove torque.** After Ctrl-C, motors stayed enabled and holding (green LED). `tools/disable_all.py` sends Damiao disable to all 7 motors and flushes it to the bus. Run it after every stop. Support the arm first. Note that `can_motor_control` only sends queued commands on `robot.tick()`, and its `is_enabled` flag read `False` even while LEDs were green, so trust the LEDs.
- [ ] **LED meanings** (Damiao, per roboticscenter.ai wiki): green steady = enabled/holding, red steady = powered, torque off, red flashing = fault. A power cycle clears a fault.
- [ ] **Joint-limit lockout.** Keyboard jogging to full forward stretch pushed joint 3 past its 3.14 rad limit; DimOS then ignores every key. Recovery: hold the arm, stop DimOS, disable torque, fold by hand. Warn users not to jog to full stretch.
- [ ] **No "go home" in keyboard mode.** The keyboard blueprint has no joint trajectory task. Moving to a joint target (for example folded home, all zeros) needs `openyam-planner-coordinator` with the `Arm` SDK from `dimos shell`, which is untested on this arm. Test it in `--simulation` first, then on hardware.
- [x] **Session routine.** Start folded on the stops; probe; start; jog; return toward folded; hold the arm; stop; run `disable_all.py`; check all LEDs steady red.
- [ ] Update section 4 once the above is done (remove "not yet tested", drop the 24 V item from "Not confirmed yet").

## Known warnings (harmless in the simulation)

- A Pink joint-limit warning at the zero home pose on startup.
- Stale model state for a moment before streams arrive.
- Collision-mesh display unavailable.
- On shutdown, a message about a missing `joint_trajectory` task (the keyboard blueprint doesn't have one).

## Repo layout

```
scripts/              launch, stop and environment scripts (simulation only)
yammac/               macOS keyboard teleop package for simulation and the real arm
tools/                real-arm helpers: read-only probe, torque-off, joint 2/3 re-zero
macos-keyboard-fix/   macOS keyboard window patch, its helper, tests, and the original release file
sign/                 printable A4 robot-table sign with a QR code to this repo
requirements-tested.txt   package versions from the working install
```

**Robot-table sign:** print `sign/robot-table-sign.pdf` (A4). The QR code opens this repo, so the repo must be public for visitors to see it. To point it somewhere else: `uv run --with "qrcode[pil]" sign/make_sign.py <url>`.

`macos-keyboard-fix/test_keyboard_bridge.py` exercises the keyboard bridge's watchdog and cleanup without any robotics stack: `cd macos-keyboard-fix && python3 -m unittest test_keyboard_bridge`.

DimOS is Apache-2.0 licensed by Dimensional Inc. `macos-keyboard-fix/keyboard_teleop_module.release-0.0.14.py` is an unmodified copy of their release file, kept so the fix can verify what it patches.
