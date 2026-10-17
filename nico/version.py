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
