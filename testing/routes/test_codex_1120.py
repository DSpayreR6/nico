#!/usr/bin/env python3
"""Nachprüfung von Codex (GPT-6) am 2026-11-20: fünf teilweise offene Befunde.

Jeder Abschnitt prüft genau Codex' Abnahmekriterium (manifest.md,
„Nachprüfung vom 20.11.2026“):

 2  Wechsel Text → Panel mit geändertem Inhalt und gesperrtem Git-Index:
    Sicherungsfehler, Dateibytes bleiben. Im Erfolgsfall ist die Vorversion
    aus git zurückholbar.
 5  Hardware-Dateien in der Wurzel und in einem Verzeichnis `root/`: beide
    einzeln rückholbar.
 6  ZIP-Sicherung: auch `nico.json` als Link nach außen bleibt draußen und
    steht im Archivkommentar; eine normale `nico.json` bleibt drin.
20  stateVersion hinter einer Anweisung oder in /* … */ auskommentiert gilt
    nicht; eine aktive Definition mit Kommentar dahinter zählt.
29  Roundtrip-Test meldet eine geänderte Gruppenliste in einer Host-Datei
    mit Datei und vollem Pfad.

Lauf: nix-shell shell.nix --run "python testing/routes/test_codex_1120.py"
"""
import json, os, re, subprocess, sys, tempfile, zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "testing"))

xdg = Path(tempfile.mkdtemp(prefix="nico-xdg-"))
os.environ["XDG_CONFIG_HOME"] = str(xdg)
cfg = Path(tempfile.mkdtemp(prefix="nico-cfg-"))
(cfg / "config.json").write_text(json.dumps({"hosts_dir": "hosts"}), encoding="utf-8")
(xdg / "nico").mkdir(parents=True)
(xdg / "nico" / "settings.json").write_text(
    json.dumps({"nixos_config_dir": str(cfg), "language": "de", "git_sync": False}),
    encoding="utf-8")

from nico import config_manager, core, generator, git_manager, hardware, importer, server, validator  # noqa: E402

fails = []
def check(label, cond, got=None):
    print(("  OK   " if cond else "  FAIL ") + label + ("" if cond or got is None else f"  (war: {got})"))
    if not cond: fails.append(label)

client = server.create_app().test_client()
page = client.get("/").get_data(as_text=True)
csrf = re.search(r'<meta name="csrf-token" content="([^"]+)"', page).group(1)
H = {"X-CSRF-Token": csrf, "Content-Type": "application/json"}

# ── 2 ───────────────────────────────────────────────────────────────────────
print("2. Text → Panel: erst sichern, dann schreiben")
data = config_manager.get_defaults()
co = cfg / "configuration.nix"
co.write_text(generator.generate_configuration_nix(data), encoding="utf-8")
ok, _ = git_manager.init_repo(str(cfg))
check("Git eingerichtet", ok)
git_manager.auto_commit(str(cfg), label="start")
r = client.post("/api/file/panel-mode", headers=H, data=json.dumps({"path": "configuration.nix", "mode": "r"}))
check("auf Text geschaltet", r.status_code == 200, r.get_json())
git_manager.auto_commit(str(cfg), label="raw")
# A change on disk that is in no commit yet, then the index locked.
co.write_text(co.read_text(encoding="utf-8").replace("}", "  # aussen\n}", 1), encoding="utf-8")
before = co.read_bytes()
lock = cfg / ".git" / "index.lock"
lock.write_text("", encoding="utf-8")
edited = before.decode("utf-8").replace("# aussen", "# aus dem Editor")
r = client.post("/api/file/panel-mode", headers=H, data=json.dumps(
    {"path": "configuration.nix", "mode": "r", "content": edited}))
body = r.get_json() or {}
check("gesperrter Index: Sicherungsfehler", r.status_code == 500
      and body.get("error") == "ERR_RESTRUCTURE_BACKUP", (r.status_code, body))
check("Dateibytes unverändert", co.read_bytes() == before)
lock.unlink()
r = client.post("/api/file/panel-mode", headers=H, data=json.dumps(
    {"path": "configuration.nix", "mode": "r", "content": edited}))
check("ohne Sperre: geschrieben", r.status_code == 200 and "# aus dem Editor" in co.read_text(encoding="utf-8"),
      r.get_json())
revs = subprocess.run(["git", "rev-list", "-n", "5", "HEAD"], cwd=cfg, capture_output=True, text=True).stdout.split()
held = any(subprocess.run(["git", "show", f"{rev}:configuration.nix"], cwd=cfg, capture_output=True).stdout == before
           for rev in revs)
check("Vorversion aus git zurückholbar", held)

# ── 5 ───────────────────────────────────────────────────────────────────────
print("\n5. Wurzel und Verzeichnis root/")
check("verschiedene Namen", hardware._stash_name(None, Path("hardware-configuration.nix"))
      != hardware._stash_name(None, Path("root/hardware-configuration.nix")))
hw_top, hw_root = b"{ ... }: { boot.kernelModules = [ \"top\" ]; }\n", b"{ ... }: { boot.kernelModules = [ \"rootdir\" ]; }\n"
(cfg / "hardware-configuration.nix").write_bytes(hw_top)
(cfg / "root").mkdir()
(cfg / "root" / "hardware-configuration.nix").write_bytes(hw_root)
real = hardware.classify
hardware.classify = lambda path, ids=None: (hardware.UNKNOWN, [], [])
try:
    kept = hardware.stash_hw_files(str(cfg))
finally:
    hardware.classify = real
by_origin = {Path(e["origin"]).parent.name: e["file"] for e in kept}
stash = config_manager.HW_STASH_DIR
check("beide gesichert", len(kept) == 2, kept)
check("beide Inhalte einzeln rückholbar",
      len(set(by_origin.values())) == 2
      and {(stash / f).read_bytes() for f in by_origin.values()} == {hw_top, hw_root}, by_origin)
(cfg / "hardware-configuration.nix").unlink()
(cfg / "root" / "hardware-configuration.nix").unlink()

# ── 6 ───────────────────────────────────────────────────────────────────────
print("\n6. nico.json in der ZIP-Sicherung")
(cfg / "nico.json").write_text('{"x": 1}', encoding="utf-8")
name = importer.backup_to_zip(cfg)
with zipfile.ZipFile(cfg / name) as zf:
    check("normale nico.json im Archiv", "nico.json" in zf.namelist())
(cfg / name).unlink()
(cfg / "nico.json").unlink()
secret = Path(tempfile.mkdtemp()) / "geheim.json"
secret.write_text('{"geheim": true}', encoding="utf-8")
(cfg / "nico.json").symlink_to(secret)
name = importer.backup_to_zip(cfg)
with zipfile.ZipFile(cfg / name) as zf:
    check("Link nach außen nicht im Archiv", "nico.json" not in zf.namelist())
    check("… im Archivkommentar genannt", "nico.json" in zf.comment.decode("utf-8"), zf.comment)
(cfg / name).unlink()
(cfg / "nico.json").unlink()

# ── 20 ──────────────────────────────────────────────────────────────────────
print("\n20. Auskommentierte stateVersion")
def sites(text):
    co.write_text(text, encoding="utf-8")
    return validator._state_version_sites(str(cfg), None)
check("hinter einer Anweisung auskommentiert: zählt nicht",
      sites('{ ... }: {\n  a = 1; # system.stateVersion = "24.11";\n}\n') == [])
check("in /* … */: zählt nicht",
      sites('{ ... }: {\n  /*\n  system.stateVersion = "24.11";\n  */\n}\n') == [])
got = sites('{ ... }: {\n  system.stateVersion = "24.11"; # bei der Installation\n}\n')
check("aktiv mit Kommentar dahinter: zählt, Zeile stimmt",
      len(got) == 1 and got[0][0] == "24.11" and got[0][2] == 2, got)

# ── 29 ──────────────────────────────────────────────────────────────────────
print("\n29. Roundtrip-Test sieht Host-Dateien")
import run as roundtrip  # testing/run.py
orig = Path(tempfile.mkdtemp(prefix="rt-orig-"))
res = Path(tempfile.mkdtemp(prefix="rt-res-"))
for base, groups in ((orig, "[ ]"), (res, '[ "wheel" ]')):
    (base / "hosts" / "h1").mkdir(parents=True)
    (base / "hosts" / "h1" / "default.nix").write_text(
        "{ ... }: {\n  users.users.anna.extraGroups = %s;\n}\n" % groups, encoding="utf-8")
    (base / "modules").mkdir()
    # the same attribute name elsewhere must not hide the change
    (base / "modules" / "other.nix").write_text(
        '{ ... }: {\n  users.users.bob.extraGroups = [ "wheel" ];\n}\n', encoding="utf-8")
case = roundtrip.CaseResult(name="host-groups")
roundtrip._check_fidelity(orig, res, case)
hit = [e for e in case.errors if "hosts/h1/default.nix" in e and "users.users.anna.extraGroups" in e]
check("Abweichung mit Datei und vollem Pfad gemeldet", hit, case.errors)

print()
if fails:
    print(f"{len(fails)} FEHLER")
    sys.exit(1)
print("Alle Prüfungen bestanden.")
