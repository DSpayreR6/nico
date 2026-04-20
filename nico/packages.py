"""
NixOS package search via `nix search nixpkgs <query> --json`.
Running the search server-side avoids CORS and credential issues.
On the first call nix may need to download nixpkgs metadata – subsequent
searches use the local cache and are fast.
"""

import json
import subprocess
import urllib.parse


def search_nixpkgs(query: str) -> list[dict]:
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
        raise RuntimeError("'nix' nicht gefunden. Ist NixOS korrekt installiert?")
    except subprocess.TimeoutExpired:
        raise RuntimeError(
            "Suche hat zu lange gedauert (60s). Beim ersten Aufruf muss nixpkgs "
            "ggf. heruntergeladen werden – danach ist der Cache warm. Nochmal versuchen."
        )

    stdout = result.stdout.strip()
    if not stdout:
        # nix search returns empty JSON {} when nothing matches, not an error
        if result.returncode == 0:
            return []
        raise RuntimeError(f"nix search fehlgeschlagen: {result.stderr[:300]}")

    try:
        data = json.loads(stdout)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"Unerwartete Ausgabe von nix search: {e}")

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
                f"?channel=24.11&show={urllib.parse.quote(attr)}"
                f"&query={urllib.parse.quote(query)}"
            ),
        })

    results.sort(key=lambda r: r["attr"])
    return results
