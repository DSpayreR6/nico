#!/usr/bin/env python3
"""Außerhalb von NiCo geändert: erst fragen, dann umbauen (Fall A).

Entscheidung 2026-11-18 (Martin): Jede Datei, die ein Formular schreibt,
trägt eine Prüfsumme. Stimmt sie nicht oder fehlt die Versionszeile, hat
NiCo den Stand auf Platte nicht geschrieben. Bisher baute NiCo solche
Dateien still um – configuration.nix und flake.nix schon beim GET
/api/config, Host- und Home-Manager-Dateien beim nächsten Speichern, deren
Prüfsumme wurde nicht einmal gelesen. Jetzt meldet /api/file `outside`, die
Oberfläche fragt: Umbauen (Sicherungspunkt bzw. ZIP vorher), Als Raw öffnen,
Unterschiede ansehen.

Lauf: nix-shell shell.nix --run "python testing/routes/test_outside_restructure.py"
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

from nico import config_manager, core, generator, git_manager, server  # noqa: E402
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

def get(url):
    r = client.get(url, headers=H)
    return r.status_code, (r.get_json(silent=True) or {})

def read(rel):
    return (cfg / rel).read_text(encoding="utf-8")

def edit_outside(rel, line):
    """An editor appends a setting and a comment; the hash stays as it was."""
    t = read(rel)
    i = t.rstrip().rfind("}")
    (cfg / rel).write_text(t[:i] + line + t[i:], encoding="utf-8")

def in_brick(rel, needle):
    return any(needle in b["text"] for b in extract_brick_blocks(read(rel)).values())

CO, HOST, FL = "configuration.nix", "hosts/h1/default.nix", "flake.nix"

print("Ausgangslage")
s, r = post("/api/config", dict(config_manager.get_defaults(), hostname="kiste",
                                _co_ready=True, _co_path=CO))
check("configuration.nix erzeugt", s == 200 and r.get("success"), r)
s, r = post("/api/host/h1/config", {"_co_ready": True, "_co_path": HOST, "hostname": "h1"})
check("Host-Datei erzeugt", s == 200 and r.get("success"), r)
s, r = post("/api/hm/create", {"username": "anna", "home_dir": "/home/anna",
                               "state_version": "25.05"})
check("HM-Datei erzeugt", s == 200 and r.get("success"), r)
HM = r.get("path", "home/anna.nix")
ok, _ = git_manager.init_repo(str(cfg))
check("Git eingerichtet", ok)
git_manager.auto_commit(str(cfg), label="start")

print("\n1. Von NiCo geschriebene Dateien: nichts zu fragen")
for rel in (CO, HOST, HM):
    s, r = get(f"/api/file?path={rel}")
    check(f"{rel}: outside leer", s == 200 and r.get("outside") is None, r.get("outside"))
s, r = post("/api/file/restructure", {"path": CO})
check("Umbauen einer unveränderten Datei tut nichts", s == 200 and r.get("backup") is None, r)
for rel in (CO, HOST):
    s, r = get(f"/api/file/panel-check?path={rel}")
    check(f"{rel}: Panel würde nichts ändern", r.get("changes") is False, r)

# ── 2. configuration.nix ────────────────────────────────────────────────────
print("\n2. configuration.nix außen geändert")
edit_outside(CO, "  # von Hand\n  services.vonhand.enable = true;\n")
before = read(CO)
s, r = get(f"/api/file?path={CO}")
check("/api/file meldet modified", r.get("outside") == "modified", r.get("outside"))
check("Lesen schreibt nichts", read(CO) == before)
s, r = get("/api/config")
check("GET /api/config schreibt nichts mehr", s == 200 and read(CO) == before)
s, r = get(f"/api/file/restructure-diff?path={CO}")
added = [h["content"] for f in r.get("files", []) for h in f["hunks"] if h["type"] == "added"]
check("Unterschiede: der neue Brick erscheint", any("<brick:" in a for a in added), added[:6])
check("Unterschiede: Datei dabei unverändert", read(CO) == before)
s, r = post("/api/file/restructure", {"path": CO})
check("Umbauen ok, Sicherung über git", s == 200 and r.get("backup") == "git", r)
log = [c["message"] for c in git_manager.get_log(str(cfg), 5)]
check("Sicherungspunkt mit dem Stand von außen",
      any(m.startswith(f"NiCo: backup before restructuring {CO}") for m in log), log)
backup = next(c for c in git_manager.get_log(str(cfg), 5)
              if c["message"].startswith("NiCo: backup before restructuring"))
_rc, old = git_manager._run(["show", f"{backup['hash']}:{CO}"], cwd=str(cfg))
check("… der Sicherungspunkt enthält die Datei wie von außen", old.strip() == before.strip())
check("Einstellung steht jetzt in einem Nix-Brick", in_brick(CO, "services.vonhand.enable"))
# Directly above a statement that becomes a brick: moves with it (086e7c8).
check("Kommentar steht mit im Brick", in_brick(CO, "# von Hand"))
check("Prüfsumme stimmt wieder", generator.check_version_hash(read(CO)) == "ok")
s, r = get(f"/api/file?path={CO}")
check("danach nichts mehr zu fragen", r.get("outside") is None, r.get("outside"))

# ── 3. Host-Datei ───────────────────────────────────────────────────────────
print("\n3. Host-Datei außen geändert (Prüfsumme wurde bisher nie gelesen)")
edit_outside(HOST, "  services.hostvonhand.enable = true;\n")
s, r = get(f"/api/file?path={HOST}")
check("/api/file meldet modified", r.get("outside") == "modified", r.get("outside"))
s, r = post("/api/file/restructure", {"path": HOST})
check("Umbauen ok", s == 200 and r.get("success"), r)
check("Einstellung im Nix-Brick", in_brick(HOST, "services.hostvonhand.enable"))
check("Prüfsumme stimmt", generator.check_version_hash(read(HOST)) == "ok")

# ── 4. Home-Manager-Datei ───────────────────────────────────────────────────
print("\n4. Home-Manager-Datei außen geändert")
edit_outside(HM, "  # mein Dienst\n  services.hmvonhand.enable = true;\n")
before = read(HM)
s, r = get(f"/api/file?path={HM}")
check("/api/file meldet modified", r.get("outside") == "modified", r.get("outside"))
check("Inhalt kommt ungeändert (nicht schon normalisiert)", r.get("content") == before)
s, r = post("/api/file/restructure", {"path": HM})
check("Umbauen ok", s == 200 and r.get("success"), r)
check("Einstellung im Nix-Brick", in_brick(HM, "services.hmvonhand.enable"))
check("Kommentar in imported-notes", "mein Dienst" in read(HM) and "imported-notes" in read(HM))

# ── 5. Als Raw öffnen ───────────────────────────────────────────────────────
print("\n5. Als Raw öffnen: nur #r, sonst bleibt alles")
edit_outside(HOST, "  services.rawbleibt.enable = true;\n")
before = read(HOST)
s, r = post("/api/file/panel-mode", {"path": HOST, "mode": "r"})
check("Raw-Modus gesetzt", s == 200 and r.get("ok"), r)
strip = lambda t: [l for l in t.splitlines() if not l.startswith("# nico-version:")]
check("Inhalt sonst unverändert", strip(read(HOST)) == strip(before))
check("… die Einstellung steht nicht in einem Brick", not in_brick(HOST, "services.rawbleibt"))
s, r = get(f"/api/file?path={HOST}")
check("im Raw-Modus nichts zu fragen", r.get("outside") is None, r.get("outside"))

# ── 5b. Raw-Modus: Meldung und Umschalten ───────────────────────────────────
print("\n5b. Raw-Modus: außen geändert = nur Meldung; Raw-Änderung vor dem Panel")
edit_outside(HOST, "  services.rawaussen.enable = true;\n")
s, r = get(f"/api/file?path={HOST}")
check("im Raw-Modus meldet /api/file outside (für die Meldung)", r.get("outside") == "modified",
      r.get("outside"))
check("… und panel_mode r", r.get("panel_mode") == "r", r.get("panel_mode"))
t = read(HOST)
i = t.rstrip().rfind("}")
s, r = post("/api/file", {"files": [{"path": HOST, "content": t[:i] + ".\n" + t[i:]}]})
check("Raw speichern mit verirrtem Punkt", s == 200 and r.get("success"), r)
s, r = get(f"/api/file?path={HOST}")
check("Raw-Speichern setzt die Prüfsumme: outside leer", r.get("outside") is None, r.get("outside"))
# Since 2026-11-20 (Martin, rule A): a file Nix cannot read is raw-only, with
# the reason and the line. Before, restructuring dropped the stray dot -
# harmless here, but for a real syntax error it would discard text that
# meant something. The user fixes it in raw mode.
check("Syntaxfehler: nur Raw, mit Grund", r.get("unmappable") is True
      and (r.get("unmappable_reason") or {}).get("kind") == "parse", r.get("unmappable_reason"))
s, r = post("/api/file/panel-mode", {"path": HOST, "mode": "p"})
check("Panel-Schalter verweigert", s == 400 and r.get("error") == "ERR_NOT_MAPPABLE", (s, r))
check("Punkt steht noch in der Datei", "." in [l.strip() for l in read(HOST).splitlines()])
t = read(HOST)
i = t.rstrip().rfind("}")
fixed = t[:i].rstrip()
assert fixed.endswith(".")
s, r = post("/api/file", {"files": [{"path": HOST, "content": fixed[:-1].rstrip() + "\n" + t[i:]}]})
check("im Raw behoben und gespeichert", s == 200 and r.get("success"), r)
s, r = get(f"/api/file/panel-check?path={HOST}")
check("panel-check sieht die Raw-Änderung", r.get("changes") is True, r)
s, r = post("/api/file/panel-mode", {"path": HOST, "mode": "p"})
check("aufs Panel umgeschaltet", s == 200 and r.get("ok"), r)
before_restructure = read(HOST)
s, r = post("/api/file/restructure", {"path": HOST})
check("Umbauen nach dem Umschalten ok", s == 200 and r.get("success") and r.get("backup") == "git", r)
check("Raw-Einstellung als Nix-Brick erhalten", in_brick(HOST, "services.rawbleibt.enable"))
check("Punkt ist weg", "." not in [l.strip() for l in read(HOST).splitlines()])
# The state before restructuring was already committed by the raw save, so
# no extra labelled checkpoint is needed - what counts is that it is in git.
import subprocess as _sp
revs = _sp.run(["git", "rev-list", "-n", "10", "HEAD"], cwd=cfg, capture_output=True,
               text=True).stdout.split()
in_git = any(_sp.run(["git", "show", f"{rev}:{HOST}"], cwd=cfg, capture_output=True,
                     text=True).stdout == before_restructure for rev in revs)
check("Stand vor dem Umbauen liegt in einem Sicherungspunkt", in_git)

# ── 6. Ohne Versionszeile ───────────────────────────────────────────────────
print("\n6. configuration.nix ganz ohne Versionszeile (von Hand geschrieben)")
(cfg / CO).write_text('{ config, pkgs, ... }:\n{\n  networking.hostName = "hand";\n'
                      '  services.handdienst.enable = true;\n}\n', encoding="utf-8")
s, r = get(f"/api/file?path={CO}")
check("/api/file meldet no_hash", r.get("outside") == "no_hash", r.get("outside"))
check("und schreibt keinen Typ-Stempel", "nico-version" not in read(CO))

# ── 7. flake.nix ────────────────────────────────────────────────────────────
print("\n7. flake.nix außen geändert")
(cfg / FL).write_text(generator.generate_flake_nix(
    dict(config_manager.get_defaults(), flakes=True), nixos_dir=str(cfg)), encoding="utf-8")
s, r = get(f"/api/file?path={FL}")
check("frisch erzeugt: nichts zu fragen", r.get("outside") is None, r.get("outside"))
t = read(FL).replace("  outputs", "  # eigener Hinweis\n  outputs", 1)
(cfg / FL).write_text(t, encoding="utf-8")
s, r = get(f"/api/file?path={FL}")
check("/api/file meldet modified", r.get("outside") == "modified", r.get("outside"))
s, r = post("/api/file/restructure", {"path": FL})
check("Umbauen ok", s == 200 and r.get("success"), r)
check("Prüfsumme stimmt", generator.check_version_hash(read(FL)) == "ok")
check("Host-Brick nicht verdoppelt", read(FL).count("# <brick: HOST:") == 1,
      read(FL).count("# <brick: HOST:"))
s, r = get(f"/api/file/panel-check?path={FL}")
check("danach würde das Panel nichts ändern", r.get("changes") is False, r)
# Known gap, unchanged by Fall A: flake.nix has no comment rescue (manifest,
# plan 2026-11-16, step 8). The comment is in the checkpoint made before.
print("  HINWEIS Kommentar in flake.nix nach dem Umbau:",
      "erhalten" if "eigener Hinweis" in read(FL) else "weg (bekannte Lücke, steht im Sicherungspunkt)")

# ── 8. Ohne git: ZIP ────────────────────────────────────────────────────────
print("\n8. Ohne git: Sicherung als ZIP")
import shutil  # noqa: E402
shutil.rmtree(cfg / ".git")
# configuration.nix still has no version line from step 6
s, r = post("/api/file/restructure", {"path": CO})
check("Umbauen ok, Sicherung als ZIP", s == 200 and r.get("backup") == "zip" and r.get("zip"), r)
check("ZIP liegt im Config-Verzeichnis", bool(r.get("zip")) and (cfg / r["zip"]).exists())

print()
if fails:
    print(f"{len(fails)} FEHLER:", *fails, sep="\n  ")
    sys.exit(1)
print("Alle Prüfungen bestanden.")
