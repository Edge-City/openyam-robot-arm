"""KeyboardTeleopModule variant that hosts its pygame window in a subprocess.

Reuses the upstream module's key handling and publishing so the commands sent
to the coordinator are identical; only the window lives in another process.
"""

from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading
import time
from types import SimpleNamespace
from typing import Any

import pygame

from dimos.core.core import rpc
from dimos.teleop.keyboard.keyboard_teleop_module import (
    KeyboardTeleopModule,
    _twist_from_keys,
)
from dimos.utils.logging_config import setup_logger

logger = setup_logger()


class SubprocessKeyboardTeleopModule(KeyboardTeleopModule):
    _proc: subprocess.Popen[str] | None = None

    @rpc
    def start(self) -> None:
        # Skip KeyboardTeleopModule.start (it opens pygame on a worker thread).
        super(KeyboardTeleopModule, self).start()
        self._stop_event.clear()
        # The upstream module forces SDL_VIDEODRIVER=x11 at import; macOS has no X11.
        env = {k: v for k, v in os.environ.items() if k != "SDL_VIDEODRIVER"}
        self._proc = subprocess.Popen(
            [sys.executable, "-m", "yammac.key_window"],
            env=env,
            stdout=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
        self._events: queue.Queue[dict[str, Any]] = queue.Queue()
        threading.Thread(target=self._read_events, daemon=True).start()
        self._thread = threading.Thread(target=self._control_loop, daemon=True)
        self._thread.start()
        logger.info("Keyboard window started in subprocess", pid=self._proc.pid)

    @rpc
    def stop(self) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(2.0)
        if self._proc is not None and self._proc.poll() is None:
            self._proc.terminate()
            try:
                self._proc.wait(2.0)
            except subprocess.TimeoutExpired:
                self._proc.kill()
        super(KeyboardTeleopModule, self).stop()

    def _read_events(self) -> None:
        assert self._proc is not None and self._proc.stdout is not None
        for line in self._proc.stdout:
            try:
                self._events.put(json.loads(line))
            except json.JSONDecodeError:
                continue
        self._events.put({"t": "quit"})

    def _control_loop(self) -> None:
        held_motion_keys: set[int] = set()
        was_moving = False
        while not self._stop_event.is_set():
            while True:
                try:
                    msg = self._events.get_nowait()
                except queue.Empty:
                    break
                if msg["t"] == "quit":
                    self._stop_event.set()
                    break
                event_type = pygame.KEYDOWN if msg["t"] == "down" else pygame.KEYUP
                self._handle_pygame_event(
                    SimpleNamespace(type=event_type, key=msg["k"]), held_motion_keys
                )

            linear, angular = _twist_from_keys(
                held_motion_keys,
                linear_speed=self.config.linear_speed,
                angular_speed=self.config.angular_speed,
            )
            is_moving = any(value != 0.0 for value in (*linear, *angular))
            if is_moving or was_moving:
                self._publish_twist(linear=linear, angular=angular)
                was_moving = is_moving
            time.sleep(0.02)

        self._publish_twist()
