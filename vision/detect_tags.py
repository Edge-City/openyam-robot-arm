"""Detect ArUco tags (DICT_4X4_50) and estimate their 6-DoF pose.

Tag frame (OpenCV IPPE_SQUARE convention): origin at the tag centre, x to the
right and y to the top of the printed tag (as you read its label), z out of the
paper towards the camera. Positions are in the camera frame (x right, y down,
z forward), in metres. When tag 0 (table / world origin) is visible, every
other tag is also reported in tag 0's frame.

  .venv/bin/python vision/detect_tags.py --calib vision/calib/usbcam.yaml --source /dev/video0 --tag-size 40
  .venv/bin/python vision/detect_tags.py --calib vision/calib/side.yaml --source phone:side --sizes 0:40,1:25 --out annotated.jpg
  .venv/bin/python vision/detect_tags.py --calib ... --source phone:side --watch      # continuous

Library:
  from detect_tags import TagDetector
  det = TagDetector(K, dist, tag_size_m=0.04, sizes={1: 0.025})
  tags = det.detect(bgr)          # list of Tag(id, role, size, corners, rvec, tvec, R, reproj_px)
  rel = det.relative_to(tags, 0)  # {id: 4x4 pose in tag 0's frame}
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np

from sources import grab_one, source_args, source_from_args
from targets_spec import TAG_DICT, aruco_dict, role


@dataclass
class Tag:
    id: int
    role: str
    size: float                 # m
    corners: np.ndarray         # 4x2 px, TL TR BR BL
    rvec: np.ndarray
    tvec: np.ndarray            # m, camera frame
    R: np.ndarray = field(repr=False)
    reproj_px: float = 0.0

    @property
    def T(self) -> np.ndarray:
        T = np.eye(4)
        T[:3, :3], T[:3, 3] = self.R, self.tvec
        return T

    @property
    def distance(self) -> float:
        return float(np.linalg.norm(self.tvec))

    def as_dict(self):
        return {"id": self.id, "role": self.role, "size_m": self.size,
                "t_m": [round(float(v), 4) for v in self.tvec],
                "rpy_deg": [round(float(v), 2) for v in rpy_deg(self.R)],
                "rvec": [round(float(v), 5) for v in self.rvec], "distance_m": round(self.distance, 4),
                "reproj_px": round(self.reproj_px, 3)}


def rpy_deg(R) -> np.ndarray:
    """Roll (x), pitch (y), yaw (z) for R = Rz(yaw) Ry(pitch) Rx(roll), degrees."""
    pitch = np.arcsin(-np.clip(R[2, 0], -1, 1))
    if abs(np.cos(pitch)) > 1e-6:
        roll = np.arctan2(R[2, 1], R[2, 2])
        yaw = np.arctan2(R[1, 0], R[0, 0])
    else:
        roll, yaw = np.arctan2(-R[1, 2], R[1, 1]), 0.0
    return np.degrees([roll, pitch, yaw])


def tag_object_points(s: float) -> np.ndarray:
    h = s / 2
    return np.array([[-h, h, 0], [h, h, 0], [h, -h, 0], [-h, -h, 0]], np.float32)


class TagDetector:
    def __init__(self, K, dist, tag_size_m: float = 0.04, sizes: dict[int, float] | None = None,
                 dict_name: str = TAG_DICT, calib_size: tuple[int, int] | None = None, ids=None):
        self.K = np.asarray(K, float)
        self.dist = np.zeros(5) if dist is None else np.asarray(dist, float).ravel()
        self.default_size = tag_size_m
        self.sizes = sizes or {}
        self.calib_size = calib_size
        self.ids = set(ids) if ids is not None else None
        p = cv2.aruco.DetectorParameters()
        p.cornerRefinementMethod = cv2.aruco.CORNER_REFINE_SUBPIX
        p.cornerRefinementWinSize = 5
        self.detector = cv2.aruco.ArucoDetector(aruco_dict(dict_name), p)
        self._warned = False

    def _K_for(self, shape):
        h, w = shape[:2]
        if self.calib_size is None or self.calib_size == (w, h):
            return self.K
        sx, sy = w / self.calib_size[0], h / self.calib_size[1]
        if not self._warned:
            print(f"warning: image {w}x{h} != calibration {self.calib_size}; scaling K"
                  + ("" if abs(sx - sy) < 1e-3 else " (aspect differs: crop? results unreliable)"), file=sys.stderr)
            self._warned = True
        return np.diag([sx, sy, 1.0]) @ self.K

    def detect(self, img) -> list[Tag]:
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.ndim == 3 else img
        corners, ids, _ = self.detector.detectMarkers(gray)
        if ids is None:
            return []
        K = self._K_for(gray.shape)
        tags = []
        for c, i in zip(corners, ids.ravel()):
            i = int(i)
            if self.ids is not None and i not in self.ids:
                continue
            s = self.sizes.get(i, self.default_size)
            obj = tag_object_points(s)
            imgp = c.reshape(4, 2).astype(np.float64)
            ok, rvec, tvec = cv2.solvePnP(obj, imgp, K, self.dist, flags=cv2.SOLVEPNP_IPPE_SQUARE)
            if not ok:
                continue
            proj, _ = cv2.projectPoints(obj, rvec, tvec, K, self.dist)
            err = float(np.sqrt(np.mean(np.sum((proj.reshape(4, 2) - imgp) ** 2, axis=1))))
            R = cv2.Rodrigues(rvec)[0]
            tags.append(Tag(i, role(i), s, imgp, rvec.ravel(), tvec.ravel(), R, err))
        return sorted(tags, key=lambda t: t.id)

    @staticmethod
    def relative_to(tags: list[Tag], ref_id: int = 0) -> dict[int, np.ndarray]:
        ref = next((t for t in tags if t.id == ref_id), None)
        if ref is None:
            return {}
        Tinv = np.linalg.inv(ref.T)
        return {t.id: Tinv @ t.T for t in tags if t.id != ref_id}

    def annotate(self, img, tags: list[Tag]):
        vis = img.copy()
        K = self._K_for(img.shape)
        for t in tags:
            cv2.polylines(vis, [t.corners.astype(np.int32)], True, (0, 255, 0), 2)
            cv2.drawFrameAxes(vis, K, self.dist, t.rvec, t.tvec, t.size * 0.75, 2)
            p = t.corners[0].astype(int)
            label = f"{t.id} {t.role} z={t.tvec[2]:.3f}m"
            cv2.putText(vis, label, (p[0], p[1] - 8), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 0), 4, cv2.LINE_AA)
            cv2.putText(vis, label, (p[0], p[1] - 8), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 255), 2, cv2.LINE_AA)
        return vis


def detector_from_calib(calib_path, tag_size_m=0.04, sizes=None, **kw) -> TagDetector:
    from calibrate_intrinsics import load_calibration
    K, dist, size = load_calibration(calib_path)
    return TagDetector(K, dist, tag_size_m, sizes, calib_size=size, **kw)


def parse_sizes(s: str | None) -> dict[int, float]:
    """'0:40,1:25' (mm) -> {0: 0.04, 1: 0.025}"""
    out = {}
    for part in (s or "").split(","):
        if part.strip():
            k, v = part.split(":")
            out[int(k)] = float(v) / 1000.0
    return out


def report(tags: list[Tag]) -> str:
    if not tags:
        return "no tags"
    lines = []
    for t in tags:
        r = rpy_deg(t.R)
        lines.append(f"id {t.id:2d} {t.role:22s} t=({t.tvec[0]:+.4f}, {t.tvec[1]:+.4f}, {t.tvec[2]:+.4f}) m  "
                     f"rpy=({r[0]:+7.2f}, {r[1]:+7.2f}, {r[2]:+7.2f}) deg  dist {t.distance:.3f} m  "
                     f"reproj {t.reproj_px:.2f} px")
    for i, T in TagDetector.relative_to(tags, 0).items():
        r = rpy_deg(T[:3, :3])
        lines.append(f"   in tag-0 frame: id {i:2d} xyz=({T[0,3]:+.4f}, {T[1,3]:+.4f}, {T[2,3]:+.4f}) m  "
                     f"yaw {r[2]:+.1f} deg")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    source_args(ap)
    ap.add_argument("--calib", default=None,
                    help="vision/calib/<camera>.yaml (without it a guessed K is used: detection works, poses are rough)")
    ap.add_argument("--tag-size", type=float, default=40.0, help="default tag size in mm (measured black edge)")
    ap.add_argument("--sizes", default=None, help="per-id sizes in mm, e.g. 0:40,1:25")
    ap.add_argument("--out", default=None, help="write an annotated image (last frame)")
    ap.add_argument("--json", action="store_true", help="print JSON instead of text")
    ap.add_argument("--watch", action="store_true", help="keep detecting until Ctrl-C")
    a = ap.parse_args()

    try:
        src = source_from_args(a)
    except RuntimeError as e:
        sys.exit(f"error: {e}")
    det = None
    if a.calib:
        det = detector_from_calib(a.calib, a.tag_size / 1000.0, parse_sizes(a.sizes))
    try:
        while True:
            img = grab_one(src)
            if img is None:
                if src.live:
                    sys.exit("no frame from source")
                break
            if det is None:
                h, w = img.shape[:2]
                print(f"warning: no --calib, guessing K (f = 0.9 x width); poses are approximate", file=sys.stderr)
                det = TagDetector([[0.9 * w, 0, w / 2], [0, 0.9 * w, h / 2], [0, 0, 1]], None,
                                  a.tag_size / 1000.0, parse_sizes(a.sizes))
            tags = det.detect(img)
            name = getattr(src, "current_path", None) or src.name
            if a.json:
                print(json.dumps({"source": str(name), "time": time.time(), "tags": [t.as_dict() for t in tags]}))
            else:
                print(f"[{name}] {img.shape[1]}x{img.shape[0]}  {len(tags)} tag(s)")
                print(report(tags))
            if a.out:
                Path(a.out).parent.mkdir(parents=True, exist_ok=True)
                cv2.imwrite(a.out, det.annotate(img, tags))
            if not (a.watch or not src.live):
                break
    except KeyboardInterrupt:
        pass
    finally:
        src.close()


if __name__ == "__main__":
    main()
