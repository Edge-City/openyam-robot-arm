"""Uniform frame grabbing for the vision scripts.

Every source returns BGR numpy frames from ``read()`` (or ``None`` when nothing
new is available / the folder is exhausted). Open one from a spec string:

    /dev/video0, v4l2:/dev/video0, 0      USB/UVC camera (OpenCV V4L2 by default;
                                          v4l2+ffmpeg:/dev/video0 uses ffmpeg MJPG)
    http://host/path.jpg                  poll a "latest frame" JPEG URL
    phone:side                            phone camera via dashboard/phonecam.py's local API
                                          (URL template from $VISION_PHONE_URL or
                                          --url-template, default http://127.0.0.1:8444/cam/{name}.jpg)
    dashboard                             the lab console's shared USB camera
                                          (http://127.0.0.1:8090/camera.jpg), use this when the
                                          console already holds /dev/video0
    path/to/folder, 'glob/*.jpg', img.png image files, in sorted order

CLI:  python vision/sources.py SPEC --out frame.jpg       grab one frame
      python vision/sources.py --list-phones              list phone cameras
"""
from __future__ import annotations

import argparse
import glob
import hashlib
import json
import os
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import cv2
import numpy as np

PHONE_URL_TEMPLATE = os.environ.get("VISION_PHONE_URL", "http://127.0.0.1:8444/cam/{name}.jpg")
PHONE_LIST_URL = os.environ.get("VISION_PHONE_LIST", "http://127.0.0.1:8444/cameras")
DASHBOARD_URL = os.environ.get("VISION_DASHBOARD_URL", "http://127.0.0.1:8090/camera.jpg")
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff", ".webp"}

# The USB CAMERA 4K needs about 3 s of auto-exposure before frames are usable
# (LOG.md 2026-10-16): skip roughly 90 frames at 30 fps after opening.
DEFAULT_WARMUP = 90


class FrameSource:
    name = "source"
    live = True

    def read(self) -> np.ndarray | None:
        raise NotImplementedError

    def close(self) -> None:
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def __iter__(self):
        while True:
            f = self.read()
            if f is None:
                if self.live:
                    time.sleep(0.01)
                    continue
                return
            yield f


# --------------------------------------------------------------------------- v4l2
class V4L2OpenCV(FrameSource):
    """UVC camera through OpenCV's V4L2 backend, MJPG."""

    def __init__(self, device: str | int = "/dev/video0", size=(1280, 720), fps: int = 30,
                 warmup: int = DEFAULT_WARMUP):
        self.name = str(device)
        d = str(device)
        # OpenCV's V4L2 backend opens by index; /dev/videoN -> N
        dev = int(d) if d.isdigit() else int(d[len("/dev/video"):]) if d[len("/dev/video"):].isdigit() else d
        self.cap = cv2.VideoCapture(dev, cv2.CAP_V4L2)
        if not self.cap.isOpened():
            raise RuntimeError(f"cannot open {device} (busy? the lab console may hold it: use 'dashboard' as the source)")
        self.cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, size[0])
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, size[1])
        self.cap.set(cv2.CAP_PROP_FPS, fps)
        self.cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        for _ in range(warmup):
            self.cap.grab()

    def read(self):
        ok, f = self.cap.read()
        return f if ok else None

    def close(self):
        self.cap.release()


class V4L2FFmpeg(FrameSource):
    """UVC camera through ffmpeg (MJPG passthrough), keeps only the latest frame."""

    def __init__(self, device: str = "/dev/video0", size=(1280, 720), warmup: int = DEFAULT_WARMUP):
        self.name = device
        cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin",
               "-f", "v4l2", "-input_format", "mjpeg", "-video_size", f"{size[0]}x{size[1]}",
               "-i", device, "-c:v", "copy", "-bsf:v", "mjpeg2jpeg", "-f", "mjpeg", "pipe:1"]
        self.proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=0)
        self.warmup = warmup
        self.cond = threading.Condition()
        self.jpg: bytes | None = None
        self.seq = 0
        self.read_seq = 0
        self.err = b""
        threading.Thread(target=self._reader, daemon=True).start()

    def _reader(self):
        buf = bytearray()
        while True:
            chunk = self.proc.stdout.read(65536)
            if not chunk:
                break
            buf += chunk
            while True:
                soi = buf.find(b"\xff\xd8")
                if soi < 0:
                    buf.clear()
                    break
                eoi = buf.find(b"\xff\xd9", soi + 2)
                if eoi < 0:
                    if soi > 0:
                        del buf[:soi]
                    break
                jpg = bytes(buf[soi:eoi + 2])
                del buf[:eoi + 2]
                with self.cond:
                    self.seq += 1
                    if self.seq > self.warmup:
                        self.jpg = jpg
                    self.cond.notify_all()
        try:
            self.err = self.proc.stderr.read() or b""
        except OSError:
            pass
        with self.cond:
            self.cond.notify_all()

    def read(self, timeout: float = 10.0):
        with self.cond:
            ok = self.cond.wait_for(lambda: (self.jpg is not None and self.seq != self.read_seq)
                                    or self.proc.poll() is not None, timeout=timeout)
            if self.proc.poll() is not None and self.jpg is None:
                raise RuntimeError(f"ffmpeg exited: {self.err.decode(errors='replace').strip()[-300:]}"
                                   " (busy? try the 'dashboard' source)")
            if not ok or self.jpg is None:
                return None
            self.read_seq = self.seq
            jpg = self.jpg
        return cv2.imdecode(np.frombuffer(jpg, np.uint8), cv2.IMREAD_COLOR)

    def close(self):
        if self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(2)
            except subprocess.TimeoutExpired:
                self.proc.kill()


# --------------------------------------------------------------------------- http
class HttpJpeg(FrameSource):
    """Polls a URL that returns the latest JPEG. read() returns only new frames
    (identical bytes are skipped) unless new_only=False."""

    def __init__(self, url: str, timeout: float = 5.0, min_interval: float = 0.05, new_only: bool = True,
                 name: str | None = None):
        self.url, self.timeout, self.min_interval, self.new_only = url, timeout, min_interval, new_only
        self.name = name or url
        self.last_hash = None
        self.last_t = 0.0

    def fetch_bytes(self) -> bytes:
        with urllib.request.urlopen(self.url, timeout=self.timeout) as r:
            return r.read()

    def read(self):
        wait = self.min_interval - (time.time() - self.last_t)
        if wait > 0:
            time.sleep(wait)
        self.last_t = time.time()
        try:
            data = self.fetch_bytes()
        except urllib.error.HTTPError as e:
            raise RuntimeError(f"{self.url}: HTTP {e.code} {e.reason}") from None
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            raise RuntimeError(f"{self.url}: {e} (is the server running?)") from None
        h = hashlib.sha1(data).digest()
        if self.new_only and h == self.last_hash:
            return None
        self.last_hash = h
        return cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)


def list_phone_cameras(url: str = PHONE_LIST_URL, timeout: float = 3.0) -> list[dict]:
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.load(r).get("cameras", [])


# --------------------------------------------------------------------------- files
class ImageFiles(FrameSource):
    live = False

    def __init__(self, spec: str):
        p = Path(spec)
        if p.is_dir():
            paths = sorted(q for q in p.iterdir() if q.suffix.lower() in IMAGE_EXTS)
        elif p.is_file():
            paths = [p]
        else:
            paths = sorted(Path(q) for q in glob.glob(spec) if Path(q).suffix.lower() in IMAGE_EXTS)
        if not paths:
            raise RuntimeError(f"no images found for {spec!r}")
        self.paths = paths
        self.i = 0
        self.current_path: Path | None = None
        self.name = p.name or spec

    def read(self):
        while self.i < len(self.paths):
            self.current_path = self.paths[self.i]
            self.i += 1
            f = cv2.imread(str(self.current_path), cv2.IMREAD_COLOR)
            if f is not None:
                return f
            print(f"warning: cannot read {self.current_path}", file=sys.stderr)
        return None


# --------------------------------------------------------------------------- factory
def open_source(spec: str, size=(1280, 720), warmup: int = DEFAULT_WARMUP,
                url_template: str | None = None, **kw) -> FrameSource:
    s = str(spec)
    if s.startswith("phone:"):
        name = s.split(":", 1)[1]
        return HttpJpeg((url_template or PHONE_URL_TEMPLATE).format(name=name), name=f"phone-{name}", **kw)
    if s == "dashboard":
        return HttpJpeg(DASHBOARD_URL, name="dashboard-usbcam", **kw)
    if s.startswith(("http://", "https://")):
        return HttpJpeg(s, **kw)
    if s.startswith("v4l2+ffmpeg:"):
        return V4L2FFmpeg(s.split(":", 1)[1], size=size, warmup=warmup)
    if s.startswith("v4l2:"):
        s = s.split(":", 1)[1]
    if s.startswith("/dev/video") or s.isdigit():
        return V4L2OpenCV(s, size=size, warmup=warmup)
    return ImageFiles(s)


def source_args(ap: argparse.ArgumentParser) -> None:
    """Shared CLI options for the scripts that take a source."""
    ap.add_argument("--source", default="/dev/video0", help="see vision/sources.py (default /dev/video0)")
    ap.add_argument("--size", default="1280x720", help="capture size for v4l2 sources")
    ap.add_argument("--warmup", type=int, default=DEFAULT_WARMUP, help="frames to skip after opening a v4l2 camera")
    ap.add_argument("--url-template", default=None, help="phone URL template, e.g. http://127.0.0.1:8444/cam/{name}.jpg")


def source_from_args(a) -> FrameSource:
    w, h = (int(v) for v in a.size.lower().split("x"))
    return open_source(a.source, size=(w, h), warmup=a.warmup, url_template=a.url_template)


def grab_one(src: FrameSource, timeout: float = 10.0) -> np.ndarray | None:
    t0 = time.time()
    while time.time() - t0 < timeout:
        f = src.read()
        if f is not None:
            return f
        if not src.live:
            return None
        time.sleep(0.05)
    return None


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("spec", nargs="?", help="source spec (overrides --source)")
    source_args(ap)
    ap.add_argument("--out", default=None, help="write the grabbed frame here")
    ap.add_argument("--list-phones", action="store_true")
    a = ap.parse_args()
    if a.list_phones:
        for c in list_phone_cameras():
            print(c)
        return
    if a.spec:
        a.source = a.spec
    with source_from_args(a) as src:
        t0 = time.time()
        f = grab_one(src)
        if f is None:
            sys.exit("no frame")
        print(f"{src.name}: {f.shape[1]}x{f.shape[0]}, mean brightness {f.mean():.1f}, {time.time()-t0:.1f} s")
        if a.out:
            Path(a.out).parent.mkdir(parents=True, exist_ok=True)
            cv2.imwrite(a.out, f)
            print("wrote", a.out)


if __name__ == "__main__":
    main()
