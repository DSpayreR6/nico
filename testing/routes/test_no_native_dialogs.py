"""Keine browser-eigenen Dialoge im Frontend (VM-Test 2026-11-21).

confirm(), alert() und prompt() sehen aus wie der Browser, nicht wie NiCo.
Alle Stellen laufen über nicoConfirm / nicoAlert / nicoPrompt (core.js).
Der Test hält das fest: ein neuer Aufruf der Browser-Dialoge fällt hier auf.

Lauf: nix-shell shell.nix --run "python testing/routes/test_no_native_dialogs.py"
"""
import re, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
JS = ROOT / "nico" / "static" / "js"

# A call, not a method or a word in a comment: no letter, dot or digit before.
CALL = re.compile(r"(?<![\w.$])(?:window\.)?(confirm|alert|prompt)\s*\(")

fails = []
for f in sorted(JS.glob("*.js")):
    for n, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
        code = line.split("//", 1)[0]
        if CALL.search(code):
            fails.append(f"{f.name}:{n}: {line.strip()}")

core = (JS / "core.js").read_text(encoding="utf-8")
for fn in ("nicoConfirm", "nicoAlert", "nicoPrompt"):
    if f"function {fn}(" not in core:
        fails.append(f"core.js: {fn} fehlt")

for x in fails:
    print("  FAIL", x)
if fails:
    sys.exit(1)
print("  OK   keine browser-eigenen Dialoge, NiCo-Dialoge vorhanden")
print("Alle Prüfungen bestanden.")
