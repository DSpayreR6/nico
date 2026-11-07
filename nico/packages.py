"""
NixOS package search via `nix search nixpkgs <query> --json`.
Running the search server-side avoids CORS and credential issues.
On the first call nix may need to download nixpkgs metadata – subsequent
searches use the local cache and are fast.
"""

import json
import subprocess
import urllib.parse

# Errors travel as codes, data behind the unit separator (same as
# git_manager._err); the interface translates them. These used to be German
# sentences, shown to every user regardless of language.
_P_SEP = "\x1f"


def _err(code: str, *params) -> str:
    return _P_SEP.join([code, *(str(p) for p in params)])


def search_nixpkgs(query: str, channel: str = "unstable") -> list[dict]:
    try:
        result = subprocess.run(
            [
                "nix", "search", "nixpkgs", query, "--json",
                "--extra-experimental-features", "nix-command flakes",
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )
    except FileNotFoundError:
        raise RuntimeError(_err("ERR_PKG_NO_NIX"))
    except subprocess.TimeoutExpired:
        raise RuntimeError(_err("ERR_PKG_TIMEOUT", 60))

    stdout = result.stdout.strip()
    if not stdout:
        # nix search returns empty JSON {} when nothing matches, not an error
        if result.returncode == 0:
            return []
        raise RuntimeError(_err("ERR_PKG_SEARCH", result.stderr[:300].strip()))

    try:
        data = json.loads(stdout)
    except json.JSONDecodeError as e:
        raise RuntimeError(_err("ERR_PKG_BAD_OUTPUT", e))

    results = []
    for key, info in data.items():
        # key: "legacyPackages.x86_64-linux.firefox"
        #  or: "legacyPackages.x86_64-linux.kdePackages.kate"
        parts = key.split(".", 2)
        attr = parts[2] if len(parts) > 2 else key
        results.append({
            "attr":        attr,
            "pname":       info.get("pname", attr),
            "version":     info.get("version", ""),
            "description": info.get("description", ""),
            "url": (
                "https://search.nixos.org/packages"
                f"?channel={urllib.parse.quote(channel)}&show={urllib.parse.quote(attr)}"
                f"&query={urllib.parse.quote(query)}"
            ),
        })

    results.sort(key=lambda r: r["attr"])
    return results
