# Experiment: real OpenYAM arm from Linux

**Experimenters:** Naman (with Claude Code)  
**Started:** 2026-10-16  
**Status:** active

## Goal

Properly interface with the physical OpenYAM arm and have it do things:

1. Connect from the Ubuntu 24.04 laptop and read every motor (read-only).
2. Jog it safely (keyboard teleop) on Linux.
3. Move to joint targets programmatically (go home, scripted poses), which nobody has done yet.
4. Then: higher-level tasks (pick and place, planning, maybe an agent driving it).

## Background (how we got here)

From the repo history, all on 2026-10-16:

- `067b323` Repo created: README, simulation launch/stop scripts, macOS keyboard window fix for DimOS 0.0.14 (`pygame.error: x11 not available`).
- `59ff463` macOS fix works with plain `dimos --simulation run`; printable QR sign for the robot table.
- `f30b729` **First hardware session (macOS).** Findings:
  - The macOS keyboard fix is simulation-only, so `yammac/` was written: keyboard window in a subprocess, blueprint `yammac.keyboard-teleop-openyam`, works on hardware.
  - Joints 2 and 3 read about -2π at the folded pose, so DimOS refused to drive. Fixed with a Damiao set-zero on motors 2 and 3 (`tools/rezero_j2_j3.py`). Zero survives a power cycle.
  - Ctrl-C does not reliably drop torque (LEDs stay green). `tools/disable_all.py` forces it off. `is_enabled` reads `False` even when enabled, so trust the LEDs, not the flag.
  - Jogging to full forward stretch pushes joint 3 past its 3.14 rad limit and DimOS then ignores all keys.
  - Keyboard blueprint has no joint trajectory task, so no "go home". `openyam-planner-coordinator` + `Arm` SDK from `dimos shell` is the candidate, untested.
- `ca267f9` Merged as PR #1.

## Known hardware facts

- 6 joints + gripper. Damiao DM4340 on joints 1-3 (CAN 0x01-0x03), DM4310 on 4-6 (0x04-0x06), gripper DM4310 (send 0x08, reply 0x18). Replies on send ID + 0x10.
- Classical CAN, 1 Mbit/s. Adapter: MKS CANable V2.0, gs_usb/candleLight `1d50:606f`. Plug directly, not via hub or charge-only cable. Switches: 120 Ω on, BOOT off.
- Base cable: thin red to CANH, black to CANL, GND empty. Motor supply 24 V (round DC socket).
- Zero/home = folded rest pose (upper arm flat pointing back, forearm folded on top, gripper level, joints 2 and 3 on their stops). Every joint should read about 0 there.
- Joint 1: 0 when the folded arm faces model +x; positive is CCW from above; range -150° to +180°.
- LEDs (Damiao): green steady = enabled/holding, red steady = powered and torque off, red flashing = fault (power cycle clears it).
- Gripper: opening decreases motor position.

## Session routine (from the macOS session)

Start folded on the stops → probe → start DimOS → jog → return toward folded → hold the arm → Ctrl-C → `disable_all.py` → check every LED steady red.

## This laptop

- Ubuntu 24.04 LTS, Python 3.12.3, 35 GB RAM, 34 GB free disk. User is in `plugdev` and `sudo`.
- At start: no DimOS `.venv` in the repo, no `uv` on PATH. Adapter not plugged in yet.

## Open questions / things to watch on Linux

- **gs_usb kernel driver vs libusb.** On Linux the kernel's `gs_usb` driver will likely claim the adapter and create `can0` (SocketCAN). Our tools use `can_motor_control.GsUsbBus`, which talks to the device over libusb directly. Need to find out whether it detaches the kernel driver itself, or whether we should use SocketCAN (`ip link set can0 type can bitrate 1000000 up`) and what `dimos --can-port` expects on Linux.
- **USB permissions.** libusb access to `1d50:606f` may need a udev rule (e.g. `MODE="0660", GROUP="plugdev"`).
- Does the joint 2/3 zero still read ~0 (it should, it persisted across power cycles)?
- Keyboard teleop on Linux should work with the stock blueprint (X11/Wayland: check which session we're in; stock module forces `SDL_VIDEODRIVER=x11`, needs XWayland under Wayland).

## Log

### 2026-10-16

- Created branch `experiments` and the shared `experiments/` notebook. Read the repo history and set up this log. Checked the laptop: no DimOS install yet, adapter not connected. Waiting for the arm to be wired up.
- **Decision:** `CLAUDE.md` now holds the working rules (log as you go, tag entries, record environments, save scripts, safety). Pushing to `Edge-City/openyam-robot-arm` failed with 403 for GitHub user `Nhajela`, so the repo was forked to https://github.com/Nhajela/openyam-robot-arm and `experiments` was pushed there (remote `fork`).
- **Observation (hardware connected):** `lsusb` shows the CANable `1d50:606f` ("canable2 gs_usb", serial `003300453945501620303651`) and a camera `1bcf:2d4f` "USB CAMERA 4K" (Sunplus) at `/dev/video0` and `/dev/video1` (group `video`). The session runs Wayland.
- **Observation:** The kernel's `gs_usb` module is loaded and has bound the adapter as SocketCAN `can0` (state DOWN/STOPPED, clock 170 MHz). This is the expected Linux behaviour, unlike macOS where nothing claims it.
- **Problem:** The adapter's USB node `/dev/bus/usb/001/004` is `root:root 0664`, so `can_motor_control.GsUsbBus` (libusb) can't claim it as a normal user. Bringing up `can0` also needs root. `sudo` needs a password, so I can't do either.
- **Fix (pending the human):** `linux-sudo-setup.sh` in this folder installs the missing apt packages (`portaudio19-dev git-lfs libturbojpeg pre-commit ffmpeg`, plus `v4l-utils can-utils` for debugging), adds a udev rule giving `plugdev` (we're in it) access to `1d50:606f`, and applies the DimOS LCM sysctl buffers.
- **Decision:** Installed without the official installer (it stops at apt/sudo). Instead: uv in user space, `uv venv --python 3.12 .venv`, `uv pip install --torch-backend cpu "dimos[manipulation]==0.0.14"` (pinned to the version tested on the Mac), then `-e ./yammac`. Log: `logs/install-dimos.log`.
- **Open:** Once permissions are fixed, does libusb detach the kernel `gs_usb` driver by itself, or do we need to unbind it? Alternatively we could use SocketCAN `can0` directly, if DimOS and can_motor_control support it on Linux.
