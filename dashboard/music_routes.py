"""Music -> dance routes (analysis, choreography, audio). Nothing here moves the arm.

Mount on any FastAPI app with ``add_music_routes(app)`` (the console, or the standalone
``dashboard/music_server.py`` on :8093). Serves:

  GET  /music                          the page (static/music.html)
  GET  /api/music/songs                songs in music/inbox [{file, slug, size, analyzed, tempo_bpm, duration}]
  POST /api/music/upload?filename=x    raw audio body -> music/inbox/<slug>.<ext>, analyzed   (local clients only, <= 30 MB)
  GET  /api/music/analysis/{slug}      analysis JSON (computed and cached on first request)
  GET  /api/music/audio/{slug}         the song itself (range requests work, for seeking)
  GET  /api/music/motifs               the motif library, styles, caps
  POST /api/music/choreograph          {song, style, seed, start?, plan?, save?} -> playable, report, plan, samples
                                       (save=<name> writes playables/, music/plans/, music/clips/; local clients only)
  GET  /api/music/clip/{name}          music/clips/<name>.wav
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from fastapi import Body, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))
from armctl import choreo as C  # noqa: E402
from armctl import music as M  # noqa: E402
from armctl import playable as P  # noqa: E402

STATIC_DIR = HERE / "static"
MAX_UPLOAD = 30 * 1024 * 1024
LOCAL_HOSTS = {"127.0.0.1", "::1", "localhost"}
AUDIO_MIME = {".mp3": "audio/mpeg", ".wav": "audio/wav", ".flac": "audio/flac", ".ogg": "audio/ogg",
              ".oga": "audio/ogg", ".opus": "audio/ogg", ".m4a": "audio/mp4", ".aac": "audio/aac",
              ".aif": "audio/aiff", ".aiff": "audio/aiff", ".webm": "audio/webm"}
NO_STORE = {"Cache-Control": "no-store"}


def _local_only(request: Request) -> None:
    host = request.client.host if request.client else ""
    if host not in LOCAL_HOSTS:
        raise HTTPException(403, "writes are allowed from this machine only (open the page on 127.0.0.1)")


def _songs() -> dict[str, Path]:
    out: dict[str, Path] = {}
    if M.INBOX.is_dir():
        for p in sorted(M.INBOX.iterdir()):
            if p.is_file() and p.suffix.lower() in AUDIO_MIME:
                out.setdefault(M.slug(p.name), p)
    return out


def _song(slug: str) -> Path:
    p = _songs().get(slug)
    if p is None:
        raise HTTPException(404, f"no song {slug!r} in music/inbox")
    return p


def _analysis(slug: str, refresh: bool = False) -> dict:
    try:
        return M.analyze_cached(_song(slug), refresh=refresh)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(422, f"analysis failed: {type(e).__name__}: {e}")


def add_music_routes(app: FastAPI, page_path: str = "/music") -> None:
    @app.get(page_path)
    def music_page():
        return FileResponse(STATIC_DIR / "music.html", headers=NO_STORE)

    @app.get("/api/music/songs")
    def songs():
        out = []
        for s, p in _songs().items():
            row = {"file": p.name, "slug": s, "size": p.stat().st_size, "analyzed": False}
            ap = M.analysis_path(p.name)
            if ap.exists():
                try:
                    an = json.loads(ap.read_text())
                    row.update(analyzed=True, tempo_bpm=an.get("tempo_bpm"), duration=an.get("duration"))
                except Exception:
                    pass
            out.append(row)
        return JSONResponse(out, headers=NO_STORE)

    @app.post("/api/music/upload")
    async def upload(request: Request, filename: str):
        _local_only(request)
        ext = Path(filename).suffix.lower()
        ctype = (request.headers.get("content-type") or "").split(";")[0].strip().lower()
        if ext not in AUDIO_MIME:
            raise HTTPException(415, f"not an audio file extension: {ext or '(none)'} "
                                     f"(use {', '.join(sorted(AUDIO_MIME))})")
        if ctype and not (ctype.startswith("audio/") or ctype in ("application/octet-stream", "video/webm")):
            raise HTTPException(415, f"content-type {ctype} is not audio")
        clen = request.headers.get("content-length")
        if clen and clen.isdigit() and int(clen) > MAX_UPLOAD:
            raise HTTPException(413, f"file is larger than {MAX_UPLOAD // (1024 * 1024)} MB")
        data = bytearray()
        async for chunk in request.stream():
            data += chunk
            if len(data) > MAX_UPLOAD:
                raise HTTPException(413, f"file is larger than {MAX_UPLOAD // (1024 * 1024)} MB")
        if not data:
            raise HTTPException(400, "empty upload")
        M.INBOX.mkdir(parents=True, exist_ok=True)
        slug = M.slug(filename)
        dest = M.INBOX / f"{slug}{ext}"
        for other in M.INBOX.glob(f"{slug}.*"):  # one file per slug
            if other != dest and other.suffix.lower() in AUDIO_MIME:
                other.unlink()
        dest.write_bytes(bytes(data))
        try:
            import asyncio
            an = await asyncio.to_thread(M.analyze_cached, dest, True)
        except Exception as e:
            dest.unlink(missing_ok=True)
            raise HTTPException(422, f"could not analyze {filename}: {type(e).__name__}: {e}")
        return JSONResponse({"file": dest.name, "slug": slug, "tempo_bpm": an["tempo_bpm"],
                             "duration": an["duration"], "summary": M.summary(an)}, headers=NO_STORE)

    @app.get("/api/music/analysis/{slug}")
    def analysis(request: Request, slug: str, refresh: bool = False):
        if refresh:
            _local_only(request)
        an = _analysis(slug, refresh)
        return JSONResponse({**an, "summary": M.summary(an)}, headers=NO_STORE)

    @app.get("/api/music/audio/{slug}")
    def audio(slug: str):
        p = _song(slug)
        return FileResponse(p, media_type=AUDIO_MIME.get(p.suffix.lower(), "application/octet-stream"))

    @app.get("/api/music/motifs")
    def motifs():
        return JSONResponse({"motifs": [{"name": m.name, "unit_beats": m.unit, "desc": m.desc} for m in C.MOTIFS.values()],
                             "styles": list(C.STYLES), "caps": {"preset": C.CAPS, "vel": C.VEL_CAP, "acc": C.ACC_CAP},
                             "doc": C.motifs_doc(), "max_len": M.MAX_EXCERPT_S, "blend_s": C.BLEND_S})

    @app.post("/api/music/choreograph")
    def choreograph(request: Request, body: dict = Body(...)):
        slug = str(body.get("song", ""))
        style = body.get("style", "groove")
        if style not in C.STYLES:
            raise HTTPException(400, f"style must be one of {C.STYLES}")
        save = body.get("save")
        if save:
            _local_only(request)
            if not P.NAME_RE.match(str(save)):
                raise HTTPException(400, "save name: lowercase letters, digits and '-', at most 64")
        an = _analysis(slug)
        name = str(save) if save else P.slugify(f"{slug}-{style}")[:64]
        try:
            window = None
            if body.get("start") is not None:
                window = C.window_from(an, float(body["start"]), float(body.get("max_len", M.MAX_EXCERPT_S)))
            pl, rep, plan = C.choreograph(an, window, style, int(body.get("seed", 0) or 0), body.get("plan"),
                                          name=name)
        except (ValueError, TypeError, KeyError) as e:
            raise HTTPException(400, f"plan/window: {e}")
        s = P.sample(pl, 50)
        out = {"playable": pl, "report": rep, "plan": plan, "samples": {"t": s["t"], "q": s["q"], "rate": 50},
               "summary": C.summary(an, pl, rep), "saved": False, "name": name}
        if save:
            if not rep["ok"]:
                raise HTTPException(422, "not saved: validation or FK failed\n" + out["summary"])
            P.save(pl)
            M.PLANS_DIR.mkdir(parents=True, exist_ok=True)
            (M.PLANS_DIR / f"{name}.json").write_text(json.dumps(plan, indent=1) + "\n")
            w = pl["music"]["window"]
            C.write_clip(_song(slug), w["start"], w["end"], M.CLIPS_DIR / f"{name}.wav")
            out.update(saved=True, clip=f"/api/music/clip/{name}",
                       files=[f"playables/{name}.json", f"music/plans/{name}.json", f"music/clips/{name}.wav"])
        return JSONResponse(out, headers=NO_STORE)

    @app.get("/api/music/clip/{name}")
    def clip(name: str):
        if not P.NAME_RE.match(name):
            raise HTTPException(400, "bad name")
        p = M.CLIPS_DIR / f"{name}.wav"
        if not p.exists():
            raise HTTPException(404, "no such clip")
        return FileResponse(p, media_type="audio/wav")
