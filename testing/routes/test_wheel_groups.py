#!/usr/bin/env python3
"""wheel (Admin-Rechte) vergibt NiCo nie von selbst.

Revision 2026-11-18, Befund 14 (Codex, reproduziert von Claude Opus 5.5):
Der Import machte Benutzer zu Admins. `extraGroups = [];` wurde zu
`[ "networkmanager" "wheel" ]`, ein Benutzer ganz ohne extraGroups bekam die
Vorgabe mit wheel, und der Generator erzwang `wheel` selbst bei leerer
Auswahl. Entscheidung Martin: wheel muss ausdrücklich gewählt werden; eine
Config ohne Admin meldet der Validator rot (Rebuild hält an, lässt sich
erzwingen).

Lauf: nix-shell shell.nix --run "python testing/routes/test_wheel_groups.py"
"""
import json, os, sys, tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
os.environ["XDG_CONFIG_HOME"] = tempfile.mkdtemp(prefix="nico-xdg-")

from nico import config_manager, generator, importer, validator  # noqa: E402

fails = []
def check(label, cond, got=None):
    print(("  OK   " if cond else "  FAIL ") + label + ("" if cond or got is None else f"  (war: {got})"))
    if not cond: fails.append(label)

def roundtrip(body):
    """Import like the import routes, then write as NiCo does."""
    t = "{ config, pkgs, ... }:\n{\n" + body + "}\n"
    rec = importer.parse_config(t)
    data = dict(config_manager.get_defaults()); data.update(rec)
    data["_flake_mode"] = False
    return rec, generator.generate_configuration_nix(data)

def groups_line(text, user):
    i = text.find(f"users.users.{user} = {{")
    block = text[i:text.find("};", i)] if i >= 0 else ""
    return next((l.strip() for l in block.splitlines() if "extraGroups" in l), None)

print("1. Import übernimmt die Gruppen genau")
rec, out = roundtrip('  users.users.anna = { isNormalUser = true; extraGroups = []; };\n')
check("leere Liste bleibt leer", rec.get("user_groups") == [], rec.get("user_groups"))
check("… kein wheel in der geschriebenen Datei", '"wheel"' not in out, groups_line(out, "anna"))
rec, out = roundtrip('  users.users.anna = { isNormalUser = true; };\n')
check("ohne extraGroups: keine Gruppen", rec.get("user_groups") == [], rec.get("user_groups"))
check("… kein wheel in der geschriebenen Datei", '"wheel"' not in out, groups_line(out, "anna"))
rec, out = roundtrip('  users.users.anna = { isNormalUser = true; extraGroups = [ "audio" ]; };\n')
check("eigene Gruppen bleiben, nichts dazu", groups_line(out, "anna") == 'extraGroups = [ "audio" ];',
      groups_line(out, "anna"))
rec, out = roundtrip('  users.users.anna = { isNormalUser = true; extraGroups = [ "wheel" ]; };\n'
                     '  users.users.bert = { isNormalUser = true; extraGroups = []; };\n'
                     '  users.users.carl = { isNormalUser = true; };\n')
eus = {u["username"]: u for u in rec.get("extra_users", [])}
check("weiterer Benutzer mit leerer Liste: leer", eus.get("bert", {}).get("groups") == [], eus.get("bert"))
check("weiterer Benutzer ohne Zeile: leer", eus.get("carl", {}).get("groups") == [], eus.get("carl"))
check("… geschrieben ohne wheel", groups_line(out, "bert") is None and groups_line(out, "carl") is None,
      (groups_line(out, "bert"), groups_line(out, "carl")))
check("wheel beim Hauptbenutzer bleibt", groups_line(out, "anna") == 'extraGroups = [ "wheel" ];',
      groups_line(out, "anna"))

print("\n2. Generator erzwingt nichts, Vorgabe ohne wheel")
d = dict(config_manager.get_defaults(), username="neu", user_groups=[], _flake_mode=False)
check("leere Auswahl: keine extraGroups-Zeile",
      groups_line(generator.generate_configuration_nix(d), "neu") is None)
check("Vorgabe ohne wheel", "wheel" not in config_manager.get_defaults()["user_groups"],
      config_manager.get_defaults()["user_groups"])

print("\n3. Validator: Config ohne Admin")
def rule(text):
    dd = Path(tempfile.mkdtemp(prefix="nico-w-"))
    (dd / "configuration.nix").write_text(text, encoding="utf-8")
    return validator._rule_no_admin_user(str(dd), {}, False)
f = rule('{ ... }:\n{\n  users.users.anna = { isNormalUser = true; extraGroups = [ "audio" ]; };\n}\n')
check("niemand in wheel → roter Befund", len(f) == 1 and f[0].severity == "error",
      [(x.rule_id, x.severity) for x in f])
check("… mit Übersetzungsschlüssel", f and f[0].message_key == "validator.f.no_admin_user")
check("einer in wheel → still", rule('{ ... }:\n{\n  users.users.anna = { isNormalUser = true; '
                                     'extraGroups = [ "wheel" ]; };\n}\n') == [])
check("über users.groups.wheel.members → still",
      rule('{ ... }:\n{\n  users.users.anna.isNormalUser = true;\n'
           '  users.groups.wheel.members = [ "anna" ];\n}\n') == [])
check("wheel in einem Nix-Brick → still",
      rule('{ ... }:\n{\n  users.users.anna.isNormalUser = true;\n'
           '# <brick: TAIL / #1 admin>\n  users.users.anna.extraGroups = [ "wheel" ];\n'
           '# </brick: admin>\n}\n') == [])
check("auskommentiert zählt nicht",
      len(rule('{ ... }:\n{\n  users.users.anna.isNormalUser = true;\n'
               '  # users.users.anna.extraGroups = [ "wheel" ];\n}\n')) == 1)
check("ohne Benutzer → still (andere Regeln zuständig)", rule('{ ... }:\n{\n}\n') == [])
check("nur Systembenutzer → still",
      rule('{ ... }:\n{\n  users.users.dienst = { isSystemUser = true; group = "x"; };\n}\n') == [])

print()
if fails:
    print(f"{len(fails)} FEHLER:", *fails, sep="\n  ")
    sys.exit(1)
print("Alle Prüfungen bestanden.")
