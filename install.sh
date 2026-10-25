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
    # The one thing nobody may miss: bold, reversed, red. Everything else on
    # this screen is a choice you can take back – this is not.
    C_WARN=$(printf '\033[1;7;31m')
    C_OFF=$(printf '\033[0m')
else
    C_LOGO='' C_STABLE='' C_UPDATE='' C_DEV='' C_PLAIN='' C_CMD='' C_DIM=''
    C_WARN='' C_OFF=''
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
    # Indents measured, not guessed: the logo spans columns 2..38, so its
    # centre is 20 – wordmark and subtitle are centred on that same column.
    printf '           NixOS Configurator\n\n'
    printf '%sNiCo%s %s rewrites the files %s%s in your config directory.\n' \
        "$C_DIM" "$C_OFF" "$C_WARN" "$C_OFF" "$C_DIM"
    printf 'It makes a ZIP backup of them when you import an existing\n'
    printf 'configuration.%s\n\n' "$C_OFF"
}

# ── Nix availability and the flake gate ──────────────────────────────────────
# Flakes are still an experimental feature and are off on a freshly installed
# NixOS, where `nix run github:…` aborts with "experimental Nix feature
# 'nix-command' is disabled". The flags are harmless when flakes are already
# enabled, so a wrong guess here costs nothing but a longer command line.

NIX="nix"

# `nix profile install` was renamed to `nix profile add` and only warns since
# Nix 2.34 – but older Nix does not know `add` at all, so neither name is right
# everywhere. Ask this Nix which one it has instead of guessing.
PROFILE_ADD="profile install"

check_nix() {
    if ! command -v nix >/dev/null 2>&1; then
        printf 'No `nix` command found. This script only makes sense on a\n'
        printf 'machine with Nix installed – see https://nixos.org/download\n'
        exit 1
    fi
    # The probe has to be a command that actually passes the gate. `nix flake
    # --version` does not: --version and --help are handled before the gate
    # and return 0 even where every real command then fails – measured in a
    # VM on 2026-10-25, where `nix run` aborted right after a clean probe.
    # `nix flake metadata` on a path that does not exist hits the gate first,
    # touches no network and changes nothing. Grepping the message rather than
    # the exit code also catches the case where only `flakes` is disabled and
    # `nix-command` is enabled.
    if nix flake metadata /nonexistent-nico-flake-probe 2>&1 \
       | grep -q 'experimental Nix feature'; then
        NIX="nix --extra-experimental-features 'nix-command flakes'"
        printf '%sFlakes are disabled on this machine, so every command below\n' "$C_DIM"
        printf 'carries --extra-experimental-features.%s\n\n' "$C_OFF"
    fi
    if eval "$NIX profile add --help" >/dev/null 2>&1; then
        PROFILE_ADD="profile add"
    fi
}

# ── The installed NiCo ───────────────────────────────────────────────────────
# Looked up again before every menu, not once at startup: after installing,
# the entry has to stop saying "not installed". The profile path is the
# fallback because a shell started before the install may not find the new
# command by name yet.

nico_bin() {
    if command -v nico >/dev/null 2>&1; then
        printf 'nico'
    elif [ -x "$HOME/.nix-profile/bin/nico" ]; then
        printf '%s/.nix-profile/bin/nico' "$HOME"
    fi
}

# ── Running a command ────────────────────────────────────────────────────────
# Shown, confirmed, then executed through eval so that the line on screen is
# literally the line that runs – no second, hidden variant.

LAST_STATUS=0

# Enter means yes, so the extra step costs one keypress
confirm() {
    printf '%s%s [Y/n] %s' "$C_CMD" "$1" "$C_OFF"
    read -r answer || return 1
    case "$answer" in
        ''|y|Y|yes|YES|j|J) return 0 ;;
        *) return 1 ;;
    esac
}

exec_cmd() {
    printf '\n'
    eval "$1"
    LAST_STATUS=$?
    printf '\n%s[exit %s]%s\n' "$C_DIM" "$LAST_STATUS" "$C_OFF"
}

pause() {
    printf '%sPress Enter for the menu.%s' "$C_DIM" "$C_OFF"
    read -r _ignored
    printf '\n'
}

# Printing the command alone was not enough: nix starts writing immediately
# and pushes the line out of sight before anyone has read it. The question
# holds it there. Answering no is useful in its own right – it shows the
# command without running it, so the script doubles as a place to look one up.

ask_cmd() {
    printf '\n%s$ %s%s\n\n' "$C_CMD" "$1" "$C_OFF"
    if ! confirm 'Run this command?'; then
        printf '%sNot run.%s\n\n' "$C_DIM" "$C_OFF"
        return 1
    fi
    exec_cmd "$1"
    return 0
}

run_cmd() {
    ask_cmd "$1" && pause
}

# Installing is the one action where nothing visible happens: no window, no
# program running, just a prompt again. Offering the start right here is what
# everyone expects to happen anyway. No second "Run this command?" after it –
# that is the same decision asked twice.

install_cmd() {
    ask_cmd "$1" || return
    if [ "$LAST_STATUS" -ne 0 ]; then
        pause
        return
    fi
    printf '\n'
    if confirm 'Start NiCo now?'; then
        bin=$(nico_bin)
        if [ -n "$bin" ]; then
            printf '\n%s$ %s%s\n' "$C_CMD" "$bin" "$C_OFF"
            exec_cmd "$bin"
        else
            printf '%sInstalled, but no `nico` command found yet. Open a new\n' "$C_DIM"
            printf 'terminal and type: nico%s\n' "$C_OFF"
        fi
    fi
    pause
}

# ── Menu ─────────────────────────────────────────────────────────────────────
# Green is the released state, red the development branch. The colour is never
# the only signal – every line says so in words as well, because red/green is
# the most common form of colour blindness.

menu() {
    # Plain, not green: the script cannot tell whether the installed NiCo came
    # from main or from dev, and it should not claim what it has not checked.
    # NiCo's own info dialog answers that, with version and commit.
    if [ -n "$(nico_bin)" ]; then
        printf '%s  1  Start             the installed NiCo%s\n' "$C_PLAIN" "$C_OFF"
    else
        # Greyed out, never hidden: hiding it would renumber everything below
        # depending on the machine's state
        printf '%s  1  Start             the installed NiCo (not installed)%s\n' "$C_DIM" "$C_OFF"
    fi
    printf '%s  2  Test              start now, install nothing          [main]%s\n' "$C_STABLE" "$C_OFF"
    printf '%s  3  Install           install permanently, do not start   [main]%s\n' "$C_STABLE" "$C_OFF"
    printf '%s  4  Update            update the installed NiCo%s\n'                  "$C_UPDATE" "$C_OFF"
    printf '%s  5  Test (dev)        start now, install nothing          [dev]%s\n'  "$C_DEV"    "$C_OFF"
    printf '%s  6  Install (dev)     install permanently, do not start   [dev]%s\n'  "$C_DEV"    "$C_OFF"
    printf '%s  0  Quit%s\n\n'                                                       "$C_PLAIN"  "$C_OFF"
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
        1)
            bin=$(nico_bin)
            if [ -n "$bin" ]; then
                run_cmd "$bin"
            else
                printf '%sNiCo is not installed yet – pick 3, or 2 to try it\n' "$C_DIM"
                printf 'without installing anything.%s\n\n' "$C_OFF"
            fi
            ;;
        2) run_cmd     "$NIX run $REPO_MAIN" ;;
        3) install_cmd "$NIX $PROFILE_ADD $REPO_MAIN" ;;
        4) run_cmd     "$NIX profile upgrade nico" ;;
        # --refresh on every dev command: without it Nix serves a cached copy
        # of the branch, which is how you end up testing yesterday's fix
        5) run_cmd     "$NIX run $REPO_DEV --refresh" ;;
        6) install_cmd "$NIX $PROFILE_ADD $REPO_DEV --refresh" ;;
        0|q|Q) exit 0 ;;
        *) printf '%sPick 1-6 or 0.%s\n\n' "$C_DIM" "$C_OFF" ;;
    esac
done
