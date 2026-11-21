"""
Has a .nix file changed outside NiCo since its form was loaded?

A form holds the values of the file as it was when the form was filled. If
the file changes on disk while the form is open - Syncthing from a second
machine, `git pull`, an editor, a second NiCo - the next save from the form,
including the silent one before a rebuild, wrote those old values back over
the newer file (decision Martin, 2026-11-17: ask instead).

The version hash in the file cannot tell: a NiCo on another machine writes a
valid one. Two things are compared instead:

  * the base of the form: loaded() hands the form a digest of the content
    it was filled from (/api/parse/co), the form sends it back with every
    save. It belongs to that one form, so a second tab that loads the newer
    file does not move the base of the first one. Until 2026-11-20 the base
    was one state per process and file, and exactly that happened
    (revision 2026-11-18, finding 4).
  * what NiCo itself wrote last: remember() is called by atomic.write_bytes
    for every .nix file. A disk state NiCo wrote is never "from outside" -
    otherwise the write that follows every form save (/api/write) would
    count as a change.

  * A missing or unreadable file is a change for a form loaded from it:
    the save would recreate it.
  * Without a base (an old client) the last write of NiCo is the only
    comparison; a file NiCo has not seen then counts as unchanged.
  * git writing files (rollback, pull) is not NiCo writing them in this
    sense - a form that was not reloaded afterwards is stale, and that is
    exactly the case to ask about.

Memory only, by design: the state belongs to the forms open in this process.
"""
from __future__ import annotations

import difflib
import hashlib
import threading
from pathlib import Path

_known: dict[str, str] = {}
# Contents forms were filled from, by (file, digest): what the diff for a
# stale form compares against. A few per file are enough - one per open tab.
_loaded: dict[str, dict[str, str]] = {}
_LOADED_PER_FILE = 8
_lock = threading.Lock()


def _key(path: str | Path) -> str:
    return str(Path(path).resolve())


def _text(content: str | bytes) -> str:
    if isinstance(content, bytes):
        return content.decode("utf-8", errors="replace")
    return content


def digest(content: str | bytes) -> str:
    """The base a form carries for `content`."""
    return hashlib.sha256(_text(content).encode("utf-8")).hexdigest()


def remember(path: str | Path, content: str | bytes) -> None:
    """Record `content` as the state NiCo itself wrote to `path`."""
    if Path(path).suffix != ".nix":
        return
    with _lock:
        _known[_key(path)] = _text(content)


def set_known(path: str | Path, content: str | None) -> None:
    """Put back what known() said before - for atomic.Rollback, whose
    restoring write must not make an old state count as written by NiCo."""
    with _lock:
        if content is None:
            _known.pop(_key(path), None)
        else:
            _known[_key(path)] = content


def known(path: str | Path) -> str | None:
    with _lock:
        return _known.get(_key(path))


def loaded(path: str | Path, content: str | bytes) -> str:
    """A form is filled from `content`; returns the base it sends back."""
    text = _text(content)
    base = digest(text)
    with _lock:
        states = _loaded.setdefault(_key(path), {})
        states.pop(base, None)
        states[base] = text
        while len(states) > _LOADED_PER_FILE:
            states.pop(next(iter(states)))
    return base


def changed_outside(path: str | Path, base: str | None = None) -> bool:
    """True if `path` on disk is neither the form's base nor NiCo's last write."""
    mine = known(path)
    if base is None and mine is None:
        return False
    try:
        now = Path(path).read_text(encoding="utf-8")
    except OSError:
        # Deleted or unreadable: saving the form would recreate the file
        # from old values. Counted as unchanged before (finding 4). Only
        # with a base - without one no form says it was loaded from it.
        return base is not None
    if mine is not None and now == mine:
        return False
    if base is None:
        return True
    return digest(now) != base


def diff(path: str | Path, label: str, base: str | None = None) -> dict:
    """The state the form was filled from against disk, in the shape
    /api/git/diff returns. Falls back to NiCo's last write.

    Only added and removed lines: that is all the diff viewer shows.
    """
    with _lock:
        before = _loaded.get(_key(path), {}).get(base or "")
    if before is None:
        before = known(path) or ""
    try:
        now = Path(path).read_text(encoding="utf-8")
    except OSError:
        now = ""
    return diff_texts(before, now, label)


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
