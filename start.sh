#!/usr/bin/env bash
# Start NiCo from THIS source tree – the development launcher.
#
# It deliberately never calls an installed `nico` command: if NiCo is also
# installed (nix profile install / systemPackages), that command points into
# the read-only Nix store and runs a frozen build. Code changes here would
# then have no effect and no restart would ever pick them up – which cost a
# full afternoon once. Run the installed `nico` directly if that is what you
# want; the About dialog shows which of the two is running.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [ -f "$SCRIPT_DIR/shell.nix" ]; then
  exec nix-shell "$SCRIPT_DIR/shell.nix" \
    --run "cd '$SCRIPT_DIR' && python3 -m nico.main"
else
  exec nix-shell \
    -p python3 python3Packages.flask \
    --run "cd '$SCRIPT_DIR' && python3 -m nico.main"
fi
