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
    targets = [Path(nixos_dir) / "configuration.nix"]
    if is_flake:
        targets.append(Path(nixos_dir) / "flake.nix")

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
            findings.append(Finding(
                rule_id="duplicate_attrs",
                severity="error",
                message=f"{nix_file.name}: Doppelte Attribute gefunden.",
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


# ── Rule function registry ─────────────────────────────────────────────────────

_RULE_FNS: dict[str, object] = {
    "user_in_config":    _rule_user_in_config,
    "flake_host_exists": _rule_flake_host_exists,
    "hardware_imported": _rule_hardware_imported,
    "hardware_matches":  _rule_hardware_matches,
    "duplicate_attrs":   _rule_duplicate_attrs,
    "imports_exist":     _rule_imports_exist,
    "brix_redundant":    _rule_brix_redundant,
}
