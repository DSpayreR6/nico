"""
Config management for NiCo.  Three layers:

  1. App settings  (~/.config/nico/settings.json, XDG)
     Program-level preferences that stay on the machine:
       - nixos_config_dir: path to the config directory
       - language: UI language (de, en, ...)
       - theme: UI theme (future)

  2. Config settings  ({nixos_dir}/config.json)
     Configuration-specific settings that travel with the config:
       - hosts_dir: directory name for host configs (default: "hosts")
       - modules_dir: directory name for modules (default: "modules")
       - hm_dir: directory name for home-manager files (default: "home")
       - flake_update_on_rebuild: whether to run flake update before rebuild

  3. NixOS config data lives EXCLUSIVELY in the .nix files:
       - configuration.nix  → main system config (read/written by load/save_config)
       - home.nix           → home-manager config (read/written by load/save_config)
       - hosts/*/default.nix → per-host config (read/written by load/save_host_config)
"""

import os
import re
from pathlib import Path


def _xdg_config_home() -> Path:
    env = os.environ.get("XDG_CONFIG_HOME", "").strip()
    return Path(env) if env else Path.home() / ".config"


# App settings live in the XDG config dir: the package dir is read-only when
# NiCo is installed from the Nix store (decision 2026-07-02, see vorgaben.txt).
APP_SETTINGS_FILE = _xdg_config_home() / "nico" / "settings.json"

# Pre-XDG location next to the package – migrated once, then never written again.
_LEGACY_APP_SETTINGS_FILE = Path(__file__).parent.parent / "nico-settings.json"


def _migrate_legacy_app_settings() -> None:
    """One-time move of a pre-XDG nico-settings.json into APP_SETTINGS_FILE."""
    import json
    if APP_SETTINGS_FILE.exists() or not _LEGACY_APP_SETTINGS_FILE.exists():
        return
    try:
        content = _LEGACY_APP_SETTINGS_FILE.read_text(encoding="utf-8")
        json.loads(content)  # migrate only valid JSON
        APP_SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
        APP_SETTINGS_FILE.write_text(content, encoding="utf-8")
        _LEGACY_APP_SETTINGS_FILE.rename(
            _LEGACY_APP_SETTINGS_FILE.with_name("nico-settings.json.bak")
        )
    except Exception:
        pass  # unreadable legacy file: start fresh with defaults

# Default app settings (program-level, stay on the machine)
DEFAULT_APP_SETTINGS = {
    "nixos_config_dir": "",
    "language": "de",
    "theme": "auto",
    "code_view_plain": False,
    "rebuild_log": False,
    "section_filter": "all",
    "git_sync": True,
    "git_status_only": False,
}

# Default config settings (travel with the config)
DEFAULT_CONFIG_SETTINGS = {
    "hosts_dir": "hosts",
    "modules_dir": "modules",
    "hm_dir": "home",
    "flake_update_on_rebuild": False,
    "push_after_save":    False,
    "push_after_rebuild": False,
    "panel_default":      "p",
    # Validation rules: all enabled by default; keys match validator.ALL_RULES ids
    "validation_rules": {
        "user_in_config":    True,
        "flake_host_exists": True,
        "hardware_imported": True,
        "hardware_matches":  True,
        "duplicate_attrs":   True,
        "imports_exist":     True,
        "brix_redundant":    True,
    },
}

# In-memory defaults – never persisted to JSON
DEFAULT_NIXOS_CONFIG = {
    # ── System
    "hostname":     "",
    "state_version": "",
    "nix_args":     "config, pkgs, lib",

    # ── Lokalisierung
    "timezone":          "",
    "locale":            "",
    "keyboard_layout":   "",
    "keyboard_variant":  "",
    "keyboard_console":  "",

    # ── Netzwerk
    "networkmanager":    True,
    "ssh":               False,
    "firewall_disable":  False,
    "firewall_tcp_enable": False,
    "firewall_tcp_ports": "",
    "firewall_udp_enable": False,
    "firewall_udp_ports": "",

    # ── Services
    "printing":  False,
    "avahi":     False,
    "bluetooth": False,
    "blueman":   False,

    # ── Desktop
    "desktop_environment": "none",
    "autologin_user":      "",

    # ── Audio
    "pipewire": False,

    # ── Benutzer
    "username":              "",
    "user_description":      "",
    "user_initial_password": "",
    "user_uid":              "",
    "user_groups":           ["wheel", "networkmanager"],
    "user_groups_extra":     "",
    "user_shell":            "bash",
    "user_extra_nix":        "",
    "guest_user":            False,

    # ── Programme
    "packages":    [],
    "allowUnfree": False,

    # ── Nix & System
    "flakes":             False,
    "nix_optimize_store": False,
    "nix_gc":             False,
    "nix_gc_frequency":   "weekly",
    "nix_gc_age":         "30d",

    # ── Schriftarten
    "fonts":       [],
    "fonts_extra": "",

    # True when hardware-configuration.nix is present
    "hardware_config": False,

    # ── Hardware
    "enable_all_firmware": False,
    "cpu_microcode":       "none",
    "opengl":              False,
    "opengl_32bit":        False,
    "zram_swap":           False,

    # ── Virtualisierung
    "docker":               False,
    "docker_rootless":      False,
    "podman":               False,
    "podman_docker_compat": False,
    "virtualbox_host":      False,
    "libvirtd":             False,
    "virt_manager":         False,

    # ── Dateisystem & Backup
    "btrfs_scrub":      False,
    "snapper_enable":   False,
    "snapper_configs":  [],

    # Weitere Benutzer
    "extra_users": [],

    # Brick blocks: {name: {"section": str, "order": int, "text": str}}
    "brick_blocks": {},

}


# ── App-level settings (APP_SETTINGS_FILE, XDG) ──────────────────────────────
# Program preferences that stay on this machine: config path, language, theme.

def get_app_settings() -> dict:
    import json
    import copy
    import time
    _migrate_legacy_app_settings()
    defaults = copy.deepcopy(DEFAULT_APP_SETTINGS)
    if not APP_SETTINGS_FILE.exists():
        return defaults
    for attempt in range(3):
        try:
            with open(APP_SETTINGS_FILE, encoding="utf-8") as f:
                stored = json.load(f)
            defaults.update(stored)
            return defaults
        except Exception:
            if attempt == 2:
                return defaults
            time.sleep(0.02)
    return defaults


def save_app_settings(settings: dict) -> None:
    import json
    import os
    import tempfile
    existing = get_app_settings()
    existing.update(settings)
    APP_SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        dir=APP_SETTINGS_FILE.parent,
        delete=False,
    ) as f:
        json.dump(existing, f, indent=2)
        f.flush()
        os.fsync(f.fileno())
        tmp_name = f.name
    Path(tmp_name).replace(APP_SETTINGS_FILE)


def get_nixos_config_dir() -> str | None:
    val = get_app_settings().get("nixos_config_dir")
    return val if val else None


# ── Config-level settings ({nixos_dir}/config.json) ───────────────────────────
# Configuration-specific settings that travel with the config directory.

def _config_json(nixos_dir: str) -> Path:
    return Path(nixos_dir) / "config.json"


def load_config_settings(nixos_dir: str) -> dict:
    """Load config.json from the nixos config directory."""
    import json
    import copy
    defaults = copy.deepcopy(DEFAULT_CONFIG_SETTINGS)
    f = _config_json(nixos_dir)
    if not f.exists():
        return defaults
    try:
        with open(f) as fh:
            stored = json.load(fh)
        defaults.update(stored)
        return defaults
    except Exception:
        return defaults


def save_config_settings(nixos_dir: str, settings: dict) -> None:
    """Save config.json to the nixos config directory (atomic, like app settings)."""
    import json
    import os
    import tempfile
    f = _config_json(nixos_dir)
    existing = load_config_settings(nixos_dir)
    existing.update(settings)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=f.parent, delete=False,
    ) as fh:
        json.dump(existing, fh, indent=2, ensure_ascii=False)
        fh.flush()
        os.fsync(fh.fileno())
        tmp_name = fh.name
    Path(tmp_name).replace(f)


# ── Legacy: nico.json migration ───────────────────────────────────────────────
# Migrate old nico.json to new config.json if present.

def _nico_json(nixos_dir: str) -> Path:
    return Path(nixos_dir) / "nico.json"


def migrate_nico_json(nixos_dir: str) -> None:
    """Migrate old nico.json to config.json if it exists."""
    import json
    old_file = _nico_json(nixos_dir)
    if not old_file.exists():
        return
    try:
        with open(old_file) as fh:
            old_data = json.load(fh)
        # Migrate known keys to config.json
        migrate_keys = {"hm_dir", "flake_update_on_rebuild"}
        to_migrate = {k: v for k, v in old_data.items() if k in migrate_keys}
        if to_migrate:
            save_config_settings(nixos_dir, to_migrate)
        # Remove old file after successful migration
        old_file.unlink()
    except Exception:
        pass  # Silently ignore migration errors


# ── NixOS config (read/write .nix files) ─────────────────────────────────────

def get_defaults() -> dict:
    """In-memory defaults – never written to disk."""
    import copy
    return copy.deepcopy(DEFAULT_NIXOS_CONFIG)


def init_nico_dir(nixos_config_dir: str) -> None:
    """No-op: NiCo no longer stores config data in JSON files."""
    pass


def load_config(nixos_config_dir: str) -> dict:
    """Parse configuration.nix (and home.nix if present) into a config dict.
    Always returns a dict with defaults for any fields not found in the files."""
    import copy
    from . import importer as _imp
    from .brix import strip_brick_blocks, extract_brick_blocks

    data = copy.deepcopy(DEFAULT_NIXOS_CONFIG)
    data["hardware_config"] = (
        Path(nixos_config_dir) / "hardware-configuration.nix"
    ).is_file()

    nix_file = Path(nixos_config_dir) / "configuration.nix"
    if nix_file.exists():
        try:
            content = nix_file.read_text(encoding="utf-8")
            parsed  = _imp.parse_config(strip_brick_blocks(content))
            data.update(parsed)
            data["brick_blocks"] = extract_brick_blocks(content)
        except OSError:
            pass

    # Migrate old flat snapper fields to new list format
    _sn_old_hourly  = data.pop("snapper_timeline_hourly",  5)
    _sn_old_daily   = data.pop("snapper_timeline_daily",   7)
    _sn_old_weekly  = data.pop("snapper_timeline_weekly",  0)
    _sn_old_monthly = data.pop("snapper_timeline_monthly", 1)
    _sn_old_yearly  = data.pop("snapper_timeline_yearly",  0)
    _sn_root = data.pop("snapper_root", False)
    _sn_home = data.pop("snapper_home", False)
    if _sn_root or _sn_home:
        data["snapper_enable"] = True
        migrated: list = []
        if _sn_root:
            migrated.append({"name": "root", "mountpoint": "/",
                              "hourly": _sn_old_hourly, "daily": _sn_old_daily,
                              "weekly": _sn_old_weekly, "monthly": _sn_old_monthly,
                              "yearly": _sn_old_yearly})
        if _sn_home:
            migrated.append({"name": "home", "mountpoint": "/home",
                              "hourly": _sn_old_hourly, "daily": _sn_old_daily,
                              "weekly": _sn_old_weekly, "monthly": _sn_old_monthly,
                              "yearly": _sn_old_yearly})
        if not data.get("snapper_configs"):
            data["snapper_configs"] = migrated

    return data


def save_config(nixos_config_dir: str, data: dict) -> None:
    """Generate configuration.nix and write it to nixos_config_dir."""
    import copy
    from . import generator as _gen
    from .brix import extract_brick_blocks

    data = copy.copy(data)
    nix_file  = Path(nixos_config_dir) / "configuration.nix"

    # Preserve brick_blocks from the existing file when not supplied
    if not data.get("brick_blocks") and nix_file.exists():
        try:
            data["brick_blocks"] = extract_brick_blocks(
                nix_file.read_text(encoding="utf-8")
            )
        except OSError:
            pass

    nix_file.write_text(
        _gen.generate_configuration_nix(data), encoding="utf-8"
    )


# ── Multi-Host Support ────────────────────────────────────────────────────────

def scan_hosts(nixos_dir: str) -> list[str]:
    """Sorted list of host names found in {hosts_dir}/*/default.nix."""
    cfg = load_config_settings(nixos_dir)
    hosts_dir_name = cfg.get("hosts_dir", "hosts") or "hosts"
    hosts_dir = Path(nixos_dir) / hosts_dir_name
    if not hosts_dir.is_dir():
        return []
    return sorted(p.parent.name for p in hosts_dir.glob("*/default.nix"))


def load_host_config(nixos_dir: str, host_name: str) -> dict | None:
    """Parse {hosts_dir}/<host_name>/default.nix; returns None if the file doesn't exist."""
    from . import importer as _imp
    from .brix import strip_brick_blocks, extract_brick_blocks

    cfg = load_config_settings(nixos_dir)
    hosts_dir_name = cfg.get("hosts_dir", "hosts") or "hosts"
    nix_file = Path(nixos_dir) / hosts_dir_name / host_name / "default.nix"
    if not nix_file.exists():
        return None

    try:
        content = nix_file.read_text(encoding="utf-8")
        data    = _imp.parse_config(strip_brick_blocks(content))
        data["brick_blocks"] = extract_brick_blocks(content)
        data["_hw_config"] = (
            Path(nixos_dir) / hosts_dir_name / host_name / "hardware-configuration.nix"
        ).is_file()
        return data
    except OSError:
        return None


def save_host_config(nixos_dir: str, host_name: str, data: dict) -> None:
    """Generate and write {hosts_dir}/<host_name>/default.nix."""
    import copy
    from . import generator as _gen
    from .brix import extract_brick_blocks

    cfg = load_config_settings(nixos_dir)
    hosts_dir_name = cfg.get("hosts_dir", "hosts") or "hosts"
    host_dir = Path(nixos_dir) / hosts_dir_name / host_name
    nix_file = host_dir / "default.nix"
    data     = copy.copy(data)

    if not data.get("brick_blocks") and nix_file.exists():
        try:
            data["brick_blocks"] = extract_brick_blocks(
                nix_file.read_text(encoding="utf-8")
            )
        except OSError:
            pass

    hw_config = data.get("_hw_config") or (
        host_dir / "hardware-configuration.nix"
    ).is_file()

    host_dir.mkdir(parents=True, exist_ok=True)
    nix_file.write_text(
        _gen.generate_host_nix(data, host_name, hw_config), encoding="utf-8"
    )
