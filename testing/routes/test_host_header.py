#!/usr/bin/env python3
"""Die Kopfzeile generierter Host-Dateien ist englisch (Revisionsbefund T14).

Dateitext ist englisch (vorgaben.txt), übersetzt wird nur in der Oberfläche.
Bestehende Dateien tragen noch die deutsche Zeile: Sie müssen gültig bleiben und
dürfen beim Einlesen keinen Rest hinterlassen.

Lauf: nix-shell shell.nix --run "python testing/routes/test_host_header.py"
"""
import pathlib, sys

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from nico import generator, importer

fails = []
def check(label, cond, got=None):
    print(("  OK   " if cond else "  FAIL ") + label + ("" if cond or got is None else f"  (war: {got})"))
    if not cond: fails.append(label)


OLD_LINE = "# Host-spezifische Konfiguration für nix-vm"
NEW_LINE = "# Host-specific configuration for nix-vm"

new = generator.generate_host_nix({}, "nix-vm")
check("neue Kopfzeile", NEW_LINE in new.split("\n"), new[:120])
check("keine deutsche Kopfzeile", "Host-spezifische" not in new)
check("neue Datei ist gültig", generator.check_version_hash(new) == "ok")
check("neue Kopfzeile bleibt beim Einlesen nicht übrig",
      "Host-specific" not in importer.build_rest_brix(new, {}))

body = new.replace(NEW_LINE, OLD_LINE)
lines = [l for l in body.split("\n") if not l.startswith("# nico-version:")]
old = generator._add_version_hash("\n".join(lines), ftype="co")
check("alte Datei bleibt gültig", generator.check_version_hash(old) == "ok")
check("alte Kopfzeile bleibt beim Einlesen nicht übrig",
      "Host-spezifische" not in importer.build_rest_brix(old, {}))

print()
if fails:
    print(f"{len(fails)} FEHLER:", *fails, sep="\n  ")
    sys.exit(1)
print("Alle Prüfungen bestanden.")
