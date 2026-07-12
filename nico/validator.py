"""
NiCo configuration validator.

Each rule is a callable that receives (nixos_dir, config, is_flake, host) and
returns a list of Finding objects.  run_validation() executes the enabled subset.

host: optional host name for multi-host flake configs.  When provided, hardware
rules check the host's own files; otherwise the root config files are used.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

# ── Data types ─────────────────────────────────────────────────────────────────

@dataclass
class Finding:
    rule_id: str
    severity: str    # "error" | "warning" | "info"
    message: str          # German fallback text
    detail: str = ""
    message_key: str = ""            # i18n key (validator.f.*) for the frontend
    params: "tuple | list" = ()      # positional params for {0},{1},… in the key


@dataclass
class Rule:
    id: str
    label: str
    description: str
    severity: str    # default severity shown in UI
    flake_only: bool = False


# ── Rule catalogue ─────────────────────────────────────────────────────────────

ALL_RULES: list[Rule] = [
    Rule("user_in_config",
         "Benutzer in Config",
         "Prüft ob der aktuelle Systembenutzer in users.users.* angelegt ist.",
         "error"),
    Rule("flake_host_exists",
         "Flake-Host vorhanden",
         "Prüft ob der gewählte Host unter nixosConfigurations in flake.nix steht.",
         "error", flake_only=True),
    Rule("host_orphaned",
         "Verwaister Host",
         "Erkennt Host-Verzeichnisse die weder in flake.nix noch über imports eingebunden sind.",
         "info"),
    Rule("flake_arch_matches",
         "Architektur passt zum Rechner",
         "Vergleicht die system-Architekturen in flake.nix mit der Architektur dieses Rechners.",
         "info", flake_only=True),
    Rule("hardware_imported",
         "Hardware-Config eingebunden",
         "Prüft ob hardware-configuration.nix in imports eingebunden ist.",
         "warning"),
    Rule("hardware_matches",
         "Hardware passt zum System",
         "Warnt wenn die Hardware-Config Disk-UUIDs enthält die auf diesem System nicht existieren.",
         "warning"),
    Rule("duplicate_attrs",
         "Doppelte Attribute",
         "Meldet doppelte Top-Level-Attribute die Nix nicht akzeptiert.",
         "error"),
    Rule("imports_exist",
         "Import-Pfade vorhanden",
         "Prüft ob alle in imports referenzierten Dateipfade auf Platte existieren.",
         "error"),
    Rule("brix_redundant",
         "Brix-Blöcke mit Panel-Optionen",
         "Erkennt Brix-Blöcke deren Inhalt vermutlich vollständig über das NiCo-Panel konfigurierbar wäre.",
         "info"),
    Rule("hm_user_defined",
         "HM-Referenz in Flake",
         "Prüft ob home-manager.users.* in einer .nix-Datei referenziert wird wenn HM-Dateien vorhanden sind.",
         "warning", flake_only=True),
    Rule("hm_missing_file",
         "HM-Datei fehlt",
         "Prüft ob in flake.nix referenzierte HM-Dateien auf Platte existieren.",
         "error", flake_only=True),
    Rule("hm_orphan_root",
         "Verwaiste home.nix",
         "Erkennt eine home.nix im Config-Root die in keiner anderen Datei referenziert wird.",
         "info"),
    Rule("hm_allowunfree",
         "HM allowUnfree konsistent",
         "Prüft ob nixpkgs.config.allowUnfree in NixOS- und HM-Config übereinstimmt.",
         "warning"),
    Rule("flake_hm_branch",
         "Home Manager Branch passt zu nixpkgs",
         "Prüft ob der home-manager Input einen Release-Branch hat (release-XX.YY) "
         "der zur nixpkgs-Version übereinstimmt.",
         "error", flake_only=True),
    Rule("state_version_match",
         "stateVersion passt zu nixpkgs",
         "Prüft ob system.stateVersion in den NixOS-Configs zur nixpkgs-Release-Version passt.",
         "warning", flake_only=True),
    Rule("hm_state_version_match",
         "HM stateVersion konsistent",
         "Prüft ob home.stateVersion in HM-Configs mit system.stateVersion übereinstimmt.",
         "warning"),
    Rule("snapper_btrfs",
         "Snapper-Mountpoints prüfen",
         "Prüft ob die konfigurierten Snapper-Mountpoints existieren und btrfs sind.",
         "error"),
    Rule("snapper_in_host",
         "Snapper in Host-Config",
         "Warnt wenn Snapper in einer Flake-Config mit mehreren Hosts in der Basis-Config steht.",
         "info", flake_only=True),
    Rule("git_missing_gitignore",
         ".gitignore prüfen",
         "Warnt wenn .gitignore fehlt oder empfohlene Einträge für NiCo-Dateien (Logs, Backups) fehlen.",
         "warning"),
    Rule("git_large_log",
         "Großes Rebuild-Log",
         "Warnt wenn nixos-rebuild.log größer als 100 MB ist und versehentlich in Git committed werden könnte.",
         "warning"),
    Rule("git_foreign_files",
         "Fremddateien in Git",
         "Listet Dateien, die nicht zur Config gehören, aber in Git getrackt und hochgeladen werden.",
         "info"),
]

RULE_MAP: dict[str, Rule] = {r.id: r for r in ALL_RULES}


def default_validation_rules() -> dict[str, bool]:
    """Return a dict with all rules enabled (default for new configs)."""
    return {r.id: True for r in ALL_RULES}


def rules_as_dicts() -> list[dict]:
    """Serialise ALL_RULES for the frontend (/api/validate/rules)."""
    return [
        {"id": r.id, "label": r.label, "description": r.description,
         "severity": r.severity, "flake_only": r.flake_only}
        for r in ALL_RULES
    ]


# ── Public API ─────────────────────────────────────────────────────────────────

def run_validation(
    nixos_dir: str,
    enabled_rules: dict[str, bool],
    config: dict,
    host: str | None = None,
) -> list[dict]:
    """
    Execute all enabled (and applicable) rules and return a list of finding
    dicts: {rule_id, severity, message, detail}.

    host: when given, hardware rules check that host's files specifically.
    """
    is_flake = (Path(nixos_dir) / "flake.nix").exists()
    findings: list[Finding] = []

    for rule in ALL_RULES:
        if not enabled_rules.get(rule.id, True):
            continue
        if rule.flake_only and not is_flake:
            continue
        fn = _RULE_FNS.get(rule.id)
        if fn is None:
            continue
        try:
            findings.extend(fn(nixos_dir, config, is_flake, host))
        except Exception as exc:
            findings.append(Finding(
                rule_id=rule.id,
                severity="info",
                message=f"Regel '{rule.id}' konnte nicht ausgefuehrt werden.", message_key="validator.f.rule_failed", params=[rule.id],
                detail=str(exc),
            ))

    return [
        {"rule_id": f.rule_id, "severity": f.severity,
         "message": f.message, "detail": f.detail,
         "message_key": f.message_key, "params": [str(p) for p in f.params]}
        for f in findings
    ]


# ── Helpers ────────────────────────────────────────────────────────────────────

def _extract_imports(nix_content: str) -> list[str]:
    """Return file paths found inside imports = [...] in nix_content."""
    return [p for p, _ in _extract_imports_with_lines(nix_content)]


def _extract_imports_with_lines(nix_content: str) -> list[tuple[str, int]]:
    """Return (path, line_number) tuples from imports = [...] in nix_content.

    Only plain relative paths (./foo) are returned.  Expression paths like
    (modulesPath + "...") and channel paths like <nixpkgs/...> are skipped –
    they cannot be resolved as plain filesystem paths.
    """
    from .brix import strip_brick_blocks
    clean = strip_brick_blocks(nix_content)
    m = re.search(r'imports\s*=\s*\[([^\]]*)\]', clean, re.DOTALL)
    if not m:
        return []
    base_line = clean[:m.start(1)].count('\n') + 1
    results: list[tuple[str, int]] = []
    for offset, line in enumerate(m.group(1).split('\n')):
        # Remove comments
        line_clean = re.sub(r'#[^\n]*', '', line)
        line_no = base_line + offset
        # Only match unquoted ./relative or quoted "./relative" – never absolute or
        # expression paths.  This avoids false positives from (modulesPath + "...")
        # patterns that hardware-configuration.nix commonly contains.
        for path in re.findall(r'(?<!["\w])(\.\/[\w./\-]+)', line_clean):
            results.append((path.strip(), line_no))
        for path in re.findall(r'"(\./[^"]+)"', line_clean):
            results.append((path.strip(), line_no))
    return [(p, l) for p, l in results if p]


# ── Rule implementations ───────────────────────────────────────────────────────

_EXCL_USERS = frozenset({'root', 'nobody', 'guest', 'gast'})


def _host_paths(nixos_dir: str, config: dict, host: str | None) -> tuple[Path, str]:
    """Return (base_dir, config_filename) for the given host (or root config)."""
    base = Path(nixos_dir)
    if host:
        # hosts_dir is a config-level setting (config.json), not part of the NixOS data dict
        from . import config_manager as _cm
        cfg_settings = _cm.load_config_settings(nixos_dir)
        hosts_dir = (cfg_settings.get("hosts_dir") or "hosts").strip() or "hosts"
        return base / hosts_dir / host, "default.nix"
    return base, "configuration.nix"


def _rule_user_in_config(nixos_dir: str, config: dict, is_flake: bool,
                         host: str | None = None) -> list[Finding]:
    try:
        current = (
            os.environ.get("USER")
            or os.environ.get("LOGNAME")
            or subprocess.run(
                ["whoami"], capture_output=True, text=True, timeout=3
            ).stdout.strip()
        )
    except Exception:
        return []

    if not current or current in _EXCL_USERS:
        return []

    # Scan ALL .nix files – catches users defined in host configs too
    known: set[str] = set()
    for nix_file in sorted(Path(nixos_dir).rglob("*.nix")):
        if ".git" in nix_file.parts:
            continue
        try:
            content = nix_file.read_text(encoding="utf-8")
        except OSError:
            continue
        for m in re.finditer(r'users\.users\.([\w]+)\s*[={]', content):
            uname = m.group(1)
            if uname not in _EXCL_USERS:
                known.add(uname)

    if current not in known:
        return [Finding(
            rule_id="user_in_config",
            severity="error",
            message=f'Benutzer "{current}" ist in keiner Config-Datei angelegt.', message_key="validator.f.user_in_config", params=[current],
            detail="Nach einem Rebuild wäre kein Login möglich. "
                   f"Benutzer unter users.users.{current} eintragen.",
        )]
    return []


def _rule_flake_host_exists(nixos_dir: str, config: dict, is_flake: bool,
                            host: str | None = None) -> list[Finding]:
    flake_path = Path(nixos_dir) / "flake.nix"
    if not flake_path.exists():
        return []

    # When a specific host is selected, only check that one.
    # config["flake_hosts"] entries are dicts ({"name": ...}); normalize to names.
    raw_hosts = [host] if host else (config.get("flake_hosts") or [])
    hosts = [h.get("name") if isinstance(h, dict) else h for h in raw_hosts]
    hosts = [h for h in hosts if h]
    if not hosts:
        return []

    try:
        content = flake_path.read_text(encoding="utf-8")
    except OSError:
        return []

    defined = set(re.findall(r'nixosConfigurations\s*\.\s*"?([\w-]+)"?', content))
    findings = []
    for host_name in hosts:
        if host_name not in defined:
            findings.append(Finding(
                rule_id="flake_host_exists",
                severity="error",
                message=f'Host "{host_name}" fehlt unter nixosConfigurations in flake.nix.', message_key="validator.f.flake_host_exists", params=[host_name],
                detail="Host ist konfiguriert, aber nicht als Flake-Output deklariert.",
            ))
    return findings


def _rule_host_orphaned(nixos_dir: str, config: dict, is_flake: bool,
                        host: str | None = None) -> list[Finding]:
    """Report host directories on disk that are not wired into the build.

    Flake mode: the directory is not referenced anywhere in flake.nix.
    Non-flake mode: the directory is not reachable via imports from
    configuration.nix. Orphaned hosts may be intentional – info only.
    """
    base = Path(nixos_dir)
    from . import config_manager as _cm
    cfg_settings = _cm.load_config_settings(nixos_dir)
    hosts_dir = (cfg_settings.get("hosts_dir") or "hosts").strip() or "hosts"
    hosts_root = base / hosts_dir
    if not hosts_root.is_dir():
        return []

    host_names = sorted(
        d.name for d in hosts_root.iterdir()
        if d.is_dir() and (d / "default.nix").is_file()
    )
    if not host_names:
        return []

    if is_flake:
        flake_path = base / "flake.nix"
        if not flake_path.exists():
            return []
        try:
            content = flake_path.read_text(encoding="utf-8")
        except OSError:
            return []
        defined = set(re.findall(r'nixosConfigurations\s*\.\s*"?([\w-]+)"?', content))
        orphans = [
            n for n in host_names
            if n not in defined and f"{hosts_dir}/{n}" not in content
        ]
    else:
        # Collect every file reachable via imports starting at configuration.nix.
        reachable: set[Path] = set()
        queue = [base / "configuration.nix"]
        while queue:
            f = queue.pop().resolve()
            if f in reachable or not f.is_file():
                continue
            reachable.add(f)
            try:
                content = f.read_text(encoding="utf-8")
            except OSError:
                continue
            # Unlike _extract_imports (./-only), also follow ../-imports here:
            # modules in subdirectories commonly bind hosts via ../hosts/<name>.
            from .brix import strip_brick_blocks
            clean = strip_brick_blocks(content)
            m = re.search(r'imports\s*=\s*\[([^\]]*)\]', clean, re.DOTALL)
            if not m:
                continue
            seg = re.sub(r'#[^\n]*', '', m.group(1))
            rels = re.findall(r'(?<!["\w])(\.\.?/[\w./\-]+)', seg)
            rels += re.findall(r'"(\.\.?/[^"]+)"', seg)
            for rel in rels:
                p = f.parent / rel
                if p.is_dir():
                    p = p / "default.nix"
                queue.append(p)
        orphans = [
            n for n in host_names
            if (hosts_root / n / "default.nix").resolve() not in reachable
        ]

    return [
        Finding(
            rule_id="host_orphaned",
            severity="info",
            message=f'Host "{n}" ist nirgends eingebunden und wird beim Rebuild ignoriert.',
            message_key="validator.f.host_orphaned", params=[n],
            detail="Kann Absicht sein – das Verzeichnis bleibt unangetastet.",
        )
        for n in orphans
    ]


def _rule_flake_arch_matches(nixos_dir: str, config: dict, is_flake: bool,
                             host: str | None = None) -> list[Finding]:
    """Warn (info) when flake.nix targets a different CPU architecture than
    this machine. A rebuild for a foreign arch fails before activation, but
    the nix error is cryptic – this rule translates it into a plain hint.
    Mismatches are legitimate when maintaining a config for another device."""
    import platform
    flake_path = Path(nixos_dir) / "flake.nix"
    if not flake_path.exists():
        return []
    try:
        content = flake_path.read_text(encoding="utf-8")
    except OSError:
        return []
    machine = platform.machine()
    if not machine:
        return []
    local = f"{machine}-linux"
    archs = set(re.findall(r'system\s*=\s*"([\w-]+-linux)"', content))
    return [
        Finding(
            rule_id="flake_arch_matches",
            severity="info",
            message=f'flake.nix: Architektur "{arch}" ≠ dieser Rechner ("{local}").',
            message_key="validator.f.flake_arch_matches", params=[arch, local],
            detail="Beabsichtigt, wenn die Config für ein anderes Gerät gepflegt wird.",
        )
        for arch in sorted(archs)
        if arch != local
    ]


def _rule_hardware_imported(nixos_dir: str, config: dict, is_flake: bool,
                            host: str | None = None) -> list[Finding]:
    base, co_name = _host_paths(nixos_dir, config, host)
    hw = base / "hardware-configuration.nix"
    co = base / co_name
    if not hw.exists():
        return []
    if not co.exists():
        return []

    try:
        content = co.read_text(encoding="utf-8")
    except OSError:
        return []

    paths = _extract_imports(content)
    if not any("hardware-configuration" in p for p in paths):
        return [Finding(
            rule_id="hardware_imported",
            severity="warning",
            message=f"hardware-configuration.nix existiert, ist aber nicht in {co_name} eingebunden.", message_key="validator.f.hardware_imported", params=[co_name],
            detail="In imports = [ ./hardware-configuration.nix ] aufnehmen.",
        )]
    return []


def _rule_hardware_matches(nixos_dir: str, config: dict, is_flake: bool,
                           host: str | None = None) -> list[Finding]:
    base, _ = _host_paths(nixos_dir, config, host)
    hw = base / "hardware-configuration.nix"
    if not hw.exists():
        return []

    try:
        content = hw.read_text(encoding="utf-8")
    except OSError:
        return []

    hw_uuids = set(re.findall(
        r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}',
        content, re.IGNORECASE,
    ))
    if not hw_uuids:
        return []

    try:
        result = subprocess.run(
            ["blkid", "-s", "UUID", "-o", "value"],
            capture_output=True, text=True, timeout=5,
        )
        local_uuids = {u.strip().lower() for u in result.stdout.splitlines() if u.strip()}
    except Exception:
        return []  # blkid not available → skip silently

    unknown = {u for u in hw_uuids if u.lower() not in local_uuids}
    if unknown:
        sample = sorted(unknown)[:3]
        extra = f" und {len(unknown) - 3} weitere" if len(unknown) > 3 else ""
        return [Finding(
            rule_id="hardware_matches",
            severity="warning",
            message="Hardware-Config enthält Disk-UUIDs die auf diesem System nicht existieren.", message_key="validator.f.hardware_matches", params=[],
            detail="Unbekannte UUIDs: " + ", ".join(sample) + extra,
        )]
    return []


def _rule_duplicate_attrs(nixos_dir: str, config: dict, is_flake: bool,
                          host: str | None = None) -> list[Finding]:
    findings = []
    base = Path(nixos_dir)
    targets = [
        nix_file for nix_file in sorted(base.rglob("*.nix"))
        if ".git" not in nix_file.parts
    ]

    for nix_file in targets:
        if not nix_file.exists():
            continue
        try:
            content = nix_file.read_text(encoding="utf-8")
        except OSError:
            continue

        from . import nix_parser as _np
        from .brix import strip_brick_blocks

        # key → list of line numbers where it appears
        seen: dict[str, list[int]] = {}
        result = _np.parse(strip_brick_blocks(content))
        if result.available:
            for b in result.known + result.unknown:
                seen.setdefault(b.key, []).append(b.start_line)
        else:
            # Regex fallback with depth tracking – only collect attributes at
            # brace depth 1 (the body of the outermost NixOS module { ... }).
            # This avoids false positives from nested blocks like snapper configs
            # or fileSystems entries.
            depth = 0
            for i, line in enumerate(content.split('\n'), start=1):
                # Check depth BEFORE counting braces on this line
                if depth == 1:
                    m = re.match(r'\s*([\w.\-"]+(?:\.[\w.\-"]+)*)\s*=\s*(?!=)', line)
                    if m:
                        seen.setdefault(m.group(1), []).append(i)
                depth += line.count('{') - line.count('}')

        dupes = {k: lines for k, lines in seen.items() if len(lines) > 1}
        if dupes:
            parts = [
                f"'{k}' mehrfach: " + ", ".join(f"Zeile {l}" for l in lines)
                for k, lines in sorted(dupes.items())
            ]
            try:
                label = str(nix_file.relative_to(base))
            except ValueError:
                label = nix_file.name
            findings.append(Finding(
                rule_id="duplicate_attrs",
                severity="error",
                message=f"{label}: Doppelte Attribute gefunden.", message_key="validator.f.duplicate_attrs", params=[label],
                detail="\n".join(parts),
            ))
    return findings


def _rule_imports_exist(nixos_dir: str, config: dict, is_flake: bool,
                        host: str | None = None) -> list[Finding]:
    findings = []
    base = Path(nixos_dir)

    for nix_file in sorted(base.rglob("*.nix")):
        # Skip the .git directory
        if ".git" in nix_file.parts:
            continue
        try:
            content = nix_file.read_text(encoding="utf-8")
        except OSError:
            continue

        for raw_path, line_no in _extract_imports_with_lines(content):
            resolved = (nix_file.parent / raw_path).resolve()
            if not resolved.exists():
                findings.append(Finding(
                    rule_id="imports_exist",
                    severity="error",
                    message=f"Fehlende Datei in {nix_file.name}, Zeile {line_no}: {raw_path}", message_key="validator.f.imports_exist", params=[nix_file.name, line_no, raw_path],
                    detail=str(resolved),
                ))
    return findings


_FIELD_SECTION: dict[str, str] = {
    # Boot
    "boot_loader": "Boot", "secure_boot": "Boot", "lanzaboote": "Boot",
    "kernel": "Boot", "boot_efi_can_touch": "Boot", "boot_config_limit": "Boot",
    # System
    "hostname": "System", "state_version": "System", "allowUnfree": "System",
    "nix_args": "System",
    # Lokalisierung
    "timezone": "Lokalisierung", "locale": "Lokalisierung",
    "keyboard_layout": "Lokalisierung", "keyboard_variant": "Lokalisierung",
    "keyboard_console": "Lokalisierung", "extra_locale": "Lokalisierung",
    # Netzwerk
    "networkmanager": "Netzwerk", "ssh": "Netzwerk",
    "firewall_disable": "Netzwerk", "firewall_tcp_enable": "Netzwerk",
    "firewall_tcp_ports": "Netzwerk", "firewall_udp_enable": "Netzwerk",
    "firewall_udp_ports": "Netzwerk",
    # Services
    "printing": "Services", "avahi": "Services", "bluetooth": "Services",
    "blueman": "Services", "libinput": "Services", "fprintd": "Services",
    "pcscd": "Services", "sunshine": "Services",
    # Desktop
    "desktop_environment": "Desktop", "autologin_user": "Desktop",
    # Audio
    "pipewire": "Audio", "pipewire_32bit": "Audio",
    # Benutzer
    "username": "Benutzer", "user_description": "Benutzer",
    "user_initial_password": "Benutzer", "user_uid": "Benutzer",
    "user_groups": "Benutzer", "user_groups_extra": "Benutzer",
    "user_shell": "Benutzer", "user_extra_nix": "Benutzer",
    # Programme
    "packages": "Programme", "steam": "Programme",
    # Schriftarten
    "fonts": "Schriftarten", "fonts_extra": "Schriftarten",
    # Nix & System
    "flakes": "Nix & System", "nix_optimize_store": "Nix & System",
    "nix_gc": "Nix & System", "nix_gc_frequency": "Nix & System",
    "nix_gc_age": "Nix & System",
    # Hardware
    "opengl": "Hardware", "cpu_microcode": "Hardware",
    "enable_all_firmware": "Hardware", "hardware_config": "Hardware",
    "zram_swap": "Hardware",
    # Virtualisierung
    "docker": "Virtualisierung", "docker_rootless": "Virtualisierung",
    "podman": "Virtualisierung", "podman_docker_compat": "Virtualisierung",
    "virtualbox_host": "Virtualisierung", "libvirtd": "Virtualisierung",
    "virt_manager": "Virtualisierung",
    # Dateisystem & Backup
    "btrfs_scrub": "Dateisystem & Backup", "snapper_enable": "Dateisystem & Backup",
    "snapper_configs": "Dateisystem & Backup",
    # Home Manager
    "home_manager": "Home Manager",
}


def _section_hint(recognized: dict) -> str:
    sections = sorted({_FIELD_SECTION[k] for k in recognized if k in _FIELD_SECTION})
    if not sections:
        return "NiCo-Panel"
    return ", ".join(f'"{s}"' for s in sections)


def _extract_flake_input_url(content: str, input_name: str) -> tuple[str | None, int | None]:
    """Return (url, line_number) for a flake input, or (None, None) if not found."""
    lines = content.split('\n')
    pat_inline = re.compile(
        rf'(?:inputs\s*\.\s*)?{re.escape(input_name)}\s*\.\s*url\s*=\s*"([^"]+)"'
    )
    for i, line in enumerate(lines, 1):
        m = pat_inline.search(line)
        if m:
            return m.group(1), i

    # Multi-line block: input_name = { ... url = "..." ... }
    pat_block = re.compile(
        rf'(?:inputs\s*\.\s*)?{re.escape(input_name)}\s*='
    )
    in_block = False
    brace_depth = 0
    for i, line in enumerate(lines, 1):
        if not in_block:
            if pat_block.search(line) and '{' in line:
                in_block = True
                brace_depth = line.count('{') - line.count('}')
        else:
            brace_depth += line.count('{') - line.count('}')
            m = re.search(r'url\s*=\s*"([^"]+)"', line)
            if m:
                return m.group(1), i
            if brace_depth <= 0:
                in_block = False

    return None, None


def _parse_github_ref(url: str) -> str | None:
    """Extract ref (branch/tag) from github:org/repo/ref URL. Returns None if no ref."""
    if not url.startswith('github:'):
        return None
    parts = url[len('github:'):].split('/')
    if len(parts) >= 3:
        return '/'.join(parts[2:])
    return None


def _parse_nixpkgs_version(url: str) -> str | None:
    """Return 'XX.YY' for versioned nixpkgs, 'unstable' for unstable, None otherwise."""
    ref = _parse_github_ref(url)
    if ref is None:
        return None
    return _parse_nixpkgs_version_from_ref(ref)


def _parse_nixpkgs_version_from_ref(ref: str) -> str | None:
    """Return 'XX.YY' for nixos-XX.YY refs, 'unstable' for unstable refs."""
    m = re.match(r'nixos-(\d+\.\d+)', ref)
    if m:
        return m.group(1)
    if 'unstable' in ref:
        return 'unstable'
    return None


def _expected_hm_ref_for_nixpkgs_version(nixpkgs_version: str | None) -> str | None:
    if not nixpkgs_version:
        return None
    if nixpkgs_version == 'unstable':
        return 'master'
    return f"release-{nixpkgs_version}"


def _load_flake_lock_refs(nixos_dir: str) -> tuple[str | None, str | None] | None:
    lock_path = Path(nixos_dir) / "flake.lock"
    if not lock_path.exists():
        return None
    try:
        data = json.loads(lock_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None

    nodes = data.get("nodes") or {}

    def node_ref(name: str) -> str | None:
        node = nodes.get(name) or {}
        for section in ("locked", "original"):
            ref = (node.get(section) or {}).get("ref")
            if isinstance(ref, str) and ref:
                return ref
        return None

    return node_ref("nixpkgs"), node_ref("home-manager")


def _rule_flake_hm_branch(nixos_dir: str, config: dict, is_flake: bool,
                           host: str | None = None) -> list[Finding]:
    flake_path = Path(nixos_dir) / "flake.nix"
    if not flake_path.exists():
        return []
    try:
        content = flake_path.read_text(encoding="utf-8")
    except OSError:
        return []
    if 'home-manager' not in content:
        return []

    nixpkgs_url, nixpkgs_line = _extract_flake_input_url(content, 'nixpkgs')
    hm_url, hm_line = _extract_flake_input_url(content, 'home-manager')
    if hm_url is None:
        return []

    nixpkgs_version = _parse_nixpkgs_version(nixpkgs_url) if nixpkgs_url else None
    hm_ref = _parse_github_ref(hm_url)

    findings = []

    if nixpkgs_version and nixpkgs_version != 'unstable':
        expected_ref = _expected_hm_ref_for_nixpkgs_version(nixpkgs_version)
        np_loc = f"flake.nix Zeile {nixpkgs_line}: nixpkgs → {nixpkgs_url}"
        hm_loc = f"flake.nix Zeile {hm_line}: home-manager → {hm_url}"
        if hm_ref is None:
            findings.append(Finding(
                rule_id="flake_hm_branch",
                severity="error",
                message=f"flake.nix Zeile {hm_line}: home-manager hat keinen Release-Branch.", message_key="validator.f.flake_hm_branch.no_release", params=[hm_line],
                detail=(
                    f"{np_loc}\n"
                    f"{hm_loc}\n"
                    f"nixpkgs nutzt Version {nixpkgs_version}, aber home-manager hat keinen Branch "
                    f"(zieht 'master'). Korrekt: github:nix-community/home-manager/{expected_ref}"
                ),
            ))
        elif hm_ref != expected_ref:
            findings.append(Finding(
                rule_id="flake_hm_branch",
                severity="error",
                message=f"flake.nix Zeile {hm_line}: home-manager Branch '{hm_ref}' ≠ nixpkgs {nixpkgs_version}.", message_key="validator.f.flake_hm_branch.mismatch", params=[hm_line, hm_ref, nixpkgs_version],
                detail=(
                    f"{np_loc}\n"
                    f"{hm_loc}\n"
                    f"Erwartet: github:nix-community/home-manager/{expected_ref}"
                ),
            ))
    elif nixpkgs_version == 'unstable':
        expected_ref = _expected_hm_ref_for_nixpkgs_version(nixpkgs_version)
        if hm_ref and hm_ref != expected_ref:
            findings.append(Finding(
                rule_id="flake_hm_branch",
                severity="error",
                message=f"flake.nix Zeile {hm_line}: home-manager Branch '{hm_ref}', aber nixpkgs ist unstable.", message_key="validator.f.flake_hm_branch.unstable", params=[hm_line, hm_ref],
                detail=(
                    f"flake.nix Zeile {nixpkgs_line}: nixpkgs → {nixpkgs_url}\n"
                    f"flake.nix Zeile {hm_line}: home-manager → {hm_url}\n"
                    "Für nixpkgs-unstable sollte home-manager auf 'master' zeigen."
                ),
            ))
    elif nixpkgs_url is None and hm_ref is None:
        findings.append(Finding(
            rule_id="flake_hm_branch",
            severity="warning",
            message=f"flake.nix Zeile {hm_line}: home-manager hat keinen expliziten Branch.", message_key="validator.f.flake_hm_branch.no_branch", params=[hm_line],
            detail=(
                f"flake.nix Zeile {hm_line}: home-manager → {hm_url}\n"
                "Ohne Branch (release-XX.YY) wird 'master' verwendet – "
                "kann bei stabilen nixpkgs-Releases zu Inkompatibilitäten führen."
            ),
        ))

    lock_refs = _load_flake_lock_refs(nixos_dir)
    if lock_refs:
        lock_np_ref, lock_hm_ref = lock_refs
        lock_np_version = _parse_nixpkgs_version_from_ref(lock_np_ref or "")
        expected_lock_hm_ref = _expected_hm_ref_for_nixpkgs_version(lock_np_version)
        lock_hm_mismatch = bool(
            expected_lock_hm_ref and lock_hm_ref and lock_hm_ref != expected_lock_hm_ref
        )
        if lock_hm_mismatch:
            findings.append(Finding(
                rule_id="flake_hm_branch",
                severity="error",
                message="flake.lock: home-manager und nixpkgs sind auf unterschiedliche Release-Zweige gelockt.", message_key="validator.f.flake_hm_branch.lock_diverged", params=[],
                detail=(
                    f"flake.lock: nixpkgs ref = {lock_np_ref or '<unbekannt>'}\n"
                    f"flake.lock: home-manager ref = {lock_hm_ref}\n"
                    f"Erwartet: home-manager ref = {expected_lock_hm_ref}\n"
                    "Lockfile aktualisieren: nix flake update"
                ),
            ))

        expected_flake_hm_ref = _expected_hm_ref_for_nixpkgs_version(nixpkgs_version)
        if (expected_flake_hm_ref and lock_hm_ref and lock_hm_ref != expected_flake_hm_ref
                and expected_flake_hm_ref != expected_lock_hm_ref):
            findings.append(Finding(
                rule_id="flake_hm_branch",
                severity="warning",
                message="flake.lock passt nicht zur home-manager-Auswahl in flake.nix.", message_key="validator.f.flake_hm_branch.lock_hm", params=[],
                detail=(
                    f"flake.nix Zeile {hm_line}: home-manager → {hm_url}\n"
                    f"flake.lock: home-manager ref = {lock_hm_ref}\n"
                    f"Erwartet nach flake.nix: {expected_flake_hm_ref}\n"
                    "Lockfile aktualisieren: nix flake update"
                ),
            ))

        flake_np_ref = _parse_github_ref(nixpkgs_url) if nixpkgs_url else None
        if flake_np_ref and lock_np_ref and lock_np_ref != flake_np_ref:
            findings.append(Finding(
                rule_id="flake_hm_branch",
                severity="warning",
                message="flake.lock passt nicht zur nixpkgs-Auswahl in flake.nix.", message_key="validator.f.flake_hm_branch.lock_nixpkgs", params=[],
                detail=(
                    f"flake.nix Zeile {nixpkgs_line}: nixpkgs → {nixpkgs_url}\n"
                    f"flake.lock: nixpkgs ref = {lock_np_ref}\n"
                    f"Erwartet nach flake.nix: {flake_np_ref}\n"
                    "Lockfile aktualisieren: nix flake update"
                ),
            ))

    return findings


def _rule_state_version_match(nixos_dir: str, config: dict, is_flake: bool,
                               host: str | None = None) -> list[Finding]:
    flake_path = Path(nixos_dir) / "flake.nix"
    if not flake_path.exists():
        return []
    try:
        content = flake_path.read_text(encoding="utf-8")
    except OSError:
        return []

    nixpkgs_url, nixpkgs_line = _extract_flake_input_url(content, 'nixpkgs')
    if not nixpkgs_url:
        return []
    nixpkgs_version = _parse_nixpkgs_version(nixpkgs_url)
    if not nixpkgs_version or nixpkgs_version == 'unstable':
        return []

    pat = re.compile(r'system\s*\.\s*stateVersion\s*=\s*"([^"]+)"')
    findings = []
    base = Path(nixos_dir)

    for nix_file in sorted(base.rglob("*.nix")):
        if ".git" in nix_file.parts:
            continue
        try:
            file_content = nix_file.read_text(encoding="utf-8")
        except OSError:
            continue
        try:
            rel = str(nix_file.relative_to(base))
        except ValueError:
            rel = nix_file.name

        for i, line in enumerate(file_content.split('\n'), 1):
            m = pat.search(line)
            if m:
                state_ver = m.group(1)
                if state_ver != nixpkgs_version:
                    findings.append(Finding(
                        rule_id="state_version_match",
                        severity="warning",
                        message=f"{rel} Zeile {i}: system.stateVersion \"{state_ver}\" ≠ nixpkgs {nixpkgs_version}.", message_key="validator.f.state_version_match", params=[rel, i, state_ver, nixpkgs_version],
                        detail=(
                            f"flake.nix Zeile {nixpkgs_line}: nixpkgs → {nixpkgs_url}\n"
                            f"{rel} Zeile {i}: system.stateVersion = \"{state_ver}\"\n"
                            f"Erwartet: system.stateVersion = \"{nixpkgs_version}\""
                        ),
                    ))

    return findings


def _rule_hm_state_version_match(nixos_dir: str, config: dict, is_flake: bool,
                                  host: str | None = None) -> list[Finding]:
    from . import config_manager as _cm
    cfg_settings = _cm.load_config_settings(nixos_dir)
    hm_dir_name = cfg_settings.get("hm_dir") or "home"
    base = Path(nixos_dir)
    hm_dir = base / hm_dir_name

    sys_pat = re.compile(r'system\s*\.\s*stateVersion\s*=\s*"([^"]+)"')
    hm_pat  = re.compile(r'home\s*\.\s*stateVersion\s*=\s*"([^"]+)"')

    system_versions: list[tuple[str, str, int]] = []
    hm_versions:     list[tuple[str, str, int]] = []

    for nix_file in sorted(base.rglob("*.nix")):
        if ".git" in nix_file.parts:
            continue
        try:
            file_content = nix_file.read_text(encoding="utf-8")
        except OSError:
            continue
        try:
            rel = str(nix_file.relative_to(base))
        except ValueError:
            rel = nix_file.name

        is_hm = hm_dir.exists() and nix_file.is_relative_to(hm_dir)
        pat = hm_pat if is_hm else sys_pat
        target = hm_versions if is_hm else system_versions

        for i, line in enumerate(file_content.split('\n'), 1):
            m = pat.search(line)
            if m:
                target.append((m.group(1), rel, i))

    if not system_versions or not hm_versions:
        return []

    sys_ver, sys_rel, sys_line = system_versions[0]
    findings = []
    for hm_ver, hm_rel, hm_line in hm_versions:
        if hm_ver != sys_ver:
            findings.append(Finding(
                rule_id="hm_state_version_match",
                severity="warning",
                message=f"{hm_rel} Zeile {hm_line}: home.stateVersion \"{hm_ver}\" ≠ system.stateVersion \"{sys_ver}\".", message_key="validator.f.hm_state_version_match", params=[hm_rel, hm_line, hm_ver, sys_ver],
                detail=(
                    f"{sys_rel} Zeile {sys_line}: system.stateVersion = \"{sys_ver}\"\n"
                    f"{hm_rel} Zeile {hm_line}: home.stateVersion = \"{hm_ver}\"\n"
                    "Beide Werte müssen identisch sein."
                ),
            ))

    return findings


def _rule_brix_redundant(nixos_dir: str, config: dict, is_flake: bool,
                         host: str | None = None) -> list[Finding]:
    from . import importer as _imp
    from .brix import extract_brick_blocks

    co = Path(nixos_dir) / "configuration.nix"
    if not co.exists():
        return []

    try:
        content = co.read_text(encoding="utf-8")
    except OSError:
        return []

    findings = []
    blocks = extract_brick_blocks(content)

    for name, block in blocks.items():
        text = block.get("text", "")
        # Strip brix/brick markers to get bare Nix content
        body = re.sub(r'^\s*#\s*</?br(?:ix|ick):[^>]*>\s*$', '', text,
                      flags=re.MULTILINE).strip()
        if not body:
            continue

        # Wrap in a minimal NixOS module so parse_config + tree-sitter work
        wrapped = f"{{ pkgs, config, lib, ... }}:\n{{\n{body}\n}}"
        try:
            recognized = _imp.parse_config(wrapped)
            if not recognized:
                continue
            rest = _imp.build_rest_brix(wrapped, recognized)
            if not rest.strip():
                hint = _section_hint(recognized)
                findings.append(Finding(
                    rule_id="brix_redundant",
                    severity="info",
                    message=f'Brix "{name}": Inhalt kann vermutlich im NiCo-Panel eingegeben werden – bitte prüfen.', message_key="validator.f.brix_redundant", params=[name],
                    detail=f"Erkannte Optionen in Sektion {hint}. "
                           "Nach einem Import prüfen ob alle Einstellungen korrekt übernommen wurden.",
                ))
        except Exception:
            continue

    return findings


def _rule_hm_user_defined(nixos_dir: str, config: dict, is_flake: bool,
                          host: str | None = None) -> list[Finding]:
    from . import config_manager as _cm
    cfg_settings = _cm.load_config_settings(nixos_dir)
    hm_dir_name = cfg_settings.get("hm_dir") or "home"
    hm_dir_path = Path(nixos_dir) / hm_dir_name
    if not hm_dir_path.is_dir():
        return []
    if not any(hm_dir_path.glob("*.nix")):
        return []

    pattern = re.compile(r'home-manager\s*\.\s*users\s*\.\s*"?[\w-]+"?')
    for nix_file in sorted(Path(nixos_dir).rglob("*.nix")):
        if ".git" in nix_file.parts:
            continue
        try:
            content = nix_file.read_text(encoding="utf-8")
        except OSError:
            continue
        if pattern.search(content):
            return []

    return [Finding(
        rule_id="hm_user_defined",
        severity="warning",
        message=f"HM-Dateien in {hm_dir_name}/ vorhanden, aber home-manager.users.* fehlt in allen .nix-Dateien.", message_key="validator.f.hm_user_defined", params=[hm_dir_name],
        detail=f"Die Dateien in {hm_dir_name}/ sind nicht im Flake referenziert. "
               "In flake.nix oder einem eingebundenen Modul "
               "home-manager.users.<username> = import ./<datei>.nix eintragen.",
    )]


def _rule_hm_missing_file(nixos_dir: str, config: dict, is_flake: bool,
                           host: str | None = None) -> list[Finding]:
    flake_path = Path(nixos_dir) / "flake.nix"
    if not flake_path.exists():
        return []
    try:
        content = flake_path.read_text(encoding="utf-8")
    except OSError:
        return []

    pattern = re.compile(
        r'home-manager\s*\.\s*users\s*\.\s*"?([\w-]+)"?\s*=\s*import\s+(\.\/[^\s;]+)',
    )
    findings = []
    for m in pattern.finditer(content):
        username   = m.group(1)
        import_path = m.group(2).strip().rstrip(';').strip()
        resolved   = (Path(nixos_dir) / import_path).resolve()
        if not resolved.exists():
            findings.append(Finding(
                rule_id="hm_missing_file",
                severity="error",
                message=f"flake.nix: home-manager.users.{username} referenziert fehlende Datei: {import_path}", message_key="validator.f.hm_missing_file", params=[username, import_path],
                detail=str(resolved),
            ))
    return findings


def _rule_hm_orphan_root(nixos_dir: str, config: dict, is_flake: bool,
                          host: str | None = None) -> list[Finding]:
    root_home = Path(nixos_dir) / "home.nix"
    if not root_home.exists():
        return []

    for nix_file in sorted(Path(nixos_dir).rglob("*.nix")):
        if ".git" in nix_file.parts:
            continue
        if nix_file == root_home:
            continue
        try:
            content = nix_file.read_text(encoding="utf-8")
        except OSError:
            continue
        if re.search(r'[./"]home\.nix["/\s]', content) or "home.nix" in content:
            return []

    return [Finding(
        rule_id="hm_orphan_root",
        severity="info",
        message="home.nix im Config-Root ist in keiner anderen Datei referenziert.", message_key="validator.f.hm_orphan_root", params=[],
        detail="Die Datei wird nirgendwo importiert und hat vermutlich keinen Effekt. "
               "Sie kann gelöscht werden.",
    )]


def _rule_hm_allowunfree(nixos_dir: str, config: dict, is_flake: bool,
                         host: str | None = None) -> list[Finding]:
    if not config.get("allowUnfree"):
        return []

    from . import config_manager as _cm
    cfg_settings = _cm.load_config_settings(nixos_dir)
    hm_dir_name = cfg_settings.get("hm_dir") or "home"
    base = Path(nixos_dir)
    hm_subdir = base / hm_dir_name

    hm_files: list[Path] = []
    if hm_subdir.is_dir():
        hm_files = [f for f in hm_subdir.rglob("*.nix") if ".git" not in f.parts]
    if not hm_files:
        return []

    pattern = re.compile(r'nixpkgs\s*\.\s*config\s*\.\s*allowUnfree\s*=\s*true')
    for nix_file in hm_files:
        try:
            if pattern.search(nix_file.read_text(encoding="utf-8")):
                return []
        except OSError:
            continue

    hm_hint = " oder ".join(str(f.relative_to(base)) for f in hm_files[:2])
    return [Finding(
        rule_id="hm_allowunfree",
        severity="warning",
        message="nixpkgs.config.allowUnfree fehlt in der HM-Konfiguration.", message_key="validator.f.hm_allowunfree", params=[],
        detail=f"allowUnfree ist in NiCo aktiviert, aber in {hm_hint} "
               "ist nixpkgs.config.allowUnfree = true nicht gesetzt. "
               "Unfree-Pakete in Home Manager werden sonst abgelehnt.",
    )]


def _rule_snapper_btrfs(nixos_dir: str, config: dict, is_flake: bool,
                        host: str | None = None) -> list[Finding]:
    if not config.get("snapper_enable"):
        return []

    def _snapper_target(name: str, mount: str) -> str:
        label = f'Snapper "{name}"' if name else f'Snapper-Mountpoint "{mount}"'
        if host:
            return f'{label} in Host "{host}"'
        return label

    findings = []
    for entry in (config.get("snapper_configs") or []):
        mount = (entry.get("mountpoint") or "").strip()
        name  = (entry.get("name") or "").strip()
        if not mount:
            continue
        target = _snapper_target(name, mount)
        if not os.path.exists(mount):
            findings.append(Finding(
                rule_id="snapper_btrfs",
                severity="error",
                message=f'{target}: Mountpoint "{mount}" existiert nicht.', message_key="validator.f.snapper_btrfs.missing", params=[target, mount],
                detail="Der eingetragene Pfad muss auf diesem System vorhanden sein.",
            ))
            continue
        if not os.path.ismount(mount):
            findings.append(Finding(
                rule_id="snapper_btrfs",
                severity="error",
                message=f'{target}: Mountpoint "{mount}" ist nicht gemountet.', message_key="validator.f.snapper_btrfs.not_mounted", params=[target, mount],
                detail="Snapper kann nur auf gemountete Dateisysteme angewendet werden.",
            ))
            continue
        try:
            result = subprocess.run(
                ["findmnt", "-n", "-o", "FSTYPE", mount],
                capture_output=True, text=True, timeout=5,
            )
            fstype = result.stdout.strip()
        except Exception:
            continue
        if fstype != "btrfs":
            findings.append(Finding(
                rule_id="snapper_btrfs",
                severity="error",
                message=f'{target}: "{mount}" ist kein btrfs-Dateisystem (erkannt: "{fstype}").', message_key="validator.f.snapper_btrfs.not_btrfs", params=[target, mount, fstype],
                detail="Snapper unterstützt nur btrfs. Der Mountpoint muss ein btrfs-Subvolume sein.",
            ))
    return findings


def _rule_snapper_in_host(nixos_dir: str, config: dict, is_flake: bool,
                          host: str | None = None) -> list[Finding]:
    if not config.get("snapper_enable"):
        return []
    hosts = config.get("flake_hosts") or []
    if len(hosts) <= 1:
        return []
    return [Finding(
        rule_id="snapper_in_host",
        severity="info",
        message="Snapper ist in der Basis-Config konfiguriert, nicht pro Host.", message_key="validator.f.snapper_in_host", params=[],
        detail="Bei mehreren Flake-Hosts haben Hosts unterschiedliche Subvolumes. "
               "Die Snapper-Config sollte in der jeweiligen Host-Config stehen.",
    )]


_LOG_WARN_BYTES = 100 * 1024 * 1024  # 100 MB

# Known NiCo-generated files that are problematic in git


def _rule_git_missing_gitignore(nixos_dir: str, config: dict, is_flake: bool,
                                host: str | None = None) -> list[Finding]:
    from . import git_manager as _gm
    if not _gm.is_git_repo(nixos_dir):
        return []
    path = Path(nixos_dir) / ".gitignore"
    if not path.exists():
        return [Finding(
            rule_id="git_missing_gitignore",
            severity="warning",
            message="Keine .gitignore vorhanden – NiCo-Dateien (Logs, Backups) werden in Git committet.", message_key="validator.f.git_missing_gitignore.none", params=[],
            detail="In den Einstellungen → Zeitmaschine kann die .gitignore angelegt werden.",
        )]
    existing = {l.strip() for l in path.read_text(encoding="utf-8").splitlines()
                if l.strip() and not l.startswith("#")}
    missing = [e for e in _gm.GITIGNORE_ENTRIES if e not in existing]
    if missing:
        return [Finding(
            rule_id="git_missing_gitignore",
            severity="warning",
            message=f".gitignore unvollständig – {len(missing)} empfohlene Einträge fehlen.", message_key="validator.f.git_missing_gitignore.incomplete", params=[len(missing)],
            detail="Fehlend: " + ", ".join(missing) + "\nIn den Einstellungen → Zeitmaschine ergänzen.",
        )]
    return []


def _rule_git_large_log(nixos_dir: str, config: dict, is_flake: bool,
                        host: str | None = None) -> list[Finding]:
    log_path = Path(nixos_dir) / "nixos-rebuild.log"
    if not log_path.exists():
        return []
    size = log_path.stat().st_size
    if size < _LOG_WARN_BYTES:
        return []
    size_mb = size / (1024 * 1024)
    return [Finding(
        rule_id="git_large_log",
        severity="warning",
        message=f"nixos-rebuild.log ist {size_mb:.0f} MB groß.", message_key="validator.f.git_large_log", params=[f"{size_mb:.0f}"],
        detail=f"Pfad: {log_path}\nDie Datei enthält nur Rebuild-Ausgabe und kann bedenkenlos gelöscht werden.",
    )]


def _rule_git_foreign_files(nixos_dir: str, config: dict, is_flake: bool,
                            host: str | None = None) -> list[Finding]:
    from . import git_manager as _gm
    if not _gm.is_git_repo(nixos_dir):
        return []
    tracked = _gm.list_tracked_files(nixos_dir)
    foreign = []
    config_json_tracked = False
    for f in tracked:
        p = Path(f)
        if p.suffix == ".nix" or f in ("flake.lock", ".gitignore"):
            continue
        if f == "config.json":
            # NiCo's own config settings: meant to travel with the config,
            # so it is not a foreign file – but users must not delete it.
            config_json_tracked = True
            continue
        foreign.append(f)
    if not foreign:
        return []

    detail_lines = foreign + [""]
    if config_json_tracked:
        detail_lines.append(
            "config.json gehört zu NiCo (Config-Einstellungen) und muss "
            "erhalten bleiben – nicht löschen."
        )
    detail_lines.append(
        "Hinweis: Lokal gelöschte Dateien verschwinden beim nächsten "
        "Sicherungspunkt aus dem aktuellen Stand, bleiben aber in der "
        "Git-Historie (auch im Remote) erhalten."
    )
    return [Finding(
        rule_id="git_foreign_files",
        severity="info",
        message=f"{len(foreign)} Dateien, die nicht zur Config gehören, werden in Git getrackt und hochgeladen.", message_key="validator.f.git_foreign_files", params=[len(foreign)],
        detail="\n".join(detail_lines),
    )]


# ── Rule function registry ─────────────────────────────────────────────────────

_RULE_FNS: dict[str, object] = {
    "user_in_config":        _rule_user_in_config,
    "flake_host_exists":     _rule_flake_host_exists,
    "host_orphaned":         _rule_host_orphaned,
    "flake_arch_matches":    _rule_flake_arch_matches,
    "hardware_imported":     _rule_hardware_imported,
    "hardware_matches":      _rule_hardware_matches,
    "duplicate_attrs":       _rule_duplicate_attrs,
    "imports_exist":         _rule_imports_exist,
    "brix_redundant":        _rule_brix_redundant,
    "hm_user_defined":       _rule_hm_user_defined,
    "hm_missing_file":       _rule_hm_missing_file,
    "hm_orphan_root":        _rule_hm_orphan_root,
    "hm_allowunfree":        _rule_hm_allowunfree,
    "flake_hm_branch":       _rule_flake_hm_branch,
    "state_version_match":   _rule_state_version_match,
    "hm_state_version_match": _rule_hm_state_version_match,
    "snapper_btrfs":          _rule_snapper_btrfs,
    "snapper_in_host":        _rule_snapper_in_host,
    "git_missing_gitignore":  _rule_git_missing_gitignore,
    "git_large_log":          _rule_git_large_log,
    "git_foreign_files":      _rule_git_foreign_files,
}
