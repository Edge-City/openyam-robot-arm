"""Headless Firefox test of tilt.html lift-to-z, against a throwaway MOCK daemon (:8191, auto-approve)
and a throwaway phonecam phone app (:8453, random token). Nothing touches CAN or DimOS."""
import json, os, secrets, shutil, subprocess, sys, threading, time, urllib.request
from pathlib import Path

REPO = Path("/home/naman/openyam-robot-arm")
SCR = Path(__file__).parent
sys.path.insert(0, str(REPO / "dashboard"))
import phonecam, uvicorn  # noqa
from selenium import webdriver
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.common.by import By
from selenium.webdriver.firefox.service import Service

R = []
def check(ok, what):
    R.append(bool(ok)); print(("PASS " if ok else "FAIL ") + what, flush=True)
def status():
    with urllib.request.urlopen("http://127.0.0.1:8191/status", timeout=3) as r: return json.loads(r.read())
def wait(p, t=6):
    e = time.time() + t
    while time.time() < e:
        try:
            if p(): return True
        except Exception: pass
        time.sleep(0.05)
    return False

daemon = subprocess.Popen([str(REPO / ".venv/bin/python"), "-m", "armctl.daemon", "--backend", "mock", "--port", "8191",
                           "--auto-approve", "--log", str(SCR / "lift-daemon.log")], cwd=REPO,
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
tok = secrets.token_urlsafe(12)
store = phonecam.Store(SCR / "media")
srv = uvicorn.Server(uvicorn.Config(phonecam.build_phone_app(store, tok, "ws://127.0.0.1:8191/armctl"), host="127.0.0.1",
                                    port=8453, ssl_certfile=str(phonecam.CERT), ssl_keyfile=str(phonecam.KEY), log_level="warning"))
threading.Thread(target=srv.run, daemon=True).start()
wait(lambda: status()["backend"] == "mock", 10)
assert status()["backend"] == "mock"

opts = webdriver.FirefoxOptions(); opts.add_argument("-headless"); opts.accept_insecure_certs = True
drv = webdriver.Firefox(options=opts, service=Service(shutil.which("geckodriver") or "/snap/bin/geckodriver"))
drv.set_window_size(390, 844)
try:
    drv.get(f"https://127.0.0.1:8453/tilt?t={tok}")
    check(wait(lambda: drv.find_element(By.ID, "conn").text == "connected"), "connected")
    # capture outgoing stream messages
    drv.execute_script("""window.__sent=[]; const w=armctl.ws(); const o=w.send.bind(w);
      w.send=(m)=>{ try{const j=JSON.parse(m); if(j.op==='stream') window.__sent.push([performance.now(), j.v.z, j.v.x]);}catch(e){} o(m); };""")
    drv.find_element(By.ID, "sensors").click()
    drv.execute_script("window.dispatchEvent(new DeviceOrientationEvent('deviceorientation', {alpha:0, beta: 0, gamma: 0}))")
    check(wait(lambda: "sensors on" in drv.find_element(By.ID, "sensors").text), "sensors on")
    drv.find_element(By.ID, "acquire").click()
    check(wait(lambda: drv.find_element(By.ID, "lease").text == "lease: YOU"), "lease acquired")
    drv.execute_script("armctl.send({op:'do', id:'r', cmd:'ready'})")
    check(wait(lambda: not status()["state"]["busy"], 10), "at ready")
    # motion generator: profile set by window.__prof(t) -> acceleration z (device flat, so z = vertical)
    drv.execute_script("""window.__t0=performance.now(); window.__prof=()=>0;
      window.__gen=setInterval(()=>{ const t=(performance.now()-window.__t0)/1000; const a=window.__prof(t);
        const noise=(Math.random()-0.5)*0.2;
        window.dispatchEvent(new DeviceMotionEvent('devicemotion',{acceleration:{x:noise,y:noise,z:a+noise},
          accelerationIncludingGravity:{x:0,y:0,z:9.81+a}, interval:16}));
        window.dispatchEvent(new DeviceOrientationEvent('deviceorientation',{alpha:0,beta:0,gamma:0})); },16);""")
    hold = drv.find_element(By.ID, "hold")
    ac = ActionChains(drv); ac.move_to_element(hold).click_and_hold().perform()
    time.sleep(0.6)
    # still phone, 5 s: no z drift
    drv.execute_script("window.__sent=[]")
    time.sleep(5)
    zs = drv.execute_script("return window.__sent.map(s=>s[1])")
    check(len(zs) > 50 and max(abs(z) for z in zs) == 0, f"still phone 5 s: {len(zs)} streams, max |z| = {max(abs(z) for z in zs) if zs else None}")
    # brisk lift: +4 m/s^2 for 0.15 s, then -4 for 0.15 s (phone rises ~9 cm, ends still)
    q0 = status()["state"]["q"]
    drv.execute_script("""window.__sent=[]; const t1=(performance.now()-window.__t0)/1000+0.05;
      window.__prof=(t)=> (t>t1 && t<t1+0.15) ? 4 : (t>=t1+0.15 && t<t1+0.30) ? -4 : 0;""")
    time.sleep(2.0)
    sent = drv.execute_script("return window.__sent")
    zs = [s[1] for s in sent]
    peak = max(zs) if zs else 0
    check(peak > 0.02, f"lift: positive z stream, peak {peak:.4f} m/s (max 0.05)")
    check(min(zs) >= 0, f"lift: never negative z (min {min(zs)})")
    check(abs(zs[-1]) < 1e-9 and all(abs(z) < 1e-9 for z in zs[-10:]), f"lift: back to 0 after ({zs[-5:]})")
    pos_n = sum(1 for z in zs if z > 0)
    print(f"   {pos_n} positive-z streams out of {len(zs)}; peak {peak}")
    lower = drv.execute_script("""window.__sent=[]; const t1=(performance.now()-window.__t0)/1000+0.05;
      window.__prof=(t)=> (t>t1 && t<t1+0.15) ? -4 : (t>=t1+0.15 && t<t1+0.30) ? 4 : 0;""")
    time.sleep(2.0)
    zs = drv.execute_script("return window.__sent.map(s=>s[1])")
    check(min(zs) < -0.02 and max(zs) <= 0 and abs(zs[-1]) < 1e-9, f"lower: negative z only (min {min(zs):.4f}, max {max(zs)}) then 0")
    # toggle off: lift gives no z
    ac.release().perform()
    time.sleep(0.3)
    drv.find_element(By.ID, "liftOn").click()
    ac = ActionChains(drv); ac.move_to_element(hold).click_and_hold().perform()
    drv.execute_script("""window.__sent=[]; const t1=(performance.now()-window.__t0)/1000+0.05;
      window.__prof=(t)=> (t>t1 && t<t1+0.15) ? 4 : (t>=t1+0.15 && t<t1+0.30) ? -4 : 0;""")
    time.sleep(1.5)
    zs = drv.execute_script("return window.__sent.map(s=>s[1])")
    check(len(zs) > 10 and all(z == 0 for z in zs), "toggle off: lift ignored")
    ac.release().perform()
    # fallback path: only accelerationIncludingGravity, phone upright (beta 90): vertical is device y
    drv.find_element(By.ID, "liftOn").click()
    drv.execute_script("""clearInterval(window.__gen); window.__t0=performance.now(); window.__prof=()=>0;
      window.__gen=setInterval(()=>{ const t=(performance.now()-window.__t0)/1000; const a=window.__prof(t);
        window.dispatchEvent(new DeviceMotionEvent('devicemotion',{acceleration:null,
          accelerationIncludingGravity:{x:0,y:9.81+a,z:0}, interval:16}));
        window.dispatchEvent(new DeviceOrientationEvent('deviceorientation',{alpha:0,beta:90,gamma:0})); },16);""")
    time.sleep(3)
    ac = ActionChains(drv); ac.move_to_element(hold).click_and_hold().perform()
    drv.execute_script("""window.__sent=[]; const t1=(performance.now()-window.__t0)/1000+0.3;
      window.__prof=(t)=> (t>t1 && t<t1+0.15) ? 4 : (t>=t1+0.15 && t<t1+0.30) ? -4 : 0;""")
    time.sleep(2.5)
    zs = drv.execute_script("return window.__sent.map(s=>s[1])")
    info = drv.find_element(By.ID, "liftInfo").text
    check(max(zs) > 0.01 and abs(zs[-1]) < 1e-9, f"fallback (gravity-filtered, upright): peak {max(zs):.4f}, end {zs[-1]} [{info}]")
    ac.release().perform()
    time.sleep(0.3)
    drv.save_screenshot(str(SCR / "tilt-lift.png"))
    errs = drv.execute_script("return window.__errs || []")
finally:
    drv.quit(); srv.should_exit = True; daemon.terminate(); daemon.wait(5)
print(f"{sum(R)}/{len(R)} passed")
