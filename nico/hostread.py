"""
Reading this machine outside the config directory – only with consent.

NiCo works on the files of one config directory. A few checks also need to
look at the machine it runs on: the Validator compares the users and groups
of the config with /etc/passwd and /etc/group, the hardware check compares
hardware-configuration.nix with the disks under /dev/disk, the time machine's
system side lists the generations. Until 2026-11-21 this happened without
asking, and only /proc was documented as an exception in vorgaben.txt.

Martin, 2026-11-21: reading outside is not allowed unless the user releases
it. Three switches in the app settings – they belong to this machine, not to
the config –, all off until switched on, each with a release button where the
function needs it and all three in the settings:

  VALIDATOR    /etc/passwd, /etc/group, /run/current-system/nixos-version,
               /etc/os-release, install markers (/etc/machine-id, ssh host
               keys, /var/lib/nixos), /proc/swaps, /proc/self/mounts, findmnt
  HARDWARE     /etc/nixos/hardware-configuration.nix, /dev/disk/by-*, lsblk,
               the search in /etc/nixos, /run/current-system,
               /nix/var/nix/profiles
  GENERATIONS  the generation list, /run/current-system, /run/booted-system,
               /nix/var/nix/profiles/system

Not behind a switch, because the user's own click is the consent: the import
from /etc/nixos, creating the /etc/nixos symlink, and /proc/meminfo, read only
once the safe rebuild is chosen.
"""
from __future__ import annotations

VALIDATOR   = "read_host_validator"
HARDWARE    = "read_host_hardware"
GENERATIONS = "read_host_generations"
KINDS = (VALIDATOR, HARDWARE, GENERATIONS)

# The answer of a route that would have to read and may not.
ERR_OFF = "ERR_HOST_READ_OFF"


def allowed(kind: str) -> bool:
    """True when the user has released this kind of reading."""
    if kind not in KINDS:
        return False
    from . import config_manager
    try:
        return config_manager.get_app_settings().get(kind) is True
    except Exception:
        return False


def state() -> dict:
    """{kind: bool} for all three, for the frontend."""
    return {k: allowed(k) for k in KINDS}
