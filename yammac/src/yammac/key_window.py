"""Standalone pygame key window.

macOS (Cocoa) only allows windows on a process's main thread, but DimOS runs
module code on worker threads. This script runs as its own process so pygame
owns the main thread, and streams key events to stdout as JSON lines:

    {"t": "down", "k": <pygame key code>}
    {"t": "up", "k": <pygame key code>}
    {"t": "quit"}
"""

from __future__ import annotations

import json
import os
import sys

os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")

import pygame

CONTROLS = [
    ("W/S", "+X/-X (forward/back)"),
    ("A/D", "+Y/-Y (left/right)"),
    ("Q/E", "+Z/-Z (up/down)"),
    ("R/F", "+Roll/-Roll"),
    ("T/G", "+Pitch/-Pitch"),
    ("Y/H", "+Yaw/-Yaw"),
    ("[/]", "Open/close gripper"),
    ("ESC", "Quit"),
]


def emit(msg: dict[str, object]) -> None:
    sys.stdout.write(json.dumps(msg) + "\n")
    sys.stdout.flush()


def replay_script(script: str) -> None:
    """Headless test mode: YAMMAC_SCRIPTED_KEYS="w:2,q:1,[:0" holds each key for N seconds."""
    import time

    time.sleep(float(os.environ.get("YAMMAC_SCRIPT_DELAY", "15")))
    for step in script.split(","):
        name, _, hold = step.partition(":")
        code = pygame.key.key_code(name)
        emit({"t": "down", "k": code})
        time.sleep(float(hold or 0))
        emit({"t": "up", "k": code})
        time.sleep(0.5)
    time.sleep(3600)


def main() -> None:
    script = os.environ.get("YAMMAC_SCRIPTED_KEYS")
    if script:
        pygame.init()
        replay_script(script)
        return
    pygame.init()
    screen = pygame.display.set_mode((600, 360))
    pygame.display.set_caption("OpenYAM Keyboard Teleop (macOS)")
    font = pygame.font.Font(None, 28)
    clock = pygame.time.Clock()
    held: set[str] = set()

    running = True
    while running:
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                running = False
            elif event.type == pygame.KEYDOWN:
                if event.key == pygame.K_ESCAPE:
                    running = False
                else:
                    held.add(pygame.key.name(event.key))
                    emit({"t": "down", "k": event.key})
            elif event.type == pygame.KEYUP:
                held.discard(pygame.key.name(event.key))
                emit({"t": "up", "k": event.key})
            elif event.type == pygame.WINDOWFOCUSLOST:
                # Releasing focus must not leave the arm jogging.
                for name in list(held):
                    emit({"t": "up", "k": pygame.key.key_code(name)})
                held.clear()

        screen.fill((30, 30, 30))
        screen.blit(font.render("Keyboard Teleop - click here first", True, (255, 255, 255)), (20, 20))
        held_text = "Held: " + (" ".join(sorted(held)).upper() or "-")
        screen.blit(font.render(held_text, True, (100, 255, 100)), (20, 60))
        y = 100
        for key, desc in CONTROLS:
            screen.blit(font.render(f"{key}: {desc}", True, (180, 180, 180)), (20, y))
            y += 25
        pygame.display.flip()
        clock.tick(50)

    emit({"t": "quit"})
    pygame.quit()


if __name__ == "__main__":
    main()
