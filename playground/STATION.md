# Playing a playground motion on the lab arm

**Status:** the contract is defined; the console "station" page is **not built yet** (next step).

A playground QR is one of:
- `https://<host>/p/<id>`: fetch `GET https://<host>/api/p/<id>` → `{playable, validation, prompt, ...}` (CORS open, read-only)
- `https://<host>/p/local#<base64url(deflate(playable JSON))>`: the whole motion is in the link; works offline

Manual procedure until the station exists:

```sh
# 1. fetch and save the playable
curl -s https://<host>/api/p/<id> | .venv/bin/python -c "import json,sys; p=json.load(sys.stdin)['playable']; p['name']='pg-<id>'; json.dump(p, open('playables/pg-<id>.json','w'), indent=1)"
# 2. re-validate with the lab validator (the authority)
.venv/bin/python -c "import armctl.playable as P; print(P.report(P.validate(P.load('pg-<id>'))))"
# 3. preview: console → Playables → pg-<id> → Preview (and "Try on sim")
# 4. with the human's go, on the real arm
experiments/2026-10-16-linux-real-arm/session_play.sh pg-<id>
```

Planned station (console page): scan with a phone camera or paste a link → fetch or decode → validate + FK check → preview → queue → operator presses Play. The page never plays without a human click, and it rejects anything the Python validator refuses.
