# Lab web app (dashboard)

One local web app for working at the OpenYAM arm, served by the lab console on http://127.0.0.1:8090. Every page shares the top navigation (Console · Playables · Phones · Sim) and the same light/dark theme (follows the system; the ◐ button cycles auto/light/dark and is remembered per browser).

| Page | URL | What it's for |
|---|---|---|
| Console | `/` | Coach panel (Claude writes `coach.md`, you tick its checkboxes), **Programs** (armctl lease, approvals, E-STOP, refusals), **Playables**, the **real arm** in 3D (live CAN) next to the **simulated arm** (armctl mock) in its own view, joint gauges, USB camera, CAN bus status, phone-camera feeds, experiment log |
| Playables | `/playables` | 3D preview of `playables/*.json`: play/scrub, timeline with violation bands, per-joint table, paste-and-validate. Nothing here moves anything |
| Phones | `/phones` | QR codes and links for the three phone pages (camera, tilt, joystick), first-time steps, and the cameras that are streaming |
| Sim | `/sim` | The simulated arm fullscreen, with who is driving it (for a second screen) |

The phone pages themselves (`phone.html` camera, `tilt.html`, `joystick.html`) are served by `phonecam.py` on HTTPS :8443 and share a small header that switches between them.

Query flags: `?nocam` (console: USB camera off at start), `?nophones` (console and Phones page: never load live phone-camera frames, e.g. for screenshots or screen sharing). `/playables?p=<name>` opens one playable.

## Run

```bash
dashboard/start-all.sh           # starts whatever isn't running, in tmux session "claude", prints the URLs
dashboard/start-all.sh --check   # only report
```

`start-all.sh` starts three servers, each in its own tmux window (`console`, `phonecam`, `armctl`), and skips any whose port is already listening:

| Server | Port | Command |
|---|---|---|
| lab console | 8090 (127.0.0.1) | `.venv/bin/python dashboard/server.py` |
| armctl daemon, **mock backend** | 8091 (127.0.0.1) | `.venv/bin/python -m armctl.daemon --backend mock` |
| phonecam | 8443 HTTPS for phones, 8444 local | `.venv/bin/python dashboard/phonecam.py` |

It never starts DimOS or anything that drives the real arm. It prints where the phone URL is saved (`dashboard/certs/phone-url.txt`) but not the URL, because the URL contains the access token.

Console options: `--host 0.0.0.0 --mode listen --log experiments/<exp>/LOG.md`, `--armctl http://127.0.0.1:8091`, `--phonecam http://127.0.0.1:8444`, `--iface can0`. Needs `can0` up (`experiments/2026-10-16-linux-real-arm/can0-up.sh`), ffmpeg for the camera, and DimOS's `yam_description` data (`--urdf-dir`, default `~/.local/share/dimos/repo/data/yam_description`). The pages load three.js, urdf-loader and marked from cdn.jsdelivr.net, so the browser needs internet.

Files: `coach.md` (instructions shown at the top), `inbox.md` (notes typed into "Note to Claude", timestamped), `static/` (frontend: `style.css` + `theme.js` shared by all console pages, `arm3d.js` shared 3D view, `app.js` console, `playable-preview.*`, `phones.html`, `sim.html`, and the phone pages).

### Playables in the console

The console serves the playable API itself (`add_playable_routes(app)` from `playable_preview_server.py`): `GET /api/playables`, `GET /api/playables/<name>` (playable, validation, 50 Hz samples) and `POST /api/playables/validate` (validate only, nothing is saved). The **Playables** panel lists each one with its duration, author and valid/invalid badge, and has:

- **Preview** → `/playables?p=<name>`.
- **Try on sim** (only when the armctl daemon runs the mock backend, and only from the laptop): the page connects to the daemon as program `console` and runs the normal flow: validate → ask for the lease (press **Approve** in Programs) → `playable.play` → press **Play** in Programs. The orange simulated arm moves; the real arm doesn't.
- The real-arm command to copy, `experiments/2026-10-16-linux-real-arm/session_play.sh <name>`. **Real arm: run it from a terminal with the human's go-ahead.** Nothing in the web app moves the real arm.

`playable_preview_server.py` still runs standalone (`.venv/bin/python dashboard/playable_preview_server.py` → http://127.0.0.1:8092/, same page, same-origin API) but isn't needed any more.

### Programs panel

Refusals are listed newest first (at most 20) with relative times; those from before the page was opened are faded under "before this page was opened". **Clear** hides the current list in this browser only; the daemon keeps its log.

## Safety

The console server can only ever transmit one kind of CAN frame: the Damiao read-only refresh request (`0x7FF  [id 00 CC 00 00 00 00 00]`). It never enables, disables, zeroes or commands a motor.

- **auto** mode polls all 7 motors at 10 Hz, but only while no other process is using the bus: if it sees motor command frames from anyone else (0x001-0x008, 0x100-0x108, non-refresh 0x7FF) in the last 1 s it goes silent and shows "external controller (DimOS?) detected". If another reader is already polling, it stays silent and decodes those replies.
- **listen-only** never transmits. Use it whenever DimOS is running if you want to be strict.

In both modes it decodes every motor reply on the bus, so it works as a passive monitor while DimOS drives the arm. Velocity and torque are approximate (assumed DM4340/DM4310 ranges); the gripper is shown as raw motor radians and its 3D finger mapping is a rough guess.

Web app safety notes:

- Anyone who can reach the console can press E-STOP, approve leases and confirm playables. Keep it on 127.0.0.1 (the default).
- The armctl daemon currently runs the **mock** backend: phone programs, "Try on sim" and the Sim views drive a simulated arm only. "Try on sim" is disabled unless the daemon reports `backend: mock`, and it still goes through the daemon's lease, operator approval, envelope check and Play confirmation.
- The real arm is driven only from a terminal (`session_play.sh`, `play_real.py`, `armctl_real.py`) with the human's go-ahead. No page starts DimOS.
- Phone pages move the (simulated) arm only while a HOLD button or a stick is held. Tilt also maps lifting/lowering the phone to up/down (z): the accelerometer's world-vertical acceleration goes through a leaky integrator (τ 0.35 s, deadband 0.15 m/s²); a brisk lift gives a short burst of +z, a still phone gives none. The toggle "Up/down from lifting the phone" turns it off; the ▲/▼ buttons still work.

## Phone cameras

`dashboard/phonecam.py` lets phones on the same Wi-Fi act as extra cameras, using only a web page (no app).

```bash
.venv/bin/python dashboard/phonecam.py     # HTTPS 0.0.0.0:8443 for phones, HTTP 127.0.0.1:8444 for local use
```

- On the first run it creates a self-signed cert for the laptop's IPs and a random token in `dashboard/certs/` (gitignored). The cert is regenerated when the IPs change. The phone URL is `https://<laptop-ip>:8443/?t=<token>`. It is shown on the console's **Phones** page, and saved in `dashboard/certs/phone-url.txt` and `phone-qr.png`. The token is printed only to an interactive terminal, never to a redirected log. Delete `certs/token` and restart to rotate it.
- On the phone: scan the QR code, accept the certificate warning, pick a name (side, top, ...), tap Start and allow the camera. The page sends JPEG frames at 1, 3 or 10 fps and keeps the screen awake where the browser supports it. It works in iOS Safari and Android Chrome.
- Every phone route needs the token (`?t=`, after which a cookie is set). Uploads must be `image/jpeg`, at most 4 MB, with up to 8 cameras.
- Local API (127.0.0.1 only, no token): `/cameras` (JSON with ages), `/cam/<name>.jpg`, `/cam/<name>.mjpg`, `/qr.png`. The console proxies these under `/phonecam/...`.
- Snapshots: every 2 s to `experiments/2026-10-16-linux-real-arm/media/phonecam/<name>/latest.jpg`, plus a timestamped copy at most once a minute (about 9 MB per camera per hour, so be selective about committing them).
- The console's **Phones** page shows QR codes and links for `/`, `/tilt` and `/joystick` (built from `certs/phone-url.txt`). The QR codes stay hidden until you press *Show QR codes*, and `/api/phones`, its QR images and `/phonecam/qr.png` only answer browsers on the laptop itself (127.0.0.1), even if the console runs with `--host 0.0.0.0`. The console's `/phonecam/cameras` proxy drops the token-bearing `phone_url`.
- Firewall: ufw is active, so phones need `sudo ufw allow from 192.168.100.0/24 to any port 8443 proto tcp`. The Wi-Fi is shared, so the token is what keeps other people's devices out.
