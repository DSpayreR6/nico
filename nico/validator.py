"""
NiCo configuration validator.

Each rule is a callable that receives (nixos_dir, config, is_flake, host) and
returns a list of Finding objects.  run_validation() executes the enabled subset.

host: optional host name for multi-host flake configs.  When provided, hardware
rules check the host's own files; otherwise the root config files are used.
"""
from __future__ import annotations

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
    message: str
    detail: str = ""


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
         "Überflüssige Brix-Blöcke",
         "Erkennt Brix-Blöcke deren Inhalt 1:1 über das NiCo-Panel konfigurierbar wäre.",
         "info"),
    Rule("hm_user_defined",
         "HM-User definiert",
         "Prüft ob home-manager.users.<user> in der Flake-Config definiert ist.",
         "warning", flake_only=True),
    Rule("hm_allowunfree",
         "HM allowUnfree konsistent",
         "Prüft ob nixpkgs.config.allowUnfree in NixOS- und HM-Config übereinstimmt.",
         "warning"),
    Rule("snapper_btrfs",
         "Snapper-Mountpoints prüfen",
         "Prüft ob die konfigurierten Snapper-Mountpoints existieren und btrfs sind.",
         "error"),
    Rule("snapper_in_host",
         "Snapper in Host-Config",
         "Warnt wenn Snapper in einer Flake-Config mit mehreren Hosts in der Basis-Config steht.",
         "info", flake_only=True),
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
                message=f"Regel '{rule.id}' konnte nicht ausgefuehrt werden.",
                detail=str(exc),
            ))

    return [
        {"rule_id": f.rule_id, "severity": f.severity,
         "message": f.message, "detail": f.detail}
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
        hosts_dir = config.get("hosts_dir", "hosts")
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
            message=f'Benutzer "{current}" ist in keiner Config-Datei angelegt.',
            detail="Nach einem Rebuild wäre kein Login möglich. "
                   f"Benutzer unter users.users.{current} eintragen.",
        )]
    return []


def _rule_flake_host_exists(nixos_dir: str, config: dict, is_flake: bool,
                            host: str | None = None) -> list[Finding]:
    flake_path = Path(nixos_dir) / "flake.nix"
    if not flake_path.exists():
        return []

    # When a specific host is selected, only check that one
    hosts: list[str] = [host] if host else (config.get("flake_hosts") or [])
    if not hosts:
        return []

    try:
        content = flake_path.read_text(encoding="utf-8")
    except OSError:
        return []

    defined = set(re.findall(r'nixosConfigurations\s*\.\s*"?([\w-]+)"?', content))
    findings = []
    for host in hosts:
        if host not in defined:
            findings.append(Finding(
                rule_id="flake_host_exists",
                severity="error",
                message=f'Host "{host}" fehlt unter nixosConfigurations in flake.nix.',
                detail="Host ist konfiguriert, aber nicht als Flake-Output deklariert.",
            ))
    return findings


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
            message=f"hardware-configuration.nix existiert, ist aber nicht in {co_name} eingebunden.",
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
            message="Hardware-Config enthält Disk-UUIDs die auf diesem System nicht existieren.",
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
                message=f"{label}: Doppelte Attribute gefunden.",
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
                    message=f"Fehlende Datei in {nix_file.name}, Zeile {line_no}: {raw_path}",
                    detail=str(resolved),
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
                findings.append(Finding(
                    rule_id="brix_redundant",
                    severity="info",
                    message=f'Brix "{name}" koennte ueber das NiCo-Panel konfiguriert werden.',
                    detail="Inhalt ist vollständig als bekannte NixOS-Option erkannt. "
                           "Nach einem Import wäre dieser Brix überflüssig.",
                ))
        except Exception:
            continue

    return findings


def _rule_hm_user_defined(nixos_dir: str, config: dict, is_flake: bool,
                          host: str | None = None) -> list[Finding]:
    hm = config.get("home_manager") or {}
    if not hm.get("enabled"):
        return []
    username = (hm.get("username") or "").strip()
    if not username:
        return []

    # Match both quoted and unquoted: home-manager.users.alice or home-manager.users."alice"
    pattern = re.compile(
        r'home-manager\s*\.\s*users\s*\.\s*"?' + re.escape(username) + r'"?',
    )
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
        message=f'home-manager.users.{username} ist in keiner .nix-Datei definiert.',
        detail="Nach einer HM-Integration muss der User explizit über "
               f"home-manager.users.{username} eingebunden werden, "
               "z. B. in flake.nix oder einem eingebundenen Modul.",
    )]


def _rule_hm_allowunfree(nixos_dir: str, config: dict, is_flake: bool,
                         host: str | None = None) -> list[Finding]:
    hm = config.get("home_manager") or {}
    if not hm.get("enabled"):
        return []
    if not config.get("allowUnfree"):
        return []

    pattern = re.compile(r'nixpkgs\s*\.\s*config\s*\.\s*allowUnfree\s*=\s*true')

    base = Path(nixos_dir)
    cfg_settings = config.get("_cfg_settings") or {}
    hm_dir_name = cfg_settings.get("hm_dir") or config.get("hm_dir") or "home"

    # Collect HM-side .nix files: home.nix at root + everything under hm_dir
    hm_files: list[Path] = []
    root_home = base / "home.nix"
    if root_home.exists():
        hm_files.append(root_home)
    hm_subdir = base / hm_dir_name
    if hm_subdir.is_dir():
        hm_files.extend(
            f for f in hm_subdir.rglob("*.nix") if ".git" not in f.parts
        )

    hm_has_unfree = False
    for nix_file in hm_files:
        try:
            if pattern.search(nix_file.read_text(encoding="utf-8")):
                hm_has_unfree = True
                break
        except OSError:
            continue

    if hm_has_unfree:
        return []

    hm_hint = "home.nix"
    if hm_files:
        hm_hint = " oder ".join(str(f.relative_to(base)) for f in hm_files[:2])
    else:
        hm_hint = f"home.nix oder {hm_dir_name}/"

    return [Finding(
        rule_id="hm_allowunfree",
        severity="warning",
        message="nixpkgs.config.allowUnfree fehlt in der HM-Konfiguration.",
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
                message=f'{target}: Mountpoint "{mount}" existiert nicht.',
                detail="Der eingetragene Pfad muss auf diesem System vorhanden sein.",
            ))
            continue
        if not os.path.ismount(mount):
            findings.append(Finding(
                rule_id="snapper_btrfs",
                severity="error",
                message=f'{target}: Mountpoint "{mount}" ist nicht gemountet.',
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
                message=f'{target}: "{mount}" ist kein btrfs-Dateisystem (erkannt: "{fstype}").',
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
        message="Snapper ist in der Basis-Config konfiguriert, nicht pro Host.",
        detail="Bei mehreren Flake-Hosts haben Hosts unterschiedliche Subvolumes. "
               "Die Snapper-Config sollte in der jeweiligen Host-Config stehen.",
    )]


# ── Rule function registry ─────────────────────────────────────────────────────

_RULE_FNS: dict[str, object] = {
    "user_in_config":    _rule_user_in_config,
    "flake_host_exists": _rule_flake_host_exists,
    "hardware_imported": _rule_hardware_imported,
    "hardware_matches":  _rule_hardware_matches,
    "duplicate_attrs":   _rule_duplicate_attrs,
    "imports_exist":     _rule_imports_exist,
    "brix_redundant":    _rule_brix_redundant,
    "hm_user_defined":   _rule_hm_user_defined,
    "hm_allowunfree":    _rule_hm_allowunfree,
    "snapper_btrfs":     _rule_snapper_btrfs,
    "snapper_in_host":   _rule_snapper_in_host,
}
