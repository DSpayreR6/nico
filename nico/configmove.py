"""
Moving the config directory to another place (Martin, 2026-11-25).

"Really move": afterwards the old directory is gone. Everything goes along –
the versioning (.git with its remote), config.json, every file. The app
settings then point to the new place (done by the route, which owns them).

Safety:
- the target must be new or an empty directory, and neither inside the
  config nor containing it;
- on the same filesystem this is one rename – atomic, nothing half-done;
- across filesystems: copy, flush, verify, only then remove the source
  (_move_across). A failed copy or verification leaves the source untouched
  and the copy is removed again; a failed removal of the source leaves the
  verified copy untouched.
"""
from __future__ import annotations

import errno
import hashlib
import os
import shutil
from pathlib import Path

ETC_NIXOS = Path("/etc/nixos")   # module constant: tests point it elsewhere
# The rename of the same-filesystem case; tests raise EXDEV here to take the
# path across filesystems without a second filesystem.
_rename = os.rename


def check_target(src: Path, target: Path) -> str | None:
    """Error code when `target` cannot take the config from `src`, else None."""
    src, target = src.resolve(), target.resolve()
    if target == src or src in target.parents:
        return "ERR_MOVE_INSIDE"
    if target in src.parents:
        return "ERR_MOVE_CONTAINS"
    if target.exists():
        if not target.is_dir():
            return "ERR_NOT_A_DIR"
        if any(target.iterdir()):
            return "ERR_MOVE_NOT_EMPTY"
    return None


def move(src: Path, target: Path) -> tuple[str | None, bool]:
    """Move the config directory. Returns (error code or None, source_left).

    source_left: moved and verified, but the old directory could not be
    removed completely - the target is the config now, what is left at the
    source is the user's to delete (revision 2026-11-25, F1).
    """
    err = check_target(src, target)
    if err:
        return err, False
    src, target = src.resolve(), target.resolve()
    try:
        if target.exists():
            target.rmdir()               # empty: the moved directory takes its place
        target.parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        return "ERR_MOVE_FAILED", False
    # Same filesystem: one rename - atomic, nothing half-done.
    try:
        _rename(src, target)
        return None, False
    except OSError as exc:
        if exc.errno != errno.EXDEV:
            return "ERR_MOVE_FAILED", False
    return _move_across(src, target)


def _move_across(src: Path, target: Path) -> tuple[str | None, bool]:
    """Copy, flush, verify, only then remove the source.

    shutil.move did copy and delete in one call: when the delete broke off
    half-way, the error path removed the complete copy - and what was left
    was half the source (revision 2026-11-25, F1). Now the phases are
    separate (decision Martin 2026-11-26):

      1. copy (links as links, modes and times kept)
      2. fsync every file and directory - on the target device, not only
         in memory
      3. drop the copied files from the page cache, so step 4 reads them
         back from the device and not from memory
      4. compare with the source: same entries, same link targets, same
         content (sha256). Any difference: the copy goes, the source stays.
      5. remove the source. If that fails, the verified target stays
         untouched - never removed again - and the caller says what is left.

    Not checkable from here: a cache inside the device itself.
    """
    try:
        shutil.copytree(src, target, symlinks=True)
        _flush_tree(target)
        bad = _first_difference(src, target)
    except OSError:
        shutil.rmtree(target, ignore_errors=True)
        return "ERR_MOVE_FAILED", False
    if bad is not None:
        shutil.rmtree(target, ignore_errors=True)
        return "ERR_MOVE_VERIFY\x1f" + bad, False
    try:
        shutil.rmtree(src)
    except OSError:
        return None, True
    return None, False


def _flush_tree(root: Path) -> None:
    """fsync every file and directory below `root`, then ask the kernel to
    drop the files from the page cache (best effort where unsupported)."""
    for dirpath, dirnames, filenames in os.walk(root):
        for name in filenames:
            p = os.path.join(dirpath, name)
            if os.path.islink(p):
                continue
            fd = os.open(p, os.O_RDONLY)
            try:
                os.fsync(fd)
                if hasattr(os, "posix_fadvise"):
                    try:
                        os.posix_fadvise(fd, 0, 0, os.POSIX_FADV_DONTNEED)
                    except OSError:
                        pass
            finally:
                os.close(fd)
        try:
            fd = os.open(dirpath, os.O_RDONLY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
        except OSError:
            pass                       # not every filesystem allows it


def _digest(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _entries(root: Path) -> dict[str, tuple]:
    """{relative path: (kind, detail)} of everything below `root`."""
    out: dict[str, tuple] = {}
    for dirpath, dirnames, filenames in os.walk(root):
        for name in dirnames + filenames:
            p = os.path.join(dirpath, name)
            rel = os.path.relpath(p, root)
            if os.path.islink(p):
                out[rel] = ("link", os.readlink(p))
            elif os.path.isdir(p):
                out[rel] = ("dir", "")
            else:
                out[rel] = ("file", p)
    return out


def _first_difference(src: Path, target: Path) -> str | None:
    """The first entry in which the copy differs from the source, or None."""
    a, b = _entries(src), _entries(target)
    for rel in sorted(set(a) | set(b)):
        if rel not in a or rel not in b:
            return rel
        ka, da = a[rel]
        kb, db = b[rel]
        if ka != kb:
            return rel
        if ka == "link" and da != db:
            return rel
        if ka == "file" and _digest(da) != _digest(db):
            return rel
    return None


def symlink_pointed_to(old: Path) -> bool:
    """True when /etc/nixos is a symlink that pointed to `old` – after the
    move it dangles and should be switched to the new place."""
    try:
        if not ETC_NIXOS.is_symlink():
            return False
        dest = Path(os.readlink(ETC_NIXOS))
        if not dest.is_absolute():
            dest = ETC_NIXOS.parent / dest
        return os.path.normpath(dest) == os.path.normpath(old.resolve())
    except OSError:
        return False
