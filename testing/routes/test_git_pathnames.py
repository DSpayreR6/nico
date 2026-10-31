#!/usr/bin/env python3
"""Dateinamen, die git escaped zurückgibt – Sicherungspunkt, Größengrenze, Rücksicherung.

Aus der Revision vom 2026-10-31. `_run` rief git ohne `core.quotePath=false`
auf, und git escaped jeden Namen außerhalb von ASCII und schreibt ihn in
Anführungszeichen: `"drucker-b\\303\\274ro.nix"`. Wörtlich gelesen passt so ein
Name auf keine Datei – git tut dann nichts, und der Aufrufer meldet Erfolg für
eine Datei, die er nie angefasst hat. Drei Folgen, alle gemessen:

  1. Eine neue .nix-Datei mit Umlaut landete in keinem Sicherungspunkt.
     `auto_commit` gab (True, '', []) zurück – Erfolg und keine übersprungene
     Datei, weil `status -uno` die ungetrackte Datei gar nicht zeigt.
  2. Die 50-MB-Grenze sah eine übergroße Datei nicht und ließ ihren Blob in den
     Commit. Das ist die Signatur des Vorfalls vom 2026-09-12, bei dem ein
     2,1-GB-Log jeden Push blockierte.
  3. Eine Rücksicherung ließ solche Dateien auf dem neueren Stand stehen,
     während alles andere zurückging, und meldete trotzdem Erfolg – genau der
     Mischzustand, den `rollback` laut eigenem Kommentar verhindern soll.

Geprüft wird mit vier Namensformen, weil sie unterschiedlich escaped werden:
Umlaut und Leerzeichen gehen über `core.quotePath=false`, Tabulator nur über
`-z` (Steuerzeichen escaped git immer), Apostroph kommt unverändert an.

Lauf: nix-shell shell.nix --run "python testing/routes/test_git_pathnames.py"
"""
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

xdg = Path(tempfile.mkdtemp(prefix="nico-xdg-"))
os.environ["XDG_CONFIG_HOME"] = str(xdg)

from nico import git_manager as gm  # noqa: E402

NAMES = ["drucker-büro.nix", "mein modul.nix", "tabs\tdrin.nix", "an'fuehrung.nix"]

fails = []


def check(label, cond, got=None):
    print(("  OK   " if cond else "  FAIL ") + label
          + ("" if cond or got is None else f"  (war: {got})"))
    if not cond:
        fails.append(label)


def new_repo(with_commit=True):
    d = tempfile.mkdtemp(prefix="nico-git-")
    for args in (["init", "-q"], ["config", "user.email", "t@t"],
                 ["config", "user.name", "t"]):
        subprocess.run(["git"] + args, cwd=d, check=True)
    if with_commit:
        Path(d, "configuration.nix").write_text("{ }\n", encoding="utf-8")
        subprocess.run(["git", "add", "-A"], cwd=d, check=True)
        subprocess.run(["git", "commit", "-qm", "init"], cwd=d, check=True)
    return d


def tracked(d):
    """Die getrackten Dateien, NUL-getrennt gelesen – sonst führt die Prüfung
    sich selbst an der Nase herum und liest die escapte Form."""
    out = subprocess.run(["git", "ls-files", "-z"], cwd=d,
                         capture_output=True, text=True).stdout
    return [p for p in out.split("\0") if p]


# ── 1. Neue Config-Dateien kommen in den Sicherungspunkt ─────────────────────
print("\n1. Sicherungspunkt mit escapten Namen")
d = new_repo()
for n in NAMES:
    Path(d, n).write_text("{ }\n", encoding="utf-8")

ok, msg, skipped = gm.auto_commit(d, label="Test")
check("auto_commit erfolgreich", ok, (ok, msg))
check("meldet einen Commit statt stillem Nichtstun", bool(msg), repr(msg))
in_commit = tracked(d)
for n in NAMES:
    check(f"im Sicherungspunkt: {n!r}", n in in_commit)
check("nichts fälschlich übersprungen", skipped == [], skipped)

# ── 2. Die Größengrenze sieht sie auch ───────────────────────────────────────
print("\n2. 50-MB-Grenze")
d = new_repo()
big = Path(d, "größe.nix")
big.write_text("{ }\n", encoding="utf-8")
gm.auto_commit(d)                      # erst klein und getrackt
big.write_bytes(b"x" * (gm.MAX_STAGE_BYTES + 1024))
ok, msg, skipped = gm.auto_commit(d, label="Test")
check("übergroße Datei wird gemeldet", "größe.nix" in skipped, skipped)
size = subprocess.run(["git", "-c", "core.quotePath=false", "cat-file", "-s",
                       "HEAD:größe.nix"], cwd=d, capture_output=True, text=True)
check("ihr Blob ist nicht im Commit gewachsen",
      size.stdout.strip() == str(len("{ }\n")), size.stdout.strip())

# ── 3. Rücksicherung lässt keine Datei zurück ────────────────────────────────
print("\n3. Rücksicherung")
d = new_repo(with_commit=False)
files = {n: Path(d, n) for n in ["configuration.nix"] + NAMES}
for p in files.values():
    p.write_text("{ alt = true; }\n", encoding="utf-8")
subprocess.run(["git", "add", "-A"], cwd=d, check=True)
subprocess.run(["git", "commit", "-qm", "A"], cwd=d, check=True)
good = subprocess.run(["git", "rev-parse", "HEAD"], cwd=d,
                      capture_output=True, text=True).stdout.strip()[:7]
for p in files.values():
    p.write_text("{ neu = true; }\n", encoding="utf-8")
subprocess.run(["git", "commit", "-aqm", "B"], cwd=d, check=True)

ok, msg = gm.rollback(d, good)
check("rollback erfolgreich", ok, (ok, msg))
for n, p in files.items():
    check(f"zurückgesetzt: {n!r}", "alt" in p.read_text(encoding="utf-8"),
          p.read_text(encoding="utf-8").strip())

# ── 4. Eine halbe Rücksicherung gilt nicht als Erfolg ────────────────────────
print("\n4. Teilweise Rücksicherung wird nicht als Erfolg gemeldet")
_orig_run = gm._run


def _fail_one(args, cwd, timeout=10, remote=False, input=None):
    # Ein checkout einer bestimmten Datei scheitert – der Zustand, den der
    # Escaping-Fehler erzeugt hat, hier künstlich hergestellt.
    if args[:1] == ["checkout"] and args[-1] == "drucker-büro.nix":
        return 1, "error: pathspec did not match"
    return _orig_run(args, cwd, timeout=timeout, remote=remote, input=input)


d = new_repo(with_commit=False)
files_2 = {n: Path(d, n) for n in ["configuration.nix", "drucker-büro.nix"]}
for p in files_2.values():
    p.write_text("{ alt = true; }\n", encoding="utf-8")
subprocess.run(["git", "add", "-A"], cwd=d, check=True)
subprocess.run(["git", "commit", "-qm", "A"], cwd=d, check=True)
good = subprocess.run(["git", "rev-parse", "HEAD"], cwd=d,
                      capture_output=True, text=True).stdout.strip()[:7]
for p in files_2.values():
    p.write_text("{ neu = true; }\n", encoding="utf-8")
subprocess.run(["git", "commit", "-aqm", "B"], cwd=d, check=True)

gm._run = _fail_one
try:
    ok, msg = gm.rollback(d, good)
finally:
    gm._run = _orig_run
check("meldet keinen Erfolg", not ok, (ok, msg))
check("nennt die Datei, die stehengeblieben ist",
      "drucker-büro.nix" in msg, msg)

# ── 5. Fremddatei-Liste trägt echte Namen ────────────────────────────────────
print("\n5. Fremddatei-Liste (Erlaubnisliste für add und gitignore)")
d = new_repo()
Path(d, "nötig.log").write_text("x\n", encoding="utf-8")
foreign = gm.list_untracked_foreign(d)
check("Name unescaped in der Liste", "nötig.log" in foreign, foreign)
# Die Liste ist die Erlaubnisliste: steht der Name dort escaped, wird er
# angeboten, angenommen – und git findet dann keine Datei dazu.
ok, msg = gm.include_foreign_files(d, ["nötig.log"])
staged = subprocess.run(["git", "diff", "--cached", "--name-only", "-z"],
                        cwd=d, capture_output=True, text=True).stdout
check("lässt sich damit aufnehmen", ok, (ok, msg))
check("ist danach wirklich vorgemerkt",
      "nötig.log" in [p for p in staged.split("\0") if p], staged)

print()
if fails:
    print("FEHLGESCHLAGEN:", len(fails))
    for f in fails:
        print("  -", f)
    sys.exit(1)
print("Alle Prüfungen bestanden.")
