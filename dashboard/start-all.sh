#!/usr/bin/env bash
# Start the lab web apps (if not already running) in tmux session "claude", then print the URLs.
#   dashboard/start-all.sh            start what's missing
#   dashboard/start-all.sh --check    only report what's running
#
# Starts: the lab console (:8090; also serves /playables, /phones, /sim), phonecam (:8443 phones,
# :8444 local) and the armctl daemon with the MOCK backend (:8091, simulated arm, no hardware).
# It never starts DimOS or anything that drives the real arm. Idempotent: a server whose port is
# already listening (or whose process is already running) is left alone.
set -uo pipefail
cd "$(dirname "$0")/.."
REPO=$(pwd)
X=experiments/2026-10-16-linux-real-arm
SESSION=claude
PY=.venv/bin/python
CHECK_ONLY=0
[[ "${1:-}" == "--check" ]] && CHECK_ONLY=1

listening() { ss -ltnH "sport = :$1" 2>/dev/null | grep -q .; }
running() { pgrep -f -- "$1" >/dev/null 2>&1; }

# start NAME PORT PROCESS_PATTERN COMMAND
start() {
  local name=$1 port=$2 pat=$3 cmd=$4
  if listening "$port" || running "$pat"; then
    printf '  %-9s already running (port %s)\n' "$name" "$port"
    return 0
  fi
  if (( CHECK_ONLY )); then
    printf '  %-9s NOT running (port %s)\n' "$name" "$port"
    return 0
  fi
  tmux has-session -t "$SESSION" 2>/dev/null || tmux new-session -d -s "$SESSION" -n notes -c "$REPO"
  # Reuse a same-named window only if it sits idle at a shell prompt; otherwise open a new one.
  local target="" cur
  if tmux list-windows -t "$SESSION" -F '#{window_name}' | grep -qx "$name"; then
    cur=$(tmux display-message -p -t "$SESSION:$name" '#{pane_current_command}' 2>/dev/null)
    case "$cur" in bash|zsh|sh|fish) target="$SESSION:$name" ;; esac
  fi
  local full="cd $REPO && $cmd; echo '$name exited'; exec bash"
  if [[ -n "$target" ]]; then
    tmux respawn-window -k -t "$target" -c "$REPO" "bash -c \"$full\""
  else
    tmux new-window -d -t "$SESSION" -n "$name" -c "$REPO" "bash -c \"$full\""
  fi
  for _ in $(seq 40); do listening "$port" && break; sleep 0.25; done
  if listening "$port"; then
    printf '  %-9s started in tmux %s:%s (port %s)\n' "$name" "$SESSION" "$name" "$port"
  else
    printf '  %-9s started in tmux %s:%s, but port %s is not up yet: check that window\n' "$name" "$SESSION" "$name" "$port"
  fi
}

echo "OpenYAM lab apps:"
# armctl first: the console and phonecam both talk to it.
start armctl  8091 "armctl\.daemon --backend mock$" \
  "$PY -m armctl.daemon --backend mock"
start phonecam 8443 "dashboard/phonecam\.py$" \
  "$PY dashboard/phonecam.py 2>&1 | tee -a $X/logs/phonecam.log"
start console 8090 "dashboard/server\.py$" \
  "$PY dashboard/server.py 2>&1 | tee -a $X/logs/dashboard-server.log"

cat <<EOF

  Console      http://127.0.0.1:8090/          (real arm over CAN, programs, playables)
  Playables    http://127.0.0.1:8090/playables (3D preview; nothing moves)
  Phones       http://127.0.0.1:8090/phones    (QR codes for camera, tilt, joystick)
  Sim          http://127.0.0.1:8090/sim       (armctl mock arm, fullscreen)
  armctl       ws://127.0.0.1:8091/armctl      (mock backend: simulated arm, no hardware)
  phonecam     http://127.0.0.1:8444/cameras   (local API; phones use https://<laptop-ip>:8443)

  The phone URL (it contains the access token) is in dashboard/certs/phone-url.txt;
  the Phones page shows it as QR codes. Don't paste it into logs or chat.
  Real arm: nothing here starts DimOS. Playables run on the real arm only from a terminal
  (experiments/2026-10-16-linux-real-arm/session_play.sh <name>) with the human's go-ahead.
EOF
