#!/usr/bin/env python3
"""Phone cameras for the OpenYAM lab: phones on the Wi-Fi stream JPEG frames to the laptop.

Run:  .venv/bin/python dashboard/phonecam.py

Two listeners in one process:
  * https://0.0.0.0:8443  phone-facing. Self-signed cert (dashboard/certs/), every
    route needs the secret token (?t=<token>, then a cookie). Serves the phone page
    and accepts frames: POST /api/frame/<name> with an image/jpeg body.
    Also the armctl phone programs: GET /tilt, GET /joystick, and WebSocket /armctl,
    a relay to the local armctl daemon (ws://127.0.0.1:8091/armctl, --armctl), since
    phones can't reach 127.0.0.1 and an https page can't open ws://.
  * http://127.0.0.1:8444 local only, no token, for Claude and the console:
      GET /cameras            JSON list with ages
      GET /cam/<name>.jpg     latest frame
      GET /cam/<name>.mjpg    live MJPEG
      GET /qr.png             QR code of the phone URL
      GET /phone-url          {"url": ...}

Snapshots: every 2 s per camera to experiments/<exp>/media/phonecam/<name>/latest.jpg,
plus a timestamped copy at most once a minute.
"""
from __future__ import annotations

import argparse
import asyncio
import collections
import datetime as dt
import hmac
import io
import ipaddress
import json
import os
import re
import secrets
import socket
import subprocess
import threading
import time
from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, Response, StreamingResponse

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
CERT_DIR = HERE / "certs"
CERT = CERT_DIR / "cert.pem"
KEY = CERT_DIR / "key.pem"
SANS_FILE = CERT_DIR / "sans.txt"
TOKEN_FILE = CERT_DIR / "token"
QR_PNG = CERT_DIR / "phone-qr.png"
URL_FILE = CERT_DIR / "phone-url.txt"
PHONE_PAGE = HERE / "static" / "phone.html"
# armctl phone programs (served here because phones need HTTPS for motion sensors, and an
# https page can't open ws:// to the laptop; /armctl relays to the local armctl daemon).
PROGRAM_PAGES = {"tilt": HERE / "static" / "tilt.html", "joystick": HERE / "static" / "joystick.html"}
ARMCTL_WS = "ws://127.0.0.1:8091/armctl"
DEFAULT_MEDIA = REPO / "experiments" / "2026-10-16-linux-real-arm" / "media" / "phonecam"
ALWAYS_IPS = ["192.168.100.94"]

MAX_UPLOAD = 4 * 1024 * 1024
MAX_CAMERAS = 8
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")
COOKIE = "pc_token"
SNAP_EVERY_S = 2.0
ARCHIVE_EVERY_S = 60.0


# --------------------------------------------------------------------------
# Network, cert, token, QR
# --------------------------------------------------------------------------
def local_ips() -> list[str]:
    ips: list[str] = []
    try:
        out = subprocess.run(["ip", "-4", "-o", "addr"], capture_output=True, text=True, timeout=3).stdout
        for line in out.splitlines():
            parts = line.split()
            if len(parts) >= 4:
                ip = parts[3].split("/")[0]
                if not ipaddress.ip_address(ip).is_loopback:
                    ips.append(ip)
    except (OSError, subprocess.SubprocessError, ValueError):
        pass
    return ips


def primary_ip() -> str:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))  # no packet is sent
        return s.getsockname()[0]
    except OSError:
        return ALWAYS_IPS[0]
    finally:
        s.close()


def ensure_cert(ips: list[str]) -> None:
    CERT_DIR.mkdir(exist_ok=True)
    want = sorted(set(ips + ALWAYS_IPS + ["127.0.0.1"]))
    have = SANS_FILE.read_text().split() if SANS_FILE.exists() else []
    if CERT.exists() and KEY.exists() and set(want) <= set(have):
        return
    san = ",".join([f"IP:{ip}" for ip in want] + ["DNS:localhost"])
    subprocess.run(
        [
            "openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-sha256",
            "-keyout", str(KEY), "-out", str(CERT), "-days", "365",
            "-subj", "/CN=openyam-phonecam", "-addext", f"subjectAltName={san}",
        ],
        check=True, capture_output=True,
    )
    os.chmod(KEY, 0o600)
    SANS_FILE.write_text("\n".join(want) + "\n")
    print(f"generated self-signed cert for {san}")


def ensure_token() -> str:
    CERT_DIR.mkdir(exist_ok=True)
    if TOKEN_FILE.exists():
        tok = TOKEN_FILE.read_text().strip()
        if tok:
            return tok
    tok = secrets.token_urlsafe(16)
    TOKEN_FILE.write_text(tok + "\n")
    os.chmod(TOKEN_FILE, 0o600)
    return tok


def make_qr(url: str, show_ascii: bool) -> None:
    try:
        import qrcode
    except ImportError:
        print("(install `qrcode` into .venv for a QR code)")
        return
    qr = qrcode.QRCode(border=1)
    qr.add_data(url)
    qr.make(fit=True)
    try:
        qr.make_image().save(QR_PNG)
    except Exception as e:  # PIL missing etc.
        print(f"(could not write {QR_PNG}: {e})")
    if show_ascii:
        buf = io.StringIO()
        qr.print_ascii(out=buf, invert=True)
        print(buf.getvalue())


# --------------------------------------------------------------------------
# Frame store
# --------------------------------------------------------------------------
class Rate:
    def __init__(self, window: float = 3.0):
        self.window = window
        self.ts: collections.deque[float] = collections.deque()

    def tick(self, t: float) -> None:
        self.ts.append(t)
        while self.ts and t - self.ts[0] > self.window:
            self.ts.popleft()

    def rate(self, now: float) -> float:
        while self.ts and now - self.ts[0] > self.window:
            self.ts.popleft()
        return len(self.ts) / self.window


STALE_AFTER = 60.0  # seconds without a frame before a camera is dropped


class Store:
    def __init__(self, media_dir: Path):
        self.media_dir = media_dir
        self.lock = threading.Lock()
        self.cams: dict[str, dict] = {}

    def put(self, name: str, jpg: bytes, client: str, ua: str) -> None:
        now = time.time()
        with self.lock:
            c = self.cams.get(name)
            if c is None:
                if len(self.cams) >= MAX_CAMERAS:
                    # Drop the stalest camera to make room.
                    oldest = min(self.cams, key=lambda k: self.cams[k]["t"])
                    del self.cams[oldest]
                c = self.cams[name] = {"seq": 0, "rate": Rate(), "saved_seq": 0, "saved_t": 0.0, "archived_t": 0.0}
            c.update(jpg=jpg, t=now, client=client, ua=ua[:120])
            c["seq"] += 1
            c["rate"].tick(now)

    def get(self, name: str) -> tuple[bytes | None, int]:
        with self.lock:
            c = self.cams.get(name)
            return (c["jpg"], c["seq"]) if c else (None, 0)

    def remove(self, name: str) -> bool:
        with self.lock:
            return self.cams.pop(name, None) is not None

    def prune(self, max_age: float = STALE_AFTER) -> None:
        """Forget cameras that stopped sending (renamed or closed phones)."""
        now = time.time()
        with self.lock:
            for n in [n for n, c in self.cams.items() if now - c["t"] > max_age]:
                del self.cams[n]

    def listing(self) -> list[dict]:
        self.prune()
        now = time.time()
        with self.lock:
            return [
                {
                    "name": n,
                    "age": round(now - c["t"], 2),
                    "fps": round(c["rate"].rate(now), 1),
                    "bytes": len(c["jpg"]),
                    "frames": c["seq"],
                    "client": c["client"],
                    "ua": c["ua"],
                    "latest_file": str(self.media_dir / n / "latest.jpg"),
                }
                for n, c in sorted(self.cams.items())
            ]

    def saver(self) -> None:
        while True:
            time.sleep(SNAP_EVERY_S)
            now = time.time()
            with self.lock:
                todo = []
                for n, c in self.cams.items():
                    if c["seq"] != c["saved_seq"]:
                        archive = now - c["archived_t"] >= ARCHIVE_EVERY_S
                        todo.append((n, c["jpg"], c["t"], archive))
                        c["saved_seq"] = c["seq"]
                        if archive:
                            c["archived_t"] = now
            for n, jpg, t, archive in todo:
                try:
                    d = self.media_dir / n
                    d.mkdir(parents=True, exist_ok=True)
                    tmp = d / ".latest.jpg.tmp"
                    tmp.write_bytes(jpg)
                    os.replace(tmp, d / "latest.jpg")
                    if archive:
                        stamp = dt.datetime.fromtimestamp(t).strftime("%Y%m%d-%H%M%S")
                        (d / f"{stamp}.jpg").write_bytes(jpg)
                except OSError as e:
                    print(f"snapshot {n}: {e}")


# --------------------------------------------------------------------------
# Apps
# --------------------------------------------------------------------------
def valid_name(name: str) -> str:
    name = name.strip().lower()
    if not NAME_RE.match(name):
        raise HTTPException(400, "camera name: 1-32 chars of a-z 0-9 _ -")
    return name


def mjpeg_response(store: Store, name: str, request: Request) -> StreamingResponse:
    async def gen():
        seq = 0
        last = time.time()
        while not await request.is_disconnected():
            jpg, s = store.get(name)
            if jpg is not None and s != seq:
                seq = s
                last = time.time()
                yield (
                    f"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: {len(jpg)}\r\n\r\n".encode()
                    + jpg + b"\r\n"
                )
            elif time.time() - last > 60:
                break
            await asyncio.sleep(0.05)

    return StreamingResponse(gen(), media_type="multipart/x-mixed-replace; boundary=frame",
                             headers={"Cache-Control": "no-store"})


def build_phone_app(store: Store, token: str, armctl_ws: str = ARMCTL_WS) -> FastAPI:
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    @app.middleware("http")
    async def auth(request: Request, call_next):
        q = request.query_params.get("t", "")
        c = request.cookies.get(COOKIE, "")
        ok_q = bool(q) and hmac.compare_digest(q, token)
        ok_c = bool(c) and hmac.compare_digest(c, token)
        if not (ok_q or ok_c):
            return PlainTextResponse("403: missing or wrong token. Scan the QR code on the lab console.", status_code=403)
        resp = await call_next(request)
        if ok_q and not ok_c:
            resp.set_cookie(COOKIE, token, max_age=30 * 86400, httponly=True, secure=True, samesite="strict")
        return resp

    @app.get("/")
    def page():
        return HTMLResponse(PHONE_PAGE.read_text(encoding="utf-8"), headers={"Cache-Control": "no-store"})

    @app.get("/tilt")
    @app.get("/joystick")
    def program_page(request: Request):
        page = PROGRAM_PAGES[request.url.path.strip("/")]
        return HTMLResponse(page.read_text(encoding="utf-8"), headers={"Cache-Control": "no-store"})

    @app.websocket("/armctl")
    async def armctl_relay(ws: WebSocket):
        # The http middleware above does not run for websockets: check the token here.
        q, c = ws.query_params.get("t", ""), ws.cookies.get(COOKIE, "")
        if not ((q and hmac.compare_digest(q, token)) or (c and hmac.compare_digest(c, token))):
            await ws.close(code=4403)
            return
        origin, host = ws.headers.get("origin", ""), ws.headers.get("host", "")
        if origin and origin.split("://", 1)[-1] != host:
            await ws.close(code=4403)  # cross-site page
            return
        await ws.accept()
        import websockets  # only needed for the relay

        phone_ip = ws.client.host if ws.client else "?"
        try:
            up = await websockets.connect(armctl_ws, max_size=300_000, open_timeout=3,
                                          additional_headers={"X-Armctl-Via": "phone", "X-Forwarded-For": phone_ip})
        except Exception as e:
            await ws.send_text(json.dumps({"op": "refused", "reason": f"armctl daemon not reachable ({type(e).__name__})"}))
            await ws.close(code=1011)
            return

        async def down():  # daemon -> phone
            async for m in up:
                await ws.send_text(m if isinstance(m, str) else m.decode())

        async def upstream():  # phone -> daemon
            while True:
                m = await ws.receive_text()
                if len(m) <= 300_000:
                    await up.send(m)

        tasks = [asyncio.create_task(down()), asyncio.create_task(upstream())]
        try:
            await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        except (WebSocketDisconnect, Exception):
            pass
        finally:
            for t in tasks:
                t.cancel()
            await up.close()  # the daemon treats this as a disconnect: holder -> stop
            try:
                await ws.close()
            except Exception:
                pass

    @app.post("/api/frame/{name}")
    async def frame(name: str, request: Request):
        name = valid_name(name)
        ctype = request.headers.get("content-type", "").split(";")[0].strip().lower()
        if ctype != "image/jpeg":
            raise HTTPException(415, "only image/jpeg")
        clen = request.headers.get("content-length")
        if clen and clen.isdigit() and int(clen) > MAX_UPLOAD:
            raise HTTPException(413, "frame too large")
        buf = bytearray()
        async for chunk in request.stream():
            buf += chunk
            if len(buf) > MAX_UPLOAD:
                raise HTTPException(413, "frame too large")
        if len(buf) < 4 or buf[:3] != b"\xff\xd8\xff":
            raise HTTPException(415, "body is not a JPEG")
        client = request.client.host if request.client else "?"
        store.put(name, bytes(buf), client, request.headers.get("user-agent", ""))
        return {"ok": True, "bytes": len(buf)}

    @app.get("/api/cameras")
    def cams():
        return {"cameras": [{k: v for k, v in c.items() if k in ("name", "age", "fps")} for c in store.listing()]}

    @app.get("/favicon.ico")
    def favicon():
        return Response(status_code=204)

    return app


def build_local_app(store: Store, phone_url: str) -> FastAPI:
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    @app.get("/cameras")
    def cameras():
        return JSONResponse({"cameras": store.listing(), "phone_url": phone_url})

    @app.get("/cam/{name}.jpg")
    def cam_jpg(name: str):
        jpg, _ = store.get(valid_name(name))
        if jpg is None:
            raise HTTPException(404, "no such camera")
        return Response(jpg, media_type="image/jpeg", headers={"Cache-Control": "no-store"})

    @app.delete("/cam/{name}")
    def cam_delete(name: str):
        return {"removed": store.remove(valid_name(name))}

    @app.get("/cam/{name}.mjpg")
    def cam_mjpg(name: str, request: Request):
        name = valid_name(name)
        if store.get(name)[0] is None:
            raise HTTPException(404, "no such camera")
        return mjpeg_response(store, name, request)

    @app.get("/qr.png")
    def qr():
        if not QR_PNG.exists():
            raise HTTPException(404, "no QR (qrcode not installed)")
        return Response(QR_PNG.read_bytes(), media_type="image/png", headers={"Cache-Control": "no-store"})

    @app.get("/phone-url")
    def url():
        return {"url": phone_url}

    return app


async def serve(args) -> None:
    ips = local_ips()
    ensure_cert(ips)
    token = ensure_token()
    host_ip = args.public_ip or primary_ip()
    phone_url = f"https://{host_ip}:{args.port}/?t={token}"
    URL_FILE.write_text(phone_url + "\n")
    os.chmod(URL_FILE, 0o600)
    # The token is a secret: only show it on an interactive terminal, never in a
    # redirected log (experiment logs get committed).
    tty = os.isatty(1)
    shown = phone_url if tty else f"https://{host_ip}:{args.port}/?t=<token: see {URL_FILE.relative_to(REPO)} or the console QR>"
    print(f"Phone URL: {shown}")
    print(f"(also reachable on: {', '.join(ips) or 'none'})")
    make_qr(phone_url, show_ascii=tty)
    print(f"Phone programs: /tilt and /joystick (same token), relaying to {args.armctl}")
    print(f"Local API: http://127.0.0.1:{args.local_port}/cameras   QR: {QR_PNG}")

    store = Store(Path(args.media_dir))
    threading.Thread(target=store.saver, name="saver", daemon=True).start()

    phone = uvicorn.Server(uvicorn.Config(
        build_phone_app(store, token, args.armctl), host=args.host, port=args.port,
        ssl_certfile=str(CERT), ssl_keyfile=str(KEY), log_level="warning",
        timeout_graceful_shutdown=1,
    ))
    local = uvicorn.Server(uvicorn.Config(
        build_local_app(store, phone_url), host="127.0.0.1", port=args.local_port,
        log_level="warning", timeout_graceful_shutdown=1,
    ))
    await asyncio.gather(phone.serve(), local.serve())


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", default="0.0.0.0", help="phone-facing HTTPS bind address")
    ap.add_argument("--port", type=int, default=8443)
    ap.add_argument("--local-port", type=int, default=8444, help="127.0.0.1-only HTTP API")
    ap.add_argument("--public-ip", default=None, help="IP to put in the phone URL (default: detected)")
    ap.add_argument("--media-dir", default=str(DEFAULT_MEDIA))
    ap.add_argument("--armctl", default=ARMCTL_WS, help="armctl daemon WebSocket that /armctl relays to")
    args = ap.parse_args()
    asyncio.run(serve(args))


if __name__ == "__main__":
    main()
