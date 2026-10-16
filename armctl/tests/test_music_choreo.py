"""Tests for armctl.music (analysis) and armctl.choreo (dance generation), plus the music routes.

Run: .venv/bin/python -m pytest armctl/tests/test_music_choreo.py -q
Songs are synthesized (armctl/tests/synth_song.py), so beat times, bars and the energy change are known.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("librosa")

from armctl import choreo as C  # noqa: E402
from armctl import fk  # noqa: E402
from armctl import music as M  # noqa: E402
from armctl import playable as P  # noqa: E402
from armctl.tests.synth_song import synth_song, write_wav  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
BEAT_DANCE_WAV = REPO / "experiments/2026-10-16-linux-real-arm/media/audio/beat-dance-100bpm.wav"
HAVE_FFMPEG = shutil.which("ffmpeg") is not None


def _fk_available() -> bool:
    try:
        fk.checker()
        return True
    except Exception:
        return False


# ------------------------------------------------------------------ fixtures
SONGS = {  # name: (bpm, bars, change_bar, lead, as_mp3)
    "s120": (120.0, 24, 12, 0.7, False),
    "s87": (87.0, 20, 8, 0.3, True),
    "long": (124.0, 64, 40, 0.5, False),
}


@pytest.fixture(scope="module")
def songs(tmp_path_factory):
    d = tmp_path_factory.mktemp("songs")
    out = {}
    for name, (bpm, bars, cb, lead, mp3) in SONGS.items():
        y, beats, sr = synth_song(bpm, bars, cb, lead)
        p = d / f"{name}.wav"
        write_wav(p, y, sr)
        if mp3 and HAVE_FFMPEG:
            q = p.with_suffix(".mp3")
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(p), "-b:a", "192k", str(q)], check=True)
            p = q
        out[name] = {"path": p, "beats": beats, "bpm": bpm, "change": lead + cb * 4 * 60.0 / bpm,
                     "an": M.analyze(p)}
    return out


# ------------------------------------------------------------------ analysis
@pytest.mark.parametrize("name", ["s120", "s87", "long"])
def test_tempo_within_2_percent(songs, name):
    s = songs[name]
    assert abs(s["an"]["tempo_bpm"] - s["bpm"]) / s["bpm"] < 0.02


@pytest.mark.parametrize("name", ["s120", "s87"])
def test_beats_aligned_within_30ms(songs, name):
    s = songs[name]
    det = np.array(s["an"]["beats"])
    true = s["beats"]
    err_true = np.array([np.min(np.abs(det - t)) for t in true])  # every true beat is found
    assert err_true.max() < 0.030, f"worst true beat off by {err_true.max() * 1000:.1f} ms"
    inside = det[(det > true[0] - 0.1) & (det < true[-1] + 0.1)]
    err_det = np.array([np.min(np.abs(true - t)) for t in inside])  # and nothing extra in between
    assert err_det.max() < 0.030


@pytest.mark.parametrize("name", ["s120", "s87"])
def test_downbeats_on_bar_lines(songs, name):
    s = songs[name]
    period = 60.0 / s["bpm"]
    lead = s["beats"][0]
    for t in s["an"]["downbeats"]:
        k = (t - lead) / period
        assert abs(k - 4 * round(k / 4)) < 0.1, f"downbeat at {t:.3f} s is beat {k:.2f}"


@pytest.mark.parametrize("name", ["s120", "s87", "long"])
def test_section_boundary_at_energy_change(songs, name):
    s = songs[name]
    an = s["an"]
    bar = 4 * 60.0 / s["bpm"]
    starts = [x["start"] for x in an["sections"][1:]]
    assert any(abs(t - s["change"]) < bar + 0.05 for t in starts), (starts, s["change"])
    before = [x for x in an["sections"] if x["end"] <= s["change"] + 0.1]
    after = [x for x in an["sections"] if x["start"] >= s["change"] - 0.1]
    assert max(x["energy"] for x in before) < min(x["energy"] for x in after)
    assert after[0]["level"] == "high" and before[0]["level"] == "low"
    assert any(abs(m["t"] - s["change"]) < bar + 0.05 for m in an["key_moments"])


def test_excerpt_long_song(songs):
    s = songs["long"]
    an = s["an"]
    x = an["excerpt"]
    assert x["duration"] <= M.MAX_EXCERPT_S + 1e-6
    assert x["start_beat"] in an["downbeat_beats"]
    assert x["mean_energy"] > np.mean(an["beat_energy"]) + 0.1  # it picked the loud part
    assert x["end"] > s["change"]


def test_short_song_window_is_whole_song(songs):
    an = songs["s120"]["an"]
    assert an["excerpt"]["start_beat"] == an["downbeat_beats"][0]
    assert an["excerpt"]["end_beat"] == len(an["beats"]) - 1


def test_analysis_json_roundtrip(songs, tmp_path):
    an = songs["s120"]["an"]
    p = M.save_analysis(an, tmp_path / "a.json")
    back = json.loads(p.read_text())
    assert back["tempo_bpm"] == an["tempo_bpm"] and len(back["beats"]) == len(an["beats"])
    assert "summary" not in back and "BPM" in M.summary(back)


@pytest.mark.skipif(not BEAT_DANCE_WAV.exists(), reason="beat-dance wav not generated")
def test_beat_dance_wav_100bpm():
    an = M.analyze(BEAT_DANCE_WAV)
    assert abs(an["tempo_bpm"] - 100.0) < 2.0
    true = 1.0 + np.arange(52) * 0.6  # 1 s of leading silence, then 52 beats
    det = np.array(an["beats"])
    assert max(np.min(np.abs(det - t)) for t in true) < 0.030
    assert abs(an["beats"][0] - 1.0) < 0.030


def test_load_audio_mp3_matches_wav(songs, tmp_path):
    if not HAVE_FFMPEG:
        pytest.skip("no ffmpeg")
    y, _, sr = synth_song(100, 4, 2, 0.2)
    w = tmp_path / "x.wav"
    write_wav(w, y, sr)
    m = tmp_path / "x.mp3"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(w), str(m)], check=True)
    a, b = M.load_audio(w), M.load_audio(m)
    assert abs(len(a) - len(b)) < 0.05 * M.SR  # same length within 50 ms (no encoder-delay offset)


# ------------------------------------------------------------------ choreography
def test_d_max_matches_the_cubic_rule():
    for T in (0.3, 0.5, 0.6, 1.2, 2.4):
        d = C.d_max(T)
        assert 1.5 * d / T <= 0.6 * C.CAP_MARGIN + 1e-9
        assert 6 * d / T**2 <= 2.0 * C.CAP_MARGIN + 1e-9
    assert abs(C.d_max(1.2) - 0.432) < 1e-3  # make_beat_dance: ~0.45 rad per 2-beat move at 100 BPM


def _segments_ok(pl):
    kf = pl["keyframes"]
    for a, b in zip(kf, kf[1:]):
        T = b["t"] - a["t"]
        d = np.max(np.abs(np.array(b["q"]) - np.array(a["q"])))
        assert 1.5 * d / T <= 0.6 + 1e-6 and 6 * d / T**2 <= 2.0 + 1e-6, (a, b)


@pytest.mark.parametrize("style", C.STYLES)
@pytest.mark.parametrize("name", ["s120", "s87", "long"])
def test_choreograph_valid_and_on_beat(songs, name, style):
    an = songs[name]["an"]
    pl, rep, plan = C.choreograph(an, style=style, seed=1, name="t-dance", run_fk=_fk_available())
    assert rep["validation"]["ok"], P.report(rep["validation"])
    assert pl["caps"] == "dance"
    assert rep["duration"] <= P.MAX_DURATION_S
    kf = pl["keyframes"]
    assert kf[0]["t"] == 0 and np.allclose(kf[0]["q"], C.READY) and np.allclose(kf[-1]["q"], C.READY)
    w = rep["window"]
    rel = np.array(an["beats"][w["start_beat"]:w["end_beat"] + 1]) - an["beats"][w["start_beat"]]
    for k in kf:  # every keyframe lands on a detected beat
        assert np.min(np.abs(rel - k["t"])) < 0.002, k["t"]
    assert abs(kf[-1]["t"] - rel[-1]) < 0.002
    _segments_ok(pl)
    # the dance envelope, explicitly (both checks) as in make_beat_dance.py
    from armctl.envelope import Envelope
    prev = P._ENV
    P.set_envelope(P._Envelope(Envelope(vel_caps=0.6, acc_caps=2.0)))
    try:
        r = P._validate(pl)
    finally:
        P.set_envelope(prev)
    assert r["ok"], P.report(r)
    if _fk_available():
        assert rep["fk"]["ok"] and rep["fk"]["min_tip_z"] >= fk.MIN_TIP_Z
    # low-energy blocks pick quiet motifs, high-energy blocks energetic ones
    pools = C.POOLS[style]
    for b in rep["blocks"]:
        assert b["motif"] in set(pools["low"] + pools["mid"] + pools["high"] + [C.ACCENT[style], "nod", "breathe", "hold"])
    assert len(plan["segments"]) == len(rep["blocks"])


def test_fast_tempo_shrinks_moves_not_speed(songs):
    # same plan on a fast and a slow song: the fast one gets smaller amplitudes
    slow, fast = songs["s87"]["an"], songs["s120"]["an"]
    seg = [{"start_beat": 0, "end_beat": 16, "motif": "pump", "amplitude": 1.5, "note": ""}]
    a_slow = C.choreograph(slow, plan={"segments": seg}, run_fk=False)[1]["blocks"][0]
    a_fast = C.choreograph(fast, plan={"segments": seg}, run_fk=False)[1]["blocks"][0]
    assert a_fast["amplitude"] < a_slow["amplitude"] <= 1.5
    assert a_fast["limited_by"] == "caps"


def test_plan_override_and_gaps(songs):
    an = songs["s120"]["an"]
    plan = {"style": "groove", "window": {"start_beat": 48, "end_beat": 80},
            "segments": [{"start_beat": 4, "end_beat": 12, "motif": "sway", "amplitude": 0.5, "note": "mine"},
                         {"start_beat": 16, "end_beat": 32, "motif": "hit", "amplitude": 1.0}]}
    pl, rep, used = C.choreograph(an, plan=plan, name="t-plan", run_fk=False)
    assert rep["validation"]["ok"]
    assert [b["motif"] for b in rep["blocks"]] == ["hold", "sway", "hold", "hit"]
    assert rep["blocks"][1]["note"] == "mine"
    assert rep["window"]["start_beat"] == 48 and rep["window"]["n_beats"] == 32
    assert abs(pl["keyframes"][-1]["t"] - (an["beats"][80] - an["beats"][48])) < 0.002
    # hold keyframes stay at ready
    for k in pl["keyframes"]:
        if k["t"] < an["beats"][52] - an["beats"][48] - 1e-6:
            assert np.allclose(k["q"], C.READY)


@pytest.mark.parametrize("bad, msg", [
    ([{"start_beat": 0, "end_beat": 8, "motif": "moonwalk"}], "unknown motif"),
    ([{"start_beat": 0, "end_beat": 8, "motif": "nod"}, {"start_beat": 4, "end_beat": 12, "motif": "sway"}], "overlap"),
    ([{"start_beat": 0, "end_beat": 999, "motif": "nod"}], "must satisfy"),
    ([{"start_beat": 0, "end_beat": 8, "motif": "nod", "amplitude": 3}], "amplitude"),
    ([], "non-empty"),
])
def test_plan_errors(songs, bad, msg):
    with pytest.raises(ValueError, match=msg):
        C.choreograph(songs["s120"]["an"], plan={"segments": bad}, run_fk=False)


def test_window_from_snaps_to_downbeat(songs):
    an = songs["long"]["an"]
    w = C.window_from(an, start=31.0)
    assert w["start_beat"] in an["downbeat_beats"] and w["duration"] <= M.MAX_EXCERPT_S
    assert abs(w["start"] - 31.0) <= 2 * an["beat_period"] + 1e-6
    with pytest.raises(ValueError):
        C.window_from(an, start_beat=10, end_beat=len(an["beats"]) - 1)  # far longer than 55 s


def test_write_clip_has_lead_silence(songs, tmp_path):
    import soundfile as sf
    s = songs["s120"]
    out = C.write_clip(s["path"], 24.69, 30.0, tmp_path / "c.wav")
    y, sr = sf.read(str(out))
    assert sr == 44100
    lead = int(C.BLEND_S * sr)
    assert np.abs(y[:lead]).max() == 0.0
    assert np.abs(y[lead:lead + int(0.1 * sr)]).max() > 0.05  # the downbeat hits right after the lead
    assert abs(len(y) / sr - (C.BLEND_S + 5.31 + 1.5)) < 0.05


def test_cli_dry_run_and_motifs(songs, capsys):
    assert C.main(["motifs"]) == 0
    assert "sway" in capsys.readouterr().out


# ------------------------------------------------------------------ routes
def test_music_routes(songs, tmp_path, monkeypatch):
    pytest.importorskip("fastapi")
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    import sys
    sys.path.insert(0, str(REPO / "dashboard"))
    import music_routes as R

    for attr, sub in (("INBOX", "inbox"), ("ANALYSIS_DIR", "analysis"), ("CLIPS_DIR", "clips"), ("PLANS_DIR", "plans")):
        monkeypatch.setattr(M, attr, tmp_path / sub)
    monkeypatch.setattr(P, "PLAYABLES_DIR", tmp_path / "playables")
    monkeypatch.setattr(R, "LOCAL_HOSTS", R.LOCAL_HOSTS | {"testclient"})
    app = FastAPI()
    R.add_music_routes(app)
    c = TestClient(app)
    data = songs["s120"]["path"].read_bytes()
    r = c.post("/api/music/upload?filename=My Song!.wav", content=data, headers={"content-type": "audio/wav"})
    assert r.status_code == 200, r.text
    assert r.json()["slug"] == "my-song" and abs(r.json()["tempo_bpm"] - 120) < 2.4
    assert c.post("/api/music/upload?filename=a.txt", content=b"x").status_code == 415
    assert c.post("/api/music/upload?filename=a.mp3", content=b"x", headers={"content-type": "text/plain"}).status_code == 415
    monkeypatch.setattr(R, "MAX_UPLOAD", 1000)
    assert c.post("/api/music/upload?filename=b.wav", content=data, headers={"content-type": "audio/wav"}).status_code == 413
    assert [s["slug"] for s in c.get("/api/music/songs").json()] == ["my-song"]
    an = c.get("/api/music/analysis/my-song").json()
    assert an["beats"] and "summary" in an
    assert c.get("/api/music/analysis/nope").status_code == 404
    au = c.get("/api/music/audio/my-song", headers={"Range": "bytes=0-99"})
    assert au.status_code == 206 and len(au.content) == 100
    r = c.post("/api/music/choreograph", json={"song": "my-song", "style": "hype"})
    assert r.status_code == 200 and r.json()["report"]["validation"]["ok"] and not r.json()["saved"]
    assert len(r.json()["samples"]["q"]) > 100
    plan = r.json()["plan"]
    plan["segments"] = [{"start_beat": 0, "end_beat": 8, "motif": "nod", "amplitude": 0.5}]
    r = c.post("/api/music/choreograph", json={"song": "my-song", "plan": plan, "save": "my-dance"})
    assert r.status_code == 200, r.text
    assert r.json()["saved"] and r.json()["report"]["command"].startswith(
        "experiments/2026-10-16-linux-real-arm/session_play.sh my-dance --caps dance --audio music/clips/my-dance.wav")
    assert (tmp_path / "playables/my-dance.json").exists() and (tmp_path / "plans/my-dance.json").exists()
    assert c.get("/api/music/clip/my-dance").status_code == 200
    bad = c.post("/api/music/choreograph", json={"song": "my-song", "plan": {"segments": [{"start_beat": 0, "end_beat": 4, "motif": "x"}]}})
    assert bad.status_code == 400 and "unknown motif" in bad.text
    monkeypatch.setattr(R, "LOCAL_HOSTS", {"127.0.0.1"})
    assert c.post("/api/music/choreograph", json={"song": "my-song", "save": "x"}).status_code == 403
