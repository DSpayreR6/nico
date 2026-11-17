#!/usr/bin/env python3
"""Geändert, während das Formular offen ist: fragen statt überschreiben.

Entscheidung 2026-11-17 (Martin, manifest „Befund aus Schritt 0“, Punkt 2
Fall B): Ändert sich eine Datei außerhalb von NiCo, während ihr Formular
geladen ist – Syncthing, git pull, ein Editor, eine zweite NiCo –, schrieb
das nächste Speichern aus dem Formular die alten Werte zurück. Jetzt
antwortet der Server mit 409 ERR_CHANGED_OUTSIDE, die Oberfläche fragt:
neu laden, Unterschiede ansehen, überschreiben (mit Sicherungspunkt).

Die Prüfsumme in der Kopfzeile erkennt das nicht – eine NiCo auf einem
anderen Rechner schreibt eine gültige. Darum hier ausdrücklich mit
gültiger Prüfsumme geändert (core.set_type_in_content).

Lauf: nix-shell shell.nix --run "python testing/routes/test_outside_change.py"
"""
import json, os, re, sys, tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

xdg = Path(tempfile.mkdtemp(prefix="nico-xdg-"))
os.environ["XDG_CONFIG_HOME"] = str(xdg)
cfg = Path(tempfile.mkdtemp(prefix="nico-cfg-"))
(cfg / "hosts" / "h1").mkdir(parents=True)
(cfg / "config.json").write_text(json.dumps({"hosts_dir": "hosts"}), encoding="utf-8")
(xdg / "nico").mkdir(parents=True)
(xdg / "nico" / "settings.json").write_text(
    json.dumps({"nixos_config_dir": str(cfg), "language": "de", "git_sync": False}),
    encoding="utf-8")

from nico import config_manager, core, git_manager, outside, server  # noqa: E402

fails = []
def check(label, cond, got=None):
    print(("  OK   " if cond else "  FAIL ") + label + ("" if cond or got is None else f"  (war: {got})"))
    if not cond: fails.append(label)

client = server.create_app().test_client()
page = client.get("/").get_data(as_text=True)
csrf = re.search(r'<meta name="csrf-token" content="([^"]+)"', page).group(1)
H = {"X-CSRF-Token": csrf, "Content-Type": "application/json"}

def post(url, body):
    r = client.post(url, headers=H, data=json.dumps(body))
    return r.status_code, (r.get_json(silent=True) or {})

def read(rel):
    return (cfg / rel).read_text(encoding="utf-8")

def change_outside(rel, old, new):
    """Another NiCo writes: valid hash, not through this process."""
    text = read(rel).replace(old, new)
    (cfg / rel).write_text(core.set_type_in_content(text, core.get_nico_type(text)),
                           encoding="utf-8")

def form(rel):
    data = client.get(f"/api/parse/co?path={rel}", headers=H).get_json()
    data.update({"_co_ready": True, "_co_path": rel})
    return data

CO = "configuration.nix"
HOST = "hosts/h1/default.nix"

print("Ausgangslage")
s, r = post("/api/config", dict(config_manager.get_defaults(), hostname="alt",
                                _co_ready=True, _co_path=CO))
check("configuration.nix erzeugt", s == 200 and r.get("success"), r)
s, r = post("/api/host/h1/config", {"_co_ready": True, "_co_path": HOST, "hostname": "h1"})
check("Host-Datei erzeugt", s == 200 and r.get("success"), r)
ok, _ = git_manager.init_repo(str(cfg))
check("Git eingerichtet", ok)
git_manager.auto_commit(str(cfg), label="start")

# ── 1. configuration.nix ────────────────────────────────────────────────────
print("\n1. configuration.nix: außen geändert, Formular veraltet")
stale = form(CO)
stale["timezone"] = "Europe/Berlin"
change_outside(CO, '"alt"', '"vonaussen"')
outside_state = read(CO)
s, r = post("/api/config", stale)
check("Speichern antwortet 409", s == 409, s)
check("… mit ERR_CHANGED_OUTSIDE und Datei", r.get("error") == "ERR_CHANGED_OUTSIDE"
      and r.get("file") == CO, r)
check("Datei unverändert", read(CO) == outside_state)

r = client.get(f"/api/file/outside-diff?path={CO}", headers=H).get_json()
hunks = (r.get("files") or [{}])[0].get("hunks", [])
check("Unterschiede: alter Wert entfernt", any(h["type"] == "removed" and '"alt"' in h["content"] for h in hunks), hunks)
check("Unterschiede: neuer Wert hinzu", any(h["type"] == "added" and '"vonaussen"' in h["content"] for h in hunks), hunks)

print("\n   Überschreiben")
before = len(git_manager.get_log(str(cfg)))
s, r = post("/api/config", dict(stale, _overwrite=True))
check("Überschreiben ok", s == 200 and r.get("success"), r)
check("Formularwert steht in der Datei", '"alt"' in read(CO) and "Europe/Berlin" in read(CO))
log = git_manager.get_log(str(cfg))
check("Sicherungspunkt angelegt", len(log) > before, (before, len(log)))
labels = " ".join(json.dumps(e, ensure_ascii=False) for e in log[:2])
check("… mit Bezeichnung", "backup before overwriting configuration.nix" in labels, labels[:200])
s, r = post("/api/config", stale)
check("danach normal speicherbar (eigener Stand bekannt)", s == 200 and r.get("success"), r)

print("\n   Neu laden")
change_outside(CO, '"alt"', '"zweiter"')
s, _ = post("/api/config", stale)
check("wieder 409", s == 409, s)
fresh = form(CO)
check("Formular liest den neuen Stand", fresh.get("hostname") == "zweiter", fresh.get("hostname"))
s, r = post("/api/config", fresh)
check("nach dem Neuladen speicherbar", s == 200 and r.get("success"), r)
check("neuer Stand bleibt", '"zweiter"' in read(CO))

# ── 2. Host-Datei ───────────────────────────────────────────────────────────
print("\n2. Host-Datei")
stale = form(HOST)
change_outside(HOST, '"h1"', '"h1aussen"')
outside_state = read(HOST)
s, r = post("/api/host/h1/config", stale)
check("Speichern antwortet 409", s == 409 and r.get("file") == HOST, (s, r))
check("Datei unverändert", read(HOST) == outside_state)
s, r = post("/api/host/h1/config", dict(stale, _overwrite=True))
check("Überschreiben ok", s == 200 and r.get("success"), r)
check("Formularwert steht in der Datei", '"h1"' in read(HOST))
check("„_overwrite“ landet nicht in der Datei", "_overwrite" not in read(HOST))

# ── 3. Fälle ohne Rückfrage ─────────────────────────────────────────────────
print("\n3. Ohne Rückfrage")
stale = form(CO)
s, _ = post("/api/file/panel-mode", {"path": CO, "mode": "r"})
change_outside(CO, '"zweiter"', '"rawaussen"')
s, r = post("/api/config", stale)
check("Raw-Modus: keine Rückfrage, nichts geschrieben", s == 200 and '"rawaussen"' in read(CO), (s, r))
post("/api/file/panel-mode", {"path": CO, "mode": "p"})

stale = form(CO)
change_outside(CO, '"rawaussen"', '"unbekannt"')
outside._known.clear()          # NiCo restarted after the form was loaded
s, r = post("/api/config", stale)
check("unbekannter Stand (Neustart): keine Rückfrage", s == 200 and r.get("success"), (s, r))

stale = form(HOST)
text = read(HOST)
i = text.rstrip().rfind("}")
s, _ = post("/api/file", {"files": [{"path": HOST,
            "content": text[:i] + "  programs.mtr.enable = true;\n" + text[i:]}]})
check("NiCo schreibt die Host-Datei selbst (Raw-Speichern)", s == 200, s)
s, r = post("/api/host/h1/config", stale)
check("… das zählt nicht als außen", s == 200 and r.get("success"), (s, r))

print()
print("ALLE PRÜFUNGEN BESTANDEN" if not fails else f"FEHLGESCHLAGEN: {len(fails)}")
sys.exit(0 if not fails else 1)
