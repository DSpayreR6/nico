"""
Look ahead before a rebuild: what will be downloaded, what will be compiled.

`nixos-rebuild dry-build` evaluates the config and lists what the build needs
without building anything. Two things are read from it:

- the download size, for a progress bar with a stable total;
- the derivations that have to be built locally. Most of those are NixOS glue
  (unit files, /etc, the system path) and cost nothing. The expensive ones are
  real packages compiled from source because the binary cache has no copy –
  typically anything with an unfree part, which Hydra never builds.
  bambu-studio is the case that prompted this (Martin, 2026-11-07): compiled
  on every nixpkgs update, and the machine ran out of memory.

Kept free of Flask so it can be used without the web interface.
"""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

DRY_BUILD_TIMEOUT = 600   # evaluating a large config can take minutes
_SHOW_CHUNK       = 200   # derivations per `nix derivation show` call

# nix says "this derivation" / "this path" when there is exactly one.
_RE_BUILT   = re.compile(r"^(?:these \d+ derivations|this derivation) will be built:")
_RE_FETCHED = re.compile(
    r"(?:these \d+ paths|this path) will be fetched \(([0-9.]+)\s*([KMGT]i?B) download")
_UNIT_MIB   = {"KiB": 1 / 1024, "MiB": 1, "GiB": 1024, "TiB": 1024 * 1024}


def flake_ref(nixos_dir: str, hostname: str, is_git_repo: bool) -> str:
    """The flake reference nixos-rebuild gets for this host.

    Without git, nix would try to copy through the git index and fail; the
    path: prefix makes it read straight from the file system.
    """
    if is_git_repo:
        return f".#{hostname}"
    return f"path:{Path(nixos_dir).resolve().as_posix()}#{hostname}"


def dry_build_cmd(nixos_dir: str, use_flake: bool, flake_arg: str = "",
                  update_flake: bool = False) -> list[str]:
    """`nixos-rebuild dry-build` for the same target the rebuild will build.

    `dry-build`, not `build --dry-run`: the Python rewrite of nixos-rebuild
    rejects the latter. With update_flake the inputs are resolved afresh in
    memory only (`--recreate-lock-file --no-write-lock-file`), so the forecast
    matches a rebuild that runs `nix flake update` first, and flake.lock is
    left untouched.
    """
    if use_flake:
        cmd = ["nixos-rebuild", "dry-build", "--flake", flake_arg]
        if update_flake:
            cmd += ["--recreate-lock-file", "--no-write-lock-file"]
        return cmd
    conf = Path(nixos_dir) / "configuration.nix"
    return ["nixos-rebuild", "dry-build", "-I", f"nixos-config={conf}"]


def parse_dry_build(text: str) -> tuple[list[str], float]:
    """(derivations to build, download size in MiB) from dry-build output."""
    drvs: list[str] = []
    mib = 0.0
    in_built = False
    for line in text.splitlines():
        if _RE_BUILT.match(line):
            in_built = True
            continue
        if in_built:
            s = line.strip()
            if line.startswith((" ", "\t")) and s.startswith("/nix/store/") and s.endswith(".drv"):
                drvs.append(s)
                continue
            in_built = False
        m = _RE_FETCHED.search(line)
        if m:
            mib += float(m.group(1)) * _UNIT_MIB.get(m.group(2), 0)
    return drvs, mib


def _env_of(drv: dict) -> dict:
    """The build environment of one derivation, structured attrs included."""
    env = dict(drv.get("env") or {})
    raw = env.get("__json")
    if isinstance(raw, str):
        try:
            env.update(json.loads(raw))
        except ValueError:
            pass
    if isinstance(drv.get("structuredAttrs"), dict):
        env.update(drv["structuredAttrs"])
    return env


def heavy_from_show(shown: dict) -> list[dict]:
    """Real packages among the output of `nix derivation show`.

    A package built from source has a `src` (or `srcs`) plus `pname` and
    `version` – the mark of mkDerivation. NixOS glue has none of that; a
    helper script may have a `src` but no pname/version. Measured on
    nix-desktop: 0 of 25 glue derivations of an ordinary rebuild match,
    bambu-studio does.
    """
    derivations = shown.get("derivations", shown) if isinstance(shown, dict) else {}
    found: dict[str, dict] = {}
    for drv in derivations.values():
        if not isinstance(drv, dict):
            continue
        env = _env_of(drv)
        if (env.get("src") or env.get("srcs")) and env.get("pname") and env.get("version"):
            name = str(env["pname"])
            found.setdefault(name, {"name": name, "version": str(env["version"])})
    return sorted(found.values(), key=lambda d: d["name"])


def heavy_builds(drvs: list[str]) -> list[dict]:
    """Ask nix about the derivations and keep the real packages."""
    out: list[dict] = []
    for i in range(0, len(drvs), _SHOW_CHUNK):
        chunk = drvs[i:i + _SHOW_CHUNK]
        try:
            r = subprocess.run(
                ["nix", "--extra-experimental-features", "nix-command",
                 "derivation", "show", *chunk],
                capture_output=True, text=True, timeout=120,
            )
            if r.returncode != 0:
                continue
            out += heavy_from_show(json.loads(r.stdout or "{}"))
        except (OSError, subprocess.TimeoutExpired, ValueError):
            continue
    seen: set[str] = set()
    return [d for d in out if not (d["name"] in seen or seen.add(d["name"]))]


def precheck(nixos_dir: str, use_flake: bool, flake_arg: str = "",
             update_flake: bool = False) -> dict:
    """Run the dry build and summarise it.

    Never raises. `ok` is False when the forecast is not available – nix
    missing, evaluation failed, timeout. The caller then simply starts the
    rebuild, which reports a real error far better than a forecast could.
    """
    cmd = dry_build_cmd(nixos_dir, use_flake, flake_arg, update_flake)
    try:
        r = subprocess.run(cmd, cwd=nixos_dir, capture_output=True, text=True,
                           timeout=DRY_BUILD_TIMEOUT)
    except (OSError, subprocess.TimeoutExpired):
        return {"ok": False, "heavy": [], "build_count": 0, "fetch_mib": 0.0}
    text = (r.stdout or "") + "\n" + (r.stderr or "")
    drvs, mib = parse_dry_build(text)
    if r.returncode != 0:
        return {"ok": False, "heavy": [], "build_count": 0, "fetch_mib": 0.0}
    return {"ok": True, "heavy": heavy_builds(drvs) if drvs else [],
            "build_count": len(drvs), "fetch_mib": mib}
