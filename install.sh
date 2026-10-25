#!/usr/bin/env sh
# NiCo installer – a menu around the handful of nix commands from the README.
#
# Run it without downloading anything first:
#   sh <(curl -sL https://raw.githubusercontent.com/DSpayreR6/nico/main/install.sh)
#
# It deliberately never hides what it does: every command is printed in full
# before it runs, nothing is filtered, and the output stays on screen. What you
# see is what you can type yourself the next time, without this script.
#
# POSIX sh on purpose – it has to work on a NixOS installer shell too.

set -u

REPO_MAIN="github:DSpayreR6/nico"
REPO_DEV="github:DSpayreR6/nico/dev"

# ── Colours ──────────────────────────────────────────────────────────────────
# Dropped when the output is not a terminal or NO_COLOR is set, otherwise a
# redirected log would be full of escape sequences.

if [ -t 1 ] && [ -z "${NO_COLOR-}" ]; then
    # 38;5;110 is the closest 256-colour match for the Nix blue #7EBAE4
    C_LOGO=$(printf '\033[38;5;110m')
    C_STABLE=$(printf '\033[32m')
    C_UPDATE=$(printf '\033[33m')
    C_DEV=$(printf '\033[31m')
    C_PLAIN=$(printf '\033[37m')
    C_CMD=$(printf '\033[1m')
    C_DIM=$(printf '\033[2m')
    C_OFF=$(printf '\033[0m')
else
    C_LOGO='' C_STABLE='' C_UPDATE='' C_DEV='' C_PLAIN='' C_CMD='' C_DIM='' C_OFF=''
fi

# ── Banner ───────────────────────────────────────────────────────────────────
# Printed once at startup, not on every menu redraw: banner plus menu is taller
# than a 24-line console, and the menu is the part that has to stay in view.

banner() {
    printf '%s' "$C_LOGO"
    cat <<'LOGO'
                 @@@@@@
               @@@@@@@@@@     @*
      #@@@    @@@@@@@@@@@@@@@@@  +
      @@@@@@    @@@@@@@@@@@@@  @@@-
       @@@@@@@#   @@@@@@@    @@@@
      @@@@@@@@@@@   @@@@@  @@@@@%%
      @@@@@@@@@@@@@   @@@  @@%%%%%
  @@@@@@@@@@@ @@@@@@@   %  @%%%%%%%%%%
  @@@@@@@@@@@   @@@@@@@    @%%%%%%%%%%
  @@@@@@@@%@%     %%%%%%%  %%%%%%%%%%%
      %%@%%%%   %   %%%%%%%%%%%%%%
      @%%%%%%   %%%   %%%%%%%%%%%%
       %%%%%%   %%%%%   %%%%%%%%%
      %%%%%     %%%%%%%   %%%%%%%%#
      *%%   %%%%%%%%%%%%%   %%%%%%
          %%  %%%%%%%%%%%%    %%
                 %%%%%%-
                 %%%%%%

 ███╗   ██╗██╗ ██████╗  ██████╗
 ████╗  ██║██║██╔════╝ ██╔═══██╗
 ██╔██╗ ██║██║██║      ██║   ██║
 ██║╚██╗██║██║██║      ██║   ██║
 ██║ ╚████║██║╚██████╗ ╚██████╔╝
 ╚═╝  ╚═══╝╚═╝ ╚═════╝  ╚═════╝
LOGO
    printf '%s\n' "$C_OFF"
    printf '       NixOS Configurator\n\n'
    printf '%sNiCo rewrites the files in your config directory. It makes a ZIP\n' "$C_DIM"
    printf 'backup of them when you import an existing configuration.%s\n\n' "$C_OFF"
}

# ── Nix availability and the flake gate ──────────────────────────────────────
# Flakes are still an experimental feature and are off on a freshly installed
# NixOS, where `nix run github:…` aborts with "experimental Nix feature
# 'nix-command' is disabled". The flags are harmless when flakes are already
# enabled, so a wrong guess here costs nothing but a longer command line.

NIX="nix"

check_nix() {
    if ! command -v nix >/dev/null 2>&1; then
        printf 'No `nix` command found. This script only makes sense on a\n'
        printf 'machine with Nix installed – see https://nixos.org/download\n'
        exit 1
    fi
    if ! nix flake --version >/dev/null 2>&1; then
        NIX="nix --extra-experimental-features 'nix-command flakes'"
        printf '%sFlakes are disabled on this machine, so every command below\n' "$C_DIM"
        printf 'carries --extra-experimental-features.%s\n\n' "$C_OFF"
    fi
}

# ── Running a command ────────────────────────────────────────────────────────
# Printed first, then executed through eval so that the line on screen is
# literally the line that runs – no second, hidden variant.

run_cmd() {
    printf '\n%s$ %s%s\n\n' "$C_CMD" "$1" "$C_OFF"
    eval "$1"
    status=$?
    printf '\n%s[exit %s]%s\n' "$C_DIM" "$status" "$C_OFF"
    printf '%sPress Enter for the menu.%s' "$C_DIM" "$C_OFF"
    read -r _ignored
    printf '\n'
}

# ── Menu ─────────────────────────────────────────────────────────────────────
# Green is the released state, red the development branch. The colour is never
# the only signal – every line says so in words as well, because red/green is
# the most common form of colour blindness.

menu() {
    printf '%s  1  Test              runs it, installs nothing   [main]%s\n' "$C_STABLE" "$C_OFF"
    printf '%s  2  Install           released version            [main]%s\n'  "$C_STABLE" "$C_OFF"
    printf '%s  3  Update            changes an installed NiCo%s\n'           "$C_UPDATE" "$C_OFF"
    printf '%s  4  Test (dev)        development branch          [dev]%s\n'   "$C_DEV"    "$C_OFF"
    printf '%s  5  Install (dev)     development branch          [dev]%s\n'   "$C_DEV"    "$C_OFF"
    printf '%s  0  Quit%s\n\n'                                                "$C_PLAIN"  "$C_OFF"
}

# ── Main loop ────────────────────────────────────────────────────────────────

banner
check_nix

while :; do
    menu
    printf 'Choice: '
    if ! read -r choice; then
        printf '\n'
        exit 0
    fi
    case "$choice" in
        1) run_cmd "$NIX run $REPO_MAIN" ;;
        2) run_cmd "$NIX profile install $REPO_MAIN" ;;
        3) run_cmd "$NIX profile upgrade nico" ;;
        # --refresh on every dev command: without it Nix serves a cached copy
        # of the branch, which is how you end up testing yesterday's fix
        4) run_cmd "$NIX run $REPO_DEV --refresh" ;;
        5) run_cmd "$NIX profile install $REPO_DEV --refresh" ;;
        0|q|Q) exit 0 ;;
        *) printf '%sPick 1-5 or 0.%s\n\n' "$C_DIM" "$C_OFF" ;;
    esac
done
