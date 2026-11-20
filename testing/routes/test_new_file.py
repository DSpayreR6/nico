#!/usr/bin/env python3
"""Neue Datei im Dateibaum: eine .nix ist sofort gültiges Nix.

VM-Test 2026-11-20: „Neue Datei“ legte eine leere Datei an. Eine leere .nix
liest Nix nicht – eingebunden hielt der Validator rot an („Datei nicht
lesbar“), der Dry-Run lief nicht. Jetzt beginnt jede neue .nix als kleinstes
gültiges Modul (der Dateibaum legt nur .nix-Dateien an).

Lauf: nix-shell shell.nix --run "python testing/routes/test_new_file.py"
"""
import json, os, re, shutil, sys, tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

xdg = Path(tempfile.mkdtemp(prefix="nico-xdg-"))
os.environ["XDG_CONFIG_HOME"] = str(xdg)
cfg = Path(tempfile.mkdtemp(prefix="nico-cfg-"))
(cfg / "modules").mkdir()
(xdg / "nico").mkdir(parents=True)
(xdg / "nico" / "settings.json").write_text(
    json.dumps({"nixos_config_dir": str(cfg), "language": "de", "git_sync": False}),
    encoding="utf-8")

from nico import server, validator  # noqa: E402

fails = []
def check(label, cond, got=None):
    print(("  OK   " if cond else "  FAIL ") + label + ("" if cond or got is None else f"  (war: {got})"))
    if not cond: fails.append(label)

client = server.create_app().test_client()
page = client.get("/").get_data(as_text=True)
csrf = re.search(r'<meta name="csrf-token" content="([^"]+)"', page).group(1)
H = {"X-CSRF-Token": csrf, "Content-Type": "application/json"}

def create(name):
    r = client.post("/api/files/create", headers=H,
                    data=json.dumps({"parent_path": "modules", "name": name, "type": "file"}))
    return r.status_code, r.get_json() or {}

s, r = create("neu.nix")
check("angelegt", s == 200 and r.get("success"), (s, r))
text = (cfg / "modules" / "neu.nix").read_text(encoding="utf-8")
check("nicht leer, Grundgerüst", text.strip().startswith("{ ... }:") and text.strip().endswith("}"), text)
if shutil.which("nix-instantiate"):
    check("Nix kann sie lesen", validator.nix_parse_error(text) is None, validator.nix_parse_error(text))

print()
if fails:
    print(f"{len(fails)} FEHLER")
    sys.exit(1)
print("Alle Prüfungen bestanden.")
