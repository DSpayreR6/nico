#!/usr/bin/env bash
# Start NiCo – NixOS Configurator
# Tries a pip-installed `nico` command first, falls back to nix-shell.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if command -v nico &>/dev/null; then
  nico
else
  exec nix-shell \
    -p python312 python312Packages.flask \
    --run "cd '$SCRIPT_DIR' && python3 -m nico.main"
fi
