"""
The files of a config directory - and the ones an action does not cover.

Until 2026-11-26 about six places walked the config directory themselves
(format check, categorize, detach, backup ZIP, export, import), each with
its own rules. Some skipped symlinks leading out of the directory, others
followed them and read or even wrote the file at the other end (revision
2026-11-25, S1/S2). This module is the one walk all of them use.

A file the user linked in from elsewhere lies there on purpose - secrets
kept out of the repository, modules shared by several machines, the
hardware file of /etc/nixos. NiCo never touches it (Martin, 2026-11-26: no
option to include them). But leaving it out silently is what NiCo avoids
everywhere else, so the walk also returns what it left out and why, and
every action the user starts shows that list before it runs:

  link_out     symlink to a file or directory outside the config - not covered
  hardlink     the same file under a second name elsewhere - covered, but the
               copy here is independent of the other one afterwards
  nix_path     a path in the Nix code pointing outside the config
               (`imports = [ /etc/nixos/hardware-configuration.nix ];`) -
               the config needs a file that is not in it
  link_breaks  (moving only) a symlink that points nowhere after the move:
               relative and leading out, or absolute and leading in
  path_breaks  (moving only) an absolute path in the Nix code into the
               config - it still names the old place after the move

Hidden directories (.git) are never walked. Symlinked directories are not
descended into; one into /nix/store (`result` of nix build) is not worth a
line in the list.
"""
from __future__ import annotations

import os
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path


@dataclass
class Left:
    file: str                 # relative to the config directory
    kind: str                 # see module docstring
    target: str = ""          # where a link points / the path in the code
    line: int = 0             # nix_path, path_breaks: line in `file`


@dataclass
class Scan:
    files: list[tuple[str, Path]] = field(default_factory=list)   # covered
    left: list[Left] = field(default_factory=list)                # reported

    def rels(self) -> list[str]:
        return [rel for rel, _ in self.files]

    def left_json(self) -> list[dict]:
        return [asdict(x) for x in self.left]


def _inside(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def link_leaves(path: Path, root: Path) -> bool:
    """True for a symlink whose target lies outside `root` (or cannot be
    resolved - a loop, a dangling link: not something to follow either)."""
    if not path.is_symlink():
        return False
    try:
        return not _inside(path.resolve(strict=True), Path(root).resolve())
    except (OSError, RuntimeError):
        return True


def _link_breaks_on_move(path: Path, root: Path) -> bool:
    """After moving `root` elsewhere, does this symlink point nowhere?"""
    try:
        raw = os.readlink(path)
    except OSError:
        return False
    dest = Path(raw)
    if dest.is_absolute():
        return _inside(Path(os.path.normpath(dest)), root)
    return not _inside(Path(os.path.normpath(path.parent / dest)), root)


def scan(root: str | Path, *, nix_only: bool = True, move: bool = False,
         refs: bool = False) -> Scan:
    """Walk the config directory. nix_only: only `.nix` files (format check,
    categorize, detach); otherwise every file (backup ZIP, export, move).
    move: also report what breaks when the directory moves. refs: also read
    the .nix files for paths in the code (the list shown before an action;
    the action itself only needs the files)."""
    root = Path(root).resolve()
    out = Scan()
    for dirpath, dirnames, filenames in os.walk(root):
        here = Path(dirpath)
        # Hidden directories are never part of the walk; symlinked ones are
        # looked at as entries below, never descended into.
        keep = []
        for name in sorted(dirnames):
            if name.startswith("."):
                continue
            p = here / name
            if p.is_symlink():
                filenames.append(name)
                continue
            keep.append(name)
        dirnames[:] = keep
        for name in sorted(filenames):
            if name.startswith("."):
                continue
            p = here / name
            rel = p.relative_to(root).as_posix()
            is_dir_link = p.is_symlink() and p.is_dir()
            if nix_only and not is_dir_link and not name.endswith(".nix"):
                continue
            if p.is_symlink():
                try:
                    target = os.readlink(p)
                except OSError:
                    target = ""
                if link_leaves(p, root):
                    if not str(Path(p).resolve()).startswith("/nix/store/"):
                        out.left.append(Left(rel, "link_out", target))
                    continue
                if move and _link_breaks_on_move(p, root):
                    out.left.append(Left(rel, "link_breaks", target))
                if is_dir_link:
                    continue          # inside: its files are walked anyway
            try:
                st = p.stat()
            except OSError:
                continue
            if not p.is_file():
                continue
            if not p.is_symlink() and st.st_nlink > 1:
                out.left.append(Left(rel, "hardlink"))
            out.files.append((rel, p))
    if refs:
        for rel, p in out.files:
            if rel.endswith(".nix"):
                out.left.extend(nix_paths(p, rel, root, move=move))
    return out


def nix_files(nixos_dir: str | Path, under: str | Path | None = None) -> list[Path]:
    """Stand-in for `sorted(Path(nixos_dir).rglob("*.nix"))`: the config's
    own .nix files, without hidden directories and links out of it. The
    paths start with `nixos_dir` as given (not resolved), so callers that
    take `relative_to(nixos_dir)` keep working. `under`: only files below
    this directory (a subdirectory of the config, e.g. the Home Manager
    directory)."""
    base = Path(nixos_dir)
    prefix = ""
    if under is not None:
        prefix = Path(os.path.relpath(Path(under), base)).as_posix()
        if prefix == ".":
            prefix = ""
        elif prefix.startswith(".."):
            return []
    out = []
    for rel, _p in scan(base).files:
        if prefix and not (rel == prefix or rel.startswith(prefix + "/")):
            continue
        out.append(base / rel)
    return out


# ── Paths in the Nix code ───────────────────────────────────────────────────
# Only path literals count: unquoted, starting with /, ./, ../ or ~/. A
# string in quotes is a setting (`fileSystems."/home"`, `"/etc/ssh/…"`), not
# a file the build reads. Comments and strings are blanked first by the one
# walker that knows nested strings (importer._mask_literals - a regex here
# ended `"${pkgs.writeShellScript "x" ''…''}"` at the inner quote and
# reported the script's paths), line breaks kept for the line numbers.

_PATH_RE = re.compile(
    r"(?<![\w.\-/}~$'\"])"           # not inside a word, URL or interpolation
    r"(~/|\.\./|\./|/)"              # anchor
    r"([A-Za-z0-9._+\-]+(?:/[A-Za-z0-9._+\-]+)*)")


def nix_paths(path: Path, rel: str, root: Path, *, move: bool = False) -> list[Left]:
    """Path literals in a .nix file that point outside the config (and, when
    moving, absolute ones into it)."""
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []
    from .importer import _mask_literals     # importer uses this module
    code = _mask_literals(text)
    found: list[Left] = []
    seen: set[tuple[int, str]] = set()
    for m in _PATH_RE.finditer(code):
        anchor, rest = m.group(1), m.group(2)
        literal = anchor + rest
        if anchor == "/" and literal.startswith("/nix/store/"):
            continue
        if anchor == "~/":
            dest = Path(os.path.expanduser(literal))
        elif anchor == "/":
            dest = Path(literal)
        else:
            dest = path.parent / literal
        dest = Path(os.path.normpath(dest))
        line = code.count("\n", 0, m.start()) + 1
        if (line, literal) in seen:
            continue
        seen.add((line, literal))
        if not _inside(dest, root):
            found.append(Left(rel, "nix_path", literal, line))
        elif move and anchor in ("/", "~/"):
            found.append(Left(rel, "path_breaks", literal, line))
    return found
