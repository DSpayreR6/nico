#!/usr/bin/env python3
"""Die „läuft bereits“-Seite warnt, wenn eine andere Version läuft.

Gefunden in der VM (2026-11-08): Ein Start über `nix run` endete auf dieser
Seite, gearbeitet wurde danach mit der alten, schon laufenden Version.

Lauf: nix-shell shell.nix --run "python testing/routes/test_already_running_note.py"
"""
import pathlib, sys

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from nico import main

fails = []
def check(label, cond, got=None):
    print(("  OK   " if cond else "  FAIL ") + label + ("" if cond or got is None else f"  (war: {got})"))
    if not cond: fails.append(label)


NEW = ("0.16.0", "df69bf2")
note = main._version_note(("0.14.0-dev", "3162d8b"), NEW, "de")
check("nennt laufende Version", "0.14.0-dev (3162d8b)" in note, note)
check("nennt gestartete Version", "0.16.0 (df69bf2)" in note, note)
check("Text ist übersetzt", "gestartet wurde" in note and "running.versionDiffers" not in note, note)

check("gleiche Version: nichts", main._version_note(NEW, NEW, "de") == "")
check("+ am Ende zählt nicht", main._version_note(("0.16.0", "df69bf2+"), NEW, "de") == "")
check("gleiche Version, anderer Commit", main._version_note(("0.16.0", "aaaaaaa"), NEW, "de") != "")
check("alte Instanz ohne Angabe", "? (?)" in main._version_note(("", ""), NEW, "de"))
check("eigene Version unbekannt: nichts", main._version_note(("0.14.0", "x"), ("", ""), "de") == "")

for lang in ("de", "en", "es", "fr", "ja", "ru", "zh"):
    n = main._version_note(("1", "a"), ("2", "b"), lang)
    check(f"{lang}: beide Platzhalter ersetzt", "1 (a)" in n and "2 (b)" in n and "{" not in n, n)
check("unbekannte Sprache fällt auf Englisch zurück",
      "is running" in main._version_note(("1", "a"), ("2", "b"), "xx"))
check("Sonderzeichen werden maskiert",
      "<b>" not in main._version_note(("<b>", "a"), ("2", "b"), "en"))

print()
if fails:
    print(f"{len(fails)} FEHLER:", *fails, sep="\n  ")
    sys.exit(1)
print("Alle Prüfungen bestanden.")
