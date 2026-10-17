"""Tests for armctl.playable. Run: .venv/bin/python -m pytest armctl/tests -q"""

import copy
import json

import numpy as np
import pytest

from armctl import playable as P

READY = [0.0, 0.5, 0.8, 0.0, 0.0, 0.0]


def mk(keyframes, **kw):
    p = {"name": "t", "author": "test", "created": "2026-10-16T00:00:00+05:30", "space": "joint",
         "start": "ready", "keyframes": keyframes, "loop": 1, "speed": 1.0}
    p.update(kw)
    return p


def kf(t, q, ease="cubic", grip=None):
    d = {"t": t, "q": list(q), "ease": ease}
    if grip is not None:
        d["grip"] = grip
    return d


def q_with(j, v, base=READY):
    q = list(base)
    q[j] = v
    return q


def kinds(r):
    return {v["kind"] for v in r["violations"]}


def slow_ok():
    # j1 0 -> 0.2 -> 0 over 2 s segments: well under 0.3 rad/s and 1 rad/s^2
    return mk([kf(0, READY), kf(2.0, q_with(0, 0.2)), kf(4.0, READY)])


@pytest.mark.parametrize("name", P.list_playables())
def test_sample_playables_validate(name):
    r = P.validate(P.load(name))
    assert r["ok"], P.report(r)
    assert r["duration"] <= P.MAX_DURATION_S
    assert len(r["max_vel"]) == 6 and len(r["max_acc"]) == 6


def test_samples_exist():
    assert {"wave", "nod", "happy-dance", "look-around"} <= set(P.list_playables())


def test_simple_ok_and_stats():
    r = P.validate(slow_ok())
    assert r["ok"], P.report(r)
    assert r["duration"] == pytest.approx(4.0)
    assert r["max_vel"][0] == pytest.approx(1.5 * 0.2 / 2.0, rel=0.02)  # smoothstep peak 1.5 d/T
    assert r["transition"] is None


def test_keyframe_out_of_range():
    lo4 = P.envelope().soft[3][0]
    r = P.validate(mk([kf(0, READY), kf(30, q_with(3, lo4 - 0.05)), kf(60, READY)]))
    assert not r["ok"]
    assert "keyframe_out_of_range" in kinds(r)
    v = next(v for v in r["violations"] if v["kind"] == "keyframe_out_of_range")
    assert v["joint"] == "j4" and v["t"] == 30 and v["limit"] == pytest.approx(lo4, abs=1e-3)


def test_path_out_of_range_is_caught_on_samples():
    # the path crosses the soft limit around a keyframe; reported per sample run with its span
    hi1 = P.envelope().soft[0][1]
    r = P.validate(mk([kf(0, READY), kf(50, q_with(0, hi1 + 0.01)), kf(59, q_with(0, hi1 - 0.5))]))
    v = next(v for v in r["violations"] if v["kind"] == "path_out_of_range")
    assert v["joint"] == "j1" and v["t_play_span"][0] < 50 < v["t_play_span"][1]


def test_velocity_violation():
    r = P.validate(mk([kf(0, READY), kf(1.0, q_with(0, 0.5)), kf(4.0, READY)]))
    assert not r["ok"]
    vs = [v for v in r["violations"] if v["kind"] == "velocity"]
    assert vs and vs[0]["joint"] == "j1"
    assert abs(vs[0]["value"]) == pytest.approx(0.75, rel=0.02)  # 1.5 * 0.5 / 1.0
    assert vs[0]["t"] is not None and 0 < vs[0]["t"] < 1.0


def test_acceleration_violation_without_velocity_violation():
    # d=0.1 over 0.6 s: peak vel 0.25 (< 0.3) but peak acc 6*0.1/0.36 = 1.67 (> 1.0)
    r = P.validate(mk([kf(0, READY), kf(0.6, q_with(5, 0.1)), kf(3.0, READY)]))
    assert "acceleration" in kinds(r)
    assert "velocity" not in kinds(r)


def test_linear_from_rest_is_an_acceleration_spike():
    r = P.validate(mk([kf(0, READY), kf(4.0, q_with(0, 0.2), ease="linear"), kf(8.0, READY)]))
    assert "acceleration" in kinds(r)


def test_speed_scaling_applies():
    p = slow_ok()
    p["speed"] = 2.0
    r = P.validate(p)
    assert r["duration"] == pytest.approx(2.0)
    assert r["max_vel"][0] == pytest.approx(2 * 1.5 * 0.2 / 2.0, rel=0.02)
    p["speed"] = 0.5
    assert P.validate(p)["duration"] == pytest.approx(8.0)


def test_loops_multiply_duration_and_need_closed_loop():
    p = slow_ok()
    p["loop"] = 3
    r = P.validate(p)
    assert r["ok"] and r["duration"] == pytest.approx(12.0)
    p["keyframes"][-1]["q"] = q_with(0, 0.1)
    assert "loop_discontinuity" in kinds(P.validate(p))


def test_non_monotonic_t():
    r = P.validate(mk([kf(0, READY), kf(2.0, q_with(0, 0.1)), kf(1.5, READY)]))
    assert not r["ok"] and "non_monotonic_t" in kinds(r)
    r = P.validate(mk([kf(0, READY), kf(2.0, q_with(0, 0.1)), kf(2.0, READY)]))
    assert "non_monotonic_t" in kinds(r)


def test_too_long():
    r = P.validate(mk([kf(0, READY), kf(40, q_with(0, 0.2)), kf(80, READY)]))
    assert "too_long" in kinds(r)


def test_too_long_counts_loops_and_speed():
    p = mk([kf(0, READY), kf(10, q_with(0, 0.2)), kf(20, READY)], loop=2, speed=0.5)
    assert "too_long" in kinds(P.validate(p))  # 20 / 0.5 * 2 = 80 s


def test_start_transition_prepended_and_checked():
    q0 = q_with(0, 0.6)
    r = P.validate(mk([kf(0, q0), kf(2.0, q0, ease="hold")]))
    assert r["transition"] is not None and r["transition"]["from"] == "ready"
    assert r["ok"], P.report(r)  # transition is sized to the caps
    assert r["duration"] == pytest.approx(r["transition"]["duration"] + 2.0, abs=0.02)


def test_hold_jump():
    r = P.validate(mk([kf(0, READY), kf(2.0, q_with(0, 0.2), ease="hold")]))
    assert "hold_jump" in kinds(r)


def test_schema_errors():
    assert "schema" in kinds(P.validate(mk([kf(0, READY[:5])])))
    assert "schema" in kinds(P.validate(mk([kf(0.5, READY), kf(2, READY)])))  # first t != 0
    assert "schema" in kinds(P.validate(mk([kf(0, READY)], start="nowhere")))
    assert "schema" in kinds(P.validate(mk([kf(0, READY), kf(1, READY, ease="bouncy")])))
    assert "schema" in kinds(P.validate(mk([kf(0, READY, grip=1.5)])))
    r = P.validate(mk([kf(0, READY)], space="cartesian"))
    assert not r["ok"] and "unsupported" in kinds(r)


def test_sample_grid_and_endpoints():
    s = P.sample(slow_ok(), rate=100)
    assert len(s["t"]) == 401
    assert np.allclose(s["q"][0], READY) and np.allclose(s["q"][-1], READY)
    assert s["q"][200][0] == pytest.approx(0.2)
    assert all(b > a for a, b in zip(s["t"], s["t"][1:]))
    # every sample of a valid playable is inside the soft box
    for name in P.list_playables():
        s = P.sample(P.load(name), rate=100)
        Q = np.array(s["q"])
        for j, (lo, hi) in enumerate(P.envelope().soft):
            assert Q[:, j].min() >= lo - 1e-9 and Q[:, j].max() <= hi + 1e-9, (name, j)


def test_retime_fixes_speed_violations():
    p = mk([kf(0, READY), kf(1.0, q_with(0, 0.5)), kf(1.5, q_with(0, 0.4)), kf(3.0, READY)])
    r0 = P.validate(p)
    assert not r0["ok"] and kinds(r0) <= P.SPEED_KINDS
    fixed = P.retime(p)
    r1 = P.validate(fixed)
    assert r1["ok"], P.report(r1)
    assert p["keyframes"][1]["t"] == 1.0  # original untouched
    # "just enough": no more than ~25% slower than the analytic minimum for the worst segment
    T_min = max(1.5 * 0.5 / 0.3, (6 * 0.5 / 1.0) ** 0.5)
    seg1 = fixed["keyframes"][1]["t"] - fixed["keyframes"][0]["t"]
    assert T_min <= seg1 <= T_min * 1.25
    assert [k["q"] for k in fixed["keyframes"]] == [k["q"] for k in p["keyframes"]]


def test_retime_refuses_non_speed_violations():
    hi1 = P.envelope().soft[0][1]
    with pytest.raises(ValueError):
        P.retime(mk([kf(0, READY), kf(0.5, q_with(0, hi1 + 0.2)), kf(1.0, READY)]))


def test_save_load_roundtrip(tmp_path):
    p = slow_ok()
    p["name"] = "roundtrip-test"
    path = P.save(p, tmp_path)
    assert path.name == "roundtrip-test.json"
    assert P.load(path) == json.loads(json.dumps(p))
    with pytest.raises(ValueError):
        P.save({**p, "name": "Bad Name"}, tmp_path)


def test_custom_envelope_caps():
    old = P.envelope()
    try:
        P.set_envelope(P._Envelope(vel_cap=0.05))
        assert "velocity" in kinds(P.validate(slow_ok()))
    finally:
        P.set_envelope(old)


def test_caps_preset_travels_with_playable():
    """A playable declaring "caps": "dance" validates at the dance caps; unknown presets are refused."""
    fast = P.load("beat-dance")
    assert fast.get("caps") == "dance" and P.validate(fast)["ok"]
    assert not P.validate({**fast, "caps": "default"})["ok"]  # too fast for the normal caps
    bad = P.validate({**fast, "caps": "ludicrous"})
    assert not bad["ok"] and "unknown caps preset" in bad["violations"][0]["msg"]


def test_format_version_compat():
    base = P.load("nod")
    assert P.compat(base)[0]  # legacy: no fields
    v1 = {**base, "format": "openyam-playable", "version": "1.0"}
    assert P.validate(v1)["ok"]
    assert P.validate({**v1, "version": "1.7", "future_field": {"x": 1}})["ok"]  # minor bump + unknown field
    r = P.validate({**v1, "version": "2.0"})
    assert not r["ok"] and r["violations"][0]["kind"] == "version"
    assert not P.validate({**v1, "format": "something-else"})["ok"]
    assert not P.validate({**v1, "version": 1})["ok"]


def test_single_keyframe_is_a_schema_error_not_a_crash():
    r = P.validate({"name": "x", "start": "ready", "keyframes": [{"t": 0, "q": [0, .5, .8, 0, 0, 0]}]})
    assert not r["ok"] and r["violations"][0]["kind"] == "schema"
