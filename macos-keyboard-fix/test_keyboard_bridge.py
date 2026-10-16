"""Exercise watchdog and child cleanup using real OS pipes, without robotics."""
import json
import subprocess
import sys
import threading
import time
import types
import unittest
from unittest.mock import patch

import macos_keyboard_ui as bridge


class BridgeTests(unittest.TestCase):
    def exercise(self, child_code, fail_on_zero=False):
        original_popen = subprocess.Popen
        children = []
        samples = []
        stop = threading.Event()
        config_module = types.ModuleType("dimos.core.global_config")
        config_module.global_config = types.SimpleNamespace(simulation="mujoco")
        keyboard_module = types.ModuleType("dimos.teleop.keyboard.keyboard_teleop_module")
        keyboard_module._twist_from_keys = lambda keys, **kw: ((0.05 if keys else 0, 0, 0), (0, 0, 0))

        def publish(**kwargs):
            samples.append((time.monotonic(), kwargs))
            if fail_on_zero and not kwargs:
                raise RuntimeError("synthetic closed stream")

        module = types.SimpleNamespace(
            config=types.SimpleNamespace(linear_speed=0.05, angular_speed=0.5),
            _stop_event=stop, _publish_twist=publish,
            _publish_gripper_command=lambda **kw: None,
        )

        def spawn(_argv, **kwargs):
            self.child_env = kwargs.get("env")
            process = original_popen([sys.executable, "-u", "-c", child_code], **kwargs)
            children.append(process)
            return process

        timer = threading.Timer(0.8, stop.set)
        timer.start()
        try:
            with patch.dict(sys.modules, {
                "dimos.core.global_config": config_module,
                "dimos.teleop.keyboard.keyboard_teleop_module": keyboard_module,
            }), patch.object(bridge.subprocess, "Popen", spawn):
                if fail_on_zero:
                    with self.assertRaises(RuntimeError):
                        bridge.run_bridge(module)
                else:
                    bridge.run_bridge(module)
        finally:
            timer.cancel()
        self.assertTrue(children)
        self.assertTrue(all(p.poll() is not None for p in children), "UI child leaked")
        return samples

    def test_partial_packet_cannot_block_stale_input_stop(self):
        samples = self.exercise(
            'import json,time,sys; print(json.dumps({"keys":[1],"time":time.monotonic()}),flush=True); '
            'sys.stdout.write("{partial"); sys.stdout.flush(); time.sleep(10)'
        )
        first = next(t for t, kw in samples if kw.get("linear", [0])[0] != 0)
        zero = next(t for t, kw in samples if not kw and t > first)
        self.assertLess(zero - first, 0.55)

    def test_stale_packet_is_never_applied(self):
        samples = self.exercise(
            'import json,time; print(json.dumps({"keys":[1],"time":time.monotonic()-10}),flush=True); time.sleep(10)'
        )
        self.assertFalse(any(kw.get("linear", [0])[0] != 0 for _, kw in samples))

    def test_closed_stream_still_reaps_child(self):
        self.exercise('import time; time.sleep(10)', fail_on_zero=True)

    def test_eof_publishes_zero(self):
        samples = self.exercise('pass')
        self.assertEqual(samples[-1][1], {})

    def test_child_inherits_simulation_mode_without_launcher_env(self):
        with patch.dict(bridge.os.environ):
            bridge.os.environ.pop("DIMOS_SIMULATION", None)
            self.exercise('pass')
        self.assertEqual(self.child_env["DIMOS_SIMULATION"], "mujoco")


if __name__ == "__main__":
    unittest.main()
