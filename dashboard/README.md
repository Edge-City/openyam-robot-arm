# Lab console (dashboard)

A local web page for working at the OpenYAM arm: a "what to do now" coach panel (Claude writes `coach.md`, you tick its checkboxes), a live 3D view of the measured arm next to a ghost of the home pose, joint gauges against the URDF limits, motor states and temperatures, the USB camera, CAN bus status and the active experiment log.

## Run

```bash
.venv/bin/python dashboard/server.py            # http://127.0.0.1:8090
.venv/bin/python dashboard/server.py --host 0.0.0.0 --mode listen --log experiments/<exp>/LOG.md
```

Needs `can0` up (`experiments/2026-10-16-linux-real-arm/can0-up.sh`), ffmpeg for the camera, and DimOS's `yam_description` data (`--urdf-dir`, default `~/.local/share/dimos/repo/data/yam_description`). The page loads three.js, urdf-loader and marked from cdn.jsdelivr.net, so the browser needs internet.

Files: `coach.md` (instructions shown at the top), `inbox.md` (notes typed into "Note to Claude", timestamped), `static/` (frontend).

## Safety

The server can only ever transmit one kind of CAN frame: the Damiao read-only refresh request (`0x7FF  [id 00 CC 00 00 00 00 00]`). It never enables, disables, zeroes or commands a motor.

- **auto** mode polls all 7 motors at 10 Hz, but only while no other process is using the bus: if it sees motor command frames from anyone else (0x001-0x008, 0x100-0x108, non-refresh 0x7FF) in the last 1 s it goes silent and shows "external controller (DimOS?) detected". If another reader is already polling, it stays silent and decodes those replies.
- **listen-only** never transmits. Use it whenever DimOS is running if you want to be strict.

In both modes it decodes every motor reply on the bus, so it works as a passive monitor while DimOS drives the arm. Velocity and torque are approximate (assumed DM4340/DM4310 ranges); the gripper is shown as raw motor radians and its 3D finger mapping is a rough guess.
