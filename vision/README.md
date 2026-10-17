# vision/: camera calibration and ArUco tag poses

Groundwork for seeing the OpenYAM arm and objects on the table: printable targets, camera intrinsics from a ChArUco board, and 6-DoF poses of ArUco tags. Nothing here talks to the CAN bus or the arm.

## Environment

Uses the repo venv: `.venv/bin/python` (Python 3.12). It has both `opencv-python 5.0.0.93` and `opencv-contrib-python 4.14.0.94` installed. The two share the `cv2` package directory and the contrib build is the one actually on disk: `cv2.__version__` is `4.14.0`, and the modern ArUco API works (`cv2.aruco.ArucoDetector`, `CharucoBoard`, `CharucoDetector`, `CharucoBoard.matchImagePoints`). That's why no separate `vision/.venv` was needed. **Caveat:** reinstalling or upgrading `opencv-python` alone would overwrite `cv2` with a build that has no `aruco`. If `import cv2; cv2.aruco` ever fails, don't patch `.venv`. Create a separate env instead:

```
~/.local/bin/uv venv --python 3.12 vision/.venv
~/.local/bin/uv pip install --python vision/.venv/bin/python opencv-contrib-python numpy pillow
```

Other dependencies: numpy, Pillow (for writing the PDFs) and ffmpeg (optional, only for the `v4l2+ffmpeg:` source).

Run every script from the repo root, as `.venv/bin/python vision/<script>.py`. They import each other from `vision/`.

## Files

| File | What it does |
|---|---|
| `targets_spec.py` | Single place for the target definitions: board dictionary, size and squares, tag dictionary, sizes and roles (id 0 = table / world origin, id 1 = gripper, ids 2-9 = objects). |
| `make_targets.py` | Writes the printable A4 PDFs and PNGs (300 dpi) plus `charuco_board.json` and `tags.json` (exact printed sizes and tag centres) to `targets/`. |
| `sources.py` | Uniform frame grabbing (see the source specs below). Run it on its own to grab one frame or list the phone cameras. |
| `calibrate_intrinsics.py` | ChArUco intrinsic calibration, either live (capture on a keypress, or auto-capture of diverse poses) or from a folder. Writes `calib/<camera>.yaml` and prints quality guidance. |
| `detect_tags.py` | ArUco detection plus pose (`solvePnP`, `SOLVEPNP_IPPE_SQUARE`), as a CLI and as a library (`TagDetector`). It reports each tag in the camera frame and, when tag 0 is visible, in tag 0's (table) frame. |
| `selftest.py` | Renders the generated targets into synthetic photos from a known virtual camera, calibrates, detects tags, and checks the errors. |

## Sources (`--source`)

| Spec | Meaning |
|---|---|
| `/dev/video0` (or `v4l2:/dev/video0`, `0`) | The UVC camera through OpenCV V4L2, MJPG, `--size 1280x720`. It skips `--warmup 90` frames because auto-exposure takes about 3 s, and the first frames are dark. |
| `v4l2+ffmpeg:/dev/video0` | The same camera through ffmpeg MJPG passthrough (the same command the lab console uses). Keeps only the latest frame. |
| `dashboard` | The lab console's shared USB camera, `http://127.0.0.1:8090/camera.jpg`. **Use this when the console is running**: it holds `/dev/video0`, and opening the device directly then fails with "busy". |
| `phone:<name>` | Phone camera from `dashboard/phonecam.py`'s local API. The URL template is `http://127.0.0.1:8444/cam/{name}.jpg`; override it with `--url-template` or `$VISION_PHONE_URL`. List the cameras with `.venv/bin/python vision/sources.py --list-phones`. |
| `http://...jpg` | Any "latest frame" JPEG URL. It's polled, and identical frames are skipped. |
| a folder, a glob or an image file | Image files in sorted order. |

```
.venv/bin/python vision/sources.py phone:side --out /tmp/side.jpg
.venv/bin/python vision/sources.py --list-phones
```

## Printing (the human's job)

1. `.venv/bin/python vision/make_targets.py` (already run; the outputs are in `vision/targets/`).
2. Print **`targets/charuco_board_A4.pdf`** (A4 landscape) and **`targets/aruco_tags_A4.pdf`** (2 pages: 40 mm tags, then 25 mm tags) at **100% / "Actual size"**. Turn off "fit to page", "shrink to fit" and any scaling. Matte paper is best, because glossy paper causes glare.
3. **Measure the 50 mm check ruler on each sheet.** It must be 50.0 mm. If it isn't, measure the printed board square and the tag black edge with calipers, and pass the measured values (`--square-mm 29.8`, `--tag-size 39.7`). Design values at exactly 100%: board square **29.97 mm**, marker 21.98 mm, 7 x 5 squares, DICT_5X5_100. Tags: **39.96 mm** and **24.98 mm**, DICT_4X4_50 (the size is the outer edge of the black square).
4. **Mount the board flat** on stiff cardboard or foam board (spray glue, no bubbles). A bent board ruins the calibration. Keep the white margin around the board and around each tag.
5. Cut out the tags along the dashed lines, keeping the white margin. Use **one copy of each ID** in the scene. Tag 0 goes flat on the table (it defines the world/table frame), tag 1 goes on the gripper (rigidly, flat), and 2-9 go on objects.

## Calibrating a camera

Calibrate each camera at the resolution you'll use it at. For a phone that's the stream's size: lock the orientation, and don't zoom or switch lenses afterwards.

```
# live with a window: SPACE captures, a toggles auto-capture, q calibrates
.venv/bin/python vision/calibrate_intrinsics.py --camera side --source phone:side --auto
# headless auto-capture (25 diverse views), then calibrate
.venv/bin/python vision/calibrate_intrinsics.py --camera side --source phone:side --headless --views 25
# re-run from the saved captures
.venv/bin/python vision/calibrate_intrinsics.py --camera side --source vision/calib/captures/side
```

Auto-capture keeps a view only when at least 8 ChArUco corners are seen and the board pose differs from every kept view by more than 12° or 12% in position. Tips: 15-30 views; tilt the board 20-45° in different directions; vary the distance; cover the edges and corners of the image; hold still (motion blur hurts). The output `calib/<camera>.yaml` holds `camera_matrix`, `dist_coeffs` (k1 k2 p1 p2 k3), the image size, RMS reprojection error, number of views, coverage and per-view errors. Guidance: RMS < 0.3 px is excellent, < 0.6 good, > 1.0 means recapture. The script drops views worse than 3x the median once.

**The laptop USB camera is currently completely defocused** (LOG.md, a hardware problem), so don't calibrate it until its focus is fixed. Use the phones.

## Detecting tags

```
.venv/bin/python vision/detect_tags.py --calib vision/calib/side.yaml --source phone:side --tag-size 40
.venv/bin/python vision/detect_tags.py --calib vision/calib/side.yaml --source phone:side --sizes 0:40,1:25 --out /tmp/tags.jpg
.venv/bin/python vision/detect_tags.py --calib vision/calib/side.yaml --source phone:side --watch --json
```

Without `--calib` it guesses K (f = 0.9 x width). Detection still works then, but poses are rough. The tag frame: the origin is at the tag centre, x is to the right and y to the top of the printed tag, and z points out of the paper. Library use:

```python
from detect_tags import detector_from_calib
det = detector_from_calib("vision/calib/side.yaml", tag_size_m=0.040, sizes={1: 0.025})
tags = det.detect(frame)                  # [Tag(id, role, size, corners, rvec, tvec, R, reproj_px)]
in_table = det.relative_to(tags, 0)       # {id: 4x4 pose in tag 0's frame}
```

## Self-test

```
.venv/bin/python vision/selftest.py [--seed N] [--keep DIR]
```

Virtual camera: 1280x720, fx 920, fy 918, cx 652, cy 355, distortion (-0.12, 0.09, 0.0006, -0.0004, -0.02). Each run renders 20 board views and 12 views of each tag sheet, with supersampling, lens distortion, a lighting gradient, blur (σ 0.6 px), noise (σ 2.5) and JPEG compression. It runs the calibration CLI on the rendered folder, then detects the tags with the **recovered** calibration. Results on 2026-10-16 (seeds 1-3):

| Check | Result |
|---|---|
| fx, fy | within 0.04% (error at most 0.35 px) |
| cx, cy | within 1.0 px (at most 0.14% of the image size) |
| RMS reprojection | 0.06-0.09 px (one outlier view dropped automatically in 2 of 3 runs) |
| Pixel-to-ray model difference | at most 1.9 px (0.12°) in the inner 80% of the image, up to 3.5 px at the extreme corners, where the board never went (extrapolated) |
| Tag detection | 716 of 718 tags in view detected |
| Tag position (0.25-0.55 m away) | median 1.5-1.7 mm, 95th percentile at most 3.7 mm, max 7.7 mm |
| Tag rotation | median 0.15-0.17°, 95th percentile 0.4°, max 1.8° |
| Tag relative to tag 0 | median 0.6-0.8 mm, max 5.2 mm |

The live auto-capture loop (diversity gate, saving captures, calibration) was also exercised headless with a simulated live feed of the rendered views: 15 views, RMS 0.083 px, fx 920.2 vs 920.

These are best-case numbers: a real print, real lighting and focus will be worse. Expect a few mm.

## Real-camera check (2026-10-16)

- `/dev/video0` was held by the lab console's ffmpeg, so opening it directly fails cleanly with "busy... use 'dashboard'". The OpenCV build has V4L2 (`v4l/v4l2: YES`), but direct capture is **untested** while the console holds the device.
- `detect_tags.py --source dashboard` grabbed a 1280x720 frame and found 0 tags. As expected, the frame is completely defocused, but the pipeline runs end to end. The same went for `phone:iphone` (1280x720) and `phone:android` (720x1280): 0 tags, no crash.

## Next steps

1. **Calibrate the phone cameras** (`side`, `top`, ...) with the printed board. Fix the USB camera's focus, then calibrate it too.
2. **Camera to table:** with tag 0 flat on the table, `relative_to(tags, 0)` gives every object in table coordinates. That's enough for "where is the object on the table".
3. **Hand-eye (camera to robot base):** fix tag 1 rigidly to the gripper. Drive the arm through about 10-20 well-spread poses with armctl (`move_joints`, inside the soft envelope, in simulation first and then with the human's go-ahead), and at each pose record the forward-kinematics pose of the flange from armctl/DimOS (`T_base_gripper`) together with the camera's `T_cam_tag1`. For a static camera, solve `cv2.calibrateRobotWorldHandEye` (or `cv2.calibrateHandEye` with the inverted base poses) for `T_base_cam` and the unknown `T_gripper_tag`. Check it by predicting the tag's image position at held-out poses (mm-level residuals expected). With `T_base_cam` and tag 0, `T_base_table` follows.
4. **Objects for pick-and-place:** start with tagged objects (ids 2-9, with the grasp offset from the tag per object), giving positions in the base frame and then `reach`/`move_joints` targets that armctl checks against the envelope. Later: tagless detection (colour/depth segmentation or a learned detector) using the same calibrated cameras, with multi-camera triangulation (phones from 2 angles) for height.
5. Feed the detections to the lab console (an overlay on the camera feeds, and object markers in the 3D view).
