"""
Moving the config directory to another place (Martin, 2026-11-25).

"Really move": afterwards the old directory is gone. Everything goes along –
the versioning (.git with its remote), config.json, every file. The app
settings then point to the new place (done by the route, which owns them).

Safety:
- the target must be new or an empty directory, and neither inside the
  config nor containing it;
- on the same filesystem this is one rename – atomic, nothing half-done;
- across filesystems shutil.move copies first and removes the source only
  after the copy succeeded. A failed copy leaves the source untouched and the
  partial copy is removed again.
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path

ETC_NIXOS = Path("/etc/nixos")   # module constant: tests point it elsewhere


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


def move(src: Path, target: Path) -> str | None:
    """Move the config directory. Returns an error code or None."""
    err = check_target(src, target)
    if err:
        return err
    src, target = src.resolve(), target.resolve()
    try:
        if target.exists():
            target.rmdir()               # empty: the moved directory takes its place
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(target))
    except OSError:
        # A copy across filesystems that broke off: the source still exists,
        # the partial copy must not stay behind as a second config.
        if src.exists() and target.exists():
            shutil.rmtree(target, ignore_errors=True)
        return "ERR_MOVE_FAILED"
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
