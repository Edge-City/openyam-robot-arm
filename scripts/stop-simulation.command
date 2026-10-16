#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")/.."
source ./scripts/environment.sh
exec "$VIRTUAL_ENV/bin/python" ./scripts/stop-simulation.py
