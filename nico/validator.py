"""
NiCo configuration validator.

Each rule is a callable that receives (nixos_dir, config, is_flake, host) and
returns a list of Finding objects.  run_validation() executes the enabled subset.

host: optional host name for multi-host flake configs.  When provided, hardware
rules check the host's own files; otherwise the root config files are used.
"""
from __future__ import annotations

import hashlib
import json
import os
import platform
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
    detail: str = ""                 # German fallback text
    message_key: str = ""            # i18n key (validator.f.*) for the frontend
    params: "tuple | list" = ()      # positional params for {0},{1},… in the key
    detail_key: str = ""             # i18n key (validator.d.*) for `detail`
    detail_params: "tuple | list" = ()
    # Findings whose detail is pure data (a path, an exception, a list of file
    # names) carry no detail_key – there is nothing to translate in them.


@dataclass
class Rule:
    id: str
    label: str
    description: str
    severity: str    # default severity shown in UI
    flake_only: bool = False
    # host_local rules inspect the *running* machine (findmnt, /proc, disk UUIDs)
    # instead of the config text, so they can only speak for the host NiCo runs on.
    host_local: bool = False


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
         "info", flake_only=True, host_local=True),
    Rule("hardware_imported",
         "Hardware-Config eingebunden",
         "Prüft ob hardware-configuration.nix in imports eingebunden ist.",
         "error"),
    Rule("hardware_host_location",
         "Hardware-Config am erwarteten Ort",
         "Meldet eine Hardware-Config im Wurzelverzeichnis, während flake.nix sie im Host-Verzeichnis erwartet.",
         "error", flake_only=True),
    Rule("user_name_valid",
         "Benutzername gültig",
         "Prüft Benutzernamen gegen die von NixOS erlaubte Schreibweise.",
         "error"),
    Rule("user_extra_conflict",
         "Doppeltes Nutzer-Attribut",
         "Meldet Zeilen im Textfeld eines Nutzers, die ein Attribut setzen, das NiCo "
         "schon aus dem Formular schreibt – Nix lehnt doppelte Attribute ab.",
         "error"),
    Rule("user_no_password",
         "Anmeldung möglich",
         "Prüft ob jeder Benutzer, den der Rebuild neu anlegt, ein Passwort oder einen "
         "SSH-Schlüssel hat – ohne beides legt NixOS das Konto gesperrt an.",
         "error", host_local=True),
    Rule("user_uid_duplicate",
         "uid doppelt vergeben",
         "Meldet dieselbe uid bei mehreren Benutzern in der Config.",
         "error"),
    Rule("user_uid_system",
         "uid passt zum Rechner",
         "Vergleicht die uid-Angaben mit den Benutzern dieses Rechners: gewechselte uid "
         "oder eine uid, die schon jemand anderem gehört.",
         "error", host_local=True),
    Rule("group_gid_duplicate",
         "gid doppelt vergeben",
         "Meldet dieselbe gid bei mehreren Gruppen in der Config.",
         "error"),
    Rule("group_gid_system",
         "gid passt zum Rechner",
         "Vergleicht die gid-Angaben mit den Gruppen dieses Rechners: gewechselte gid "
         "oder eine gid, die schon einer anderen Gruppe gehört.",
         "error", host_local=True),
    Rule("hardware_matches",
         "Hardware passt zum System",
         "Hält an, wenn die hardware-configuration.nix dieses Rechners Datenträger "
         "nennt, die es hier nicht gibt, oder sich nicht prüfen lässt. Die Dateien "
         "anderer Hosts bleiben unberührt – die sollen zu anderen Rechnern passen.",
         "error", host_local=True),
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
    Rule("brix_nested",
         "Verschachtelte Nix-Brix",
         "Erkennt Nix-Brix die in einem anderen Brick stecken – der innere Brick ist für NiCo unsichtbar.",
         "warning"),
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
         "System-State-Version nicht neuer als nixpkgs",
         "Meldet eine system.stateVersion, die neuer ist als das gebaute nixpkgs-Release – "
         "mit einem Release, das es dort noch nicht gibt, kann niemand installiert haben.",
         "warning", flake_only=True),
    Rule("state_version_future",
         "System-State-Version nicht aus der Zukunft",
         "Meldet eine system.stateVersion, die neuer ist als das Release, das dieser Rechner "
         "läuft.",
         "warning", host_local=True),
    Rule("state_version_missing",
         "System-State-Version gesetzt",
         "Meldet eine Config ohne system.stateVersion – NixOS nimmt dann das gerade gebaute "
         "Release, was für jede älter installierte Maschine falsch ist.",
         "warning"),
    Rule("state_version_per_host",
         "System-State-Version pro Host",
         "Meldet eine system.stateVersion in der gemeinsamen Basis-Config einer Flake mit "
         "mehreren Hosts – jede Maschine wurde zu ihrer eigenen Zeit installiert.",
         "warning", flake_only=True),
    Rule("hm_state_version_match",
         "Home-State-Version passt zum HM-Zweig",
         "Meldet eine home.stateVersion, die neuer ist als der verwendete "
         "Home-Manager-Zweig. Mit system.stateVersion wird sie nicht verglichen – "
         "die beiden zählen verschiedene Dinge.",
         "warning"),
    Rule("hm_duplicate_args",
         "Doppelte HM-Argumente",
         "Meldet doppelte Argumente im Funktionskopf von Home-Manager-Dateien (Nix-Syntaxfehler).",
         "error"),
    Rule("snapper_btrfs",
         "Snapper-Mountpoints prüfen",
         "Prüft ob die konfigurierten Snapper-Mountpoints existieren und btrfs sind.",
         "error", host_local=True),
    Rule("btrfs_without_btrfs",
         "Btrfs-Wartung ohne btrfs",
         "Warnt wenn Btrfs-Scrub oder Auto-Balance aktiv sind, auf diesem Rechner aber kein "
         "btrfs eingebunden ist bzw. / kein btrfs ist.",
         "warning", host_local=True),
    Rule("swap_in_snapshot",
         "Swapfile in Snapshot-Subvolume",
         "Warnt wenn ein Btrfs-Swapfile im selben Subvolume liegt wie eine Snapper-Config – Btrfs kann dieses Subvolume dann nicht mehr snapshotten.",
         "warning", host_local=True),
    Rule("host_checks_pending",
         "Ungeprüfte Hosts",
         "Meldet Hosts, deren rechnerabhängige Prüfungen noch nie oder nur mit einem älteren Config-Stand gelaufen sind.",
         "info"),
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
    Rule("flake_untracked_reference",
         "Referenzierte Datei nicht in Git",
         "Warnt wenn eine .nix-Datei eine Datei referenziert, die nicht in Git erfasst ist – der Flake-Build schlägt dann fehl.",
         "warning", flake_only=True),
]

RULE_MAP: dict[str, Rule] = {r.id: r for r in ALL_RULES}


def default_validation_rules() -> dict[str, bool]:
    """Return a dict with all rules enabled (default for new configs)."""
    return {r.id: True for r in ALL_RULES}


def rules_as_dicts() -> list[dict]:
    """Serialise ALL_RULES for the frontend (/api/validate/rules)."""
    return [
        {"id": r.id, "label": r.label, "description": r.description,
         "severity": r.severity, "flake_only": r.flake_only,
         "host_local": r.host_local}
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
         "message_key": f.message_key, "params": [str(p) for p in f.params],
         "detail_key": f.detail_key,
         "detail_params": [str(p) for p in f.detail_params]}
        for f in findings
    ]


# ── Helpers ────────────────────────────────────────────────────────────────────

def _extract_imports(nix_content: str) -> list[str]:
    """Return file paths found inside imports = [...] in nix_content."""
    return [p for p, _ in _extract_imports_with_lines(nix_content)]


_RE_BRICK_MARKER = re.compile(r'^\s*#\s*</?bri[xc]k?:', re.IGNORECASE)


def _brick_bodies(nix_content: str) -> str:
    """All brick bodies of a file joined – the part _extract_imports() strips.

    Marker lines are dropped, because _extract_imports() runs
    strip_brick_blocks() on whatever it is handed and would otherwise throw
    the whole thing away again.
    """
    from .brix import extract_brick_blocks
    try:
        blocks = extract_brick_blocks(nix_content)
    except Exception:
        return ""
    out: list[str] = []
    for block in blocks.values():
        out += [ln for ln in block.get("text", "").splitlines()
                if not _RE_BRICK_MARKER.match(ln)]
    return "\n".join(out)


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
            detail_key="validator.d.user_in_config", detail_params=[current],
        )]
    return []


def _flake_defined_hosts(content: str) -> set[str]:
    """Host names declared under nixosConfigurations in flake.nix.

    Supports both the dotted form (nixosConfigurations.name = …) and the
    attrset form (nixosConfigurations = { name = …; }) that NiCo generates."""
    defined = set(re.findall(r'nixosConfigurations\s*\.\s*"?([\w-]+)"?', content))
    m = re.search(r'nixosConfigurations\s*=\s*{', content)
    if m:
        depth, i = 1, m.end()
        start = i
        while i < len(content) and depth:
            if content[i] == "{":
                depth += 1
            elif content[i] == "}":
                depth -= 1
            i += 1
        block = content[start:i - 1]
        # attribute names at the top level of the block (balanced braces before)
        for am in re.finditer(r'(?m)^\s*"?([A-Za-z0-9_-]+)"?\s*=', block):
            prefix = block[:am.start()]
            if prefix.count("{") == prefix.count("}"):
                defined.add(am.group(1))
    return defined


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

    defined = _flake_defined_hosts(content)
    findings = []
    for host_name in hosts:
        if host_name not in defined:
            findings.append(Finding(
                rule_id="flake_host_exists",
                severity="error",
                message=f'Host "{host_name}" fehlt unter nixosConfigurations in flake.nix.', message_key="validator.f.flake_host_exists", params=[host_name],
                detail="Host ist konfiguriert, aber nicht als Flake-Output deklariert.",
                detail_key="validator.d.flake_host_exists",
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
            detail_key="validator.d.host_orphaned",
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
            detail_key="validator.d.flake_arch_matches",
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

    # A flake host gets the import from the host body in flake.nix, not from
    # its own default.nix – looking only at the file here would report every
    # flake host as broken.
    if is_flake:
        try:
            flake = (Path(nixos_dir) / "flake.nix").read_text(encoding="utf-8")
        except OSError:
            flake = ""
        if "hardware-configuration.nix" in flake:
            return []

    # _extract_imports() strips bricks, so an imports block the user keeps as a
    # brick would look missing. Imported configs carry theirs exactly that way.
    paths = _extract_imports(content) + _extract_imports(_brick_bodies(content))
    if not any("hardware-configuration" in p for p in paths):
        return [Finding(
            rule_id="hardware_imported",
            severity="error",
            message=f"hardware-configuration.nix existiert, ist aber nicht in {co_name} eingebunden.", message_key="validator.f.hardware_imported", params=[co_name],
            detail="In imports = [ ./hardware-configuration.nix ] aufnehmen.",
            detail_key="validator.d.hardware_imported",
        )]
    return []


def _rule_hardware_host_location(nixos_dir: str, config: dict, is_flake: bool,
                                 host: str | None = None) -> list[Finding]:
    """Flake expects the hardware config per host, but it sits in the root.

    Setup copies hardware-configuration.nix next to configuration.nix. Whoever
    switches to flakes afterwards keeps that file, while the generated host
    body imports hosts/<name>/hardware-configuration.nix – a path that does not
    exist. The flake then fails to evaluate, with an error that says nothing
    about the actual cause.
    """
    flake_path = Path(nixos_dir) / "flake.nix"
    if not flake_path.exists():
        return []
    if not (Path(nixos_dir) / "hardware-configuration.nix").exists():
        return []

    try:
        content = flake_path.read_text(encoding="utf-8")
    except OSError:
        return []

    from . import config_manager as _cm
    hosts_dir = (_cm.load_config_settings(nixos_dir).get("hosts_dir") or "hosts").strip() or "hosts"

    raw_hosts = [host] if host else (config.get("flake_hosts") or [])
    hosts = [h.get("name") if isinstance(h, dict) else h for h in raw_hosts]
    hosts = [h for h in hosts if h]

    findings = []
    for host_name in hosts:
        rel = f"{hosts_dir}/{host_name}/hardware-configuration.nix"
        if rel not in content:
            continue                       # this host does not expect one there
        if (Path(nixos_dir) / rel).exists():
            continue
        findings.append(Finding(
            rule_id="hardware_host_location",
            severity="error",
            message=f"flake.nix bindet ./{rel} ein, die Datei liegt aber im Wurzelverzeichnis.",
            message_key="validator.f.hardware_host_location", params=[rel],
            detail="hardware-configuration.nix in das Host-Verzeichnis verschieben.",
            detail_key="validator.d.hardware_host_location",
        ))
    return findings


# NixOS useradd check, see nixos/modules/config/users-groups.nix
_RE_USERNAME = re.compile(r"^[a-z_]([a-z0-9_-]{0,31}|[a-z0-9_-]{0,30}\$)$")


def _rule_user_name_valid(nixos_dir: str, config: dict, is_flake: bool,
                          host: str | None = None) -> list[Finding]:
    """Report user names NixOS will reject.

    A name that fails here stops the rebuild at assertion time, after the
    evaluation, so the message arrives late and reads like a Nix internal.
    """
    names: list[str] = []
    primary = (config.get("username") or "").strip()
    if primary:
        names.append(primary)
    for extra in config.get("extra_users") or []:
        name = (extra.get("username") or "").strip() if isinstance(extra, dict) else ""
        if name:
            names.append(name)

    findings = []
    for name in names:
        if _RE_USERNAME.match(name):
            continue
        findings.append(Finding(
            rule_id="user_name_valid",
            severity="error",
            message=f"Benutzername {name!r} ist für NixOS nicht gültig.",
            message_key="validator.f.user_name_valid", params=[name],
            detail="Erlaubt sind Kleinbuchstaben, Ziffern, - und _, beginnend mit Buchstabe oder _.",
            detail_key="validator.d.user_name_valid",
        ))
    return findings


def _rule_user_extra_conflict(nixos_dir: str, config: dict, is_flake: bool,
                              host: str | None = None) -> list[Finding]:
    """Report free-text user lines that collide with a form field.

    The generator writes those attributes itself, so a hand-typed line of the
    same name ends up in the block twice and Nix aborts the build with
    "attribute … already defined". Imported content never triggers this (the
    importer keeps such fields out); it takes typing into the field by hand.
    """
    from .generator import USER_BLOCK_FIELDS
    from .importer import _split_top_level_stmts, _stmt_attr_name

    entries: list[tuple[str, str]] = []
    primary = (config.get("username") or "").strip()
    if primary:
        entries.append((primary, config.get("user_extra_nix") or ""))
    for extra in config.get("extra_users") or []:
        if isinstance(extra, dict) and (extra.get("username") or "").strip():
            entries.append((extra["username"].strip(), extra.get("extra_nix") or ""))

    findings = []
    for name, extra_nix in entries:
        if not extra_nix.strip():
            continue
        clashes = sorted({
            attr for stmt in _split_top_level_stmts(extra_nix)
            if (attr := _stmt_attr_name(stmt)) in USER_BLOCK_FIELDS
        })
        if not clashes:
            continue
        findings.append(Finding(
            rule_id="user_extra_conflict",
            severity="error",
            message=f"Nutzer {name!r}: {', '.join(clashes)} steht in den weiteren "
                    f"Nix-Zeilen und wird zugleich aus dem Formular geschrieben.",
            message_key="validator.f.user_extra_conflict",
            params=[name, ", ".join(clashes)],
            detail="Nix lehnt ein doppelt definiertes Attribut ab. Zeile aus dem "
                   "Textfeld entfernen und das Formularfeld darüber verwenden.",
            detail_key="validator.d.user_extra_conflict",
        ))
    return findings


# Attributes that give a user a way in. Any single one is enough; none of them
# means NixOS creates the account locked ("!" in /etc/shadow) – no password is
# then accepted, not even an empty one, and the machine needs an installer to
# get back into.
_RE_USER_LOGIN = re.compile(
    r'\b(?:hashedPassword|initialPassword|initialHashedPassword|'
    r'hashedPasswordFile|passwordFile|password)\s*=')
_RE_USER_SSH_KEY = re.compile(r'authorizedKeys\s*\.\s*key(?:s|Files)\s*=\s*\[[^\]]*"')
_RE_USER_UID = re.compile(r'\buid\s*=\s*(\d+)')
_RE_USER_SYSTEM = re.compile(r'\bisSystemUser\s*=\s*true')
_RE_MUTABLE_USERS_OFF = re.compile(r'users\s*\.\s*mutableUsers\s*=\s*false')


def _file_bindings(content: str) -> dict:
    """attrpath → value text for one file, brick bodies included.

    The same two tracks the importer uses – tree-sitter first, regex fallback –
    so both see the same bindings. Counting braces by hand is not an option
    here: a `{` inside a '' … '' string tears the block apart, and user blocks
    are exactly where such strings turn up (ssh keys, shell aliases).

    Brick bodies are read as well. A freshly imported config keeps everything
    NiCo does not understand as a Nix brick, and a user block can sit in there.
    """
    from . import nix_parser as _np
    from .importer import _rx_top_level_bindings
    kv: dict = {}
    try:
        parsed = _np.parse(content)
        if parsed.available:
            kv = dict(_np.make_kv(parsed))
    except Exception:
        kv = {}
    if not kv:
        try:
            kv, _ = _rx_top_level_bindings(content)
        except Exception:
            kv = {}
    bricks = _brick_bodies(content)
    if bricks.strip():
        try:
            brick_kv, _ = _rx_top_level_bindings(bricks)
            for key, value in brick_kv.items():
                kv.setdefault(key, value)
        except Exception:
            pass
    return kv


def _scan_user_text(entry: dict, text: str) -> None:
    """Fold one piece of a user block into the facts collected for that user."""
    if _RE_USER_LOGIN.search(text) or _RE_USER_SSH_KEY.search(text):
        entry["has_login"] = True
    if _RE_USER_SYSTEM.search(text):
        entry["is_system"] = True
    m = _RE_USER_UID.search(text)
    if m and entry["uid"] is None:
        entry["uid"] = int(m.group(1))


def _attr_line(content: str, name: str, kind: str = "users") -> int:
    """Line of the first users.<kind>.<name> mention – for the message only."""
    m = re.search(r'users\s*\.\s*' + kind + r'\s*\.\s*"?' + re.escape(name) + r'"?',
                  content)
    return content[:m.start()].count("\n") + 1 if m else 0


def _hosts_dir_path(nixos_dir: str) -> Path:
    from . import config_manager as _cm
    try:
        name = (_cm.load_config_settings(nixos_dir).get("hosts_dir") or "hosts")
    except Exception:
        name = "hosts"
    return Path(nixos_dir) / (name.strip().strip("/") or "hosts")


def _other_host_dirs(nixos_dir: str, host: str | None,
                     strict: bool = False) -> set:
    """Host directories that do not belong to `host`.

    A user declared for another machine says nothing about this one – reporting
    it would be noise on every multi-host config.

    strict is for the rules that read the running machine (/etc/passwd,
    /etc/group): when the host cannot be identified at all, they must not fall
    back to reading every host directory, because then every user of every
    other machine looks like a user missing here. Nothing attributable means
    nothing read.
    """
    root = _hosts_dir_path(nixos_dir)
    if not root.is_dir():
        return set()
    dirs = {d for d in root.iterdir() if d.is_dir()}
    if host and (root / host) in dirs:
        return dirs - {root / host}
    return dirs if strict else set()


def _scoped_nix_files(nixos_dir: str, host: str | None = None,
                      strict: bool = False):
    """Yield (path, rel, content) for the .nix files that build `host`.

    Other hosts' directories stay out; the root files are always in, that is
    where a flake config keeps what applies to every host.
    """
    base = Path(nixos_dir)
    skip_dirs = _other_host_dirs(nixos_dir, host, strict)
    for nix_file in sorted(base.rglob("*.nix")):
        if ".git" in nix_file.parts:
            continue
        if any(d in nix_file.parents for d in skip_dirs):
            continue
        try:
            content = nix_file.read_text(encoding="utf-8")
        except OSError:
            continue
        try:
            rel = str(nix_file.relative_to(base))
        except ValueError:
            rel = nix_file.name
        yield nix_file, rel, content


def _other_host_finding(rule_id: str, host: str, this_host: str) -> Finding:
    """A host-local rule can only speak for the machine it runs on."""
    return Finding(
        rule_id=rule_id,
        severity="info",
        message=f'Host "{host}" kann hier nicht geprüft werden – '
                f'dieser Rechner ist "{this_host}".',
        message_key="validator.f.host_other", params=[host, this_host],
        detail="Diese Prüfung liest das laufende System. Führe den Validator "
               "auf dem betroffenen Rechner erneut aus.",
        detail_key="validator.d.host_other",
    )


def _declared_users(nixos_dir: str, host: str | None = None,
                    strict: bool = False) -> dict:
    """Every users.users.<name> in the config, with what matters for logging in.

    Both spellings are collected: the attrset block NiCo writes and the dotted
    form (users.users.martin.uid = 1001) a hand-written config may use.

    With a host given, the other hosts' directories stay out: their users
    belong to another machine. The root files are always read – that is where a
    flake config keeps what applies to every host.
    """
    from . import nix_parser as _np
    users: dict = {}
    for _nix_file, rel, content in _scoped_nix_files(nixos_dir, host, strict):
        for key, value_text in _file_bindings(content).items():
            parts = key.split(".")
            if len(parts) < 3 or parts[0] != "users" or parts[1] != "users":
                continue
            name = parts[2].strip('"')
            if not name or name in _EXCL_USERS:
                continue
            entry = users.setdefault(name, {
                "uid": None, "has_login": False, "is_system": False,
                "file": rel, "line": _attr_line(content, name),
            })
            if len(parts) == 3:
                _scan_user_text(entry, _np.extract_inner_block(value_text) or value_text)
            else:
                _scan_user_text(entry, f'{".".join(parts[3:])} = {value_text}')
    return users


def _system_uids() -> dict:
    """name → uid as the running machine has them (/etc/passwd)."""
    try:
        import pwd
        return {p.pw_name: p.pw_uid for p in pwd.getpwall()}
    except Exception:
        return {}


_RE_GROUP_GID = re.compile(r'\bgid\s*=\s*(\d+)')


def _declared_groups(nixos_dir: str, host: str | None = None,
                     strict: bool = False) -> dict:
    """Every users.groups.<name> with a pinned gid, both spellings.

    Groups carry the file permissions, and their gid is the same kind of
    mutable system state as a uid: NixOS hands it out once and writes it to
    /etc/group, outside any generation.
    """
    from . import nix_parser as _np
    groups: dict = {}
    for _nix_file, rel, content in _scoped_nix_files(nixos_dir, host, strict):
        for key, value_text in _file_bindings(content).items():
            parts = key.split(".")
            if len(parts) < 3 or parts[0] != "users" or parts[1] != "groups":
                continue
            name = parts[2].strip('"')
            if not name:
                continue
            entry = groups.setdefault(name, {
                "gid": None, "file": rel,
                "line": _attr_line(content, name, kind="groups"),
            })
            text = ((_np.extract_inner_block(value_text) or value_text)
                    if len(parts) == 3
                    else f'{".".join(parts[3:])} = {value_text}')
            m = _RE_GROUP_GID.search(text)
            if m and entry["gid"] is None:
                entry["gid"] = int(m.group(1))
    return groups


def _system_gids() -> dict:
    """name → gid as the running machine has them (/etc/group)."""
    try:
        import grp
        return {g.gr_name: g.gr_gid for g in grp.getgrall()}
    except Exception:
        return {}


def _rule_group_gid_duplicate(nixos_dir: str, config: dict, is_flake: bool,
                              host: str | None = None) -> list[Finding]:
    """Report the same gid pinned for more than one group."""
    by_gid: dict = {}
    for name, info in _declared_groups(
            nixos_dir, host or running_host(), strict=True).items():
        if info["gid"] is not None:
            by_gid.setdefault(info["gid"], []).append(name)

    findings = []
    for gid, names in sorted(by_gid.items()):
        if len(names) < 2:
            continue
        joined = ", ".join(sorted(names))
        findings.append(Finding(
            rule_id="group_gid_duplicate",
            severity="error",
            message=f"gid {gid} ist für mehrere Gruppen vergeben: {joined}.",
            message_key="validator.f.group_gid_duplicate", params=[gid, joined],
            detail="Zwei Gruppen mit derselben gid teilen sich alle "
                   "Dateirechte: wer in der einen ist, kommt an die Dateien der "
                   "anderen. Eine der gid-Angaben ändern oder weglassen.",
            detail_key="validator.d.group_gid_duplicate",
        ))
    return findings


def _rule_group_gid_system(nixos_dir: str, config: dict, is_flake: bool,
                           host: str | None = None) -> list[Finding]:
    """Compare pinned gids with the groups of the running machine.

    Same two accidents as with uids, only one floor down: the gid of an
    existing group changes and leaves its files behind without a group that
    reaches them, or the gid already belongs to another group here – which
    silently widens who may read what.
    """
    this_host = running_host()
    if host and this_host and host != this_host:
        return [_other_host_finding("group_gid_system", host, this_host)]

    existing = _system_gids()
    if not existing:
        return []
    owner_of = {gid: name for name, gid in existing.items()}

    findings = []
    for name, info in sorted(
            _declared_groups(nixos_dir, host or this_host, strict=True).items()):
        gid = info["gid"]
        if gid is None:
            continue
        here = existing.get(name)
        if here is not None and here != gid:
            findings.append(Finding(
                rule_id="group_gid_system",
                severity="error",
                message=f'Gruppe "{name}" hat auf diesem Rechner gid {here}, '
                        f"die Config verlangt {gid}.",
                message_key="validator.f.group_gid_changed",
                params=[name, here, gid],
                detail=f"Der Rebuild ändert die gid von {here} auf {gid}. "
                       f'Dateien, die der Gruppe "{name}" gehören, tragen '
                       f"weiter {here} und werden danach von keiner Gruppe "
                       f"mehr erreicht.",
                detail_key="validator.d.group_gid_changed",
                detail_params=[name, here, gid],
            ))
        owner = owner_of.get(gid)
        if owner and owner != name:
            findings.append(Finding(
                rule_id="group_gid_system",
                severity="error",
                message=f'gid {gid} für "{name}" gehört auf diesem Rechner '
                        f'schon der Gruppe "{owner}".',
                message_key="validator.f.group_gid_taken",
                params=[gid, name, owner],
                detail="Zwei Gruppen mit derselben gid teilen sich alle "
                       "Dateirechte – eine stille Rechteausweitung, die nirgends "
                       "auffällt. Eine freie gid wählen oder die Angabe "
                       "weglassen, dann vergibt NixOS selbst eine.",
                detail_key="validator.d.group_gid_taken",
            ))
    return findings


def _mutable_users_off(nixos_dir: str) -> bool:
    """True if any config file sets users.mutableUsers = false."""
    for nix_file in sorted(Path(nixos_dir).rglob("*.nix")):
        if ".git" in nix_file.parts:
            continue
        try:
            content = nix_file.read_text(encoding="utf-8")
        except OSError:
            continue
        if _RE_MUTABLE_USERS_OFF.search(content):
            return True
    return False


def _rule_user_no_password(nixos_dir: str, config: dict, is_flake: bool,
                           host: str | None = None) -> list[Finding]:
    """Report a user the rebuild would create without any way to log in.

    Silent for users that already exist on this machine: with mutableUsers at
    its default their password lives in /etc/shadow, outside the config, and
    nothing is lost. Unless mutableUsers = false makes the config the only
    source – then an existing password is dropped too.
    """
    # /etc/passwd is the running machine's. Judging another host by it would
    # report every user that machine simply does not have.
    this_host = running_host()
    if host and this_host and host != this_host:
        return [_other_host_finding("user_no_password", host, this_host)]

    users = _declared_users(nixos_dir, host or this_host, strict=True)
    if not users:
        return []

    immutable = _mutable_users_off(nixos_dir)
    existing = _system_uids()

    findings = []
    for name, info in sorted(users.items()):
        if info["is_system"] or info["has_login"]:
            continue
        if immutable:
            detail_key = "validator.d.user_no_password_immutable"
        elif name not in existing:
            detail_key = "validator.d.user_no_password"
        else:
            continue
        findings.append(Finding(
            rule_id="user_no_password",
            severity="error",
            message=f'Benutzer "{name}" ({info["file"]} Zeile {info["line"]}) '
                    f"hat keinen Weg sich anzumelden.",
            message_key="validator.f.user_no_password",
            params=[name, info["file"], info["line"]],
            detail=f'Nach dem Rebuild ist das Konto "{name}" gesperrt. '
                   "Passwort über das Formular setzen (initialPassword) oder "
                   "hashedPassword in den weiteren Nix-Zeilen eintragen.",
            detail_key=detail_key, detail_params=[name],
        ))
    return findings


def _rule_user_uid_duplicate(nixos_dir: str, config: dict, is_flake: bool,
                             host: str | None = None) -> list[Finding]:
    """Report the same uid handed to more than one user."""
    # A uid collision only means something inside one machine; two hosts may
    # hand the same uid to different people. Without a host given, that is
    # this machine.
    by_uid: dict = {}
    for name, info in _declared_users(
            nixos_dir, host or running_host(), strict=True).items():
        if info["uid"] is not None:
            by_uid.setdefault(info["uid"], []).append(name)

    findings = []
    for uid, names in sorted(by_uid.items()):
        if len(names) < 2:
            continue
        joined = ", ".join(sorted(names))
        findings.append(Finding(
            rule_id="user_uid_duplicate",
            severity="error",
            message=f"uid {uid} ist für mehrere Benutzer vergeben: {joined}.",
            message_key="validator.f.user_uid_duplicate", params=[uid, joined],
            detail="Zwei Benutzer mit derselben uid teilen sich alle "
                   "Dateirechte. Eine der uid-Angaben ändern oder weglassen.",
            detail_key="validator.d.user_uid_duplicate",
        ))
    return findings


def _rule_user_uid_system(nixos_dir: str, config: dict, is_flake: bool,
                          host: str | None = None) -> list[Finding]:
    """Compare the uid in the config with the users of the running machine.

    Two different accidents, both silent until the next login: the uid of an
    existing user changes (the home directory keeps the old owner), or the uid
    already belongs to somebody else here.
    """
    this_host = running_host()
    if host and this_host and host != this_host:
        return [_other_host_finding("user_uid_system", host, this_host)]

    existing = _system_uids()
    if not existing:
        return []
    owner_of = {uid: name for name, uid in existing.items()}

    findings = []
    for name, info in sorted(
            _declared_users(nixos_dir, host or this_host, strict=True).items()):
        uid = info["uid"]
        if uid is None:
            continue
        here = existing.get(name)
        if here is not None and here != uid:
            findings.append(Finding(
                rule_id="user_uid_system",
                severity="error",
                message=f'Benutzer "{name}" hat auf diesem Rechner uid {here}, '
                        f"die Config verlangt {uid}.",
                message_key="validator.f.user_uid_changed",
                params=[name, here, uid],
                detail="Der Rebuild ändert die uid, das Heimatverzeichnis "
                       "behält den alten Eigentümer – die Sitzung bricht dann "
                       "direkt nach der Anmeldung ab.",
                detail_key="validator.d.user_uid_changed",
                detail_params=[name, here, uid],
            ))
        owner = owner_of.get(uid)
        if owner and owner != name:
            findings.append(Finding(
                rule_id="user_uid_system",
                severity="error",
                message=f'uid {uid} für "{name}" gehört auf diesem Rechner '
                        f'schon "{owner}".',
                message_key="validator.f.user_uid_taken",
                params=[uid, name, owner],
                detail="Zwei Konten mit derselben uid teilen sich alle "
                       "Dateirechte. Eine freie uid wählen oder die Angabe "
                       "weglassen – dann vergibt NixOS selbst eine.",
                detail_key="validator.d.user_uid_taken",
            ))
    return findings


def _rule_hardware_matches(nixos_dir: str, config: dict, is_flake: bool,
                           host: str | None = None) -> list[Finding]:
    """
    Does the hardware config of *this* machine's host describe this machine?

    The question is answered in nico/hardware.py, because the import and the
    restore paths have to ask exactly the same one and must not drift apart.

    Only our own host is judged.  hosts/nix-laptop/hardware-configuration.nix is
    supposed to not match this machine, and the old version of this rule reported
    it as broken whenever the laptop host was validated from the desktop.
    """
    from . import hardware as _hw

    decision = _hw.inspect(nixos_dir)

    if decision.action == _hw.OK:
        return []

    if decision.action == _hw.ADD:
        # No hardware config anywhere – hardware_imported already covers that,
        # and there is nothing machine-specific to add here.
        return []

    if decision.action == _hw.PICK_HOST:
        names = ", ".join(decision.hosts) or "?"
        return [Finding(
            rule_id="hardware_matches",
            severity="error",
            message=f'Zu welchem Host "{_hw.running_host()}" gehört, ist nicht '
                    f"erkennbar – keine hardware-configuration.nix passt eindeutig "
                    f"zu diesem Rechner.",
            message_key="validator.f.hardware_ambiguous",
            params=[_hw.running_host()],
            detail=(
                f"Hosts in der Config: {names}. Vor dem Bauen muss festgelegt "
                "werden, welcher Host dieser Rechner ist – gebaut wird sonst "
                "mit den Datenträgern einer anderen Maschine."
            ),
            detail_key="validator.d.hardware_ambiguous",
            detail_params=[names],
        )]

    target = decision.target
    if target is None:
        return []
    # Validating a different host says nothing about this machine's disks.
    if host and target.host and host != target.host:
        return []

    where = str(target.path.relative_to(Path(nixos_dir)))

    if target.verdict == _hw.MISMATCH:
        sample = target.missing[:3]
        extra = len(target.missing) - len(sample)
        shown = ", ".join(sample) + (f" (+{extra})" if extra > 0 else "")
        return [Finding(
            rule_id="hardware_matches",
            severity="error",
            message=f"{where} nennt Datenträger, die es auf diesem Rechner nicht gibt.",
            message_key="validator.f.hardware_matches",
            params=[where],
            detail=(
                f"Nicht vorhanden: {shown}. Die Datei beschreibt eine andere "
                "Maschine – gebaut wird eine Config, die hier keine Festplatte "
                "findet. Die Datei dieses Rechners eintragen, bevor gebaut wird."
            ),
            detail_key="validator.d.hardware_matches",
            detail_params=[shown],
        )]

    # UNKNOWN – nothing checkable, or a reference we cannot resolve.
    unres = ", ".join(target.unresolved[:3]) or "–"
    return [Finding(
        rule_id="hardware_matches",
        severity="error",
        message=f"{where} lässt sich nicht gegen diesen Rechner prüfen.",
        message_key="validator.f.hardware_unknown",
        params=[where],
        detail=(
            f"Nicht prüfbare Angaben: {unres}. Ob die Datei zu diesem Rechner "
            "gehört, ist damit offen – und eine falsche Datei kostet das ganze "
            "System. Einmal bestätigen oder die Datei dieses Rechners eintragen."
        ),
        detail_key="validator.d.hardware_unknown",
        detail_params=[unres],
    )]


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
    "boot_loader": "BOOT", "secure_boot": "BOOT", "lanzaboote": "BOOT",
    "kernel": "BOOT", "boot_efi_can_touch": "BOOT", "boot_config_limit": "BOOT",
    # System
    "hostname": "SYST", "state_version": "SYST", "allowUnfree": "SYST",
    "nix_args": "SYST",
    # Lokalisierung
    "timezone": "I18N", "locale": "I18N",
    "keyboard_layout": "I18N", "keyboard_variant": "I18N",
    "keyboard_console": "I18N", "extra_locale": "I18N",
    # Netzwerk
    "networkmanager": "NETW", "ssh": "NETW",
    "firewall_disable": "NETW", "firewall_tcp_enable": "NETW",
    "firewall_tcp_ports": "NETW", "firewall_udp_enable": "NETW",
    "firewall_udp_ports": "NETW",
    # Services
    "printing": "SERV", "avahi": "SERV", "bluetooth": "SERV",
    "blueman": "SERV", "libinput": "SERV", "fprintd": "SERV",
    "pcscd": "SERV", "sunshine": "SERV",
    # Desktop
    "desktop_environment": "DESK", "autologin_user": "DESK",
    # Audio
    "pipewire": "AUDI", "pipewire_32bit": "AUDI",
    # Benutzer
    "username": "USER", "user_description": "USER",
    "user_initial_password": "USER", "user_uid": "USER",
    "user_groups": "USER", "user_groups_extra": "USER",
    "user_shell": "USER", "user_extra_nix": "USER",
    # Programme
    "packages": "PROG", "steam": "PROG",
    # Schriftarten
    "fonts": "FONT", "fonts_extra": "FONT",
    # Nix & System
    "flakes": "NIXS", "nix_optimize_store": "NIXS",
    "nix_gc": "NIXS", "nix_gc_frequency": "NIXS",
    "nix_gc_age": "NIXS",
    # Hardware
    "opengl": "HDWR", "cpu_microcode": "HDWR",
    "enable_all_firmware": "HDWR", "hardware_config": "HDWR",
    "zram_swap": "HDWR",
    # Virtualisierung
    "docker": "VIRT", "docker_rootless": "VIRT",
    "podman": "VIRT", "podman_docker_compat": "VIRT",
    "virtualbox_host": "VIRT", "libvirtd": "VIRT",
    "virt_manager": "VIRT",
    # Dateisystem & Backup
    "smartd": "FSYS",
    "tmp_use_tmpfs": "FSYS",
    "tmp_tmpfs_size": "FSYS",
    "tmp_clean_on_boot": "FSYS",
    "btrfs_scrub": "FSYS", "snapper_enable": "FSYS",
    "snapper_configs": "FSYS",
    "btrfs_balance": "FSYS",
    "btrfs_balance_frequency": "FSYS",
    "btrfs_balance_dusage": "FSYS",
    "btrfs_balance_musage": "FSYS",
    # Home Manager
    "home_manager": "HOME",
}


def _section_hint(recognized: dict) -> str:
    """
    Section codes of the recognised fields, comma separated.

    Codes, not names: the frontend replaces each one with the section name in
    the user's language before showing the finding.
    """
    sections = sorted({_FIELD_SECTION[k] for k in recognized if k in _FIELD_SECTION})
    if not sections:
        return "NiCo-Panel"
    return ", ".join(sections)


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
                detail_key="validator.d.flake_hm_branch.nobranch",
                detail_params=[np_loc, hm_loc, nixpkgs_version, expected_ref],
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
                detail_key="validator.d.flake_hm_branch.wrongbranch",
                detail_params=[np_loc, hm_loc, expected_ref],
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
                detail_key="validator.d.flake_hm_branch.unstable",
                detail_params=[nixpkgs_line, nixpkgs_url, hm_line, hm_url],
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
                detail_key="validator.d.flake_hm_branch.lock_hm",
                detail_params=[lock_np_ref or "<unbekannt>", lock_hm_ref, expected_lock_hm_ref],
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
                detail_key="validator.d.flake_hm_branch.lock_stale_hm",
                detail_params=[hm_line, hm_url, lock_hm_ref, expected_flake_hm_ref],
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
                detail_key="validator.d.flake_hm_branch.lock_stale_np",
                detail_params=[nixpkgs_line, nixpkgs_url, lock_np_ref, flake_np_ref],
            ))

    return findings


# ── stateVersion ──────────────────────────────────────────────────────────────
#
# system.stateVersion names the release a machine was *installed* with and is
# never raised afterwards: it keeps the defaults of stateful options at the
# values the data on disk was written for. home.stateVersion is Home Manager's
# own marker for the same idea, bound to a home directory instead of a machine.
# The two count different things and are equal only by coincidence, so none of
# the rules below compares them with each other.

# The priority wrappers matter here: a bare definition in the base config and
# one in a host file are two definitions of the same option, and Nix refuses
# that. With lib.mkDefault on the base, the host's value wins instead – that is
# the difference between a config that builds and one that stops at evaluation.
_PRIO = r'(?:(?:lib\s*\.\s*)?(mkDefault|mkForce|mkOverride\s*\d+)\s+)?'
_RE_SYS_STATE_VERSION = re.compile(
    r'system\s*\.\s*stateVersion\s*=\s*' + _PRIO + r'"([^"]+)"')
_RE_HM_STATE_VERSION = re.compile(
    r'home\s*\.\s*stateVersion\s*=\s*' + _PRIO + r'"([^"]+)"')


def _release_tuple(version: str) -> "tuple | None":
    """'25.11' → (25, 11). None for anything else (unstable, master, empty)."""
    m = re.fullmatch(r'(\d+)\.(\d+)', (version or "").strip())
    return (int(m.group(1)), int(m.group(2))) if m else None


def _running_release() -> str:
    """The release this machine runs, as 'YY.MM' – empty if unreadable."""
    text = ""
    try:
        text = Path("/run/current-system/nixos-version").read_text(encoding="utf-8")
    except OSError:
        try:
            osr = Path("/etc/os-release").read_text(encoding="utf-8")
            m = re.search(r'^VERSION_ID="?([^"\n]+)', osr, re.M)
            text = m.group(1) if m else ""
        except OSError:
            return ""
    m = re.match(r'(\d+\.\d+)', text.strip())
    return m.group(1) if m else ""


def _hm_dir_path(nixos_dir: str) -> Path:
    from . import config_manager as _cm
    try:
        name = (_cm.load_config_settings(nixos_dir).get("hm_dir") or "home")
    except Exception:
        name = "home"
    return Path(nixos_dir) / name


def _state_version_sites(nixos_dir: str, host: str | None,
                         home_manager: bool = False) -> list:
    """(value, rel, line, priority) for every stateVersion of that kind in scope.

    priority is '' for a plain definition, otherwise the wrapper that was used
    ('mkDefault', 'mkForce', …).
    """
    hm_dir = _hm_dir_path(nixos_dir)
    pattern = _RE_HM_STATE_VERSION if home_manager else _RE_SYS_STATE_VERSION
    sites = []
    for nix_file, rel, content in _scoped_nix_files(nixos_dir, host):
        in_hm = hm_dir.exists() and nix_file.is_relative_to(hm_dir)
        if in_hm != home_manager:
            continue
        for i, line in enumerate(content.split("\n"), 1):
            m = pattern.search(line)
            if m:
                prio = (m.group(1) or "").split()[0] if m.group(1) else ""
                sites.append((m.group(2), rel, i, prio))
    return sites


# Markers that are created when a machine is first set up and never again:
# the first activation, the first boot. NixOS records its installation date
# nowhere, so this is the only evidence there is.
_INSTALL_MARKERS = ("/var/lib/nixos", "/etc/machine-id",
                    "/etc/ssh/ssh_host_ed25519_key", "/etc/ssh/ssh_host_rsa_key")


def _birth_date(path: str):
    """The file's birth date, or None when the filesystem does not keep one."""
    try:
        out = subprocess.run(["stat", "-c", "%w", path], capture_output=True,
                             text=True, timeout=3).stdout.strip()
    except Exception:
        return None
    m = re.match(r'(\d{4})-(\d{2})-(\d{2})', out)
    if not m:
        return None            # "-" on filesystems without birth time
    import datetime
    try:
        return datetime.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


def _release_for_date(day) -> str:
    """The NixOS release current at that date, '' when too close to call.

    Releases land at the end of May and the end of November, but the exact day
    moves by a week or two. Within three weeks of a turn, a date cannot decide
    between two releases – and a stateVersion that is one release off is the
    very damage these rules exist for, so nothing is claimed there.
    """
    import datetime
    turns = []
    for year in (day.year - 1, day.year, day.year + 1):
        turns.append((datetime.date(year, 5, 31), f"{year % 100:02d}.05"))
        turns.append((datetime.date(year, 11, 30), f"{year % 100:02d}.11"))
    turns.sort()
    if any(abs((day - when).days) <= 21 for when, _ in turns):
        return ""
    current = [name for when, name in turns if when <= day]
    return current[-1] if current else ""


def _install_release() -> tuple:
    """(release, date) derived from this machine's install markers.

    Returns ('', None) unless several markers agree and the date is far enough
    from a release turn to be unambiguous.
    """
    dates = [d for d in (_birth_date(p) for p in _INSTALL_MARKERS) if d]
    if len(dates) < 2:
        return "", None
    day = min(dates)
    # Markers from different days mean something was reinstalled or rebuilt in
    # place; then this is not an installation date.
    if (max(dates) - day).days > 1:
        return "", None
    release = _release_for_date(day)
    return (release, day) if release else ("", None)


def _rule_state_version_missing(nixos_dir: str, config: dict, is_flake: bool,
                                host: str | None = None) -> list[Finding]:
    """Report a config without any system.stateVersion.

    Without it NixOS falls back to the release currently being built – right
    only for a machine installed with exactly that release, and silently wrong
    for every older one.
    """
    if _state_version_sites(nixos_dir, host):
        return []
    # Nothing written yet is the setup case, not a finding.
    if not any(True for _ in _scoped_nix_files(nixos_dir, host)):
        return []

    # Where to put it has to agree with state_version_per_host: with host
    # directories in play, the base config is the wrong place, and naming it
    # here would send the user back and forth between two rules.
    hosts_root = _hosts_dir_path(nixos_dir)
    if hosts_root.is_dir() and any(d.is_dir() for d in hosts_root.iterdir()):
        where = f"{hosts_root.name}/{host or '<name>'}/default.nix"
    else:
        where = "configuration.nix"
    # The derived value is only for the machine NiCo runs on – and only when
    # the markers agree and the date is not near a release turn. Unsure means
    # nothing is named: a wrong number with an explanation gets typed in.
    this_host = running_host()
    release, day = ("", None)
    if not host or not this_host or host == this_host:
        release, day = _install_release()

    if release:
        return [Finding(
            rule_id="state_version_missing",
            severity="warning",
            message="system.stateVersion ist nicht gesetzt.",
            message_key="validator.f.state_version_missing",
            detail="Ohne den Wert nimmt NixOS das Release, das gerade gebaut "
                   f"wird. Einzutragen ist das Release, mit dem diese Maschine "
                   f"installiert wurde ({where}). Dieser Rechner wurde am "
                   f"{day.isoformat()} eingerichtet – damals war \"{release}\" "
                   "das aktuelle Release.",
            detail_key="validator.d.state_version_missing_hint",
            detail_params=[where, release, day.isoformat()],
        )]

    return [Finding(
        rule_id="state_version_missing",
        severity="warning",
        message="system.stateVersion ist nicht gesetzt.",
        message_key="validator.f.state_version_missing",
        detail="Ohne den Wert nimmt NixOS das Release, das gerade gebaut wird. "
               f"Einzutragen ist das Release, mit dem diese Maschine "
               f"installiert wurde ({where}).",
        detail_key="validator.d.state_version_missing", detail_params=[where],
    )]


def _rule_state_version_future(nixos_dir: str, config: dict, is_flake: bool,
                               host: str | None = None) -> list[Finding]:
    """Report a stateVersion newer than the release this machine runs.

    Nobody can have installed with a release that did not exist yet, so such a
    value is always wrong – the case a fresh VM lands in when it imports a
    config from a machine that is further along.
    """
    this_host = running_host()
    if host and this_host and host != this_host:
        return [_other_host_finding("state_version_future", host, this_host)]

    running = _running_release()
    running_t = _release_tuple(running)
    if not running_t:
        return []

    findings = []
    for value, rel, line, _prio in _state_version_sites(nixos_dir, host):
        value_t = _release_tuple(value)
        if value_t and value_t > running_t:
            findings.append(Finding(
                rule_id="state_version_future",
                severity="warning",
                message=f'{rel} Zeile {line}: system.stateVersion "{value}" ist '
                        f"neuer als das laufende Release {running}.",
                message_key="validator.f.state_version_future",
                params=[rel, line, value, running],
                detail="stateVersion ist das Release, mit dem die Maschine "
                       "installiert wurde. Ein neuerer Wert kann nicht "
                       "stimmen – er lässt NixOS neue Vorgaben auf alten "
                       "Zustand anwenden.",
                detail_key="validator.d.state_version_future",
                detail_params=[value, running],
            ))
    return findings


def _rule_state_version_per_host(nixos_dir: str, config: dict, is_flake: bool,
                                 host: str | None = None) -> list[Finding]:
    """Report one shared stateVersion standing in for several machines.

    Each host was installed at its own time, so the value belongs in the host
    file. In the common base config it claims the same installation date for
    every machine the flake builds.
    """
    flake_path = Path(nixos_dir) / "flake.nix"
    try:
        content = flake_path.read_text(encoding="utf-8")
    except OSError:
        return []
    hosts = _flake_defined_hosts(content)
    if len(hosts) < 2:
        return []

    hosts_root = _hosts_dir_path(nixos_dir)
    hosts_dir = hosts_root.name
    # All host directories, not just the selected one: which hosts carry their
    # own value decides whether the shared one is a defect at all.
    base_sites: list = []
    own: dict = {}
    for value, rel, line, prio in _state_version_sites(nixos_dir, None):
        parts = rel.replace("\\", "/").split("/")
        if len(parts) >= 2 and parts[0] == hosts_dir:
            own.setdefault(parts[1], (value, rel, line, prio))
        else:
            base_sites.append((value, rel, line, prio))

    if not base_sites:
        return []

    findings = []
    value, rel, line, prio = base_sites[0]

    # Two definitions of one option: Nix aborts the evaluation unless the base
    # one steps back with lib.mkDefault. This is the "checked against each
    # other" part – a host value does not simply win.
    if not prio:
        for host_name, (h_value, h_rel, h_line, h_prio) in sorted(own.items()):
            if h_prio == "mkForce":
                continue
            findings.append(Finding(
                rule_id="state_version_per_host",
                severity="error",
                message=f"system.stateVersion ist zweimal definiert: {rel} Zeile "
                        f"{line} und {h_rel} Zeile {h_line}.",
                message_key="validator.f.state_version_conflict",
                params=[rel, line, h_rel, h_line],
                detail="Nix lehnt zwei Definitionen derselben Option ab und "
                       f"bricht die Auswertung ab. Entweder den Wert in {rel} "
                       f"Zeile {line} entfernen – dann gilt für jeden Host der "
                       "eigene – oder dort lib.mkDefault davorschreiben, damit "
                       "der Host-Wert gewinnt.",
                detail_key="validator.d.state_version_conflict",
                detail_params=[rel, line],
            ))

    # Only the hosts without their own value inherit the shared one. With a
    # value of its own, a host is already correct and must not be named.
    inheriting = sorted(h for h in hosts if h not in own)
    if len(inheriting) >= 2:
        names = ", ".join(inheriting)
        findings.append(Finding(
            rule_id="state_version_per_host",
            severity="warning",
            message=f'{rel} Zeile {line}: system.stateVersion "{value}" gilt so '
                    f"für {len(inheriting)} Hosts.",
            message_key="validator.f.state_version_per_host",
            params=[rel, line, value, len(inheriting)],
            detail=f"Betroffen: {names}. Jede Maschine wurde zu ihrer eigenen "
                   f"Zeit installiert, der Wert gehört darum in "
                   f"{hosts_dir}/<name>/default.nix.",
            detail_key="validator.d.state_version_per_host",
            detail_params=[names, hosts_dir],
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
    nixpkgs_t = _release_tuple(nixpkgs_version or "")
    if not nixpkgs_t:
        return []

    # Only "newer than" is a defect. A stateVersion *older* than nixpkgs is the
    # normal state of every machine that has seen an upgrade, and demanding
    # equality is what dragged this config's value up release by release.
    findings = []
    for state_ver, rel, i, _prio in _state_version_sites(nixos_dir, host):
        state_t = _release_tuple(state_ver)
        if not state_t or state_t <= nixpkgs_t:
            continue
        findings.append(Finding(
            rule_id="state_version_match",
            severity="warning",
            message=f'{rel} Zeile {i}: system.stateVersion "{state_ver}" ist neuer '
                    f"als nixpkgs {nixpkgs_version}.",
            message_key="validator.f.state_version_match",
            params=[rel, i, state_ver, nixpkgs_version],
            detail=(
                f"flake.nix Zeile {nixpkgs_line}: nixpkgs → {nixpkgs_url}\n"
                f'{rel} Zeile {i}: system.stateVersion = "{state_ver}"\n'
                "Mit einem Release installiert, das es im gebauten nixpkgs noch "
                "nicht gibt – das kann nicht stimmen."
            ),
            detail_key="validator.d.state_version_match",
            detail_params=[nixpkgs_line, nixpkgs_url, rel, i, state_ver],
        ))

    return findings


def _rule_hm_state_version_match(nixos_dir: str, config: dict, is_flake: bool,
                                  host: str | None = None) -> list[Finding]:
    """Report a home.stateVersion newer than the Home Manager branch in use.

    Deliberately *not* compared against system.stateVersion any more: that
    value marks when the machine was installed, this one when the user's Home
    Manager state began. Whoever starts Home Manager years after the install
    has two different and both correct values.
    """
    flake_path = Path(nixos_dir) / "flake.nix"
    try:
        content = flake_path.read_text(encoding="utf-8")
    except OSError:
        return []

    hm_url, hm_line = _extract_flake_input_url(content, 'home-manager')
    if not hm_url:
        return []
    hm_ref = _parse_github_ref(hm_url) or ""
    m = re.match(r'release-(\d+\.\d+)', hm_ref)
    branch_t = _release_tuple(m.group(1)) if m else None
    if not branch_t:
        return []   # master/unstable: no release to compare against

    findings = []
    for hm_ver, hm_rel, line, _prio in _state_version_sites(nixos_dir, host,
                                                            home_manager=True):
        hm_t = _release_tuple(hm_ver)
        if not hm_t or hm_t <= branch_t:
            continue
        findings.append(Finding(
            rule_id="hm_state_version_match",
            severity="warning",
            message=f'{hm_rel} Zeile {line}: home.stateVersion "{hm_ver}" ist neuer '
                    f"als der Home-Manager-Zweig {hm_ref}.",
            message_key="validator.f.hm_state_version_match",
            params=[hm_rel, line, hm_ver, hm_ref],
            detail=(
                f"flake.nix Zeile {hm_line}: home-manager → {hm_url}\n"
                f'{hm_rel} Zeile {line}: home.stateVersion = "{hm_ver}"\n'
                "Der Wert ist das Release, mit dem der Home-Manager-Zustand "
                "dieses Nutzers angefangen hat; neuer als der verwendete Zweig "
                "kann er nicht sein."
            ),
            detail_key="validator.d.hm_state_version_match",
            detail_params=[hm_line, hm_url, hm_rel, line, hm_ver],
        ))

    return findings


def _rule_hm_duplicate_args(nixos_dir: str, config: dict, is_flake: bool,
                            host: str | None = None) -> list[Finding]:
    from . import config_manager as _cm
    from .hm_generator import _hm_arg_base
    cfg_settings = _cm.load_config_settings(nixos_dir)
    hm_dir_name = cfg_settings.get("hm_dir") or "home"
    base = Path(nixos_dir)
    hm_dir = base / hm_dir_name
    if not hm_dir.exists():
        return []

    findings: list[Finding] = []
    for nix_file in sorted(hm_dir.rglob("*.nix")):
        if ".git" in nix_file.parts:
            continue
        try:
            content = nix_file.read_text(encoding="utf-8")
        except OSError:
            continue
        m = re.search(r'^\s*\{([^}]*)\}\s*:', content, re.MULTILINE)
        if not m:
            continue
        try:
            rel = str(nix_file.relative_to(base))
        except ValueError:
            rel = nix_file.name

        seen: set[str] = set()
        dups: list[str] = []
        for arg in m.group(1).split(','):
            name = _hm_arg_base(arg)
            if not name:
                continue
            if name in seen and name not in dups:
                dups.append(name)
            seen.add(name)

        if dups:
            findings.append(Finding(
                rule_id="hm_duplicate_args",
                severity="error",
                message=f"{rel}: Argument(e) {', '.join(dups)} mehrfach im Funktionskopf.", message_key="validator.f.hm_duplicate_args", params=[rel, ", ".join(dups)],
                detail=(
                    f"Funktionskopf: {m.group(0).strip()}\n"
                    "Nix bricht mit 'duplicate formal function argument' ab.\n"
                    "Doppelte Einträge im HM-Panel unter 'Argumente' entfernen."
                ),
                detail_key="validator.d.hm_duplicate_args",
                detail_params=[m.group(0).strip()],
            ))
    return findings


def _rule_brix_nested(nixos_dir: str, config: dict, is_flake: bool,
                      host: str | None = None) -> list[Finding]:
    """Nested bricks anywhere in the config, not just configuration.nix –
    the case this rule exists for sat in home/<user>.nix."""
    from .brix import find_nested_bricks

    base = Path(nixos_dir)
    findings: list[Finding] = []
    for nix_file in sorted(base.rglob("*.nix")):
        if ".git" in nix_file.parts:
            continue
        try:
            content = nix_file.read_text(encoding="utf-8")
        except OSError:
            continue
        nested = find_nested_bricks(content)
        if not nested:
            continue
        try:
            rel = str(nix_file.relative_to(base))
        except ValueError:
            rel = nix_file.name
        for f in nested:
            findings.append(Finding(
                rule_id="brix_nested",
                severity="warning",
                message=(f'{rel}: Brick "{f["outer"]}" enthält einen zweiten Brick '
                         f'"{f["inner"]}" (Zeile {f["line"]}) – verschachtelte '
                         "Nix-Brix werden nicht unterstützt."),
                message_key="validator.f.brix_nested",
                params=[rel, f["outer"], f["inner"], f["line"]],
                detail=(
                    "NiCo verwaltet nur den äußeren Brick. Der innere taucht in der "
                    "Brick-Liste nicht auf und wird mitgelöscht, wenn der äußere "
                    "gelöscht oder verschoben wird.\n"
                    "Nix selbst stört das nicht – die Marker sind Kommentare.\n"
                    "Beheben: im Raw-Editor entweder die beiden äußeren oder die "
                    "beiden inneren Markerzeilen entfernen, nicht beide Paare."
                ),
                detail_key="validator.d.brix_nested",
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
                    detail_key="validator.d.brix_redundant", detail_params=[hint],
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
        detail_key="validator.d.hm_user_defined", detail_params=[hm_dir_name],
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
        detail_key="validator.d.hm_orphan_root",
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
        detail_key="validator.d.hm_allowunfree", detail_params=[hm_hint],
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
                detail_key="validator.d.snapper_btrfs.missing",
            ))
            continue
        if not os.path.ismount(mount):
            findings.append(Finding(
                rule_id="snapper_btrfs",
                severity="error",
                message=f'{target}: Mountpoint "{mount}" ist nicht gemountet.', message_key="validator.f.snapper_btrfs.not_mounted", params=[target, mount],
                detail="Snapper kann nur auf gemountete Dateisysteme angewendet werden.",
                detail_key="validator.d.snapper_btrfs.unmounted",
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
                detail_key="validator.d.snapper_btrfs.notbtrfs",
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
        detail_key="validator.d.snapper_in_host",
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
            detail_key="validator.d.git_missing_gitignore.none",
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
            detail_key="validator.d.git_missing_gitignore.some",
            detail_params=[", ".join(missing)],
        )]
    return []


def _rule_git_large_log(nixos_dir: str, config: dict, is_flake: bool,
                        host: str | None = None) -> list[Finding]:
    # Only the old location inside the config directory is checked. Since
    # 2026-09-12 NiCo writes the log to the cache dir and caps it, so anything
    # found here is a leftover that git can still pick up.
    from . import config_manager as _cm
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
        detail=(f"Pfad: {log_path}\nDie Datei enthält nur Rebuild-Ausgabe und kann bedenkenlos "
                f"gelöscht werden. NiCo schreibt das Log inzwischen nach "
                f"{_cm.REBUILD_LOG_FILE} – diese Datei hier stammt aus einer "
                f"früheren Version."),
        detail_key="validator.d.git_large_log",
        detail_params=[str(log_path), str(_cm.REBUILD_LOG_FILE)],
    )]


def _rule_git_foreign_files(nixos_dir: str, config: dict, is_flake: bool,
                            host: str | None = None) -> list[Finding]:
    from . import git_manager as _gm
    if not _gm.is_git_repo(nixos_dir):
        return []
    tracked = _gm.list_tracked_files(nixos_dir)
    # Files the user chose to keep tracked (foreign-file cleanup dialog)
    try:
        from . import config_manager as _cm
        keep = set(_cm.load_config_settings(nixos_dir).get("git_keep_files", []) or [])
    except Exception:
        keep = set()
    foreign = []
    config_json_tracked = False
    for f in tracked:
        p = Path(f)
        if p.suffix == ".nix" or f in ("flake.lock", ".gitignore") or f in keep:
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


# Relative path references in .nix files (./foo or ../foo). Interpolations and
# absolute paths are out of scope – this is a cheap name-based heuristic.
_NIX_REL_PATH_RE = re.compile(r"(?<![A-Za-z0-9_./])\.\.?/[A-Za-z0-9_@+.\-/]+")


def _rule_flake_untracked_reference(nixos_dir: str, config: dict, is_flake: bool,
                                    host: str | None = None) -> list[Finding]:
    from . import git_manager as _gm
    if not is_flake or not _gm.is_git_repo(nixos_dir):
        return []
    tracked = set(_gm.list_tracked_files(nixos_dir))
    root = Path(nixos_dir).resolve()
    # {referenced file: [referencing .nix files]}. Untracked .nix targets are
    # skipped: NiCo stages new .nix files automatically before every rebuild.
    missing: dict[str, list[str]] = {}
    for nix_file in sorted(root.rglob("*.nix")):
        if ".git" in nix_file.parts:
            continue
        try:
            text = nix_file.read_text(encoding="utf-8")
        except OSError:
            continue
        for m in _NIX_REL_PATH_RE.finditer(text):
            try:
                target = (nix_file.parent / m.group(0)).resolve()
            except OSError:
                continue
            if not target.is_file() or target.suffix == ".nix":
                continue
            try:
                rel = str(target.relative_to(root))
            except ValueError:
                continue  # reference outside the config dir
            if rel in tracked:
                continue
            missing.setdefault(rel, []).append(str(nix_file.relative_to(root)))
    if not missing:
        return []
    detail_lines = [f"{rel}  ←  {', '.join(sorted(set(refs)))}"
                    for rel, refs in sorted(missing.items())]
    detail_lines += ["",
                     "Flake-Builds sehen nur Dateien, die in Git erfasst sind. "
                     "Nicht getrackte (oder per .gitignore ausgeschlossene) Dateien "
                     "fehlen beim Build – er schlägt fehl. Lösung: Datei beim "
                     "nächsten Sicherungspunkt mit aufnehmen."]
    return [Finding(
        rule_id="flake_untracked_reference",
        severity="warning",
        message=f"{len(missing)} referenzierte Dateien sind nicht in Git erfasst – der Flake-Build wird fehlschlagen.", message_key="validator.f.flake_untracked_reference", params=[len(missing)],
        detail="\n".join(detail_lines),
    )]


# ── Host-local helpers (running machine, not config text) ─────────────────────

def _findmnt_of(path: str) -> tuple[str, str, str] | None:
    """
    Return (target, fstype, options) of the mount that contains `path`.

    findmnt --target walks up until it finds a mount point, so this works for
    plain files too.  When several lines come back (bind mounts, stacked
    entries) the deepest target wins – that is the one actually holding it.
    """
    try:
        result = subprocess.run(
            ["findmnt", "-n", "-o", "TARGET,FSTYPE,OPTIONS", "--target", path],
            capture_output=True, text=True, timeout=5,
        )
    except Exception:
        return None
    rows = []
    for line in (result.stdout or "").splitlines():
        parts = line.split(None, 2)
        if len(parts) == 3:
            rows.append((parts[0], parts[1], parts[2]))
    if not rows:
        return None
    return max(rows, key=lambda r: len(r[0]))


def _subvol_of(options: str) -> str | None:
    """Extract the btrfs subvolume from a mount option string, normalised to /name."""
    for opt in options.split(","):
        opt = opt.strip()
        if opt.startswith("subvol="):
            return "/" + opt[len("subvol="):].strip().strip("/")
    return None


def _swap_files() -> list[str]:
    """
    File-backed swap entries of the running system, read from /proc/swaps.

    Partition swap (including zram) is skipped – only files live inside a
    btrfs subvolume and can block snapshots.  Paths are escaped octal-style
    by the kernel, so \\040 and friends are decoded back.
    """
    try:
        raw = Path("/proc/swaps").read_text()
    except Exception:
        return []
    files = []
    for line in raw.splitlines()[1:]:          # first line is the header
        parts = line.split()
        if len(parts) < 2 or parts[1] != "file":
            continue
        name = re.sub(r"\\(\d{3})", lambda m: chr(int(m.group(1), 8)), parts[0])
        files.append(name)
    return files


def running_host() -> str:
    """Hostname of the machine NiCo runs on (matches networking.hostName)."""
    return (platform.node() or "").split(".")[0]


def host_fingerprint(nixos_dir: str, host: str | None) -> str:
    """
    Short hash over the .nix files that make up `host`.

    Used to detect whether a stored host check still describes the current
    config.  Covers the host directory plus the root-level files every host
    inherits; content changes anywhere in there invalidate the check.
    """
    from . import config_manager

    base = Path(nixos_dir)
    files: list[Path] = []
    for name in ("configuration.nix", "flake.nix", "hardware-configuration.nix"):
        f = base / name
        if f.is_file():
            files.append(f)
    if host:
        settings  = config_manager.load_config_settings(nixos_dir)
        hosts_dir = settings.get("hosts_dir") or "hosts"
        host_root = base / hosts_dir / host
        if host_root.is_dir():
            files.extend(sorted(p for p in host_root.rglob("*.nix") if p.is_file()))
    digest = hashlib.sha256()
    for f in sorted(set(files)):
        try:
            digest.update(str(f.relative_to(base)).encode())
            digest.update(f.read_bytes())
        except Exception:
            continue
    return digest.hexdigest()[:16]


# ── Rules: btrfs maintenance without btrfs ────────────────────────────────────

def _mounted_fstypes() -> set[str]:
    """Filesystem types currently mounted on this machine."""
    types: set[str] = set()
    try:
        raw = Path("/proc/self/mounts").read_text()
    except Exception:
        return types
    for line in raw.splitlines():
        parts = line.split()
        if len(parts) >= 3:
            types.add(parts[2])
    return types


# Everything in a written config that only makes sense on btrfs. The first two
# are what NiCo itself writes from the form; the rest catch hand-written and
# imported declarations, which is where this rule used to be blind.
_BTRFS_MARKERS = (
    re.compile(r'services\s*\.\s*btrfs\s*\.\s*autoScrub'),
    re.compile(r'systemd\s*\.\s*(?:services|timers)\s*\.\s*"?[\w.-]*btrfs[\w.-]*"?'),
    re.compile(r'pkgs\s*\.\s*btrfs-progs'),
    re.compile(r'\bbtrfs\s+(?:balance|scrub|quota|subvolume|filesystem|device)\b'),
)


def _btrfs_declarations(nixos_dir: str, host: str | None) -> list[str]:
    """Files of the written config that set up something btrfs-only.

    Read from disk rather than from NiCo's model on purpose. A Nix-Brick is
    opaque to the model – `config` has no entry for it – so a rule asking
    "is auto-scrub on?" stayed silent for every setting that arrived with an
    import. On disk a brick is ordinary Nix and reads like anything else.
    Found this way on 2026-10-30: a `btrfs-dynamic-reclaim` unit inside a
    brick, which failed to start on a VM without btrfs and took the rebuild
    down with it. No form field exists for it, so no model entry either.
    """
    from . import config_manager as _cm
    from .importer import _strip_nix_comments

    base = Path(nixos_dir)
    cfg_settings = _cm.load_config_settings(nixos_dir)
    hosts_dir = (cfg_settings.get("hosts_dir") or "hosts").strip() or "hosts"
    hits: list[str] = []
    for nix_file in sorted(base.rglob("*.nix")):
        if ".git" in nix_file.parts:
            continue
        rel = nix_file.relative_to(base)
        # A host directory belongs to its own host only; the base config
        # applies to all of them.
        if len(rel.parts) >= 2 and rel.parts[0] == hosts_dir:
            if host and rel.parts[1] != host:
                continue
        try:
            content = nix_file.read_text(encoding="utf-8")
        except OSError:
            continue
        content = _strip_nix_comments(content)
        if any(rx.search(content) for rx in _BTRFS_MARKERS):
            hits.append(str(rel))
    return hits


def _rule_btrfs_without_btrfs(nixos_dir: str, config: dict, is_flake: bool,
                              host: str | None = None) -> list[Finding]:
    scrub   = bool(config.get("btrfs_scrub"))
    balance = bool(config.get("btrfs_balance"))
    declared = _btrfs_declarations(nixos_dir, host)
    if not (scrub or balance or declared):
        return []

    # /proc and findmnt read the running machine, so this can only speak for it.
    this_host = running_host()
    if host and this_host and host != this_host:
        return [Finding(
            rule_id="btrfs_without_btrfs",
            severity="info",
            message=f'Host "{host}" kann hier nicht auf btrfs geprüft werden – '
                    f'dieser Rechner ist "{this_host}".',
            message_key="validator.f.btrfs_without_btrfs.other_host",
            params=[host, this_host],
            detail="Die Prüfung liest die Mountpoints des laufenden Systems. "
                   "Führe den Validator auf dem betroffenen Rechner erneut aus.",
            detail_key="validator.d.btrfs_without_btrfs.other_host",
        )]

    findings: list[Finding] = []
    if "btrfs" not in _mounted_fstypes():
        if scrub or balance:
            # One key per case – a job list built here would put German words into
            # every translated message
            jobs, key = (("Auto-Scrub und Auto-Balance", "both") if scrub and balance else
                         ("Auto-Scrub", "scrub") if scrub else
                         ("Auto-Balance", "balance"))
            findings.append(Finding(
                rule_id="btrfs_without_btrfs",
                severity="warning",
                message=f"{jobs} aktiv, aber auf diesem Rechner ist kein btrfs eingebunden.",
                message_key=f"validator.f.btrfs_without_btrfs.no_btrfs_{key}",
                detail="Die Config baut und bootet, die Wartungsjobs laufen aber ins Leere: "
                       "Scrub und Balance gibt es nur für btrfs. Bei ext4 oder einem anderen "
                       "Dateisystem beide Schalter ausschalten.",
                detail_key="validator.d.btrfs_without_btrfs.no_btrfs",
            ))
        if declared:
            # Not a form field – this came from a brick or a hand-written file.
            # It is reported separately because the advice differs: there is no
            # switch to turn off, the declaration has to be removed or moved to
            # the host it belongs to.
            findings.append(Finding(
                rule_id="btrfs_without_btrfs",
                severity="warning",
                message=f"Diese Config richtet btrfs-Dienste ein, auf diesem Rechner "
                        f"ist aber kein btrfs eingebunden ({', '.join(declared[:3])}).",
                message_key="validator.f.btrfs_without_btrfs.declared",
                params=[", ".join(declared[:3])],
                detail="Gefunden in den geschriebenen .nix-Dateien, also auch in "
                       "Nix-Brix. Ein btrfs-Dienst auf einem Rechner ohne btrfs "
                       "scheitert beim Start und lässt den Rebuild fehlschlagen. "
                       "Gehört die Einstellung zu einem anderen Rechner, verschiebe "
                       "sie in dessen Host-Datei.",
                detail_key="validator.d.btrfs_without_btrfs.declared",
            ))
        return findings

    # btrfs exists somewhere, but NiCo's balance job runs on / specifically
    if balance:
        root_mount = _findmnt_of("/")
        if root_mount and root_mount[1] != "btrfs":
            findings.append(Finding(
                rule_id="btrfs_without_btrfs",
                severity="warning",
                message=f'Auto-Balance läuft auf "/", das ist hier kein btrfs '
                        f'(erkannt: "{root_mount[1]}").',
                message_key="validator.f.btrfs_without_btrfs.root_not_btrfs",
                params=[root_mount[1]],
                detail="Der von NiCo erzeugte Balance-Job arbeitet fest auf /. "
                       "Liegt das btrfs-Dateisystem woanders, bleibt der Job wirkungslos "
                       "und schlägt bei jedem Lauf fehl.",
                detail_key="validator.d.btrfs_without_btrfs.root_not_btrfs",
            ))
    return findings


# ── Rules: host-local swap / snapshot conflict ────────────────────────────────

def _rule_swap_in_snapshot(nixos_dir: str, config: dict, is_flake: bool,
                           host: str | None = None) -> list[Finding]:
    if not config.get("snapper_enable"):
        return []
    snapper_configs = [e for e in (config.get("snapper_configs") or [])
                       if (e.get("mountpoint") or "").strip()]
    if not snapper_configs:
        return []

    # findmnt reads the running machine.  Checking a host other than this one
    # would compare that host's snapper settings against these mounts.
    this_host = running_host()
    if host and this_host and host != this_host:
        return [Finding(
            rule_id="swap_in_snapshot",
            severity="info",
            message=f'Host "{host}" kann hier nicht auf Swap/Snapshot geprüft werden – '
                    f'dieser Rechner ist "{this_host}".',
            message_key="validator.f.swap_in_snapshot.other_host",
            params=[host, this_host],
            detail="Die Prüfung liest die Mountpoints des laufenden Systems. "
                   "Führe den Validator auf dem betroffenen Rechner erneut aus.",
            detail_key="validator.d.swap_in_snapshot.other_host",
        )]

    swaps = _swap_files()
    if not swaps:
        return []

    findings: list[Finding] = []
    for swap_path in swaps:
        swap_mount = _findmnt_of(swap_path)
        if not swap_mount or swap_mount[1] != "btrfs":
            continue
        swap_subvol = _subvol_of(swap_mount[2])
        if swap_subvol is None:
            continue
        for entry in snapper_configs:
            mount = (entry.get("mountpoint") or "").strip()
            name  = (entry.get("name") or "").strip() or mount
            snap_mount = _findmnt_of(mount)
            if not snap_mount or snap_mount[1] != "btrfs":
                continue
            snap_subvol = _subvol_of(snap_mount[2])
            if snap_subvol is None:
                continue
            # Same subvolume, or the swap sits below the snapshotted one.
            below = swap_subvol.startswith(snap_subvol.rstrip("/") + "/")
            if swap_subvol != snap_subvol and not below:
                continue
            findings.append(Finding(
                rule_id="swap_in_snapshot",
                severity="warning",
                message=f'Swapfile "{swap_path}" liegt im Subvolume "{swap_subvol}", '
                        f'das Snapper-Config "{name}" snapshottet.',
                message_key="validator.f.swap_in_snapshot.hit",
                params=[swap_path, swap_subvol, name],
                detail=(
                    "Btrfs verweigert Snapshots eines Subvolumes mit aktivem Swapfile "
                    "dauerhaft. Die Config baut und bootet trotzdem – der Fehler zeigt "
                    "sich erst beim nächsten Timeline-Snapshot, danach können "
                    "btrfs-cleaner/btrfs-transaction dauerhaft CPU-Last erzeugen.\n"
                    "\n"
                    "Geprüft wurde der Ist-Zustand des laufenden Systems "
                    "(/proc/swaps und findmnt), nicht der Stand der Config-Dateien. "
                    "Nach einer Änderung gilt das Ergebnis erst nach dem nächsten "
                    "Rebuild und Neustart.\n"
                    "\n"
                    "Lösung: eigenes Subvolume für den Swap anlegen (außerhalb der "
                    "gesnapshotteten Hierarchie), fileSystems.\"/swap\" mit "
                    "subvol=swap ergänzen, swapDevices auf /swap/swapfile umbiegen "
                    "und das Swapfile mit chattr +C (NoCOW) neu anlegen statt es zu "
                    "verschieben."
                ),
                detail_key="validator.d.swap_in_snapshot",
            ))
    return findings


# ── Rules: pending host checks ────────────────────────────────────────────────

def _rule_host_checks_pending(nixos_dir: str, config: dict, is_flake: bool,
                              host: str | None = None) -> list[Finding]:
    from . import config_manager

    hosts = config_manager.scan_hosts(nixos_dir)
    if len(hosts) < 2:
        return []          # single-host configs have nothing to defer

    cfg_settings = config_manager.load_config_settings(nixos_dir)
    stored       = cfg_settings.get("host_checks") or {}
    this_host    = running_host()

    never, stale = [], []
    for name in hosts:
        entry = stored.get(name)
        if not entry:
            never.append(name)
            continue
        if entry.get("fp") != host_fingerprint(nixos_dir, name):
            # Date in parentheses instead of "zuletzt <date>": the params of a
            # message_key end up in the UI verbatim, so they must not carry
            # German words into the other six languages.
            stale.append(f'{name} ({entry.get("at", "?")[:10]})')

    if not never and not stale:
        return []

    # One key per case instead of a pre-joined string, otherwise the translated
    # sentence would still contain a German list built here.
    never_str, stale_str = ", ".join(never), ", ".join(stale)
    if never and stale:
        message_key = "validator.f.host_checks_pending.both"
        params      = [never_str, stale_str]
        message     = (f"Rechnerabhängige Prüfungen offen – noch nie geprüft: {never_str}; "
                       f"Config seit der Prüfung geändert: {stale_str}.")
    elif never:
        message_key = "validator.f.host_checks_pending.never"
        params      = [never_str]
        message     = f"Rechnerabhängige Prüfungen offen – noch nie geprüft: {never_str}."
    else:
        message_key = "validator.f.host_checks_pending.stale"
        params      = [stale_str]
        message     = f"Rechnerabhängige Prüfungen offen – Config seit der Prüfung geändert: {stale_str}."

    return [Finding(
        rule_id="host_checks_pending",
        severity="info",
        message=message,
        message_key=message_key,
        params=params,
        detail=(
            "Einige Regeln lesen das laufende System (Mountpoints, Datenträger, "
            f'Architektur) und gelten daher nur für "{this_host}". '
            "Für die genannten Hosts muss der Validator auf dem jeweiligen Rechner "
            "laufen. NiCo merkt sich das Ergebnis in config.json und zeigt es hier "
            "auf jedem Rechner an, sobald die Config synchronisiert ist."
        ),
        detail_key="validator.d.host_checks_pending", detail_params=[this_host],
    )]


# ── Rule function registry ─────────────────────────────────────────────────────

_RULE_FNS: dict[str, object] = {
    "user_in_config":        _rule_user_in_config,
    "flake_host_exists":     _rule_flake_host_exists,
    "host_orphaned":         _rule_host_orphaned,
    "flake_arch_matches":    _rule_flake_arch_matches,
    "hardware_imported":     _rule_hardware_imported,
    "hardware_host_location": _rule_hardware_host_location,
    "user_name_valid":       _rule_user_name_valid,
    "user_extra_conflict":   _rule_user_extra_conflict,
    "user_no_password":      _rule_user_no_password,
    "user_uid_duplicate":    _rule_user_uid_duplicate,
    "user_uid_system":       _rule_user_uid_system,
    "group_gid_duplicate":   _rule_group_gid_duplicate,
    "group_gid_system":      _rule_group_gid_system,
    "hardware_matches":      _rule_hardware_matches,
    "duplicate_attrs":       _rule_duplicate_attrs,
    "imports_exist":         _rule_imports_exist,
    "brix_redundant":        _rule_brix_redundant,
    "brix_nested":           _rule_brix_nested,
    "hm_user_defined":       _rule_hm_user_defined,
    "hm_missing_file":       _rule_hm_missing_file,
    "hm_orphan_root":        _rule_hm_orphan_root,
    "hm_allowunfree":        _rule_hm_allowunfree,
    "flake_hm_branch":       _rule_flake_hm_branch,
    "state_version_match":   _rule_state_version_match,
    "state_version_future":  _rule_state_version_future,
    "state_version_missing": _rule_state_version_missing,
    "state_version_per_host": _rule_state_version_per_host,
    "hm_state_version_match": _rule_hm_state_version_match,
    "hm_duplicate_args":      _rule_hm_duplicate_args,
    "snapper_btrfs":          _rule_snapper_btrfs,
    "btrfs_without_btrfs":    _rule_btrfs_without_btrfs,
    "swap_in_snapshot":       _rule_swap_in_snapshot,
    "host_checks_pending":    _rule_host_checks_pending,
    "snapper_in_host":        _rule_snapper_in_host,
    "git_missing_gitignore":  _rule_git_missing_gitignore,
    "git_large_log":          _rule_git_large_log,
    "flake_untracked_reference": _rule_flake_untracked_reference,
    "git_foreign_files":      _rule_git_foreign_files,
}
