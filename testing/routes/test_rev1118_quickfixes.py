#!/usr/bin/env python3
"""Revision 2026-11-18 (Codex): die Befunde, die ohne Besprechung zu beheben waren.

7 ZIP-Sicherungen derselben Sekunde, 8 Formatfreigabe über einen
Config-Wechsel, 19 HM-Patch mit maskiertem Anführungszeichen, 20
auskommentierte stateVersion, 21 Fingerabdruck ohne gemeinsame Module,
22 Flake-Host ohne default.nix, 24 Git-Rücksicherung mit Mischstand,
30 doppelte Definition beim Formular-Speichern, 31 Lesefehler vor dem
Speichern. 10 und 27 (fehlende Übersetzungen) prüft test_lang_integrity.

Lauf: nix-shell shell.nix --run "python testing/routes/test_rev1118_quickfixes.py"
"""
import json, os, re, sys, tempfile, time
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

xdg = Path(tempfile.mkdtemp(prefix="nico-xdg-"))
os.environ["XDG_CONFIG_HOME"] = str(xdg)
cfg = Path(tempfile.mkdtemp(prefix="nico-cfg-"))
(cfg / "config.json").write_text(json.dumps({"hosts_dir": "hosts"}), encoding="utf-8")
(xdg / "nico").mkdir(parents=True)
(xdg / "nico" / "settings.json").write_text(
    json.dumps({"nixos_config_dir": str(cfg), "language": "de", "git_sync": False}),
    encoding="utf-8")

from nico import config_manager, core, generator, git_manager, importer, server, validator  # noqa: E402
from nico.brix import extract_brick_blocks  # noqa: E402

fails = []
def check(label, cond, got=None):
    print(("  OK   " if cond else "  FAIL ") + label + ("" if cond or got is None else f"  (war: {got})"))
    if not cond: fails.append(label)

def tmp():
    d = Path(tempfile.mkdtemp(prefix="nico-q-"))
    (d / "config.json").write_text(json.dumps({"hosts_dir": "hosts"}), encoding="utf-8")
    return d

# ── 7 ────────────────────────────────────────────────────────────────────────
print("7 – zwei ZIP-Sicherungen in derselben Sekunde")
d = tmp()
(d / "a.nix").write_text("{ a = 1; }\n", encoding="utf-8")
fixed = time.localtime()
with mock.patch("time.localtime", return_value=fixed):
    z1 = importer.backup_to_zip(d)
    (d / "a.nix").write_text("{ a = 2; }\n", encoding="utf-8")
    z2 = importer.backup_to_zip(d)
check("zwei verschiedene Namen", z1 != z2, (z1, z2))
check("beide liegen da", (d / z1).exists() and (d / z2).exists())

# ── 19 ───────────────────────────────────────────────────────────────────────
print("\n19 – HM-Patch mit maskiertem Anführungszeichen im alten Wert")
t = 'home.homeDirectory = "/home/a\\"b";\n'
out = core.hm_patch_str(t, "home.homeDirectory", "/home/neu")
check("alter Wert ganz ersetzt", out == 'home.homeDirectory = "/home/neu";\n', out)

# ── 20 ───────────────────────────────────────────────────────────────────────
print("\n20 – auskommentierte stateVersion zählt nicht")
d = tmp()
(d / "configuration.nix").write_text(
    '{ ... }:\n{\n  # system.stateVersion = "24.11";\n  networking.hostName = "x";\n}\n',
    encoding="utf-8")
check("keine Fundstelle", validator._state_version_sites(str(d), None) == [],
      validator._state_version_sites(str(d), None))
(d / "configuration.nix").write_text(
    '{ ... }:\n{\n  system.stateVersion = "24.11";\n}\n', encoding="utf-8")
check("aktive Zeile wird gefunden", len(validator._state_version_sites(str(d), None)) == 1)

# ── 21 ───────────────────────────────────────────────────────────────────────
print("\n21 – Fingerabdruck sieht gemeinsame Module")
d = tmp()
(d / "configuration.nix").write_text("{ imports = [ ./modules/shared.nix ]; }\n", encoding="utf-8")
(d / "modules").mkdir()
(d / "modules" / "shared.nix").write_text("{ a = 1; }\n", encoding="utf-8")
(d / "hosts" / "h1").mkdir(parents=True)
(d / "hosts" / "h2").mkdir(parents=True)
(d / "hosts" / "h1" / "default.nix").write_text("{ }\n", encoding="utf-8")
(d / "hosts" / "h2" / "default.nix").write_text("{ }\n", encoding="utf-8")
fp = validator._host_fingerprint if hasattr(validator, "_host_fingerprint") else None
name = next(n for n in dir(validator) if "fingerprint" in n.lower() and callable(getattr(validator, n)))
fp = getattr(validator, name)
a = fp(str(d), "h1")
(d / "modules" / "shared.nix").write_text("{ a = 2; }\n", encoding="utf-8")
check("Modul geändert → anderer Fingerabdruck", fp(str(d), "h1") != a)
b = fp(str(d), "h1")
(d / "hosts" / "h2" / "default.nix").write_text("{ b = 1; }\n", encoding="utf-8")
check("fremder Host geändert → gleicher Fingerabdruck", fp(str(d), "h1") == b)

# ── 30, 31 ───────────────────────────────────────────────────────────────────
print("\n30 – Formular-Speichern mit doppelter Definition wird abgelehnt")
d = tmp()
base = dict(config_manager.get_defaults(), hostname="kiste")
config_manager.save_config(str(d), base)
text = (d / "configuration.nix").read_text(encoding="utf-8")
# a brick setting what the form also writes
text = text.replace("\n}\n", '\n# <brick: TAIL / #1 eigen>\n  networking.hostName = "brick";\n# </brick: eigen>\n}\n')
(d / "configuration.nix").write_text(text, encoding="utf-8")
before = (d / "configuration.nix").read_text(encoding="utf-8")
try:
    config_manager.save_config(str(d), dict(base, brick_blocks=extract_brick_blocks(before)))
    raised = None
except config_manager.DuplicateAttrError as exc:
    raised = exc.names
check("DuplicateAttrError", bool(raised), raised)
check("Datei unverändert", (d / "configuration.nix").read_text(encoding="utf-8") == before)

print("\n31 – Lesefehler vor dem Speichern bricht ab")
d = tmp()
config_manager.save_config(str(d), base)
t = (d / "configuration.nix").read_text(encoding="utf-8")
t = t.replace("\n}\n", '\n# <brick: TAIL / #1 meins>\n  services.meins.enable = true;\n# </brick: meins>\n}\n')
(d / "configuration.nix").write_text(t, encoding="utf-8")
real_read = Path.read_text
def flaky(self, *a, **k):
    if self.name == "configuration.nix" and str(self).startswith(str(d)):
        raise OSError("simulierter Lesefehler")
    return real_read(self, *a, **k)
with mock.patch.object(Path, "read_text", flaky):
    try:
        config_manager.save_config(str(d), dict(base))
        raised = False
    except OSError:
        raised = True
check("Fehler kommt an", raised)
check("Brick steht noch in der Datei", "services.meins.enable" in real_read(d / "configuration.nix"))

# ── 8, 22 über die Routen ────────────────────────────────────────────────────
client = server.create_app().test_client()
page = client.get("/").get_data(as_text=True)
csrf = re.search(r'<meta name="csrf-token" content="([^"]+)"', page).group(1)
H = {"X-CSRF-Token": csrf, "Content-Type": "application/json"}
def post(url, body):
    r = client.post(url, headers=H, data=json.dumps(body))
    return r.status_code, (r.get_json(silent=True) or {})

print("\n8 – Formatfreigabe gilt nur für die Config, in der sie gegeben wurde")
from nico.brix import FILE_FORMAT  # noqa: E402
newer = f"{{ ... }}:\n# nico-version: co#00000000#f{FILE_FORMAT + 5}\n{{ }}\n"
(cfg / "configuration.nix").write_text(newer, encoding="utf-8")
s, r = post("/api/config/settings", {})
check("Config A: gesperrt", r.get("error") == "ERR_FORMAT_TOO_NEW", (s, r))
s, r = post("/api/format/proceed", {})
check("Config A: freigegeben", s == 200, (s, r))
cfg_b = tmp()
(cfg_b / "configuration.nix").write_text(newer, encoding="utf-8")
(xdg / "nico" / "settings.json").write_text(
    json.dumps({"nixos_config_dir": str(cfg_b), "language": "de", "git_sync": False}),
    encoding="utf-8")
s, r = post("/api/config/settings", {})
check("Config B: wieder gesperrt", r.get("error") == "ERR_FORMAT_TOO_NEW", (s, r))
(xdg / "nico" / "settings.json").write_text(
    json.dumps({"nixos_config_dir": str(cfg), "language": "de", "git_sync": False}),
    encoding="utf-8")

print("\n22 – Flake-Host: Fehler beim Anlegen der default.nix kommt an")
(cfg / "configuration.nix").unlink()
(cfg / "flake.nix").write_text(generator.generate_flake_nix(
    dict(config_manager.get_defaults(), flakes=True), nixos_dir=str(cfg)), encoding="utf-8")
flake_before = (cfg / "flake.nix").read_text(encoding="utf-8")
with mock.patch.object(config_manager, "save_host_config", side_effect=OSError("voll")):
    s, r = post("/api/flake/host/add", {"name": "neu"})
check("Fehler statt Erfolg", s == 500 and r.get("error") == "ERR_FILE_WRITE", (s, r))
check("flake.nix unverändert", (cfg / "flake.nix").read_text(encoding="utf-8") == flake_before)

# ── 24 ───────────────────────────────────────────────────────────────────────
print("\n24 – Git-Rücksicherung: Fehler beim Entfernen ist kein Erfolg")
d = tmp()
(d / "a.nix").write_text("{ a = 1; }\n", encoding="utf-8")
git_manager.init_repo(str(d))
git_manager.auto_commit(str(d), label="eins")
first = git_manager.get_log(str(d), 1)[0]["hash"]
(d / "b.nix").write_text("{ b = 1; }\n", encoding="utf-8")
git_manager.auto_commit(str(d), label="zwei")
real_run = git_manager._run
def failing_rm(args, *a, **k):
    if args[:1] == ["rm"]:
        return 128, "fatal: index.lock"
    return real_run(args, *a, **k)
fn = next(n for n in ("rollback_to_commit", "restore_commit", "rollback") if hasattr(git_manager, n))
with mock.patch.object(git_manager, "_run", failing_rm):
    ok, msg = getattr(git_manager, fn)(str(d), first)
check("meldet keinen Erfolg", ok is False, (ok, msg))
check("… als Teil-Rücksicherung", "ERR_PARTIALLY_RESTORED" in str(msg), msg)

print()
if fails:
    print(f"{len(fails)} FEHLER:", *fails, sep="\n  ")
    sys.exit(1)
print("Alle Prüfungen bestanden.")
