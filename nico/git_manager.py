"""
Git integration for NiCo – Layer 1: local repository.

Layer 1 (this file): init local repo, auto-commit after every write.
Layer 2 (future, Admin-Bereich): optional GitHub/remote push.

All git commands use subprocess with explicit argument lists – never shell=True
with variable content to prevent shell injection.
"""

import os
import subprocess
from datetime import datetime
from pathlib import Path

# SSH-Optionen für alle Remote-Operationen: kein interaktiver Prompt,
# neuer Host-Key wird automatisch akzeptiert, Verbindungs-Timeout 10s.
_SSH_ENV = {
    **os.environ,
    "GIT_TERMINAL_PROMPT": "0",
    "GIT_SSH_COMMAND": "ssh -o BatchMode=yes -o StrictHostKeyChecking=accept-new -o ConnectTimeout=10",
}


def _run(args: list[str], cwd: str, timeout: int = 10, remote: bool = False) -> tuple[int, str]:
    """Run a git command. Returns (returncode, combined stdout+stderr).
    Returns (127, 'git not found') if git is not installed.
    Pass remote=True for commands that involve network access."""
    try:
        result = subprocess.run(
            ["git"] + args,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=_SSH_ENV if remote else None,
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
    """Always set a local git identity using system user@hostname so commits show machine origin."""
    import os
    import socket
    user = os.environ.get("USER") or os.environ.get("LOGNAME") or "nico"
    host = socket.gethostname() or "localhost"
    _run(["config", "user.email", f"{user}@{host}"], cwd=nixos_dir)
    _run(["config", "user.name",  user],              cwd=nixos_dir)


def _get_primary_remote_name(nixos_dir: str) -> str:
    """Return the configured primary remote name, preferring the upstream remote, then origin."""
    rc, current_branch = _run(["rev-parse", "--abbrev-ref", "HEAD"], cwd=nixos_dir)
    if rc == 0 and current_branch.strip() and current_branch.strip() != "HEAD":
        branch = current_branch.strip()
        rc, branch_remote = _run(["config", "--get", f"branch.{branch}.remote"], cwd=nixos_dir)
        if rc == 0 and branch_remote.strip():
            return branch_remote.strip()

    rc, remotes = _run(["remote"], cwd=nixos_dir)
    remote_names = [line.strip() for line in remotes.splitlines() if line.strip()]
    if "origin" in remote_names:
        return "origin"
    return remote_names[0] if remote_names else ""


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


def git_pull(nixos_dir: str) -> tuple[bool, str]:
    """
    Run git pull on the current branch.
    Returns (success, output_message).
    """
    if not is_git_repo(nixos_dir):
        return False, "Kein Git-Repository."
    rc, out = _run(["pull"], cwd=nixos_dir, timeout=30, remote=True)
    if rc != 0:
        return False, out or "git pull fehlgeschlagen"
    return True, out


def git_fetch_remote(nixos_dir: str) -> tuple[bool, str]:
    """Fetch the configured remote without changing local files."""
    if not is_git_repo(nixos_dir):
        return False, "Kein Git-Repository."
    remote_name = _get_primary_remote_name(nixos_dir)
    if not remote_name:
        return False, "Kein Remote konfiguriert."
    rc, out = _run(["fetch", "--quiet", remote_name], cwd=nixos_dir, timeout=30, remote=True)
    if rc != 0:
        return False, out or "git fetch fehlgeschlagen"
    return True, out


def list_remote_branches(nixos_dir: str) -> tuple[bool, list[str], str]:
    """List fetched remote branches below the configured remote."""
    if not is_git_repo(nixos_dir):
        return False, [], "Kein Git-Repository."
    remote_name = _get_primary_remote_name(nixos_dir)
    if not remote_name:
        return False, [], "Kein Remote konfiguriert."
    rc, out = _run(["for-each-ref", "--format=%(refname:short)", f"refs/remotes/{remote_name}/*"], cwd=nixos_dir)
    if rc != 0:
        return False, [], out or "Remote-Branches konnten nicht gelesen werden."
    branches = []
    for line in out.splitlines():
        branch = line.strip()
        if (
            not branch
            or branch == f"{remote_name}/HEAD"
            or branch == remote_name
            or not branch.startswith(f"{remote_name}/")
        ):
            continue
        branches.append(branch)
    return True, branches, ""


def set_upstream_branch(nixos_dir: str, remote_branch: str) -> tuple[bool, str]:
    """Connect the current local branch to a <remote>/<branch> upstream."""
    if not is_git_repo(nixos_dir):
        return False, "Kein Git-Repository."
    rc, local_branch = _run(["rev-parse", "--abbrev-ref", "HEAD"], cwd=nixos_dir)
    if rc != 0 or not local_branch or local_branch.strip() == "HEAD":
        return False, "Lokaler Branch konnte nicht ermittelt werden."
    local_branch = local_branch.strip()
    remote_branch = remote_branch.strip()
    remote_name = _get_primary_remote_name(nixos_dir)
    if not remote_name:
        return False, "Kein Remote konfiguriert."
    if not remote_branch.startswith(f"{remote_name}/"):
        return False, "Ungültiger Remote-Branch."
    rc, out = _run(["branch", "--set-upstream-to", remote_branch, local_branch], cwd=nixos_dir)
    if rc != 0:
        return False, out or "Remote-Branch konnte nicht zugeordnet werden."
    return True, ""


def classify_push_error(output: str) -> str:
    """Map raw git error output to a short error code."""
    o = output.lower()
    if 'permission denied (publickey)' in o or 'could not read from remote repository' in o:
        return 'NO_KEY'
    if ('permission to' in o and 'denied' in o) or 'access denied' in o:
        return 'NO_WRITE'
    if 'repository not found' in o or 'does not exist' in o:
        return 'NOT_FOUND'
    if 'could not resolve' in o or 'connection refused' in o or 'timed out' in o or 'network is unreachable' in o:
        return 'NO_NETWORK'
    if 'rejected' in o and ('non-fast-forward' in o or 'fetch first' in o):
        return 'NOT_FAST_FORWARD'
    if 'authentication failed' in o or 'invalid username or password' in o or 'bad credentials' in o:
        return 'AUTH_FAILED'
    return 'UNKNOWN'


def git_push(nixos_dir: str) -> tuple[bool, str, str]:
    """Run git push. Returns (success, raw_output, error_code)."""
    if not is_git_repo(nixos_dir):
        return False, "Kein Git-Repository.", "UNKNOWN"
    rc, out = _run(["push"], cwd=nixos_dir, timeout=30, remote=True)
    if rc != 0:
        return False, out or "git push fehlgeschlagen", classify_push_error(out)
    return True, out, ""


def check_write_access(nixos_dir: str) -> tuple[bool, str, str]:
    """Test push write access via dry run. Returns (ok, error_code, raw_output)."""
    if not is_git_repo(nixos_dir):
        return False, 'UNKNOWN', 'Kein Git-Repository.'
    rc, out = _run(["push", "--dry-run"], cwd=nixos_dir, timeout=30, remote=True)
    if rc != 0:
        return False, classify_push_error(out), out
    return True, '', out


def set_remote(nixos_dir: str, url: str) -> tuple[bool, str]:
    """Add or update origin remote. Returns (ok, raw_output)."""
    if not is_git_repo(nixos_dir):
        return False, 'Kein Git-Repository.'
    remote_name = _get_primary_remote_name(nixos_dir) or "origin"
    rc, _ = _run(["remote", "get-url", remote_name], cwd=nixos_dir)
    if rc == 0:
        rc, out = _run(["remote", "set-url", remote_name, url], cwd=nixos_dir)
    else:
        rc, out = _run(["remote", "add", remote_name, url], cwd=nixos_dir)
    if rc != 0:
        return False, out or 'Fehler beim Setzen des Remotes.'
    return True, ''


def git_reset_hard(nixos_dir: str) -> tuple[bool, str]:
    """Reset to the configured upstream branch and remove untracked files."""
    if not is_git_repo(nixos_dir):
        return False, "Kein Git-Repository."
    rc, upstream = _run(["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"], cwd=nixos_dir)
    if rc != 0 or not upstream:
        return False, "Kein Upstream-Branch konfiguriert."
    rc, out = _run(["reset", "--hard", upstream.strip()], cwd=nixos_dir)
    if rc != 0:
        return False, out or "git reset fehlgeschlagen"
    _run(["clean", "-fd"], cwd=nixos_dir)
    return True, out


def git_commit_push(nixos_dir: str, label: str = "") -> tuple[bool, str]:
    """Commit all local changes and push to remote."""
    if not is_git_repo(nixos_dir):
        return False, "Kein Git-Repository."
    _ensure_identity(nixos_dir)
    ok_commit, msg_commit = auto_commit(nixos_dir, label=label or "NiCo: Lokale Änderungen")
    if not ok_commit and "nothing to commit" not in msg_commit:
        return False, msg_commit
    ok_push, msg_push, _ = git_push(nixos_dir)
    if not ok_push:
        return False, msg_push
    return True, ""


def _get_commit_info(nixos_dir: str, ref: str) -> dict:
    """Return {hash, message, date, author} for a given ref, or empty dict on failure."""
    fmt = "%H\x1f%s\x1f%ai\x1f%ae"
    rc, out = _run(["log", ref, "-1", f"--format={fmt}"], cwd=nixos_dir)
    if rc != 0 or not out.strip():
        return {}
    parts = out.strip().split("\x1f", 3)
    if len(parts) != 4:
        return {}
    return {"hash": parts[0][:7], "message": parts[1], "date": parts[2][:16], "author": parts[3]}


def _get_commit_list(nixos_dir: str, range_spec: str, limit: int = 5) -> list:
    """Return up to `limit` commits for a range as list of {hash, message, date, author}."""
    fmt = "%H\x1f%s\x1f%ai\x1f%ae"
    rc, out = _run(["log", range_spec, f"-{limit}", f"--format={fmt}"], cwd=nixos_dir)
    if rc != 0 or not out.strip():
        return []
    result = []
    for line in out.strip().splitlines():
        parts = line.split("\x1f", 3)
        if len(parts) == 4:
            result.append({"hash": parts[0][:7], "message": parts[1], "date": parts[2][:16], "author": parts[3]})
    return result


def check_remote_status(nixos_dir: str) -> dict:
    """
    Check whether the local repo is behind its remote.
    Returns {
        "has_git": bool,
        "has_remote": bool,
        "remote_url": str,
        "behind": int,   # commits local is behind remote (0 = up to date)
        "error": str     # non-empty if fetch failed
    }
    """
    if not is_git_repo(nixos_dir):
        return {"has_git": False, "has_remote": False, "remote_url": "", "behind": 0, "error": ""}

    remote_name = _get_primary_remote_name(nixos_dir)
    if not remote_name:
        return {"has_git": True, "has_remote": False, "remote_url": "", "behind": 0, "error": ""}
    rc, remote_url = _run(["remote", "get-url", remote_name], cwd=nixos_dir)
    if rc != 0 or not remote_url:
        return {"has_git": True, "has_remote": False, "remote_url": "", "behind": 0, "error": ""}

    rc, fetch_out = _run(["fetch", "--quiet", remote_name], cwd=nixos_dir, timeout=15, remote=True)
    if rc != 0:
        return {"has_git": True, "has_remote": True, "remote_url": remote_url, "behind": 0,
                "error": f"fetch fehlgeschlagen: {fetch_out}"}

    rc, count_out = _run(["rev-list", "HEAD..@{u}", "--count"], cwd=nixos_dir)
    if rc != 0:
        # No upstream tracking branch configured
        return {"has_git": True, "has_remote": True, "remote_url": remote_url, "behind": 0, "error": ""}

    try:
        behind = int(count_out.strip())
    except ValueError:
        behind = 0

    return {"has_git": True, "has_remote": True, "remote_url": remote_url, "behind": behind, "error": ""}


def check_start_guard(nixos_dir: str) -> dict:
    """
    Inspect the local repository before NiCo loads and normalizes files.
    Returns a state machine for the startup guard dialog.
    States:
      not_git | no_remote | remote_no_upstream | clean_up_to_date | dirty | behind | ahead | diverged | error
    """
    import shutil

    if shutil.which("git") is None:
        return {
            "state": "error",
            "git_installed": False,
            "has_git": False,
            "has_remote": False,
            "dirty": False,
            "ahead": 0,
            "behind": 0,
            "remote_url": "",
            "detail": "git not found",
        }

    if not is_git_repo(nixos_dir):
        return {
            "state": "not_git",
            "git_installed": True,
            "has_git": False,
            "has_remote": False,
            "dirty": False,
            "ahead": 0,
            "behind": 0,
            "remote_url": "",
            "detail": "",
        }

    remote_name = _get_primary_remote_name(nixos_dir)
    if not remote_name:
        return {
            "state": "no_remote",
            "git_installed": True,
            "has_git": True,
            "has_remote": False,
            "dirty": False,
            "ahead": 0,
            "behind": 0,
            "remote_url": "",
            "detail": "",
        }
    rc, remote_url = _run(["remote", "get-url", remote_name], cwd=nixos_dir)
    if rc != 0 or not remote_url:
        return {
            "state": "no_remote",
            "git_installed": True,
            "has_git": True,
            "has_remote": False,
            "dirty": False,
            "ahead": 0,
            "behind": 0,
            "remote_url": "",
            "detail": "",
        }

    rc, status_out = _run(["status", "--porcelain"], cwd=nixos_dir)
    dirty = bool(status_out.strip()) if rc == 0 else False

    rc, upstream = _run(["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"], cwd=nixos_dir)
    if rc != 0 or not upstream:
        _, local_branch = _run(["rev-parse", "--abbrev-ref", "HEAD"], cwd=nixos_dir)
        return {
            "state": "remote_no_upstream",
            "git_installed": True,
            "has_git": True,
            "has_remote": True,
            "dirty": dirty,
            "ahead": 0,
            "behind": 0,
            "remote_url": remote_url,
            "detail": "",
            "local_branch": local_branch.strip() if local_branch else "",
        }

    rc, fetch_out = _run(["fetch", "--quiet", remote_name], cwd=nixos_dir, timeout=15, remote=True)
    if rc != 0:
        return {
            "state": "error",
            "git_installed": True,
            "has_git": True,
            "has_remote": True,
            "dirty": dirty,
            "ahead": 0,
            "behind": 0,
            "remote_url": remote_url,
            "detail": fetch_out,
        }

    rc, count_out = _run(["rev-list", "--left-right", "--count", "HEAD...@{u}"], cwd=nixos_dir)
    if rc != 0:
        return {
            "state": "error",
            "git_installed": True,
            "has_git": True,
            "has_remote": True,
            "dirty": dirty,
            "ahead": 0,
            "behind": 0,
            "remote_url": remote_url,
            "detail": count_out,
        }

    parts = count_out.split()
    try:
        ahead = int(parts[0]) if len(parts) >= 1 else 0
        behind = int(parts[1]) if len(parts) >= 2 else 0
    except ValueError:
        ahead = 0
        behind = 0

    if ahead > 0 and behind > 0:
        state = "diverged"
    elif behind > 0:
        state = "behind"
    elif dirty:
        state = "dirty"
    elif ahead > 0:
        state = "ahead"
    else:
        state = "clean_up_to_date"

    _, local_branch  = _run(["rev-parse", "--abbrev-ref", "HEAD"], cwd=nixos_dir)
    _, remote_branch = _run(["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"], cwd=nixos_dir)

    dirty_files = []
    if dirty:
        # Use stdout directly — _run strips the full output which eats the leading
        # space of the first porcelain line (e.g. " M hosts/...") causing path[0] loss.
        try:
            _proc = subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=nixos_dir, capture_output=True, text=True, timeout=10,
            )
            for line in _proc.stdout.splitlines()[:15]:
                if len(line) >= 4:
                    dirty_files.append({"status": line[:2].strip(), "path": line[3:]})
        except Exception:
            pass

    ahead_commits  = _get_commit_list(nixos_dir, "@{u}..HEAD")  if ahead  > 0 else []
    behind_commits = _get_commit_list(nixos_dir, "HEAD..@{u}")  if behind > 0 else []
    last_local     = _get_commit_info(nixos_dir, "HEAD")
    last_remote    = _get_commit_info(nixos_dir, "@{u}")

    return {
        "state": state,
        "git_installed": True,
        "has_git": True,
        "has_remote": True,
        "dirty": dirty,
        "ahead": ahead,
        "behind": behind,
        "remote_url": remote_url,
        "detail": "",
        "local_branch":   local_branch.strip(),
        "remote_branch":  remote_branch.strip(),
        "dirty_files":    dirty_files,
        "ahead_commits":  ahead_commits,
        "behind_commits": behind_commits,
        "last_local":     last_local,
        "last_remote":    last_remote,
    }


def git_push_force(nixos_dir: str) -> tuple[bool, str]:
    """Run git push --force on the current branch."""
    if not is_git_repo(nixos_dir):
        return False, "Kein Git-Repository."
    rc, out = _run(["push", "--force"], cwd=nixos_dir, timeout=30, remote=True)
    if rc != 0:
        return False, out or "git push --force fehlgeschlagen"
    return True, out


def git_commit_push_force(nixos_dir: str) -> tuple[bool, str]:
    """Commit all local changes (if any) and force-push to remote."""
    if not is_git_repo(nixos_dir):
        return False, "Kein Git-Repository."
    _ensure_identity(nixos_dir)
    ok_commit, msg_commit = auto_commit(nixos_dir, label="NiCo: Lokale Änderungen")
    if not ok_commit and "nothing to commit" not in msg_commit:
        return False, msg_commit
    ok_push, msg_push = git_push_force(nixos_dir)
    if not ok_push:
        return False, msg_push
    return True, ""


def check_close_state(nixos_dir: str) -> dict:
    """
    Fast check at close time (no network fetch).
    Returns {has_remote, needs_push, ahead, dirty}
    """
    if not is_git_repo(nixos_dir):
        return {"has_remote": False, "needs_push": False, "ahead": 0, "dirty": False}

    remote_name = _get_primary_remote_name(nixos_dir)
    if not remote_name:
        return {"has_remote": False, "needs_push": False, "ahead": 0, "dirty": False}
    rc, remote_url = _run(["remote", "get-url", remote_name], cwd=nixos_dir)
    if rc != 0 or not remote_url:
        return {"has_remote": False, "needs_push": False, "ahead": 0, "dirty": False}

    # -uno: ignore untracked files (e.g. result symlink, .stfolder) – only tracked changes matter here
    rc, status_out = _run(["status", "--porcelain", "-uno"], cwd=nixos_dir)
    dirty = bool(status_out.strip()) if rc == 0 else False

    rc, count_out = _run(["rev-list", "@{u}..HEAD", "--count"], cwd=nixos_dir)
    try:
        ahead = int(count_out.strip()) if rc == 0 else 0
    except ValueError:
        ahead = 0

    return {
        "has_remote": True,
        "needs_push": dirty or ahead > 0,
        "ahead": ahead,
        "dirty": dirty,
    }


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
