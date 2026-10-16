# Dance to any song

Local audio file → beat / tempo / bar / section analysis → a plan (motif per stretch of beats; automatic or written by a human or Claude) → a validated, beat-synced playable → 3D preview → the real arm, with the music in sync.

| Folder | What | In git? |
|---|---|---|
| `music/inbox/` | drop songs here (mp3, wav, flac, ogg, m4a, …) | no (copyright) |
| `music/analysis/<song>.json` | beats, downbeats, sections, energy, key moments, suggested excerpt | yes |
| `music/plans/<name>.json` | the plan a playable was built from (edit and re-run) | yes |
| `music/clips/<name>.wav` | the excerpt with 1.0 s of silence in front, played with the arm | no (copyright) |
| `playables/<name>.json` | the dance itself (`"caps": "dance"`, a `music` block says which song and window) | yes |

## Quick start (repo root)

```bash
cp ~/Downloads/song.mp3 music/inbox/
.venv/bin/python -m armctl.music analyze music/inbox/song.mp3            # readable summary + music/analysis/song.json
.venv/bin/python -m armctl.choreo music/inbox/song.mp3 --style groove    # dry run: plan, validation, FK
.venv/bin/python -m armctl.choreo music/inbox/song.mp3 --style groove --save song-groove
```

`--save` prints the play command. Rehearse in sim first, keep people an arm's length back, and only run it on the real arm when the person at the arm says go:

```bash
experiments/2026-10-16-linux-real-arm/session_play.sh song-groove --caps dance --audio music/clips/song-groove.wav
```

The web page (3D preview with the music, waveform, sections, motif timeline): `.venv/bin/python dashboard/music_server.py` → http://127.0.0.1:8093/ (or `/music` on the console once its routes are registered).

## Styles

- `chill`: slow 2–4 beat moves (breathe, sway, wrist wave; big sweeps when it gets loud).
- `groove` (default): nods and sways when quiet, pumps / up-left-up-right / sweeps when loud, a hit at each big energy jump.
- `hype`: 2-beat hits and pumps with accents landing on downbeats; bounces in quiet parts.

## The plan (the intelligence layer)

Read the analysis (sections, energy per beat, key moments), then write the plan. Beats count from the window start: beat 0 is the first downbeat of the excerpt, where the arm is at ready, and the arm is back at ready on the last beat.

```bash
.venv/bin/python -m armctl.choreo motifs                                              # the motif library
.venv/bin/python -m armctl.choreo music/inbox/song.mp3 --emit-plan /tmp/plan.json     # start from the automatic plan
# edit /tmp/plan.json
.venv/bin/python -m armctl.choreo music/inbox/song.mp3 --plan /tmp/plan.json --save song-mine
```

```json
{"style": "groove",
 "window": {"start_beat": 32, "end_beat": 112},
 "segments": [
  {"start_beat": 0,  "end_beat": 8,  "motif": "nod",     "amplitude": 0.6, "note": "count-in"},
  {"start_beat": 8,  "end_beat": 24, "motif": "sway",    "amplitude": 0.8, "note": "verse groove"},
  {"start_beat": 24, "end_beat": 32, "motif": "hit",     "amplitude": 1.0, "note": "the drop lands on 25"},
  {"start_beat": 32, "end_beat": 80, "motif": "up-side", "amplitude": 1.0, "note": "chorus"}]}
```

`window` uses the analysis' absolute beat indices (`excerpt` has the suggested one; at most 55 s). Gaps become `hold`. `amplitude` (0–1.5) scales the motif's nominal size, then each move is capped to what the dance caps allow at the song's real beat times (a cubic move of d rad in T s peaks at 1.5·d/T rad/s and 6·d/T² rad/s²), so fast songs get smaller moves, never faster ones. The output table says which segments were capped.

## Checks every dance passes before it is saved

1. `armctl.playable.validate` with the `dance` caps (0.6 rad/s, 2.0 rad/s², soft limits, ≤ 60 s), including the `armctl.envelope` cross-check.
2. Forward kinematics on every 50 Hz sample (`armctl/fk.py`, pinocchio on the yam URDF): gripper tip ≥ 0.08 m above the base plane, every link ≥ 0.03 m. Self-collision and cables are not modelled.

## Sync

`play_real.py` starts `aplay` when the trajectory is accepted and spends 1.0 s blending to the first keyframe; the clip starts with 1.0 s of silence, so the excerpt's first downbeat meets keyframe 0. The arm's tracking lag is not compensated yet (see the LOG idea about measuring it on video).
