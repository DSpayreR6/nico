"""Rebuild, dry-run and sudo nonce routes.

Split from server.py. Route bodies are unchanged; shared request helpers
from create_app() are passed in via the ctx dict.
"""

import json
import os
import re
import secrets
import subprocess
import threading
import time
import uuid
from pathlib import Path

from flask import Response, jsonify, request

from .. import buildlimits, config_manager, git_manager, prebuild
from ..core import (
    clean_nix_error as _clean_nix_error,
    get_flake_hosts as _get_flake_hosts,
)


# ── Rebuild job state ────────────────────────────────────────────────────────
# A rebuild used to live inside its own SSE response: the process was started by
# the streaming endpoint, so losing that connection (tab switch, reload, closed
# window) took the whole view with it and left nixos-rebuild running unattended
# with nobody reading its output. The job now runs in a background thread and
# buffers its ready-to-send SSE events here, so clients can attach and re-attach
# at any time. Only one job at a time; the finished job stays available until a
# new one starts, so a reload can still show the result.
_JOB_MAX_EVENTS = 20000  # ring buffer; the UI keeps far fewer log lines anyway

# nixos-rebuild-ng activates the system through a transient systemd unit. While
# that unit runs, a second rebuild cannot create it again: systemd-run fails
# with "unit was already loaded" and nixos-rebuild-ng shows the user a Python
# traceback. The job guard below only knows jobs of this process, so it misses
# a switch that survived a NiCo restart – ask systemd instead.
_SWITCH_UNIT = "nixos-rebuild-switch-to-configuration.service"
_SWITCH_BUSY_STATES = {"active", "activating", "reloading", "deactivating"}

_job_cv = threading.Condition()
_job = None

# Finished dry-run results by job id, until the tab that started the run
# fetched and acknowledged it (Codex re-check 2026-11-26). The server holds
# one job: when a second run started between the end of the first and the
# moment its tab opened the stream, that tab read the second result - and
# a save waiting on a passed dry run went ahead on someone else's result.
# A few are enough - one per tab waiting; an id pushed out answers "gone".
_DRY_RESULTS_MAX = 8
_dry_results: "dict[str, str]" = {}   # job id -> its done event (SSE block)


class DownloadTally:
    """Compressed bytes of the cache downloads in a rebuild.

    Nix reports each download as a FileTransfer activity with (done,
    expected) progress and a stop at its end. A finished download used to
    have its bytes subtracted again, so "loaded" shrank and "remaining" grew
    back while the rebuild ran (Martin, a long-standing observation; cause
    found by Codex, 2026-11-20). Now every transfer counts once: while it
    runs with what has arrived, after its end with its final bytes, and its
    planned size becomes what really arrived.
    """

    def __init__(self):
        self._acts: dict = {}
        self.done = 0
        self.expected = 0

    def progress(self, act_id, done: int, expected: int) -> None:
        d0, e0 = self._acts.get(act_id, (0, 0))
        self.done = max(0, self.done + done - d0)
        self.expected = max(0, self.expected + expected - e0)
        self._acts[act_id] = (done, expected)

    def stop(self, act_id) -> None:
        d, e = self._acts.pop(act_id, (0, 0))
        self.expected = max(self.done, self.expected + d - e)
# Held from the "is a job running?" check until the new job is registered.
# Both used to be separate sections under _job_cv, about 450 lines apart:
# two starts close together (two tabs, a double click) both passed the
# check, and the second replaced the job of the first (revision 2026-11-18,
# finding 25). Not _job_cv itself: the stream readers wait on it.
_start_lock = threading.Lock()

# Since 2026-11-26 (revision 2026-11-25, F6) the one reservation for every
# action that must not run next to another: rebuild, dry run, generation
# switch, update, moving the config, restarting NiCo. Moving and restarting
# used to check only the registered job, so a rebuild still being prepared
# (holding _start_lock, no job yet) could run next to a move that took its
# directory away. Reserved as the first step, before any state changes;
# never waits - the second one is told at once what is running.
_holder: str | None = None   # what holds _start_lock while it is held

# A generation entered in the profile whose activation failed (F8):
# {"number", "mode"} for the "activate again" offer, None otherwise. In
# memory only - after a restart of NiCo the list no longer offers it, any
# state stays one click away.
_activation_retry: dict | None = None

_BUSY_CODES = {
    "rebuild":    "ERR_REBUILD_RUNNING",
    "dryrun":     "ERR_BUSY_DRYRUN",
    "generation": "ERR_BUSY_GENERATION",
    "selfupdate": "ERR_BUSY_UPDATE",
    "move":       "ERR_BUSY_MOVE",
    "restart":    "ERR_BUSY_RESTART",
}


def _job_kind(mode: str) -> str:
    """The kind of a registered job from its mode."""
    if mode in ("dryrun", "selfupdate"):
        return mode
    if str(mode).startswith("generation-"):
        return "generation"
    return "rebuild"


# A config action that changes many files as one step (writeguard.EXCLUSIVE:
# import, restore, pull, …) while it runs. Paket C (Codex re-check
# 2026-11-26): an import deletes the old files before it writes the new ones,
# so a rebuild evaluating meanwhile could find no configuration.nix -
# changed_during only compares start and end. Kept apart in both orders:
# the action is refused while such a job runs or is being prepared, and no
# such job starts while the action runs. Generation switch and update do
# not read the config and stay free.
_config_action: str | None = None
_READS_CONFIG = {"rebuild", "dryrun", "move", "restart"}
_CONFIG_ACTION_BUSY = "ERR_BUSY_CONFIG_ACTION"


def reserve(kind: str) -> str | None:
    """Take the reservation for `kind`. None when taken - the caller must
    release() it - otherwise the error code naming what is running."""
    global _holder
    if not _start_lock.acquire(blocking=False):
        return _BUSY_CODES.get(_holder or "rebuild", "ERR_REBUILD_RUNNING")
    if kind in _READS_CONFIG and _config_action is not None:
        _start_lock.release()
        return _CONFIG_ACTION_BUSY
    with _job_cv:
        running = _job.get("mode", "") if _job is not None and _job["running"] else None
    if running is not None:
        _start_lock.release()
        return _BUSY_CODES[_job_kind(running)]
    _holder = kind
    return None


def reserve_config_action(kind: str) -> str | None:
    """Mark a config action as running (see _config_action). None when
    marked - release_config_action() afterwards - or the busy code.

    Checked and marked under _start_lock, the lock every job start holds
    from its check to its registration: there is no moment in which both
    see the other as absent. A start being prepared holds that lock only
    for its preparation; waiting up to two seconds for it keeps an import
    from being refused for a generation switch that is just registering.
    """
    global _config_action
    if not _start_lock.acquire(timeout=2.0):
        return _BUSY_CODES.get(_holder or "rebuild", "ERR_REBUILD_RUNNING")
    try:
        if _config_action is not None:
            return _CONFIG_ACTION_BUSY
        with _job_cv:
            running = _job.get("mode", "") if _job is not None and _job["running"] else None
        if running is not None and _job_kind(running) in _READS_CONFIG:
            return _BUSY_CODES[_job_kind(running)]
        _config_action = kind
        return None
    finally:
        _start_lock.release()


def release_config_action() -> None:
    # Without _start_lock: a terminal rebuild holds it for its whole run,
    # and clearing needs no check - at worst a start sees the action one
    # moment longer and is refused.
    global _config_action
    _config_action = None


def release() -> None:
    """Give the reservation back (not after a restart: the process ends)."""
    global _holder
    _holder = None
    _start_lock.release()


def _start_job(generate, *, mode: str, hostname: str = "", verbose: bool = False) -> str:
    """Register a job and run `generate` (yielding SSE event strings) in a
    worker thread; the monitor and /api/rebuild/stream follow it.

    The one frame for every job (revision 2026-11-25, D1): rebuild,
    generation switch, update and dry run had each their own copy of the
    registration, the worker and the end. The caller holds the reservation;
    from here on the running job is what keeps the others out."""
    global _job
    import json as _json
    job_id = uuid.uuid4().hex
    with _job_cv:
        _job = {
            "id":       job_id,
            "running":  True,
            "events":   [],
            "dropped":  0,
            "success":  None,
            "acked":    False,
            "mode":     mode,
            "hostname": hostname,
            "verbose":  verbose,
            "started":  time.time(),
            "finished": None,
        }
        _job_cv.notify_all()

    def _run():
        try:
            for event in generate():
                _job_append(job_id, event)
        except Exception as exc:  # never let the thread die silently
            _job_append(job_id,
                        f"data: {_json.dumps({'type': 'error', 'message': str(exc)})}\n\n")
        finally:
            _job_finish(job_id)

    threading.Thread(target=_run, name=f"{_job_kind(mode)}-{job_id[:8]}", daemon=True).start()
    return job_id

# Upper bound for the written rebuild log. nixos-rebuild runs with `--log-format internal-json -v`, which emits one JSON line per evaluated
# file: a single failed run produced 2.1 GB here, so everything above this is
# dropped. Since 2026-11-15 those lines are only kept while "show all
# messages" is on (is_detail_msg).
#
# The tail is what is kept, and the tail is the wrong end: with `-v` a run
# finishes on store-optimisation chatter. On 2026-11-01 a failed rebuild
# dropped 2,540,770 lines and with them the only line naming the cause
# (`python3.13-fpdf2` failing its test suite); what survived was the
# `Cannot build … 1 dependency failed` cascade, which names nothing. The
# lines that explain a failure are therefore collected separately, are not
# subject to this cap, and are written above the log – see _ErrorLines.
_LOG_MAX_BYTES = 5 * 1024 * 1024


def rebuild_cmd(mode: str, flake_arg: str, conf_path) -> list:
    """The nixos-rebuild call of the monitored rebuild, without the limits.

    Always with `-v`, so the switch in the monitor can turn the detail
    messages on while the build runs; switched off they are dropped right
    after reading (is_detail_msg) and reach neither monitor nor log.

    Module level so a test can drive it; the job itself is a closure.
    """
    if flake_arg:
        cmd = ["sudo", "-S", "nixos-rebuild", mode, "--flake", flake_arg]
    else:
        cmd = ["sudo", "-S", "nixos-rebuild", mode, "-I", f"nixos-config={conf_path}"]
    return cmd + ["--log-format", "internal-json", "-v"]


# nix verbosity: 0 error, 1 warn, 2 notice, 3 info – what a rebuild in the
# terminal shows. From 4 (talkative) on it is detail that only `-v` brings.
_LVL_TALKATIVE = 4


def is_detail_msg(json_str: str) -> bool:
    """True for a `@nix` message line of level 4 or above.

    One line per evaluated file and per store link: what scrolled through
    the monitor and pushed warnings out of sight (Martin, 2026-11-15). Only
    `msg` counts. Activities (`start`/`stop`) and results carry a level too
    and must pass – phases, progress and builder output are read from them;
    measured on 2026-11-15, they arrive with and without `-v` alike.
    """
    # Cheap test first: this runs for every line of the build.
    if '"msg"' not in json_str:
        return False
    try:
        data = json.loads(json_str)
    except Exception:
        return False
    if not isinstance(data, dict) or data.get("action") != "msg":
        return False
    level = data.get("level")
    return isinstance(level, int) and not isinstance(level, bool) and level >= _LVL_TALKATIVE


def _job_verbose() -> bool:
    """Whether the running job shows detail messages – read live per line."""
    with _job_cv:
        return bool(_job and _job.get("verbose"))

class _OwnChatter:
    """nixos-rebuild's own detail lines, which the monitor hides.

    With `-v` nixos-rebuild-ng logs every command it runs (`debug: …`) and
    ends a failure on a Python traceback of some fifty lines, of which the
    last one says what happened (Martins VM run of 2026-11-15). Neither is a
    `@nix` message, so is_detail_msg never saw them.

    Unlike the nix detail messages these always reach the log file – they
    are a few dozen lines, and a failure that does not repeat on a second
    run (the vboxnet0 race of 2026-11-13) would otherwise be lost. Only the
    monitor skips them while "show all messages" is off.

    Stateful: `hidden` must see every plain line, whatever the switch says,
    because the switch can flip in the middle of a traceback.
    """

    def __init__(self):
        self._in_traceback = False

    def hidden(self, line: str) -> bool:
        plain = _RE_ANSI.sub("", line)
        if self._in_traceback:
            # Frames, source lines and carets are indented; the exception
            # itself is not, and it is the line worth showing.
            if not plain.strip() or plain[:1].isspace():
                return True
            self._in_traceback = False
        if plain.startswith("Traceback (most recent call last):"):
            self._in_traceback = True
            return True
        return plain.startswith("debug: ")


# Terminal escapes: nix colours its own errors and so do compilers and test
# runners, so a match on `^FAILED` would miss `\x1b[31;1mFAILED\x1b[0m`.
_RE_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")

# Builder output carries no log level, so the line itself has to decide.
# Deliberately narrow: the plain word `error` travels in harmless file names
# (`femtools/errors.py` scrolled past ten times in the run that prompted
# this), so only the shapes a compiler, a test runner or nix itself uses to
# announce a failure count.
_RE_BUILD_ERR = re.compile(r"(?:^|\s)(?:error|fatal error):|failed with exit code",
                           re.IGNORECASE)
_RE_BUILD_ERR_CASED = re.compile(r"^(?:FAILED|ERROR)\b")

# The activation phase writes plain text, not `@nix` JSON: switch-to-configuration
# and systemd talk straight to stderr. Measured on Martins run of 2026-11-01,
# where the one line that explained the failure read
# `Failed to restart network-addresses-vboxnet0.service` and the box showed
# two useless git warnings instead.
#
# Narrow on purpose. The same output carries four lines that look alike and
# mean nothing is wrong: `stopping the following units: …`,
# `NOT restarting the following changed units: …`,
# `restarting the following units: …` and
# `the following new units were started: …`. None of them may match.
_RE_RAW_ERR = re.compile(
    r"^Failed to \S+ \S+"                        # systemd: Failed to restart x.service
    r"|the following units failed"                # switch-to-configuration summary
    r"|activation script snippet '[^']*' failed"
    r"|^Job for \S+ failed"
    r"|(?:^|\s)error:",
    re.IGNORECASE)

# nix result types. Their own enum, so 101 here is not the file-transfer
# activity type that also carries 101 – the overlap is nix's, not a typo.
_RES_BUILD_LOG      = 101  # resBuildLogLine: a line the builder wrote
_RES_POST_BUILD_LOG = 107  # resPostBuildLogLine: same, from a post-build hook


def _build_log_text(data: dict) -> str:
    """The builder output of a `@nix` result message, '' when it carries none.

    Module level on purpose: the stream parser lives inside the job closure
    and cannot be driven from a test, while this is the one decision that
    went wrong – both result types were dropped unread until 0.15.0.
    """
    if data.get("action") != "result":
        return ""
    if data.get("type") not in (_RES_BUILD_LOG, _RES_POST_BUILD_LOG):
        return ""
    fields = data.get("fields") or []
    text = fields[0] if fields else ""
    return text if isinstance(text, str) else ""


class _ErrorLines:
    """The lines of one rebuild that say why it failed.

    Kept apart from the log buffer because the size cap keeps the tail and
    the cause is at the front: the first error is the one that stopped the
    build, everything after it is the dependency cascade. So the first
    _ERR_MAX lines are kept and later ones counted, not the other way round.
    """

    _ERR_MAX = 200

    def __init__(self):
        self.lines: list[str] = []
        self.dropped = 0

    def add(self, line: str, pkg: str = "") -> None:
        text = _RE_ANSI.sub("", line).rstrip()
        if not text:
            return
        if pkg:
            text = f"{pkg}: {text}"
        if len(self.lines) >= self._ERR_MAX:
            self.dropped += 1
            return
        self.lines.append(text)

    def consider_msg(self, level, line: str) -> bool:
        """Keep a nix message of level error (0).

        Warnings (level 1) used to count too and that was wrong: a flake
        rebuild out of a git tree with uncommitted changes warns every single
        time, so on Martins run of 2026-11-01 the box held nothing but
        `warning: Git tree … is dirty`, twice, while the line naming the
        failure was missing. Warnings stay in the log, where they belong.
        A block list of known always-on warnings would go stale; the level is
        the honest boundary.
        """
        if not isinstance(level, int) or level > 0:
            return False
        self.add(line)
        return True

    def consider_raw(self, line: str) -> bool:
        """Keep a plain stderr line that announces a failure."""
        plain = _RE_ANSI.sub("", line).strip()
        if not plain or not _RE_RAW_ERR.search(plain):
            return False
        self.add(line)
        return True

    def consider_build(self, line: str, pkg: str = "") -> bool:
        """Keep a builder output line that announces a failure."""
        plain = _RE_ANSI.sub("", line).strip()
        if not (_RE_BUILD_ERR.search(plain) or _RE_BUILD_ERR_CASED.search(plain)):
            return False
        self.add(line, pkg)
        return True

    def header(self) -> list[str]:
        if not self.lines:
            return []
        out = ["── Error lines of this run ──", *self.lines]
        if self.dropped:
            out.append(f"── {self.dropped} further error lines not kept ──")
        out.append("── Full output below ──")
        return out


# switch-to-configuration ends with status 4 when the new system is in place
# and only some units did not come back. Measured on Martins runs of
# 2026-11-01 and 2026-11-13: VirtualBox' vboxnet0 interface is torn down and
# recreated during the switch, `network-addresses-vboxnet0.service` is
# restarted in between and fails on the missing device – three seconds later
# it runs. nixos-rebuild reports that as a failure like any other, although
# everything was built, the profile is set and the configuration activated.
_RE_SWITCH_FAILED = re.compile(
    r"^switching to system configuration \S+ failed \(status (\d+)\)")
# The same status once more, in the last line of nixos-rebuild-ng's traceback:
#   subprocess.CalledProcessError: Command '['systemd-run', …,
#   '--unit=nixos-rebuild-switch-to-configuration', …]' returned non-zero
#   exit status 4.
# On Martins VM run of 2026-11-15 (NixOS 26.05) the rebuild ended red although
# only btrfs-dynamic-reclaim.service had failed; the pasted tail showed this
# line and not the one above. Only a command that names switch-to-configuration
# counts – a failed `nix build` raises the same exception with its own status.
_RE_SWITCH_CMD_FAILED = re.compile(
    r"^subprocess\.CalledProcessError: Command '.*switch-to-configuration.*' "
    r"returned non-zero exit status (\d+)\.?$")
_RE_UNIT_NOT_STARTED = re.compile(
    r"^Failed to (?:start|restart|reload|reload-or-restart) (\S+)$")
_RE_UNITS_FAILED = re.compile(r"the following units failed:\s*(.+)$", re.IGNORECASE)
_SWITCH_UNITS_FAILED = 4


class _Activation:
    """What the activation phase of one rebuild said about its outcome.

    `partial` is the one case that is not a plain failure: built, switched,
    some units not restarted. Every other status (a failing activation
    script, a bootloader that could not be installed) stays an error.
    """

    def __init__(self):
        self.status: int | None = None
        self.units: list[str] = []

    def _add(self, unit: str) -> None:
        unit = unit.strip().rstrip(",.")
        if unit and unit not in self.units:
            self.units.append(unit)

    def consider(self, line: str) -> None:
        plain = _RE_ANSI.sub("", line).strip()
        if not plain:
            return
        m = _RE_SWITCH_FAILED.match(plain)
        if m:
            self.status = int(m.group(1))
            return
        m = _RE_SWITCH_CMD_FAILED.match(plain)
        if m:
            # switch-to-configuration's own line wins where both are there.
            if self.status is None:
                self.status = int(m.group(1))
            return
        m = _RE_UNIT_NOT_STARTED.match(plain)
        if m:
            self._add(m.group(1))
            return
        m = _RE_UNITS_FAILED.search(plain)
        if m:
            for unit in m.group(1).split(","):
                self._add(unit)

    @property
    def partial(self) -> bool:
        return self.status == _SWITCH_UNITS_FAILED


# The prompts in the generated terminal script are English, like every other
# text NiCo writes into a file. Translating them would mean sending a string
# from the browser into a shell script - an injection path that vorgaben.txt
# closes on purpose ("kein shell=True") - or teaching the backend to read the
# language files, which it does nowhere else. Decided 2026-11-01.
class _LogBuffer:
    """The output of one rebuild, capped while it is collected.

    A plain list stood here and the cap applied only when the log was
    written: a run like the one of 2026-09-12 (2.1 GB) sat in NiCo's memory
    in full while the machine was busy building (revision 2026-11-10, B43).
    Keeps the tail, like the written log, and counts what it let go.
    """

    def __init__(self, max_bytes: int | None = None):
        from collections import deque
        self._lines = deque()
        self._size = 0
        self._max = _LOG_MAX_BYTES if max_bytes is None else max_bytes
        self.dropped = 0

    def append(self, line: str) -> None:
        self._lines.append(line)
        self._size += len(line.encode("utf-8", "replace")) + 1
        while self._size > self._max and len(self._lines) > 1:
            old = self._lines.popleft()
            self._size -= len(old.encode("utf-8", "replace")) + 1
            self.dropped += 1

    def __len__(self):
        return len(self._lines)

    def __iter__(self):
        return iter(self._lines)

    def __reversed__(self):
        return reversed(self._lines)


def _write_rebuild_log(log_path: Path, lines: "list[str] | _LogBuffer",
                       errors: "_ErrorLines | None" = None) -> bool:
    """Write the rebuild output, keeping at most _LOG_MAX_BYTES from the end.

    The error lines go in front of it, whole: they are the reason the file is
    read at all, and the cap must not be able to swallow them.

    Returns True if the file was written.
    """
    kept, size = [], 0
    for line in reversed(lines):
        size += len(line.encode("utf-8", "replace")) + 1
        if size > _LOG_MAX_BYTES:
            break
        kept.append(line)
    kept.reverse()
    dropped = len(lines) - len(kept) + getattr(lines, "dropped", 0)
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(log_path, "w", encoding="utf-8") as fh:
            for line in (errors.header() if errors else []):
                fh.write(line + "\n")
            if dropped:
                fh.write(
                    f"── Truncated: {dropped} older lines removed, "
                    f"the last {_LOG_MAX_BYTES // (1024 * 1024)} MB follow ──\n"
                )
            fh.write("\n".join(kept))
        return True
    except Exception:
        return False


def switch_unit_busy() -> bool:
    """True while nixos-rebuild's activation unit is still running.

    Not reachable or no systemd: return False – a check that cannot run must
    not block a rebuild.
    """
    try:
        res = subprocess.run(
            ["systemctl", "is-active", _SWITCH_UNIT],
            capture_output=True, text=True, timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return res.stdout.strip() in _SWITCH_BUSY_STATES


def failed_unit_names() -> set[str]:
    """Names of the units systemd currently reports as failed.

    Taken before a rebuild so that afterwards only what *this* run broke is
    reported. A desktop carries failed units that have nothing to do with the
    config - five KDE crash handlers on the machine this was written on - and
    listing them after every build would be noise from day one.
    """
    try:
        res = subprocess.run(
            ["systemctl", "list-units", "--failed", "--no-legend", "--plain",
             "--no-pager", "--output=json"],
            capture_output=True, text=True, timeout=10,
        )
        entries = json.loads(res.stdout or "[]") if res.returncode == 0 else []
    except (OSError, subprocess.SubprocessError, ValueError):
        return set()
    if not isinstance(entries, list):
        return set()
    return {e["unit"].strip() for e in entries
            if isinstance(e, dict) and isinstance(e.get("unit"), str) and e["unit"].strip()}


def failed_units(limit: int = 6, log_lines: int = 15,
                 exclude: "set[str] | None" = None) -> list[dict]:
    """Units systemd reports as failed, each with the tail of its journal.

    Asked after every rebuild, successful or not. nixos-rebuild returns a
    non-zero exit code when an activation script or a unit fails, but the
    reason is buried in a log that scrolls past - on 2026-10-30 a
    `btrfs-dynamic-reclaim` unit from an imported Nix-Brick brought a rebuild
    down and the one line that explained it ("no writable btrfs volume found")
    had to be dug out with systemctl by hand. A unit that fails quietly after
    a *successful* rebuild is worse still: nobody looks.

    Never raises and never blocks: no systemd, no permission or a timeout
    simply means an empty list.
    """
    try:
        res = subprocess.run(
            ["systemctl", "list-units", "--failed", "--no-legend", "--plain",
             "--no-pager", "--output=json"],
            capture_output=True, text=True, timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    if res.returncode != 0:
        return []

    try:
        entries = json.loads(res.stdout or "[]")
    except ValueError:
        return []
    if not isinstance(entries, list):
        return []

    skip = exclude or set()
    out: list[dict] = []
    for entry in entries:
        if len(out) >= limit:
            break
        name = (entry or {}).get("unit") if isinstance(entry, dict) else None
        if not isinstance(name, str) or not name.strip():
            continue
        name = name.strip()
        if name in skip:
            continue
        journal = ""
        try:
            jr = subprocess.run(
                ["journalctl", "-u", name, "-b", "-n", str(log_lines),
                 "--no-pager", "--output=cat"],
                capture_output=True, text=True, timeout=10,
            )
            if jr.returncode == 0:
                journal = jr.stdout.strip()
        except (OSError, subprocess.SubprocessError):
            pass
        out.append({
            "unit":        name,
            "description": (entry.get("description") or "").strip(),
            "journal":     journal,
        })
    return out


def job_running() -> bool:
    """A rebuild, switch or update is running – a restart would kill it."""
    with _job_cv:
        return _job is not None and bool(_job["running"])


def _job_public(job):
    """Serializable job summary (caller must hold _job_cv)."""
    return {
        "id":       job["id"],
        "running":  job["running"],
        "success":  job["success"],
        "acked":    job["acked"],
        "mode":     job["mode"],
        "hostname": job["hostname"],
        "verbose":  bool(job.get("verbose")),
        "started":  job["started"],
        "finished": job["finished"],
        "events":   job["dropped"] + len(job["events"]),
    }


def _job_append(job_id, event):
    """Append one SSE event block to the job buffer and wake up all readers."""
    with _job_cv:
        if _job is None or _job["id"] != job_id:
            return
        _job["events"].append(event)
        overflow = len(_job["events"]) - _JOB_MAX_EVENTS
        if overflow > 0:
            del _job["events"][:overflow]
            _job["dropped"] += overflow
        # The generator reports the outcome as its final event; parse it here so
        # /status knows whether the rebuild succeeded without replaying the log.
        if event.startswith('data: {"type": "done"'):
            _job["success"] = '"success": true' in event
            if '"kind": "dryrun"' in event:
                _dry_results.pop(job_id, None)
                _dry_results[job_id] = event
                while len(_dry_results) > _DRY_RESULTS_MAX:
                    _dry_results.pop(next(iter(_dry_results)))
        elif event.startswith('data: {"type": "error"'):
            _job["success"] = False
        _job_cv.notify_all()


def _job_finish(job_id):
    with _job_cv:
        if _job is None or _job["id"] != job_id:
            return
        _job["running"]  = False
        _job["finished"] = time.time()
        if _job["success"] is None:
            _job["success"] = False
        _job_cv.notify_all()


# ── Terminal rebuild: reserved from the route to the end of the command ─────
# Until 2026-11-26 the terminal rebuild reserved nothing: once the window was
# open, a move or a dry run with "update flake" could start next to the build
# (Codex re-check). The reservation is now taken before the git staging and
# handed over to the script without a gap (Paket C, refined with ChatGPT):
#
#   1. The script takes `flock` on terminal.lock, then writes <id>.ready.
#   2. NiCo answers in <id>.answer: "go <id>" - or, when the script did not
#      report within TERMINAL_START_TIMEOUT, "dead <id>". The answer file is
#      written in full under a temporary name and linked into place, so it
#      appears complete or not at all, and exists only once: go and dead
#      cannot both happen. The script runs nothing before it has read a
#      complete "go" for its own id; an empty or foreign file is no answer.
#   3. After "go" NiCo waits for the lock. The script held it before it
#      reported, so even a command that ends at once is seen ending - the
#      watcher never has to catch the lock while it is held.
#   4. The script gives the lock back after its last command, before
#      "Press Enter to close": an open window does not hold the config.
#
# A terminal that starts after the timeout finds "dead" and stops before it
# touches the config. A killed script or a closed window releases the lock
# through the kernel.
TERMINAL_START_TIMEOUT = 60.0
_RELEASE = "\x00release"          # marker in the work lines: give the lock back here


def _terminal_dir() -> Path:
    """Where lock, report and answer files live: $XDG_RUNTIME_DIR/nico
    (tmpfs, per user), else the cache directory."""
    runtime = os.environ.get("XDG_RUNTIME_DIR", "")
    if runtime and Path(runtime).is_dir():
        d = Path(runtime) / "nico"
    else:
        cache = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
        d = Path(cache) / "nico" / "run"
    d.mkdir(parents=True, exist_ok=True, mode=0o700)
    return d


def terminal_script(work: list, run_id: str, d: Path) -> list:
    """The whole script: lock, report, wait for "go", the work lines with
    the release point (_RELEASE), clean-up."""
    import shlex as _shlex
    q = _shlex.quote
    lock, ready, answer = d / "terminal.lock", d / f"{run_id}.ready", d / f"{run_id}.answer"
    # The script waits longer than NiCo: NiCo always decides first.
    ticks = int((TERMINAL_START_TIMEOUT + 30) * 10)
    head = [
        "#!/usr/bin/env bash",
        f"_lock={q(str(lock))}; _ready={q(str(ready))}; _answer={q(str(answer))}; _id={run_id}",
        'exec 9>"$_lock"',
        'if ! flock -n 9; then',
        '    echo "NiCo: another rebuild is still running - nothing was started."',
        '    read -rp "Press Enter to close..."; rm -f "$0"; exit 1',
        'fi',
        'printf "%s\\n" "$_id" > "$_ready.tmp" && mv "$_ready.tmp" "$_ready"',
        '_ans=""',
        f'for _i in $(seq 1 {ticks}); do',
        '    if [ -f "$_answer" ]; then',
        '        _ans=$(cat "$_answer")',
        '        case "$_ans" in "go $_id"|"dead $_id") break ;; esac',
        '    fi',
        '    sleep 0.1',
        'done',
        'rm -f "$_ready"',
        'if [ "$_ans" != "go $_id" ]; then',
        '    exec 9>&-',
        '    echo "NiCo: this start has expired - nothing was run. Please start the rebuild again in NiCo."',
        '    read -rp "Press Enter to close..."; rm -f "$0"; exit 1',
        'fi',
    ]
    body = ['exec 9>&-' if line == _RELEASE else line for line in work]
    if _RELEASE not in work:
        body.append('exec 9>&-')
    return head + body + ['rm -f "$_answer" "$0"']


def _terminal_answer(answer: Path, text: str) -> str:
    """Write `text` as the answer unless one exists; returns the answer that
    stands. Complete when visible (written aside, then linked into place),
    and only once (link fails when the name exists)."""
    tmp = answer.with_name(answer.name + f".{uuid.uuid4().hex}.tmp")
    # Not a config file and not atomic.write_bytes: the answer has to be
    # linked into place (exists once), which a rename cannot promise.
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        os.write(fd, (text + "\n").encode("utf-8"))
        os.fsync(fd)
    finally:
        os.close(fd)
    try:
        os.link(tmp, answer)
        return text
    except FileExistsError:
        try:
            return answer.read_text(encoding="utf-8").strip()
        except OSError:
            return ""
    finally:
        tmp.unlink(missing_ok=True)


def _terminal_watch(run_id: str, d: Path) -> None:
    """Hold the reservation the route took until the script is done with
    the config (see the comment above), then give it back."""
    import fcntl
    import sys as _sys
    ready, answer, lock = d / f"{run_id}.ready", d / f"{run_id}.answer", d / "terminal.lock"
    fd = None
    try:
        end = time.monotonic() + TERMINAL_START_TIMEOUT
        reported = False
        while time.monotonic() < end:
            try:
                if ready.read_text(encoding="utf-8").strip() == run_id:
                    reported = True
                    break
            except OSError:
                pass
            time.sleep(0.1)
        # The means to see the end, before the script may start (Codex C3):
        # opened after "go", a failure here gave the reservation back while
        # the build could already run. Cannot be opened → "dead", nothing runs.
        if reported:
            try:
                fd = os.open(lock, os.O_RDWR | os.O_CREAT, 0o600)
            except OSError as exc:
                print(f"NiCo: terminal rebuild not started, cannot watch {lock}: {exc}",
                      file=_sys.stderr)
                reported = False
        go = _terminal_answer(answer, ("go " if reported else "dead ") + run_id) == "go " + run_id
        if go:
            # From here the build may run: the reservation is held until its
            # end is confirmed. A failing look at the lock is tried again,
            # never taken for the end.
            warned = False
            while True:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX)      # returns when the script let go
                    fcntl.flock(fd, fcntl.LOCK_UN)
                    break
                except OSError as exc:
                    if not warned:
                        print(f"NiCo: watching the terminal rebuild failed, trying again: {exc}",
                              file=_sys.stderr)
                        warned = True
                    time.sleep(1.0)
    except Exception:
        pass
    finally:
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass
        release()


def _terminal_tidy(d: Path) -> None:
    """Answer files of starts that expired, older than a day."""
    cutoff = time.time() - 86400
    for f in d.glob("*.answer"):
        try:
            if f.stat().st_mtime < cutoff:
                f.unlink()
        except OSError:
            pass


def register(app, ctx):
    _check_csrf    = ctx["check_csrf"]
    _require_setup = ctx["require_setup"]
    _sudo_nonces   = ctx["sudo_nonces"]
    _time_mod      = ctx["time_mod"]
    _csrf_token    = ctx["csrf_token"]

    @app.route("/api/rebuild/default-host")
    def rebuild_default_host():
        """Return the default host for rebuild (hostname match, saved setting, or null)."""
        # A GET that may write the app settings (revision 2026-11-10, S13).
        if err := _check_csrf(): return err
        nixos_dir, err = _require_setup()
        if err:
            return err
        data = config_manager.load_config(nixos_dir) or {}
        if not config_manager.flake_mode(nixos_dir):
            return jsonify({"default_host": None})

        hosts = _get_flake_hosts(nixos_dir, data)

        # Machine identity always wins: a stored default may have been synced
        # or migrated from another machine and must never override the host
        # NiCo is actually running on.
        import socket as _socket
        machine = _socket.gethostname()
        if machine in hosts:
            return jsonify({"default_host": machine})

        # Fallback for machines whose hostname matches no flake host.
        app_settings = config_manager.get_app_settings()
        saved = (app_settings.get("default_host") or "").strip()
        if saved and saved in hosts:
            return jsonify({"default_host": saved})
        if saved and saved not in hosts:
            config_manager.save_app_settings({"default_host": ""})

        return jsonify({"default_host": None})

    @app.route("/api/rebuild/safe-suggest")
    def rebuild_safe_suggest():
        """Suggested --max-jobs/--cores for the safe rebuild on this machine."""
        return jsonify(buildlimits.suggest())

    @app.route("/api/rebuild/open-terminal", methods=["POST"])
    def rebuild_open_terminal():
        """Build nixos-rebuild command and launch it in a terminal emulator.

        Reserved like the monitored rebuild, from before the git staging to
        the end of the last command in the terminal (_terminal_watch)."""
        if err := _check_csrf(): return err
        nixos_dir, err = _require_setup()
        if err:
            return err
        import shutil as _shutil_f
        # Without flock the end of the command cannot be seen; the rebuild in
        # the monitor needs none (decision Martin 2026-11-26).
        if not _shutil_f.which("flock"):
            return jsonify({"error": "ERR_NO_FLOCK"}), 409
        busy = reserve("rebuild")
        if busy:
            return jsonify({"error": busy}), 409
        handed_over = False
        try:
            resp = _open_terminal_reserved(nixos_dir)
            handed_over = bool(getattr(resp, "_nico_handed_over", False))
            return resp
        finally:
            if not handed_over:
                release()

    def _open_terminal_reserved(nixos_dir):
        """rebuild_open_terminal with the reservation held; hands it to the
        watcher once the terminal is started."""
        # Same collision as in rebuild_start: the terminal would run into
        # "unit was already loaded" and the user would have to read a traceback
        # in a window NiCo cannot explain.
        if switch_unit_busy():
            return jsonify({"error": "ERR_SWITCH_RUNNING"}), 409

        import shlex as _shlex
        import stat as _stat
        import subprocess as _sp
        import tempfile as _tempfile
        import shutil as _shutil2

        body = request.get_json(silent=True) or {}
        data = config_manager.load_config(nixos_dir) or {}
        use_flake = config_manager.flake_mode(nixos_dir)

        hostname          = (body.get("hostname") or "").strip()
        mode              = (body.get("mode") or "switch").strip()
        update_flake      = bool(body.get("update_flake", False)) and use_flake
        push_shutdown     = bool(body.get("push_shutdown_after", False))
        shutdown_after    = bool(body.get("shutdown_after", False)) and not push_shutdown
        safe_mode         = bool(body.get("safe_mode", False))

        if mode not in ('switch', 'boot', 'test'):
            return jsonify({"error": "ERR_INVALID_MODE"}), 400

        nixos_path = Path(nixos_dir).resolve()

        stage_before_rebuild = False
        if use_flake:
            is_repo = git_manager.is_git_repo(nixos_dir)
            # Same staging as the SSE rebuild: flake eval in a git repo
            # only sees tracked files.
            stage_before_rebuild = is_repo
            flake_arg = prebuild.flake_ref(nixos_dir, hostname, is_repo)
            rebuild_cmd = f"sudo nixos-rebuild {mode} --flake {_shlex.quote(flake_arg)}"
        else:
            conf_path = nixos_path / "configuration.nix"
            rebuild_cmd = f"sudo nixos-rebuild {mode} -I nixos-config={_shlex.quote(str(conf_path))}"

        if safe_mode:
            jobs, cores = buildlimits.resolve(body.get("safe_jobs"), body.get("safe_cores"),
                                              config_manager.get_app_settings())
            rebuild_cmd += f" --max-jobs {jobs} --cores {cores}"

        # Staged here, through the same routine as the monitor: the script
        # used to run a bare `git add -A`, past the foreign-file guard and
        # the size limit (Codex re-check 2026-11-20).
        if stage_before_rebuild:
            staged, stage_msg = git_manager.stage_all(nixos_dir)
            if not staged:
                return jsonify({"error": stage_msg}), 500

        script_lines = [
            "#!/usr/bin/env bash",
            f"cd {_shlex.quote(str(nixos_path))}",
        ]
        if shutdown_after or push_shutdown:
            script_lines.append("_rebuild_ok=0")
            if update_flake:
                script_lines += [
                    "if nix flake update; then",
                    f"    {rebuild_cmd} && _rebuild_ok=1",
                    "else",
                    "    echo 'nix flake update failed'",
                    "fi",
                ]
            else:
                script_lines.append(f"{rebuild_cmd} && _rebuild_ok=1")
            if push_shutdown:
                script_lines += [
                    'if [ "$_rebuild_ok" -eq 1 ]; then',
                    '    git push',
                    '    exec 9>&-',
                    '    sudo shutdown -h now',
                    'else',
                    '    exec 9>&-',
                    '    echo',
                    '    read -rp "Rebuild failed. Press Enter to close..."',
                    'fi',
                ]
            else:
                script_lines += [
                    'exec 9>&-',
                    'if [ "$_rebuild_ok" -eq 1 ]; then',
                    '    sudo shutdown -h now',
                    'else',
                    '    echo',
                    '    read -rp "Press Enter to close..."',
                    'fi',
                ]
        else:
            if update_flake:
                script_lines.append("nix flake update")
                script_lines.append(f"[ $? -eq 0 ] && {rebuild_cmd} || echo 'nix flake update failed'")
            else:
                script_lines.append(rebuild_cmd)
            script_lines += [
                _RELEASE,
                'echo',
                'read -rp "Press Enter to close..."',
            ]
        run_id = uuid.uuid4().hex
        tdir = _terminal_dir()
        _terminal_tidy(tdir)
        # The work lines run inside the handshake (terminal_script); the
        # first one changes into the config.
        script_lines = terminal_script(script_lines[1:], run_id, tdir)

        fd, script_path = _tempfile.mkstemp(suffix=".sh", prefix="nico-rebuild-")
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as f:
                f.write('\n'.join(script_lines) + '\n')
            os.chmod(script_path, _stat.S_IRWXU)
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

        TERMINALS = [
            ['konsole', '-e', 'bash', script_path],
            ['xterm', '-e', 'bash', script_path],
            ['alacritty', '-e', 'bash', script_path],
            ['kitty', 'bash', script_path],
            ['gnome-terminal', '--', 'bash', script_path],
            ['xfce4-terminal', '-e', f'bash {script_path}'],
        ]
        for term_cmd in TERMINALS:
            if _shutil2.which(term_cmd[0]):
                try:
                    _sp.Popen(term_cmd)
                except Exception:
                    continue
                threading.Thread(target=_terminal_watch, args=(run_id, tdir),
                                 name=f"terminal-{run_id[:8]}", daemon=True).start()
                resp = jsonify({"success": True})
                resp._nico_handed_over = True
                return resp

        try:
            os.unlink(script_path)
        except OSError:
            pass
        return jsonify({"error": "ERR_NO_TERMINAL"}), 500

    @app.route("/api/rebuild/start", methods=["POST"])
    def rebuild_start():
        """
        Start a rebuild in a background thread and buffer its SSE events.

        The response only confirms the start; the output is read via
        /api/rebuild/stream, which can be (re-)attached at any time.

        JSON body:
          mode         – "switch" | "boot" | "test"  (default: "switch")
          hostname     – flake host to build (optional)
          sudo_nonce   – nonce for the cached sudo password (optional)
          update_flake – run `nix flake update` first (flake mode only)
          safe_mode    – limit jobs/cores
          safe_jobs    – --max-jobs for the safe rebuild (optional)
          safe_cores   – --cores for the safe rebuild (optional; without
                         them: app settings, then buildlimits.suggest)
          verbose      – show the detail messages of nix (optional; without
                         it: app setting rebuild_verbose, default off).
                         Can be changed while the job runs:
                         /api/rebuild/verbose

        SSE event types produced by the job:
          {"type": "output",   "line": "..."}
          {"type": "phase",    "phase": "evaluating|fetching|building|activating", "active": true|false, "pkg": "..."}
          {"type": "progress", "done": N, "total": M, "pkg": "..."}
          {"type": "done",     "success": true|false, "exit_code": N}
          {"type": "error",    "message": "..."}

        Architecture note: mode is already a parameter so switch/boot/test
        can be added to the UI later without touching this endpoint.
        """
        if err := _check_csrf(): return err

        payload = request.get_json(silent=True) or {}

        mode = payload.get('mode') or 'switch'
        if mode not in ('switch', 'boot', 'test'):
            return jsonify({"error": "ERR_INVALID_MODE"}), 400

        nixos_dir, err = _require_setup()
        if err:
            return err

        # A second start while the first is still being set up gets the
        # same answer as one during the build; a move, dry run, update …
        # in progress names itself (reserve()).
        busy = reserve("rebuild")
        if busy:
            return jsonify({"error": busy}), 409
        try:
            return _start_locked(payload, mode, nixos_dir)
        finally:
            release()

    def _start_locked(payload, mode, nixos_dir):
        """rebuild_start from the running-check on; _start_lock is held."""

        import json as _json
        import subprocess as _sp
        import re as _re

        with _job_cv:
            if _job is not None and _job["running"]:
                return jsonify({"error": "ERR_REBUILD_RUNNING"}), 409

        # A switch from an earlier NiCo run can still be active – the job state
        # above lives in this process only and is gone after a restart.
        if switch_unit_busy():
            return jsonify({"error": "ERR_SWITCH_RUNNING"}), 409

        data      = config_manager.load_config(nixos_dir) or {}
        conf_path = Path(nixos_dir) / "configuration.nix"
        use_flake = config_manager.flake_mode(nixos_dir)

        # hostname: request value takes precedence; fall back to nico.json
        hostname_param = str(payload.get('hostname') or '').strip()
        if hostname_param and re.fullmatch(r'[\w.-]+', hostname_param):
            hostname = hostname_param
        else:
            hostname = (data.get("hostname") or "nixos").strip() or "nixos"

        # Sudo-Passwort via Nonce holen
        sudo_nonce = str(payload.get('sudo_nonce') or '')
        sudo_password = ''
        if sudo_nonce and sudo_nonce in _sudo_nonces:
            pw, expiry = _sudo_nonces.pop(sudo_nonce)
            if expiry > _time_mod.time():
                sudo_password = pw

        update_flake = bool(payload.get('update_flake')) and use_flake
        safe_mode    = bool(payload.get('safe_mode'))

        # Download size from /api/rebuild/precheck, when the interface ran it
        # just before. Then the dry build is not repeated here – evaluating
        # the config twice in a row costs time and gives the same answer.
        prefetch_mib = payload.get('prefetch_mib')
        prefetch_mib = (float(prefetch_mib)
                        if isinstance(prefetch_mib, (int, float)) and prefetch_mib >= 0
                        else None)

        flake_arg = ""
        if use_flake:
            flake_arg = prebuild.flake_ref(nixos_dir, hostname,
                                           git_manager.is_git_repo(nixos_dir))
        cmd = rebuild_cmd(mode, flake_arg, conf_path)

        app_settings        = config_manager.get_app_settings()
        if safe_mode:
            jobs, cores = buildlimits.resolve(payload.get('safe_jobs'),
                                              payload.get('safe_cores'), app_settings)
            cmd += ["--max-jobs", str(jobs), "--cores", str(cores)]

        # Detail messages: what the dialog sent, else the remembered choice.
        verbose = payload.get('verbose')
        if not isinstance(verbose, bool):
            verbose = bool(app_settings.get("rebuild_verbose", False))

        rebuild_log_on      = bool(app_settings.get("rebuild_log", False))
        prefetch_dry_run_on = bool(app_settings.get("prefetch_dry_run", True))
        log_path       = config_manager.REBUILD_LOG_FILE
        # Snapshot before the build so only what this run broke is reported.
        units_before   = failed_unit_names()

        def _generate():
            # Tracks active nix activities: {id: {'phase', 'pkg', 'nix_type', 'dl_done', 'dl_expected'}}
            active_acts = {}
            # Aggregated download bytes across all substitute activities
            dl_state    = DownloadTally()
            # Build derivation counter; 'aggregate' switches to Builds-activity tracking
            build_state = {'done': 0, 'total': 0, 'aggregate': False}
            # Global CLI-style progress, preferred when present
            global_state = {
                'available': False,
                'built_done': 0,
                'built_total': 0,
                'copied_done': 0,
                'copied_total': 0,
                'copied_label': '',
                'copied_expected': 0,
                'dl_done': 0,
                'dl_expected': 0,
            }
            # Buffer for log output
            log_lines   = _LogBuffer()
            # Collected apart from it, never truncated – see _ErrorLines
            err_lines   = _ErrorLines()
            # Outcome of the activation phase – see _Activation
            activation  = _Activation()
            # nixos-rebuild's debug lines and tracebacks – see _OwnChatter
            chatter     = _OwnChatter()

            # nix activity types
            ACT_BUILD         = 105  # individual derivation build
            ACT_SUBSTITUTE    = 108  # substituting path from binary cache (phase detection only)
            ACT_FILE_TRANSFER = 101  # HTTP file transfer (compressed download bytes)
            ACT_BUILDS        = 104  # aggregate builds tracker – resProgress gives total/done

            def _pkg_from_text(text):
                m = _re.search(r'/nix/store/[a-z0-9]+-([^/\s\'.]+)', text)
                return m.group(1) if m else ''

            def _emit_phase(phase, active, pkg=''):
                return f"data: {_json.dumps({'type': 'phase', 'phase': phase, 'active': active, 'pkg': pkg})}\n\n"

            def _emit_progress():
                building_pkgs = [v['pkg'] for v in active_acts.values()
                                 if v['phase'] == 'building' and v['pkg']]
                pkg = building_pkgs[0] if building_pkgs else ''
                return f"data: {_json.dumps({'type': 'progress', 'done': build_state['done'], 'total': build_state['total'], 'pkg': pkg})}\n\n"

            def _emit_dl():
                return f"data: {_json.dumps({'type': 'dl_progress', 'done': dl_state.done, 'expected': dl_state.expected})}\n\n"

            def _emit_global_progress():
                payload = {
                    'type': 'global_progress',
                    'built_done': global_state['built_done'],
                    'built_total': global_state['built_total'],
                    'copied_done': global_state['copied_done'],
                    'copied_total': global_state['copied_total'],
                    'copied_label': global_state['copied_label'],
                    'copied_expected': global_state['copied_expected'],
                    'dl_done': global_state['dl_done'],
                    'dl_expected': global_state['dl_expected'],
                }
                return f"data: {_json.dumps(payload)}\n\n"

            def _parse_size_to_bytes(value, unit):
                unit_map = {
                    'B': 1,
                    'KB': 1000, 'MB': 1000**2, 'GB': 1000**3, 'TB': 1000**4,
                    'KIB': 1024, 'MIB': 1024**2, 'GIB': 1024**3, 'TIB': 1024**4,
                }
                mult = unit_map.get((unit or 'B').upper())
                if mult is None:
                    return None
                try:
                    return int(float(value) * mult)
                except (TypeError, ValueError):
                    return None

            def _parse_bracket_progress(line):
                """
                Parse CLI-style aggregate progress, e.g.
                [53/312 built, 16/599/953 copied (6.7/15.9 GiB), 1.4/4.0 GiB DL]
                """
                m = _re.match(r'^\[([^\]]+)\]', line)
                if not m:
                    return None

                summary = m.group(1)
                result = {}

                built_match = _re.search(r'(\d+)\s*/\s*(\d+)\s+built\b', summary, _re.I)
                if built_match:
                    result['built_done'] = int(built_match.group(1))
                    result['built_total'] = int(built_match.group(2))

                copied_match = _re.search(
                    r'(\d+)\s*/\s*(\d+)(?:\s*/\s*(\d+))?\s+copied\b(?:\s*\(\s*([0-9.]+)\s*/\s*([0-9.]+)\s*([KMGT]?i?B)\s*\))?',
                    summary,
                    _re.I,
                )
                if copied_match:
                    result['copied_done'] = int(copied_match.group(1))
                    result['copied_total'] = int(copied_match.group(2))
                    copied_counts = [copied_match.group(1), copied_match.group(2)]
                    if copied_match.group(3):
                        copied_counts.append(copied_match.group(3))
                    result['copied_label'] = '/'.join(copied_counts)
                    if copied_match.group(4) and copied_match.group(5) and copied_match.group(6):
                        copied_done = _parse_size_to_bytes(copied_match.group(4), copied_match.group(6))
                        copied_expected = _parse_size_to_bytes(copied_match.group(5), copied_match.group(6))
                        if copied_done is not None:
                            result['copied_bytes_done'] = copied_done
                        if copied_expected is not None:
                            result['copied_expected'] = copied_expected

                dl_match = _re.search(r'([0-9.]+)\s*/\s*([0-9.]+)\s*([KMGT]?i?B)\s+DL\b', summary, _re.I)
                if dl_match:
                    dl_done = _parse_size_to_bytes(dl_match.group(1), dl_match.group(3))
                    dl_expected = _parse_size_to_bytes(dl_match.group(2), dl_match.group(3))
                    if dl_done is not None:
                        result['dl_done'] = dl_done
                    if dl_expected is not None:
                        result['dl_expected'] = dl_expected

                return result or None

            def _parse_nix_line(json_str):
                """Parse a @nix JSON line; yield SSE event strings."""
                try:
                    data = _json.loads(json_str)
                except Exception:
                    yield f"data: {_json.dumps({'type': 'output', 'line': json_str})}\n\n"
                    return

                action = data.get('action')

                if action == 'msg':
                    msg_text = data.get('msg', '')
                    if msg_text:
                        err_lines.consider_msg(data.get('level'), msg_text)
                        yield f"data: {_json.dumps({'type': 'output', 'line': msg_text})}\n\n"

                elif action == 'start':
                    act_id   = data.get('id')
                    nix_type = data.get('type', 0)
                    text     = data.get('text', '')

                    # nix_type takes priority for build/fetch; text handles evaluating + fallback
                    if nix_type == ACT_BUILD:
                        phase = 'building'
                        pkg   = _pkg_from_text(text)
                    elif nix_type == ACT_SUBSTITUTE:
                        phase, pkg = 'fetching', ''
                    elif _re.search(r'evaluating', text, _re.I):
                        phase, pkg = 'evaluating', ''
                    elif _re.search(r"building '?/nix/store/", text, _re.I):
                        phase = 'building'
                        pkg   = _pkg_from_text(text)
                    elif _re.search(r'fetch|download|copy', text, _re.I):
                        phase, pkg = 'fetching', ''
                    else:
                        phase, pkg = None, ''

                    if act_id is not None:
                        was_active = phase and any(v['phase'] == phase for v in active_acts.values())
                        active_acts[act_id] = {
                            'phase': phase, 'pkg': pkg, 'nix_type': nix_type,
                            'dl_done': 0, 'dl_expected': 0,
                        }
                        if phase and not was_active:
                            yield _emit_phase(phase, True, pkg)
                        if nix_type == ACT_BUILD:
                            if not build_state['aggregate']:
                                build_state['total'] += 1
                            if build_state['total'] > 0:
                                yield _emit_progress()

                elif action == 'stop':
                    act_id = data.get('id')
                    if act_id in active_acts:
                        act      = active_acts.pop(act_id)
                        nix_type = act['nix_type']

                        if nix_type == ACT_FILE_TRANSFER:
                            # Finished: its bytes stay counted (DownloadTally).
                            dl_state.stop(act_id)
                            if dl_state.expected > 0:
                                yield _emit_dl()

                        if nix_type == ACT_BUILD:
                            if not build_state['aggregate']:
                                build_state['done'] += 1
                            if build_state['total'] > 0:
                                yield _emit_progress()

                        stopped_phase = act['phase']
                        if stopped_phase:
                            still_active = any(v['phase'] == stopped_phase for v in active_acts.values())
                            if not still_active:
                                yield _emit_phase(stopped_phase, False)

                elif action == 'result':
                    result_type = data.get('type')
                    act_id      = data.get('id')
                    fields      = data.get('fields', [])

                    if result_type in (_RES_BUILD_LOG, _RES_POST_BUILD_LOG):
                        # What the builder itself wrote. These were dropped
                        # unread, which is why a failing test suite was
                        # invisible in the monitor: nix only ever says
                        # `1 dependency failed`, the package name and the
                        # assertion live here.
                        #
                        # Only the lines that announce a failure are taken.
                        # Forwarding all of them would mean the full compile
                        # output of every package in the log – nix itself
                        # shows these on the terminal only with `-L` – and
                        # would push the one line that matters out of the
                        # 5 MB all the faster.
                        text = _build_log_text(data)
                        if not text.strip():
                            return
                        pkg = (active_acts.get(act_id) or {}).get('pkg') or ''
                        if err_lines.consider_build(text, pkg):
                            label = f"{pkg}> " if pkg else "> "
                            yield f"data: {_json.dumps({'type': 'output', 'line': label + _RE_ANSI.sub('', text).rstrip()})}\n\n"
                        return

                    if result_type == 105:  # resProgress: [done, expected, running, failed]
                        act = active_acts.get(act_id)
                        if act is None or len(fields) < 2:
                            return
                        done, expected = fields[0], fields[1]
                        if act['nix_type'] == ACT_FILE_TRANSFER and isinstance(done, int) and isinstance(expected, int):
                            # Compressed download bytes from HTTP file transfer
                            dl_state.progress(act_id, done, expected)
                            act['dl_done']        = done
                            act['dl_expected']    = expected
                            if dl_state.expected > 0:
                                yield _emit_dl()
                        elif act['nix_type'] == ACT_BUILDS and isinstance(done, int) and isinstance(expected, int):
                            # Aggregate build counter from Builds activity – available before actBuild starts
                            build_state['aggregate'] = True
                            build_state['done']  = done
                            if expected > 0:
                                build_state['total'] = expected
                            if build_state['total'] > 0:
                                yield _emit_progress()

            try:
                # Flake eval in a git repo only sees tracked files – stage new
                # files so `.#host` matches the on-disk state. Non-flake builds
                # never need staging, so git is left untouched there.
                staged_with_git = False
                stage_msg = ""
                if use_flake:
                    try:
                        staged_with_git, stage_msg = git_manager.stage_all(nixos_dir)
                    except Exception as exc:
                        staged_with_git, stage_msg = False, str(exc) or "ERR_GIT_FAILED"

                if staged_with_git:
                    yield f"data: {_json.dumps({'type': 'output', 'line': '── git add ──'})}\n\n"
                elif stage_msg and stage_msg != git_manager.ERR_NO_GIT_REPO:
                    # The flake would be built from a half-staged index – stop
                    # before nix (Codex re-check 2026-11-20).
                    yield f"data: {_json.dumps({'type': 'error', 'message': stage_msg})}\n\n"
                    return
                elif stage_msg:
                    # Log lines are tool output and stay English, like the
                    # ── nixos-rebuild ── headers around them and nix's own
                    # lines. stage_all only fails this way when there is no
                    # repository; the raw error code used to land here.
                    yield f"data: {_json.dumps({'type': 'output', 'line': '── git add skipped: not a git repository ──'})}\n\n"

                if update_flake:
                    yield f"data: {_json.dumps({'type': 'output', 'line': '── nix flake update ──'})}\n\n"
                    upd = _sp.Popen(
                        ["nix", "flake", "update"],
                        cwd=nixos_dir,
                        stdout=_sp.PIPE,
                        stderr=_sp.STDOUT,
                        text=True,
                        bufsize=1,
                    )
                    for raw_line in upd.stdout:
                        yield f"data: {_json.dumps({'type': 'output', 'line': raw_line.rstrip()})}\n\n"
                    upd.wait()
                    if upd.returncode != 0:
                        yield f"data: {_json.dumps({'type': 'error', 'message': chr(0x1f).join(['ERR_FLAKE_UPDATE_FAILED', str(upd.returncode)])})}\n\n"
                        return
                    yield f"data: {_json.dumps({'type': 'output', 'line': '── nixos-rebuild ──'})}\n\n"

                # Pre-fetch dry-run: evaluate dependency graph to get stable
                # total download size. Skipped when the precheck already did.
                # The planned size goes out as soon as it is known – also 0,
                # which ends the "being determined" note in the monitor.
                if prefetch_mib is not None:
                    yield f"data: {_json.dumps({'type': 'prefetch_total', 'mib': prefetch_mib})}\n\n"
                elif prefetch_dry_run_on:
                    yield f"data: {_json.dumps({'type': 'prefetch_pending'})}\n\n"
                    # Runs after `nix flake update`, so no lock-file trick here.
                    dr_cmd = prebuild.dry_build_cmd(nixos_dir, use_flake, flake_arg)
                    try:
                        yield _emit_phase('analysing', True)
                        dr = _sp.run(dr_cmd, cwd=nixos_dir, capture_output=True,
                                     text=True, timeout=prebuild.DRY_BUILD_TIMEOUT)
                        _, total_mib = prebuild.parse_dry_build(
                            (dr.stdout or "") + "\n" + (dr.stderr or ""))
                        yield _emit_phase('analysing', False)
                        yield f"data: {_json.dumps({'type': 'prefetch_total', 'mib': total_mib})}\n\n"
                    except Exception:
                        yield _emit_phase('analysing', False)
                        yield f"data: {_json.dumps({'type': 'prefetch_total', 'mib': 0})}\n\n"

                # What the build reads, to tell at the end whether the config
                # changed meanwhile (decision Martin 2026-11-26).
                from .. import configfiles
                state_before = configfiles.nix_state(nixos_dir)
                proc = _sp.Popen(
                    cmd,
                    cwd=nixos_dir,       # flake.nix muss im cwd liegen
                    stdin=_sp.PIPE,
                    stdout=_sp.PIPE,
                    stderr=_sp.STDOUT,   # merge stderr into stdout
                    text=True,
                    bufsize=1,           # line-buffered
                )
                # Passwort an sudo -S senden und stdin schließen
                proc.stdin.write((sudo_password + "\n") if sudo_password else "")
                proc.stdin.flush()
                proc.stdin.close()
                for raw_line in proc.stdout:
                    line = raw_line.rstrip('\r\n')
                    if line.startswith('@nix '):
                        # Detail messages: dropped here, before log and
                        # monitor, unless the switch is on right now.
                        if is_detail_msg(line[5:]) and not _job_verbose():
                            continue
                        log_lines.append(line)
                        yield from _parse_nix_line(line[5:])
                    else:
                        log_lines.append(line)
                        # Everything outside the JSON stream: nixos-rebuild's
                        # own messages and the whole activation phase.
                        err_lines.consider_raw(line)
                        activation.consider(line)
                        progress = _parse_bracket_progress(line)
                        if progress:
                            global_state['available'] = True
                            if 'built_done' in progress:
                                global_state['built_done'] = progress['built_done']
                            if 'built_total' in progress:
                                global_state['built_total'] = progress['built_total']
                            if 'copied_done' in progress:
                                global_state['copied_done'] = progress['copied_done']
                            if 'copied_total' in progress:
                                global_state['copied_total'] = progress['copied_total']
                            if 'copied_label' in progress:
                                global_state['copied_label'] = progress['copied_label']
                            if 'copied_expected' in progress:
                                global_state['copied_expected'] = progress['copied_expected']
                            if 'dl_done' in progress:
                                global_state['dl_done'] = progress['dl_done']
                            if 'dl_expected' in progress:
                                global_state['dl_expected'] = progress['dl_expected']
                            yield _emit_global_progress()
                        # Asked for every line, so a traceback is followed
                        # to its end even while everything is shown.
                        own_detail = chatter.hidden(line)
                        if not own_detail or _job_verbose():
                            yield f"data: {_json.dumps({'type': 'output', 'line': line})}\n\n"
                        # Activating phase detected from raw output (activation scripts
                        # run outside of nix's logger and write directly to stderr)
                        if _re.search(r'activating the configuration', line, _re.I):
                            yield _emit_phase('activating', True)
                proc.wait()
                success = proc.returncode == 0
                log_written = False
                if not success or rebuild_log_on:
                    log_written = _write_rebuild_log(log_path, log_lines, err_lines)
                if not success:
                    yield f"data: {_json.dumps({'type': 'output', 'line': f'── log saved: {log_path} ──'})}\n\n"
                # Asked in both outcomes: a unit that fails quietly after a
                # successful rebuild is the one nobody notices.
                units = failed_units(exclude=units_before)
                # Built and switched, only some units did not restart: its own
                # outcome, shown as a warning. `success` stays the exit code.
                activated_warn = (not success) and activation.partial
                changed_during = configfiles.nix_state(nixos_dir) != state_before
                yield f"data: {_json.dumps({'type': 'done', 'success': success, 'exit_code': proc.returncode, 'log_written': log_written, 'failed_units': units, 'error_lines': err_lines.lines, 'error_lines_dropped': err_lines.dropped, 'activated_with_warnings': activated_warn, 'unrestarted_units': activation.units if activated_warn else [], 'changed_during': changed_during})}\n\n"
            except FileNotFoundError:
                msg = "ERR_NO_NIXOS_REBUILD"   # translated in the interface
                yield f"data: {_json.dumps({'type': 'error', 'message': msg})}\n\n"
            except Exception as exc:
                yield f"data: {_json.dumps({'type': 'error', 'message': str(exc)})}\n\n"

        job_id = _start_job(_generate, mode=mode, hostname=hostname if use_flake else "",
                            verbose=verbose)
        return jsonify({"success": True, "job_id": job_id})

    # ── Time machine, System side: generations (2026-11-21) ──────────────────

    @app.route("/api/generations")
    def generations_list():
        """The system states (generations) of this machine, newest first.

        Only with the release hostread.GENERATIONS – without it nothing is
        read and the answer says so, for the release button.
        """
        from .. import generations, hostread
        if not hostread.allowed(hostread.GENERATIONS):
            return jsonify({"allowed": False, "generations": []})
        gens = generations.list_generations()
        # Entered, but the activation failed (F8): the state marked for the
        # next start is not what the boot menu holds - offer the retry,
        # as long as the profile still points to it.
        retry = _activation_retry
        if retry and not any(g.default and g.number == retry["number"] for g in gens):
            retry = None
        return jsonify({"allowed": True, "generations": generations.as_dicts(gens),
                        "retry": retry})

    @app.route("/api/generations/switch", methods=["POST"])
    def generations_switch():
        """Make a generation the system state, in the rebuild monitor.

        JSON body: number (a generation from the list), mode "switch" (now
        and from the next start) or "boot" (from the next start),
        sudo_nonce. No separate rollback (Martin, 2026-11-21): the previous
        state is one row in the list like any other.

        Runs as a job like the rebuild – the same lock, so it never runs next
        to a rebuild, the same monitor, which survives a reload. The config
        files are not touched; the next rebuild builds from them again.
        """
        if err := _check_csrf(): return err
        nixos_dir, err = _require_setup()
        if err:
            return err
        from .. import generations, hostread
        if not hostread.allowed(hostread.GENERATIONS):
            return jsonify({"error": hostread.ERR_OFF}), 403
        payload = request.get_json(silent=True) or {}
        mode = payload.get("mode") if payload.get("mode") in generations.MODES else None
        if mode is None:
            return jsonify({"error": "ERR_INVALID_ACTION"}), 400
        gens = generations.list_generations()
        raw = payload.get("number")
        number = raw if isinstance(raw, int) and not isinstance(raw, bool) else None
        if number is None or number not in {g.number for g in gens}:
            return jsonify({"error": "ERR_GENERATION_UNKNOWN"}), 400
        # "Activate again" (F8): only the second step, and only for the state
        # the profile already points to.
        if payload.get("activate_only") and not any(g.default and g.number == number
                                                    for g in gens):
            return jsonify({"error": "ERR_GENERATION_UNKNOWN"}), 400
        busy = reserve("generation")
        if busy:
            return jsonify({"error": busy}), 409
        try:
            return _start_generation(payload, number, mode, nixos_dir)
        finally:
            release()

    def _start_generation(payload, number: int, mode: str, nixos_dir: str):
        import json as _json
        import subprocess as _sp
        from .. import generations

        with _job_cv:
            if _job is not None and _job["running"]:
                return jsonify({"error": "ERR_REBUILD_RUNNING"}), 409
        if switch_unit_busy():
            return jsonify({"error": "ERR_SWITCH_RUNNING"}), 409

        sudo_nonce = str(payload.get("sudo_nonce") or "")
        sudo_password = ""
        if sudo_nonce and sudo_nonce in _sudo_nonces:
            pw, expiry = _sudo_nonces.pop(sudo_nonce)
            if expiry > _time_mod.time():
                sudo_password = pw
        activate_only = bool(payload.get("activate_only"))
        commands = ([generations.activate_command(mode)] if activate_only
                    else generations.switch_commands(number, mode))
        units_before = failed_unit_names()

        def _out(line):
            return f"data: {_json.dumps({'type': 'output', 'line': line})}\n\n"

        def _generate():
            global _activation_retry
            yield _out(f"── system state {number} ({mode}) ──")
            activation = _Activation()
            ok = True
            # Two steps, reported apart (revision 2026-11-25, F8): entered in
            # the profile, then activated. When only the first one worked,
            # the list marks the state for the next start while the boot
            # menu still holds the old one - the monitor says so, and the
            # list offers "activate again".
            entered = activate_only
            failed_step = ""
            for cmd in commands:
                yield _out("$ " + " ".join(c for c in cmd if c != "-S"))
                if "switch-to-configuration" in cmd[2]:
                    yield f"data: {_json.dumps({'type': 'phase', 'phase': 'activating', 'active': True, 'pkg': ''})}\n\n"
                try:
                    proc = _sp.Popen(cmd, cwd=nixos_dir, stdin=_sp.PIPE, stdout=_sp.PIPE,
                                     stderr=_sp.STDOUT, text=True, bufsize=1)
                except FileNotFoundError:
                    yield f"data: {_json.dumps({'type': 'error', 'message': 'ERR_GENERATION_TOOL'})}\n\n"
                    return
                proc.stdin.write((sudo_password + "\n") if sudo_password else "")
                proc.stdin.flush()
                proc.stdin.close()
                for raw_line in proc.stdout:
                    line = raw_line.rstrip("\r\n")
                    activation.consider(line)
                    yield _out(line)
                proc.wait()
                if proc.returncode != 0:
                    ok = False
                    failed_step = "activate" if "switch-to-configuration" in cmd[2] else "enter"
                    break
                if "switch-to-configuration" not in cmd[2]:
                    entered = True
            if ok:
                _activation_retry = None
            elif entered and failed_step == "activate":
                _activation_retry = {"number": number, "mode": mode}
            activated_warn = (not ok) and activation.partial
            yield ("data: " + _json.dumps({
                "type": "done", "success": ok, "kind": "generation",
                "number": number, "mode": mode,
                "entered": entered, "failed_step": failed_step,
                "failed_units": failed_units(exclude=units_before),
                "error_lines": [], "error_lines_dropped": 0,
                "activated_with_warnings": activated_warn,
                "unrestarted_units": activation.units if activated_warn else [],
            }) + "\n\n")

        job_id = _start_job(_generate, mode=f"generation-{mode}")
        return jsonify({"success": True, "job_id": job_id})

    # ── Updating an installed NiCo (2026-11-22) ──────────────────────────────

    @app.route("/api/selfupdate/status")
    def selfupdate_status():
        """Whether this user has NiCo installed with `nix profile` – only
        then the help menu offers the update."""
        from .. import selfupdate
        # Its own path only - `nix profile list` ran here on every page load
        # without asking (revision 2026-11-25, S3). Which profile entry it
        # is, is asked on the click (selfupdate_start).
        return jsonify({"installed": selfupdate.running_store_path() is not None})

    @app.route("/api/selfupdate/entry")
    def selfupdate_entry():
        """The profile entry of this running NiCo, for the question before
        the update (where it is fetched from). Reads `nix profile list`, so
        only with the release hostread.PROFILE (always or once)."""
        from .. import hostread, selfupdate
        if not hostread.allowed(hostread.PROFILE):
            return jsonify({"error": hostread.ERR_OFF, "kind": hostread.PROFILE}), 403
        store = selfupdate.running_store_path()
        entry = selfupdate.profile_entry(store) if store else None
        if entry is None:
            return jsonify({"error": "ERR_NOT_INSTALLED"}), 400
        return jsonify({"name": entry[0], "url": entry[1]})

    @app.route("/api/selfupdate", methods=["POST"])
    def selfupdate_start():
        """`nix profile upgrade <name> --refresh` in the rebuild monitor.

        A job like the rebuild – the same lock, so it never runs next to a
        rebuild or a switch, and the same monitor. No root and no config
        directory needed: the user profile belongs to the user. The running
        NiCo keeps running; the new version is used after a restart.
        """
        if err := _check_csrf(): return err
        import json as _json
        import subprocess as _sp
        from .. import selfupdate

        from .. import hostread
        # Reading the installed programs is reading this machine: only with
        # the release, always or once (hostread.py, Martin 2026-11-26).
        if not hostread.allowed(hostread.PROFILE):
            return jsonify({"error": hostread.ERR_OFF, "kind": hostread.PROFILE}), 403
        # The entry this running NiCo belongs to, nothing else (W1).
        store = selfupdate.running_store_path()
        entry = selfupdate.profile_entry(store) if store else None
        if entry is None:
            return jsonify({"error": "ERR_NOT_INSTALLED"}), 400
        cmd = selfupdate.upgrade_command(entry[0])

        def _out(line):
            return f"data: {_json.dumps({'type': 'output', 'line': line})}\n\n"

        def _generate():
            yield _out("$ " + " ".join(cmd))
            try:
                proc = _sp.Popen(cmd, cwd=str(Path.home()), stdin=_sp.DEVNULL,
                                 stdout=_sp.PIPE, stderr=_sp.STDOUT, text=True, bufsize=1)
            except FileNotFoundError:
                yield f"data: {_json.dumps({'type': 'error', 'message': 'ERR_NIX_MISSING'})}\n\n"
                return
            for raw_line in proc.stdout:
                yield _out(raw_line.rstrip("\r\n"))
            proc.wait()
            yield ("data: " + _json.dumps({
                "type": "done", "success": proc.returncode == 0, "kind": "selfupdate",
                "failed_units": [], "error_lines": [], "error_lines_dropped": 0,
                "activated_with_warnings": False, "unrestarted_units": [],
            }) + "\n\n")

        busy = reserve("selfupdate")
        if busy:
            return jsonify({"error": busy}), 409
        try:
            job_id = _start_job(_generate, mode="selfupdate")
        finally:
            release()
        return jsonify({"success": True, "job_id": job_id})

    @app.route("/api/rebuild/precheck", methods=["POST"])
    def rebuild_precheck():
        """Forecast for the rebuild about to start: download size and the
        packages that will be compiled locally (see prebuild.py).

        Called by the interface after writing and the Validator, so it looks
        at the written state like every dry run (vorgaben.txt). Answers
        {"skipped": true} when the pre-rebuild analysis is switched off in
        the admin area.

        JSON body: hostname, update_flake – the same as for /start.
        """
        if err := _check_csrf(): return err
        nixos_dir, err = _require_setup()
        if err:
            return err
        if not bool(config_manager.get_app_settings().get("prefetch_dry_run", True)):
            return jsonify({"skipped": True})

        # Stages and evaluates like a dry run: not next to a move, a rebuild
        # or a dry run (reserve(); the interface then starts the rebuild,
        # which names what is running).
        busy = reserve("rebuild")
        if busy:
            return jsonify({"error": busy}), 409
        try:
            return _precheck_reserved(nixos_dir)
        finally:
            release()

    def _precheck_reserved(nixos_dir: str):
        payload   = request.get_json(silent=True) or {}
        data      = config_manager.load_config(nixos_dir) or {}
        use_flake = bool(config_manager.flake_mode(nixos_dir))
        hostname_param = str(payload.get('hostname') or '').strip()
        if hostname_param and re.fullmatch(r'[\w.-]+', hostname_param):
            hostname = hostname_param
        else:
            hostname = (data.get("hostname") or "nixos").strip() or "nixos"

        flake_arg = ""
        if use_flake:
            is_repo = git_manager.is_git_repo(nixos_dir)
            if is_repo:
                # The flake only sees tracked files; /start stages the same
                # way right after, so this changes nothing it would not.
                staged, stage_msg = git_manager.stage_all(nixos_dir)
                if not staged:
                    return jsonify({"error": stage_msg}), 500
            flake_arg = prebuild.flake_ref(nixos_dir, hostname, is_repo)

        result = prebuild.precheck(nixos_dir, use_flake, flake_arg,
                                   update_flake=bool(payload.get('update_flake')) and use_flake)
        return jsonify(result)

    @app.route("/api/rebuild/status")
    def rebuild_status():
        """Report the current (or last finished) rebuild job, if any."""
        with _job_cv:
            if _job is None:
                return jsonify({"job": None})
            return jsonify({"job": _job_public(_job)})

    @app.route("/api/rebuild/verbose", methods=["POST"])
    def rebuild_verbose():
        """Switch the detail messages on or off – for the running job at once,
        and as the remembered choice for the next one (app settings)."""
        if err := _check_csrf(): return err
        on = (request.get_json(silent=True) or {}).get("on")
        if not isinstance(on, bool):
            return jsonify({"success": False}), 400
        with _job_cv:
            if _job is not None and _job["running"]:
                _job["verbose"] = on
        config_manager.save_app_settings({"rebuild_verbose": on})
        return jsonify({"success": True, "verbose": on})

    @app.route("/api/rebuild/ack", methods=["POST"])
    def rebuild_ack():
        """Mark the finished job as seen so a reload stops reopening the monitor.

        `job` (body): the id the tab followed. A dry run is acknowledged only
        with its own id - acknowledging "whatever finished last" marked the
        result of another tab's run as seen (Codex re-check 2026-11-26).
        Without an id (the rebuild monitor) only a job that is not a dry run
        is marked."""
        if err := _check_csrf(): return err
        want = (request.get_json(silent=True) or {}).get("job")
        with _job_cv:
            if isinstance(want, str) and want:
                _dry_results.pop(want, None)
                if _job is not None and _job["id"] == want and not _job["running"]:
                    _job["acked"] = True
            elif _job is not None and not _job["running"] and _job["mode"] != "dryrun":
                _job["acked"] = True
        return jsonify({"success": True})

    @app.route("/api/rebuild/stream")
    def rebuild_stream():
        """
        SSE endpoint: replays the buffered output of the current job and then
        follows it live. Attaching does not start anything, so reconnecting
        after a lost connection is safe.

        Uses a query-param token for CSRF because EventSource (browser API)
        only supports GET and cannot add custom headers.

        Query params:
          token – CSRF token (required)
          from  – first event index to send (default: 0). The browser's own
                  reconnect sends Last-Event-ID instead, which wins.
          job   – the job the tab started (a dry run). When another job has
                  replaced it meanwhile, its stored result is sent, or
                  {"type": "gone"} when that is no longer kept - never the
                  output of the other job (Codex re-check 2026-11-26).

        Each event carries its index as SSE id, so a reconnect resumes exactly
        where the previous connection stopped. A final {"type": "eof"} event
        tells the client to close instead of reconnecting forever.
        """
        import json as _json

        # CSRF via query param (EventSource cannot set headers)
        token = request.args.get('token', '')
        if not secrets.compare_digest(token, _csrf_token):
            return jsonify({"error": "ERR_CSRF"}), 403

        last_id = request.headers.get('Last-Event-ID', '')
        if last_id.isdigit():
            start = int(last_id) + 1
        else:
            try:
                start = max(0, int(request.args.get('from', '0')))
            except ValueError:
                start = 0

        want = request.args.get('job', '').strip() or None

        def _stored(job):
            with _job_cv:
                return _dry_results.get(job)

        def _sse():
            idx = start
            with _job_cv:
                job_id = _job["id"] if _job else None
                if want and job_id != want:
                    job_id = None
            if want and job_id is None:
                yield _stored(want) or f"data: {_json.dumps({'type': 'gone'})}\n\n"
                yield f"data: {_json.dumps({'type': 'eof'})}\n\n"
                return
            if job_id is None:
                yield f"data: {_json.dumps({'type': 'eof'})}\n\n"
                return
            while True:
                with _job_cv:
                    if _job is None or _job["id"] != job_id:
                        break  # job replaced by a newer one – indices no longer match
                    if idx < _job["dropped"]:
                        idx = _job["dropped"]
                    pending = _job["events"][idx - _job["dropped"]:]
                    if not pending:
                        if not _job["running"]:
                            break
                        _job_cv.wait(timeout=15)
                        pending = []
                if pending:
                    yield ''.join(f"id: {idx + i}\n{ev}" for i, ev in enumerate(pending))
                    idx += len(pending)
                else:
                    yield ": keep-alive\n\n"
            # Replaced while following it: what was not sent yet is gone from
            # the buffer, the stored result is not.
            if want:
                with _job_cv:
                    replaced = _job is None or _job["id"] != want
                if replaced:
                    yield _stored(want) or f"data: {_json.dumps({'type': 'gone'})}\n\n"
            yield f"data: {_json.dumps({'type': 'eof'})}\n\n"

        return Response(
            _sse(),
            mimetype='text/event-stream',
            headers={
                'Cache-Control':    'no-cache',
                'X-Accel-Buffering': 'no',  # disable nginx/proxy buffering
            },
        )

    @app.route("/api/rebuild/log")
    def rebuild_log():
        """Return the last nixos-rebuild log as plain text."""
        nixos_dir, err = _require_setup()
        if err:
            return err
        log_path = config_manager.REBUILD_LOG_FILE
        if not log_path.exists():
            # Fall back to the pre-2026-09-12 location inside the config dir,
            # so an existing log stays readable after the move.
            log_path = Path(nixos_dir) / "nixos-rebuild.log"
        if not log_path.exists():
            return jsonify({"error": "ERR_NO_LOG"}), 404
        try:
            content = log_path.read_text(encoding="utf-8", errors="replace")
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500
        return Response(content, mimetype="text/plain; charset=utf-8")


    @app.route("/api/dry-run", methods=["POST"])
    def dry_run():
        """Start a dry run as a job (revision 2026-11-25, D1).

        Until 2026-11-26 the dry run was a plain request: no reservation, so
        it ran next to a rebuild or a move (with "update flake" it writes
        flake.lock, and it stages files), and a reload lost its result. Now
        it is a job in the same frame as the rebuild: reserved like the
        others, and its result - one `done` event of kind "dryrun" carrying
        {ok, mode, output, changed_during} - survives a reload until it was
        seen (/api/rebuild/ack).
        """
        if err := _check_csrf(): return err
        nixos_dir, err = _require_setup()
        if err:
            return err
        import json as _json
        from .. import configfiles
        body = request.get_json(silent=True) or {}

        def _generate():
            before = configfiles.nix_state(nixos_dir)
            result = _dry_run_compute(nixos_dir, body)
            result["changed_during"] = configfiles.nix_state(nixos_dir) != before
            yield ("data: " + _json.dumps({"type": "done", "kind": "dryrun",
                                           "success": bool(result.get("ok")),
                                           "dry": result}) + "\n\n")

        busy = reserve("dryrun")
        if busy:
            return jsonify({"error": busy}), 409
        try:
            job_id = _start_job(_generate, mode="dryrun")
        finally:
            release()
        return jsonify({"success": True, "job_id": job_id})

    def _dry_run_compute(nixos_dir: str, body: dict) -> dict:
        """
        Validate configuration without writing anything. Returns
        {ok, mode, output}; `output` may be an error code (ERR_DRY_…).

        Flake mode  (flakes: true in nico.json):
          nix build .#nixosConfigurations.<host>.config.system.build.toplevel --dry-run
          Runs in nixos_dir.  Accepts body: {hostname, all_hosts}.
          all_hosts=true builds all known hosts in one call.

        Non-flake mode:
          1. nix-instantiate --eval -E "(import <nixpkgs/nixos> …)" – semantic
          2. Fallback: nix-instantiate --parse  – syntax only
        """
        import subprocess

        stored = config_manager.load_config(nixos_dir) or {}
        use_flake    = config_manager.flake_mode(nixos_dir)
        update_flake = bool(body.get("update_flake", False)) and use_flake

        # ── Flake mode ────────────────────────────────────────────────────
        if use_flake:
            # Optionales flake update vor dem Dry-Run
            if update_flake:
                try:
                    upd = subprocess.run(
                        ["nix", "flake", "update"],
                        cwd=nixos_dir,
                        capture_output=True,
                        text=True,
                        timeout=120,
                    )
                    if upd.returncode != 0:
                        combined = (upd.stdout + upd.stderr).strip()
                        return ({"ok": False, "mode": "flake",
                                        "output": chr(0x1f).join(["ERR_DRY_UPDATE_FAILED", combined])})
                except subprocess.TimeoutExpired:
                    return ({"ok": False, "mode": "flake", "output": "ERR_UPDATE_TIMEOUT"})
                except FileNotFoundError:
                    return ({"ok": False, "mode": "none",  "output": "ERR_DRY_NO_NIX"})

            all_hosts  = bool(body.get("all_hosts", False))
            hostname_p = (body.get("hostname") or "").strip()

            if all_hosts:
                hosts = _get_flake_hosts(nixos_dir, stored)
            elif hostname_p and re.fullmatch(r'[\w.-]+', hostname_p):
                hosts = [hostname_p]
            else:
                hosts = [(_get_flake_hosts(nixos_dir, stored) or ["nixos"])[0]]

            # The same flake the rebuild reads (prebuild.flake_base), staged
            # the same way: path: here saw ignored and unstaged files the
            # rebuild does not (revision 2026-11-18, finding 26).
            is_repo = git_manager.is_git_repo(nixos_dir)
            if is_repo:
                staged, stage_msg = git_manager.stage_all(nixos_dir)
                if not staged:
                    return ({"ok": False, "mode": "flake", "output": stage_msg})
            flake_ref = prebuild.flake_base(nixos_dir, is_repo)
            targets = [
                f"{flake_ref}#nixosConfigurations.{h}.config.system.build.toplevel"
                for h in hosts
            ]
            cmd = ["nix", "build"] + targets + [
                "--dry-run", "--no-link",
                "--extra-experimental-features", "nix-command flakes",
            ]

            try:
                result = subprocess.run(
                    cmd,
                    cwd=nixos_dir,
                    capture_output=True,
                    text=True,
                    timeout=120,
                )
                combined = (result.stdout + result.stderr).strip()
                if result.returncode == 0:
                    out = combined or "✓ Nichts zu bauen – Konfiguration ist aktuell."
                    return ({"ok": True, "mode": "flake", "output": out})
                return ({"ok": False, "mode": "flake", "output": combined or "ERR_UNKNOWN"})
            except subprocess.TimeoutExpired:
                return ({"ok": False, "mode": "flake", "output": "ERR_DRY_TIMEOUT_FLAKE"})
            except FileNotFoundError:
                return ({"ok": False, "mode": "none",  "output": "ERR_DRY_NO_NIX"})

        # ── Non-flake mode ────────────────────────────────────────────────
        # Checks exactly what a non-flake rebuild would build: configuration.nix
        # plus whatever it imports itself. Host directories not imported anywhere
        # are reported by the validator (host_orphaned), not by dry-run.

        cfg_nix = Path(nixos_dir) / "configuration.nix"
        if not cfg_nix.exists():
            return ({"ok": False, "mode": "semantic", "output": "ERR_NO_CONFIG"})

        tmpfile = str(cfg_nix)

        try:
            # Attempt 1: full NixOS module evaluation.
            # Use the standard nixos-config lookup so dry-run matches
            # nixos-rebuild behavior for non-default config roots.
            try:
                result = subprocess.run(
                    [
                        "nix-instantiate",
                        "--eval",
                        "<nixpkgs/nixos>",
                        "-A", "config.system.build.toplevel.drvPath",
                        "-I", f"nixos-config={tmpfile}",
                    ],
                    cwd=nixos_dir,
                    capture_output=True, text=True, timeout=60
                )
                if result.returncode == 0:
                    return ({
                        "ok": True,
                        "mode": "semantic",
                        # Code statt Satz - das Feld laeuft im Frontend durch
                        # tErr(), genau wie ERR_DRY_TIMEOUT_SEM zwei Zweige
                        # weiter unten. Der deutsche Satz stand hier fuer alle
                        # Sprachen (behoben 2026-11-01).
                        "output": "ERR_DRY_OK_SEM",
                    })
                raw_err = (result.stderr or result.stdout or "").strip()
                nixpkgs_missing = (
                    "nixpkgs/nixos" in raw_err and
                    ("was not found" in raw_err or "cannot find" in raw_err.lower()
                     or "not found in" in raw_err)
                )
                if not nixpkgs_missing:
                    cleaned = _clean_nix_error(raw_err, tmpfile)
                    return ({"ok": False, "mode": "semantic", "output": cleaned})
            except subprocess.TimeoutExpired:
                return ({"ok": False, "mode": "semantic", "output": "ERR_DRY_TIMEOUT_SEM"})
            except FileNotFoundError:
                return ({"ok": False, "mode": "none", "output": "ERR_DRY_NO_NIX"})
            except Exception:
                pass  # fall through to syntax-only

            # Attempt 2: syntax-only fallback
            result = subprocess.run(
                ["nix-instantiate", "--parse", tmpfile],
                capture_output=True, text=True, timeout=15
            )
            if result.returncode == 0:
                return ({
                    "ok": True,
                    "mode": "syntax",
                    "output": "ERR_DRY_OK_SYNTAX",
                })
            raw_err = (result.stderr or result.stdout or "ERR_UNKNOWN").strip()
            return ({"ok": False, "mode": "syntax", "output": _clean_nix_error(raw_err, tmpfile)})

        except FileNotFoundError:
            return ({"ok": False, "mode": "none", "output": "ERR_DRY_NO_NIX"})
        except subprocess.TimeoutExpired:
            return ({"ok": False, "mode": "syntax", "output": "ERR_DRY_TIMEOUT_SYN"})

    # -------------------------------------------------------------------- sudo

    @app.route("/api/sudo/acquire", methods=["POST"])
    def sudo_acquire():
        """Speichert ein Sudo-Passwort temporär (60 s) und gibt eine Einmal-Nonce zurück."""
        if err := _check_csrf(): return err
        body = request.get_json(silent=True) or {}
        password = body.get("password", "")
        nonce = secrets.token_hex(16)
        now = _time_mod.time()
        _sudo_nonces[nonce] = (password, now + 60)
        # abgelaufene aufräumen
        for k in list(_sudo_nonces):
            if _sudo_nonces[k][1] < now:
                del _sudo_nonces[k]
        return jsonify({"nonce": nonce})

