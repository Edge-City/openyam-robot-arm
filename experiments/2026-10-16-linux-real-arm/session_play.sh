#!/usr/bin/env bash
# Full real-arm routine for one playable: coordinator up -> play -> home -> coordinator down -> torque off.
#   experiments/2026-10-16-linux-real-arm/session_play.sh nod [--speed 0.5]
# Arm must start folded at home, nothing else driving it. Ctrl-C at any point cancels and still turns torque off.
set -uo pipefail
cd "$(dirname "$0")/../.."
X=experiments/2026-10-16-linux-real-arm
export ZENOH_SCOUT_ADDR=224.0.0.123:7449
if pgrep -af "bin/dimos" | awk '$2 ~ /python/' | grep -q .; then echo "a DimOS run is already active; refusing"; exit 1; fi
.venv/bin/dimos --can-port can0 --viewer none run coordinator-openyam >> $X/logs/armctl-real-coordinator.log 2>&1 &
DIMOS=$!
cleanup() {
  kill -INT $DIMOS 2>/dev/null; sleep 4; kill -TERM $DIMOS 2>/dev/null; wait $DIMOS 2>/dev/null
  .venv/bin/python -I tools/disable_all.py can0 | tail -7
}
trap cleanup EXIT
for _ in $(seq 60); do grep -q "ControlCoordinator started" <(tail -n 50 $X/logs/armctl-real-coordinator.log) && break; sleep 0.5; done
sleep 1
.venv/bin/python -I $X/play_real.py "$@" && .venv/bin/python -I $X/armctl_real.py home
