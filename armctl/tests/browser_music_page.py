#!/usr/bin/env python3
"""Headless Firefox check of the music -> dance page. Not collected by pytest.

    .venv/bin/python dashboard/music_server.py &          # http://127.0.0.1:8093
    .venv/bin/python armctl/tests/browser_music_page.py [--song beat-dance-100bpm] [--shot /tmp/music.png]

Needs selenium, firefox and geckodriver, and a song in music/inbox. Saves a playable named
browser-test-<song> (and its plan and clip), then deletes them again.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from pathlib import Path

from selenium import webdriver
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.common.by import By
from selenium.webdriver.firefox.service import Service
from selenium.webdriver.support.ui import Select

REPO = Path(__file__).resolve().parents[2]
RESULTS: list[tuple[bool, str]] = []


def check(ok, what):
    RESULTS.append((bool(ok), what))
    print(("PASS " if ok else "FAIL ") + what, flush=True)


def wait(pred, timeout=20.0):
    t = time.time() + timeout
    while time.time() < t:
        try:
            if pred():
                return True
        except Exception:
            pass
        time.sleep(0.1)
    return False


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8093")
    ap.add_argument("--song", default="beat-dance-100bpm")
    ap.add_argument("--shot", default=None, help="save a screenshot here")
    a = ap.parse_args()
    opts = webdriver.FirefoxOptions()
    opts.add_argument("-headless")
    opts.set_preference("media.autoplay.default", 0)
    opts.set_preference("media.autoplay.blocking_policy", 0)
    svc = Service(shutil.which("geckodriver") or "/snap/firefox/current/usr/lib/firefox/geckodriver")
    drv = webdriver.Firefox(options=opts, service=svc)
    drv.set_window_size(1500, 950)
    name = f"browser-test-{a.song}"[:64]
    try:
        drv.get(f"{a.base}/music?song={a.song}")
        E = lambda i: drv.find_element(By.ID, i)  # noqa: E731
        check(wait(lambda: "valid" in E("status").text, 60), f"choreographed on load: {E('status').text[:90]!r}")
        check(wait(lambda: E("viewerMsg").text == "", 30), "URDF loaded into the 3D view")
        check(E("song").get_attribute("value") == a.song, "song selected from ?song=")
        check("BPM" in E("anSum").text, "analysis summary shown")
        n_rows = len(drv.find_elements(By.CSS_SELECTOR, "#blocks tr")) - 1
        check(n_rows >= 2, f"motif blocks table has {n_rows} rows")
        painted = drv.execute_script("""
            const c = document.getElementById('overview'), g = c.getContext('2d');
            const d = g.getImageData(0, 0, c.width, c.height).data; const s = new Set();
            for (let i = 0; i < d.length; i += 4 * 97) s.add(d[i] + ',' + d[i+1] + ',' + d[i+2]);
            return s.size;""")
        check(painted > 10, f"overview canvas painted ({painted} distinct colours sampled)")

        E("playBtn").click()
        time.sleep(2.5)
        t = float(E("time").text.split("/")[0])
        check(1.5 < t < 4.0, f"playback advances in time ({t:.2f} s after 2.5 s)")
        check(E("nowMotif").text != "", f"current motif shown while playing: {E('nowMotif').text!r}")
        check("beat" in E("beatInfo").text, f"beat counter: {E('beatInfo').text!r}")
        E("playBtn").click()

        # move the window: type a start time (the page snaps to a downbeat server-side)
        first = E("status").text.splitlines()[-1]
        st = E("start")
        st.clear()
        st.send_keys("20")
        drv.execute_script("document.getElementById('start').dispatchEvent(new Event('change'))")
        check(wait(lambda: E("status").text.splitlines()[-1] != first and "window" in E("status").text, 30),
              f"window moved: {E('status').text.splitlines()[-1] if E('status').text else ''!r}")

        # the intelligence layer: edit the plan JSON and apply it
        plan = json.loads(E("plan").get_attribute("value"))
        n = plan["window"]["n_beats"]
        plan["segments"] = [{"start_beat": 0, "end_beat": 8, "motif": "nod", "amplitude": 0.6, "note": "edited"},
                            {"start_beat": 8, "end_beat": n, "motif": "sweep", "amplitude": 1.0, "note": "edited"}]
        drv.execute_script("document.getElementById('plan').value = arguments[0]", json.dumps(plan))
        E("applyBtn").click()
        check(wait(lambda: "sweep" in E("blocks").text and "plan applied" in E("msg").text, 30),
              f"edited plan applied (sweep block shown; msg {E('msg').text!r})")

        # drag the excerpt box on the overview (no effect if the whole song is the window, so just exercise it)
        ov = E("overview")
        drv.execute_script("arguments[0].scrollIntoView({block: 'center'})", ov)
        ActionChains(drv).move_to_element_with_offset(ov, 10, 0).click_and_hold().move_by_offset(-60, 0).release().perform()
        time.sleep(1.0)
        check("bad" not in E("msg").get_attribute("class"), f"drag handled without error ({E('msg').text!r})")

        if a.shot:
            E("playBtn").click()
            time.sleep(1.5)
            E("playBtn").click()
            drv.save_screenshot(a.shot)
            print("screenshot:", a.shot)

        # save -> command
        sn = E("saveName")
        sn.clear()
        sn.send_keys(name)
        wait(lambda: E("saveBtn").is_enabled(), 10)
        E("saveBtn").click()
        want = f"experiments/2026-10-16-linux-real-arm/session_play.sh {name} --caps dance --audio music/clips/{name}.wav"
        check(wait(lambda: E("cmd").text == want, 60), f"real-arm command shown: {E('cmd').text!r}")
        files = [REPO / f"playables/{name}.json", REPO / f"music/plans/{name}.json", REPO / f"music/clips/{name}.wav"]
        check(all(p.exists() for p in files), "saved playable, plan and clip exist")
        pl = json.loads(files[0].read_text()) if files[0].exists() else {}
        check(pl.get("caps") == "dance" and pl.get("music", {}).get("clip") == f"music/clips/{name}.wav",
              "saved playable declares caps 'dance' and its clip")
        for p in files:
            p.unlink(missing_ok=True)
        logs = drv.execute_script("return (window.__errors || []).slice(0, 5)")
        check(not logs, f"no captured page errors {logs}")
    finally:
        drv.quit()
    bad = [w for ok, w in RESULTS if not ok]
    print(f"{len(RESULTS) - len(bad)}/{len(RESULTS)} passed")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
