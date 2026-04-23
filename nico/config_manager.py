"""
Config management for NiCo.  Three layers:

  1. App settings  (~/.config/nico/settings.json)
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

import re
import subprocess
from pathlib import Path

APP_SETTINGS_FILE = Path.home() / ".config" / "nico" / "settings.json"

# Default app settings (program-level, stay on the machine)
DEFAULT_APP_SETTINGS = {
    "nixos_config_dir": "",
    "language": "de",
    "theme": "auto",
    "code_view_plain": False,
    "rebuild_log": False,
}

# Default config settings (travel with the config)
DEFAULT_CONFIG_SETTINGS = {
    "hosts_dir": "hosts",
    "modules_dir": "modules",
    "hm_dir": "home",
    "flake_update_on_rebuild": False,
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
    "btrfs_scrub":               False,
    "snapper_home":              False,
    "snapper_root":              False,
    "snapper_timeline_hourly":   5,
    "snapper_timeline_daily":    7,
    "snapper_timeline_weekly":   0,
    "snapper_timeline_monthly":  1,
    "snapper_timeline_yearly":   0,

    # Weitere Benutzer
    "extra_users": [],

    # Brick blocks: {name: {"section": str, "order": int, "text": str}}
    "brick_blocks": {},

    # ── Home Manager
    "home_manager": {
        "enabled":            False,
        "git_enable":         False,
        "git_name":           "",
        "git_email":          "",
        "git_default_branch": "main",
        "shell":              "bash",
        "shell_init_extra":   "",
        "packages":           [],
        "firefox":            False,
        "xdg_user_dirs":      False,
        "xdg_download":       "Downloads",
        "xdg_documents":      "Documents",
        "xdg_pictures":       "Pictures",
        "xdg_music":          "Music",
        "xdg_videos":         "Videos",
        "xdg_desktop":        "Desktop",
        "xdg_templates":      "Templates",
        "xdg_publicshare":    "Public",
    },
}

ADDITIVE_FIELDS = {"packages", "fonts", "fonts_extra", "brick_blocks"}


def _detect_state_version() -> str:
    """Read running NixOS version via nixos-version, extract major.minor."""
    try:
        out = subprocess.run(
            ["nixos-version"], capture_output=True, text=True, timeout=5
        ).stdout.strip()
        m = re.match(r'^(\d+\.\d+)', out)
        if m:
            return m.group(1)
    except Exception:
        pass
    return DEFAULT_NIXOS_CONFIG["state_version"]


# ── App-level settings (~/.config/nico/settings.json) ────────────────────────
# Program preferences that stay on this machine: config path, language, theme.

def get_app_settings() -> dict:
    import json
    import copy
    import time
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
    APP_SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    existing = get_app_settings()
    existing.update(settings)
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
    """Save config.json to the nixos config directory."""
    import json
    f = _config_json(nixos_dir)
    existing = load_config_settings(nixos_dir)
    existing.update(settings)
    with open(f, "w") as fh:
        json.dump(existing, fh, indent=2, ensure_ascii=False)


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

    home_file = Path(nixos_config_dir) / "home.nix"
    if home_file.exists():
        try:
            content = home_file.read_text(encoding="utf-8")
            hm = _imp.parse_home_config(strip_brick_blocks(content))
            data["home_manager"] = hm
            data["hm_brick_blocks"] = extract_brick_blocks(content)
        except OSError:
            pass

    return data


def save_config(nixos_config_dir: str, data: dict) -> None:
    """Generate configuration.nix (and home.nix if home_manager.enabled)
    and write them to nixos_config_dir immediately."""
    import copy
    from . import generator as _gen
    from . import hm_generator as _hm
    from .brix import extract_brick_blocks, strip_brick_blocks, brix_content_to_bricks
    from . import importer as _imp

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

    # Home Manager
    hm = data.get("home_manager") or {}
    if hm.get("enabled"):
        home_file = Path(nixos_config_dir) / "home.nix"
        hm = dict(hm)
        if not data.get("hm_brick_blocks") and home_file.exists():
            try:
                home_content = home_file.read_text(encoding="utf-8")
                data["hm_brick_blocks"] = extract_brick_blocks(home_content)
                if not data["hm_brick_blocks"]:
                    clean_home = strip_brick_blocks(home_content)
                    recognized_home = _imp.parse_home_config(clean_home)
                    rest = _imp.build_home_rest_brix(clean_home, recognized_home)
                    if rest.strip():
                        data["hm_brick_blocks"] = brix_content_to_bricks(rest, section="End")
            except OSError:
                pass
        hm["hm_brick_blocks"] = data.get("hm_brick_blocks", {})
        home_file.write_text(_hm.generate_home_nix(hm), encoding="utf-8")


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
