"""
Limits for the safe rebuild: how many build jobs and compiler threads fit
into the memory of this machine.

`nixos-rebuild` takes two knobs. `--max-jobs` is the number of packages
built at the same time, `--cores` the number of threads each of them may
use. Memory use is roughly jobs × cores × what one compiler process needs,
so the suggestion divides the memory that is free right now by a rule of
thumb per thread.

It stays an estimate: what a build really needs depends on the package (a
linker run or LTO takes far more than 2 GiB), and `--cores` is only a
request the package may ignore. The suggestion therefore keeps jobs at 1 –
two large packages at once are what brings a machine down – and holds back
a reserve for the desktop before it divides.

Reads /proc/meminfo, which is the documented exception in vorgaben.txt
(read-only state of this machine). No web context needed.
"""
from __future__ import annotations

import os
from pathlib import Path

GIB = 1024 ** 3

# Rule of thumb for one C++ compiler process.
BYTES_PER_THREAD = 2 * GIB
# Held back for desktop and browser, which keep growing during a build:
# a share of the total memory, but never less than the floor.
RESERVE_SHARE = 0.10
RESERVE_FLOOR = 2 * GIB
# What the safe rebuild used before it measured anything.
FALLBACK_CORES = 4

MEMINFO = Path("/proc/meminfo")


def cpu_count() -> int:
    return max(1, os.cpu_count() or 1)


def read_meminfo(path: Path = MEMINFO) -> dict | None:
    """Return {'total': bytes, 'available': bytes} or None when unreadable.

    Swap is deliberately left out: a build that only fits with swap makes
    the machine unusable long before it fails.
    """
    try:
        raw = Path(path).read_text(encoding="ascii", errors="replace")
    except OSError:
        return None
    found = {}
    for line in raw.splitlines():
        key, _, rest = line.partition(":")
        if key in ("MemTotal", "MemAvailable"):
            parts = rest.split()
            if parts and parts[0].isdigit():
                found[key] = int(parts[0]) * 1024      # the file counts in kB
    if "MemTotal" not in found or "MemAvailable" not in found:
        return None
    return {"total": found["MemTotal"], "available": found["MemAvailable"]}


def suggest(meminfo: dict | None = None, cpus: int | None = None,
            *, measure: bool = True) -> dict:
    """Suggested limits for this machine.

    Returns jobs, cores, cpus, measured and – when measured – total, available,
    reserve and usable in bytes. `measure=False` skips reading /proc, so
    tests can pass `meminfo=None` and get the fallback.
    """
    cpus = max(1, int(cpus)) if cpus else cpu_count()
    if meminfo is None and measure:
        meminfo = read_meminfo()
    if not meminfo:
        return {"jobs": 1, "cores": min(FALLBACK_CORES, cpus), "cpus": cpus,
                "measured": False}

    total     = int(meminfo["total"])
    available = int(meminfo["available"])
    reserve   = max(RESERVE_FLOOR, int(total * RESERVE_SHARE))
    usable    = max(0, available - reserve)
    cores     = max(1, min(cpus, usable // BYTES_PER_THREAD))
    return {"jobs": 1, "cores": int(cores), "cpus": cpus, "measured": True,
            "total": total, "available": available,
            "reserve": reserve, "usable": usable}


def clean(value, cpus: int | None = None) -> int | None:
    """A user-supplied limit as int within 1..cpus, or None when it is not one.

    None means "automatic". bool is rejected on purpose: True would otherwise
    pass as 1.
    """
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, str):
        value = value.strip()
        if not value.isdigit():
            return None
        value = int(value)
    if isinstance(value, float):
        if not value.is_integer():
            return None
        value = int(value)
    if not isinstance(value, int):
        return None
    cpus = max(1, int(cpus)) if cpus else cpu_count()
    if value < 1 or value > cpus:
        return None
    return value


def resolve(jobs=None, cores=None, settings: dict | None = None) -> tuple[int, int]:
    """The limits a safe rebuild runs with: (jobs, cores).

    Order per value: the one passed in, then the one stored in the app
    settings, then the suggestion. Anything that is not a whole number
    within 1..cpus counts as not given.
    """
    settings = settings or {}
    cpus = cpu_count()
    jobs  = clean(jobs, cpus)  or clean(settings.get("rebuild_safe_jobs"), cpus)
    cores = clean(cores, cpus) or clean(settings.get("rebuild_safe_cores"), cpus)
    if jobs is None or cores is None:
        hint = suggest(cpus=cpus)
        jobs  = jobs  or hint["jobs"]
        cores = cores or hint["cores"]
    return jobs, cores
