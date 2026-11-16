#!/usr/bin/env python3
"""Einstellungen außerhalb eines Nix-Bricks überleben das Speichern im Panel.

Befund 2026-11-16 (Claude Opus 5.5, manifest „Befund aus Schritt 0“): Was im
Raw-Modus oder von außen außerhalb eines Bricks eingetragen wurde und das
Formular nicht kennt, fiel beim nächsten Panel-Speichern weg.

  * configuration.nix nach Raw→Panel: Raw-Speichern setzt den Hash neu, die
    Umwandlung in load_and_normalize_config lief danach nie.
  * Host-Dateien: gar keine Umwandlung, also auch nach einer Änderung von
    außen verloren.

Geprüft über die echten Routen, so wie die Oberfläche sie ruft: Raw-Modus
an, Raw speichern, Panel an, Formular laden, speichern bzw. schreiben. Dazu
die Vorschau, zweimaliges Speichern und der Baustein core.keep_unmodelled
selbst (keine Doppelung nach einem Import, Namenskollision, nicht abbildbare
Datei).

Gegen den Stand vor dem Fix fallen die „überlebt“-Prüfungen.

Lauf: nix-shell shell.nix --run "python testing/routes/test_keep_unmodelled.py"
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
from nico.brix import extract_brick_blocks  # noqa: E402

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

def in_brick(text, needle):
    return any(needle in b["text"] for b in extract_brick_blocks(text).values())

def outside_bricks(text, needle):
    from nico.brix import strip_brick_blocks
    return needle in strip_brick_blocks(text)

CO = "configuration.nix"
HOST = "hosts/h1/default.nix"
STMT_CO = "  services.raweintrag.enable = true;\n"
STMT_HOST = "  services.hosteintrag.enable = true;\n"
STMT_EXT = "  services.externeintrag.enable = true;\n"

def co_form():
    data = client.get("/api/config", headers=H).get_json()
    data.update({"_co_ready": True, "_co_path": CO})
    return data

def host_form():
    data = client.get(f"/api/parse/co?path={HOST}", headers=H).get_json()
    data.update({"_co_ready": True, "_co_path": HOST})
    return data

def raw_edit(rel, stmt):
    """What the UI does: raw mode on, raw save, panel mode on."""
    s1, _ = post("/api/file/panel-mode", {"path": rel, "mode": "r"})
    s2, _ = post("/api/file", {"files": [{"path": rel, "content": add_before_end(read(rel), stmt)}]})
    s3, _ = post("/api/file/panel-mode", {"path": rel, "mode": "p"})
    return (s1, s2, s3)

# ── Ausgangslage: beide Dateien von NiCo erzeugt ─────────────────────────────
print("Ausgangslage")
s, r = post("/api/config", dict(config_manager.get_defaults(), _co_ready=True, _co_path=CO))
check("configuration.nix erzeugt", s == 200 and r.get("success"), r)
s, r = post("/api/host/h1/config", {"_co_ready": True, "_co_path": HOST, "hostname": "h1"})
check("Host-Datei erzeugt", s == 200 and r.get("success"), r)

# ── 1. configuration.nix: Raw → Panel → Speichern ───────────────────────────
print("\n1. configuration.nix: Raw → Panel → Speichern")
check("Raw-Weg der Oberfläche läuft", raw_edit(CO, STMT_CO) == (200, 200, 200))
s, r = post("/api/config", co_form())
check("Speichern ok", s == 200 and r.get("success"), r)
text = read(CO)
check("Einstellung überlebt", "services.raweintrag.enable" in text)
check("… als Nix-Brick", in_brick(text, "services.raweintrag.enable"))
check("… genau einmal", text.count("services.raweintrag.enable") == 1, text.count("services.raweintrag.enable"))

print("\n   Zweimal speichern")
before = read(CO)
post("/api/config", co_form())
post("/api/config", co_form())
check("Datei bleibt gleich", read(CO) == before)

print("\n   Schreiben (/api/write)")
check("Raw-Weg der Oberfläche läuft", raw_edit(CO, STMT_EXT) == (200, 200, 200))
s, r = post("/api/write", {"commit": False})
check("Schreiben ok", s == 200 and r.get("success"), r)
text = read(CO)
check("Einstellung überlebt", "services.externeintrag.enable" in text)
check("… als Nix-Brick", in_brick(text, "services.externeintrag.enable"))
check("frühere bleibt", text.count("services.raweintrag.enable") == 1)

print("\n   Vorschau")
# Raw edit with a fresh hash, as raw save leaves it: the form load does not
# convert it, so only the preview itself can show the statement.
edited = add_before_end(read(CO), "  services.vorschau.enable = true;\n")
(cfg / CO).write_text(core.set_type_in_content(edited, core.get_nico_type(edited)), encoding="utf-8")
s, r = post("/api/preview", dict(co_form(), _path=CO))
check("Vorschau zeigt die Einstellung", "services.vorschau.enable" in (r.get("configuration_nix") or ""))
s, r = post("/api/config", co_form())
check("gespeichert steht sie auch da", "services.vorschau.enable" in read(CO))

# ── 2. Host-Datei: Raw → Panel und von außen ────────────────────────────────
print("\n2. Host-Datei: Raw → Panel → Speichern")
check("Raw-Weg der Oberfläche läuft", raw_edit(HOST, STMT_HOST) == (200, 200, 200))
s, r = post("/api/host/h1/config", host_form())
check("Speichern ok", s == 200 and r.get("success"), r)
text = read(HOST)
check("Einstellung überlebt", "services.hosteintrag.enable" in text)
check("… als Nix-Brick", in_brick(text, "services.hosteintrag.enable"))

print("\n   Host-Datei von außen geändert (ohne neuen Hash)")
(cfg / HOST).write_text(add_before_end(read(HOST), "  services.hostextern.enable = true;\n"),
                        encoding="utf-8")
s, r = post("/api/host/h1/config", host_form())
text = read(HOST)
check("Einstellung überlebt", "services.hostextern.enable" in text)
check("… als Nix-Brick", in_brick(text, "services.hostextern.enable"))

print("\n   Host schreiben (/api/host/h1/write)")
(cfg / HOST).write_text(add_before_end(read(HOST), "  services.hostwrite.enable = true;\n"),
                        encoding="utf-8")
s, r = post("/api/host/h1/write", {"commit": False})
check("Schreiben ok", s == 200 and r.get("success"), r)
text = read(HOST)
check("Einstellung überlebt", "services.hostwrite.enable" in text and
      in_brick(text, "services.hostwrite.enable"))
check("frühere bleiben, je einmal",
      text.count("services.hosteintrag.enable") == 1 and text.count("services.hostextern.enable") == 1)

print("\n   Zweimal speichern")
before = read(HOST)
post("/api/host/h1/config", host_form())
post("/api/host/h1/config", host_form())
check("Datei bleibt gleich", read(HOST) == before)

# ── 3. Der Baustein selbst ──────────────────────────────────────────────────
print("\n3. core.keep_unmodelled")
disk = "{ config, pkgs, ... }:\n{\n  services.foo.enable = true;\n}\n"
first = core.keep_unmodelled(disk, {})
check("Unbekanntes wird ein Brick", len(first) == 1 and
      "services.foo.enable" in next(iter(first.values()))["text"], first)
again = core.keep_unmodelled(disk, first)
check("schon als Brick vorhanden: keine Doppelung (Import-Weg)", again.keys() == first.keys(), list(again))
name = next(iter(first))
other = {name: {"section": "HEAD", "order": 1,
                "text": f"# <brick: HEAD / #1 {name}>\n  networking.foo = 1;\n# </brick: {name}>"}}
merged = core.keep_unmodelled(disk, other)
check("Namenskollision: alter Brick bleibt", "networking.foo" in merged[name]["text"])
new = [k for k in merged if k != name]
check("… neuer bekommt eigenen Namen", len(new) == 1 and
      "services.foo.enable" in merged[new[0]]["text"] and
      f"<brick: HEAD / " in merged[new[0]]["text"] and new[0] in merged[new[0]]["text"], merged)
unm = "{ config, ... }: let a = 1; in { services.foo.enable = true; }\n"
check("nicht abbildbare Datei: nichts dazu", core.keep_unmodelled(unm, {}) == {})
check("leere Platte: nichts dazu", core.keep_unmodelled("", {"x": {"text": "t"}}) == {"x": {"text": "t"}})

print()
print("ALLE PRÜFUNGEN BESTANDEN" if not fails else f"FEHLGESCHLAGEN: {len(fails)}")
sys.exit(0 if not fails else 1)
