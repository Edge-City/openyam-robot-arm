#!/bin/bash
# Starts the OpenYAM virtual arm. SIMULATION ONLY: never drop --simulation.
set -euo pipefail
cd "$(dirname "$0")/.."
if [[ $# -ne 0 ]]; then
  echo "This launcher accepts no arguments and runs only the OpenYAM simulation."
  exit 2
fi
source ./scripts/environment.sh
echo "OpenYAM SIMULATION ONLY. Open http://127.0.0.1:8095. Stop with Ctrl-C."
exec "$VIRTUAL_ENV/bin/dimos" --simulation run keyboard-teleop-openyam
