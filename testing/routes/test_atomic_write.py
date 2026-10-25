#!/usr/bin/env python3
"""Atomares Schreiben der Config-Dateien – ohne Browser.

Hintergrund (2026-10-25): `Path.write_text` kuerzt die Datei beim Oeffnen auf
0 Bytes und schreibt erst danach. Wer in diesem Fenster hart resettet – am
2026-10-25 zweimal passiert – hat danach eine abgeschnittene
`configuration.nix` oder `flake.nix`. Die ist nicht nur um die letzte Aenderung
aermer, sie laesst sich nicht mehr parsen: dann baut gar nichts mehr, auch kein
Rueckweg. Fuer die JSON-Settings war das Muster schon da, die .nix-Dateien
waren nie Teil davon.

Geprueft wird, dass nach einem Abbruch nie eine halbe Datei zurueckbleibt, und
die zwei Fallen, die der Umbau mitbringt: Dateirechte (ein `os.replace` nimmt
die Rechte der Temp-Datei, 0600) und Symlinks (ein `os.replace` ersetzt den
Link selbst, `write_text` schrieb durch ihn hindurch).

Lauf: nix-shell shell.nix --run "python testing/routes/test_atomic_write.py"
"""
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from nico import atomic  # noqa: E402

fails: list[str] = []


def check(label, cond, got=None):
    print(("  OK   " if cond else "  FAIL ") + label
          + ("" if cond or got is None else f"  (war: {got})"))
    if not cond:
        fails.append(label)


def tmpdir() -> Path:
    return Path(tempfile.mkdtemp(prefix="nico-atomic-"))


def leftovers(d: Path) -> list[str]:
    return [p.name for p in d.iterdir() if p.name.endswith(".tmp")]


# ── 1 Grundverhalten ─────────────────────────────────────────────────────────

print("1. Schreiben und Ersetzen")

d = tmpdir()
f = d / "configuration.nix"
atomic.write_text(f, "{ }\n")
check("neue Datei angelegt", f.read_text(encoding="utf-8") == "{ }\n")

atomic.write_text(f, "{ neu = true; }\n")
check("vorhandene Datei ersetzt",
      f.read_text(encoding="utf-8") == "{ neu = true; }\n")
check("keine Temp-Datei zurueckgelassen", leftovers(d) == [], leftovers(d))

atomic.write_text(f, "")
check("leerer Inhalt moeglich", f.read_text(encoding="utf-8") == "")

atomic.write_bytes(d / "flake.lock", b'{"nodes":{}}')
check("write_bytes schreibt roh",
      (d / "flake.lock").read_bytes() == b'{"nodes":{}}')

# Umlaute: die Sprachdateien und Kommentare sind voll davon.
atomic.write_text(f, "# Größe: 5 µm – ok\n")
check("UTF-8 bleibt erhalten",
      f.read_text(encoding="utf-8") == "# Größe: 5 µm – ok\n")


# ── 2 Dateirechte ────────────────────────────────────────────────────────────

print("2. Dateirechte")

d = tmpdir()
f = d / "configuration.nix"
f.write_text("alt\n", encoding="utf-8")
os.chmod(f, 0o755)
atomic.write_text(f, "neu\n")
check("vorhandene Rechte bleiben (0755)",
      oct(f.stat().st_mode & 0o7777) == "0o755",
      oct(f.stat().st_mode & 0o7777))

f2 = d / "neu.nix"
atomic.write_text(f2, "x\n")
check("neue Datei wird 0644, nicht 0600",
      oct(f2.stat().st_mode & 0o7777) == "0o644",
      oct(f2.stat().st_mode & 0o7777))

f3 = d / "geheim.nix"
f3.write_text("x\n", encoding="utf-8")
os.chmod(f3, 0o600)
atomic.write_text(f3, "y\n")
check("enge Rechte bleiben eng (0600)",
      oct(f3.stat().st_mode & 0o7777) == "0o600",
      oct(f3.stat().st_mode & 0o7777))


# ── 3 Symlinks ───────────────────────────────────────────────────────────────

print("3. Symlinks")

d = tmpdir()
real = d / "repo" / "configuration.nix"
real.parent.mkdir()
real.write_text("im Repo\n", encoding="utf-8")
link = d / "configuration.nix"
link.symlink_to(real)

atomic.write_text(link, "durch den Link\n")
check("Link bleibt ein Link", link.is_symlink())
check("Ziel im Repo wurde geschrieben",
      real.read_text(encoding="utf-8") == "durch den Link\n",
      real.read_text(encoding="utf-8"))
check("keine Temp-Datei im Link-Verzeichnis", leftovers(d) == [], leftovers(d))
check("keine Temp-Datei im Zielverzeichnis",
      leftovers(real.parent) == [], leftovers(real.parent))


# ── 4 Abbruch mitten im Schreiben ────────────────────────────────────────────

print("4. Abbruch")

d = tmpdir()
f = d / "flake.nix"
f.write_text("alter Stand\n", encoding="utf-8")
os.chmod(f, 0o644)

# Der Umbenennen-Schritt ist der einzige Moment, in dem die Zieldatei
# angefasst wird. Schlaegt alles davor fehl, muss der alte Stand unberuehrt
# dastehen – genau das konnte write_text nicht leisten.
real_replace = os.replace


def boom(src, dst):
    raise OSError("Abbruch mitten im Schreiben")


os.replace = boom
try:
    try:
        atomic.write_text(f, "neuer Stand, kommt nie an\n")
        raised = False
    except OSError:
        raised = True
finally:
    os.replace = real_replace

check("Fehler wird weitergegeben", raised)
check("alter Stand vollstaendig erhalten",
      f.read_text(encoding="utf-8") == "alter Stand\n",
      f.read_text(encoding="utf-8"))
check("Fragment wurde aufgeraeumt", leftovers(d) == [], leftovers(d))

# Und der Vergleich, warum es den Umbau gibt: write_text kuerzt zuerst.
g = d / "alt_verhalten.nix"
g.write_text("alter Stand\n", encoding="utf-8")
fh = open(g, "w", encoding="utf-8")   # genau das macht write_text zuerst
check("write_text hinterlaesst beim Oeffnen eine leere Datei",
      g.read_text(encoding="utf-8") == "", repr(g.read_text(encoding="utf-8")))
fh.close()


# ── 5 Die Aufrufstellen sind umgestellt ──────────────────────────────────────

print("5. Keine direkten Schreibvorgaenge mehr")

direct = []
for py in sorted((ROOT / "nico").rglob("*.py")):
    if py.name == "atomic.py":
        continue
    for i, line in enumerate(py.read_text(encoding="utf-8").splitlines(), 1):
        if (".write_text(" in line or ".write_bytes(" in line) \
                and "atomic.write" not in line:
            direct.append(f"{py.relative_to(ROOT)}:{i}")
check("kein .write_text/.write_bytes ausserhalb von atomic.py",
      direct == [], direct)


# ── Ergebnis ─────────────────────────────────────────────────────────────────

print()
if fails:
    print(f"FEHLGESCHLAGEN: {len(fails)}")
    for x in fails:
        print("  -", x)
    sys.exit(1)
print("Alle Prüfungen bestanden.")
