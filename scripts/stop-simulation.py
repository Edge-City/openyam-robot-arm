"""Stop only this installation's explicitly identified OpenYAM simulation."""
import json
from pathlib import Path
import os
import signal

import psutil

root = Path(__file__).resolve().parent.parent
expected_cli = str(Path(os.environ["VIRTUAL_ENV"]) / "bin/dimos")
found = False
for registry in (root / "state/dimos/runs").glob("*.json"):
    entry = json.loads(registry.read_text())
    if entry.get("blueprint") != "keyboard-teleop-openyam" or entry.get("config_overrides", {}).get("simulation") != "mujoco":
        continue
    try:
        process = psutil.Process(entry["pid"])
        argv = process.cmdline()
        if expected_cli not in argv or "--simulation" not in argv or "--no-simulation" in argv or "keyboard-teleop-openyam" not in argv:
            continue
        children = process.children(recursive=True)
        found = True
        print(f"Stopping simulation PID {process.pid}", flush=True)
        process.send_signal(signal.SIGINT)
        _, alive = psutil.wait_procs([process, *children], timeout=12)
        for child in alive:
            try:
                child.terminate()
            except psutil.NoSuchProcess:
                pass
        _, alive = psutil.wait_procs(alive, timeout=3)
        for child in alive:
            try:
                child.kill()
            except psutil.NoSuchProcess:
                pass
        _, alive = psutil.wait_procs(alive, timeout=3)
        if alive:
            raise RuntimeError(f"Simulation processes still present: {[p.pid for p in alive]}")
        print("Simulation process and captured children stopped.", flush=True)
    except psutil.NoSuchProcess:
        continue
if not found:
    print("No matching OpenYAM simulation is running.")
