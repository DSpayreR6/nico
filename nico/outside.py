"""
Has a .nix file changed outside NiCo since its form was loaded?

A form holds the values of the file as it was when the form was filled. If
the file changes on disk while the form is open - Syncthing from a second
machine, `git pull`, an editor, a second NiCo - the next save from the form,
including the silent one before a rebuild, wrote those old values back over
the newer file (decision Martin, 2026-11-17: ask instead).

The version hash in the file cannot tell: a NiCo on another machine writes a
valid one. So this process remembers, per file, the content it last handed
to a form or wrote itself, and a form save compares that with the disk.

  * remember() is called by atomic.write_bytes for every .nix file NiCo
    writes, and by the form-loading read (/api/parse/co).
  * A file NiCo has not seen since it started counts as unchanged: without
    a known state there is nothing to compare, and asking would be noise.
  * git writing files (rollback, pull) is not NiCo writing them in this
    sense - a form that was not reloaded afterwards is stale, and that is
    exactly the case to ask about.

Memory only, by design: the state belongs to the forms open in this process.
"""
from __future__ import annotations

import difflib
import threading
from pathlib import Path

_known: dict[str, str] = {}
_lock = threading.Lock()


def _key(path: str | Path) -> str:
    return str(Path(path).resolve())


def remember(path: str | Path, content: str | bytes) -> None:
    """Record `content` as the state of `path` NiCo knows."""
    if Path(path).suffix != ".nix":
        return
    if isinstance(content, bytes):
        content = content.decode("utf-8", errors="replace")
    with _lock:
        _known[_key(path)] = content


def known(path: str | Path) -> str | None:
    with _lock:
        return _known.get(_key(path))


def changed_outside(path: str | Path) -> bool:
    """True if `path` differs on disk from the state NiCo knows."""
    base = known(path)
    if base is None:
        return False
    try:
        return Path(path).read_text(encoding="utf-8") != base
    except OSError:
        return False


def diff(path: str | Path, label: str) -> dict:
    """Known state against disk, in the shape /api/git/diff returns.

    Only added and removed lines: that is all the diff viewer shows.
    """
    base = known(path) or ""
    try:
        now = Path(path).read_text(encoding="utf-8")
    except OSError:
        now = ""
    return diff_texts(base, now, label)


def diff_texts(base: str, now: str, label: str) -> dict:
    """Two texts against each other, same shape as diff()."""
    hunks = []
    # The version line changes with every write (hash); it says nothing
    # about what was changed.
    def lines(text):
        return [l for l in text.splitlines() if not l.startswith("# nico-version:")]
    a, b = lines(base), lines(now)
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if tag in ("replace", "delete"):
            hunks += [{"type": "removed", "content": line} for line in a[i1:i2]]
        if tag in ("replace", "insert"):
            hunks += [{"type": "added", "content": line} for line in b[j1:j2]]
    files = [{"filename": label, "status": "modified", "hunks": hunks}] if hunks else []
    return {"files": files}
