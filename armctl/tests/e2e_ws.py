#!/usr/bin/env python3
"""End-to-end check over real WebSockets against a running MOCK daemon (not collected by pytest).

    .venv/bin/python -m armctl.daemon --backend mock          # in another terminal (or tmux claude:armctl)
    .venv/bin/python armctl/tests/e2e_ws.py [--url ws://127.0.0.1:8091/armctl]

Two programs (tilt, joystick): operator-approved acquire, ready move, Cartesian streaming,
deadman, switching (stop-then-grant), refusals with reasons, E-STOP latch and reset.
Refuses to run unless the daemon reports backend "mock".
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
import urllib.request

import websockets

RESULTS: list[tuple[bool, str]] = []


def check(ok: bool, what: str) -> None:
    RESULTS.append((bool(ok), what))
    print(("PASS " if ok else "FAIL ") + what, flush=True)


class Prog:
    def __init__(self, url: str, program: str):
        self.url, self.program = url, program
        self.msgs: list[dict] = []
        self.state: dict = {}

    async def __aenter__(self):
        self.ws = await websockets.connect(self.url)
        self.task = asyncio.create_task(self._rx())
        await self.send(op="hello", program=self.program, name=f"e2e {self.program}")
        self.hello = await self.wait("hello")
        return self

    async def __aexit__(self, *exc):
        self.task.cancel()
        await self.ws.close()

    async def _rx(self):
        async for raw in self.ws:
            m = json.loads(raw)
            if m["op"] == "state":
                self.state = m
            else:
                self.msgs.append(m)

    async def send(self, **m):
        await self.ws.send(json.dumps(m))

    async def wait(self, op, pred=lambda m: True, timeout=5.0, since=0):
        t = time.monotonic() + timeout
        while time.monotonic() < t:
            for m in self.msgs[since:]:
                if m["op"] == op and pred(m):
                    return m
            await asyncio.sleep(0.02)
        return None


def post(base: str, path: str, body: dict | None = None) -> dict:
    req = urllib.request.Request(base + path, data=json.dumps(body or {}).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read())


def get(base: str, path: str) -> dict:
    with urllib.request.urlopen(base + path, timeout=5) as r:
        return json.loads(r.read())


async def main(url: str) -> int:
    base = url.replace("ws://", "http://").rsplit("/", 1)[0]
    st = get(base, "/status")
    if st["backend"] != "mock":
        print(f"refusing: daemon backend is {st['backend']!r}, this check only runs against mock")
        return 2
    if st["estop"]:
        post(base, "/estop/reset")

    async with Prog(url, "tilt") as a, Prog(url, "joystick") as b:
        check(a.hello["protocol"] == "armctl/0", "hello reply with protocol and envelope")
        await asyncio.sleep(0.2)
        check(bool(a.state) and "q" in a.state, f"state broadcast received (q={a.state.get('q')})")

        n = len(a.msgs)
        await a.send(op="acquire", id="a1")
        ack = await a.wait("ack", lambda m: m.get("id") == "a1", since=n)
        if ack and ack.get("pending"):
            check(True, "acquire is pending until the operator approves")
            await b.send(op="stream", v={"x": 0.02})
            r = await b.wait("refused")
            check(r is not None and "not the lease holder" in r["reason"], f"non-holder stream refused: {r and r['reason']}")
            post(base, "/lease", {"client": a.hello["client"]})
        else:
            check(True, "acquire auto-approved (daemon started with --auto-approve)")
        lease = await a.wait("lease", lambda m: m.get("holder") == "tilt", since=n)
        check(lease is not None, "operator approval -> lease holder tilt")

        n = len(a.msgs)
        await a.send(op="do", id="r1", cmd="ready")
        ack = await a.wait("ack", lambda m: m.get("id") == "r1", since=n)
        check(ack is not None, f"do ready accepted ({ack and ack.get('duration')} s)")
        done = await a.wait("done", lambda m: m.get("id") == "r1", timeout=(ack or {}).get("duration", 0) + 4, since=n)
        check(done is not None, f"ready reached and verified, q={a.state.get('q')}")

        q0 = list(a.state["q"])
        for _ in range(20):  # 1 s of lateral twist at 20 Hz
            await a.send(op="stream", v={"x": 0, "y": 0.05, "z": 0}, ttl_ms=200)
            await asyncio.sleep(0.05)
        moved = abs(a.state["q"][0] - q0[0])
        check(moved > 0.05, f"stream moved j1 by {moved:.3f} rad")
        await asyncio.sleep(0.7)
        q1 = list(a.state["q"])
        await asyncio.sleep(0.3)
        check(not a.state["moving"] and a.state["q"] == q1, "deadman: stopped within ttl + braking after the last setpoint")

        n = len(a.msgs)
        await a.send(op="do", id="bad", cmd="move_joints", q=[0, 0.5, 0.8, 1.62, 0, 0])
        r = await a.wait("refused", lambda m: m.get("id") == "bad", since=n)
        check(r is not None and "j4 target 1.620 > soft limit 1.471" in r["reason"], f"out-of-envelope refused: {r and r['reason']}")

        # switch: joystick asks, operator approves, tilt loses it (stop first)
        for _ in range(4):
            await a.send(op="stream", v={"y": 0.05}, ttl_ms=500)
            await asyncio.sleep(0.05)
        nb = len(b.msgs)
        await b.send(op="acquire", id="b1")
        await b.wait("ack", lambda m: m.get("id") == "b1", since=nb)
        if get(base, "/status")["lease"]["holder"] != "joystick":
            post(base, "/lease", {"program": "joystick"})
        lease = await b.wait("lease", lambda m: m.get("holder") == "joystick", since=nb)
        check(lease is not None and not get(base, "/status")["state"]["moving"], "switch: arm stopped, then lease -> joystick")
        n = len(a.msgs)
        await a.send(op="stream", v={"y": 0.05})
        r = await a.wait("refused", since=n)
        check(r is not None and "holder: joystick" in r["reason"], f"old holder refused: {r and r['reason']}")

        nb = len(b.msgs)
        await b.send(op="stream", qd=[0, 0, 0, 0.3, 0, 0], ttl_ms=300)
        await asyncio.sleep(0.1)
        post(base, "/estop")
        await asyncio.sleep(0.15)
        check(not b.state["moving"] and b.state["estop"] and b.state["lease"]["holder"] is None, "E-STOP: halted, lease revoked, latched")
        await b.send(op="acquire", id="b2")
        r = await b.wait("refused", lambda m: m.get("id") == "b2", since=nb)
        check(r is not None and "E-STOP is latched" in r["reason"], "acquire refused while E-STOP latched")
        post(base, "/estop/reset")
        await b.send(op="release")

    st = get(base, "/status")
    check(st["lease"]["holder"] is None and not st["clients"], "clean up: no clients, lease free")
    bad = [w for ok, w in RESULTS if not ok]
    print(f"\n{len(RESULTS) - len(bad)}/{len(RESULTS)} passed")
    return 1 if bad else 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="ws://127.0.0.1:8091/armctl")
    sys.exit(asyncio.run(main(ap.parse_args().url)))
