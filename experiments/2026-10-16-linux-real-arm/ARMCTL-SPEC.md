# armctl: instruction set and safety envelope for the OpenYAM

Status: **draft spec** (2026-10-16). Being prototyped in simulation (`armctl_sim.py`).

## Why

On the first Linux jog session, Cartesian keyboard teleop from the folded pose drove the wrist (j4) into its +90° limit, and the IK solver then failed on every key (see LOG.md, about 14:57). We want one layer that every interface goes through, and that makes it impossible to leave the joint range.

## Layers

```
interfaces   console · keyboard · phone · scripts · Claude (natural language)
                        │  only armctl instructions
armctl       instruction set + safety envelope
                        │
DimOS        ControlCoordinator 100 Hz, IK, joint trajectories
                        │
motors       Damiao DM4340/DM4310 on SocketCAN can0 (macOS: gs_usb)
```

## Instructions (v0)

| Instruction | Meaning |
|---|---|
| `state()` | joints (rad), temps, faults, torque state (from the reply status nibble, not `is_enabled`) |
| `home()` | slow joint move to all zeros; final approach to the j2/j3 limit at reduced speed |
| `ready()` | joint move to a pose clear of every limit (e.g. j2 0.5, j3 0.8, others 0); start teleop from here |
| `move_joints(target, speed)` | joint-space move |
| `nudge(joint, delta)` | small single-joint move |
| `reach(dx, dy, dz[, droll, dpitch, dyaw])` | Cartesian step; the IK solution is checked against the envelope before it's sent |
| `gripper(open \| close \| width)` | gripper |
| `save_pose(name)` / `goto(name)` | named poses (teach with torque off, replay) |
| `stop()` | hold the current position |
| `relax()` | torque off; warns to support the arm first |

## The gate (applied to every motion)

1. **Envelope:** soft limits = URDF limits shrunk by 0.1 rad per side (configurable per joint). Home (0 on j2/j3, which is on the hard limit) is a special allowed target, approached slowly.
2. **Plan, then check every point**, not just the goal.
3. **Speed cap** on every joint (start very low on hardware).
4. **Execute, then verify** that the arm reached the target within tolerance and time; otherwise `stop()` and report.
5. **Log** every instruction, decision and refusal.

Out-of-envelope requests are **refused with a reason** (e.g. `j4 target 1.62 > soft limit 1.47`). They are never clamped silently.

URDF limits (rad): j1 [−2.618, 3.142], j2 [0, 3.665], j3 [0, 3.142], j4 [−1.693, 1.571], j5 [−1.571, 1.571], j6 [−2.094, 2.094].

## Open questions

- Which DimOS blueprint and task gives joint trajectories alongside teleop (`keyboard-teleop-openyam-planner`?), and how a separate process calls it. Being researched.
- Can DimOS's own limits be tightened (Pink limit task margins, `JointLimits`), so the IK itself respects the soft box?
- Gripper range and direction (opening decreases position; full range not measured yet).

## Programs: many interfaces, one instruction layer (v0 design, 2026-10-16)

Naman's goal: people can turn on several **programs** (interfaces) and switch between them, and all of them become the same instructions. Examples: a phone **tilt** controller (accelerometer/gyro), a phone **joystick**, and a **language model** that turns English ("dance!") into a *playable* motion sequence.

```
programs   tilt · joystick · english→playable · keyboard · console buttons · saved playables · (anyone can add one)
              │  WebSocket JSON, the protocol below
armctl daemon  control lease · switching · deadman · safety gate · logging
              │
DimOS → motors
```

### Control arbitration
- Exactly **one program holds the control lease**. Others may connect, read state and preview, but they can't move the arm.
- Switching happens from the console (or by an `acquire` request that the operator approves). On a switch, armctl first stops (holds position), then grants the lease.
- The console always has an **emergency stop** that overrides any lease.

### Protocol (WebSocket `ws://<laptop>:8091/armctl`, JSON messages)
Client → armctl:
```json
{"op":"hello","program":"tilt","name":"Naman's iPhone"}
{"op":"acquire"}                     // ask for the lease
{"op":"release"}
{"op":"do","id":"42","cmd":"home"}   // discrete: home | ready | goto{name} | move_joints{q,speed} | nudge{joint,delta} | gripper{width|open|close} | stop | relax
{"op":"stream","v":{"x":0.02,"y":0,"z":0,"roll":0,"pitch":0,"yaw":0},"grip":0,"ttl_ms":200}   // velocity setpoint (Cartesian, or "qd":[...] for joints)
{"op":"playable.validate","playable":{...}}
{"op":"playable.play","name":"dance-1"}   // only after validation, plus operator confirmation in the console
```
armctl → clients: `{"op":"state",...}` at 20 Hz (joints, gripper, torque, lease holder, current program), `{"op":"ack"|"refused","id":"42","reason":"j4 target 1.62 > soft limit 1.47"}`, `{"op":"lease","holder":"tilt"}`.

### Streaming and deadman
- Streaming setpoints expire after `ttl_ms` (default 200 ms, max 500). With no fresh setpoint the arm decelerates to a stop. Phones that lock, background or lose Wi-Fi stop the arm.
- Every predicted next state is checked against the soft envelope. A step that would leave the envelope has that component zeroed and the client gets `refused` with the reason (jogging slides along the boundary instead of jamming into it).
- Speed caps apply on top of whatever the program asks for.

### Playables (motion sequences, "like a movie")
```json
{"name":"dance-1","author":"llm:claude","created":"2026-10-16T15:30:00+05:30",
 "space":"joint",                         // "joint" (rad) or "cartesian" (m/rad relative to a start pose)
 "start":"ready",                         // named pose the playable starts from (armctl moves there first)
 "keyframes":[{"t":0.0,"q":[0,0.5,0.8,0,0,0],"grip":0.0},
              {"t":1.5,"q":[0.4,0.6,0.9,0.3,0,0.5],"grip":0.5,"ease":"cubic"}],
 "loop":1, "speed":0.5}
```
- **Validation** (before anything moves): every keyframe *and* the interpolated path at 100 Hz inside the soft envelope; per-joint velocity and acceleration under caps; the start pose is reachable; total duration ≤ 60 s. The result is either `ok` or a list of precise violations.
- **Preview:** the console animates the playable as a ghost arm (and it can be run in `--simulation`) before it's armed.
- **Play** needs a human click in the console. Pause/stop at any time; stopping holds position.
- Playables are saved under `playables/` so they can be shared and replayed.

### English → playable (LLM program)
- Input: free text ("wave hello", "do a happy dance", "nod three times"). The model gets the joint names, soft limits, speed caps, named poses and the playable schema, and returns a playable JSON (structured output).
- The playable goes through the same validator. On violations, the errors go back to the model for up to N repair rounds, then preview, then human Play.
- The LLM never streams motion. It only authors playables. This keeps a slow, unpredictable model out of the real-time loop.

### Phone programs
- Served from the existing HTTPS phone server (secure context needed for motion sensors and the camera). iOS needs a user tap to grant `DeviceMotionEvent` permission.
- **Tilt:** device orientation → Cartesian velocity (pitch → x, roll → y, buttons for z and gripper), with a big **hold-to-move** deadman button. Calibrate "neutral" on press.
- **Joystick:** two on-screen sticks (xy plane and z/yaw) plus gripper slider; hold-to-move semantics are built into touch.
