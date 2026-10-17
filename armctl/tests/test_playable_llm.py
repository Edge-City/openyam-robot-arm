"""Offline tests of the English -> playable repair loop (no LLM is called)."""

from armctl import llm_author as L
from armctl import playable as P

READY = [0.0, 0.5, 0.8, 0.0, 0.0, 0.0]


def _pl(t1, j1):
    return {"name": "fake", "space": "joint", "start": "ready", "loop": 1, "speed": 1.0, "notes": "x",
            "keyframes": [{"t": 0, "q": READY, "grip": 0, "ease": "cubic"},
                          {"t": t1, "q": [j1, 0.5, 0.8, 0, 0, 0], "grip": 0, "ease": "cubic"},
                          {"t": t1 * 2, "q": READY, "grip": 0, "ease": "cubic"}]}


def test_repair_loop_feeds_violations_back(monkeypatch, tmp_path):
    monkeypatch.setattr(L, "LOG_PATH", tmp_path / "llm.log")
    seen = []
    replies = [_pl(0.5, 5.0), _pl(0.5, 0.4), _pl(3.0, 0.4)]  # out of range, too fast, ok

    def fake(backend, system, user, model, tag):
        seen.append(user)
        return {**replies[len(seen) - 1], "_latency_s": 0.01}

    monkeypatch.setattr(L, "call_llm", fake)
    res = L.author("test", backend="cli", rounds=3, echo=lambda *a: None)
    assert res["ok"] and len(res["attempts"]) == 3
    assert "keyframe_out_of_range j1" in seen[1] and "soft limit" in seen[1]
    assert "velocity j1" in seen[2]
    assert res["playable"]["author"].startswith("llm:")
    assert (tmp_path / "llm.log").exists()


def test_retime_offered_when_only_speed_violations_remain(monkeypatch, tmp_path):
    monkeypatch.setattr(L, "LOG_PATH", tmp_path / "llm.log")
    monkeypatch.setattr(L, "call_llm", lambda *a, **k: {**_pl(0.5, 0.4), "_latency_s": 0.01})
    res = L.author("test", backend="cli", rounds=1, echo=lambda *a: None)
    assert not res["ok"] and res.get("retime_available")
    res = L.author("test", backend="cli", rounds=1, allow_retime=True, echo=lambda *a: None)
    assert res["ok"] and res["retimed"] and P.validate(res["playable"])["ok"]


def test_extract_json_from_fenced_text():
    assert L._extract_json('here:\n```json\n{"a": 1}\n```') == {"a": 1}


def test_system_prompt_mentions_limits_and_examples():
    s = L.system_prompt()
    for needle in ("j4", "soft limits", "rad/s", "ready", "wave:", "nod:"):
        assert needle in s
