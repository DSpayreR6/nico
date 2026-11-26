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
  3. Routen (seit 2026-11-26, Revision S3/W1): Status nur aus dem eigenen
     Pfad, ohne Profil zu lesen; Lesen des Profils nur mit Freigabe (immer
     oder einmal per X-NiCo-Read-Once); nur der Eintrag der laufenden NiCo;
     Start ohne passenden Eintrag 400
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


two = json.dumps({"elements": {
    "nico": {"originalUrl": "github:DSpayreR6/nico", "storePaths": ["/nix/store/aaa-nico-0.24.0"]},
    "nico-dev": {"originalUrl": "github:DSpayreR6/nico/dev", "storePaths": ["/nix/store/bbb-nico-0.25.0-dev"]}}})
check("zwei Einträge: der mit dem laufenden Store-Pfad (W1)",
      selfupdate.parse_profile_list(two, "/nix/store/bbb-nico-0.25.0-dev") == ("nico-dev", "github:DSpayreR6/nico/dev"),
      selfupdate.parse_profile_list(two, "/nix/store/bbb-nico-0.25.0-dev"))
check("laufende NiCo in keinem Eintrag (nix run): None",
      selfupdate.parse_profile_list(two, "/nix/store/ccc-nico-0.25.0") is None)
check("Store-Pfad aus dem eigenen Pfad",
      selfupdate.running_store_path("/nix/store/bbb-nico-0.25.0-dev/lib/nico/nico/selfupdate.py")
      == "/nix/store/bbb-nico-0.25.0-dev")
check("Checkout: kein Store-Pfad", selfupdate.running_store_path("/home/x/projects/nico/nico/selfupdate.py") is None)

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

# Since 2026-11-26 (revision 2026-11-25, S3/W1): the status reads nothing
# but NiCo's own path; the profile is read on the click, with the release
# read_host_profile (always or once), and only the entry holding the
# running store path counts.
reads = []
STORE = "/nix/store/abc-nico-0.25.0"
selfupdate.running_store_path = lambda here=None: None
selfupdate.profile_entry = lambda store=None: (reads.append(store), None)[1]
d = client.get("/api/selfupdate/status").get_json()
check("Status aus einem Checkout: installed=false", d == {"installed": False}, d)
selfupdate.running_store_path = lambda here=None: STORE
d = client.get("/api/selfupdate/status").get_json()
check("Status aus dem Store: installed=true", d == {"installed": True}, d)
check("Status liest das Profil nicht", reads == [], reads)

r = client.post("/api/selfupdate", headers=hdr, data="{}")
check("ohne Freigabe: 403 ERR_HOST_READ_OFF (read_host_profile)",
      r.status_code == 403 and r.get_json() == {"error": "ERR_HOST_READ_OFF", "kind": "read_host_profile"},
      (r.status_code, r.get_json()))
r = client.get("/api/selfupdate/entry")
check("Eintrag ohne Freigabe: 403", r.status_code == 403, r.status_code)
check("… und das Profil nicht gelesen", reads == [], reads)

once = {**hdr, "X-NiCo-Read-Once": "read_host_profile"}
r = client.post("/api/selfupdate", headers=once, data="{}")
check("einmal erlaubt, kein passender Eintrag: 400 ERR_NOT_INSTALLED",
      r.status_code == 400 and r.get_json().get("error") == "ERR_NOT_INSTALLED",
      (r.status_code, r.get_json()))
check("… gesucht wurde der Eintrag der laufenden NiCo", reads == [STORE], reads)
r = client.post("/api/selfupdate", headers=hdr, data="{}")
check("einmal heißt: nicht gespeichert", r.status_code == 403, r.status_code)

client.patch("/api/app/settings", headers=hdr, data=json.dumps({"read_host_profile": True}))
selfupdate.profile_entry = lambda store=None: (reads.append(store), ("nico-dev", "github:DSpayreR6/nico/dev"))[1]
r = client.get("/api/selfupdate/entry")
check("immer erlaubt: Eintrag mit Herkunft", r.status_code == 200
      and r.get_json() == {"name": "nico-dev", "url": "github:DSpayreR6/nico/dev"}, r.get_json())
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
check("Name des laufenden Eintrags an upgrade_command", ran[-1:] == ["nico-dev"], ran)
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
