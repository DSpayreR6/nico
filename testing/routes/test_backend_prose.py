#!/usr/bin/env python3
"""Das Backend schickt der Oberfläche Codes und Daten, keine Sätze.

Anlass (Martin, 2026-11-05, umgesetzt 2026-11-07): An 21 Stellen gab das
Backend Nutzertext unübersetzt zurück – deutsch für alle, teils englisch für
alle. Eine davon war sogar sichtbar kaputt: die Paketsuche zeigte bei einem
Fehler wörtlich „Paketsuche fehlgeschlagen: {0}“, weil die Ursache in einem
Feld stand, das die Oberfläche nie gelesen hat. Solche Sätze sehen im Code
nicht wie Übersetzungstext aus, deshalb rutschen sie durch jede Runde.

Geprüft wird statisch über den Syntaxbaum von nico/**/*.py:
  1. Kein `raise …Error("Satz")` mit einem Text, den eine Route weiterreicht –
     RuntimeError trägt einen Code (`_err(...)`).
  2. Kein Dict-Literal mit `message`/`error`/`detail` als festem Text: der
     Wert ist leer oder ein Code `ERR_…`.
  3. Kein Validator-Befund, dessen `detail` einen Satz enthält, ohne
     `detail_key` – der deutsche Text ist dort nur Rückfall.

Lauf: nix-shell shell.nix --run "python testing/routes/test_backend_prose.py"
"""
import ast
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "nico"

fails = []
# Two words in a row = a sentence, not a code or a path.
_WORDS = re.compile(r"[A-Za-zÄÖÜäöüß]{2,}\s+[A-Za-zÄÖÜäöüß]{2,}")
# Internal exceptions that never reach the interface (caught and logged).
_RAISE_OK = {"ValueError"}


def check(label, cond, got=None):
    print(("  OK   " if cond else "  FAIL ") + label
          + ("" if cond or got is None else f"  ({got})"))
    if not cond:
        fails.append(label)


def strings_in(node):
    for n in ast.walk(node):
        if isinstance(n, ast.Constant) and isinstance(n.value, str):
            yield n.value


raise_hits, dict_hits, finding_hits = [], [], []
for path in sorted(SRC.rglob("*.py")):
    rel = path.relative_to(ROOT)
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for n in ast.walk(tree):
        # 1. raise SomeError("sentence")
        if isinstance(n, ast.Raise) and isinstance(n.exc, ast.Call):
            name = getattr(n.exc.func, "id", "") or getattr(n.exc.func, "attr", "")
            if name not in _RAISE_OK and n.exc.args:
                arg = n.exc.args[0]
                if isinstance(arg, (ast.Constant, ast.JoinedStr)):
                    if any(_WORDS.search(s) for s in strings_in(arg)):
                        raise_hits.append(f"{rel}:{n.lineno}")
        # 2. {"message": "sentence"}
        if isinstance(n, ast.Dict):
            for k, v in zip(n.keys, n.values):
                if not (isinstance(k, ast.Constant) and k.value in ("message", "error", "detail")):
                    continue
                if isinstance(v, (ast.Constant, ast.JoinedStr)):
                    texts = list(strings_in(v))
                    joined = "".join(texts)
                    if joined and not joined.startswith("ERR_") and _WORDS.search(joined):
                        dict_hits.append(f"{rel}:{v.lineno}")
        # 3. Finding(detail="sentence") without detail_key
        if isinstance(n, ast.Call) and getattr(n.func, "id", "") == "Finding":
            kw = {k.arg: k.value for k in n.keywords}
            if "detail" in kw and "detail_key" not in kw:
                if any(_WORDS.search(s) for s in strings_in(kw["detail"])):
                    finding_hits.append(f"{rel}:{n.lineno}")

print("\n1. Ausnahmen tragen Codes")
check("kein raise mit Satz", not raise_hits, raise_hits)
print("\n2. Antworten tragen Codes")
check("kein message/error/detail mit Satz", not dict_hits, dict_hits)
print("\n3. Validator-Details sind übersetzbar")
check("kein detail-Satz ohne detail_key", not finding_hits, finding_hits)

# ── Selbstprobe: die Prüfung erkennt, was sie erkennen soll ─────────────────
print("\n4. Selbstprobe")
probe = ast.parse('x = {"message": "Keine URL angegeben."}\n'
                  'raise RuntimeError("nix nicht gefunden")\n')
check("erkennt Satz im Dict",
      any(isinstance(n, ast.Dict) and _WORDS.search(n.values[0].value)
          for n in ast.walk(probe)))
check("Code wird nicht als Satz gelesen", not _WORDS.search("ERR_NO_URL"))

print()
if fails:
    print("FEHLGESCHLAGEN:", len(fails))
    for x in fails:
        print("  -", x)
    sys.exit(1)
print("Alle Prüfungen bestanden.")
