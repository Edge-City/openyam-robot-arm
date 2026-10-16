#!/usr/bin/env python3
"""OpenYAM lab console: a local read-only dashboard for the arm.

Run:  .venv/bin/python dashboard/server.py            (http://127.0.0.1:8090)
      .venv/bin/python dashboard/server.py --host 0.0.0.0 --log path/to/LOG.md

Safety: the ONLY CAN frame this server can transmit is the Damiao read-only
"refresh" request (ID 0x7FF, [motor_id, 0x00, 0xCC, 0, 0, 0, 0, 0]). It never
enables, disables, zeroes or commands a motor. In "auto" mode it polls at 10 Hz
only while no other process is talking to the motors; in "listen" mode it never
transmits. Either way it decodes every motor reply it sees on the bus, so it
also works passively while DimOS is driving the arm.
"""
from __future__ import annotations

import argparse
import asyncio
import collections
import contextlib
import datetime as dt
import errno
import json
import os
import re
import socket
import struct
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles

from playable_preview_server import add_playable_routes  # read-only playable API (+ validate-only POST)

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
STATIC_DIR = HERE / "static"
COACH_PATH = HERE / "coach.md"
INBOX_PATH = HERE / "inbox.md"
DEFAULT_LOG = REPO / "experiments" / "2026-10-16-linux-real-arm" / "LOG.md"
DEFAULT_URDF_DIR = Path.home() / ".local/share/dimos/repo/data/yam_description"
PHONE_URL_FILE = HERE / "certs" / "phone-url.txt"  # written by phonecam.py; contains the phone token
PHONE_PAGES = {  # phonecam.py routes, all behind the same token
    "camera": ("/", "Phone camera", "Stream this phone's camera to the console (side, top, …)."),
    "tilt": ("/tilt", "Tilt controller", "Tilt to move in x/y, lift the phone for up/down. Hold to move."),
    "joystick": ("/joystick", "Joystick", "Two thumb sticks: x/y, and height/yaw. Gripper slider."),
}
LOOPBACK = ("127.0.0.1", "::1", "localhost", "testclient")

# --------------------------------------------------------------------------
# Motor table
# --------------------------------------------------------------------------
PMAX = 12.5
DM4340 = {"model": "DM4340", "vmax": 10.0, "tmax": 28.0}
DM4310 = {"model": "DM4310", "vmax": 30.0, "tmax": 10.0}

MOTORS = [
    # name, CAN id, motor type, URDF limits (rad) or None
    ("joint1", 0x01, DM4340, (-2.61799, 3.14159)),
    ("joint2", 0x02, DM4340, (0.0, 3.66519)),
    ("joint3", 0x03, DM4340, (0.0, 3.14159)),
    ("joint4", 0x04, DM4310, (-1.69297, 1.5708)),
    ("joint5", 0x05, DM4310, (-1.5708, 1.5708)),
    ("joint6", 0x06, DM4310, (-2.0944, 2.0944)),
    ("gripper", 0x08, DM4310, None),
]
MOTOR_IDS = tuple(m[1] for m in MOTORS)
REPLY_IDS = {0x10 + mid: i for i, (_, mid, _, _) in enumerate(MOTORS)}

FAULT_NAMES = {
    0x8: "over-voltage",
    0x9: "under-voltage",
    0xA: "over-current",
    0xB: "MOS over-temp",
    0xC: "coil over-temp",
    0xD: "comm lost",
    0xE: "overload",
}

REFRESH_ID = 0x7FF
POLL_HZ = 10.0
EXTERNAL_QUIET_S = 1.0  # other process silent this long before we transmit
STARTUP_LISTEN_S = 1.0  # listen this long before our first transmit

CAN_FRAME_FMT = "=IB3x8s"
CAN_FRAME_SIZE = struct.calcsize(CAN_FRAME_FMT)
CAN_EFF_FLAG = 0x80000000
CAN_RTR_FLAG = 0x40000000
CAN_ERR_FLAG = 0x20000000


def u_to_f(x: int, lo: float, hi: float, bits: int) -> float:
    return x * (hi - lo) / ((1 << bits) - 1) + lo


def refresh_frame(motor_id: int) -> bytes:
    """The one and only frame this program may send."""
    if motor_id not in MOTOR_IDS:
        raise ValueError(f"refusing to build frame for unknown motor id {motor_id:#x}")
    data = bytes([motor_id, 0x00, 0xCC, 0, 0, 0, 0, 0])
    return struct.pack(CAN_FRAME_FMT, REFRESH_ID, 8, data)


def is_command_frame(can_id: int, data: bytes) -> str | None:
    """Classify a frame sent by another process. Returns 'controller', 'reader' or None."""
    if can_id == REFRESH_ID:
        if len(data) >= 3 and data[2] == 0xCC:
            return "reader"  # someone else polling read-only
        return "controller"  # 0x7FF param/zero/etc
    if 0x001 <= can_id <= 0x008:
        return "controller"  # MIT / enable / disable / zero
    if 0x100 <= can_id <= 0x108 or 0x200 <= can_id <= 0x208 or 0x300 <= can_id <= 0x308:
        return "controller"  # pos-vel / vel / force-pos modes
    return None


class RollingRate:
    def __init__(self, window: float = 2.0):
        self.window = window
        self.ts: collections.deque[float] = collections.deque()

    def tick(self, t: float) -> None:
        self.ts.append(t)
        self._trim(t)

    def _trim(self, now: float) -> None:
        while self.ts and now - self.ts[0] > self.window:
            self.ts.popleft()

    def rate(self, now: float) -> float:
        self._trim(now)
        return len(self.ts) / self.window


# --------------------------------------------------------------------------
# CAN monitor
# --------------------------------------------------------------------------
class CanMonitor:
    def __init__(self, iface: str, mode: str = "auto"):
        self.iface = iface
        self.mode = mode  # "auto" | "listen"
        self.lock = threading.Lock()
        self.sock: socket.socket | None = None
        self.stop_evt = threading.Event()
        self.started = time.time()
        self.connected_at: float | None = None
        self.last_error: str | None = None
        self.last_error_t: float | None = None
        self.rx_total = 0
        self.tx_total = 0
        self.rx_rate = RollingRate()
        self.tx_rate = RollingRate()
        self.last_controller_t: float | None = None
        self.last_reader_t: float | None = None
        self.last_controller_frame: str | None = None
        self.transmitting = False
        self.motors = [
            {
                "name": n,
                "id": mid,
                "model": mt["model"],
                "limits": list(lim) if lim else None,
                "t": None,
                "raw": None,
                "rate": RollingRate(),
                "count": 0,
            }
            for (n, mid, mt, lim) in MOTORS
        ]

    # ---- socket management
    def _open(self) -> None:
        s = socket.socket(socket.AF_CAN, socket.SOCK_RAW, socket.CAN_RAW)
        # Defaults, set explicitly: see other sockets' TX (loopback on), but not our own.
        s.setsockopt(socket.SOL_CAN_RAW, socket.CAN_RAW_LOOPBACK, 1)
        s.setsockopt(socket.SOL_CAN_RAW, socket.CAN_RAW_RECV_OWN_MSGS, 0)
        s.bind((self.iface,))
        s.settimeout(0.2)
        self.sock = s
        self.connected_at = time.time()

    def _error(self, msg: str) -> None:
        with self.lock:
            self.last_error = msg
            self.last_error_t = time.time()

    def start(self) -> None:
        threading.Thread(target=self._rx_loop, name="can-rx", daemon=True).start()
        threading.Thread(target=self._tx_loop, name="can-tx", daemon=True).start()

    def stop(self) -> None:
        self.stop_evt.set()

    def _rx_loop(self) -> None:
        while not self.stop_evt.is_set():
            if self.sock is None:
                try:
                    self._open()
                except OSError as e:
                    self._error(f"open {self.iface}: {e}")
                    self.sock = None
                    time.sleep(2.0)
                    continue
            try:
                frame = self.sock.recv(CAN_FRAME_SIZE)
            except socket.timeout:
                continue
            except OSError as e:
                self._error(f"recv: {e}")
                try:
                    self.sock.close()
                except OSError:
                    pass
                self.sock = None
                self.connected_at = None
                time.sleep(1.0)
                continue
            if len(frame) < CAN_FRAME_SIZE:
                continue
            can_id, dlc, data = struct.unpack(CAN_FRAME_FMT, frame)
            now = time.time()
            if can_id & CAN_ERR_FLAG:
                self._error(f"CAN error frame id={can_id & 0x1FFFFFFF:#x} data={data[:dlc].hex(' ')}")
                continue
            if can_id & (CAN_EFF_FLAG | CAN_RTR_FLAG):
                continue
            data = data[:dlc]
            with self.lock:
                self.rx_total += 1
                self.rx_rate.tick(now)
                idx = REPLY_IDS.get(can_id)
                if idx is not None and dlc == 8:
                    m = self.motors[idx]
                    m["raw"] = data
                    m["t"] = now
                    m["count"] += 1
                    m["rate"].tick(now)
                    continue
                kind = is_command_frame(can_id, data)
                if kind == "controller":
                    self.last_controller_t = now
                    self.last_controller_frame = f"{can_id:03X} [{dlc}] {data.hex(' ').upper()}"
                elif kind == "reader":
                    self.last_reader_t = now

    def _external_active(self, now: float) -> tuple[bool, bool]:
        ctrl = self.last_controller_t is not None and now - self.last_controller_t < EXTERNAL_QUIET_S
        reader = self.last_reader_t is not None and now - self.last_reader_t < EXTERNAL_QUIET_S
        return ctrl, reader

    def _tx_loop(self) -> None:
        period = 1.0 / POLL_HZ
        next_t = time.time()
        while not self.stop_evt.is_set():
            next_t += period
            now = time.time()
            with self.lock:
                ctrl, reader = self._external_active(now)
                ok = (
                    self.mode == "auto"
                    and self.sock is not None
                    and self.connected_at is not None
                    and now - self.connected_at > STARTUP_LISTEN_S
                    and not ctrl
                    and not reader  # another reader already polls; its replies reach us
                )
                self.transmitting = ok
                sock = self.sock
            if ok and sock is not None:
                for mid in MOTOR_IDS:
                    # Re-check between frames so we back off within one frame of
                    # seeing a controller.
                    with self.lock:
                        if self.mode != "auto" or self._external_active(time.time())[0]:
                            break
                    try:
                        sock.send(refresh_frame(mid))
                        with self.lock:
                            self.tx_total += 1
                            self.tx_rate.tick(time.time())
                    except OSError as e:
                        if e.errno == errno.ENOBUFS:
                            self._error("send: ENOBUFS (tx queue full)")
                        else:
                            self._error(f"send: {e}")
                        break
            sl = next_t - time.time()
            if sl > 0:
                time.sleep(sl)
            else:
                next_t = time.time()

    # ---- state
    def snapshot(self) -> dict:
        now = time.time()
        with self.lock:
            ctrl, reader = self._external_active(now)
            motors = []
            for (name, mid, mt, lim), m in zip(MOTORS, self.motors):
                d = {
                    "name": name,
                    "id": mid,
                    "model": mt["model"],
                    "limits": list(lim) if lim else None,
                    "age": (now - m["t"]) if m["t"] else None,
                    "rate": round(m["rate"].rate(now), 1),
                    "count": m["count"],
                }
                raw = m["raw"]
                if raw is not None:
                    state = raw[0] >> 4
                    pos16 = (raw[1] << 8) | raw[2]
                    vel12 = (raw[3] << 4) | (raw[4] >> 4)
                    tau12 = ((raw[4] & 0xF) << 8) | raw[5]
                    pos = u_to_f(pos16, -PMAX, PMAX, 16)
                    if state == 0:
                        sname = "disabled"
                    elif state == 1:
                        sname = "enabled"
                    elif state >= 8:
                        sname = "fault: " + FAULT_NAMES.get(state, f"code {state:#x}")
                    else:
                        sname = f"state {state}"
                    d.update(
                        raw=raw.hex(" ").upper(),
                        state=state,
                        state_name=sname,
                        fault=state >= 8,
                        pos=pos,
                        deg=pos * 180.0 / 3.141592653589793,
                        vel=u_to_f(vel12, -mt["vmax"], mt["vmax"], 12),
                        tau=u_to_f(tau12, -mt["tmax"], mt["tmax"], 12),
                        t_mos=raw[6],
                        t_rotor=raw[7],
                    )
                motors.append(d)
            bus = {
                "iface": self.iface,
                "connected": self.sock is not None,
                "mode": self.mode,
                "transmitting": self.transmitting,
                "external_controller": ctrl,
                "external_controller_age": (now - self.last_controller_t) if self.last_controller_t else None,
                "external_controller_frame": self.last_controller_frame,
                "external_reader": reader,
                "rx_fps": round(self.rx_rate.rate(now), 1),
                "tx_fps": round(self.tx_rate.rate(now), 1),
                "rx_total": self.rx_total,
                "tx_total": self.tx_total,
                "last_error": self.last_error,
                "last_error_age": (now - self.last_error_t) if self.last_error_t else None,
            }
        return {"t": now, "bus": bus, "motors": motors}


# --------------------------------------------------------------------------
# Camera (shared ffmpeg)
# --------------------------------------------------------------------------
class Camera:
    IDLE_STOP_S = 30.0

    def __init__(self, device: str, size: str):
        self.device = device
        self.size = size
        self.lock = threading.Lock()
        self.cond = threading.Condition(self.lock)
        self.proc: subprocess.Popen | None = None
        self.frame: bytes | None = None
        self.frame_t = 0.0
        self.seq = 0
        self.clients = 0
        self.last_use = 0.0
        self.error: str | None = None
        self.fps = RollingRate()
        threading.Thread(target=self._reaper, name="cam-reaper", daemon=True).start()

    def touch(self) -> None:
        with self.lock:
            self.last_use = time.time()
            if self.proc is None or self.proc.poll() is not None:
                self._start_locked()

    def _start_locked(self) -> None:
        if not os.path.exists(self.device):
            self.error = f"{self.device} not found"
            self.proc = None
            return
        cmd = [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin",
            "-f", "v4l2", "-input_format", "mjpeg", "-video_size", self.size,
            "-i", self.device,
            "-c:v", "copy", "-bsf:v", "mjpeg2jpeg", "-f", "mjpeg", "pipe:1",
        ]
        try:
            self.proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=0)
        except OSError as e:
            self.error = f"ffmpeg: {e}"
            self.proc = None
            return
        self.error = None
        self.frame = None
        threading.Thread(target=self._reader, args=(self.proc,), name="cam-reader", daemon=True).start()

    def _reader(self, proc: subprocess.Popen) -> None:
        buf = bytearray()
        assert proc.stdout is not None
        while True:
            chunk = proc.stdout.read(65536)
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
                jpg = bytes(buf[soi : eoi + 2])
                del buf[: eoi + 2]
                now = time.time()
                with self.cond:
                    self.frame = jpg
                    self.frame_t = now
                    self.seq += 1
                    self.fps.tick(now)
                    self.cond.notify_all()
        err = b""
        try:
            err = proc.stderr.read() if proc.stderr else b""
        except OSError:
            pass
        rc = proc.wait()
        with self.cond:
            if self.proc is proc:
                self.proc = None
                msg = err.decode(errors="replace").strip().splitlines()
                self.error = f"ffmpeg exited ({rc})" + (f": {msg[-1]}" if msg else "")
            self.cond.notify_all()

    def _reaper(self) -> None:
        while True:
            time.sleep(2.0)
            with self.lock:
                p = self.proc
                idle = self.clients == 0 and time.time() - self.last_use > self.IDLE_STOP_S
                if p is not None and p.poll() is None and idle:
                    self.proc = None
                    self.error = None
                else:
                    p = None
            if p is not None:
                p.terminate()
                try:
                    p.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    p.kill()

    def wait_frame(self, after_seq: int, timeout: float) -> tuple[bytes | None, int]:
        with self.cond:
            self.cond.wait_for(lambda: self.seq > after_seq or self.proc is None, timeout=timeout)
            return (self.frame if self.seq > after_seq else None), self.seq

    def latest(self, max_wait: float = 5.0) -> bytes | None:
        self.touch()
        with self.cond:
            self.cond.wait_for(
                lambda: (self.frame is not None and time.time() - self.frame_t < 2.0) or self.proc is None,
                timeout=max_wait,
            )
            return self.frame

    def status(self) -> dict:
        now = time.time()
        with self.lock:
            return {
                "device": self.device,
                "running": self.proc is not None and self.proc.poll() is None,
                "clients": self.clients,
                "fps": round(self.fps.rate(now), 1),
                "error": self.error,
            }


# --------------------------------------------------------------------------
# Markdown file helpers
# --------------------------------------------------------------------------
CHECKBOX_RE = re.compile(r"^(\s*(?:[-*+]|\d+[.)])\s+)\[([ xX])\]")
FENCE_RE = re.compile(r"^\s*(```|~~~)")


def mtime(p: Path) -> float | None:
    try:
        return p.stat().st_mtime
    except OSError:
        return None


def atomic_write(p: Path, text: str) -> None:
    fd, tmp = tempfile.mkstemp(dir=p.parent, prefix=".tmp-", suffix=p.suffix)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, p)


def toggle_checkbox(text: str, index: int, checked: bool) -> str:
    lines = text.split("\n")
    n = 0
    in_fence = False
    for i, line in enumerate(lines):
        if FENCE_RE.match(line):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        m = CHECKBOX_RE.match(line)
        if m:
            if n == index:
                mark = "x" if checked else " "
                lines[i] = line[: m.start(2)] + mark + line[m.end(2) :]
                return "\n".join(lines)
            n += 1
    raise IndexError(index)


# --------------------------------------------------------------------------
# App
# --------------------------------------------------------------------------
def build_app(args) -> FastAPI:
    can = CanMonitor(args.iface, args.mode)
    cam = Camera(args.camera, args.camera_size)
    log_path = Path(args.log).resolve()
    urdf_dir = Path(args.urdf_dir)
    file_lock = threading.Lock()

    @contextlib.asynccontextmanager
    async def lifespan(_app: FastAPI):
        can.start()
        yield
        can.stop()

    app = FastAPI(title="OpenYAM lab console", lifespan=lifespan)

    def full_state() -> dict:
        s = can.snapshot()
        s["camera"] = cam.status()
        s["coach_mtime"] = mtime(COACH_PATH)
        s["log_mtime"] = mtime(log_path)
        s["log_path"] = str(log_path.relative_to(REPO)) if log_path.is_relative_to(REPO) else str(log_path)
        return s

    @app.get("/api/state")
    def api_state():
        return JSONResponse(full_state())

    @app.get("/api/stream")
    async def api_stream(request: Request):
        async def gen():
            yield "retry: 1000\n\n"
            while True:
                if await request.is_disconnected():
                    break
                yield f"data: {json.dumps(full_state())}\n\n"
                await asyncio.sleep(0.05)

        return StreamingResponse(
            gen(), media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.post("/api/mode")
    async def api_mode(body: dict):
        mode = body.get("mode")
        if mode not in ("auto", "listen"):
            raise HTTPException(400, "mode must be 'auto' or 'listen'")
        with can.lock:
            can.mode = mode
            if mode == "listen":
                can.transmitting = False
        return {"mode": mode}

    # ---- coach / inbox / log
    def md_response(p: Path) -> Response:
        try:
            text = p.read_text(encoding="utf-8")
        except FileNotFoundError:
            text = f"_({p.name} not found: {p})_\n"
        return PlainTextResponse(
            text, media_type="text/markdown; charset=utf-8",
            headers={"X-Mtime": str(mtime(p) or 0), "Cache-Control": "no-store"},
        )

    @app.get("/coach")
    def coach():
        return md_response(COACH_PATH)

    @app.post("/api/coach/check")
    def coach_check(body: dict):
        try:
            index = int(body["index"])
            checked = bool(body["checked"])
        except (KeyError, TypeError, ValueError):
            raise HTTPException(400, "need {index:int, checked:bool}")
        with file_lock:
            try:
                text = COACH_PATH.read_text(encoding="utf-8")
                new = toggle_checkbox(text, index, checked)
            except FileNotFoundError:
                raise HTTPException(404, "coach.md missing")
            except IndexError:
                raise HTTPException(404, f"no checkbox #{index}")
            atomic_write(COACH_PATH, new)
        return {"ok": True, "mtime": mtime(COACH_PATH)}

    @app.post("/api/inbox")
    def inbox(body: dict):
        text = str(body.get("text", "")).strip()
        if not text:
            raise HTTPException(400, "empty note")
        text = " ".join(text.splitlines())
        stamp = dt.datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
        with file_lock:
            new_file = not INBOX_PATH.exists()
            with INBOX_PATH.open("a", encoding="utf-8") as f:
                if new_file:
                    f.write("# Inbox: notes from the human to Claude\n\n")
                f.write(f"- {stamp}: {text}\n")
        return {"ok": True, "stamp": stamp}

    @app.get("/log")
    def log():
        return md_response(log_path)

    # ---- camera
    @app.get("/camera.jpg")
    def camera_jpg():
        frame = cam.latest()
        if frame is None:
            raise HTTPException(503, cam.status()["error"] or "no frame yet")
        return Response(frame, media_type="image/jpeg", headers={"Cache-Control": "no-store"})

    @app.get("/camera.mjpg")
    async def camera_mjpg(request: Request):
        cam.touch()
        if not cam.status()["running"]:
            raise HTTPException(503, cam.status()["error"] or "camera not running")
        with cam.lock:
            cam.clients += 1
        boundary = "frame"

        async def gen():
            seq = 0
            try:
                while not await request.is_disconnected():
                    frame, seq = await asyncio.to_thread(cam.wait_frame, seq, 1.0)
                    if frame is None:
                        if not cam.status()["running"]:
                            break
                        continue
                    cam.last_use = time.time()
                    yield (
                        f"--{boundary}\r\nContent-Type: image/jpeg\r\nContent-Length: {len(frame)}\r\n\r\n".encode()
                        + frame + b"\r\n"
                    )
            finally:
                with cam.lock:
                    cam.clients -= 1
                    cam.last_use = time.time()

        return StreamingResponse(
            gen(), media_type=f"multipart/x-mixed-replace; boundary={boundary}",
            headers={"Cache-Control": "no-store"},
        )

    # ---- phone cameras (proxy to dashboard/phonecam.py's local API)
    def phonecam_get(path: str) -> tuple[int, bytes, str]:
        try:
            with urllib.request.urlopen(args.phonecam.rstrip("/") + path, timeout=2) as r:
                return r.status, r.read(), r.headers.get("Content-Type", "application/octet-stream")
        except urllib.error.HTTPError as e:
            return e.code, e.read(), e.headers.get("Content-Type", "text/plain")
        except (urllib.error.URLError, OSError) as e:
            return 502, json.dumps({"detail": f"phonecam not reachable: {e}"}).encode(), "application/json"

    def loopback_only(request: Request) -> None:
        # The phone token gives control of the arm's phone programs: only hand it (as links or
        # QR codes) to a browser on this laptop, even if the console runs with --host 0.0.0.0.
        if (request.client.host if request.client else "") not in LOOPBACK:
            raise HTTPException(403, "phone links and QR codes are only shown on the laptop itself (127.0.0.1)")

    @app.get("/phonecam/cameras")
    async def phonecam_cameras():
        code, body, ctype = await asyncio.to_thread(phonecam_get, "/cameras")
        if code == 200:
            with contextlib.suppress(ValueError, AttributeError):
                d = json.loads(body)
                d.pop("phone_url", None)  # contains the token; the page doesn't need it
                for c in d.get("cameras", []):
                    c.pop("latest_file", None)
                body = json.dumps(d).encode()
        return Response(body, status_code=code, media_type=ctype, headers={"Cache-Control": "no-store"})

    @app.get("/phonecam/cam/{name}.jpg")
    async def phonecam_jpg(name: str):
        if not re.match(r"^[a-z0-9][a-z0-9_-]{0,31}$", name):
            raise HTTPException(400, "bad name")
        code, body, ctype = await asyncio.to_thread(phonecam_get, f"/cam/{name}.jpg")
        return Response(body, status_code=code, media_type=ctype, headers={"Cache-Control": "no-store"})

    @app.get("/phonecam/qr.png")
    async def phonecam_qr(request: Request):
        loopback_only(request)
        code, body, ctype = await asyncio.to_thread(phonecam_get, "/qr.png")
        return Response(body, status_code=code, media_type=ctype, headers={"Cache-Control": "no-store"})

    # ---- phones page: links + QR codes for phonecam's pages (token from certs/phone-url.txt)
    def phone_base() -> str | None:
        try:
            url = PHONE_URL_FILE.read_text(encoding="utf-8").strip()
        except OSError:
            return None
        m = re.match(r"^(https://[^/?#\s]+)/\?t=([A-Za-z0-9_-]+)$", url)
        return url if m else None

    def phone_link(page: str) -> str | None:
        url = phone_base()
        if url is None or page not in PHONE_PAGES:
            return None
        origin, query = url.split("/?", 1)
        return f"{origin}{PHONE_PAGES[page][0]}?{query}"

    @app.get("/api/phones")
    async def api_phones(request: Request):
        loopback_only(request)
        code, _, _ = await asyncio.to_thread(phonecam_get, "/cameras")
        links = {k: phone_link(k) for k in PHONE_PAGES}
        url = phone_base()
        return JSONResponse({
            "phonecam_running": code == 200,
            "url_file": str(PHONE_URL_FILE.relative_to(REPO)),
            "url_file_found": url is not None,
            "origin": url.split("/?", 1)[0] if url else None,  # https://<laptop-ip>:8443, no token
            "pages": [{"key": k, "path": p, "title": t, "desc": d, "link": links[k]} for k, (p, t, d) in PHONE_PAGES.items()],
        }, headers={"Cache-Control": "no-store"})

    @app.get("/api/phones/qr/{page}.png")
    def api_phone_qr(page: str, request: Request):
        loopback_only(request)
        link = phone_link(page)
        if link is None:
            raise HTTPException(404, "no phone URL yet: start dashboard/phonecam.py")
        try:
            import io

            import qrcode
        except ImportError:
            raise HTTPException(404, "qrcode not installed in .venv")
        qr = qrcode.QRCode(border=2, box_size=8)
        qr.add_data(link)
        qr.make(fit=True)
        buf = io.BytesIO()
        qr.make_image().save(buf)
        return Response(buf.getvalue(), media_type="image/png", headers={"Cache-Control": "no-store"})

    # ---- armctl daemon (programs, lease, E-STOP): proxy to armctl/daemon.py's loopback API
    ARMCTL_POST = {"lease", "lease/deny", "estop", "estop/reset", "enable", "playable/confirm", "playable/cancel"}

    def armctl_call(method: str, path: str, body: bytes | None) -> tuple[int, bytes, str]:
        req = urllib.request.Request(args.armctl.rstrip("/") + "/" + path, data=body, method=method,
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, r.read(), r.headers.get("Content-Type", "application/json")
        except urllib.error.HTTPError as e:
            return e.code, e.read(), e.headers.get("Content-Type", "text/plain")
        except (urllib.error.URLError, OSError) as e:
            return 502, json.dumps({"detail": f"armctl daemon not reachable: {e}"}).encode(), "application/json"

    @app.get("/api/config")
    def api_config():
        # Where the page's own armctl program connection ("Try on sim") goes.
        ws = re.sub(r"^http", "ws", args.armctl.rstrip("/")) + "/armctl"
        return {"armctl_ws": ws}

    @app.get("/armctl/status")
    async def armctl_status():
        code, body, ctype = await asyncio.to_thread(armctl_call, "GET", "status", None)
        return Response(body, status_code=code, media_type=ctype, headers={"Cache-Control": "no-store"})

    @app.post("/armctl/{path:path}")
    async def armctl_post(path: str, request: Request):
        if path not in ARMCTL_POST:
            raise HTTPException(404, "unknown armctl endpoint")
        raw = await request.body()
        code, body, ctype = await asyncio.to_thread(armctl_call, "POST", path, raw or b"{}")
        return Response(body, status_code=code, media_type=ctype, headers={"Cache-Control": "no-store"})

    # ---- static
    if urdf_dir.is_dir():
        app.mount("/urdf", StaticFiles(directory=urdf_dir), name="urdf")

    # ---- playables (read-only list/load/sample + validate-only POST; never moves anything)
    add_playable_routes(app)

    # ---- pages
    def page(name: str):
        return lambda: FileResponse(STATIC_DIR / name, headers={"Cache-Control": "no-store"})

    app.get("/")(page("index.html"))
    app.get("/playables")(page("playable-preview.html"))
    app.get("/phones")(page("phones.html"))
    app.get("/sim")(page("sim.html"))

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    return app


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8090)
    ap.add_argument("--iface", default="can0", help="SocketCAN interface")
    ap.add_argument("--mode", choices=["auto", "listen"], default="auto", help="start mode")
    ap.add_argument("--log", default=str(DEFAULT_LOG), help="experiment LOG.md to show")
    ap.add_argument("--urdf-dir", default=str(DEFAULT_URDF_DIR), help="yam_description directory")
    ap.add_argument("--camera", default="/dev/video0")
    ap.add_argument("--camera-size", default="1280x720")
    ap.add_argument("--phonecam", default="http://127.0.0.1:8444", help="phonecam.py local API base URL")
    ap.add_argument("--armctl", default="http://127.0.0.1:8091", help="armctl daemon operator API base URL")
    args = ap.parse_args()
    app = build_app(args)
    print(f"OpenYAM lab console on http://{args.host}:{args.port}  (CAN {args.iface}, mode {args.mode})")
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning", timeout_graceful_shutdown=1)


if __name__ == "__main__":
    main()
