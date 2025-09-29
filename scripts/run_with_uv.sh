#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
  ./scripts/run_with_uv.sh [entrypoint.py] [-- args...]

Description:
  - Ensures uv is installed
  - Ensures CPython 3.12.3 is available (configurable via $PYTHON_VERSION)
  - Creates/uses a local virtual environment at .venv pinned to that Python
  - Installs dependencies from requirements.txt using uv pip
  - Executes the entrypoint inside the environment

Examples:
  # Run default main (security_threat_detection.py at repo root)
  ./scripts/run_with_uv.sh

  # Run test harness
  ./scripts/run_with_uv.sh security_test_threat_detection.py

  # Pass arguments to your program (use -- to separate)
  ./scripts/run_with_uv.sh security_threat_detection.py -- --camera 0 --threshold 0.6

Environment variables:
  PYTHON_VERSION     Python version to provision (default: 3.12.3)
  VENV_DIR           Virtualenv directory (default: .venv at repo root)
  REQUIREMENTS_FILE  Requirements file path (default: requirements.txt at repo root)
EOF
}

# Configurables (can be overridden via env)
PYTHON_VERSION="${PYTHON_VERSION:-3.12.3}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
VENV_DIR="${VENV_DIR:-$PROJECT_ROOT/.venv}"
REQUIREMENTS_FILE="${REQUIREMENTS_FILE:-$PROJECT_ROOT/requirements.txt}"

# Entrypoint: default to security_threat_detection.py at repo root
ENTRYPOINT_DEFAULT="$PROJECT_ROOT/security_threat_detection.py"
ENTRYPOINT="${1:-$ENTRYPOINT_DEFAULT}"
if [[ "${ENTRYPOINT}" == "-h" || "${ENTRYPOINT}" == "--help" ]]; then
  usage
  exit 0
fi
if [[ $# -gt 0 ]]; then shift; fi

need_cmd() {
  command -v "$1" >/dev/null 2>&1
}

echo "[uv] Checking for uv..."
if ! need_cmd uv; then
  echo "[uv] Not found. Installing uv..."
  # Install uv (https://docs.astral.sh/uv/)
  curl -LsSf https://astral.sh/uv/install.sh | sh

  # Try to pick up uv in this session (common install locations)
  if [ -d "$HOME/.local/bin" ] && ! echo "$PATH" | grep -q "$HOME/.local/bin"; then
    export PATH="$HOME/.local/bin:$PATH"
  fi
  if [ -d "$HOME/.cargo/bin" ] && ! echo "$PATH" | grep -q "$HOME/.cargo/bin"; then
    export PATH="$HOME/.cargo/bin:$PATH"
  fi

  if ! need_cmd uv; then
    echo "[uv] Installation completed but uv not on PATH." >&2
    echo "     Add ~/.local/bin or ~/.cargo/bin to your PATH and re-run." >&2
    exit 1
  fi
fi

echo "[uv] Ensuring Python $PYTHON_VERSION is available..."
uv python install "$PYTHON_VERSION"

# Create or reuse venv; ensure it matches requested Python version
if [ -d "$VENV_DIR" ]; then
  PYVER=""
  if [ -x "$VENV_DIR/bin/python" ]; then
    PYVER="$("$VENV_DIR/bin/python" -c 'import sys; print(".".join(map(str, sys.version_info[:3])))')"
  fi
  if [ "$PYVER" != "$PYTHON_VERSION" ]; then
    echo "[uv] Existing venv uses Python $PYVER, but $PYTHON_VERSION requested. Recreating..."
    rm -rf "$VENV_DIR"
    uv venv --python "$PYTHON_VERSION" "$VENV_DIR"
  else
    echo "[uv] Using existing venv at $VENV_DIR (Python $PYVER)"
  fi
else
  echo "[uv] Creating venv at $VENV_DIR with Python $PYTHON_VERSION ..."
  uv venv --python "$PYTHON_VERSION" "$VENV_DIR"
fi

PYTHON_BIN="$VENV_DIR/bin/python"

# Install dependencies
if [ -f "$REQUIREMENTS_FILE" ]; then
  echo "[uv] Installing dependencies from $REQUIREMENTS_FILE ..."
  uv pip install --python "$PYTHON_BIN" -r "$REQUIREMENTS_FILE"
else
  echo "[uv] No requirements.txt found at $REQUIREMENTS_FILE; skipping dependency install"
fi

# Resolve entrypoint: if not absolute and not found, try relative to repo root
if [ ! -f "$ENTRYPOINT" ]; then
  if [ -f "$PROJECT_ROOT/$ENTRYPOINT" ]; then
    ENTRYPOINT="$PROJECT_ROOT/$ENTRYPOINT"
  fi
fi

if [ ! -f "$ENTRYPOINT" ]; then
  echo "[run] Entrypoint not found: $ENTRYPOINT" >&2
  echo "       Tip: pass a script path relative to repo root, e.g.:" >&2
  echo "       ./scripts/run_with_uv.sh security_test_threat_detection.py" >&2
  exit 1
fi

echo "[run] Executing: $ENTRYPOINT (Python $PYTHON_VERSION) in $VENV_DIR"
exec "$PYTHON_BIN" "$ENTRYPOINT" "$@"