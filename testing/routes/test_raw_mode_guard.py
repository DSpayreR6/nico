#!/usr/bin/env python3
"""Eine Datei im Raw-Modus (#r) fasst kein Speichern aus dem Formular an.

Befund 2026-11-17 (Claude Opus 5.5, Martins VM-Test): Raw-Modus an, Zeile
eintragen, Diskette – „Diese Datei wird von NiCo verwaltet.“ Dazwischen hatte
ein stilles Speichern des Formulars (_autoSave → /api/config) die Datei aus
dem Formular neu geschrieben, ohne #r. Der Raw-Editor zeigte weiter die alte
Kopfzeile, der Server sah kein #r und sperrte. Dabei werden auch Raw-
Änderungen an Einstellungen, die das Formular kennt, mit alten
Formularwerten überschrieben.

Geprüft über die echten Routen: Raw-Modus an, Raw speichern (mit einer
geänderten Formular-Einstellung und einer unbekannten), dann jeder
Schreibweg des Formulars mit dem alten Formularstand. Die Datei muss Byte
für Byte bleiben, und Raw-Speichern muss danach weiter gehen.

Gegen den Stand vor dem Fix fallen die „bleibt“-Prüfungen.

Lauf: nix-shell shell.nix --run "python testing/routes/test_raw_mode_guard.py"
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

from nico import config_manager, core, server  # noqa: E402

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

def add_before_end(text, stmt):
    i = text.rstrip().rfind("}")
    return text[:i] + stmt + text[i:]

CO = "configuration.nix"
HOST = "hosts/h1/default.nix"

# ── Ausgangslage ────────────────────────────────────────────────────────────
print("Ausgangslage")
s, r = post("/api/config", dict(config_manager.get_defaults(), hostname="alt",
                                _co_ready=True, _co_path=CO))
check("configuration.nix erzeugt", s == 200 and r.get("success"), r)
s, r = post("/api/host/h1/config", {"_co_ready": True, "_co_path": HOST, "hostname": "h1"})
check("Host-Datei erzeugt", s == 200 and r.get("success"), r)

# The stale form, as it stands in the browser before the raw edit
co_form = client.get("/api/config", headers=H).get_json()
co_form.update({"_co_ready": True, "_co_path": CO})
host_form = client.get(f"/api/parse/co?path={HOST}", headers=H).get_json()
host_form.update({"_co_ready": True, "_co_path": HOST})

def raw_save(rel, old, new, stmt):
    text = read(rel)
    check(f"„{old}“ steht vorher drin", old in text)
    s, r = post("/api/file", {"files": [{"path": rel,
                "content": add_before_end(text.replace(old, new), stmt)}]})
    check("Raw speichern ok", s == 200 and r.get("success"), r)

def unchanged(rel, before, label):
    after = read(rel)
    check(f"{label}: Datei bleibt", after == before)
    check(f"{label}: #r bleibt", core.get_panel_mode(after) == "r", core.get_panel_mode(after))

# ── 1. configuration.nix ────────────────────────────────────────────────────
print("\n1. configuration.nix im Raw-Modus")
s, _ = post("/api/file/panel-mode", {"path": CO, "mode": "r"})
check("Raw-Modus an", s == 200)
raw_save(CO, '"alt"', '"neu"', "  programs.mtr.enable = true;\n")
before = read(CO)

s, r = post("/api/config", co_form)
check("stilles Speichern meldet Erfolg", s == 200 and r.get("success"), r)
unchanged(CO, before, "/api/config")

s, r = post("/api/write", {"commit": False})
check("Schreiben meldet Erfolg", s == 200 and r.get("success"), r)
check("… und nichts geschrieben", "configuration.nix" not in r.get("written", []), r.get("written"))
unchanged(CO, before, "/api/write")

client.get("/api/config", headers=H)
unchanged(CO, before, "Formular laden")

print("\n   Von außen geändert, dann Formular laden")
ext = add_before_end(before, "  programs.iftop.enable = true;\n")
(cfg / CO).write_text(ext, encoding="utf-8")
client.get("/api/config", headers=H)
unchanged(CO, ext, "nach Änderung von außen")

print("\n   Raw speichern geht weiter")
raw_save(CO, '"neu"', '"neuer"', "")
check("Inhalt angekommen", '"neuer"' in read(CO) and "programs.mtr.enable" in read(CO))

# ── 2. Host-Datei ───────────────────────────────────────────────────────────
print("\n2. Host-Datei im Raw-Modus")
s, _ = post("/api/file/panel-mode", {"path": HOST, "mode": "r"})
check("Raw-Modus an", s == 200)
raw_save(HOST, '"h1"', '"h1raw"', "  programs.iftop.enable = true;\n")
before = read(HOST)

s, r = post("/api/host/h1/config", host_form)
check("stilles Speichern meldet Erfolg", s == 200 and r.get("success"), r)
unchanged(HOST, before, "/api/host/h1/config")

s, r = post("/api/host/h1/write", {"commit": False})
check("Schreiben meldet Erfolg", s == 200 and r.get("success"), r)
unchanged(HOST, before, "/api/host/h1/write")

# ── 3. Panel-Modus: Formular schreibt wie bisher ────────────────────────────
print("\n3. Zurück im Panel-Modus")
s, _ = post("/api/file/panel-mode", {"path": CO, "mode": "p"})
check("Panel-Modus an", s == 200)
form = client.get("/api/config", headers=H).get_json()
form.update({"_co_ready": True, "_co_path": CO})
check("Formular liest den Raw-Stand", form.get("hostname") == "neuer", form.get("hostname"))
form["hostname"] = "panel"
s, r = post("/api/config", form)
check("Speichern ok", s == 200 and r.get("success"), r)
text = read(CO)
check("Panel-Wert geschrieben", '"panel"' in text)
check("Raw-Zeile überlebt", "programs.mtr.enable" in text)

# ── 4. Home-Manager-Datei ───────────────────────────────────────────────────
print("\n4. Home-Manager-Datei im Raw-Modus")
s, r = post("/api/hm/create", {"username": "anna", "home_dir": "/home/anna",
                               "state_version": "25.05"})
check("HM-Datei erzeugt", s == 200 and r.get("success"), r)
HM = r.get("path", "home/anna.nix")
s, _ = post("/api/file/panel-mode", {"path": HM, "mode": "r"})
check("Raw-Modus an", s == 200)
before = read(HM)
s, r = post("/api/hm/patch", {"path": HM, "home_dir": "/home/anderswo"})
check("Patch meldet Erfolg", s == 200 and r.get("success"), r)
unchanged(HM, before, "/api/hm/patch")

print()
print("ALLE PRÜFUNGEN BESTANDEN" if not fails else f"FEHLGESCHLAGEN: {len(fails)}")
sys.exit(0 if not fails else 1)
