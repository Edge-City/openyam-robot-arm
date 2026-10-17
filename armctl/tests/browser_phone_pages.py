#!/usr/bin/env python3
"""Headless Firefox check of the phone programs (tilt, joystick) through phonecam's HTTPS
relay against a MOCK armctl daemon started with --auto-approve. Not collected by pytest.

    .venv/bin/python -m armctl.daemon --backend mock --port 8191 --auto-approve --log /tmp/x.log &
    # a phonecam phone app on https://127.0.0.1:8453 relaying to ws://127.0.0.1:8191/armctl, token T
    .venv/bin/python armctl/tests/browser_phone_pages.py --base https://127.0.0.1:8453 --token T \
        --daemon http://127.0.0.1:8191

Needs selenium, firefox and geckodriver. Sensors are faked with synthetic deviceorientation
events; sticks and hold buttons are driven with real (mouse) pointer actions.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
import urllib.request

from selenium import webdriver
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.common.by import By
from selenium.webdriver.firefox.service import Service

RESULTS: list[tuple[bool, str]] = []


def check(ok, what):
    RESULTS.append((bool(ok), what))
    print(("PASS " if ok else "FAIL ") + what, flush=True)


def status(daemon):
    with urllib.request.urlopen(daemon + "/status", timeout=3) as r:
        return json.loads(r.read())


def wait(pred, timeout=5.0):
    t = time.time() + timeout
    while time.time() < t:
        if pred():
            return True
        time.sleep(0.05)
    return False


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="https://127.0.0.1:8453")
    ap.add_argument("--token", required=True)
    ap.add_argument("--daemon", default="http://127.0.0.1:8191")
    a = ap.parse_args()
    if status(a.daemon)["backend"] != "mock":
        print("refusing: not a mock daemon")
        return 2

    opts = webdriver.FirefoxOptions()
    opts.add_argument("-headless")
    opts.accept_insecure_certs = True
    opts.set_preference("dom.w3c_pointer_events.enabled", True)
    svc = Service(shutil.which("geckodriver") or "/snap/bin/geckodriver")
    drv = webdriver.Firefox(options=opts, service=svc)
    drv.set_window_size(430, 900)  # phone-ish
    try:
        # ---------------------------------------------------------------- tilt
        drv.get(f"{a.base}/tilt?t={a.token}")
        check(wait(lambda: drv.find_element(By.ID, "conn").text == "connected"), "tilt: page loads, WebSocket via relay connects")
        check(wait(lambda: any(c["program"] == "tilt" for c in status(a.daemon)["clients"])), "tilt: daemon sees program 'tilt'")
        drv.find_element(By.ID, "sensors").click()
        drv.execute_script("window.dispatchEvent(new DeviceOrientationEvent('deviceorientation', {beta: 30, gamma: 0}))")
        check(wait(lambda: "sensors on" in drv.find_element(By.ID, "sensors").text), "tilt: orientation events picked up")
        drv.find_element(By.ID, "acquire").click()
        check(wait(lambda: drv.find_element(By.ID, "lease").text == "lease: YOU"), "tilt: acquire -> lease: YOU (auto-approve)")
        # go to ready first so Cartesian jogging is away from the home limits
        drv.execute_script("armctl.send({op:'do', id:'r', cmd:'ready'})")
        check(wait(lambda: not status(a.daemon)["state"]["busy"], 9), "tilt: ready move finished")
        hold = drv.find_element(By.ID, "hold")
        check(hold.is_enabled(), "tilt: HOLD button enabled once sensors + lease")
        q0 = status(a.daemon)["state"]["q"]
        act = ActionChains(drv)
        act.move_to_element(hold).click_and_hold().perform()  # neutral = beta 30, gamma 0
        drv.execute_script("window.dispatchEvent(new DeviceOrientationEvent('deviceorientation', {beta: 30, gamma: -20}))")
        time.sleep(1.0)
        st = status(a.daemon)["state"]
        check(st["streaming"] and st["moving"], f"tilt: holding + roll left (+y) -> streaming, moving (qd={st['qd']})")
        ActionChains(drv).release().perform()
        time.sleep(0.8)
        st = status(a.daemon)["state"]
        check(not st["moving"] and abs(st["q"][0] - q0[0]) > 0.02, f"tilt: release -> stopped; j1 moved {st['q'][0] - q0[0]:+.3f}")
        q1 = st["q"]
        drv.execute_script("window.dispatchEvent(new DeviceOrientationEvent('deviceorientation', {beta: 0, gamma: -40}))")
        time.sleep(0.5)
        check(status(a.daemon)["state"]["q"] == q1, "tilt: tilting without holding moves nothing")
        zup = drv.find_element(By.ID, "zup")
        ActionChains(drv).move_to_element(zup).click_and_hold().pause(0.8).release().perform()
        time.sleep(0.6)
        st = status(a.daemon)["state"]
        check(st["q"] != q1 and not st["moving"], "tilt: hold 'up' moves (z), release stops")
        drv.find_element(By.ID, "gopen").click()
        check(wait(lambda: status(a.daemon)["state"]["grip_target"] == 1.0), "tilt: gripper open")
        drv.find_element(By.ID, "acquire").click()  # release
        check(wait(lambda: status(a.daemon)["lease"]["holder"] is None), "tilt: release frees the lease")

        # ---------------------------------------------------------------- joystick
        drv.get(f"{a.base}/joystick?t={a.token}")
        check(wait(lambda: drv.find_element(By.ID, "conn").text == "connected"), "joystick: page loads and connects")
        drv.find_element(By.ID, "acquire").click()
        check(wait(lambda: drv.find_element(By.ID, "lease").text == "lease: YOU"), "joystick: acquire -> lease: YOU")
        s1 = drv.find_element(By.ID, "s1")
        q0 = status(a.daemon)["state"]["q"]
        ActionChains(drv).move_to_element(s1).click_and_hold().move_by_offset(0, -60).perform()
        time.sleep(1.0)
        st = status(a.daemon)["state"]
        check(st["streaming"] and st["moving"], "joystick: left stick up -> streaming, moving")
        ActionChains(drv).release().perform()
        time.sleep(0.8)
        st = status(a.daemon)["state"]
        check(not st["moving"] and st["q"] != q0, "joystick: thumb off -> stopped")
        s2 = drv.find_element(By.ID, "s2")
        q1 = st["q"]
        ActionChains(drv).move_to_element(s2).click_and_hold().move_by_offset(-60, 0).pause(0.8).release().perform()
        time.sleep(0.7)
        st = status(a.daemon)["state"]
        check(st["q"][0] != q1[0] and not st["moving"], "joystick: right stick sideways -> yaw (j1), then stop")
        drv.execute_script("const g=document.getElementById('grip'); g.value=0.6; g.dispatchEvent(new Event('change'))")
        check(wait(lambda: abs(status(a.daemon)["state"]["grip_target"] - 0.6) < 1e-6), "joystick: gripper slider -> 0.6")
        drv.execute_script("armctl.ws().close()")  # Wi-Fi drop while holding the lease
        check(wait(lambda: status(a.daemon)["lease"]["holder"] is None, 3), "joystick: connection drop frees the lease")
        drv.save_screenshot("/tmp/claude-1000/-home-naman-openyam-robot-arm/1b586730-ced1-4e49-8c29-7a53acf736f5/scratchpad/joystick.png")
        drv.get(f"{a.base}/tilt?t={a.token}")
        time.sleep(1)
        drv.save_screenshot("/tmp/claude-1000/-home-naman-openyam-robot-arm/1b586730-ced1-4e49-8c29-7a53acf736f5/scratchpad/tilt.png")
        errs = drv.execute_script("return window.__errs || []")
        check(not errs, "no JS errors captured")
    finally:
        drv.quit()
    bad = [w for ok, w in RESULTS if not ok]
    print(f"\n{len(RESULTS) - len(bad)}/{len(RESULTS)} passed")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
