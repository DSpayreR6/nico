"""
Git integration for NiCo – Layer 1: local repository.

Layer 1 (this file): init local repo, auto-commit after every write.
Layer 2 (future, Admin-Bereich): optional GitHub/remote push.

All git commands use subprocess with explicit argument lists – never shell=True
with variable content to prevent shell injection.
"""

import subprocess
from datetime import datetime
from pathlib import Path


def _run(args: list[str], cwd: str, timeout: int = 10) -> tuple[int, str]:
    """Run a git command. Returns (returncode, combined stdout+stderr).
    Returns (127, 'git not found') if git is not installed."""
    try:
        result = subprocess.run(
            ["git"] + args,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return result.returncode, (result.stdout + result.stderr).strip()
    except FileNotFoundError:
        return 127, "git not found"
    except Exception as e:
        return 1, str(e)


def is_git_repo(nixos_dir: str) -> bool:
    """Return True if nixos_dir is the root of its own git repository."""
    rc, out = _run(["rev-parse", "--show-toplevel"], cwd=nixos_dir)
    if rc != 0:
        return False
    return Path(out.strip()).resolve() == Path(nixos_dir).resolve()


def _ensure_identity(nixos_dir: str) -> None:
    """Set a local git identity if no global one is configured."""
    rc, _ = _run(["config", "--global", "user.email"], cwd=nixos_dir)
    if rc != 0:
        # No global identity – set a local fallback so commits work
        _run(["config", "user.email", "nico@localhost"], cwd=nixos_dir)
        _run(["config", "user.name",  "NiCo"],           cwd=nixos_dir)


def init_repo(nixos_dir: str) -> tuple[bool, str]:
    """
    Initialize a git repository in nixos_dir and create an initial commit
    of all existing files.
    Returns (success, message).
    """
    rc, out = _run(["init"], cwd=nixos_dir)
    if rc != 0:
        return False, f"git init fehlgeschlagen: {out}"

    _ensure_identity(nixos_dir)

    _run(["add", "-A"], cwd=nixos_dir)
    rc, out = _run(["commit", "-m", "NiCo: Repository initialisiert"], cwd=nixos_dir)
    if rc != 0 and "nothing to commit" not in out:
        return False, f"Initialer Commit fehlgeschlagen: {out}"

    return True, "Git-Repository wurde angelegt und initialer Commit erstellt."


def stage_all(nixos_dir: str) -> tuple[bool, str]:
    """Stage all current files in nixos_dir. Best-effort helper for rebuild flows."""
    if not is_git_repo(nixos_dir):
        return False, "Kein Git-Repository."
    rc, out = _run(["add", "-A"], cwd=nixos_dir)
    if rc != 0:
        return False, out or "git add -A fehlgeschlagen"
    return True, ""


def auto_commit(nixos_dir: str, label: str = "") -> tuple[bool, str]:
    """
    Stage all changes and create a commit.
    label: optional user-provided name shown in the git log (the "Sicherungspunkt" label).
    Returns (success, message).
    """
    if not is_git_repo(nixos_dir):
        return False, "Kein Git-Repository."

    _ensure_identity(nixos_dir)
    _run(["add", "-A"], cwd=nixos_dir)

    # Check if there's actually something to commit
    rc, status = _run(["status", "--porcelain"], cwd=nixos_dir)
    if not status:
        return True, ""  # Nothing changed – silent success

    ts = datetime.now().strftime("%Y-%m-%d %H:%M")
    if label and label.strip():
        msg = f"{label.strip()} ({ts})"
    else:
        msg = f"NiCo: Konfiguration geschrieben ({ts})"

    rc, out = _run(["commit", "-m", msg], cwd=nixos_dir)
    if rc != 0:
        return False, f"Commit fehlgeschlagen: {out}"

    return True, msg


def check_remote_status(nixos_dir: str) -> dict:
    """
    Check whether the local repo is behind its remote.
    Returns {
        "has_remote": bool,
        "remote_url": str,
        "behind": int,   # commits local is behind remote (0 = up to date)
        "error": str     # non-empty if fetch failed
    }
    """
    if not is_git_repo(nixos_dir):
        return {"has_remote": False, "remote_url": "", "behind": 0, "error": ""}

    rc, remote_url = _run(["remote", "get-url", "origin"], cwd=nixos_dir)
    if rc != 0 or not remote_url:
        return {"has_remote": False, "remote_url": "", "behind": 0, "error": ""}

    rc, fetch_out = _run(["fetch", "--quiet", "origin"], cwd=nixos_dir, timeout=15)
    if rc != 0:
        return {"has_remote": True, "remote_url": remote_url, "behind": 0,
                "error": f"fetch fehlgeschlagen: {fetch_out}"}

    rc, count_out = _run(["rev-list", "HEAD..@{u}", "--count"], cwd=nixos_dir)
    if rc != 0:
        # No upstream tracking branch configured
        return {"has_remote": True, "remote_url": remote_url, "behind": 0, "error": ""}

    try:
        behind = int(count_out.strip())
    except ValueError:
        behind = 0

    return {"has_remote": True, "remote_url": remote_url, "behind": behind, "error": ""}


def get_log(nixos_dir: str, n: int = 30) -> list[dict]:
    """Return the last n commits as a list of {hash, message, date} dicts."""
    if not is_git_repo(nixos_dir):
        return []
    # Use unit separator (\x1f) so messages with spaces don't break parsing
    fmt = "%H\x1f%s\x1f%ai"
    rc, out = _run(["log", f"-{n}", f"--format={fmt}"], cwd=nixos_dir)
    if rc != 0 or not out:
        return []
    commits = []
    for line in out.splitlines():
        parts = line.split("\x1f", 2)
        if len(parts) == 3:
            commits.append({"hash": parts[0], "message": parts[1], "date": parts[2]})
    return commits


def rollback(nixos_dir: str, commit_hash: str) -> tuple[bool, str]:
    """
    Restore managed Nix files from a previous commit without rewriting history.
    Uses git checkout <hash> -- <file> for each file that existed at that commit,
    then creates a new auto-commit to record the rollback.
    Returns (success, message).
    """
    import re as _re
    if not _re.fullmatch(r"[0-9a-f]{7,40}", commit_hash):
        return False, "Ungültiger Commit-Hash."
    if not is_git_repo(nixos_dir):
        return False, "Kein Git-Repository."

    _ensure_identity(nixos_dir)

    managed = ["configuration.nix", "flake.nix"]
    restored = []
    for fname in managed:
        rc, _ = _run(["checkout", commit_hash, "--", fname], cwd=nixos_dir)
        if rc == 0:
            restored.append(fname)

    if not restored:
        return False, "Keine Dateien für diesen Commit gefunden."

    short = commit_hash[:7]
    msg   = f"NiCo: Rollback zu {short} ({', '.join(restored)})"
    rc, out = _run(["commit", "-m", msg], cwd=nixos_dir)
    if rc != 0 and "nothing to commit" not in out:
        return False, f"Commit nach Rollback fehlgeschlagen: {out}"

    return True, f"Wiederhergestellt: {', '.join(restored)} → {short}"
