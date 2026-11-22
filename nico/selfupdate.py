"""
Updating an installed NiCo from its help menu (Martin, 2026-11-22).

Only a NiCo installed with `nix profile install github:DSpayreR6/nico[/dev]`
can be updated in place: Nix remembers the url it came from, so
`nix profile upgrade <name> --refresh` follows the same branch by itself –
the same command as Update in install.sh. A NiCo started with `nix run` is
fetched fresh on every start, one started from a git checkout (start.sh) is
updated by git; for both the menu entry stays hidden.

The running NiCo is not touched: its files live in the Nix store and stay
there, the new version is used after a restart. Config directory, config.json
and the app settings are not part of the profile. A failed upgrade leaves the
old version installed (Nix switches the profile atomically), and
`nix profile rollback` brings back the previous one.

Network access happens only on the user's click (vorgaben.txt: no silent
remote actions).
"""
from __future__ import annotations

import json
import subprocess

REPO_MARK = "DSpayreR6/nico"

# Harmless where flakes are already enabled, needed where they are not – the
# same reasoning as install.sh.
_NIX = ["nix", "--extra-experimental-features", "nix-command flakes"]


def parse_profile_list(text: str) -> tuple[str, str] | None:
    """(name, original url) of NiCo in `nix profile list --json` output, or
    None. Matched on the url rather than the name: the name is whatever Nix
    derived, the url is ours (install.sh does the same).

    Two formats: since Nix 2.20 `elements` is an object keyed by name; before
    it was a list, and upgrade took the index instead of a name."""
    try:
        data = json.loads(text)
    except (ValueError, TypeError):
        return None
    elements = data.get("elements") if isinstance(data, dict) else None
    if isinstance(elements, dict):
        items = list(elements.items())
    elif isinstance(elements, list):
        items = [(str(i), e) for i, e in enumerate(elements)]
    else:
        return None
    for name, entry in items:
        if not isinstance(entry, dict):
            continue
        url = str(entry.get("originalUrl") or "")
        # "github:DSpayreR6/nico" or ".../nico/dev", not "nico-something"
        if url.startswith("github:") and (url[len("github:"):] == REPO_MARK
                                          or url[len("github:"):].startswith(REPO_MARK + "/")):
            return name, url
    return None


def profile_entry() -> tuple[str, str] | None:
    """The installed NiCo of this user's profile, or None."""
    try:
        res = subprocess.run(_NIX + ["profile", "list", "--json"],
                             capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if res.returncode != 0:
        return None
    return parse_profile_list(res.stdout)


def upgrade_command(name: str) -> list[str]:
    """--refresh: without it Nix may reuse a cached copy of the branch and
    report success without fetching anything new."""
    return _NIX + ["profile", "upgrade", name, "--refresh"]
