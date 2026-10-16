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
