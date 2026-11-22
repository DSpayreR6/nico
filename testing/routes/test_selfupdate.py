#!/usr/bin/env python3
"""NiCo aktualisieren aus dem Hilfe-Menü, 2026-11-22.

Nur eine mit `nix profile install github:DSpayreR6/nico[/dev]` installierte
NiCo lässt sich so aktualisieren: `nix profile upgrade <name> --refresh`,
derselbe Befehl wie Update in install.sh. Am echten Profil wird nichts
geändert – profile_entry und upgrade_command sind im Test ersetzt.

Geprüft wird:
  1. Profilliste: neues JSON-Format (elements als Objekt, Nix ≥ 2.20) und
     altes (Liste, Index als Name); Treffer über die URL, nicht über den
     Namen; ein fremdes Paket mit ähnlicher URL zählt nicht; kaputtes JSON.
  2. Befehl: upgrade <name> --refresh, Flakes-Schalter dabei, kein Shell.
  3. Routen: Status installed true/false; Start ohne Installation 400
     ERR_NOT_INSTALLED; ohne CSRF abgelehnt; mit Installation Job im
     Rebuild-Monitor mit kind=selfupdate; Fehlschlag meldet success=false.
  4. Oberfläche: Menüpunkt ist zu Beginn ausgeblendet.

Lauf: nix-shell shell.nix --run "python testing/routes/test_selfupdate.py"
"""
import json
import os
import re
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

xdg = Path(tempfile.mkdtemp(prefix="nico-xdg-"))
os.environ["XDG_CONFIG_HOME"] = str(xdg)
cfg = Path(tempfile.mkdtemp(prefix="nico-cfg-"))
(xdg / "nico").mkdir(parents=True)
(xdg / "nico" / "settings.json").write_text(
    json.dumps({"nixos_config_dir": str(cfg), "language": "de", "git_sync": False}),
    encoding="utf-8")
(cfg / "configuration.nix").write_text("{ ... }:\n{\n}\n", encoding="utf-8")

from nico import selfupdate, server  # noqa: E402
from nico.routes import rebuild as rb  # noqa: E402

fails = []


def check(label, cond, got=None):
    print(("  OK   " if cond else "  FAIL ") + label
          + ("" if cond or got is None else f"  (war: {got})"))
    if not cond:
        fails.append(label)


# ── 1. Profilliste ───────────────────────────────────────────────────────────
print("1. Profilliste")
new_fmt = json.dumps({"version": 3, "elements": {
    "home-manager-path": {"active": True, "storePaths": ["/nix/store/x-hm"]},
    "nico": {"active": True, "originalUrl": "github:DSpayreR6/nico/dev",
             "url": "github:DSpayreR6/nico/bfb3dc0"},
}})
check("neues Format: Name und URL",
      selfupdate.parse_profile_list(new_fmt) == ("nico", "github:DSpayreR6/nico/dev"),
      selfupdate.parse_profile_list(new_fmt))
renamed = json.dumps({"version": 3, "elements": {
    "nico-1": {"originalUrl": "github:DSpayreR6/nico"}}})
check("Treffer über URL, nicht Namen (nico-1, main)",
      selfupdate.parse_profile_list(renamed) == ("nico-1", "github:DSpayreR6/nico"))
old_fmt = json.dumps({"version": 2, "elements": [
    {"originalUrl": "flake:nixpkgs#hello"},
    {"originalUrl": "github:DSpayreR6/nico/dev"},
]})
check("altes Format: Index als Name",
      selfupdate.parse_profile_list(old_fmt) == ("1", "github:DSpayreR6/nico/dev"),
      selfupdate.parse_profile_list(old_fmt))
foreign = json.dumps({"version": 3, "elements": {
    "nico-tools": {"originalUrl": "github:DSpayreR6/nico-tools"},
    "other": {"originalUrl": "github:someone/DSpayreR6/nico"}}})
check("fremdes Paket mit ähnlicher URL zählt nicht",
      selfupdate.parse_profile_list(foreign) is None, selfupdate.parse_profile_list(foreign))
check("ohne NiCo: None", selfupdate.parse_profile_list(
    json.dumps({"version": 3, "elements": {}})) is None)
check("kaputtes JSON: None", selfupdate.parse_profile_list("{nope") is None)
check("leere Ausgabe: None", selfupdate.parse_profile_list("") is None)

# ── 2. Befehl ────────────────────────────────────────────────────────────────
print("2. Befehl")
cmd = selfupdate.upgrade_command("nico")
check("upgrade nico --refresh", cmd[-4:] == ["profile", "upgrade", "nico", "--refresh"], cmd)
check("Flakes-Schalter als ein Argument",
      cmd[:3] == ["nix", "--extra-experimental-features", "nix-command flakes"], cmd)

# ── 3. Routen ────────────────────────────────────────────────────────────────
print("3. Routen")
app = server.create_app()
client = app.test_client()
page = client.get("/").get_data(as_text=True)
m = re.search(r'<meta name="csrf-token" content="([^"]+)"', page)
hdr = {"X-CSRF-Token": m.group(1) if m else "", "Content-Type": "application/json"}

selfupdate.profile_entry = lambda: None
d = client.get("/api/selfupdate/status").get_json()
check("Status ohne Installation: installed=false", d == {"installed": False, "url": ""}, d)
r = client.post("/api/selfupdate", headers=hdr, data="{}")
check("Start ohne Installation 400 ERR_NOT_INSTALLED",
      r.status_code == 400 and r.get_json().get("error") == "ERR_NOT_INSTALLED",
      (r.status_code, r.get_json()))

selfupdate.profile_entry = lambda: ("nico", "github:DSpayreR6/nico/dev")
d = client.get("/api/selfupdate/status").get_json()
check("Status mit Installation: URL", d == {"installed": True,
                                            "url": "github:DSpayreR6/nico/dev"}, d)
r = client.post("/api/selfupdate", headers={"Content-Type": "application/json"}, data="{}")
check("ohne CSRF abgelehnt", r.status_code in (400, 403), r.status_code)

ran = []


def run_job(script):
    selfupdate.upgrade_command = lambda name: (ran.append(name), ["sh", "-c", script])[1]
    r = client.post("/api/selfupdate", headers=hdr, data="{}")
    if r.status_code != 200:
        return r, None
    for _ in range(100):
        st = client.get("/api/rebuild/status").get_json()
        if st.get("job") and not st["job"]["running"]:
            break
        time.sleep(0.1)
    with rb._job_cv:
        events = list(rb._job["events"])
    done = [json.loads(e[6:]) for e in events if '"type": "done"' in e]
    return r, (done[0] if done else None)


r, done = run_job("echo upgraded")
check("Job gestartet", r.status_code == 200 and r.get_json().get("success"), r.get_json())
check("Profilname an upgrade_command", ran[-1:] == ["nico"], ran)
check("Abschluss kind=selfupdate, erfolgreich",
      done and done.get("kind") == "selfupdate" and done.get("success") is True, done)
client.post("/api/rebuild/ack", headers=hdr, data="{}")

r, done = run_job("echo kaputt; exit 1")
check("Fehlschlag: success=false", done and done.get("success") is False, done)
client.post("/api/rebuild/ack", headers=hdr, data="{}")

# ── 4. Oberfläche ────────────────────────────────────────────────────────────
print("4. Oberfläche")
html = (ROOT / "nico" / "templates" / "index.html").read_text(encoding="utf-8")
check("Menüpunkt zu Beginn ausgeblendet",
      re.search(r'<button id="selfupdate-btn" class="hidden"', html) is not None)

print("\n" + ("ALLE PRÜFUNGEN BESTANDEN" if not fails else f"FEHLGESCHLAGEN: {fails}"))
sys.exit(1 if fails else 0)
