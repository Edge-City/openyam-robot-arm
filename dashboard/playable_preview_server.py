"""Tiny read-only server for the playable preview page (no CAN, no motors, no DimOS).

Run (repo root):  .venv/bin/python dashboard/playable_preview_server.py   -> http://127.0.0.1:8092/

Serves:
  /                              dashboard/static/playable-preview.html
  /static/...                    dashboard/static (same files as the console)
  /urdf/...                      the yam_description URDF + meshes (as the console does)
  GET  /api/playables            [{name, author, ok, duration, n_violations, notes}]
  GET  /api/playables/{name}     {playable, validation, samples}   (samples at 50 Hz)
  POST /api/playables/validate   body = playable JSON -> same shape (nothing is saved)

It runs separately from dashboard/server.py so the console (which polls CAN) never has to
be restarted for it. To merge later: ``add_playable_routes(app)`` in server.py's build_app.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import uvicorn
from fastapi import Body, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
sys.path.insert(0, str(REPO))
from armctl import playable as P  # noqa: E402

STATIC_DIR = HERE / "static"
DEFAULT_URDF_DIR = Path.home() / ".local/share/dimos/repo/data/yam_description"
PREVIEW_RATE = 50.0


def _bundle(pl: dict) -> dict:
    r = P.validate(pl)
    try:
        s = P.sample(pl, PREVIEW_RATE)
    except P.SchemaError:
        s = None
    return {"playable": pl, "validation": r, "samples": s, "envelope": P.envelope().describe()}


def add_playable_routes(app: FastAPI) -> None:
    """Read-only playable API (plus a validate-only POST). Safe to mount on the console."""

    @app.get("/api/playables")
    def list_playables():
        out = []
        for name in P.list_playables():
            try:
                pl = P.load(name)
                r = P.validate(pl)
                out.append({"name": name, "author": pl.get("author"), "notes": pl.get("notes"),
                            "ok": r["ok"], "duration": r["duration"],
                            "n_violations": len(r["violations"])})
            except Exception as e:  # a broken file shouldn't hide the others
                out.append({"name": name, "ok": False, "error": f"{type(e).__name__}: {e}"})
        return JSONResponse(out, headers={"Cache-Control": "no-store"})

    @app.get("/api/playables/{name}")
    def get_playable(name: str):
        if not P.NAME_RE.match(name):
            raise HTTPException(400, "bad name")
        try:
            pl = P.load(name)
        except FileNotFoundError:
            raise HTTPException(404, "no such playable")
        return JSONResponse(_bundle(pl), headers={"Cache-Control": "no-store"})

    @app.post("/api/playables/validate")
    def validate_playable(pl: dict = Body(...)):
        try:
            return JSONResponse(_bundle(pl))
        except P.SchemaError as e:
            raise HTTPException(400, str(e))


def build_app(urdf_dir: Path) -> FastAPI:
    app = FastAPI(title="OpenYAM playable preview")
    app.add_middleware(CORSMiddleware, allow_origins=["http://127.0.0.1:8090", "http://localhost:8090"],
                       allow_methods=["GET", "POST"], allow_headers=["*"])
    add_playable_routes(app)
    if urdf_dir.is_dir():
        app.mount("/urdf", StaticFiles(directory=urdf_dir), name="urdf")

    @app.get("/")
    def index():
        return FileResponse(STATIC_DIR / "playable-preview.html", headers={"Cache-Control": "no-store"})

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    return app


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8092)
    ap.add_argument("--urdf-dir", default=str(DEFAULT_URDF_DIR))
    a = ap.parse_args()
    print(f"playable preview on http://{a.host}:{a.port}/  (envelope: {P.envelope().source})")
    uvicorn.run(build_app(Path(a.urdf_dir)), host=a.host, port=a.port, log_level="warning")


if __name__ == "__main__":
    main()
