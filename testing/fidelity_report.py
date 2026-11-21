#!/usr/bin/env python3
"""Gegenprobe, Phase 1: Was ändert NiCo beim Einlesen und Neuschreiben?

Geht für jede Config den Weg des Imports (wie testing/run.py) für
`configuration.nix` und den Weg des Host-Speicherns für
`{hosts_dir}/<host>/default.nix`, und vergleicht Original und Ergebnis mit
nico/fidelity.py. Ändert nichts: jede Config wird vorher in ein temporäres
Verzeichnis kopiert (nur .nix und config.json).

    python testing/fidelity_report.py                 die 18 Roundtrip-Configs
    python testing/fidelity_report.py PFAD [PFAD …]   eigene Config-Verzeichnisse
    … --detail                                        jede Abweichung einzeln

Ausgabe: Abweichungen je Kategorie, dazu die häufigsten Pfade. Grundlage für
die Gleichheitsregeln und für die Liste dessen, was der Generator immer
schreibt (manifest „Gegenprobe", 2026-11-19).
"""
import json, os, shutil, sys, tempfile
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ["XDG_CONFIG_HOME"] = tempfile.mkdtemp(prefix="nico-xdg-")

from nico import config_manager, fidelity, generator, importer  # noqa: E402
from nico.brix import brix_content_to_bricks, extract_brick_blocks, strip_brick_blocks  # noqa: E402
from nico.core import keep_unmodelled  # noqa: E402


def copy_config(src: Path) -> Path:
    dst = Path(tempfile.mkdtemp(prefix="nico-fid-"))
    for f in src.rglob("*"):
        rel = f.relative_to(src)
        if any(p.startswith(".") for p in rel.parts) or not f.is_file():
            continue
        if f.suffix == ".nix" or rel.as_posix() == "config.json":
            (dst / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(f, dst / rel)
    return dst


def rewrite_co(cfg: Path) -> "str | None":
    """configuration.nix as the import writes it (core.apply_import_result
    followed by config_manager.save_config)."""
    path = cfg / "configuration.nix"
    if not path.exists():
        return None
    content = path.read_text(encoding="utf-8")
    if importer.config_unmappable(content):
        return None
    data = config_manager.load_config(str(cfg))
    clean = strip_brick_blocks(content)
    recognized = importer.parse_config(clean)
    rest = importer.build_rest_brix(clean, recognized)
    blocks = extract_brick_blocks(content)
    if rest.strip():
        blocks.update(brix_content_to_bricks(rest, section="HEAD", existing_blocks=blocks))
    data.update(recognized)
    data["brick_blocks"] = blocks
    importer.rescue_into_bricks(data, content)
    data["_flake_mode"] = (cfg / "flake.nix").exists()
    if (cfg / "hardware-configuration.nix").exists():
        data["hardware_config"] = True
    try:
        data = fidelity.settle(content, data, generator.generate_configuration_nix)
    except fidelity.NotFaithful as e:
        print(f"   bleibt unverändert (NotFaithful): {cfg.name}/configuration.nix: {e}")
        return None
    data["brick_blocks"] = keep_unmodelled(
        content, data.get("brick_blocks"),
        render=lambda b: generator.generate_configuration_nix({**data, "brick_blocks": b}))
    return generator.generate_configuration_nix(data)


def rewrite_host(cfg: Path, host_dir: Path) -> "str | None":
    """default.nix as a host save writes it (load_host_config + save_host_config)."""
    path = host_dir / "default.nix"
    content = path.read_text(encoding="utf-8")
    if importer.config_unmappable(content):
        return None
    data = config_manager.load_host_config(str(cfg), host_dir.name) or {}
    hw = (host_dir / "hardware-configuration.nix").is_file()
    data["brick_blocks"] = keep_unmodelled(
        content, data.get("brick_blocks"),
        render=lambda b: generator.generate_host_nix({**data, "brick_blocks": b}, host_dir.name, hw))
    try:
        data = fidelity.settle(content, data,
                               lambda d: generator.generate_host_nix(d, host_dir.name, hw))
    except fidelity.NotFaithful as e:
        print(f"   bleibt unverändert (NotFaithful): {host_dir.name}/default.nix: {e}")
        return None
    return generator.generate_host_nix(data, host_dir.name, hw)


def rewrite_hm(path: Path) -> "str | None":
    """A Home Manager file as opening or restructuring writes it."""
    from nico.core import normalize_hm_content
    content = path.read_text(encoding="utf-8")
    if importer.config_unmappable(content):
        return None
    out, _ = normalize_hm_content(content)
    return None if out == content else out      # unchanged = left alone


def files_of(cfg: Path):
    yield "configuration.nix", lambda: rewrite_co(cfg)
    settings = {}
    try:
        settings = json.loads((cfg / "config.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pass
    hm_dir = cfg / ((settings.get("hm_dir") or "home").strip() or "home")
    hm_files = [cfg / "home.nix"] + (sorted(hm_dir.glob("*.nix")) if hm_dir.is_dir() else [])
    for f in hm_files:
        if f.is_file():
            yield f.relative_to(cfg).as_posix(), (lambda f=f: rewrite_hm(f))
    hosts = cfg / ((settings.get("hosts_dir") or "hosts").strip() or "hosts")
    if hosts.is_dir():
        for h in sorted(hosts.iterdir()):
            if (h / "default.nix").is_file():
                yield f"{hosts.name}/{h.name}/default.nix", (lambda h=h: rewrite_host(cfg, h))


def main(argv):
    detail = "--detail" in argv
    dirs = [Path(a).expanduser() for a in argv if not a.startswith("--")]
    if not dirs:
        dirs = sorted(p / "original" for p in (ROOT / "testing" / "configs").iterdir()
                      if (p / "original").is_dir())
    totals, paths = Counter(), {k: Counter() for k in ("changed", "missing", "added", "reordered")}
    checked = 0
    for src in dirs:
        work = copy_config(src)
        try:
            for rel, rewrite in files_of(work):
                original = (work / rel)
                if not original.exists():
                    continue
                before = original.read_text(encoding="utf-8")
                try:
                    after = rewrite()
                except Exception as e:                     # report, keep measuring
                    print(f"!! {src.parent.name if src.name == 'original' else src}/{rel}: {e!r}")
                    continue
                if after is None:
                    continue
                checked += 1
                d = fidelity.compare(before, after)
                name = src.parent.name if src.name == "original" else str(src)
                if d and detail:
                    print(f"\n## {name}/{rel}")
                for kind in ("changed", "missing", "added", "reordered"):
                    for entry in getattr(d, kind):
                        totals[kind] += 1
                        paths[kind][entry[0]] += 1
                        if detail:
                            print(f"   {kind:9} {entry[0]}  " +
                                  "  →  ".join(e[:70] for e in entry[1:]))
        finally:
            shutil.rmtree(work, ignore_errors=True)

    print(f"\n{checked} Dateien geprüft")
    for kind in ("changed", "missing", "added", "reordered"):
        print(f"\n{kind}: {totals[kind]}")
        for p, n in paths[kind].most_common(25):
            print(f"   {n:3}  {p}")


if __name__ == "__main__":
    main(sys.argv[1:])
