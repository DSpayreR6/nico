#!/usr/bin/env python3
"""Das Hardware-Zwischenlager behält ältere Einträge (Revisionsbefund B27).

Zwei Fehler: `stash.json` wurde pro Lauf komplett ersetzt, frühere Dateien lagen
danach unerreichbar im Verzeichnis. Und eine Datei mit dem Urteil UNKNOWN
überschrieb die als MATCH gesicherte echte Datei desselben Hosts – das
Zwischenlager ist aber das Einzige, was NiCo nach einem Import zurückschreiben
kann.

Lauf: nix-shell shell.nix --run "python testing/routes/test_hardware_stash_merge.py"
"""
import os, pathlib, sys, tempfile
from types import SimpleNamespace

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

xdg = pathlib.Path(tempfile.mkdtemp(prefix="nico-xdg-"))
os.environ["XDG_CONFIG_HOME"] = str(xdg)
work = pathlib.Path(tempfile.mkdtemp(prefix="nico-work-"))

from nico import hardware as hw  # noqa: E402

fails = []
def check(label, cond, got=None):
    print(("  OK   " if cond else "  FAIL ") + label + ("" if cond or got is None else f"  (war: {got})"))
    if not cond: fails.append(label)


counter = 0
def stash(*files):
    """Run stash_hw_files on (host, verdict, content) triples."""
    global counter
    fakes = []
    for host, verdict, content in files:
        counter += 1
        path = work / f"{counter}.nix"
        path.write_text(content, encoding="utf-8")
        fakes.append(SimpleNamespace(path=path, host=host, verdict=verdict))
    hw.find_hw_files = lambda _d: fakes
    return hw.stash_hw_files("egal")


def listed() -> dict:
    return {e["host"]: e for e in hw.stashed_files()}


def content(host) -> str:
    return listed()[host]["path"].read_text(encoding="utf-8")


print("Liste wird zusammengeführt")
stash(("desktop", hw.MATCH, "desktop-1"), ("laptop", hw.MATCH, "laptop-1"))
check("beide gesichert", set(listed()) == {"desktop", "laptop"}, set(listed()))
stash(("desktop", hw.MATCH, "desktop-2"))
check("laptop bleibt auswählbar", "laptop" in listed(), set(listed()))
check("laptop-Inhalt unverändert", content("laptop") == "laptop-1")
check("desktop aktualisiert", content("desktop") == "desktop-2")
check("keine doppelten Einträge", len(hw.stashed_files()) == 2, len(hw.stashed_files()))
check("neuester Lauf zuerst", hw.stashed_files()[0]["host"] == "desktop")

print("UNKNOWN gegen MATCH")
returned = stash(("desktop", hw.UNKNOWN, "fremd"))
check("MATCH wird nicht überschrieben", content("desktop") == "desktop-2", content("desktop"))
check("Urteil bleibt MATCH", listed()["desktop"]["verdict"] == hw.MATCH)
check("übersprungene Datei wird nicht gemeldet", returned == [], returned)
check("Rest bleibt auswählbar", set(listed()) == {"desktop", "laptop"})

print("Andere Reihenfolgen")
stash(("neu", hw.UNKNOWN, "unklar-1"))
check("UNKNOWN wird gesichert, wenn nichts da ist", content("neu") == "unklar-1")
stash(("neu", hw.UNKNOWN, "unklar-2"))
check("UNKNOWN ersetzt UNKNOWN", content("neu") == "unklar-2")
stash(("neu", hw.MATCH, "echt"))
check("MATCH ersetzt UNKNOWN", content("neu") == "echt")
check("Urteil jetzt MATCH", listed()["neu"]["verdict"] == hw.MATCH)
stash(("neu", hw.MISMATCH, "falsch"))
check("MISMATCH wird nie gesichert", content("neu") == "echt")

print("Datei fehlt, obwohl der Eintrag MATCH sagt")
listed()["neu"]["path"].unlink()
stash(("neu", hw.UNKNOWN, "ersatz"))
check("UNKNOWN darf dann sichern", content("neu") == "ersatz")

print("Defekte Metadatei")
(hw._stash_dir() / hw._STASH_META).write_text("kein json", encoding="utf-8")
check("wird als leer gelesen", hw.stashed_files() == [])
stash(("desktop", hw.MATCH, "desktop-3"))
check("nächster Lauf funktioniert", content("desktop") == "desktop-3")

print()
if fails:
    print(f"{len(fails)} FEHLER:", *fails, sep="\n  ")
    sys.exit(1)
print("Alle Prüfungen bestanden.")
