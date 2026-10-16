# Working on the OpenYAM arm

Several people share this repo to learn the Anvil Robotics OpenYAM arm (DimOS software) and build interfaces and interactions with it. Work like a research experimenter: the notebook matters as much as the code.

## The notebook is the source of truth

- Every piece of work belongs to an experiment in `experiments/YYYY-MM-DD-<slug>/LOG.md` (index and ideas backlog in `experiments/README.md`). Start a new experiment for a new goal; otherwise append to the active one.
- Log as you go, not at the end. Each dated entry (with local time) records: what we tried, the exact command, the environment it ran in, what happened (paste the key output), and what it means.
- Tag entries so they can be scanned: **Observation** (what we saw), **Insight** (what we now believe and why), **Idea** (something to try), **Decision** (what we chose and why), **Problem** / **Fix**.
- Failures and dead ends are results. Write them down with the error text.
- Record the environment for anything that worked: machine and OS, Python and DimOS versions, adapter serial, git commit, and anything physical (arm pose, power, cabling). "It worked" without the environment is not reproducible.
- Save anything reusable as a script in the experiment folder (or `tools/` once stable) instead of leaving it in a terminal. Put long raw output in the experiment's `logs/` and link it.
- Put photos, camera frames and recordings next to the log and link them.
- When a finding is stable, promote it to the main README or `tools/` and link to it from the log. Add new interaction ideas to the backlog in `experiments/README.md`.

## Hardware safety (always)

- `dimos ... run` without `--simulation` switches the real motors on. Try new behaviour in `--simulation` first.
- Before enabling: arm folded on its stops, `tools/probe_motors.py` (read-only) shows every joint at about 0 and no faults.
- After every stop: support the arm, run `tools/disable_all.py`, confirm every motor LED is steady red. Ctrl-C alone does not reliably drop torque, and `is_enabled` is not trustworthy: trust the LEDs.
- Never jog to full stretch: joint 3 past 3.14 rad locks DimOS out.
- Ask the person at the arm before any command that enables torque or moves a motor, and say what will move.

## Git

- `origin` is `Edge-City/openyam-robot-arm`. If you cannot push there, push to your fork and open a PR.
