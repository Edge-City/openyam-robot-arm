# First real joint moves: done ✅

home → base +6° → **ready** → home, all through the soft-limit gate. Torque is off again (confirmed on the wire).

- [x] Coordinator started on a private bus
- [x] `jog j1 +0.1`, `ready`, `home`: smooth, no faults
- [x] Stopped and disabled; all 7 motors off
- [ ] Phones: try the **joystick** and **tilt** pages (links at the bottom of the camera page). They drive a **simulated** arm for now.
- [ ] Print the calibration board + tags (vision/targets/, 100% scale)

**Next on the real arm (with your go):** connect the armctl daemon to DimOS, so the phone joystick, tilt and playables (wave, nod, dance) can drive it inside the safe box.
