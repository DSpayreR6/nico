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

from . import atomic

# SSH-Optionen für alle Remote-Operationen: kein interaktiver Prompt,
# neuer Host-Key wird automatisch akzeptiert, Verbindungs-Timeout 10s.
_SSH_ENV = {
    **os.environ,
    "GIT_TERMINAL_PROMPT": "0",
    "GIT_SSH_COMMAND": "ssh -o BatchMode=yes -o StrictHostKeyChecking=accept-new -o ConnectTimeout=10",
}

# Standard entries for .gitignore – NiCo-generated files that must not be committed.
GITIGNORE_ENTRIES = [
    "nixos-rebuild.log",
    "nixos-config-*.zip",
    "vergleich.zip",
    "result",
    "result-*",
    "*.bak",
    ".stfolder",
]

# Root-level files that belong to the config besides *.nix. Everything else
# is a "foreign file": the foreign-file guard asks before committing it.
CONFIG_FILE_NAMES = frozenset({"flake.lock", "config.json", ".gitignore"})

# Messages that double as error codes. These used to be 20 copies of a German
# sentence returned as plain text – it never passed through t(), so an English
# user read German. The frontend sends every message through tErr(), which
# translates anything starting with ERR_ and shows the rest verbatim.
ERR_NO_GIT_REPO = "ERR_NO_GIT_REPO"
ERR_NO_REMOTE   = "ERR_NO_REMOTE"


def is_config_file(relpath: str) -> bool:
    """True if relpath is part of the NixOS config (never treated as foreign)."""
    return Path(relpath).suffix == ".nix" or relpath in CONFIG_FILE_NAMES


def foreign_guard_enabled(nixos_dir: str) -> bool:
    """Whether the foreign-file guard (ask before committing non-config files) is on."""
    from . import config_manager
    return bool(config_manager.load_config_settings(nixos_dir).get("git_foreign_guard", True))


# Every path git prints is parsed here, so it has to arrive as it is on disk.
# With core.quotePath on - the default - git escapes anything outside ASCII and
# wraps it in quotes: "drucker-b\303\274ro.nix". Read literally, such a name
# matches no file, and three things went wrong at once: a new config file with
# an umlaut was never staged (auto_commit then reported success and an empty
# skip list), the 50 MB guard did not see an oversized file and let its blob
# into the commit, and a rollback left exactly those files on the newer state
# while everything else went back - the mixed working tree this module takes
# care to avoid everywhere else.
_PATHS_VERBATIM = ["-c", "core.quotePath=false"]


def _run(args: list[str], cwd: str, timeout: int = 10, remote: bool = False,
         input: str | None = None) -> tuple[int, str]:
    """Run a git command. Returns (returncode, combined stdout+stderr).
    Returns (127, 'git not found') if git is not installed.
    Pass remote=True for commands that involve network access."""
    try:
        result = subprocess.run(
            ["git"] + _PATHS_VERBATIM + args,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=_SSH_ENV if remote else None,
            input=input,
        )
        return result.returncode, (result.stdout + result.stderr).strip()
    except FileNotFoundError:
        return 127, "git not found"
    except subprocess.TimeoutExpired:
        # Marked so callers can tell a timeout from a real git error: the two
        # need different advice ("wait / check the upload" vs. "fix the repo").
        return 1, f"__TIMEOUT__ git {args[0]} nach {timeout}s abgebrochen"
    except Exception as e:
        return 1, str(e)


def _run_paths(args: list[str], cwd: str) -> tuple[int, list[str]]:
    """Run a git command that prints a path list, NUL-separated.

    `-z` is the only form git never escapes: with it a name keeps its bytes
    whatever it contains, so nothing has to be unquoted afterwards. The
    alternative - reading the normal output and undoing the escaping - has to
    get tab, newline, backslash and quote right to be correct, and the cost of
    getting it wrong is silent: the path matches no file, git stages nothing,
    and the caller reports success for a file it never touched.

    Returns (returncode, paths). An empty list on error - callers bail on rc.
    """
    rc, out = _run(args + ["-z"], cwd=cwd)
    if rc != 0:
        return rc, []
    return rc, [p for p in out.split("\0") if p]


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


def create_gitignore(nixos_dir: str) -> tuple[bool, str]:
    """Add missing GITIGNORE_ENTRIES to .gitignore (creates file if absent)."""
    path = Path(nixos_dir) / ".gitignore"
    existing_lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    existing = {l.strip() for l in existing_lines if l.strip() and not l.startswith("#")}
    missing = [e for e in GITIGNORE_ENTRIES if e not in existing]
    if not missing:
        return True, ""
    content = "\n".join(existing_lines)
    if content and not content.endswith("\n"):
        content += "\n"
    if not content:
        content = "# NiCo\n"
    content += "\n".join(missing) + "\n"
    try:
        atomic.write_text(path, content)
        return True, ""
    except Exception as exc:
        return False, str(exc)


def read_gitignore(nixos_dir: str) -> tuple[bool, str]:
    """Return (exists, content) for .gitignore."""
    path = Path(nixos_dir) / ".gitignore"
    if not path.exists():
        return False, ""
    return True, path.read_text(encoding="utf-8")


def write_gitignore(nixos_dir: str, content: str) -> tuple[bool, str]:
    """Overwrite .gitignore with the given content."""
    try:
        atomic.write_text(Path(nixos_dir) / ".gitignore", content)
        return True, ""
    except Exception as exc:
        return False, str(exc)


def list_tracked_files(nixos_dir: str) -> list[str]:
    """Return all files currently tracked by git."""
    if not is_git_repo(nixos_dir):
        return []
    rc, files = _run_paths(["ls-files"], cwd=nixos_dir)
    return files if rc == 0 else []


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
    create_gitignore(nixos_dir)  # ensure .gitignore exists before first commit

    _run(["add", "-A"], cwd=nixos_dir)
    rc, out = _run(["commit", "-m", "NiCo: Repository initialisiert"], cwd=nixos_dir)
    if rc != 0 and "nothing to commit" not in out:
        return False, f"Initialer Commit fehlgeschlagen: {out}"

    return True, "Git-Repository wurde angelegt und initialer Commit erstellt."


def list_untracked_foreign(nixos_dir: str) -> list[str]:
    """Untracked, not-ignored files that are not part of the config
    (the candidates for the foreign-file dialog)."""
    if not is_git_repo(nixos_dir):
        return []
    rc, entries = _run_paths(["status", "--porcelain", "-uall"], cwd=nixos_dir)
    if rc != 0:
        return []
    files = []
    for entry in entries:
        if not entry.startswith("??"):
            continue
        # This list is the allowlist for "add" and "gitignore" below, so an
        # escaped name would be offered, accepted and then quietly match no
        # file at all.
        path = entry[2:].lstrip()
        if path and not is_config_file(path):
            files.append(path)
    return sorted(files)


def list_tracked_foreign(nixos_dir: str, keep: list[str] | None = None) -> list[str]:
    """Tracked files that are not part of the config, minus the user's keep list."""
    keep_set = set(keep or [])
    return sorted(f for f in list_tracked_files(nixos_dir)
                  if not is_config_file(f) and f not in keep_set)


# Nothing in a NixOS config legitimately reaches this size, while git history is
# effectively permanent: a committed blob can only be removed by rewriting every
# commit that references it. GitHub rejects anything above 100 MB outright, so a
# single oversized file can block every further push (incident 2026-09-12: a
# 2.1 GB nixos-rebuild.log). Staging therefore refuses such files everywhere.
MAX_STAGE_BYTES = 50 * 1024 * 1024

# Hosters refuse single files above this size (GitHub: 100 MB, hard limit in the
# pre-receive hook). Checked before pushing so the user is not left waiting for
# an upload that is certain to be rejected.
REMOTE_BLOB_LIMIT = 100 * 1024 * 1024

# Push and pull can move a lot of data over a slow uplink. The old 30 s aborted
# such transfers midway and the timeout was then reported as a network problem,
# which sent the user looking in the wrong place (incident 2026-09-12).
REMOTE_TRANSFER_TIMEOUT = 600


def oversized_files(nixos_dir: str, paths: list[str]) -> list[str]:
    """Of the given repo-relative paths, those above MAX_STAGE_BYTES."""
    out = []
    for rel in paths:
        p = Path(nixos_dir) / rel
        try:
            if p.is_file() and not p.is_symlink() and p.stat().st_size > MAX_STAGE_BYTES:
                out.append(rel)
        except OSError:
            continue
    return out


def _unstage_oversized(nixos_dir: str) -> list[str]:
    """Drop files above the size limit from the index again.

    Runs after staging so it catches every path, no matter which of the staging
    branches added it. A tracked file keeps its previous committed version, a
    new one simply stays untracked. Returns what was skipped.
    """
    rc, staged = _run_paths(["diff", "--cached", "--name-only", "--diff-filter=ACMR"],
                            cwd=nixos_dir)
    if rc != 0:
        return []
    skipped = oversized_files(nixos_dir, staged)
    for rel in skipped:
        # Already committed once: restore the index entry to the last committed
        # version, so the file stays tracked and only the new content is left
        # out. Otherwise (new file, or a repo without any commit yet) drop the
        # entry entirely – it goes back to being untracked.
        rc, _ = _run(["cat-file", "-e", f"HEAD:{rel}"], cwd=nixos_dir)
        if rc == 0:
            _run(["restore", "--staged", "--", rel], cwd=nixos_dir)
        else:
            _run(["rm", "--cached", "--force", "--quiet", "--", rel], cwd=nixos_dir)
    return skipped


def _stage_for_commit(nixos_dir: str) -> list[str]:
    """Stage changes for a NiCo commit.

    With the foreign-file guard on, untracked non-config files are left alone –
    the user decides about them in the foreign-file dialog. Everything tracked
    plus new config files is staged. Guard off = plain `git add -A`.

    Returns the files that were left out for being oversized."""
    if not foreign_guard_enabled(nixos_dir):
        _run(["add", "-A"], cwd=nixos_dir)
        return _unstage_oversized(nixos_dir)
    _run(["add", "-u"], cwd=nixos_dir)
    # -z entries are "XY <path>" without a line break; a rename adds a second
    # chunk holding the old path, which cannot start with "??" and is skipped.
    rc, entries = _run_paths(["status", "--porcelain", "-uall"], cwd=nixos_dir)
    if rc != 0:
        return _unstage_oversized(nixos_dir)
    for entry in entries:
        if not entry.startswith("??"):
            continue
        path = entry[2:].lstrip()
        if path and is_config_file(path):
            _run(["add", "--", path], cwd=nixos_dir)
    return _unstage_oversized(nixos_dir)


def stage_all(nixos_dir: str) -> tuple[bool, str]:
    """Stage current files in nixos_dir for rebuild flows.
    Respects the foreign-file guard (see _stage_for_commit)."""
    if not is_git_repo(nixos_dir):
        return False, ERR_NO_GIT_REPO
    _stage_for_commit(nixos_dir)
    return True, ""


def auto_commit(nixos_dir: str, label: str = "") -> tuple[bool, str, list[str]]:
    """
    Stage all changes and create a commit.
    label: optional user-provided name shown in the git log (the "Sicherungspunkt" label).
    Returns (success, message, skipped) – skipped lists files left out for
    being oversized, so callers can tell the user instead of silently
    committing less than they asked for.
    """
    if not is_git_repo(nixos_dir):
        return False, ERR_NO_GIT_REPO, []

    _ensure_identity(nixos_dir)
    skipped = _stage_for_commit(nixos_dir)

    # Check if there's actually something to commit. -uno: undecided foreign
    # files are untracked by design and must not trigger (or fail) a commit.
    rc, status = _run(["status", "--porcelain", "-uno"], cwd=nixos_dir)
    if not status:
        return True, "", skipped  # Nothing changed – silent success

    ts = datetime.now().strftime("%Y-%m-%d %H:%M")
    if label and label.strip():
        msg = f"{label.strip()} ({ts})"
    else:
        msg = f"NiCo: Konfiguration geschrieben ({ts})"

    rc, out = _run(["commit", "-m", msg], cwd=nixos_dir)
    if rc != 0:
        return False, f"Commit fehlgeschlagen: {out}", skipped

    return True, msg, skipped


def _append_gitignore_entries(nixos_dir: str, entries: list[str]) -> None:
    """Append entries to .gitignore (created if missing), skipping duplicates."""
    path = Path(nixos_dir) / ".gitignore"
    existing_lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    existing = {l.strip() for l in existing_lines if l.strip() and not l.startswith("#")}
    add = [e for e in entries if e not in existing]
    if not add:
        return
    content = "\n".join(existing_lines)
    if content and not content.endswith("\n"):
        content += "\n"
    content += "\n".join(add) + "\n"
    atomic.write_text(path, content)


def include_foreign_files(nixos_dir: str, files: list[str]) -> tuple[bool, str]:
    """Stage the given untracked foreign files so the next commit includes them."""
    if not is_git_repo(nixos_dir):
        return False, ERR_NO_GIT_REPO
    # Refuse oversized files here rather than dropping them silently in
    # _stage_for_commit: this is the one place where the user actively asked
    # for the file to go into git, so they get a reason instead of nothing.
    too_big = oversized_files(nixos_dir, files)
    if too_big:
        limit_mb = MAX_STAGE_BYTES // (1024 * 1024)
        return False, (
            f"Zu groß für Git (über {limit_mb} MB): {', '.join(too_big)}. "
            "Solche Dateien blockieren dauerhaft jeden Push und lassen sich "
            "später nur durch Umschreiben der History wieder entfernen."
        )
    allowed = set(list_untracked_foreign(nixos_dir))
    for f in files:
        if f in allowed:
            _run(["add", "--", f], cwd=nixos_dir)
    return True, ""


def exclude_foreign_files(nixos_dir: str, files: list[str]) -> tuple[bool, str]:
    """Put the given untracked foreign files on .gitignore (created if missing)."""
    if not is_git_repo(nixos_dir):
        return False, ERR_NO_GIT_REPO
    allowed = set(list_untracked_foreign(nixos_dir))
    chosen  = [f for f in files if f in allowed]
    if chosen:
        _append_gitignore_entries(nixos_dir, chosen)
    return True, ""


def untrack_foreign_files(nixos_dir: str, files: list[str]) -> tuple[bool, str]:
    """Remove tracked foreign files from git (files stay on disk), put them on
    .gitignore and commit. Note: the files remain in the git history."""
    if not is_git_repo(nixos_dir):
        return False, ERR_NO_GIT_REPO
    allowed = set(list_tracked_foreign(nixos_dir))
    chosen  = [f for f in files if f in allowed]
    if not chosen:
        return True, ""
    _ensure_identity(nixos_dir)
    rc, out = _run(["rm", "--cached", "--"] + chosen, cwd=nixos_dir)
    if rc != 0:
        return False, out or "git rm --cached fehlgeschlagen"
    _append_gitignore_entries(nixos_dir, chosen)
    _run(["add", "--", ".gitignore"], cwd=nixos_dir)
    ts = datetime.now().strftime("%Y-%m-%d %H:%M")
    rc, out = _run(["commit", "-m", f"NiCo: Fremddateien aus Git entfernt ({ts})"], cwd=nixos_dir)
    if rc != 0:
        return False, f"Commit fehlgeschlagen: {out}"
    return True, ""


def _remote_label(nixos_dir: str) -> tuple[str, str]:
    """(upstream branch, remote url) for display, empty strings if unknown."""
    rc, upstream = _run(["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"], cwd=nixos_dir)
    branch = upstream.strip() if rc == 0 else ""
    name   = _get_primary_remote_name(nixos_dir)
    rc, url = _run(["remote", "get-url", name], cwd=nixos_dir) if name else (1, "")
    return branch, (url.strip() if rc == 0 else "")


def push_info(nixos_dir: str) -> dict:
    """What a push would transfer right now – must be called before pushing.

    Afterwards the commits are no longer "ahead" and there would be nothing
    left to report, which is why a push used to say nothing but "done".
    """
    branch, url = _remote_label(nixos_dir)
    commits = _get_commit_list(nixos_dir, "@{u}..HEAD", limit=20) if branch else []
    return {"remote": branch, "url": url, "commits": commits}


def pull_info(nixos_dir: str, before: str) -> dict:
    """What a pull brought in, compared to the commit we were on before."""
    branch, url = _remote_label(nixos_dir)
    rc, head = _run(["rev-parse", "HEAD"], cwd=nixos_dir)
    after = head.strip() if rc == 0 else ""
    if not before or not after or before == after:
        return {"remote": branch, "url": url, "commits": [], "files": []}
    commits = _get_commit_list(nixos_dir, f"{before}..{after}", limit=20)
    rc, out = _run(["diff", "--name-only", before, after], cwd=nixos_dir)
    files = [f.strip() for f in out.splitlines() if f.strip()] if rc == 0 else []
    return {"remote": branch, "url": url, "commits": commits, "files": files}


def git_pull(nixos_dir: str) -> tuple[bool, str, dict]:
    """
    Run git pull on the current branch.
    Returns (success, output_message, info) – info describes what came in.
    """
    if not is_git_repo(nixos_dir):
        return False, ERR_NO_GIT_REPO, {}
    rc, before = _run(["rev-parse", "HEAD"], cwd=nixos_dir)
    before = before.strip() if rc == 0 else ""
    rc, out = _run(["pull"], cwd=nixos_dir, timeout=REMOTE_TRANSFER_TIMEOUT, remote=True)
    if rc != 0:
        return False, out or "git pull fehlgeschlagen", {}
    return True, out, pull_info(nixos_dir, before)


def git_fetch_remote(nixos_dir: str) -> tuple[bool, str]:
    """Fetch the configured remote without changing local files."""
    if not is_git_repo(nixos_dir):
        return False, ERR_NO_GIT_REPO
    remote_name = _get_primary_remote_name(nixos_dir)
    if not remote_name:
        return False, ERR_NO_REMOTE
    rc, out = _run(["fetch", "--quiet", remote_name], cwd=nixos_dir, timeout=30, remote=True)
    if rc != 0:
        return False, out or "git fetch fehlgeschlagen"
    return True, out


def list_remote_branches(nixos_dir: str) -> tuple[bool, list[str], str]:
    """List fetched remote branches below the configured remote."""
    if not is_git_repo(nixos_dir):
        return False, [], ERR_NO_GIT_REPO
    remote_name = _get_primary_remote_name(nixos_dir)
    if not remote_name:
        return False, [], ERR_NO_REMOTE
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
        return False, ERR_NO_GIT_REPO
    rc, local_branch = _run(["rev-parse", "--abbrev-ref", "HEAD"], cwd=nixos_dir)
    if rc != 0 or not local_branch or local_branch.strip() == "HEAD":
        return False, "Lokaler Branch konnte nicht ermittelt werden."
    local_branch = local_branch.strip()
    remote_branch = remote_branch.strip()
    remote_name = _get_primary_remote_name(nixos_dir)
    if not remote_name:
        return False, ERR_NO_REMOTE
    if not remote_branch.startswith(f"{remote_name}/"):
        return False, "Ungültiger Remote-Branch."
    rc, out = _run(["branch", "--set-upstream-to", remote_branch, local_branch], cwd=nixos_dir)
    if rc != 0:
        return False, out or "Remote-Branch konnte nicht zugeordnet werden."
    return True, ""


def classify_push_error(output: str) -> str:
    """Map raw git error output to a short error code."""
    o = output.lower()
    if '__timeout__' in o:
        return 'TIMEOUT'
    # GitHub's 100 MB limit, refused by the pre-receive hook. Worth its own code:
    # the fix is to get the file out of the history, not to retry the push.
    if 'gh001' in o or "exceeds github's file size limit" in o or 'large files detected' in o:
        return 'FILE_TOO_LARGE'
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


def oversized_blobs_to_push(nixos_dir: str) -> list[tuple[str, int]]:
    """Files above REMOTE_BLOB_LIMIT in commits the remote does not have yet.

    Looks at the objects themselves, not the working tree: the file causing the
    trouble is usually long deleted while the blob lives on in the history.
    Returns [(path, size_in_bytes)]; empty when the check could not run.
    """
    rc, upstream = _run(["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"], cwd=nixos_dir)
    rev_range = f"{upstream.strip()}..HEAD" if rc == 0 and upstream.strip() else "HEAD"
    rc, out = _run(["rev-list", "--objects", rev_range], cwd=nixos_dir, timeout=20)
    if rc != 0 or not out.strip():
        return []
    entries = {}
    for line in out.splitlines():
        sha, _, path = line.partition(" ")
        if path.strip():
            entries[sha] = path.strip()
    if not entries:
        return []
    rc, sizes = _run(["cat-file", "--batch-check=%(objecttype) %(objectsize) %(objectname)"],
                     cwd=nixos_dir, timeout=20, input="\n".join(entries) + "\n")
    if rc != 0:
        return []
    found = {}
    for line in sizes.splitlines():
        parts = line.split()
        if len(parts) != 3 or parts[0] != "blob":
            continue
        size = int(parts[1])
        if size > REMOTE_BLOB_LIMIT:
            path = entries.get(parts[2], parts[2])
            found[path] = max(found.get(path, 0), size)
    return sorted(found.items(), key=lambda kv: -kv[1])


def _published_base(nixos_dir: str) -> str:
    """Newest commit the remote already has (empty if nothing was ever pushed).

    Everything after it is local-only and may be rewritten; everything up to
    and including it is published and must not be touched.
    """
    rc, upstream = _run(["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"], cwd=nixos_dir)
    if rc != 0 or not upstream.strip():
        return ""
    rc, base = _run(["merge-base", "HEAD", upstream.strip()], cwd=nixos_dir)
    return base.strip() if rc == 0 else ""


def _blobs_in_commit(nixos_dir: str, commit: str, limit: int) -> list[tuple[str, int]]:
    """Files above limit in that commit's tree, as [(path, size)]."""
    rc, out = _run(["ls-tree", "-r", "-l", commit], cwd=nixos_dir, timeout=20)
    if rc != 0:
        return []
    found = []
    for line in out.splitlines():
        # <mode> blob <sha> <size>\t<path>
        meta, _, path = line.partition("\t")
        parts = meta.split()
        if len(parts) < 4 or parts[1] != "blob" or not parts[3].isdigit():
            continue
        if int(parts[3]) > limit:
            found.append((path.strip(), int(parts[3])))
    return found


def large_file_report(nixos_dir: str, limit: int | None = None) -> dict:
    """Where oversized files sit, which decides what can safely be done.

    worktree  – on disk but in no commit: deleting is enough
    local     – in commits the remote does not have: rewritable without harm
    published – already on the remote: only a history rewrite would help, and
                that breaks every other clone, so NiCo does not offer it
    """
    limit = MAX_STAGE_BYTES if limit is None else limit
    empty = {"worktree": [], "local": [], "published": [], "base": "", "commits": 0}
    if not is_git_repo(nixos_dir):
        return empty

    root = Path(nixos_dir)
    tracked = set(list_tracked_files(nixos_dir))
    worktree = []
    for path in root.rglob("*"):
        if ".git" in path.parts or path.is_symlink() or not path.is_file():
            continue
        try:
            size = path.stat().st_size
        except OSError:
            continue
        if size > limit:
            rel = path.relative_to(root).as_posix()
            if rel not in tracked:
                worktree.append((rel, size))

    base = _published_base(nixos_dir)
    rc, out = _run(["rev-list", "--reverse", f"{base}..HEAD" if base else "HEAD"], cwd=nixos_dir, timeout=20)
    local_commits = [c.strip() for c in out.splitlines() if c.strip()] if rc == 0 else []

    local, seen = [], set()
    for commit in local_commits:
        for path, size in _blobs_in_commit(nixos_dir, commit, limit):
            if path not in seen:
                seen.add(path)
                local.append((path, size))

    published = []
    if base:
        for path, size in _blobs_in_commit(nixos_dir, base, limit):
            published.append((path, size))

    return {
        "worktree":  sorted(worktree, key=lambda kv: -kv[1]),
        "local":     sorted(local, key=lambda kv: -kv[1]),
        "published": sorted(published, key=lambda kv: -kv[1]),
        "base":      base,
        "commits":   len(local_commits),
    }


def clean_large_files(nixos_dir: str, paths: list[str], delete_from_disk: bool = True) -> tuple[bool, str, dict]:
    """Get oversized files out of the working tree and out of local commits.

    Local commits back to the last published one are folded into a single new
    commit without the files. Published history is never touched – a rewrite
    there would need a force push and would break the clones on the user's
    other machines. A backup branch is left behind, so nothing is lost until
    the user says so.
    """
    if not is_git_repo(nixos_dir):
        return False, ERR_NO_GIT_REPO, {}
    report = large_file_report(nixos_dir)
    if any(p in [x[0] for x in report["published"]] for p in paths):
        return False, ("Die Datei liegt bereits auf dem Server. Das ließe sich nur durch "
                       "Umschreiben der veröffentlichten Historie beheben – das macht die "
                       "Kopien auf anderen Rechnern unbrauchbar und wird hier nicht "
                       "angeboten."), {}

    _ensure_identity(nixos_dir)
    result = {"backup_branch": "", "commit": "", "removed": [], "deleted": []}

    in_commits = [p for p, _ in report["local"] if p in paths]
    if in_commits:
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        backup = f"nico-backup-{ts}"
        rc, out = _run(["branch", backup, "HEAD"], cwd=nixos_dir)
        if rc != 0:
            return False, f"Sicherungszweig fehlgeschlagen: {out}", {}
        result["backup_branch"] = backup

        target = report["base"] or ""
        if target:
            rc, out = _run(["reset", "--soft", target], cwd=nixos_dir)
        else:
            # Never pushed: fold everything into one commit, there is no
            # published history that could break.
            rc, out = _run(["update-ref", "-d", "HEAD"], cwd=nixos_dir)
        if rc != 0:
            return False, f"Zurücksetzen fehlgeschlagen: {out}", {}

        for rel in in_commits:
            _run(["rm", "--cached", "--force", "--quiet", "--", rel], cwd=nixos_dir)
        result["removed"] = in_commits

    _append_gitignore_entries(nixos_dir, paths)
    _run(["add", "--", ".gitignore"], cwd=nixos_dir)

    if delete_from_disk:
        for rel in paths:
            try:
                (Path(nixos_dir) / rel).unlink(missing_ok=True)
                result["deleted"].append(rel)
            except OSError:
                pass

    _stage_for_commit(nixos_dir)
    rc, status = _run(["status", "--porcelain", "-uno"], cwd=nixos_dir)
    if status.strip():
        ts_msg = datetime.now().strftime("%Y-%m-%d %H:%M")
        msg = f"NiCo: Zu große Dateien aus der Versionierung entfernt ({ts_msg})"
        rc, out = _run(["commit", "-m", msg], cwd=nixos_dir)
        if rc != 0:
            return False, f"Commit fehlgeschlagen: {out}", result
        rc, head = _run(["rev-parse", "--short", "HEAD"], cwd=nixos_dir)
        result["commit"] = head.strip() if rc == 0 else ""

    return True, "", result


def drop_backup_branch(nixos_dir: str, branch: str) -> tuple[bool, str]:
    """Delete a nico-backup-* branch and reclaim the space its objects held."""
    if not branch.startswith("nico-backup-"):
        return False, "Kein NiCo-Sicherungszweig."
    rc, out = _run(["branch", "-D", branch], cwd=nixos_dir)
    if rc != 0:
        return False, out or "Zweig konnte nicht gelöscht werden."
    _run(["reflog", "expire", "--expire=now", "--expire-unreachable=now", "--all"],
         cwd=nixos_dir, timeout=60)
    _run(["gc", "--prune=now", "--quiet"], cwd=nixos_dir, timeout=300)
    return True, ""


def _oversized_push_message(blobs: list[tuple[str, int]]) -> str:
    limit_mb = REMOTE_BLOB_LIMIT // (1024 * 1024)
    listed = ", ".join(f"{p} ({s / (1024 * 1024):.0f} MB)" for p, s in blobs)
    return (
        f"Zu große Datei in einem noch nicht hochgeladenen Commit: {listed}. "
        f"Der Server nimmt Dateien über {limit_mb} MB nicht an. Die Datei steckt "
        "in der Git-Historie und muss dort entfernt werden – ein erneuter Push "
        "ändert nichts."
    )


def git_push(nixos_dir: str) -> tuple[bool, str, str]:
    """Run git push. Returns (success, raw_output, error_code)."""
    if not is_git_repo(nixos_dir):
        return False, ERR_NO_GIT_REPO, ERR_NO_GIT_REPO
    if not _get_primary_remote_name(nixos_dir):
        # Without this, git's own error comes back raw – and localised, so the
        # message language follows the system, not NiCo's.
        return False, ERR_NO_REMOTE, ERR_NO_REMOTE
    if blobs := oversized_blobs_to_push(nixos_dir):
        return False, _oversized_push_message(blobs), "FILE_TOO_LARGE"
    rc, out = _run(["push"], cwd=nixos_dir, timeout=REMOTE_TRANSFER_TIMEOUT, remote=True)
    if rc != 0:
        return False, out or "git push fehlgeschlagen", classify_push_error(out)
    return True, out, ""


def check_write_access(nixos_dir: str) -> tuple[bool, str, str]:
    """Test push write access via dry run. Returns (ok, error_code, raw_output)."""
    if not is_git_repo(nixos_dir):
        return False, ERR_NO_GIT_REPO, ERR_NO_GIT_REPO
    rc, out = _run(["push", "--dry-run"], cwd=nixos_dir, timeout=60, remote=True)
    if rc != 0:
        return False, classify_push_error(out), out
    return True, '', out


def set_remote(nixos_dir: str, url: str) -> tuple[bool, str]:
    """Add or update origin remote. Returns (ok, raw_output)."""
    if not is_git_repo(nixos_dir):
        return False, ERR_NO_GIT_REPO
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
        return False, ERR_NO_GIT_REPO
    rc, upstream = _run(["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"], cwd=nixos_dir)
    if rc != 0 or not upstream:
        return False, "Kein Upstream-Branch konfiguriert."
    rc, out = _run(["reset", "--hard", upstream.strip()], cwd=nixos_dir)
    if rc != 0:
        return False, out or "git reset fehlgeschlagen"
    _run(["clean", "-fd"], cwd=nixos_dir)
    return True, out


def git_discard_changes(nixos_dir: str) -> tuple[bool, str]:
    """Discard uncommitted changes and untracked files. Local commits stay intact."""
    if not is_git_repo(nixos_dir):
        return False, ERR_NO_GIT_REPO
    rc, out = _run(["reset", "--hard", "HEAD"], cwd=nixos_dir)
    if rc != 0:
        return False, out or "git reset fehlgeschlagen"
    _run(["clean", "-fd"], cwd=nixos_dir)
    return True, out


def git_commit_push(nixos_dir: str, label: str = "") -> tuple[bool, str, str, dict]:
    """Commit all local changes and push to remote.
    Returns (success, message, error_code, info) – the code classifies the push
    failure so the UI can show a readable reason instead of raw git output,
    info lists what was transferred (collected before the push, see push_info)."""
    if not is_git_repo(nixos_dir):
        return False, ERR_NO_GIT_REPO, ERR_NO_GIT_REPO, {}
    _ensure_identity(nixos_dir)
    ok_commit, msg_commit, _skipped = auto_commit(nixos_dir, label=label or "NiCo: Lokale Änderungen")
    if not ok_commit and "nothing to commit" not in msg_commit:
        return False, msg_commit, "", {}   # commit failed, not a push error
    info = push_info(nixos_dir)
    ok_push, msg_push, code = git_push(nixos_dir)
    if not ok_push:
        return False, msg_push, code, {}
    return True, "", "", info


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

    # Undecided foreign files (untracked, guard on) are not "dirty" – they get
    # their own dialog at the next interactive commit, the guard ignores them.
    _guard_on = foreign_guard_enabled(nixos_dir)

    def _relevant_status_lines(lines: list[str]) -> list[str]:
        if not _guard_on:
            return lines
        return [l for l in lines
                if not (l.startswith("??") and not is_config_file(l[2:].strip().strip('"')))]

    rc, status_out = _run(["status", "--porcelain"], cwd=nixos_dir)
    dirty = bool(_relevant_status_lines(status_out.splitlines())) if rc == 0 else False

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
            for line in _relevant_status_lines(_proc.stdout.splitlines())[:15]:
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


def git_push_force(nixos_dir: str) -> tuple[bool, str, str]:
    """Run git push --force on the current branch.
    Returns (success, raw_output, error_code) – same contract as git_push."""
    if not is_git_repo(nixos_dir):
        return False, ERR_NO_GIT_REPO, ERR_NO_GIT_REPO
    if not _get_primary_remote_name(nixos_dir):
        return False, ERR_NO_REMOTE, ERR_NO_REMOTE
    if blobs := oversized_blobs_to_push(nixos_dir):
        return False, _oversized_push_message(blobs), "FILE_TOO_LARGE"
    rc, out = _run(["push", "--force"], cwd=nixos_dir, timeout=REMOTE_TRANSFER_TIMEOUT, remote=True)
    if rc != 0:
        return False, out or "git push --force fehlgeschlagen", classify_push_error(out)
    return True, out, ""


def git_commit_push_force(nixos_dir: str) -> tuple[bool, str, str, dict]:
    """Commit all local changes (if any) and force-push to remote.
    Returns (success, message, error_code, info) – see git_commit_push."""
    if not is_git_repo(nixos_dir):
        return False, ERR_NO_GIT_REPO, ERR_NO_GIT_REPO, {}
    _ensure_identity(nixos_dir)
    ok_commit, msg_commit, _skipped = auto_commit(nixos_dir, label="NiCo: Lokale Änderungen")
    if not ok_commit and "nothing to commit" not in msg_commit:
        return False, msg_commit, "", {}   # commit failed, not a push error
    info = push_info(nixos_dir)
    ok_push, msg_push, code = git_push_force(nixos_dir)
    if not ok_push:
        return False, msg_push, code, {}
    return True, "", "", info


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
    Restore all tracked files from a previous commit without rewriting history.
    Uses git ls-tree to discover every file present at that commit, checks out
    each one, then creates a new auto-commit to record the rollback.
    Returns (success, message).
    """
    import re as _re
    if not _re.fullmatch(r"[0-9a-f]{7,40}", commit_hash):
        return False, "Ungültiger Commit-Hash."
    if not is_git_repo(nixos_dir):
        return False, ERR_NO_GIT_REPO

    _ensure_identity(nixos_dir)

    rc, target_list = _run_paths(["ls-tree", "-r", "--name-only", commit_hash],
                                 cwd=nixos_dir)
    if rc != 0 or not target_list:
        return False, "Commit nicht gefunden oder enthält keine Dateien."

    target_files = set(target_list)

    restored = []
    failed   = []
    for fname in sorted(target_files):
        rc, _ = _run(["checkout", commit_hash, "--", fname], cwd=nixos_dir)
        if rc == 0:
            restored.append(fname)
        else:
            failed.append(fname)

    if not restored:
        return False, "Keine Dateien wiederhergestellt."

    # A file that could not be checked out stays on its newer content while
    # everything around it went back - the mixed state this function exists to
    # avoid. It must not pass as a successful restore.
    if failed:
        return False, ("Teilweise wiederhergestellt – diese Dateien stehen noch "
                       "auf dem neueren Stand: " + ", ".join(sorted(failed)[:10]))

    # Tracked files that were added after the target commit must be removed,
    # otherwise the working tree is a mix of both states.
    removed = []
    rc_cur, current = _run_paths(["ls-files"], cwd=nixos_dir)
    if rc_cur == 0:
        for fname in current:
            if fname and fname not in target_files:
                rc_rm, _ = _run(["rm", "-f", "--", fname], cwd=nixos_dir)
                if rc_rm == 0:
                    removed.append(fname)

    short = commit_hash[:7]
    parts = [f"{len(restored)} Datei(en)"]
    if removed:
        parts.append(f"{len(removed)} entfernt")
    msg   = f"NiCo: Rollback zu {short} ({', '.join(parts)})"
    rc, out = _run(["commit", "-m", msg], cwd=nixos_dir)
    _no_change = ("nothing to commit" in out or "nichts zu committen" in out
                  or "nothing added to commit" in out)
    if rc != 0 and not _no_change:
        return False, f"Commit nach Rollback fehlgeschlagen: {out}"

    return True, f"Wiederhergestellt: {', '.join(parts)} → {short}"


def _parse_diff(diff_text: str) -> list[dict]:
    """Parse unified diff text into hunk list.

    Only added/removed lines are collected; context lines are ignored.
    Lines that appear identically in both added and removed sets were merely
    reordered by the editor (not semantically changed) and are excluded, so
    only truly new or truly deleted content remains.
    """
    raw = []
    for line in diff_text.splitlines():
        if line.startswith(("diff ", "index ", "--- ", "+++ ", "@@ ")):
            continue
        if line.startswith("+"):
            raw.append({"type": "added",   "content": line[1:]})
        elif line.startswith("-"):
            raw.append({"type": "removed", "content": line[1:]})

    # Cancel reordered lines pairwise (multiset): each removed occurrence
    # neutralizes at most one added occurrence of the same content, so a real
    # net addition/removal of duplicated lines is preserved.
    from collections import Counter
    added_counts   = Counter(h["content"] for h in raw if h["type"] == "added")
    removed_counts = Counter(h["content"] for h in raw if h["type"] == "removed")
    cancel = {c: min(added_counts[c], removed_counts[c])
              for c in added_counts.keys() & removed_counts.keys()}

    skipped: dict[tuple, int] = {}
    result = []
    for h in raw:
        key = (h["type"], h["content"])
        if skipped.get(key, 0) < cancel.get(h["content"], 0):
            skipped[key] = skipped.get(key, 0) + 1
            continue
        result.append(h)
    return result


def get_diff(nixos_dir: str, from_hash: str, to_hash: str = "HEAD") -> dict:
    """Return structured diff between two commits as {files: [...]}."""
    import re as _re

    def _valid(h: str) -> bool:
        return bool(_re.fullmatch(r"[0-9a-f]{7,40}|HEAD|HEAD~\d+", h))

    if not _valid(from_hash) or not _valid(to_hash):
        return {"error": "Ungültiger Commit-Hash"}
    if not is_git_repo(nixos_dir):
        return {"error": ERR_NO_GIT_REPO}

    rc, out = _run(["diff", "-w", "--name-status", from_hash, to_hash],
                   cwd=nixos_dir, timeout=30)
    if rc != 0:
        return {"error": out or "git diff fehlgeschlagen"}

    _DIFF_EXTENSIONS = {".nix", ".lock"}  # only config-relevant files

    status_map = {"A": "added", "M": "modified", "D": "deleted"}
    files = []
    for line in out.splitlines():
        if not line.strip():
            continue
        parts = line.split("\t", 1)
        if len(parts) != 2:
            continue
        status_char = parts[0][0]
        filename = parts[1]
        if status_char == "R":          # rename: "old\tnew"
            fn_parts = filename.split("\t")
            filename = fn_parts[-1]

        # Only show .nix files and flake.lock
        ext = "." + filename.rsplit(".", 1)[-1] if "." in filename else ""
        if ext not in _DIFF_EXTENSIONS:
            continue

        status = status_map.get(status_char, "modified")

        # flake.lock: don't diff content, just signal that it changed
        if filename == "flake.lock" or filename.endswith("/flake.lock"):
            files.append({"filename": filename, "status": status,
                          "hunks": [], "lock_updated": True})
            continue

        rc2, diff_out = _run(
            ["diff", "-w", from_hash, to_hash, "--", filename],
            cwd=nixos_dir, timeout=30,
        )
        files.append({
            "filename": filename,
            "status":   status,
            "hunks":    _parse_diff(diff_out),
        })

    return {"files": files}
