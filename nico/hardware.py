"""
Does this hardware-configuration.nix describe *this* machine?

hardware-configuration.nix is machine-bound: it names the disks of exactly one
computer, usually by UUID.  Whenever a config arrives from somewhere else - an
import, a git rollback, a clone of the repository on a freshly installed
machine - the file it brings describes the machine it came from.  Building that
config makes NixOS look for devices that do not exist here; in the best case the
build fails, in the worst case the result does not boot.

This module answers that one question and nothing else.  It never writes a file
and never decides: `inspect()` collects the facts, `decide()` turns them into the
question that has to be put to the user, and the callers act.  Three places need
it - the import, the restore paths, and the validator - and they must not each
grow their own idea of what "matches" means.

Three verdicts, and the difference between the last two matters:

  MATCH     every device reference we can check exists on this machine
  MISMATCH  at least one checkable reference does not exist here
  UNKNOWN   nothing checkable in the file, or a reference we cannot resolve

UNKNOWN is not a milder MISMATCH.  It means we do not know, and a wrong guess
costs the whole system, so it stops just as hard.  What it must never do is stop
*forever*: a config built on labels or shared folders would be red at every
rebuild, and a red finding that cannot be cleared only teaches people to click
the override.  `ack_*` therefore records a one-time confirmation per host in
config.json, tied to the file's content - edit the file and the confirmation is
gone.
"""
from __future__ import annotations

import hashlib
import re
import subprocess
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from . import atomic

HW_NAME = "hardware-configuration.nix"

# The file nixos-generate-config wrote for the machine NiCo runs on.  This is the
# only source of a *correct* hardware config we can offer as a replacement.
ETC_NIXOS_HW = Path("/etc/nixos") / HW_NAME

MATCH = "match"
MISMATCH = "mismatch"
UNKNOWN = "unknown"

# `device` of these fileSystems entries is never a local block device, so no
# device list can confirm it.  Reporting a VirtualBox shared folder as "this disk
# does not exist" is a false alarm, and false alarms are what get overrides
# clicked, so they are left out of the verdict entirely.
_VIRTUAL_FS = {
    "vboxsf", "9p", "virtiofs", "nfs", "nfs4", "cifs", "smbfs", "smb3",
    "sshfs", "tmpfs", "ramfs", "overlay", "fuse", "fuse.sshfs", "none",
}

# /dev/disk/by-<kind>/<value> - the forms nixos-generate-config emits.
_BY_KINDS = ("uuid", "partuuid", "label", "partlabel")
_BY_PATH_RE = re.compile(
    r"/dev/disk/by-(" + "|".join(_BY_KINDS) + r")/([^\"'\s;]+)"
)


# ── Facts about this machine ──────────────────────────────────────────────────

def local_device_ids() -> set[str]:
    """
    Every device identifier this machine currently exposes, lowercased.

    Reads /dev/disk/by-* directly instead of calling blkid: blkid returns an
    empty list to an unprivileged process, and NiCo runs as the user.  Taking
    that silence as "none of these disks exist" turned the old check into a
    false alarm on every machine - it reported a correct hardware config as
    foreign.  The symlink directories are world-readable and need no subprocess
    at all.
    """
    ids: set[str] = set()
    for kind in _BY_KINDS:
        d = Path("/dev/disk") / f"by-{kind}"
        try:
            for entry in d.iterdir():
                ids.add(entry.name.lower())
        except OSError:
            continue

    if ids:
        return ids

    # Containers and minimal installers may not populate /dev/disk at all.
    # lsblk also works unprivileged and is the better fallback than blkid.
    try:
        out = subprocess.run(
            ["lsblk", "-o", "UUID,PARTUUID,LABEL,PARTLABEL", "-ln"],
            capture_output=True, text=True, timeout=5,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return ids
    for line in out.splitlines():
        for token in line.split():
            if token.strip():
                ids.add(token.strip().lower())
    return ids


def running_host() -> str:
    """Hostname of the machine NiCo runs on (matches networking.hostName)."""
    import platform
    return (platform.node() or "").split(".")[0]


# ── One hardware-configuration.nix ───────────────────────────────────────────

@dataclass
class HwFile:
    path: Path
    host: str | None            # host name, or None for the config root
    verdict: str                # MATCH | MISMATCH | UNKNOWN
    missing: list[str] = field(default_factory=list)   # checkable, not here
    unresolved: list[str] = field(default_factory=list)  # not checkable at all
    acked: bool = False         # user confirmed this exact content belongs here

    @property
    def blocking(self) -> bool:
        return self.verdict != MATCH and not self.acked


def _brace_block(text: str, start: int) -> str:
    """Text of the { … } block that starts at `start` (index of the brace)."""
    depth = 0
    for i in range(start, len(text)):
        c = text[i]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
    return text[start:]


_DEVICE_RE = re.compile(r'device\s*=\s*"((?:[^"\\]|\\.)*)"')
_FSTYPE_RE = re.compile(r'fsType\s*=\s*"((?:[^"\\]|\\.)*)"')
_FS_ENTRY_RE = re.compile(r'fileSystems\s*\.\s*(?:"[^"]*"|[\w\-./]+)\s*=\s*\{')
_SWAP_RE = re.compile(r"swapDevices\s*=\s*\[", re.S)


def _device_refs(content: str) -> tuple[set[str], set[str]]:
    """
    (checkable, unresolved) device references in a hardware config.

    checkable   values behind /dev/disk/by-*/ - a device list can confirm them
    unresolved  anything else that still names a real block device, e.g. a bare
                /dev/sda1 or a plain filesystem label.  Entries of a virtual
                filesystem are dropped, they name no device.
    """
    checkable = {m.group(2).lower() for m in _BY_PATH_RE.finditer(content)}
    unresolved: set[str] = set()

    def collect(block: str) -> None:
        fstype = _FSTYPE_RE.search(block)
        if fstype and fstype.group(1).strip().lower() in _VIRTUAL_FS:
            return
        for dev in _DEVICE_RE.findall(block):
            dev = dev.strip()
            if not dev or _BY_PATH_RE.search(dev):
                continue
            unresolved.add(dev)

    for m in _FS_ENTRY_RE.finditer(content):
        collect(_brace_block(content, m.end() - 1))

    # swapDevices = [ { device = …; } … ] - a swap device that does not exist
    # fails the activation just as hard as a missing root filesystem.
    for m in _SWAP_RE.finditer(content):
        depth, i = 0, m.end() - 1
        for i in range(m.end() - 1, len(content)):
            if content[i] == "[":
                depth += 1
            elif content[i] == "]":
                depth -= 1
                if depth == 0:
                    break
        for inner in re.finditer(r"\{", content[m.end():i]):
            collect(_brace_block(content[m.end():i], inner.start()))

    return checkable, unresolved


def classify(path: Path, local_ids: set[str] | None = None) -> tuple[str, list[str], list[str]]:
    """(verdict, missing, unresolved) for one hardware-configuration.nix."""
    ids = local_device_ids() if local_ids is None else local_ids
    try:
        content = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return UNKNOWN, [], []

    checkable, unresolved = _device_refs(content)
    missing = sorted(v for v in checkable if v not in ids)

    if missing:
        return MISMATCH, missing, sorted(unresolved)
    if unresolved or not checkable:
        # Everything we could check is here, but something in the file is beyond
        # checking - or there was nothing to check.  Not a confirmed match.
        return UNKNOWN, [], sorted(unresolved)
    return MATCH, [], []


# ── All hardware configs of a config directory ───────────────────────────────

def _hosts_dir(nixos_dir: str) -> str:
    from . import config_manager
    return (config_manager.load_config_settings(nixos_dir).get("hosts_dir") or "hosts").strip() or "hosts"


def find_hw_files(nixos_dir: str) -> list[HwFile]:
    """
    Every hardware-configuration.nix in the config directory, with its host.

    Only the exact file name counts.  A hardware-configuration.nix.old left over
    from an earlier install is not a second hardware config, and counting it
    would turn a single-host config into the "which host is this machine?" case.
    """
    base = Path(nixos_dir)
    hosts_dir = _hosts_dir(nixos_dir)
    local_ids = local_device_ids()
    acks = _load_acks(nixos_dir)

    found: list[HwFile] = []
    for path in sorted(base.rglob(HW_NAME)):
        if ".git" in path.parts or path.name != HW_NAME:
            continue
        rel = path.relative_to(base)
        host = None
        if len(rel.parts) == 3 and rel.parts[0] == hosts_dir:
            host = rel.parts[1]
        verdict, missing, unresolved = classify(path, local_ids)
        found.append(HwFile(
            path=path, host=host, verdict=verdict,
            missing=missing, unresolved=unresolved,
            acked=_ack_valid(acks, host, path),
        ))
    return found


# ── The decision ─────────────────────────────────────────────────────────────

# No hardware config in the config directory at all.
ADD = "add"
# Exactly one, and it is not confirmed to be this machine's.
REPLACE = "replace"
# Several, and which one belongs to this machine is not clear.
PICK_HOST = "pick_host"
# Nothing to ask.
OK = "ok"


@dataclass
class Decision:
    action: str                     # OK | ADD | REPLACE | PICK_HOST
    files: list[HwFile] = field(default_factory=list)
    target: HwFile | None = None    # the file this machine's host owns
    hosts: list[str] = field(default_factory=list)  # choices for PICK_HOST
    matched_host: str | None = None  # host identified as this machine
    source_available: bool = False  # /etc/nixos/hardware-configuration.nix exists
    source_verdict: str | None = None  # …and does *it* describe this machine?
    host_name_hit: bool = False     # the hostname named the target, not a UUID

    @property
    def blocking(self) -> bool:
        return self.action != OK

    @property
    def source_usable(self) -> bool:
        """
        Is the local file worth offering as the replacement?

        Not a given.  The usual way a foreign config reaches a machine is that
        someone copies it into /etc/nixos and imports from there - then the
        "local" file is the foreign one, and putting it in place would change
        nothing while looking like a fix.  A confirmed mismatch therefore
        disqualifies it; UNKNOWN still counts, since unverifiable is the normal
        state of a label-based config.
        """
        return self.source_available and self.source_verdict != MISMATCH


def inspect(nixos_dir: str) -> Decision:
    """
    Look at the config directory and say what has to be asked.

    The hostname is trusted before any device comparison: on a machine called
    nix-desktop, hosts/nix-desktop is this machine's host even when its hardware
    file is the wrong one - that is exactly the case we are here to catch.  The
    device comparison then only decides whether the file is sound, not which
    file is ours.
    """
    from . import config_manager

    files = find_hw_files(nixos_dir)
    hosts = config_manager.scan_hosts(nixos_dir)
    this = running_host()

    source = ETC_NIXOS_HW.is_file()
    source_verdict = classify(ETC_NIXOS_HW)[0] if source else None

    def result(**kw) -> Decision:
        kw.setdefault("files", files)
        return Decision(source_available=source, source_verdict=source_verdict, **kw)

    if not files:
        return result(action=ADD, files=[], hosts=hosts,
                      matched_host=this if this in hosts else None)

    # The hostname names our host directly.
    by_host = {f.host: f for f in files if f.host}
    if this and this in by_host:
        target = by_host[this]
        return result(action=OK if not target.blocking else REPLACE,
                      target=target, matched_host=this, host_name_hit=True)

    # A single hardware config: it is ours whether it fits or not.
    if len(files) == 1:
        only = files[0]
        return result(action=OK if not only.blocking else REPLACE,
                      target=only, matched_host=only.host)

    # Several, no hostname hit: a single clean match identifies the machine.
    clean = [f for f in files if not f.blocking]
    if len(clean) == 1:
        return result(action=OK, target=clean[0], matched_host=clean[0].host)

    # None match, or more than one - only the user knows.
    return result(action=PICK_HOST, hosts=hosts)


# ── Putting the local file in place ──────────────────────────────────────────

IMPORT_BAK_SUFFIX = ".import.bak"


def replace_with_local(target: Path, source: Path | None = None) -> Path:
    """
    Put this machine's hardware config at `target`, keeping what was there.

    The file being replaced is kept as .import.bak and never deleted: it is a
    different machine's hardware description, and if it came in by mistake -
    the laptop's config imported on the desktop - throwing it away would be the
    data loss this whole check exists to prevent.
    """
    src = source or ETC_NIXOS_HW
    if not src.is_file():
        raise FileNotFoundError(str(src))
    if classify(src)[0] == MISMATCH:
        # Writing a file we know names other machines' disks would look like a
        # repair and leave the config just as unbootable.
        raise ValueError("source_mismatch")

    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        bak = target.with_name(target.name + IMPORT_BAK_SUFFIX)
        if bak.exists():
            bak.unlink()
        target.replace(bak)

    atomic.write_bytes(target, src.read_bytes())
    return target


# ── One-time confirmation per host ───────────────────────────────────────────

def _content_fp(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()[:16]
    except OSError:
        return ""


def _load_acks(nixos_dir: str) -> dict:
    from . import config_manager
    stored = config_manager.load_config_settings(nixos_dir).get("hardware_ack")
    return stored if isinstance(stored, dict) else {}


def _ack_key(host: str | None) -> str:
    return host or ""


def _ack_valid(acks: dict, host: str | None, path: Path) -> bool:
    entry = acks.get(_ack_key(host))
    if not isinstance(entry, dict):
        return False
    return bool(entry.get("fp")) and entry["fp"] == _content_fp(path)


def ack_hardware(nixos_dir: str, host: str | None, path: Path) -> None:
    """Record that the user confirmed this exact file belongs to this machine."""
    from . import config_manager
    acks = dict(_load_acks(nixos_dir))
    acks[_ack_key(host)] = {
        "fp": _content_fp(path),
        "at": datetime.now().isoformat(timespec="seconds"),
        "by": running_host(),
    }
    config_manager.save_config_settings(nixos_dir, {"hardware_ack": acks})


def clear_ack(nixos_dir: str, host: str | None) -> None:
    from . import config_manager
    acks = dict(_load_acks(nixos_dir))
    if _ack_key(host) in acks:
        del acks[_ack_key(host)]
        config_manager.save_config_settings(nixos_dir, {"hardware_ack": acks})
