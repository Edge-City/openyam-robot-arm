"""Standalone runner for the music -> dance page (no CAN, no motors, no DimOS).

    .venv/bin/python dashboard/music_server.py   -> http://127.0.0.1:8093/

Mounts dashboard/music_routes.py, the read-only playable API, /urdf and /static. The
console (dashboard/server.py) can mount the same routes with ``add_music_routes(app)``.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import uvicorn
from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from music_routes import add_music_routes  # noqa: E402
from playable_preview_server import DEFAULT_URDF_DIR, add_playable_routes  # noqa: E402

STATIC_DIR = HERE / "static"


def build_app(urdf_dir: Path) -> FastAPI:
    app = FastAPI(title="OpenYAM music -> dance")
    add_music_routes(app)
    add_playable_routes(app)
    if urdf_dir.is_dir():
        app.mount("/urdf", StaticFiles(directory=urdf_dir), name="urdf")

    @app.get("/")
    def index():
        return RedirectResponse("/music")

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    return app


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8093)
    ap.add_argument("--urdf-dir", default=str(DEFAULT_URDF_DIR))
    a = ap.parse_args()
    print(f"music -> dance on http://{a.host}:{a.port}/music")
    uvicorn.run(build_app(Path(a.urdf_dir)), host=a.host, port=a.port, log_level="warning")


if __name__ == "__main__":
    main()
