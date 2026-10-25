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
