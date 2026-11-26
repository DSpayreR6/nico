"""
Who is writing to the config right now - and who needs it alone.

Two axes keep actions apart (Paket C, Codex re-check 2026-11-26, decided by
Martin the same day):

  * jobs - rebuild, dry run, generation switch, update, terminal rebuild -
    run one at a time (routes/rebuild.reserve). Saving next to a rebuild is
    wanted; the result says when the config changed meanwhile.
  * writes to the config - this module. A normal write (save, autosave, a
    GET that stamps a file) may run next to any other normal write and next
    to a job. An exclusive action needs the config alone: it changes many
    files as one step (import, restore, pull, removing large files, detach,
    categorize) or takes the directory away (move, restart).

Why exclusive: an import remembers the old files, replaces them and puts
them back on an error. A save from a second tab in between - one open tab
with autosave is enough - was overwritten by that rollback although the
save had reported success. The file lock of atomic.write_bytes guards one
write, not a whole operation.

Until 2026-11-26 only the move refused writes, and only when a request
arrived: a save already past the check still wrote into the old place after
the copy was verified, and the move deleted it (Codex re-check).

Rules:
  * Once an exclusive action waits, new writers are refused at once - a
    stream of autosaves cannot starve it.
  * It waits at most WAIT_SECONDS for running writers; then it gives up,
    says so, and the config is free again immediately.
  * It does not count itself as a writer.

Which route is what stands in ROUTES below and nowhere else; server.py
applies it to every request (testing/routes/test_codex_1126_paket_c.py
checks that every writing route is listed or written by default).
"""
from __future__ import annotations

import threading
import time

WAIT_SECONDS = 5.0

_cv = threading.Condition()
_writers = 0                      # normal writes running
_exclusive: str | None = None     # kind of the exclusive action (waiting or running)

# The answer a refused writer gets, by the kind holding the config.
_BUSY = {
    "move":    "ERR_BUSY_MOVE",
    "restart": "ERR_BUSY_RESTART",
}
_BUSY_DEFAULT = "ERR_BUSY_CONFIG_ACTION"


# ── Routes ───────────────────────────────────────────────────────────────────
# Exclusive, by URL rule. The kind names the action in busy answers; every
# kind except move and restart also takes the job reservation for config
# actions (rebuild.reserve_config_action), so it never runs next to a job
# that reads the config, in either order.
EXCLUSIVE = {
    "/api/import/apply":         "import",
    "/api/import/run":           "import",
    "/api/import/zip/apply":     "import",
    "/api/symlink/create":       "import",      # may copy /etc/nixos over the config
    "/api/git/rollback":         "restore",
    "/api/git/reset-hard":       "restore",
    "/api/git/discard-local":    "restore",
    "/api/git/pull":             "pull",
    "/api/git/large-files/clean": "clean",
    "/api/config/detach":        "detach",
    "/api/categorize":           "categorize",
    "/api/flake/create":         "flake",       # several files with one rollback
    "/api/flake/host/add":       "flake",
    "/api/config/move":          "move",
    "/api/restart":              "restart",
}

# GET routes that write when called with the CSRF token: they stamp a type
# into a file, rewrite files changed outside, or fetch into .git.
GET_WRITERS = {
    "/api/config",
    "/api/file",
    "/api/file/<path:rel_path>",
    "/api/git/remote-status",
    "/api/git/start-check",
}

# Writing methods that do not touch the config: they pass even while it is
# held. Everything else with POST/PUT/PATCH/DELETE counts as a normal write.
NOT_CONFIG = {
    "/api/shutdown",
    "/api/sudo/acquire",
    "/api/rebuild/ack",
    "/api/rebuild/verbose",
    "/api/format/proceed",
    "/api/app/settings",
    "/api/config/path",
    "/api/settings/app/import",
    # Jobs that do not read the config (decision Martin 2026-11-26): they
    # stay free next to an import; the job reservation keeps them apart
    # from other jobs, the move and the restart (Codex C2: as normal writes
    # they were refused while an import ran).
    "/api/generations/switch",
    "/api/selfupdate",
}
# GET routes with a token that write nothing of the config (app settings,
# a search) - listed so the test can tell them from forgotten ones.
GET_NOT_CONFIG = {
    "/api/rebuild/default-host",
    "/api/packages/search",
}

_WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


def classify(rule: str | None, method: str, has_token: bool) -> str | None:
    """'exclusive', 'shared' or None (no claim) for one request."""
    if not rule or not rule.startswith("/api/"):
        return None
    if method in _WRITE_METHODS:
        if rule in EXCLUSIVE:
            return "exclusive"
        if rule in NOT_CONFIG:
            return None
        return "shared"
    # HEAD runs the GET handler (Flask) - the same writes (Codex C4).
    if method in ("GET", "HEAD") and has_token and rule in GET_WRITERS:
        return "shared"
    return None


# ── The counter ──────────────────────────────────────────────────────────────

def enter_shared() -> str | None:
    """Start a normal write. None, or the busy code while the config is held."""
    global _writers
    with _cv:
        if _exclusive is not None:
            return _BUSY.get(_exclusive, _BUSY_DEFAULT)
        _writers += 1
        return None


def leave_shared() -> None:
    global _writers
    with _cv:
        _writers = max(0, _writers - 1)
        _cv.notify_all()


def enter_exclusive(kind: str, wait: float | None = None) -> str | None:
    """Take the config alone. From the first moment new writers are refused;
    running ones get `wait` seconds (WAIT_SECONDS) to finish. None, or the
    busy code - then nothing is held."""
    global _exclusive
    limit = WAIT_SECONDS if wait is None else wait
    with _cv:
        if _exclusive is not None:
            return _BUSY.get(_exclusive, _BUSY_DEFAULT)
        _exclusive = kind
        end = time.monotonic() + limit
        while _writers > 0:
            left = end - time.monotonic()
            if left <= 0:
                _exclusive = None
                _cv.notify_all()
                return "ERR_BUSY_WRITE"
            _cv.wait(left)
        return None


def leave_exclusive() -> None:
    global _exclusive
    with _cv:
        _exclusive = None
        _cv.notify_all()


def state() -> dict:
    """For tests and diagnostics."""
    with _cv:
        return {"writers": _writers, "exclusive": _exclusive}
