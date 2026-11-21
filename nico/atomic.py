"""
Writing a config file so that an abrupt end cannot corrupt it.

`Path.write_text` truncates the file to zero bytes the moment it opens it and
only then writes the new content. For the span in between - normally
milliseconds - what lies on disk is an empty or half-written file. And once
write_text returns, the content may still be in the page cache: the program
believes it saved, while the disk does not have the data yet.

Either window is harmless until the machine stops in it. A reset then leaves a
truncated configuration.nix or flake.nix behind, and that does not merely lose
the last edit - the file no longer parses, so nothing builds at all, not even a
step back to an older state. The running system still boots from its existing
generation, but repairing the config means an editor or a checkpoint.

The sequence here leaves only two possible outcomes, the complete old file or
the complete new one:

  1. write to a temporary file in the *same* directory (a rename only stays
     atomic within one filesystem)
  2. flush and fsync - the data is on the disk before anything is swapped
  3. os.replace - the rename is indivisible; there is no moment in which half
     of it has happened
  4. fsync the directory, so the rename itself survives a power loss

What this does not do is tie *several* files together. A reset between writing
flake.nix and the host file still leaves one new and one old - each intact, but
not matching. Only a checkpoint taken before writing covers that.

The same pattern already guarded the JSON settings (config_manager); the .nix
files were never part of it.
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

from . import outside

# Mode for a file that does not exist yet. The temporary file is created 0600,
# so without this every newly written config would end up readable only by its
# owner - and an existing file would silently lose its mode, which for a
# tracked file git reports as a change.
_DEFAULT_MODE = 0o644


def write_bytes(path: str | Path, data: bytes) -> None:
    """Replace `path` with `data`, atomically."""
    target = Path(path)

    # A rename replaces the symlink itself, while write_text wrote *through* it.
    # Someone who linked /etc/nixos/configuration.nix to their repository would
    # otherwise find the link gone and a regular file in its place, with the
    # repository untouched. Follow the link and write where it points.
    if target.is_symlink():
        target = target.resolve()

    try:
        mode = target.stat().st_mode & 0o7777
    except OSError:
        mode = _DEFAULT_MODE

    tmp_name = None
    try:
        with tempfile.NamedTemporaryFile(
            "wb", dir=target.parent, prefix=f".{target.name}.", suffix=".tmp",
            delete=False,
        ) as fh:
            tmp_name = fh.name
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp_name, mode)
        os.replace(tmp_name, target)
        tmp_name = None
        # What NiCo wrote is what NiCo knows (outside.py): a form save
        # after this does not count it as a change from outside.
        outside.remember(target, data)
    finally:
        # A failed write must not leave its fragment next to the real file.
        if tmp_name is not None:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass

    # Best effort: without this the rename can still be lost in a power cut
    # (the old file comes back - never a damaged one). Not every filesystem
    # allows opening a directory, so a failure here is not an error.
    try:
        fd = os.open(target.parent, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    except OSError:
        pass


def write_text(path: str | Path, content: str, encoding: str = "utf-8") -> None:
    """Replace `path` with `content`, atomically."""
    write_bytes(path, content.encode(encoding))


class Rollback:
    """Several files changed as one step: remember them, put them back.

    Each write above is atomic, several are not (see the module text). For
    the actions that touch more than one file - switching to a flake, adding
    a flake host - a failed last write left the first ones changed: a
    rewritten configuration.nix, a new host directory nothing points to
    (Codex revision 2026-11-20). The same idea as importer.replace_nix_tree,
    for a handful of named paths:

        rb = Rollback([conf, flake, host_default])   # before the first write
        try: ...writes...
        except OSError: restored = rb.restore()

    A file that did not exist is removed again, so is a directory that did
    not exist, once it is empty. Martin asked (2026-11-21) why not build the
    new state in a second directory and swap: the files lie in the config
    root and below it, so only the whole config directory could be swapped -
    with .git, Syncthing and open editors in it, and two renames are not one.
    """

    def __init__(self, paths) -> None:
        self._files: dict = {}
        self._known: dict = {}
        self._dirs: list = []
        for raw in paths:
            p = Path(raw)
            # Reading fails here, before anything is written - the caller
            # stops with nothing changed.
            self._files[p] = p.read_bytes() if p.is_file() else None
            self._known[p] = outside.known(p)
            d = p.parent
            while not d.exists():
                self._dirs.append(d)
                d = d.parent

    def restore(self) -> bool:
        """Put every path back. False when one of them could not be."""
        ok = True
        for p, data in self._files.items():
            try:
                if data is None:
                    p.unlink(missing_ok=True)
                elif p.is_file() and p.read_bytes() == data:
                    # Untouched - the failed write was atomic. Writing it
                    # again would only fail again when the disk is full.
                    pass
                else:
                    p.parent.mkdir(parents=True, exist_ok=True)
                    write_bytes(p, data)
                outside.set_known(p, self._known[p])
            except OSError:
                ok = False
        # Deepest first; a directory that is not empty stays (something else
        # was put there, or a file above could not be removed).
        for d in sorted(set(self._dirs), key=lambda x: len(x.parts), reverse=True):
            try:
                d.rmdir()
            except OSError:
                pass
        return ok
