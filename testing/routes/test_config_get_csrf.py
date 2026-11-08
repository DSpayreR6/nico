#!/usr/bin/env python3
"""GET /api/config verlangt das CSRF-Token (Revisionsbefund S9).

Die Route schreibt: Hat sich eine .nix-Datei von außen geändert, baut sie NiCo
neu und speichert config.json. Eine fremde Webseite kann einen GET gegen
127.0.0.1 auslösen, die Antwort lesen kann sie nicht, das Schreiben passiert
trotzdem. Ohne Token darf also keine Datei angefasst werden.

Läuft auf eigenen Temp-Verzeichnissen, die echten App-Settings bleiben unberührt.

Lauf: nix-shell shell.nix --run "python testing/routes/test_config_get_csrf.py"
"""
import json, os, re, sys, tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

xdg = Path(tempfile.mkdtemp(prefix="nico-xdg-"))
os.environ["XDG_CONFIG_HOME"] = str(xdg)
cfg = Path(tempfile.mkdtemp(prefix="nico-cfg-"))

# No version hash: exactly the state in which the route rewrites the file.
HAND_EDITED = '''{ config, pkgs, ... }:
{
  # a comment only a person would write
  networking.hostName = "handmade";
}
'''
nix_file = cfg / "configuration.nix"
nix_file.write_text(HAND_EDITED, encoding="utf-8")
(cfg / "config.json").write_text(json.dumps({"hosts_dir": "hosts"}), encoding="utf-8")
(xdg / "nico").mkdir(parents=True)
(xdg / "nico" / "settings.json").write_text(
    json.dumps({"nixos_config_dir": str(cfg), "language": "de"}), encoding="utf-8")

from nico import server  # noqa: E402

client = server.create_app().test_client()

fails = []
def check(label, cond, got=None):
    print(("  OK   " if cond else "  FAIL ") + label + ("" if cond or got is None else f"  (war: {got})"))
    if not cond: fails.append(label)


def snapshot() -> dict:
    return {str(p.relative_to(cfg)): p.read_bytes() for p in cfg.rglob("*") if p.is_file()}


page = client.get("/").get_data(as_text=True)
m = re.search(r'<meta name="csrf-token" content="([^"]+)"', page)
csrf = m.group(1) if m else ""
check("Token aus der Seite gelesen", bool(csrf))

before = snapshot()
r = client.get("/api/config")
check("ohne Token abgewiesen", r.status_code == 403, r.status_code)
check("ohne Token kommt ERR_CSRF zurück", (r.get_json() or {}).get("error") == "ERR_CSRF", r.get_json())
check("ohne Token bleibt jede Datei unverändert", snapshot() == before)

r = client.get("/api/config", headers={"X-CSRF-Token": "falsch"})
check("falsches Token abgewiesen", r.status_code == 403, r.status_code)
check("falsches Token ändert nichts", snapshot() == before)

r = client.get("/api/config", headers={"X-CSRF-Token": csrf})
check("mit Token erfolgreich", r.status_code == 200, r.status_code)
data = r.get_json() or {}
check("mit Token kommen die Daten", data.get("hostname") == "handmade" or "brick_blocks" in data, list(data)[:8])
check("mit Token läuft die Normalisierung wie bisher", snapshot() != before)

print()
if fails:
    print(f"{len(fails)} FEHLER:", *fails, sep="\n  ")
    sys.exit(1)
print("Alle Prüfungen bestanden.")
