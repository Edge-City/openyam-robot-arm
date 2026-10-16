# Fold the wrist back to home

Torque is **off** on all 7 motors (confirmed on the wire). The arm is limp, so move it gently by hand.

<!-- highlight: joint4 -->

Only the wrist is off home now:

- [x] DimOS stopped, `disable_all.py` sent, all motors disabled
- [ ] Check every motor **LED is steady red**
- [ ] **Joint 4 (wrist pitch)** is at **+94°**, past its +90° limit. Pitch it back until the gripper is level with the forearm (bar on 0).
- [ ] **Joint 5 (wrist yaw)** is at **−80°**. Twist it back to straight (bar on 0).
- [ ] Joints 1, 2, 3 and 6 are already at about 0, so leave them.
- [ ] Tell Claude when every bar sits on its 0 mark.
