#!/usr/bin/env python3
"""
NiCo Config Integrity Check

Vergleicht die aktuelle NixOS-Config semantisch gegen eine Referenz-ZIP.
Meldet Abweichungen (Werte geändert, Daten verschoben, Daten verloren).

Verwendung:
  nix-shell shell.nix --run "python testing/verify_config.py"
  nix-shell shell.nix --run "python testing/verify_config.py --accept"
  nix-shell shell.nix --run "python testing/verify_config.py --zip mybackup.zip"
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
import zipfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from nico import importer  # noqa: E402

DEFAULT_ZIP = "vergleich.zip"

# Dateien, die nicht verglichen werden (hardware-managed, kein Panel-Inhalt)
SKIP_PATTERNS = (
    "hardware-configuration.nix",
    ".bak",
    ".old",
    ".log",
)


def _should_skip(rel_path: str) -> bool:
    p = rel_path.lower()
    return any(pat in p for pat in SKIP_PATTERNS)


def _detect_type(content: str, filename: str) -> str:
    """Gibt 'flake', 'hm' oder 'co' zurück."""
    if filename == "flake.nix":
        return "flake"
    for line in content.splitlines()[:5]:
        if "nico-version:" in line:
            return "hm" if " hm" in line else "co"
    return "co"


def _parse(content: str, filetype: str) -> dict:
    if filetype == "hm":
        return importer.parse_home_config(content)
    if filetype == "flake":
        return importer.parse_flake_nix(content)
    return importer.parse_config(content)


def _collect(directory: Path) -> dict[str, str]:
    """Alle .nix-Dateien relativ zu directory, ohne übersprungene."""
    files: dict[str, str] = {}
    for p in sorted(directory.rglob("*.nix")):
        rel = str(p.relative_to(directory))
        if not _should_skip(rel):
            try:
                files[rel] = p.read_text(encoding="utf-8", errors="replace")
            except OSError:
                pass
    return files


def _normalize(val: Any) -> Any:
    """Sortiert Listen für stabilen Vergleich."""
    if isinstance(val, list):
        if not val:
            return val
        if all(isinstance(x, str) for x in val):
            return sorted(val)
        if all(isinstance(x, dict) for x in val):
            for key in ("attr", "name", "username"):
                if val and key in val[0]:
                    return sorted(val, key=lambda x: str(x.get(key, "")))
        return val
    return val


def _fmt(val: Any) -> str:
    if isinstance(val, list):
        if not val:
            return "[]"
        if isinstance(val[0], dict):
            return f"[{len(val)} Einträge]"
        short = [str(x) for x in val[:4]]
        suffix = ", …" if len(val) > 4 else ""
        return "[" + ", ".join(short) + suffix + "]"
    if isinstance(val, str) and len(val) > 80:
        return val[:77] + "…"
    return str(val)


def _diff(ref: dict, cur: dict) -> dict:
    """Liefert removed/changed/added Dicts."""
    removed, changed, added = {}, {}, {}
    all_keys = set(ref) | set(cur)
    for k in sorted(all_keys):
        if k.startswith("_"):
            continue
        rv = _normalize(ref.get(k))
        cv = _normalize(cur.get(k))
        if k in ref and k not in cur:
            removed[k] = rv
        elif k not in ref and k in cur:
            added[k] = cv
        elif rv != cv:
            changed[k] = {"von": rv, "nach": cv}
    return {"removed": removed, "changed": changed, "added": added}


def _is_empty_val(v: Any) -> bool:
    return v in (None, False, "", [], {})


def main() -> int:
    ap = argparse.ArgumentParser(description="NiCo Config Integrity Check")
    ap.add_argument("--accept", action="store_true",
                    help="ZIP mit aktuellem Stand aktualisieren")
    ap.add_argument("--zip", default=DEFAULT_ZIP,
                    help=f"Referenz-ZIP-Dateiname (Standard: {DEFAULT_ZIP})")
    args = ap.parse_args()

    # Settings lesen (XDG-Pfad inkl. einmaliger Migration der alten nico-settings.json)
    from nico import config_manager
    config_dir = Path(
        config_manager.get_app_settings().get("nixos_config_dir", "")
    ).expanduser()
    if not config_dir.is_dir():
        print(f"Fehler: Config-Verzeichnis nicht gefunden: {config_dir}")
        return 1

    zip_path = config_dir / args.zip

    # Erster Lauf: ZIP anlegen
    if not zip_path.exists():
        if not args.accept:
            print(f"Referenz-ZIP nicht gefunden: {zip_path}")
            print("Erstelle mit --accept eine initiale Referenz.")
            return 1
        cur_files = _collect(config_dir)
        _write_zip(zip_path, cur_files)
        print(f"✅ Initiale Referenz-ZIP erstellt: {zip_path}")
        print(f"   {len(cur_files)} .nix-Dateien gespeichert.")
        return 0

    # ZIP einlesen
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir) / "ref"
        tmp.mkdir()
        with zipfile.ZipFile(zip_path, "r") as zf:
            for info in zf.infolist():
                if info.filename.endswith(".nix") and not _should_skip(info.filename):
                    zf.extract(info, tmp)

        ref_files = _collect(tmp)
        cur_files = _collect(config_dir)

    # Parsen
    ref_parsed: dict[str, dict] = {}
    cur_parsed: dict[str, dict] = {}
    all_files = sorted(set(ref_files) | set(cur_files))

    for fn in all_files:
        if fn in ref_files:
            ft = _detect_type(ref_files[fn], Path(fn).name)
            ref_parsed[fn] = _parse(ref_files[fn], ft)
        if fn in cur_files:
            ft = _detect_type(cur_files[fn], Path(fn).name)
            cur_parsed[fn] = _parse(cur_files[fn], ft)

    # ── Ausgabe ───────────────────────────────────────────────────────────────
    W = 62
    print("=" * W)
    print("NiCo Config Integrity Check")
    print("=" * W)
    print(f"Referenz : {zip_path}")
    print(f"Aktuell  : {config_dir}")
    print()

    file_reports: list[str] = []
    has_diff = False

    for fn in all_files:
        if fn in ref_files and fn not in cur_files:
            file_reports.append(f"  ⚠️  DATEI FEHLT  : {fn}")
            has_diff = True
            continue
        if fn not in ref_files and fn in cur_files:
            file_reports.append(f"  ℹ️  NEUE DATEI   : {fn}")
            has_diff = True
            continue

        diff = _diff(ref_parsed.get(fn, {}), cur_parsed.get(fn, {}))
        lines: list[str] = []
        for k, v in diff["removed"].items():
            lines.append(f"    ❌ ENTFERNT  : {k} = {_fmt(v)}")
        for k, d in diff["changed"].items():
            lines.append(f"    ↔  GEÄNDERT  : {k}")
            lines.append(f"       von  : {_fmt(d['von'])}")
            lines.append(f"       nach : {_fmt(d['nach'])}")
        for k, v in diff["added"].items():
            lines.append(f"    ✅ HINZUGEF. : {k} = {_fmt(v)}")
        if lines:
            file_reports.append(f"\n  {fn}:")
            file_reports.extend(lines)
            has_diff = True

    # Globale Bilanz
    ref_agg: dict[str, Any] = {}
    cur_agg: dict[str, Any] = {}
    # Merken, in welcher Datei ein Wert zuerst auftaucht
    ref_origin: dict[str, str] = {}
    cur_origin: dict[str, str] = {}

    for fn in all_files:
        for k, v in ref_parsed.get(fn, {}).items():
            if not k.startswith("_") and not _is_empty_val(v):
                if k not in ref_agg:
                    ref_agg[k] = v
                    ref_origin[k] = fn
        for k, v in cur_parsed.get(fn, {}).items():
            if not k.startswith("_") and not _is_empty_val(v):
                if k not in cur_agg:
                    cur_agg[k] = v
                    cur_origin[k] = fn

    globally_lost = {k: v for k, v in ref_agg.items()
                     if k not in cur_agg}
    globally_moved = {k for k in ref_agg
                      if k in cur_agg and ref_origin.get(k) != cur_origin.get(k)}

    # ── Per-Datei-Report ──────────────────────────────────────────────────────
    print("── Per-Datei-Vergleich " + "─" * (W - 22))
    if file_reports:
        for r in file_reports:
            print(r)
    else:
        print("  Alle Dateien unverändert ✓")

    # ── Verschobene Daten ─────────────────────────────────────────────────────
    if globally_moved:
        print()
        print("── Verschobene Daten " + "─" * (W - 20))
        for k in sorted(globally_moved):
            print(f"  ↕  {k}")
            print(f"     war  : {ref_origin[k]}")
            print(f"     jetzt: {cur_origin[k]}")

    # ── Kritisch: Verloren ────────────────────────────────────────────────────
    if globally_lost:
        print()
        print("── 🚨 KRITISCH: Folgende Daten nicht mehr vorhanden " + "─" * (W - 51))
        for k, v in globally_lost.items():
            print(f"  ❌ {k} = {_fmt(v)}")
            print(f"     war in: {ref_origin.get(k, '?')}")

    # ── Ergebnis ──────────────────────────────────────────────────────────────
    print()
    print("── Ergebnis " + "─" * (W - 11))
    if globally_lost:
        print("  🚨 Kritische Abweichungen! Daten verloren.")
        print("     Bitte prüfen. Mit --accept wird die ZIP trotzdem aktualisiert.")
    elif has_diff:
        print("  ✅ Keine Daten verloren (alles noch vorhanden, ggf. verschoben).")
        print("  ℹ️  Abweichungen vorhanden – siehe oben.")
    else:
        print("  ✅ Keine Abweichungen. Config unverändert.")

    if args.accept:
        print()
        _write_zip(zip_path, cur_files)
        print(f"  ✅ Referenz-ZIP aktualisiert: {zip_path}")
        print(f"     {len(cur_files)} .nix-Dateien gespeichert.")

    return 2 if globally_lost else (0 if not has_diff else 1)


def _write_zip(zip_path: Path, nix_files: dict[str, str]) -> None:
    """Schreibt .nix-Dateien in die ZIP; andere ZIP-Inhalte bleiben erhalten."""
    existing: dict[str, bytes] = {}
    if zip_path.exists():
        with zipfile.ZipFile(zip_path, "r") as zf:
            for info in zf.infolist():
                if not info.filename.endswith(".nix"):
                    existing[info.filename] = zf.read(info.filename)

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        # Nicht-.nix-Inhalte erhalten (außer dem riesigen Log)
        for name, data in existing.items():
            if not name.endswith(".log"):
                zf.writestr(name, data)
        # Aktuelle .nix-Dateien schreiben
        for rel, content in sorted(nix_files.items()):
            zf.writestr(rel, content)


if __name__ == "__main__":
    sys.exit(main())
