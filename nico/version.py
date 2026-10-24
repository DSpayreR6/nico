"""Single source for NiCo's version number at runtime.

vorgaben.txt: the version lives in flake.nix and nowhere else. It used to be
copied into all seven language files as a translation string, where it sat at
0.9.10 while two releases went past it – a version number is not a text that
differs per language, and seven copies are seven chances to forget one.

Two cases:
  installed build – the build writes the version into a VERSION file next to
                    this module, because flake.nix is not part of the package
  source tree     – read flake.nix directly, so a developer always sees what
                    the working tree says
"""

from __future__ import annotations

import re
from pathlib import Path

_VERSION_RE = re.compile(r'^\s*version\s*=\s*"([^"]+)"', re.M)


def get_version() -> str:
    """Version string, or an empty string when it cannot be determined."""
    stamped = Path(__file__).resolve().parent / "VERSION"
    try:
        if stamped.is_file():
            value = stamped.read_text(encoding="utf-8").strip()
            if value:
                return value
    except OSError:
        pass

    flake = Path(__file__).resolve().parent.parent / "flake.nix"
    try:
        if flake.is_file():
            match = _VERSION_RE.search(flake.read_text(encoding="utf-8"))
            if match:
                return match.group(1)
    except OSError:
        pass

    return ""


def get_revision() -> str:
    """Commit this build came from, or an empty string when unknown.

    The version alone cannot tell two builds of the same cycle apart: it stays
    at e.g. 0.14.0-dev from one release to the next, so a report from a test
    build off the dev branch would be unplaceable. Same two cases as
    get_version(): a file stamped at build time, or the working tree's git.
    """
    stamped = Path(__file__).resolve().parent / "REVISION"
    try:
        if stamped.is_file():
            value = stamped.read_text(encoding="utf-8").strip()
            if value and value != "unknown":
                return value
    except OSError:
        pass

    import subprocess
    root = Path(__file__).resolve().parent.parent
    try:
        out = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=5, check=False,
        )
        if out.returncode == 0:
            rev = out.stdout.strip()
            dirty = subprocess.run(
                ["git", "-C", str(root), "status", "--porcelain"],
                capture_output=True, text=True, timeout=5, check=False,
            )
            # A modified working tree is not that commit any more – saying so
            # avoids chasing a bug in code that was never built.
            return f"{rev}+" if dirty.returncode == 0 and dirty.stdout.strip() else rev
    except (OSError, subprocess.SubprocessError):
        pass

    return ""
