"""Apply the macOS keyboard-window fix to an installed DimOS 0.0.14.

Run with the DimOS environment's Python:  .venv/bin/python macos-keyboard-fix/apply.py
Refuses to touch any keyboard source other than the verified 0.0.14 release file.
"""
import difflib
import hashlib
import os
import sysconfig
from pathlib import Path

here = Path(__file__).resolve().parent
package = Path(sysconfig.get_paths()["purelib"]) / "dimos/teleop/keyboard"
target = package / "keyboard_teleop_module.py"
original = (here / "keyboard_teleop_module.release-0.0.14.py").read_bytes()
assert hashlib.sha256(original).hexdigest() == "60282642249b2728694de9c071e5ccaf1ff4d6e940163827f571ad7447792d4a"
text = original.decode().replace("import os\n", "import os\nimport sys\n", 1)
text = text.replace('os.environ["SDL_VIDEODRIVER"] = "x11"',
                    'if sys.platform != "darwin":\n    os.environ["SDL_VIDEODRIVER"] = "x11"', 1)
text = text.replace("    def _pygame_loop(self) -> None:\n", """    def _pygame_loop(self) -> None:
        if sys.platform == "darwin":
            from dimos.teleop.keyboard.macos_keyboard_ui import run_bridge

            run_bridge(self)
            return
""", 1)
updated = text.encode()
assert target.read_bytes() in (original, updated), "Unexpected installed keyboard source; refusing patch"

# Atomic replacement also avoids modifying a uv-cache hardlink in place.
for destination, content in [(target, updated), (package / "macos_keyboard_ui.py", (here / "macos_keyboard_ui.py").read_bytes())]:
    temporary = destination.with_suffix(".py.openyam-tmp")
    temporary.write_bytes(content)
    os.replace(temporary, destination)
diff = difflib.unified_diff(original.decode().splitlines(True), text.splitlines(True),
                            fromfile="release/keyboard_teleop_module.py", tofile="local/keyboard_teleop_module.py")
assert "".join(diff) == (here / "macos-keyboard.patch").read_text(), "Patch drifted from macos-keyboard.patch"
print(f"Applied macOS keyboard fix to {target}")
