#!/usr/bin/env python3
"""Der Validator hält an, wenn Nix eine Datei nicht lesen kann.

Aus Martins produktiver Config am 2026-10-31. In einem Nix-Brick der flake.nix
stand `nixpkgs-unstable` allein, ohne Zuweisung. Nix meldete einen
Syntaxfehler in Zeile 113, der Rebuild brach ab – und auf dem Bildschirm stand
ein Python-Traceback von `nixos-rebuild-ng`, weil das Werkzeug nix mit
`--log-format internal-json` aufruft und die lesbare Meldung dabei verliert.

Der Validator ließ den Bau durch: von 38 Regeln prüfte keine, ob die Dateien
überhaupt lesbar sind. Das Gate von 0.14.0 verspricht genau das zu verhindern.

Geprüft wird:
  1. Syntaxfehler in configuration.nix wird gemeldet, Schweregrad error.
  2. Derselbe Fehler in einem Nix-Brick – der Fall aus der Praxis.
  3. Ein unbekannter Name (`undefined variable`) wird ebenfalls gefunden;
     `--parse` löst Namen auf, nicht nur Klammern.
  4. Kein Fehlalarm: Modul mit Funktionsargumenten, `with pkgs;`, let-Bindung.
  5. Mehrere kaputte Dateien werden alle genannt, nicht nur die erste.
  6. Die Meldung nennt die Datei, das Detail die Nix-Zeile.
  7. Ohne nix-instantiate bleibt die Regel still, statt den Bau zu blockieren.

Lauf: nix-shell shell.nix --run "python testing/routes/test_nix_parses.py"
"""
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

xdg = Path(tempfile.mkdtemp(prefix="nico-xdg-"))
os.environ["XDG_CONFIG_HOME"] = str(xdg)

from nico import validator  # noqa: E402

fails = []


def check(label, cond, got=None):
    print(("  OK   " if cond else "  FAIL ") + label
          + ("" if cond or got is None else f"  (war: {got})"))
    if not cond:
        fails.append(label)


def cfg(**files) -> str:
    d = tempfile.mkdtemp(prefix="nico-cfg-")
    for name, text in files.items():
        p = Path(d) / name.replace("__", "/")
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    return d


def run(d):
    return validator._rule_nix_parses(d, {}, True, host=None)


GOOD = "{ config, pkgs, ... }:\n{\n  services.openssh.enable = true;\n}\n"

if shutil.which("nix-instantiate") is None:
    print("nix-instantiate nicht gefunden – Test übersprungen.")
    sys.exit(0)

# ── 1. Syntaxfehler ──────────────────────────────────────────────────────────
print("\n1. Syntaxfehler")
f = run(cfg(**{"configuration.nix": "{ a = ; }\n"}))
check("gemeldet", len(f) == 1, [x.message for x in f])
check("Schweregrad error", bool(f) and f[0].severity == "error",
      f[0].severity if f else None)
check("nennt die Datei", bool(f) and "configuration.nix" in f[0].message,
      f[0].message if f else None)
check("Detail trägt die Nix-Meldung",
      bool(f) and "error:" in (f[0].detail or ""), f[0].detail if f else None)

# ── 2. Der Fall aus der Praxis: kaputter Brick ───────────────────────────────
print("\n2. Freier Ausdruck in einem Nix-Brick")
praxis = """{
  description = "test";
  outputs = { self, nixpkgs }: {
# <brick: OUTX / #2 unstable>
nixpkgs-unstable


# </brick: unstable>
  };
}
"""
f = run(cfg(**{"flake.nix": praxis}))
check("gemeldet", len(f) == 1, [x.message for x in f])
check("nennt flake.nix", bool(f) and "flake.nix" in f[0].message,
      f[0].message if f else None)

# ── 3. Unbekannter Name ──────────────────────────────────────────────────────
print("\n3. Unbekannter Name")
f = run(cfg(**{"configuration.nix": "{ x = voellig-unbekannt; }\n"}))
check("undefined variable wird gefunden", len(f) == 1, [x.message for x in f])

# ── 4. Keine Fehlalarme ──────────────────────────────────────────────────────
print("\n4. Gültige Dateien")
ok_files = {
    "configuration.nix": GOOD,
    "hosts__nix-vm__default.nix": "{ pkgs, ... }:\nwith pkgs;\n{\n  x = hello;\n}\n",
    "modules__let.nix": "let p = 5; in { config, ... }: { y = p; }\n",
    "home__martin.nix": "{ ... }:\n{\n  home.stateVersion = \"25.05\";\n}\n",
}
f = run(cfg(**ok_files))
check("kein Befund", f == [], [x.message for x in f])

# ── 5. Mehrere kaputte Dateien ───────────────────────────────────────────────
print("\n5. Mehrere kaputte Dateien")
f = run(cfg(**{
    "configuration.nix": "{ a = ; }\n",
    "modules__zwei.nix": "{ b = ; }\n",
    "modules__heil.nix": GOOD,
}))
named = sorted(x.message for x in f)
check("beide genannt", len(f) == 2, named)
check("die heile Datei nicht", not any("heil" in m for m in named), named)

# ── 6. Verzeichnisse, die nicht dazugehören ──────────────────────────────────
print("\n6. .git wird übergangen")
d = cfg(**{"configuration.nix": GOOD})
git_dir = Path(d) / ".git"
git_dir.mkdir()
(git_dir / "kaputt.nix").write_text("{ a = ; }\n", encoding="utf-8")
check("kein Befund aus .git", run(d) == [], [x.message for x in run(d)])

# ── 7. Ohne nix-instantiate ──────────────────────────────────────────────────
print("\n7. Ohne nix-instantiate")
d = cfg(**{"configuration.nix": "{ a = ; }\n"})
_orig = subprocess.run


def _no_nix(*a, **kw):
    raise FileNotFoundError("nix-instantiate")


subprocess.run = _no_nix
try:
    f = run(d)
finally:
    subprocess.run = _orig
check("bleibt still statt zu blockieren", f == [], [x.message for x in f])

print()
if fails:
    print("FEHLGESCHLAGEN:", len(fails))
    for x in fails:
        print("  -", x)
    sys.exit(1)
print("Alle Prüfungen bestanden.")
