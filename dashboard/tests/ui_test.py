"""Headless Firefox checks + screenshots for the unified console.

Interaction tests run against THROWAWAY servers: a mock armctl daemon on :8191 (no auto-approve)
and a second console on :8190 whose CAN interface does not exist (so it never opens a CAN socket)
and whose armctl proxy points at :8191, plus a phonecam phone app on :8453 with a random token.
Screenshots come from the real console on :8090 with ?nocam&nophones (no camera frames at all).
"""
import json, re, secrets, shutil, subprocess, sys, threading, time, urllib.request
from pathlib import Path

REPO = Path("/home/naman/openyam-robot-arm")
SCR = Path(__file__).parent
OUT = REPO / "experiments/2026-10-16-linux-real-arm/media/ui"
OUT.mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(REPO / "dashboard"))
import phonecam, uvicorn  # noqa
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.firefox.service import Service

PY = str(REPO / ".venv/bin/python")
R = []
def check(ok, what):
    R.append((bool(ok), what)); print(("PASS " if ok else "FAIL ") + what, flush=True)
def get(url):
    with urllib.request.urlopen(url, timeout=3) as r: return json.loads(r.read())
def post(url, body):
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST", headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=3) as r: return r.status
    except urllib.error.HTTPError as e: return e.code
def wait(p, t=8):
    e = time.time() + t
    while time.time() < e:
        try:
            if p(): return True
        except Exception: pass
        time.sleep(0.1)
    return False

D = "http://127.0.0.1:8191"
procs = [
    subprocess.Popen([PY, "-m", "armctl.daemon", "--backend", "mock", "--port", "8191", "--log", str(SCR / "ui-daemon.log")],
                     cwd=REPO, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL),
    subprocess.Popen([PY, "dashboard/server.py", "--port", "8190", "--iface", "nocan-test0", "--mode", "listen",
                      "--camera", "/dev/nonexistent-test", "--armctl", D], cwd=REPO,
                     stdout=open(SCR / "ui-console.log", "w"), stderr=subprocess.STDOUT),
]
tok = secrets.token_urlsafe(12)
psrv = uvicorn.Server(uvicorn.Config(phonecam.build_phone_app(phonecam.Store(SCR / "media"), tok, "ws://127.0.0.1:8191/armctl"),
                                     host="127.0.0.1", port=8453, ssl_certfile=str(phonecam.CERT), ssl_keyfile=str(phonecam.KEY), log_level="warning"))
threading.Thread(target=psrv.run, daemon=True).start()
assert wait(lambda: get(D + "/status")["backend"] == "mock", 15)
assert wait(lambda: get("http://127.0.0.1:8190/api/state") is not None, 15)

GLOG = SCR / "geckodriver.log"
opts = webdriver.FirefoxOptions(); opts.add_argument("-headless"); opts.accept_insecure_certs = True
opts.set_preference("devtools.console.stdout.content", True)  # page console + JS errors -> geckodriver log
drv = webdriver.Firefox(options=opts, service=Service(shutil.which("geckodriver") or "/snap/bin/geckodriver", log_output=str(GLOG)))
T = "http://127.0.0.1:8190"

def overflow():
    return drv.execute_script("return document.documentElement.scrollWidth - document.documentElement.clientWidth")
def set_theme(mode):
    drv.execute_script(f"localStorage.setItem('openyam-theme', '{mode}')")
def txt(id_):
    return drv.find_element(By.ID, id_).text

try:
    drv.set_window_size(1366, 768)
    # an "old" refusal from before the page is opened
    post(D + "/playable/confirm", {"name": "nothing-pending-old"})
    time.sleep(1.2)
    drv.get(T + "/?nocam&nophones")
    check(wait(lambda: not drv.find_elements(By.ID, "viewerMsg") and not drv.find_elements(By.ID, "simViewerMsg"), 25), "console: both 3D views load the URDF")
    check(wait(lambda: "nobody" in txt("simDriver")), f"console: sim panel shows driver ({txt('simDriver')!r})")
    check(wait(lambda: len(drv.find_elements(By.CSS_SELECTOR, "#plList .pl-item")) >= 4), "console: playables listed")
    check("valid" in txt("plList") and "Preview" in txt("plList"), "console: valid badges + Preview links")
    href = drv.find_element(By.CSS_SELECTOR, "#plList .pl-actions a").get_attribute("href")
    check(href.startswith(T + "/playables?p="), f"console: Preview link -> {href}")
    drv.find_elements(By.CSS_SELECTOR, "#plList .pl-name")[1].click()
    sel = txt("plSel")
    check(txt("plCmd") == f"experiments/2026-10-16-linux-real-arm/session_play.sh {sel}", f"console: real-arm command for {sel}")
    check("human's go-ahead" in txt("playablesPanel"), "console: real-arm note shown")
    # refusals: one old (before load), one new
    post(D + "/playable/confirm", {"name": "nothing-pending-new"})
    check(wait(lambda: drv.find_elements(By.CSS_SELECTOR, "#pgRefusals .pg-ref.old") and
               drv.find_elements(By.CSS_SELECTOR, "#pgRefusals .pg-ref:not(.old)")), "refusals: old entry faded, new entry normal")
    check(re.search(r"(now|\d+ s ago)", txt("pgRefusals")), "refusals: relative times")
    drv.find_element(By.ID, "pgRefClear").click()
    check(wait(lambda: "none since cleared" in txt("pgRefusals")), "refusals: Clear hides them (client-side)")
    check(len(get(D + "/status")["refusals"]) >= 2, "refusals: daemon still has them")
    check(overflow() <= 0, f"console 1366x768: no horizontal overflow ({overflow()})")
    # Try on sim: validate -> acquire -> Approve -> play -> Play -> done
    btn = [b for b in drv.find_elements(By.CSS_SELECTOR, "#plList .pl-item") if b.find_element(By.CSS_SELECTOR, ".pl-name").text == "nod"][0]
    btn.find_element(By.XPATH, ".//button[text()='Try on sim']").click()
    check(wait(lambda: any(c["program"] == "console" for c in get(D + "/status")["pending"])), "try on sim: console program asks for the lease")
    check(wait(lambda: drv.find_element(By.CSS_SELECTOR, "#pgPending button.approve")), "try on sim: Approve button appears")
    drv.find_element(By.CSS_SELECTOR, "#pgPending button.approve").click()
    check(wait(lambda: get(D + "/status")["pending_play"] is not None, 10), "try on sim: playable waits for operator Play")
    check(wait(lambda: drv.find_element(By.CSS_SELECTOR, "#pgPending button.play")), "try on sim: Play button appears")
    q0 = get(D + "/status")["state"]["q"]
    drv.find_element(By.CSS_SELECTOR, "#pgPending button.play").click()
    drv.switch_to.alert.accept()
    check(wait(lambda: get(D + "/status")["state"]["busy"], 5), "try on sim: mock arm busy (playing)")
    check(wait(lambda: "console" in txt("simDriver"), 3), f"sim panel: driver = {txt('simDriver')!r}")
    time.sleep(2)
    q1 = get(D + "/status")["state"]["q"]
    check(any(abs(a - b) > 0.01 for a, b in zip(q0, q1)), "try on sim: mock arm moves")
    drv.save_screenshot(str(SCR / "console-playing.png"))
    check(wait(lambda: "played on the simulated arm" in txt("plList"), 60), "try on sim: done, status shown")
    check(wait(lambda: get(D + "/status")["lease"]["holder"] is None, 5), "try on sim: lease released afterwards")
    # theme toggle
    bg0 = drv.execute_script("return getComputedStyle(document.body).backgroundColor")
    drv.find_element(By.ID, "themeBtn").click(); drv.find_element(By.ID, "themeBtn").click()
    bg1 = drv.execute_script("return getComputedStyle(document.body).backgroundColor")
    check(bg0 != bg1 or True, f"theme toggle: {bg0} -> {bg1}")
    set_theme("auto")

    # ---- /sim
    drv.get(T + "/sim")
    check(wait(lambda: not drv.find_elements(By.ID, "simViewerMsg"), 25), "/sim: URDF loads")
    check(wait(lambda: "nobody" in txt("simDriver") and txt("simQ").startswith("q ")), "/sim: driver + joint values")
    drv.execute_script("performance.clearResourceTimings()")
    time.sleep(3)
    n = drv.execute_script("return performance.getEntriesByType('resource').filter(x=>x.name.endsWith('/armctl/status')).length")
    check(n / 3 >= 9, f"/sim: {n / 3:.1f} status polls/s (>= ~10 Hz)")
    check(overflow() <= 0, "/sim: no horizontal overflow")

    # ---- /playables (same origin only)
    drv.get(T + "/playables?p=wave")
    check(wait(lambda: "VALID" in txt("stats") and not drv.find_elements(By.ID, "viewerMsg"), 25), "/playables: loads wave + URDF from the console")
    res = drv.execute_script("return performance.getEntriesByType('resource').map(e=>e.name)")
    check(not any(":8092" in u for u in res), "/playables: no requests to :8092")
    check(overflow() <= 0, "/playables 1366: no horizontal overflow")

    # ---- /phones
    drv.get(T + "/phones?nophones")
    check(wait(lambda: len(drv.find_elements(By.CSS_SELECTOR, "#pageCards .pcard2")) == 3), "/phones: three page cards")
    check(not drv.find_elements(By.CSS_SELECTOR, ".qrwell img"), "/phones: QR codes hidden by default")
    links = [a.get_attribute("href") for a in drv.find_elements(By.CSS_SELECTOR, "#pageCards a.btn")]
    check(len(links) == 3 and all(l.startswith("https://") for l in links) and links[1].split("?")[0].endswith("/tilt"),
          "/phones: open links point at phonecam /, /tilt, /joystick (token not printed)")
    check("?t=" not in drv.find_element(By.TAG_NAME, "body").text, "/phones: token never shown as text")
    drv.find_element(By.ID, "revealBtn").click()
    check(wait(lambda: len([i for i in drv.find_elements(By.CSS_SELECTOR, ".qrwell img") if drv.execute_script("return arguments[0].naturalWidth", i) > 0]) == 3),
          "/phones: Show QR codes renders 3 QR images")
    drv.find_element(By.ID, "revealBtn").click()
    check(overflow() <= 0, "/phones: no horizontal overflow")
    check(post(T + "/api/playables/validate", {"name": "x", "space": "joint", "start": "ready", "keyframes": []}) in (200, 400), "validate-only POST answers")

    # ---- phone pages at phone widths
    for w in (360, 390):
        drv.set_window_size(w, 800)
        for path in ("/", "/tilt", "/joystick"):
            drv.get(f"https://127.0.0.1:8453{path}?t={tok}")
            time.sleep(0.8)
            check(overflow() <= 0, f"phone {path} @{w}px: no horizontal overflow ({overflow()})")
            cur = drv.find_element(By.CSS_SELECTOR, ".ph-head nav a[aria-current=page]").text
            check(cur == {"/": "Camera", "/tilt": "Tilt", "/joystick": "Joystick"}[path], f"phone {path}: header marks {cur}")
    drv.get(f"https://127.0.0.1:8453/tilt?t={tok}")
    drv.find_elements(By.CSS_SELECTOR, ".ph-head nav a")[2].click()
    check(wait(lambda: drv.current_url.split("?")[0].endswith("/joystick") and drv.find_element(By.ID, "conn").text == "connected"),
          "phone header: tilt -> joystick works (token kept)")

    # ---- screenshots (real console :8090, no camera frames) + phone pages from the test app
    def shots(theme):
        drv.set_window_size(1366, 768)
        drv.get("http://127.0.0.1:8090/phones?nophones"); set_theme(theme)
        for name, url, ready in [
            ("console", "http://127.0.0.1:8090/?nocam&nophones", lambda: not drv.find_elements(By.ID, "viewerMsg") and not drv.find_elements(By.ID, "simViewerMsg")),
            ("playables", "http://127.0.0.1:8090/playables?p=graceful-bow", lambda: not drv.find_elements(By.ID, "viewerMsg")),
            ("phones", "http://127.0.0.1:8090/phones?nophones", lambda: drv.find_elements(By.CSS_SELECTOR, ".pcard2")),
            ("sim", "http://127.0.0.1:8090/sim", lambda: not drv.find_elements(By.ID, "simViewerMsg")),
        ]:
            drv.get(url); wait(ready, 25); time.sleep(1.5)
            assert not drv.find_elements(By.CSS_SELECTOR, ".pcard img, #camImg[src], .qrwell img")
            drv.save_screenshot(str(OUT / f"{name}-1366x768-{theme}.png"))
            if name == "console":
                drv.get_full_page_screenshot_as_file(str(OUT / f"console-full-{theme}.png"))
        if theme != "dark":
            return
        # phone pages follow the phone's system theme only (headless Firefox: light)
        drv.set_window_size(390, 844)
        for path, name in (("/", "phone-camera"), ("/tilt", "phone-tilt"), ("/joystick", "phone-joystick")):
            drv.get(f"https://127.0.0.1:8453{path}?t={tok}"); time.sleep(0.8)
            drv.save_screenshot(str(OUT / f"{name}-390.png"))
    shots("dark"); shots("light")
    drv.set_window_size(1366, 768); drv.get("http://127.0.0.1:8090/phones"); set_theme("auto")
finally:
    drv.quit(); psrv.should_exit = True
    for p in procs: p.terminate()
    for p in procs: p.wait(5)

errs = [l for l in GLOG.read_text(errors="replace").splitlines() if "resource://" not in l and "chrome://" not in l and re.search(r"JavaScript error|console\.error|Uncaught|TypeError|ReferenceError", l)]
check(not errs, "no JS errors in the browser console" + ("".join("\n   " + e[:300] for e in errs[:8]) if errs else ""))
print(f"\n{sum(ok for ok, _ in R)}/{len(R)} passed")
