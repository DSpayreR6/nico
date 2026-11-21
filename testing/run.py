#!/usr/bin/env python3
"""
Headless NiCo roundtrip runner.

Each test case lives in testing/configs/<name>/original. The runner copies that
directory to a temporary work tree, lets NiCo parse and write the files, then
copies the result to testing/configs/<name>/result and reports simple
regressions.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
CONFIGS_DIR = Path(__file__).resolve().parent / "configs"

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from nico import config_manager, core, fidelity, generator, hm_generator, importer  # noqa: E402
from nico.brix import brix_content_to_bricks, extract_brick_blocks, strip_brick_blocks  # noqa: E402
from nico.core import _merge_notes, get_nico_type, keep_unmodelled  # noqa: E402


@dataclass
class CaseResult:
    name: str
    ok: bool = True
    info: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    report: dict[str, Any] = field(default_factory=dict)

    def warn(self, message: str) -> None:
        self.warnings.append(message)

    def fail(self, message: str) -> None:
        self.ok = False
        self.errors.append(message)


def _is_sensitive(path: Path) -> bool:
    name = path.name.lower()
    return (
        name == ".env"
        or name.endswith(".key")
        or "credential" in name
        or "secret" in name
    )


def _nix_files(root: Path):
    """The .nix files below root a check may compare: what _copy_tree copies.
    A sensitive directory (e.g. `secrets/`) is skipped as a whole, so the
    files in it count as sensitive too - judged by name alone, the original's
    secrets/rekey/default.nix looked lost in the result (2026-11-21)."""
    for p in sorted(root.rglob("*.nix")):
        rel = p.relative_to(root)
        if ".git" in rel.parts or any(_is_sensitive(Path(part)) for part in rel.parts):
            continue
        yield p


def _copy_tree(src: Path, dst: Path) -> None:
    def ignore(directory: str, names: list[str]) -> set[str]:
        base = Path(directory)
        return {name for name in names if _is_sensitive(base / name)}

    shutil.copytree(src, dst, ignore=ignore)


def _has_payload_files(path: Path) -> bool:
    return any(p.is_file() and p.name != ".gitkeep" for p in path.rglob("*"))


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def _normalize(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _normalize(value[k]) for k in sorted(value)}
    if isinstance(value, list):
        return sorted((_normalize(v) for v in value), key=repr)
    return value


def _flatten(data: dict[str, Any], prefix: str = "") -> dict[str, Any]:
    flat: dict[str, Any] = {}
    for key, value in data.items():
        full_key = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict):
            flat.update(_flatten(value, full_key))
        else:
            flat[full_key] = value
    return flat


def _managed_fields_from_original(config_dir: Path) -> dict[str, Any]:
    fields: dict[str, Any] = {}

    cfg_path = config_dir / "configuration.nix"
    if cfg_path.exists():
        content = strip_brick_blocks(_read_text(cfg_path))
        fields.update(importer.parse_config(content))

    home_path = config_dir / "home.nix"
    if home_path.exists():
        content = strip_brick_blocks(_read_text(home_path))
        hm_fields = importer.parse_home_config(content)
        if hm_fields:
            fields["home_manager"] = hm_fields

    flake_path = config_dir / "flake.nix"
    if flake_path.exists():
        fields.update(importer.parse_flake_nix(_read_text(flake_path)))

    return fields


def _write_nico_roundtrip(config_dir: Path) -> None:
    """What NiCo itself does with a config that was just imported.

    Since 2026-11-21 the product's own functions, no rebuilt steps (Codex
    revision 2026-11-20): the import of configuration.nix
    (core.apply_import_result, with save_config and its guards - raw mode,
    duplicate attributes, things NiCo cannot represent), the take-over of
    flake.nix (core.reimport_flake), and for every other file with a form
    behind it - host files, Home Manager files - the restructuring NiCo
    offers when it is opened (core.restructure_file), as if the user
    answered yes to each. What a step refuses stays as it came, as in the
    product. The hand-built version is kept below as _write_nico_roundtrip_old
    for comparison only (--old).
    """
    nixos_dir = str(config_dir)
    cfg_path = config_dir / "configuration.nix"
    flake_path = config_dir / "flake.nix"

    data = config_manager.load_config(nixos_dir) or config_manager.get_defaults()
    if cfg_path.exists():
        content = _read_text(cfg_path)
        recognized = importer.parse_config(content)
        rest_brix = importer.build_rest_brix(content, recognized)
        data = core.apply_import_result(nixos_dir, recognized, rest_brix, content)
    if flake_path.exists():
        core.reimport_flake(nixos_dir, data)

    for path in _nix_files(config_dir):
        rel = path.relative_to(config_dir).as_posix()
        if rel in ("configuration.nix", "flake.nix"):
            continue
        text = _read_text(path)
        ftype = get_nico_type(text)
        if core._form_kind(nixos_dir, rel, ftype) is None:
            continue
        if core._structure_unmappable(nixos_dir, rel, text, ftype) \
                or core.file_in_raw_mode(path):
            continue                 # opens in text mode, never restructured
        try:
            core.restructure_file(nixos_dir, rel)
        except (fidelity.NotFaithful, config_manager.NotMappableError,
                config_manager.DuplicateAttrError, ValueError):
            pass                     # the product leaves it as it is


def _write_nico_roundtrip_old(config_dir: Path) -> None:
    data = config_manager.load_config(str(config_dir))

    # The same guard the product applies (revision 2026-11-10, package 5a): a
    # file NiCo cannot represent is not regenerated. The runner called the
    # generators directly and so kept producing the damage the product no
    # longer does – 12 of 13 result/flake.nix had lost their outputs block.
    cfg_path = config_dir / "configuration.nix"
    flake_path = config_dir / "flake.nix"
    home_path = config_dir / "home.nix"
    keep_cfg = cfg_path.exists() and importer.config_unmappable(_read_text(cfg_path))
    keep_flake = flake_path.exists() and \
        importer.flake_unmappable(_read_text(flake_path)) is not None
    keep_home = home_path.exists() and importer.config_unmappable(_read_text(home_path))

    cfg_content = None
    if cfg_path.exists() and not keep_cfg:
        content = cfg_content = _read_text(cfg_path)
        clean = strip_brick_blocks(content)
        recognized = importer.parse_config(clean)
        rest = importer.build_rest_brix(clean, recognized)
        blocks = extract_brick_blocks(content)
        if rest.strip():
            blocks.update(brix_content_to_bricks(rest, section="HEAD", existing_blocks=blocks))
        data.update(recognized)
        data["brick_blocks"] = blocks
        # Same step the import routes take: rescue what the generator would not
        # write back, so the roundtrip exercises the real path.
        importer.rescue_into_bricks(data, content)

    if (config_dir / "hardware-configuration.nix").exists():
        data["hardware_config"] = True

    if cfg_content is not None:
        # The Gegenprobe of the import (server._apply_import_result, 2026-11-19).
        # A file it cannot settle stays as it came, as in the product.
        data["_flake_mode"] = flake_path.exists()
        try:
            data = fidelity.settle(cfg_content, data, generator.generate_configuration_nix)
        except fidelity.NotFaithful:
            keep_cfg = True

    if flake_path.exists():
        # As in the app since revision 2026-11-10 (B34): a flake.nix means
        # flake mode; the `flakes` field is only the experimental-features
        # line and stays what the imported configuration.nix says.
        data["_flake_mode"] = True
        flake_content = _read_text(flake_path)
        data.update(importer.parse_flake_nix(flake_content))
        if not keep_flake:
            data["flake_brick_blocks"] = importer.build_flake_brix(flake_content)

    if home_path.exists() and not keep_home:
        home_content = _read_text(home_path)
        clean_home = strip_brick_blocks(home_content)
        hm = importer.parse_home_config(clean_home)
        rest_home = importer.build_home_rest_brix(clean_home, hm)
        hm_blocks = extract_brick_blocks(home_content)
        if rest_home.strip():
            hm_blocks.update(brix_content_to_bricks(rest_home, section="End", existing_blocks=hm_blocks))
        # Comments outside bricks, as core.normalize_hm_content keeps them.
        hm_blocks = _merge_notes(
            hm_blocks, importer.collect_notes(home_content, rest_comments=False),
            render=lambda b: hm_generator.generate_home_nix({**hm, "hm_brick_blocks": b}))
        hm["hm_brick_blocks"] = hm_blocks
        # Gegenprobe as in core.normalize_hm_content (2026-11-19, phase 3).
        try:
            hm = fidelity.settle(home_content, hm, hm_generator.generate_home_nix,
                                 bricks_key="hm_brick_blocks",
                                 section=hm_generator.HM_SECTION_END)
            hm_blocks = hm["hm_brick_blocks"]
        except fidelity.NotFaithful:
            keep_home = True
        data["home_manager"] = hm
        data["hm_brick_blocks"] = hm_blocks

    if not keep_cfg:
        if cfg_content is not None:
            # What config_manager.save_config does with the imported file on
            # disk: rest statements and comments outside bricks are kept
            # (core.keep_unmodelled). Without it the runner never saw the
            # comment rescue of 2026-11-17.
            data["brick_blocks"] = keep_unmodelled(
                cfg_content, data.get("brick_blocks"),
                render=lambda b: generator.generate_configuration_nix({**data, "brick_blocks": b}))
        cfg_path.write_text(generator.generate_configuration_nix(data), encoding="utf-8")

    if flake_path.exists() and not keep_flake:
        flake_text = generator.generate_flake_nix(data, nixos_dir=str(config_dir))
        try:
            # As core.reimport_flake (Gegenprobe, 2026-11-19): a flake NiCo
            # would write differently stays as it came.
            fidelity.require_faithful(flake_content, flake_text)
            flake_path.write_text(flake_text, encoding="utf-8")
        except fidelity.NotFaithful:
            pass

    hm = dict(data.get("home_manager") or {})
    if (hm.get("enabled") or home_path.exists()) and not keep_home:
        hm["hm_brick_blocks"] = data.get("hm_brick_blocks", {})
        home_path.write_text(hm_generator.generate_home_nix(hm), encoding="utf-8")


def _check_fidelity(original: Path, result: Path, case: CaseResult) -> bool:
    """configuration.nix says after the roundtrip what it said before.

    Full attribute path and value, through nico/fidelity.py – the same
    comparison the import's Gegenprobe uses (2026-11-19). It replaces, for
    this file, the comparison of form fields: a field the Gegenprobe left in
    a Nix-Brick is not in the form any more and still not lost. Revision
    2026-11-18, finding 29: last name segments and skipped empty values hid
    a changed group list. Returns True when the file is faithful.
    """
    faithful = True
    # Every .nix file, not only the three in the top level: a changed group
    # list in a host file passed unnoticed (Codex re-check 2026-11-20,
    # finding 29). The file is named with every hit, so the same attribute
    # name in another module cannot hide it.
    names = [p.relative_to(original).as_posix() for p in _nix_files(original)]
    for name in names:
        before, after = original / name, result / name
        if not (before.exists() and after.exists()):
            continue
        diff = fidelity.compare(_read_text(before), _read_text(after))
        bad = sorted(fidelity.offending(diff)) + sorted(p for p, _ in diff.missing)
        for path in bad[:12]:
            case.fail(f"{name} says something else after roundtrip: {path}")
        if bad and name == "configuration.nix":
            faithful = False
    return faithful


def _compare_managed_fields(original: Path, result: Path, case: CaseResult,
                            co_faithful: bool = False) -> None:
    expected = _flatten(_managed_fields_from_original(original))
    actual = _flatten(_managed_fields_from_original(result))
    if co_faithful:
        # The fidelity check has compared configuration.nix by path and
        # value already; its fields may have moved into Nix-Bricks.
        co = original / "configuration.nix"
        co_keys = set(_flatten(importer.parse_config(strip_brick_blocks(_read_text(co))))) \
            if co.exists() else set()
        expected = {k: v for k, v in expected.items() if k not in co_keys}

    ignored = {"brick_blocks", "flake_brick_blocks", "hm_brick_blocks"}
    for key, expected_value in sorted(expected.items()):
        if key in ignored or key.rsplit(".", 1)[-1] in ignored:
            continue
        if key not in actual:
            # An option that is empty in the original (e.g. a systemPackages
            # list with every entry commented out) is not written back at all –
            # NiCo omits empty options on purpose. Nothing is lost, so this is
            # not a regression.
            if expected_value in ([], {}, "", None):
                continue
            case.fail(f"missing managed field after roundtrip: {key}")
            continue
        if _normalize(actual[key]) != _normalize(expected_value):
            case.fail(
                "changed managed field after roundtrip: "
                f"{key}: {expected_value!r} -> {actual[key]!r}"
            )


def _check_unmanaged_preserved(original: Path, result: Path, case: CaseResult) -> None:
    original_cfg = original / "configuration.nix"
    result_cfg = result / "configuration.nix"
    if not original_cfg.exists() or not result_cfg.exists():
        return

    content = strip_brick_blocks(_read_text(original_cfg))
    recognized = importer.parse_config(content)
    rest = importer.build_rest_brix(content, recognized)
    if not rest.strip():
        return

    result_text = _read_text(result_cfg)
    missing_lines = []
    for line in rest.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("# <") or stripped.startswith("# </"):
            continue
        if stripped not in result_text:
            missing_lines.append(stripped)
            if len(missing_lines) >= 5:
                break

    if missing_lines:
        case.fail("unmanaged Nix content missing from result: " + "; ".join(missing_lines))


def _run_command(args: list[str], cwd: Path, timeout: int = 120) -> tuple[int, str]:
    proc = subprocess.run(
        args,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    output = (proc.stdout + proc.stderr).strip()
    return proc.returncode, output


def _dry_run_signals(output: str) -> dict[str, Any]:
    missing_paths = re.findall(r"error: path '([^']+)' does not exist", output)
    missing_attrs = re.findall(r"error: attribute '([^']+)' missing", output)
    return {
        "lockfile_updated": "updating lock file" in output or "creating lock file" in output,
        "missing_paths": missing_paths,
        "missing_attrs": missing_attrs,
    }


def _classify_dry_run_output(output: str) -> tuple[str, str]:
    signals = _dry_run_signals(output)
    if signals["missing_paths"]:
        path = signals["missing_paths"][0]
        return "MISSING_PATH", f"Fehlender Pfad: {path}"
    if signals["missing_attrs"]:
        attr = signals["missing_attrs"][0]
        return "MISSING_ATTR", f"Fehlendes Attribut/Argument: {attr}"
    if signals["lockfile_updated"]:
        return "LOCKFILE_MUTATION", "Lockfile wurde waehrend der Auswertung aktualisiert"
    if "not tracked by Git" in output:
        return "GIT_TRACKING", "Flake-Dateien sind fuer Nix im Git-Kontext nicht sichtbar"
    if "timed out" in output.lower():
        return "TIMEOUT", "Dry-Run lief in ein Timeout"
    return "EVAL_ERROR_OTHER", "Sonstiger Evaluationsfehler"


def _classify_severity(category: str, output: str = "") -> str:
    if category in {"NO_HOST_DETECTED", "NO_NIXOS_REBUILD", "NO_CONFIGURATION_NIX", "NO_NIX_INSTANTIATE", "TIMEOUT"}:
        return "yellow"
    if category == "MISSING_PATH":
        missing_paths = _dry_run_signals(output).get("missing_paths", [])
        if any(path.endswith("/hardware-configuration.nix") for path in missing_paths):
            return "yellow"
        return "red"
    if category == "EVAL_ERROR_OTHER" and "cannot find flake 'flake:" in output:
        return "yellow"
    if category in {"MISSING_ATTR", "GIT_TRACKING", "EVAL_ERROR_OTHER"}:
        return "red"
    if category == "LOCKFILE_MUTATION":
        return "green"
    return "red"


def _append_host_report(case: CaseResult, host: str, *, status: str, output: str = "", skipped_reason: str = "") -> None:
    dry_run = case.report.setdefault("dry_run", {})
    host_results = dry_run.setdefault("host_results", [])
    entry: dict[str, Any] = {"host": host, "status": status}
    if skipped_reason:
        entry["category"] = skipped_reason
        entry["summary"] = skipped_reason
        entry["severity"] = _classify_severity(skipped_reason)
    elif output:
        if status == "passed":
            entry["category"] = "PASSED"
            entry["summary"] = "Dry-Run erfolgreich"
            entry["severity"] = "green"
        else:
            category, summary = _classify_dry_run_output(output)
            entry["category"] = category
            entry["summary"] = summary
            entry["severity"] = _classify_severity(category, output)
        entry["signals"] = _dry_run_signals(output)
        entry["output_excerpt"] = output[:1200]
    host_results.append(entry)


def _case_severity(case: CaseResult) -> str:
    dry_run = case.report.get("dry_run", {})
    host_results = dry_run.get("host_results", [])
    severity = dry_run.get("severity")
    if host_results:
        severities = [str(host_result.get("severity") or "red") for host_result in host_results]
        if "red" in severities:
            return "red"
        if "yellow" in severities:
            return "yellow"
        return "green"
    if isinstance(severity, str) and severity:
        return severity
    if dry_run.get("status") == "passed":
        return "green"
    if dry_run.get("status") == "skipped":
        return _classify_severity(str(dry_run.get("reason") or ""))
    if dry_run.get("status") == "failed":
        return _classify_severity(str(dry_run.get("category") or ""), str(dry_run.get("output_excerpt") or ""))
    return "red" if not case.ok else "green"


# Attributes NiCo deliberately writes under their current name – an old
# spelling missing from the result is a migration, not a loss.
def _ts_attr_parts(attrpath_node) -> list[str]:
    """Components of an attrpath node as plain names ("a"."b" → a, b)."""
    parts = []
    for ch in attrpath_node.children:
        if ch.type == ".":
            continue
        parts.append(ch.text.decode("utf-8", "replace").strip('"'))
    return parts


def _ts_is_empty(value) -> bool:
    """[ ], { }, "" - also behind `with x;`."""
    while value is not None and value.type == "with_expression":
        value = value.children[-1] if value.children else None
    if value is None:
        return False
    t = value.type
    named = [c for c in value.children if c.is_named and c.type != "comment"]
    if t == "list_expression" or t == "attrset_expression":
        return not named or (t == "attrset_expression" and
                             all(c.type == "binding_set" and not c.named_children for c in named))
    if t == "string_expression":
        return not named
    return False


def _ts_bindings(text: str) -> list:
    """(attribute path components, value is empty) for every binding in the
    text, at any depth, read with tree-sitter alone. Empty without it."""
    from nico import nix_parser
    tree = nix_parser.parse_tree(text)
    if tree is None:
        return []
    out: list = []

    def walk(node):
        if node.type == "binding":
            attr = value = None
            for ch in node.children:
                if ch.type == "attrpath":
                    attr = ch
                elif attr is not None and value is None and ch.type not in ("=", ";", "comment"):
                    value = ch
            if attr is not None:
                out.append((_ts_attr_parts(attr), _ts_is_empty(value)))
        for ch in node.children:
            walk(ch)

    walk(tree.root_node)
    return out


def _ts_duplicates(text: str) -> set:
    """Attribute paths Nix refuses as defined twice, found with tree-sitter.

    Within one attribute set, nested sets merge (`a.b = 1; a = { c = 2; };`
    is fine); the same leaf twice, or a leaf where a set is, is an error.
    Every attribute set that is not itself the value of a binding starts its
    own namespace (a list element, a function argument, a let body).
    """
    from nico import nix_parser
    tree = nix_parser.parse_tree(text)
    if tree is None:
        return set()
    dups: set = set()

    def namespaces(node):
        if node.type == "attrset_expression":
            collect(node, (), {})
            return
        for ch in node.children:
            namespaces(ch)

    def collect(attrset, prefix: tuple, seen: dict) -> None:
        for bs in (c for c in attrset.children if c.type == "binding_set"):
            for b in bs.children:
                if b.type == "inherit":
                    for ia in (c for c in b.children if c.type == "inherited_attrs"):
                        for name in ia.named_children:
                            p = prefix + (name.text.decode("utf-8", "replace"),)
                            if p in seen:
                                dups.add(".".join(p))
                            seen[p] = "leaf"
                    continue
                if b.type != "binding":
                    continue
                attr = value = None
                for ch in b.children:
                    if ch.type == "attrpath":
                        attr = ch
                    elif attr is not None and value is None and ch.type not in ("=", ";", "comment"):
                        value = ch
                if attr is None:
                    continue
                parts = _ts_attr_parts(attr)
                if any("${" in p for p in parts):
                    continue                     # name only known to Nix
                path = prefix + tuple(parts)
                for k in range(len(prefix) + 1, len(path)):
                    if seen.get(path[:k]) == "leaf":
                        dups.add(".".join(path[:k]))
                    seen.setdefault(path[:k], "set")
                if value is not None and value.type == "attrset_expression":
                    if seen.get(path) == "leaf":
                        dups.add(".".join(path))
                    seen.setdefault(path, "set")
                    collect(value, path, seen)
                else:
                    if path in seen:
                        dups.add(".".join(path))
                    seen[path] = "leaf"
                    if value is not None:
                        namespaces(value)

    namespaces(tree.root_node)
    return dups


_RENAMED_AWAY = {
    "tmpOnTmpfs", "tmpOnTmpfsSize", "cleanTmpDir",   # → boot.tmp.*
    "opengl", "driSupport", "driSupport32Bit",       # → hardware.graphics
    "pulseaudio",                                    # → services.pulseaudio
}


def _check_no_lost_attributes(original: Path, result: Path, case: CaseResult) -> None:
    """Every attribute of the original must still be defined in the result.

    The older check only looked at content that reached the rest-brix, so a
    block NiCo understood *partly* was never examined – that blind spot hid the
    loss of ssh settings, printer drivers and keyboard options for a long time.
    Files NiCo does not generate are compared as well: it must not drop them.
    """
    # Read with tree-sitter since 2026-11-21, not with NiCo's own text walker
    # (importer._mask_literals): a checker that shares the walker shares its
    # blind spots - that walker cut `"…${f "#"}…"` and so did the check.
    # Every binding counts, nested and on one line alike; comments and
    # strings are not code to the parser. An option that is empty in the
    # original is skipped: NiCo does not write empty options.
    def names(path: Path) -> set[str]:
        return {parts[-1] for parts, empty in _ts_bindings(_read_text(path)) if not empty}

    # flake.nix is excluded on purpose: its roundtrip restructures inputs and
    # outputs, and whether that loses content is a separate question with its
    # own answer still open (noted in manifest.md). Mixing it in here would
    # bury the loss this check exists for.
    def relevant(root: Path):
        return (p for p in _nix_files(root) if p.name != "flake.nix")

    before: set[str] = set()
    after: set[str] = set()
    for nix_file in relevant(original):
        before |= names(nix_file)
    for nix_file in relevant(result):
        after |= names(nix_file)

    lost = sorted(n for n in (before - after) if n not in _RENAMED_AWAY)
    if lost:
        case.fail("attributes lost after roundtrip: " + ", ".join(lost[:12]))


_RE_BRICK_MARK = re.compile(r'^#\s*</?brick:')
# NiCo's own lines: header, version line and section headings, which a newer
# NiCo writes anew (e.g. "── Lokalisierung ──" became "── I18N · Localisation ──").
_RE_NICO_OWN = re.compile(
    r'^#\s*(?:──.*──|nico-version:|Generated by NiCo|Do not edit manually|'
    r'Host-specific configuration for |Host-spezifische Konfiguration für )')


def _comment_lines(text: str) -> list[str]:
    """Every comment's text, whitespace collapsed, brick markers left out.

    Read with tree-sitter, so a `#` inside a string is no comment; without
    the grammar nothing is compared (the fidelity check still runs).
    """
    from nico import nix_parser
    tree = nix_parser.parse_tree(text)
    if tree is None:
        return []
    out: list[str] = []

    def walk(node):
        if node.type == "comment":
            for line in node.text.decode("utf-8", "replace").splitlines():
                s = " ".join(line.split())
                if not s.startswith("#"):
                    # /* … */: the text inside counts, the delimiters do not
                    # (the rescue writes `/* fish */` as a note "fish").
                    s = re.sub(r'^(?:/\*|\*)\s*', '', s)
                    s = re.sub(r'\s*\*/$', '', s)
                if s and s != "#" and not _RE_BRICK_MARK.match(s) \
                        and not _RE_NICO_OWN.match(s):
                    out.append(s)
        for ch in node.children:
            walk(ch)

    walk(tree.root_node)
    return out


def _check_comments_kept(original: Path, result: Path, case: CaseResult) -> None:
    """Every comment of the original is still in the same file afterwards.

    Independent of nico/fidelity.py, which the import itself uses to decide
    what it may take - a test built on the same comparison cannot find its
    blind spots (Codex revision 2026-11-20). Comments are what NiCo has
    lost most often (rescue as notes since 2026-11-17). Compared as text
    with whitespace collapsed; NiCo may move a comment, not drop it.
    """
    lost: list[str] = []
    for before in _nix_files(original):
        rel = before.relative_to(original)
        after = result / rel
        if not after.exists():
            continue
        kept = " ".join(_read_text(after).split())
        for line in _comment_lines(_read_text(before)):
            body = line.lstrip("#").strip()
            if body and body not in kept:
                lost.append(f"{rel.as_posix()}: {line[:60]}")
    for item in lost[:12]:
        case.fail(f"comment lost after roundtrip: {item}")


def _check_no_duplicate_attributes(original: Path, result: Path, case: CaseResult) -> None:
    """No leaf defined twice that was not defined twice before.

    Nix merges two definitions of the same path but refuses the same leaf,
    so this is what turns a rescued brick colliding with the form's output
    into a failing test instead of an unbuildable config. Since 2026-11-21
    with tree-sitter (_ts_duplicates), not with importer.duplicate_leaf_paths
    - the guard save_config itself uses, so it could never catch what that
    guard misses - and for every file, not only those NiCo generated.
    """
    for nix_file in _nix_files(result):
        rel = nix_file.relative_to(result)
        dupes = _ts_duplicates(_read_text(nix_file))
        before = original / rel
        if before.exists():
            dupes -= _ts_duplicates(_read_text(before))
        if dupes:
            case.fail(f"duplicate attributes in {rel}: " + ", ".join(sorted(dupes)[:8]))


def _syntax_check(result: Path, case: CaseResult) -> None:
    if shutil.which("nix-instantiate") is None:
        case.warn("nix-instantiate not found; syntax check skipped")
        return

    for nix_file in sorted(result.rglob("*.nix")):
        code, output = _run_command(["nix-instantiate", "--parse", str(nix_file)], result)
        if code != 0:
            rel = nix_file.relative_to(result)
            case.fail(f"syntax check failed for {rel}: {output}")


def _rebuild_dry_run(result: Path, case: CaseResult) -> None:
    def _infer_flake_hosts(flake_content: str) -> list[str]:
        fields = importer.parse_flake_nix(flake_content)
        hosts: list[str] = []

        default_host = fields.get("flake_default_host") or fields.get("hostname")
        if isinstance(default_host, str) and default_host.strip():
            hosts.append(default_host.strip())

        for entry in (fields.get("flake_hosts") or []):
            if isinstance(entry, dict):
                name = (entry.get("name") or "").strip()
            else:
                name = str(entry).strip()
            if name and name not in hosts:
                hosts.append(name)

        # Fallback for flakes that declare hosts as dotted assignments like
        # `nixosConfigurations.<name> = nixpkgs.lib.nixosSystem { ... };`
        for match in __import__("re").finditer(
            r'\bnixosConfigurations\.(?:"([^"]+)"|([\w.-]+))\s*=',
            flake_content,
        ):
            name = (match.group(1) or match.group(2) or "").strip()
            if name and name not in hosts:
                hosts.append(name)

        return hosts

    flake_path = result / "flake.nix"
    if flake_path.exists():
        case.report["mode"] = "flake"
        if shutil.which("nixos-rebuild") is None:
            case.warn("nixos-rebuild not found; rebuild dry-run skipped")
            case.report["dry_run"] = {"status": "skipped", "reason": "NO_NIXOS_REBUILD"}
            return
        hosts = _infer_flake_hosts(_read_text(flake_path))
        case.report["dry_run"] = {"status": "running", "hosts_detected": hosts}
        if not hosts:
            case.warn("flake.nix found, but no host detected; rebuild dry-run skipped")
            case.report["dry_run"] = {"status": "skipped", "reason": "NO_HOST_DETECTED", "hosts_detected": []}
            return
        flake_tmp = Path(tempfile.mkdtemp(prefix=f"nico-flake-dry-run-{case.name}-"))
        flake_work = flake_tmp / "config"
        try:
            _copy_tree(result, flake_work)
            for host in hosts:
                args = ["nixos-rebuild", "dry-run", "--flake", f"{flake_work}#{host}"]
                code, output = _run_command(args, flake_work, timeout=900)
                if code != 0:
                    _append_host_report(case, host, status="failed", output=output)
                    case.fail(f'rebuild dry-run failed for flake host "{host}": ' + output)
                else:
                    _append_host_report(case, host, status="passed", output=output)
        finally:
            shutil.rmtree(flake_tmp, ignore_errors=True)
        if case.report.get("dry_run", {}).get("status") == "running":
            case.report["dry_run"]["status"] = "completed"
        return

    cfg_path = result / "configuration.nix"
    case.report["mode"] = "non-flake"
    if not cfg_path.exists():
        case.warn("no configuration.nix found; rebuild dry-run skipped")
        case.report["dry_run"] = {"status": "skipped", "reason": "NO_CONFIGURATION_NIX"}
        return
    if shutil.which("nix-instantiate") is None:
        case.warn("nix-instantiate not found; rebuild dry-run skipped")
        case.report["dry_run"] = {"status": "skipped", "reason": "NO_NIX_INSTANTIATE"}
        return

    args = [
        "nix-instantiate",
        "--eval",
        "<nixpkgs/nixos>",
        "-A", "config.system.build.toplevel.drvPath",
        "-I", f"nixos-config={cfg_path}",
    ]
    code, output = _run_command(args, result, timeout=900)
    if code != 0:
        category, summary = _classify_dry_run_output(output)
        case.report["dry_run"] = {
            "status": "failed",
            "category": category,
            "summary": summary,
            "severity": _classify_severity(category, output),
            "signals": _dry_run_signals(output),
            "output_excerpt": output[:1200],
        }
        case.fail("rebuild dry-run failed: " + output)
    else:
        case.report["dry_run"] = {"status": "passed", "severity": "green"}


def _case_dirs(selected: str | None) -> list[Path]:
    if selected:
        return [CONFIGS_DIR / selected]
    if not CONFIGS_DIR.exists():
        return []
    return sorted(p for p in CONFIGS_DIR.iterdir() if p.is_dir())


def run_case(case_dir: Path, args: argparse.Namespace) -> CaseResult:
    case = CaseResult(case_dir.name)
    original = case_dir / "original"
    result = case_dir / "result"

    if not original.is_dir():
        case.fail("missing original directory")
        return case
    if not _has_payload_files(original):
        case.warn("original directory is empty; skipped")
        return case
    if not (original / "configuration.nix").exists() and not (original / "flake.nix").exists():
        case.fail("original needs configuration.nix or flake.nix")
        return case

    work_parent = Path(tempfile.mkdtemp(prefix=f"nico-test-{case.name}-"))
    work_dir = work_parent / "work"
    try:
        _copy_tree(original, work_dir)
        (_write_nico_roundtrip_old if args.old else _write_nico_roundtrip)(work_dir)

        if result.exists():
            shutil.rmtree(result)
        _copy_tree(work_dir, result)

        _compare_managed_fields(original, result, case,
                                co_faithful=_check_fidelity(original, result, case))
        _check_unmanaged_preserved(original, result, case)
        _check_no_lost_attributes(original, result, case)
        _check_no_duplicate_attributes(original, result, case)
        _check_comments_kept(original, result, case)

        if args.syntax:
            _syntax_check(result, case)
        if args.rebuild_dry_run:
            _rebuild_dry_run(result, case)
    except Exception as exc:  # noqa: BLE001 - test runner should report all case failures
        case.fail(f"runner error: {exc}")
    finally:
        if args.keep_work:
            case.info.append(f"work directory kept: {work_dir}")
        else:
            shutil.rmtree(work_parent, ignore_errors=True)

    return case


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run headless NiCo roundtrip tests.")
    parser.add_argument("--config", help="Only run one config from testing/configs/<name>.")
    parser.add_argument("--keep-work", action="store_true", help="Keep temporary work dirs.")
    parser.add_argument("--no-syntax", dest="syntax", action="store_false", help="Skip nix parse checks.")
    parser.add_argument("--rebuild-dry-run", action="store_true", help="Run nixos-rebuild dry-run.")
    parser.add_argument("--old", action="store_true",
                        help="Use the hand-built import of before 2026-11-21 (comparison only).")
    parser.add_argument("--report-json", default="testing/report-dry-run.json",
                        help="Write structured dry-run report to this JSON path.")
    parser.set_defaults(syntax=True)
    return parser.parse_args()


def _write_report(results: list[CaseResult], args: argparse.Namespace) -> None:
    if not args.rebuild_dry_run:
        return

    category_counts: dict[str, int] = {}
    severity_counts: dict[str, int] = {"green": 0, "yellow": 0, "red": 0}
    evaluated_cases = 0
    report_cases = []
    for result in results:
        dry_run = result.report.get("dry_run", {})
        host_results = dry_run.get("host_results", [])
        severity = _case_severity(result)
        dry_run["severity"] = severity

        entry = {
            "name": result.name,
            "ok": result.ok,
            "warnings": result.warnings,
            "errors": result.errors,
            "report": result.report,
        }
        report_cases.append(entry)
        if host_results:
            evaluated_cases += 1
        for host_result in host_results:
            category = host_result.get("category")
            if category:
                category_counts[category] = category_counts.get(category, 0) + 1
        category = dry_run.get("category")
        if category:
            category_counts[category] = category_counts.get(category, 0) + 1
        if severity in severity_counts:
            severity_counts[severity] += 1

    payload = {
        "summary": {
            "cases_total": len(results),
            "cases_failed": sum(1 for r in results if not r.ok),
            "cases_with_host_results": evaluated_cases,
            "severity_counts": severity_counts,
            "category_counts": category_counts,
        },
        "cases": report_cases,
    }

    report_path = (ROOT / args.report_json).resolve()
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def main() -> int:
    args = parse_args()
    cases = _case_dirs(args.config)
    if not cases:
        print("No test configs found.")
        return 1

    results = [run_case(case_dir, args) for case_dir in cases]

    for result in results:
        status = _case_severity(result).upper()
        print(f"[{status}] {result.name}")
        for message in result.info:
            print(f"  info: {message}")
        for message in result.warnings:
            print(f"  warning: {message}")
        for message in result.errors:
            print(f"  error: {message}")

    _write_report(results, args)
    if args.rebuild_dry_run:
        print(f"\nReport written: {args.report_json}")

    severities = [_case_severity(result) for result in results]
    green = sum(1 for severity in severities if severity == "green")
    yellow = sum(1 for severity in severities if severity == "yellow")
    red = sum(1 for severity in severities if severity == "red")
    print(f"\n{green} green, {yellow} yellow, {red} red")
    return 1 if red else 0


if __name__ == "__main__":
    raise SystemExit(main())
