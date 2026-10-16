# Sourced by the launch and stop scripts. Points DimOS at this folder's virtual
# environment and keeps its config, cache and run state inside the repo.
OPENYAM_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export VIRTUAL_ENV="${OPENYAM_VENV:-$OPENYAM_ROOT/.venv}"
if [[ ! -x "$VIRTUAL_ENV/bin/dimos" ]]; then
  echo "DimOS not found at $VIRTUAL_ENV/bin/dimos."
  echo "Install it first (see README), or set OPENYAM_VENV to your DimOS virtual environment."
  exit 1
fi
export PATH="$VIRTUAL_ENV/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
export XDG_CONFIG_HOME="$OPENYAM_ROOT/config"
export XDG_STATE_HOME="$OPENYAM_ROOT/state"
export XDG_CACHE_HOME="$OPENYAM_ROOT/cache"
export DIMOS_SIMULATION=mujoco
export PYGAME_HIDE_SUPPORT_PROMPT=1
unset PYTHONPATH PYTHONHOME
