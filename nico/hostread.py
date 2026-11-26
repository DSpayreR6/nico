"""
Reading this machine outside the config directory – only with consent.

NiCo works on the files of one config directory. A few checks also need to
look at the machine it runs on: the Validator compares the users and groups
of the config with /etc/passwd and /etc/group, the hardware check compares
hardware-configuration.nix with the disks under /dev/disk, the time machine's
system side lists the generations. Until 2026-11-21 this happened without
asking, and only /proc was documented as an exception in vorgaben.txt.

Martin, 2026-11-21: reading outside is not allowed unless the user releases
it. Four switches in the app settings – they belong to this machine, not to
the config –, all off until switched on, each with a release button where the
function needs it and all of them in the settings:

  VALIDATOR    /etc/passwd, /etc/group, /run/current-system/nixos-version,
               /etc/os-release, install markers (/etc/machine-id, ssh host
               keys, /var/lib/nixos), /proc/swaps, /proc/self/mounts, findmnt
  HARDWARE     /etc/nixos/hardware-configuration.nix, /dev/disk/by-*, lsblk,
               the search in /etc/nixos, /run/current-system,
               /nix/var/nix/profiles
  GENERATIONS  the generation list, /run/current-system, /run/booted-system,
               /nix/var/nix/profiles/system
  PROFILE      `nix profile list` - the programs this user installed, read
               for "Update NiCo" (since 2026-11-26, revision 2026-11-25 S3:
               it used to run on every page load without asking)

Not behind a switch, because the user's own click is the consent: the import
from /etc/nixos, creating the /etc/nixos symlink, and /proc/meminfo, read only
once the safe rebuild is chosen.

"Allow once" (Martin, 2026-11-26): where a function needs a release that is
off, the user may allow it once instead of always. The interface then sends
the kind in the header X-NiCo-Read-Once until the page is reloaded; nothing
is stored. A cross-site page cannot set that header without a CORS
preflight, which NiCo never answers.
"""
from __future__ import annotations

VALIDATOR   = "read_host_validator"
HARDWARE    = "read_host_hardware"
GENERATIONS = "read_host_generations"
PROFILE     = "read_host_profile"
KINDS = (VALIDATOR, HARDWARE, GENERATIONS, PROFILE)

# The answer of a route that would have to read and may not.
ERR_OFF = "ERR_HOST_READ_OFF"

ONCE_HEADER = "X-NiCo-Read-Once"


def _allowed_once(kind: str) -> bool:
    """Released once for the request being handled (see module docstring)."""
    try:
        from flask import has_request_context, request
    except ImportError:
        return False
    if not has_request_context():
        return False
    return kind in {k.strip() for k in request.headers.get(ONCE_HEADER, "").split(",")}


def allowed(kind: str) -> bool:
    """True when the user has released this kind of reading - always (app
    setting) or once (header of the current request)."""
    if kind not in KINDS:
        return False
    if _allowed_once(kind):
        return True
    from . import config_manager
    try:
        return config_manager.get_app_settings().get(kind) is True
    except Exception:
        return False


def state() -> dict:
    """{kind: bool} for all four switches, for the frontend - the stored
    ones only, a release once is not a setting."""
    from . import config_manager
    try:
        settings = config_manager.get_app_settings()
    except Exception:
        settings = {}
    return {k: settings.get(k) is True for k in KINDS}
