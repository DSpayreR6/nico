#!/usr/bin/env python3
"""Ausgeschaltete automatische Anmeldung bleibt beim Import aus.

Revision 2026-11-18, Befund 15 (Codex, reproduziert von Claude Opus 5.5):
`autoLogin.enable = false;` mit gesetztem `user` wurde beim Import zu
`enable = true;` – der Benutzername allein galt als „an“ und markierte die
Enable-Zeile als verarbeitet. Jetzt: `enable` ausdrücklich false oder ein
Ausdruck → nichts übernehmen, die Zeilen bleiben wörtlich als Nix-Brick.

Lauf: nix-shell shell.nix --run "python testing/routes/test_autologin_import.py"
"""
import os, re, sys, tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
os.environ["XDG_CONFIG_HOME"] = tempfile.mkdtemp(prefix="nico-xdg-")

from nico import config_manager, generator, importer  # noqa: E402

fails = []
def check(label, cond, got=None):
    print(("  OK   " if cond else "  FAIL ") + label + ("" if cond or got is None else f"  (war: {got!r})"))
    if not cond: fails.append(label)

def roundtrip(body):
    """Import like the import routes, then write as NiCo does."""
    t = "{ config, pkgs, ... }:\n{\n  services.desktopManager.plasma6.enable = true;\n" + body + "}\n"
    rec = importer.parse_config(t)
    data = dict(config_manager.get_defaults()); data.update(rec)
    data["_flake_mode"] = False
    # The import passes what the form does not take as Nix-Brix; the
    # generator writes them back. Both together are the written file.
    return rec, generator.generate_configuration_nix(data) + "\n" + importer.build_rest_brix(t, rec)

def active(text):
    """Code lines only – comments and brick markers dropped."""
    return "\n".join(l for l in text.splitlines() if not l.lstrip().startswith("#"))

PATHS = ("services.displayManager.autoLogin", "services.xserver.displayManager.autoLogin")

def forms(path, enable):
    en_dot = f"  {path}.enable = {enable};\n" if enable is not None else ""
    en_blk = f" enable = {enable};" if enable is not None else ""
    return {
        "Punkt-Form": en_dot + f'  {path}.user = "bob";\n',
        "Block-Form": f'  {path} = {{{en_blk} user = "bob"; }};\n',
    }

for path in PATHS:
    short = "alt (xserver)" if "xserver" in path else "neu"
    for enable in ("false", "(1 == 2)"):
        for form, body in forms(path, enable).items():
            print(f"\n{short}, {form}, enable = {enable}")
            rec, out = roundtrip(body)
            code = active(out)
            check("Formularfeld bleibt leer", not rec.get("autologin_user"), rec.get("autologin_user"))
            check("keine aktive Anmeldung erzeugt", not re.search(r"enable\s*=\s*true;\s*\n\s*user", code)
                  and f"{path}.enable = true" not in code, code)
            check("Originalwert bleibt wörtlich erhalten", f"enable = {enable};" in code, code)
            check("Benutzername bleibt erhalten", 'user = "bob";' in code, code)

    for enable in ("true", None):
        for form, body in forms(path, enable).items():
            label = "fehlt" if enable is None else enable
            print(f"\n{short}, {form}, enable {label}")
            rec, out = roundtrip(body)
            code = active(out)
            check("Benutzer übernommen", rec.get("autologin_user") == "bob", rec.get("autologin_user"))
            check("Anmeldung genau einmal aktiv",
                  code.count('user = "bob";') == 1 and "enable = true;" in code, code)

print()
if fails:
    print(f"{len(fails)} FEHLER:", *fails, sep="\n  ")
    sys.exit(1)
print("Alle Prüfungen bestanden.")
