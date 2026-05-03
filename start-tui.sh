#!/usr/bin/env bash
# Start NiCo TUI.
# Prefer a direct local Python start for speed; fall back to nix-shell only when
# the required runtime modules are not available.

set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

cd "$SCRIPT_DIR" || exit 1

if command -v python3 >/dev/null 2>&1; then
  if python3 - <<'PY' >/dev/null 2>&1
import importlib.util
import sys

required = ("textual", "nico")
missing = [name for name in required if importlib.util.find_spec(name) is None]
sys.exit(1 if missing else 0)
PY
  then
    exec python3 -m nico.tui
  fi
fi

if [ -f "$SCRIPT_DIR/shell.nix" ]; then
  exec nix-shell "$SCRIPT_DIR/shell.nix" \
    --run "cd '$SCRIPT_DIR' && python3 -m nico.tui"
fi

exec nix-shell \
  -p python312 python312Packages.flask python312Packages.textual \
  --run "cd '$SCRIPT_DIR' && python3 -m nico.tui"
