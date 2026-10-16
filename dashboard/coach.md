# Bring the arm home (manual recovery)

The keyboard controller got stuck: the wrist (**joint 4**) is pinned near its +90° limit, so the IK solver fails and keys stop working. We'll switch the motors off and fold the arm by hand.

<!-- highlight: joint4 -->

- [ ] **Hold the arm** with one hand under the forearm and wrist. It will go limp.
- [ ] Press **Ctrl-C** in the tmux pane to stop DimOS.
- [ ] Tell Claude "stopped". Claude sends `disable_all.py`, and the **LEDs go steady red**.
- [ ] Fold by hand: wrist level (j4 → 0), forearm down on its stop (j3 → 0), upper arm flat back (j2 → 0), base facing forward (j1 → 0).
- [ ] Watch the joint bars on the left. Every joint should sit on its **0 mark** (the 3D ghost shows the target).
