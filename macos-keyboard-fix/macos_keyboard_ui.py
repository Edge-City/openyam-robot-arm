"""Local simulation-only Cocoa compatibility bridge for DimOS 0.0.14.

The UI child imports only pygame and the standard library. Robot streams stay
in the DimOS worker. This file is copied beside keyboard_teleop_module.py.
"""
import json
import math
import os
from pathlib import Path
import select
import subprocess
import sys
import time


def run_bridge(module):
    from dimos.core.global_config import global_config
    from dimos.teleop.keyboard.keyboard_teleop_module import _twist_from_keys

    if not global_config.simulation:
        raise RuntimeError("Local macOS keyboard bridge is simulation-only")
    # `dimos --simulation run ...` sets global_config, not the environment, so
    # forward the verified mode for the child's guard.
    env = dict(os.environ, SDL_VIDEODRIVER="cocoa", PYGAME_HIDE_SUPPORT_PROMPT="1",
               DIMOS_SIMULATION=str(global_config.simulation))
    process = subprocess.Popen(
        [sys.executable, "-u", str(Path(__file__).resolve()), "--ui"],
        stdout=subprocess.PIPE, env=env,
    )
    os.set_blocking(process.stdout.fileno(), False)
    buffer = b""
    reported_ready = False
    last_message = time.monotonic()
    try:
        while not module._stop_event.is_set():
            ready, _, _ = select.select([process.stdout], [], [], 0.1)
            if ready:
                chunk = os.read(process.stdout.fileno(), 65536)
                if not chunk:
                    break
                buffer += chunk
                if len(buffer) > 65536:
                    raise ValueError("Keyboard input buffer exceeded limit")
                while b"\n" in buffer:
                    line, buffer = buffer.split(b"\n", 1)
                    if len(line) > 4096:
                        raise ValueError("Keyboard packet exceeded limit")
                    packet = json.loads(line)
                    if not reported_ready and packet.get("driver"):
                        print("OpenYAM simulation keyboard ready: " + str(packet["driver"]) + " " + str(packet.get("size")), flush=True)
                        reported_ready = True
                    keys = packet["keys"]
                    if not isinstance(keys, list) or len(keys) > 12 or not all(type(k) is int for k in keys):
                        raise ValueError("Invalid keyboard packet")
                    timestamp = float(packet["time"])
                    if not math.isfinite(timestamp) or not 0 <= time.monotonic() - timestamp <= 0.3:
                        module._publish_twist()
                        continue
                    last_message = timestamp
                    linear, angular = _twist_from_keys(
                        set(keys), linear_speed=module.config.linear_speed,
                        angular_speed=module.config.angular_speed,
                    )
                    module._publish_twist(linear=linear, angular=angular)
                    opening = packet.get("gripper")
                    if opening is not None:
                        if opening not in (0.0, 1.0):
                            raise ValueError("Invalid gripper packet")
                        module._publish_gripper_command(opening=opening)
            if time.monotonic() - last_message > 0.3:
                module._publish_twist()
            if process.poll() is not None:
                break
    finally:
        try:
            module._publish_twist()
        finally:
            try:
                if process.poll() is None:
                    process.terminate()
                try:
                    process.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=1)
            finally:
                process.stdout.close()


def run_ui():
    os.environ["SDL_VIDEODRIVER"] = "cocoa"
    os.environ["PYGAME_HIDE_SUPPORT_PROMPT"] = "1"
    import pygame

    pygame.display.init()
    pygame.font.init()
    screen = pygame.display.set_mode((640, 410))
    pygame.display.set_caption("OpenYAM Keyboard Teleop - SIMULATION ONLY")
    font = pygame.font.Font(None, 27)
    clock = pygame.time.Clock()
    held = set()
    motion = {getattr(pygame, "K_" + k) for k in "wsadqerftgyh"}
    focused = True
    running = True

    def emit(gripper=None):
        print(json.dumps({"keys": sorted(held), "gripper": gripper, "time": time.monotonic(),
                          "driver": pygame.display.get_driver(), "size": list(screen.get_size())}), flush=True)

    try:
        while running:
            for event in pygame.event.get():
                if event.type == pygame.QUIT or (event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE):
                    running = False
                    held.clear()
                    emit()
                    break
                elif event.type == pygame.WINDOWFOCUSLOST:
                    focused = False
                    held.clear()
                    emit()
                elif event.type == pygame.WINDOWFOCUSGAINED:
                    focused = True
                    held.clear()
                elif event.type == pygame.KEYDOWN and focused:
                    if event.key in motion:
                        held.add(event.key)
                        emit()
                    elif event.key in (pygame.K_LEFTBRACKET, pygame.K_RIGHTBRACKET):
                        emit(1.0 if event.key == pygame.K_LEFTBRACKET else 0.0)
                elif event.type == pygame.KEYUP and event.key in motion:
                    held.discard(event.key)
                    emit()
            emit()
            screen.fill((28, 32, 39))
            lines = [
                "OpenYAM - SIMULATION ONLY",
                "Click this window, then hold a key to jog the virtual arm.",
                "W / S: forward / back     A / D: left / right",
                "Q / E: up / down",
                "R / F: roll     T / G: pitch     Y / H: yaw",
                "[ / ]: open / close virtual gripper",
                "Release keys or leave this window to stop jogging.",
                "Esc: close keyboard. Ctrl-C in terminal: stop all modules.",
                "Viewer: http://127.0.0.1:8095",
                "Held: " + (" ".join(pygame.key.name(k) for k in sorted(held)) or "none"),
            ]
            for index, text in enumerate(lines):
                color = (110, 220, 180) if index == 0 else (220, 225, 235)
                screen.blit(font.render(text, True, color), (18, 20 + index * 36))
            pygame.display.flip()
            clock.tick(50)
    except BrokenPipeError:
        pass
    finally:
        held.clear()
        try:
            emit()
        except BrokenPipeError:
            pass
        pygame.quit()


if __name__ == "__main__":
    if sys.argv[1:] != ["--ui"] or os.environ.get("DIMOS_SIMULATION") != "mujoco":
        raise SystemExit("Use the fixed simulation launcher.")
    run_ui()
