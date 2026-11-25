#!/usr/bin/env python3
"""Validator-Regel etc_nixos_symlink, 2026-11-25.

Martin: Der Symlink /etc/nixos muss auf das richtige Verzeichnis zeigen.
NiCos eigener Rebuild übergibt den Pfad ausdrücklich und ist nicht
betroffen; ein `nixos-rebuild switch` im Terminal würde aber eine andere
Config bauen. Darum eine Warnung, kein Fehler – und nur mit der Freigabe
„Diesen Rechner lesen → Validator“, weil sie außerhalb der Config liest.

Geprüft wird mit einem nachgebauten /etc/nixos (validator.ETC_NIXOS):
  1. Regel: Warnung, host_local, liest mit der Validator-Freigabe
  2. kein Symlink (normales Verzeichnis, fehlt): keine Meldung
  3. Symlink auf diese Config: keine Meldung
  4. Symlink auf ein anderes Verzeichnis: eine Warnung mit Ziel und Config
  5. Texte in allen Sprachen vorhanden

Lauf: nix-shell shell.nix --run "python testing/routes/test_etc_nixos_symlink.py"
"""
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from nico import hostread, validator  # noqa: E402

fails = []


def check(label, cond, got=None):
    print(("  OK   " if cond else "  FAIL ") + label
          + ("" if cond or got is None else f"  (war: {got})"))
    if not cond:
        fails.append(label)


base = Path(tempfile.mkdtemp(prefix="nico-etc-"))
cfg = base / "config"
cfg.mkdir()
other = base / "andere"
other.mkdir()
link = base / "etc-nixos"


def run():
    validator.ETC_NIXOS = link
    return validator._rule_etc_nixos_symlink(str(cfg), {}, False)


print("1. Regel")
r = validator.RULE_MAP["etc_nixos_symlink"]
check("Warnung", r.severity == "warning", r.severity)
check("liest diesen Rechner, nur mit Validator-Freigabe",
      r.host_local and r.reads == hostread.VALIDATOR, (r.host_local, r.reads))

print("2. Kein Symlink")
check("/etc/nixos fehlt: keine Meldung", run() == [])
link.mkdir()
check("normales Verzeichnis: keine Meldung", run() == [])
link.rmdir()

print("3. Symlink auf diese Config")
link.symlink_to(cfg)
check("keine Meldung", run() == [])
link.unlink()

print("4. Symlink auf ein anderes Verzeichnis")
link.symlink_to(other)
found = run()
check("eine Warnung", len(found) == 1 and found[0].severity == "warning", found)
check("nennt Ziel und Config", found and found[0].params == [str(other.resolve()), str(cfg.resolve())],
      found and found[0].params)
check("Texte über Schlüssel", found and found[0].message_key == "validator.f.etc_nixos_symlink"
      and found[0].detail_key == "validator.d.etc_nixos_symlink")
link.unlink()

print("5. Texte")
for lang in ("de", "en", "es", "fr", "ja", "ru", "zh"):
    data = json.loads((ROOT / "nico" / "static" / "lang" / f"{lang}.json").read_text(encoding="utf-8"))
    keys = ("validator.rule.etc_nixos_symlink.label", "validator.rule.etc_nixos_symlink.desc",
            "validator.f.etc_nixos_symlink", "validator.d.etc_nixos_symlink")
    check(f"{lang}: alle Texte", all(data.get(k) for k in keys))
    check(f"{lang}: Meldung mit beiden Platzhaltern",
          "{0}" in data.get("validator.f.etc_nixos_symlink", "")
          and "{1}" in data.get("validator.f.etc_nixos_symlink", ""))

if fails:
    print(f"\nFEHLGESCHLAGEN: {len(fails)}")
    sys.exit(1)
print("\nAlle Prüfungen bestanden.")
