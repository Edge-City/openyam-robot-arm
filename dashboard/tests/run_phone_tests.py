import secrets, subprocess, sys, threading, time, urllib.request
from pathlib import Path
REPO = Path("/home/naman/openyam-robot-arm"); SCR = Path(__file__).parent
sys.path.insert(0, str(REPO / "dashboard"))
import phonecam, uvicorn
d = subprocess.Popen([str(REPO/".venv/bin/python"), "-m", "armctl.daemon", "--backend", "mock", "--port", "8191", "--auto-approve", "--log", str(SCR/"pt-daemon.log")], cwd=REPO, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
tok = secrets.token_urlsafe(12)
srv = uvicorn.Server(uvicorn.Config(phonecam.build_phone_app(phonecam.Store(SCR/"media"), tok, "ws://127.0.0.1:8191/armctl"), host="127.0.0.1", port=8453, ssl_certfile=str(phonecam.CERT), ssl_keyfile=str(phonecam.KEY), log_level="warning"))
threading.Thread(target=srv.run, daemon=True).start(); time.sleep(3)
try:
    rc = subprocess.call([str(REPO/".venv/bin/python"), "armctl/tests/browser_phone_pages.py", "--token", tok], cwd=REPO)
finally:
    srv.should_exit = True; d.terminate(); d.wait(5)
sys.exit(rc)
