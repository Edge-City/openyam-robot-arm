"""ChArUco intrinsic calibration -> vision/calib/<camera>.yaml

Live (window): show the board to the camera; SPACE/c captures a view, a toggles
auto-capture (captures when enough corners are seen AND the board pose differs
from every view kept so far), q / ESC calibrates and quits.

  .venv/bin/python vision/calibrate_intrinsics.py --camera usbcam --source /dev/video0
  .venv/bin/python vision/calibrate_intrinsics.py --camera side --source phone:side --auto
  .venv/bin/python vision/calibrate_intrinsics.py --camera side --source phone:side --auto --headless --views 25
  .venv/bin/python vision/calibrate_intrinsics.py --camera usbcam --source vision/calib/captures/usbcam

Captured frames are kept in vision/calib/captures/<camera>/ so a calibration can
be re-run from the folder. If the printed square is not exactly the size in
targets/charuco_board.json, pass --square-mm <measured>.
"""
from __future__ import annotations

import argparse
import datetime as dt
import sys
import time
from pathlib import Path

import cv2
import numpy as np

from sources import ImageFiles, source_args, source_from_args
from targets_spec import CALIB_DIR, load_board

MIN_CORNERS = 8


def charuco_detector(board):
    p = cv2.aruco.CharucoParameters()
    p.minMarkers = 1
    dp = cv2.aruco.DetectorParameters()
    dp.cornerRefinementMethod = cv2.aruco.CORNER_REFINE_NONE  # ChArUco corners are refined separately
    return cv2.aruco.CharucoDetector(board, p, dp)


def detect_board(detector, board, img):
    """Returns (obj_pts Nx3, img_pts Nx2, charuco_corners, charuco_ids) or None."""
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.ndim == 3 else img
    cc, ci, mc, mi = detector.detectBoard(gray)
    if ci is None or len(ci) < 4:
        return None
    obj, imgp = board.matchImagePoints(cc, ci)
    if obj is None or len(obj) < 4:
        return None
    return obj.reshape(-1, 3).astype(np.float32), imgp.reshape(-1, 2).astype(np.float32), cc, ci


def rough_pose(obj, imgp, size):
    """Board pose with a guessed K, only used to judge view diversity."""
    w, h = size
    K = np.array([[0.9 * w, 0, w / 2], [0, 0.9 * w, h / 2], [0, 0, 1]], float)
    ok, rvec, tvec = cv2.solvePnP(obj, imgp, K, None)
    return (rvec.ravel(), tvec.ravel()) if ok else None


class DiversityGate:
    """Accepts a view only if its pose differs enough from all kept views."""

    def __init__(self, min_angle_deg=12.0, min_shift=0.12):
        self.poses = []
        self.min_angle, self.min_shift = min_angle_deg, min_shift

    def novel(self, pose) -> bool:
        if pose is None:
            return False
        rv, tv = pose
        R = cv2.Rodrigues(rv)[0]
        for rv2, tv2 in self.poses:
            R2 = cv2.Rodrigues(rv2)[0]
            ang = np.degrees(np.linalg.norm(cv2.Rodrigues(R.T @ R2)[0]))
            shift = np.linalg.norm(tv - tv2) / max(np.linalg.norm(tv2), 1e-6)
            if ang < self.min_angle and shift < self.min_shift:
                return False
        return True

    def add(self, pose):
        self.poses.append(pose)


def coverage(img_pts_list, size, grid=(4, 3)):
    """Fraction of image grid cells that contain at least one corner."""
    w, h = size
    hit = np.zeros(grid[::-1], bool)
    for p in img_pts_list:
        gx = np.clip((p[:, 0] / w * grid[0]).astype(int), 0, grid[0] - 1)
        gy = np.clip((p[:, 1] / h * grid[1]).astype(int), 0, grid[1] - 1)
        hit[gy, gx] = True
    return hit.mean()


def calibrate(views, size, flags=0, reject_factor=3.0):
    """views: list of (obj Nx3, img Nx2). Returns a result dict.
    One pass of outlier rejection drops views whose RMS > reject_factor x median."""
    def run(vs):
        obj = [v[0] for v in vs]
        imgp = [v[1] for v in vs]
        rms, K, dist, rvecs, tvecs, std_in, _, per_view = cv2.calibrateCameraExtended(
            obj, imgp, size, None, None, flags=flags)
        return rms, K, dist, rvecs, tvecs, std_in.ravel(), per_view.ravel()

    if len(views) < 4:
        raise ValueError(f"need at least 4 views, have {len(views)}")
    rms, K, dist, rvecs, tvecs, std, per_view = run(views)
    dropped = []
    med = float(np.median(per_view))
    bad = [i for i, e in enumerate(per_view) if e > reject_factor * med and e > 0.5]
    if bad and len(views) - len(bad) >= 6:
        dropped = bad
        views = [v for i, v in enumerate(views) if i not in bad]
        rms, K, dist, rvecs, tvecs, std, per_view = run(views)
    return {"rms": float(rms), "K": K, "dist": dist.ravel(), "rvecs": rvecs, "tvecs": tvecs,
            "std_intrinsics": std, "per_view_rms": per_view, "n_views": len(views), "dropped": dropped,
            "image_size": tuple(size), "coverage": coverage([v[1] for v in views], size),
            "n_corners": int(sum(len(v[0]) for v in views))}


def guidance(res) -> list[str]:
    out = []
    w, h = res["image_size"]
    K = res["K"]
    rms, n = res["rms"], res["n_views"]
    out.append(f"RMS reprojection error {rms:.3f} px: " + (
        "excellent" if rms < 0.3 else "good" if rms < 0.6 else "usable" if rms < 1.0 else
        "POOR (blur, a bent board, or the wrong --square-mm; recapture)"))
    if n < 12:
        out.append(f"only {n} views: aim for 15-30 with the board tilted 20-45 deg in different directions")
    cov = res["coverage"]
    if cov < 0.8:
        out.append(f"corners covered {cov:.0%} of the image (4x3 grid): also show the board near the edges and corners")
    sfx, sfy, scx, scy = res["std_intrinsics"][:4]
    out.append(f"fx {K[0,0]:.1f} +/- {sfx:.1f}, fy {K[1,1]:.1f} +/- {sfy:.1f}, "
               f"cx {K[0,2]:.1f} +/- {scx:.1f}, cy {K[1,2]:.1f} +/- {scy:.1f} px")
    if sfx / K[0, 0] > 0.01:
        out.append("focal length uncertain (>1%): add more tilted views and views at different distances")
    if abs(K[0, 0] / K[1, 1] - 1) > 0.02:
        out.append(f"fx/fy = {K[0,0]/K[1,1]:.3f}: unusual for a phone/webcam, suspect bad views")
    if abs(K[0, 2] - w / 2) > 0.1 * w or abs(K[1, 2] - h / 2) > 0.1 * h:
        out.append("principal point is far from the image centre: suspect bad views or a cropped stream")
    hfov = np.degrees(2 * np.arctan(w / 2 / K[0, 0]))
    out.append(f"horizontal field of view {hfov:.1f} deg")
    if res["dropped"]:
        out.append(f"dropped {len(res['dropped'])} outlier view(s): {res['dropped']}")
    return out


def save_yaml(path: Path, res, camera: str, board_info: dict, source: str = ""):
    path.parent.mkdir(parents=True, exist_ok=True)
    fs = cv2.FileStorage(str(path), cv2.FILE_STORAGE_WRITE)
    fs.write("camera_name", camera)
    fs.write("calibrated_at", dt.datetime.now().isoformat(timespec="seconds"))
    fs.write("source", source)
    fs.write("image_width", int(res["image_size"][0]))
    fs.write("image_height", int(res["image_size"][1]))
    fs.write("camera_matrix", res["K"])
    fs.write("dist_coeffs", res["dist"].reshape(1, -1))
    fs.write("rms_reprojection_error_px", res["rms"])
    fs.write("n_views", int(res["n_views"]))
    fs.write("n_corners", int(res["n_corners"]))
    fs.write("image_coverage", float(res["coverage"]))
    fs.write("std_fx_fy_cx_cy", np.asarray(res["std_intrinsics"][:4], float).reshape(1, -1))
    fs.write("per_view_rms", np.asarray(res["per_view_rms"], float).reshape(1, -1))
    fs.write("board", f"ChArUco {board_info['dict']} {board_info['squares'][0]}x{board_info['squares'][1]} "
                      f"square {board_info['square_mm']} mm marker {board_info['marker_mm']} mm")
    fs.release()


def load_calibration(path):
    fs = cv2.FileStorage(str(path), cv2.FILE_STORAGE_READ)
    if not fs.isOpened():
        raise FileNotFoundError(path)
    K = fs.getNode("camera_matrix").mat()
    dist = fs.getNode("dist_coeffs").mat().ravel()
    size = (int(fs.getNode("image_width").real()), int(fs.getNode("image_height").real()))
    fs.release()
    return K, dist, size


def draw(img, det, n_views, auto, msg=""):
    vis = img.copy()
    if det is not None:
        cv2.aruco.drawDetectedCornersCharuco(vis, det[2], det[3], (0, 255, 0))
    n = 0 if det is None else len(det[0])
    txt = f"views {n_views}  corners {n}  auto {'ON' if auto else 'off'}   SPACE capture  a auto  q done  {msg}"
    cv2.putText(vis, txt, (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 0), 4, cv2.LINE_AA)
    cv2.putText(vis, txt, (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 255), 2, cv2.LINE_AA)
    return vis


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    source_args(ap)
    ap.add_argument("--camera", required=True, help="name for the output, e.g. usbcam, side, top")
    ap.add_argument("--board", default=None, help="board json (default vision/targets/charuco_board.json)")
    ap.add_argument("--square-mm", type=float, default=None, help="measured printed square size")
    ap.add_argument("--marker-mm", type=float, default=None, help="measured printed marker size")
    ap.add_argument("--auto", action="store_true", help="start with auto-capture on")
    ap.add_argument("--headless", action="store_true", help="no window (implies --auto)")
    ap.add_argument("--views", type=int, default=25, help="stop live capture after this many views")
    ap.add_argument("--min-corners", type=int, default=MIN_CORNERS)
    ap.add_argument("--out", default=None, help="output yaml (default vision/calib/<camera>.yaml)")
    a = ap.parse_args()

    board, info = load_board(a.board, a.square_mm, a.marker_mm)
    print(f"board: {info['dict']} {info['squares']} square {info['square_mm']:.3f} mm marker {info['marker_mm']:.3f} mm")
    det_ = charuco_detector(board)
    views, size = [], None
    try:
        src = source_from_args(a)
    except RuntimeError as e:
        sys.exit(f"error: {e}")
    out = Path(a.out) if a.out else CALIB_DIR / f"{a.camera}.yaml"

    if isinstance(src, ImageFiles):
        for path in src.paths:
            img = cv2.imread(str(path))
            if img is None:
                continue
            size = size or (img.shape[1], img.shape[0])
            if (img.shape[1], img.shape[0]) != size:
                print(f"  {path.name}: size {img.shape[1]}x{img.shape[0]} != {size}, skipped")
                continue
            d = detect_board(det_, board, img)
            n = 0 if d is None else len(d[0])
            print(f"  {path.name}: {n} corners" + ("" if n >= a.min_corners else " (skipped)"))
            if d is not None and n >= a.min_corners:
                views.append((d[0], d[1]))
    else:
        cap_dir = CALIB_DIR / "captures" / a.camera
        cap_dir.mkdir(parents=True, exist_ok=True)
        auto = a.auto or a.headless
        gate = DiversityGate()
        last_cap = 0.0
        msg = ""
        print(f"live from {src.name}; captures go to {cap_dir}")
        try:
            while len(views) < a.views:
                img = src.read()
                if img is None:
                    time.sleep(0.02)
                    continue
                size = size or (img.shape[1], img.shape[0])
                d = detect_board(det_, board, img)
                n = 0 if d is None else len(d[0])
                key = -1
                if not a.headless:
                    cv2.imshow("calibrate", draw(img, d, len(views), auto, msg))
                    key = cv2.waitKey(1) & 0xFF
                    if key in (ord("q"), 27):
                        break
                    if key == ord("a"):
                        auto = not auto
                want = key in (ord(" "), ord("c"))
                if n >= a.min_corners:
                    pose = rough_pose(d[0], d[1], size)
                    if want or (auto and time.time() - last_cap > 0.7 and gate.novel(pose)):
                        if pose is not None:
                            gate.add(pose)
                        views.append((d[0], d[1]))
                        last_cap = time.time()
                        cv2.imwrite(str(cap_dir / f"{dt.datetime.now():%Y%m%d-%H%M%S-%f}.png"), img)
                        msg = f"captured ({n} corners)"
                        print(f"  view {len(views)}: {n} corners")
                elif want:
                    msg = f"need >= {a.min_corners} corners"
        except KeyboardInterrupt:
            pass
        finally:
            src.close()
            if not a.headless:
                cv2.destroyAllWindows()

    if len(views) < 4:
        sys.exit(f"only {len(views)} usable views; need at least 4 (15+ recommended)")
    res = calibrate(views, size)
    save_yaml(out, res, a.camera, info, a.source)
    print(f"\nwrote {out}")
    print("K =\n", np.array2string(res["K"], precision=2, suppress_small=True))
    print("dist =", np.array2string(res["dist"], precision=4))
    for line in guidance(res):
        print(" -", line)


if __name__ == "__main__":
    main()
