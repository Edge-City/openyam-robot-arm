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
- **Fix confirmed:** after `linux-sudo-setup.sh` the adapter node is `root:plugdev 0660` without replugging (`udevadm trigger` was enough). Sysctl `rmem_max=67108864` is applied. `v4l2-ctl` and `candump` are installed.
- **Observation (camera):** USB CAMERA 4K offers MJPG and YUYV at 640x480 up to 3840x2160 (also 2592x1944, 2048x1536, 1920x1080, 1280x720...). Its controls include `focus_automatic_continuous` (on by default) and `focus_absolute` 0-1023; pan, tilt and zoom return error 5 (not really supported). The first frame after opening is dark: auto-exposure needs about 3 s, so skip roughly 90 frames at 30 fps before grabbing (`media/2026-10-16-first-look.jpg` vs `-warm.jpg`).
- **Problem (camera):** every frame is completely defocused. `focus_sweep.sh` (manual focus 0 to 1023, contact sheet in `media/focus-sweep.jpg`) shows **no change at all** across the range. **Insight:** the focus control reaches nothing real, so the lens is probably fixed or manual (a twist ring), has a film or cap on it, or is pressed against something. Needs a human to look.
- **Done:** DimOS installed. `dimos 0.0.14`, `can-motor-control 0.0.8`, `yammac 0.1.0` (editable) in `.venv`, Python 3.12, CPU torch. Took about 8 min over Wi-Fi with no errors. Full log: `logs/install-dimos.log`.
- **Problem:** `tools/probe_motors.py` failed on Linux: `AttributeError: module 'can_motor_control' has no attribute 'GsUsbBus'`.
- **Insight (important for anyone on Linux):** the `can_motor_control` stubs show a platform split. **Linux has only `SocketCanBus(interface)`; macOS has only `GsUsbBus(...)`** (plus `list_gs_usb_devices` and the `rx_received`/`tx_completed` counters). DimOS does the same in `dimos/hardware/whole_body/damiao/adapter.py` (lines 126-145): on Linux the `--can-port` value is a SocketCAN interface name, default `can0`; on macOS it's the USB serial. So on Linux the kernel `gs_usb` driver claiming the adapter is correct, and the libusb udev rule from `linux-sudo-setup.sh` isn't needed (harmless). We just need `can0` up at 1 Mbit/s, which needs root.
- **Fix:** new `tools/openyam_bus.py`: `open_bus(port)` returns SocketCAN on Linux (default `can0`, with a clear error if the interface is down) or gs_usb on macOS. Bus counters come from `/sys/class/net/can0/statistics` on Linux. `probe_motors.py`, `disable_all.py` and `rezero_j2_j3.py` all use it, so the same commands work on both OSes; pass the serial on macOS and `can0` (or nothing) on Linux.
- **Next (needs sudo):** `can0-up.sh` brings `can0` up (bitrate 1000000, restart-ms 100 so bus-off recovers by itself, txqueuelen 1000 so bursts of 7-motor commands don't hit ENOBUFS) and installs a udev rule that does the same on every replug.
- **Idea:** the camera could watch the motor LEDs and confirm "all steady red" after `disable_all.py`, instead of a human checking. That needs working focus first.
- **Done:** `can0-up.sh` ran. `can0` is UP, ERROR-ACTIVE, bitrate 1000000, sample point 0.747 (gs_usb clock 170 MHz), restart-ms 100, qlen 1000, 0 errors.
- **Problem / Fix:** the tools failed under `python -I` with `ModuleNotFoundError: openyam_bus` because isolated mode leaves the script's directory off `sys.path`. Each tool now inserts its own directory explicitly.
- **Observation (first motor probe from Linux, about 14:25):** `logs/probe-1.log`. Environment: Ubuntu 24.04, `.venv` DimOS 0.0.14 / cmc 0.0.8, SocketCAN `can0`, 24 V on, torque off.
  - 35 frames sent, 70 received (2 per request), 0 dropped, 0 errors. **All 7 motors answer, no faults, all disabled**, MOS 26-29 °C, rotor 25-26 °C.
  - joints 2-6 read about 0 (j2 -0.001, j3 -0.005, j4 +0.026, j5 -0.058, j6 +0.012). **Insight:** the joint 2/3 re-zero from the Mac session survived travel and power cycles.
  - **joint1 = -3.092 rad (-177°)**, outside the DimOS range of -150° to +180°. The arm is folded, but the base is turned about half a turn from home (model +x). DimOS would refuse to drive.
  - gripper = -2.906 (opening decreases position, so it's partly open; startup calibration should handle it).
- **Decision:** don't re-zero joint 1. Its zero is defined by the model's +x direction, and a set-zero here would make "forward" point backwards. Instead, rotate the base by hand (torque off) **counterclockwise seen from above** (the positive direction) about 177° until joint1 reads about 0, watching `probe_motors.py can0 --watch`. Do NOT go clockwise: that heads further negative, away from the range.
- **Observation (camera):** still fully defocused at probe time (`media/2026-10-16-probe-time.jpg`).
- **Observation (CAN protocol, captured with `candump` during a probe, `logs/candump-probe.log`):** the read-only state request is ID `0x7FF` with `[motor_id, 0x00, 0xCC, 0, 0, 0, 0, 0]`. The reply comes on `0x10+id`: `b0=(state<<4)|id`, pos16=`b1b2`, vel12, torque12, `b6`=MOS °C, `b7`=rotor °C. Position = pos16/65535·25 − 12.5 rad; checked by hand, `0x6045` → −3.099 (joint 1), `0x7FFD` → −0.001 (joint 2). can_motor_control sends one round of 7 requests every 50 ms.
- **Observation (model limits, `~/.local/share/dimos/repo/data/yam_description/i2rt/yam.urdf`, fetched by DimOS via git-lfs on first use):** j1 [−2.618, 3.142], j2 [0, 3.665], j3 [0, 3.142], j4 [−1.693, 1.571], j5 [−1.571, 1.571], j6 [−2.094, 2.094] rad; finger prismatics [−0.04695, 0] m. **Insight:** home (all zeros) sits right on the lower limit of j2 and j3. Our j3 reads −0.005, *just* below 0, which probably explains the "Pink joint-limit warning at the zero home pose" in the README.
- **Decision:** build a local lab console (`dashboard/`): live joints from CAN (listen-only when DimOS is driving; it only ever sends the read-only refresh above), a 3D URDF model following the measured joints with a ghost of the home pose, the camera, this log, and a "what to do now" coach panel that Claude edits and the human ticks off. Built by a background agent while the human rotates joint 1.
- **Action:** started `probe_motors.py can0 --watch 300` in the human's tmux pane (`tmux send-keys`, with permission), logging to `logs/watch-j1-rotate.log`.
- **Done (14:21):** the human rotated the base by hand with torque off. joint1 went −3.10 → −1.55 (14:19:20–14:19:33, paused) → −1.34 → +0.06 (14:21:13–14:21:21). The arm now reads home: j1 +0.06, j2 −0.00, j3 −0.00, j4 +0.03, j5 −0.06, j6 +0.01, gripper −2.91. Full trace: `logs/watch-j1-rotate.log`, `logs/watch-joints.log`. **Insight:** increasing joint1 is the direction the human called counterclockwise from above, consistent with the Mac notes. The arm can arrive (after transport) up to about 180° off on joint 1, so check joint 1 at every session start; folded doesn't mean home.
- **Decision:** the lab console is moving to the visual style of the human's explainer page (`~/Downloads/How this robot arm works.html`): coach steps on the left, 3D arm on the right.
