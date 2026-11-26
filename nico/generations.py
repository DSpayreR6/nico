"""
System states (generations) of this machine – the time machine's System side.

The time machine has two sides (decision 2026-11-21, vorgaben.txt): Config
– checkpoints (commits) and Restore, git's business – and System – the
generations Nix keeps of the built system, and Rollback. Every rebuild adds a
generation; the boot menu lists them. Martin's wish: pick an older one and
make it the one the machine runs, now or from the next start. No separate
rollback (Martin, 2026-11-21): the previous state is one row in the list.

Reading them is reading outside the config directory, so it needs the
release hostread.GENERATIONS. What is read:

  /nix/var/nix/profiles/system-<N>-link   one per generation: its number,
                                          its date (the link's mtime), the
                                          NixOS version and the kernel inside
  /nix/var/nix/profiles/system            the one the next start uses
  /run/current-system                     the one running now
  /run/booted-system                      the one the machine started with

The three marks can differ: after a `switch` the running one is newer than
the booted one; after `boot` the next start differs from the running one.

The list is read from the links themselves, not from `nixos-rebuild
list-generations`: that subcommand is new in nixos-rebuild-ng, and the links
are what it reads too.

Switching is two root commands, the same for "now" and "from the next
start":

  nix-env -p /nix/var/nix/profiles/system --switch-generation N
  /nix/var/nix/profiles/system/bin/switch-to-configuration switch|boot

The first makes N the profile's current generation (the boot default), the
second activates it now (switch) or only installs the boot entry (boot).
NiCo's config files are not touched: the next rebuild builds from them
again.
"""
from __future__ import annotations

import os
import re
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

PROFILES = Path("/nix/var/nix/profiles")
PROFILE  = PROFILES / "system"
CURRENT  = Path("/run/current-system")
BOOTED   = Path("/run/booted-system")

_LINK = re.compile(r"^system-(\d+)-link$")
_KERNEL = re.compile(r"linux-(\d+\.\d+(?:\.\d+)?[\w.-]*?)(?:/|$)")

MODES = ("switch", "boot")


@dataclass
class Generation:
    number: int
    date: str            # "YYYY-MM-DD HH:MM", local time
    nixos_version: str   # e.g. "26.11.20261117.0490633", "" if unreadable
    kernel: str          # e.g. "6.18.63", "" if unreadable
    running: bool        # /run/current-system
    booted: bool         # /run/booted-system
    default: bool        # /nix/var/nix/profiles/system (next start)


def _real(path: Path) -> str:
    try:
        return os.path.realpath(path)
    except OSError:
        return ""


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def _kernel_of(link: Path) -> str:
    try:
        target = os.readlink(link / "kernel")
    except OSError:
        target = _real(link / "kernel")
    m = _KERNEL.search(target or "")
    return m.group(1) if m else ""


def list_generations(profiles: Path = PROFILES, current: Path = CURRENT,
                     booted: Path = BOOTED) -> list[Generation]:
    """All generations, newest first. The paths are parameters for the tests;
    the caller has checked the release (hostread.GENERATIONS)."""
    profile = profiles / "system"
    try:
        default_name = os.readlink(profile)
    except OSError:
        default_name = ""
    running_real = _real(current)
    booted_real = _real(booted)
    out: list[Generation] = []
    try:
        entries = list(profiles.iterdir())
    except OSError:
        return []
    for link in entries:
        m = _LINK.match(link.name)
        if not m:
            continue
        try:
            mtime = link.lstat().st_mtime
        except OSError:
            continue
        real = _real(link)
        out.append(Generation(
            number=int(m.group(1)),
            date=datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M"),
            nixos_version=_read(link / "nixos-version"),
            kernel=_kernel_of(link),
            running=bool(real) and real == running_real,
            booted=bool(real) and real == booted_real,
            default=Path(default_name).name == link.name,
        ))
    out.sort(key=lambda g: g.number, reverse=True)
    return out


def as_dicts(gens: list[Generation]) -> list[dict]:
    return [asdict(g) for g in gens]


def switch_commands(number: int, mode: str, profile: Path = PROFILE) -> list[list[str]]:
    """The two root commands for making `number` the system state: enter it
    in the profile, then activate it (boot menu / switch now)."""
    if mode not in MODES:
        raise ValueError("ERR_INVALID_ACTION")
    return [
        ["sudo", "-S", "nix-env", "-p", str(profile), "--switch-generation", str(int(number))],
        activate_command(mode, profile),
    ]


def activate_command(mode: str, profile: Path = PROFILE) -> list[str]:
    """The second step alone - for "activate again" after it failed while
    the profile already points to the chosen state (revision 2026-11-25, F8)."""
    if mode not in MODES:
        raise ValueError("ERR_INVALID_ACTION")
    return ["sudo", "-S", str(profile / "bin" / "switch-to-configuration"), mode]
