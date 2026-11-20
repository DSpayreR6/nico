#!/usr/bin/env python3
"""Warum eine Datei nur im Raw-Modus aufgeht (VM-Test 2026-11-19, F).

Martins Datei hatte `};.` in einem Nix-Brick. Sie ging im Panel auf; erst
Panel-Schalter, „Unterschiede“ und Umbauen scheiterten mit „nicht als
Formular darstellbar“ – ohne Grund. Jetzt fragt NiCo beim Öffnen einer Datei
mit Formular Nix selbst; /api/file nennt den Grund:

  1. Syntaxfehler im Brick → unmappable, kind "parse", Zeile der Datei
  2. undefinierter Name → ebenfalls "parse" (Nix liest die Datei nicht)
  3. anderer Aufbau (let … in) → kind "structure"
  4. normale Datei → kein Grund
  5. Datei ohne Formular mit Syntaxfehler → kein Grund (ist ohnehin Raw)

Lauf: nix-shell shell.nix --run "python testing/routes/test_unmappable_reason.py"
"""
import json, os, re, shutil, sys, tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

xdg = Path(tempfile.mkdtemp(prefix="nico-xdg-"))
os.environ["XDG_CONFIG_HOME"] = str(xdg)
cfg = Path(tempfile.mkdtemp(prefix="nico-cfg-"))
(cfg / "config.json").write_text(json.dumps({"hosts_dir": "hosts"}), encoding="utf-8")
(xdg / "nico").mkdir(parents=True)
(xdg / "nico" / "settings.json").write_text(
    json.dumps({"nixos_config_dir": str(cfg), "language": "de", "git_sync": False}),
    encoding="utf-8")

from nico import config_manager, generator, server  # noqa: E402

fails = []
def check(label, cond, got=None):
    print(("  OK   " if cond else "  FAIL ") + label + ("" if cond or got is None else f"  (war: {got})"))
    if not cond: fails.append(label)

if not shutil.which("nix-instantiate"):
    print("nix-instantiate fehlt – Test übersprungen")
    sys.exit(0)

client = server.create_app().test_client()
page = client.get("/").get_data(as_text=True)
csrf = re.search(r'<meta name="csrf-token" content="([^"]+)"', page).group(1)
H = {"X-CSRF-Token": csrf}

def info(rel):
    return client.get(f"/api/file?path={rel}", headers=H).get_json() or {}

data = config_manager.get_defaults()
good = generator.generate_configuration_nix(data)
(cfg / "configuration.nix").write_text(good, encoding="utf-8")

print("4. Normale Datei")
r = info("configuration.nix")
check("darstellbar, kein Grund", r.get("unmappable") is False and r.get("unmappable_reason") is None, r.get("unmappable_reason"))

print("\n1. Syntaxfehler in einem Nix-Brick")
lines = good.rstrip("\n").split("\n")
close = max(i for i, l in enumerate(lines) if l.strip() == "}")
broken_lines = lines[:close] + [
    "  # <brick: TAIL / #1 kaputt>",
    "  services.foo = {",
    "    enable = true;",
    "  };.",
    "  # </brick: kaputt>",
] + lines[close:]
broken = "\n".join(broken_lines) + "\n"
bad_line = broken_lines.index("  };.") + 1
(cfg / "configuration.nix").write_text(broken, encoding="utf-8")
r = info("configuration.nix")
reason = r.get("unmappable_reason") or {}
check("nicht darstellbar", r.get("unmappable") is True, r.get("unmappable"))
check("Grund: parse", reason.get("kind") == "parse", reason)
check(f"Zeile der Datei ({bad_line})", reason.get("line") == bad_line, reason)
check("Nix-Meldung ohne 'error:'", reason.get("message", "").startswith("syntax error"), reason)

print("\n2. Undefinierter Name")
(cfg / "configuration.nix").write_text("{ ... }:\n{\n  networking.hostName = gibtsnicht;\n}\n", encoding="utf-8")
reason = info("configuration.nix").get("unmappable_reason") or {}
check("Grund: parse, Zeile 3", reason.get("kind") == "parse" and reason.get("line") == 3, reason)

print("\n3. Anderer Aufbau")
(cfg / "configuration.nix").write_text(
    "{ ... }:\nlet x = 1; in\n{\n  boot.loader.timeout = x;\n}\n", encoding="utf-8")
r = info("configuration.nix")
check("Grund: structure", r.get("unmappable") is True
      and (r.get("unmappable_reason") or {}).get("kind") == "structure", r.get("unmappable_reason"))

print("\n5. Datei ohne Formular")
(cfg / "notiz.nix").write_text("{ ... }: {\n};.\n", encoding="utf-8")
r = info("notiz.nix")
check("kein Grund (ohnehin Raw)", r.get("unmappable") is False and r.get("unmappable_reason") is None,
      (r.get("unmappable"), r.get("unmappable_reason")))

print()
if fails:
    print(f"{len(fails)} FEHLER")
    sys.exit(1)
print("Alle Prüfungen bestanden.")
