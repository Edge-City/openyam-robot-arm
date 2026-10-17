"""Self-test of the whole pipeline without a real camera.

Renders the generated target PNGs (vision/targets/) into synthetic photos from a
known virtual camera (random poses, lens distortion, blur, noise, JPEG), then
  1. runs calibrate_intrinsics.py on the rendered board views (as a CLI, from a
     folder) and compares the recovered K / distortion with the truth;
  2. detects the tags in rendered tag-sheet views using the *recovered*
     calibration and compares tag poses (camera frame, and relative to tag 0)
     with the truth.

  .venv/bin/python vision/selftest.py            [--seed 1] [--views 20] [--keep DIR]
Exit code 0 when every check passes.
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import cv2
import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from calibrate_intrinsics import load_calibration  # noqa: E402
from detect_tags import TagDetector  # noqa: E402
from targets_spec import PX_PER_MM, TARGETS_DIR  # noqa: E402

# Ground-truth virtual camera: 1280x720, ~70 deg HFOV, mild barrel distortion.
SIZE = (1280, 720)
K_TRUE = np.array([[920.0, 0, 652.0], [0, 918.0, 355.0], [0, 0, 1]])
DIST_TRUE = np.array([-0.12, 0.09, 0.0006, -0.0004, -0.02])
SS = 2                 # supersampling factor
TEX_PX_PER_MM = 5.0    # texture resolution used for rendering


def rot(rx=0.0, ry=0.0, rz=0.0):
    rx, ry, rz = np.radians([rx, ry, rz])
    Rx = np.array([[1, 0, 0], [0, np.cos(rx), -np.sin(rx)], [0, np.sin(rx), np.cos(rx)]])
    Ry = np.array([[np.cos(ry), 0, np.sin(ry)], [0, 1, 0], [-np.sin(ry), 0, np.cos(ry)]])
    Rz = np.array([[np.cos(rz), -np.sin(rz), 0], [np.sin(rz), np.cos(rz), 0], [0, 0, 1]])
    return Rz @ Ry @ Rx


def rot_err_deg(R1, R2):
    return float(np.degrees(np.linalg.norm(cv2.Rodrigues(R1.T @ R2)[0])))


class Renderer:
    """Renders a flat page texture. Page frame: origin at the page's top-left
    corner, x right, y down (as in the PNG), z into the paper; metres."""

    def __init__(self, K, dist, size, rng):
        self.K, self.dist, self.size, self.rng = K, dist, size, rng
        w, h = size
        self.K2 = K.copy()
        self.K2[:2] *= SS
        self.K2[0, 2] += (SS - 1) / 2
        self.K2[1, 2] += (SS - 1) / 2
        W, H = w * SS, h * SS
        gx, gy = np.meshgrid(np.arange(W, dtype=np.float32), np.arange(H, dtype=np.float32))
        pts = np.stack([gx.ravel(), gy.ravel()], 1).reshape(-1, 1, 2)
        und = cv2.undistortPointsIter(pts, self.K2, dist, None, self.K2,
                                      (cv2.TERM_CRITERIA_COUNT | cv2.TERM_CRITERIA_EPS, 40, 1e-6))
        und = und.reshape(H, W, 2)
        self.mapx, self.mapy = und[..., 0].copy(), und[..., 1].copy()

    @staticmethod
    def load_texture(png: Path):
        page = cv2.imread(str(png), cv2.IMREAD_GRAYSCALE)
        f = TEX_PX_PER_MM / PX_PER_MM
        tex = cv2.resize(page, None, fx=f, fy=f, interpolation=cv2.INTER_AREA)
        # exact texture px per mm after rounding of the resized size
        return tex, tex.shape[1] / (page.shape[1] / PX_PER_MM)

    def background(self):
        w, h = self.size[0] * SS, self.size[1] * SS
        small = self.rng.uniform(60, 200, (9, 16)).astype(np.float32)
        bg = cv2.resize(small, (w, h), interpolation=cv2.INTER_CUBIC)
        return np.clip(bg + self.rng.normal(0, 6, (h, w)), 0, 255).astype(np.float32)

    def render(self, tex, tex_ppmm, R, t, blur=0.6, noise=2.5):
        s = 1.0 / (tex_ppmm * 1000.0)               # metres per texture px
        A = np.array([[s, 0, 0.5 * s], [0, s, 0.5 * s], [0, 0, 1]])
        H = self.K2 @ np.column_stack([R[:, 0], R[:, 1], t]) @ A
        W2, H2 = self.size[0] * SS, self.size[1] * SS
        paper = self.rng.uniform(200, 245)
        texf = tex.astype(np.float32) / 255.0 * (paper - 25) + 25    # black ink ~25, paper ~200-245
        ideal = cv2.warpPerspective(texf, H, (W2, H2), flags=cv2.INTER_LINEAR, borderValue=0)
        mask = cv2.warpPerspective(np.ones_like(texf), H, (W2, H2), flags=cv2.INTER_LINEAR, borderValue=0)
        # light gradient across the image
        gx = np.linspace(self.rng.uniform(0.75, 1.0), self.rng.uniform(0.85, 1.05), W2, dtype=np.float32)
        ideal = ideal * gx[None, :]
        img = ideal * mask + self.background() * (1 - mask)
        img = cv2.remap(img, self.mapx, self.mapy, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT)
        img = cv2.resize(img, self.size, interpolation=cv2.INTER_AREA)
        img = cv2.GaussianBlur(img, (0, 0), blur)
        img = np.clip(img + self.rng.normal(0, noise, img.shape), 0, 255).astype(np.uint8)
        ok, jpg = cv2.imencode(".jpg", cv2.cvtColor(img, cv2.COLOR_GRAY2BGR), [cv2.IMWRITE_JPEG_QUALITY, 92])
        return cv2.imdecode(jpg, cv2.IMREAD_COLOR)

    def random_pose(self, centre_page_m, dist_range=(0.35, 0.75), max_tilt=45.0, max_roll=30.0, spread=0.35):
        """Pose (R, t) of the page so that centre_page_m lands at a random image spot."""
        rng = self.rng
        tilt_x = rng.uniform(-max_tilt, max_tilt)
        tilt_y = rng.uniform(-max_tilt, max_tilt)
        R = rot(tilt_x, tilt_y, rng.uniform(-max_roll, max_roll))
        d = rng.uniform(*dist_range)
        u = self.size[0] * (0.5 + rng.uniform(-spread, spread))
        v = self.size[1] * (0.5 + rng.uniform(-spread, spread))
        ray = np.linalg.inv(self.K) @ np.array([u, v, 1.0])
        c_cam = ray / ray[2] * d
        return R, c_cam - R @ centre_page_m


def check(name, ok, detail):
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}: {detail}")
    return ok


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--views", type=int, default=20)
    ap.add_argument("--tag-views", type=int, default=12)
    ap.add_argument("--keep", default=None, help="keep rendered images and the yaml in this folder")
    a = ap.parse_args()
    rng = np.random.default_rng(a.seed)
    if not (TARGETS_DIR / "charuco_board.json").exists():
        subprocess.run([sys.executable, str(HERE / "make_targets.py")], check=True)
    board_info = json.loads((TARGETS_DIR / "charuco_board.json").read_text())
    tags_info = json.loads((TARGETS_DIR / "tags.json").read_text())

    work = Path(a.keep) if a.keep else Path(tempfile.mkdtemp(prefix="vision-selftest-"))
    (work / "board").mkdir(parents=True, exist_ok=True)
    (work / "tags").mkdir(parents=True, exist_ok=True)
    print(f"virtual camera {SIZE[0]}x{SIZE[1]}  fx {K_TRUE[0,0]} fy {K_TRUE[1,1]} cx {K_TRUE[0,2]} cy {K_TRUE[1,2]}"
          f"  dist {DIST_TRUE.tolist()}\nrendering into {work}")
    rnd = Renderer(K_TRUE, DIST_TRUE, SIZE, rng)

    # ---------------------------------------------------------------- 1. calibration
    tex, tppmm = rnd.load_texture(TARGETS_DIR / "charuco_board_A4.png")
    sq = board_info["square_mm"] / 1000
    cols, rows = board_info["squares"]
    ox, oy = (np.array(board_info["origin_px"]) / PX_PER_MM / 1000)
    centre = np.array([ox + cols * sq / 2, oy + rows * sq / 2, 0])
    for k in range(a.views):
        R, t = rnd.random_pose(centre)
        cv2.imwrite(str(work / "board" / f"view{k:02d}.png"), rnd.render(tex, tppmm, R, t))
    yaml = work / "selftest.yaml"
    print("\n$ calibrate_intrinsics.py --camera selftest --source <rendered folder>")
    r = subprocess.run([sys.executable, str(HERE / "calibrate_intrinsics.py"), "--camera", "selftest",
                        "--source", str(work / "board"), "--out", str(yaml)], capture_output=True, text=True)
    print("\n".join("    " + l for l in (r.stdout + r.stderr).strip().splitlines()[-14:]))
    if r.returncode != 0:
        sys.exit("calibration failed")
    K, dist, size = load_calibration(yaml)
    fs = cv2.FileStorage(str(yaml), cv2.FILE_STORAGE_READ)
    rms = fs.getNode("rms_reprojection_error_px").real()
    nv = int(fs.getNode("n_views").real())
    fs.release()

    print("\nintrinsics (recovered vs true):")
    ok = True
    for name, i, j, ref in [("fx", 0, 0, K_TRUE[0, 0]), ("fy", 1, 1, K_TRUE[1, 1]),
                            ("cx", 0, 2, SIZE[0]), ("cy", 1, 2, SIZE[1])]:
        err = K[i, j] - K_TRUE[i, j]
        pct = abs(err) / ref * 100
        ok &= check(name, pct < 1.0, f"{K[i, j]:.2f} vs {K_TRUE[i, j]:.2f} (err {err:+.2f} px = {pct:.2f}% of "
                                     f"{'f' if name[0] == 'f' else 'image size'})")
    # Undistort a pixel grid with both models; difference in pixels (normalised by the true K).
    grid = np.stack(np.meshgrid(np.linspace(0, SIZE[0] - 1, 33), np.linspace(0, SIZE[1] - 1, 19)), -1).reshape(-1, 1, 2)
    und_true = cv2.undistortPointsIter(grid, K_TRUE, DIST_TRUE, None, K_TRUE, (3, 40, 1e-8))
    und_est = cv2.undistortPointsIter(grid, K, dist, None, K_TRUE, (3, 40, 1e-8))
    d = np.linalg.norm(und_true - und_est, axis=2).reshape(19, 33)
    inner = d[2:-2, 2:-2]          # inner ~80% of the image, where the board was actually seen
    # This is the whole model (K + distortion): the pixel -> ray difference, expressed in pixels at f_true.
    # It includes the small cx/cy error, which p1/p2 partly compensate.
    ok &= check("pixel->ray model", inner.max() < 2.0,
                f"max {inner.max():.2f} px ({np.degrees(inner.max() / K_TRUE[0, 0]):.3f} deg) in the inner 80% "
                f"of the image, {d.max():.2f} px at the extreme corners (extrapolated); "
                f"k1 {dist[0]:+.4f} vs {DIST_TRUE[0]:+.4f}, k2 {dist[1]:+.4f} vs {DIST_TRUE[1]:+.4f}")
    ok &= check("rms", rms < 0.5, f"RMS reprojection {rms:.3f} px over {nv} views")

    # ---------------------------------------------------------------- 2. tag poses
    print("\ntag poses (rendered tag sheets, detected with the RECOVERED calibration):")
    R_page_tag = np.diag([1.0, -1.0, -1.0])   # tag x right, y up, z out of the paper
    pos_err, rot_err, rel_err, n_missing, n_seen = [], [], [], 0, 0
    per_size = {}
    for page_key, png in [("40mm", "aruco_tags_40mm.png"), ("25mm", "aruco_tags_25mm.png")]:
        info = tags_info["pages"][page_key]
        s = info["size_mm"] / 1000
        centers = {int(k): np.array([v[0] / 1000, v[1] / 1000, 0]) for k, v in info["centers_mm"].items()}
        texT, tpp = rnd.load_texture(TARGETS_DIR / png)
        ids = sorted(centers)
        # aim at the centre of the tag grid; keep the views closer for the small tags
        mid = np.mean([centers[i] for i in ids], axis=0)
        det = TagDetector(K, dist, s, calib_size=size)
        pe, re_ = [], []
        for k in range(a.tag_views):
            R, t = rnd.random_pose(mid, dist_range=(0.30, 0.55) if s > 0.03 else (0.25, 0.40),
                                   max_tilt=40.0, max_roll=25.0, spread=0.1)
            img = rnd.render(texT, tpp, R, t)
            cv2.imwrite(str(work / "tags" / f"{page_key}-view{k:02d}.png"), img)
            tags = det.detect(img)
            got = {tg.id: tg for tg in tags}
            vis_ids = []
            for i in ids:
                c_cam = R @ centers[i] + t
                uv = K_TRUE @ c_cam
                uv = uv[:2] / uv[2]
                if not (40 < uv[0] < SIZE[0] - 40 and 40 < uv[1] < SIZE[1] - 40):
                    continue
                vis_ids.append(i)
                n_seen += 1
                if i not in got:
                    n_missing += 1
                    continue
                tg = got[i]
                pe.append(np.linalg.norm(tg.tvec - c_cam) * 1000)
                re_.append(rot_err_deg(tg.R, R @ R_page_tag))
            if 0 in got:
                for i, T in TagDetector.relative_to(tags, 0).items():
                    gt = R_page_tag.T @ (centers[i] - centers[0])
                    rel_err.append(np.linalg.norm(T[:3, 3] - gt) * 1000)
            if k == 0:
                cv2.imwrite(str(work / "tags" / f"{page_key}-view00-annotated.jpg"), det.annotate(img, tags))
        per_size[page_key] = (pe, re_)
        pos_err += pe
        rot_err += re_
        print(f"  {page_key} tags: {len(pe)} poses, position error median {np.median(pe):.2f} mm "
              f"(max {np.max(pe):.2f}), rotation error median {np.median(re_):.2f} deg (max {np.max(re_):.2f})")

    pos_err, rot_err, rel_err = map(np.array, (pos_err, rot_err, rel_err))
    ok &= check("detection", n_missing <= 0.05 * n_seen, f"{n_seen - n_missing}/{n_seen} in-view tags detected")
    ok &= check("tag position", np.median(pos_err) < 3.0 and np.percentile(pos_err, 95) < 8.0,
                f"median {np.median(pos_err):.2f} mm, 95th pct {np.percentile(pos_err, 95):.2f} mm, max {pos_err.max():.2f} mm "
                f"(tags 0.25-0.55 m away)")
    ok &= check("tag rotation", np.median(rot_err) < 2.0 and np.percentile(rot_err, 95) < 5.0,
                f"median {np.median(rot_err):.2f} deg, 95th pct {np.percentile(rot_err, 95):.2f} deg, max {rot_err.max():.2f} deg")
    if len(rel_err):
        ok &= check("tag relative to tag 0", np.median(rel_err) < 3.0,
                    f"median {np.median(rel_err):.2f} mm, max {rel_err.max():.2f} mm over {len(rel_err)} pairs")
    if a.keep:
        print(f"\nrendered images: {work}")
    else:
        shutil.rmtree(work, ignore_errors=True)
    print("SELFTEST", "PASSED" if ok else "FAILED")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
