#!/usr/bin/env sh
set -eu

if [ -n "${PYTHON:-}" ]; then
  python_bin="$PYTHON"
else
  python_bin=""
  for candidate in python3.13 python3.12 python3.11 python3; do
    if command -v "$candidate" >/dev/null 2>&1; then
      if "$candidate" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)' >/dev/null 2>&1; then
        python_bin="$candidate"
        break
      fi
    fi
  done
fi

if [ -z "$python_bin" ]; then
  echo "Python >=3.11 is required; set PYTHON to a compatible interpreter." >&2
  exit 1
fi

if [ -x ./.venv/bin/python ] && ! ./.venv/bin/python -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)' >/dev/null 2>&1; then
  echo "Recreating .venv with Python >=3.11." >&2
  rm -rf .venv
fi

if [ ! -d .venv ]; then
  "$python_bin" -m venv .venv
fi

./.venv/bin/python -m pip install --upgrade pip
# Install the strategy factory package (which pins runtime deps in pyproject.toml).
./.venv/bin/python -m pip install -e ".[dev]"

# Smoke test: the console-script must run.
./.venv/bin/hivefi-factory --version >/dev/null
