# Experiments

A shared lab notebook for everyone working on the OpenYAM arm. The aim is to share what we learn and build great interfaces and interactions with the arm. Write down everything, including failures, so the next person doesn't repeat them.

## How to add an experiment

1. Make a folder `experiments/YYYY-MM-DD-<slug>/` with a `LOG.md` (copy the shape of an existing one).
2. At the top, list **Experimenters** (who ran it), **Goal** and **Status** (active / paused / done / abandoned).
3. Keep a dated running log: what you tried, the exact commands, what happened, what you learned. Put any scripts, recordings or photos next to the log.
4. Add a row to the table below.
5. Once something is a stable procedure, move it into the main README or `tools/`, and link to it from the log.

Safety rules carry across all experiments: start folded on the stops, probe before enabling, keep a hand ready to support the arm, run `tools/disable_all.py` after every stop, check that every LED is steady red.

## Index

| Experiment | Experimenters | Goal | Status |
|---|---|---|---|
| [2026-10-16-linux-real-arm](2026-10-16-linux-real-arm/LOG.md) | Naman (+ Claude) | Drive the real arm from the Ubuntu laptop and go beyond keyboard jogging | active |

## Ideas backlog

Interfaces and interactions worth trying. Add yours, and say who to talk to.

- **armctl wrapper on top of DimOS**: home(), ready(), named poses, slow joint moves, limit-aware jogging. Never leaves a soft-limit box inside the URDF limits. (Naman)
- Go-home / named poses from a script (`openyam-planner-coordinator` + `Arm` SDK).
- Natural-language control: an agent that turns "pick up the cup" into arm actions.
- Gamepad or phone teleop instead of the keyboard.
- Record and replay demonstrations (teach by moving the arm by hand with torque off, then play it back).
- Camera + pick and place.
- A live web dashboard: joint states, temperatures, faults, LED state.
