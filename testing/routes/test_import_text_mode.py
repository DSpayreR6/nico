#!/usr/bin/env python3
"""Import im Textmodus, 2026-11-22.

Martins Fund: Import, „nicht umbauen, im Textmodus öffnen“ – configuration.nix
war trotzdem schon umgebaut. Der Import baute sie (und flake.nix) ohne Frage
um; die Frage kam erst beim Öffnen. Jetzt fragt jeder Importweg vorher, wie
die Config bearbeitet werden soll (edit_mode), und im Textmodus ändert NiCo
keine Datei.

Geprüft wird:
  1. Generator: jede Panel-Datei trägt #p (sonst fiel sie nach dem ersten
     Panel-Speichern auf den Standard „Textmodus“ zurück).
  2. Textmodus-Regel: Markierung, sonst panel_default – Datei ohne
     Kopfzeile im Textmodus-Standard ist Textmodus, eine #p-Datei nicht.
  3. Import (Verzeichnis) mit edit_mode=r: alle .nix-Dateien Byte für Byte
     wie die Quelle, panel_default=r in config.json.
  4. Danach ändern nichts: categorize, GET /api/config, GET /api/file,
     Panel-Speichern (POST /api/config); Speichern im Textmodus schreibt
     genau den Text, ohne Kopfzeile und ohne ERR_MANAGED_FILE.
  5. Später aufs Panel umschalten: Datei bekommt eine Kopfzeile und gilt
     als Panel.
  6. ZIP-Import mit edit_mode=r: ebenfalls unverändert.
  7. Import mit edit_mode=p: configuration.nix wird wie bisher umgebaut,
     mit #p; panel_default=p.

Lauf: nix-shell shell.nix --run "python testing/routes/test_import_text_mode.py"
"""
import io
import json
import os
import re
import sys
import tempfile
import zipfile
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

from nico import config_manager, core, generator, server  # noqa: E402

fails = []


def check(label, cond, got=None):
    print(("  OK   " if cond else "  FAIL ") + label
          + ("" if cond or got is None else f"  (war: {got})"))
    if not cond:
        fails.append(label)


SOURCE = {
    "configuration.nix": (
        "# Meine Config – von Hand geschrieben\n"
        "{ config, pkgs, ... }:\n"
        "{\n"
        "  imports = [ ./hardware-configuration.nix ./modules/desktop.nix ];\n"
        "  networking.hostName = \"puristen-pc\";   # Name\n"
        "  time.timeZone = \"Europe/Berlin\";\n"
        "  environment.systemPackages = with pkgs; [ git vim ];\n"
        "  system.stateVersion = \"25.11\";\n"
        "}\n"),
    "hardware-configuration.nix": (
        "{ config, lib, ... }:\n{\n  boot.initrd.availableKernelModules = [ \"ahci\" ];\n}\n"),
    "modules/desktop.nix": (
        "{ pkgs, ... }:\n{\n  services.xserver.enable = true; # X\n}\n"),
}


def write_source() -> Path:
    src = Path(tempfile.mkdtemp(prefix="nico-src-"))
    for rel, text in SOURCE.items():
        (src / rel).parent.mkdir(parents=True, exist_ok=True)
        (src / rel).write_text(text, encoding="utf-8")
    return src


def unchanged(label):
    diffs = [rel for rel, text in SOURCE.items()
             if (cfg / rel).read_text(encoding="utf-8") != text]
    check(label, not diffs, diffs)


def reset_cfg():
    for p in sorted(cfg.rglob("*"), reverse=True):
        if p.is_file() and p.suffix in (".nix", ".lock", ".json", ".zip"):
            p.unlink()


# ── 1. Generator ─────────────────────────────────────────────────────────────
print("1. Generator")
text = generator.generate_configuration_nix(config_manager.get_defaults())
line = next(l for l in text.splitlines() if l.startswith("# nico-version:"))
check("Kopfzeile mit #p", re.match(r"# nico-version: co#[0-9a-f]{8}#p#f\d+$", line) is not None, line)
check("Prüfsumme stimmt trotz #p", generator.check_version_hash(text) == "ok",
      generator.check_version_hash(text))

# ── 2. Textmodus-Regel ───────────────────────────────────────────────────────
print("2. Textmodus-Regel")
config_manager.save_config_settings(cfg, {**config_manager.load_config_settings(cfg),
                                          "panel_default": "r"})
(cfg / "a.nix").write_text("{ }\n", encoding="utf-8")
(cfg / "b.nix").write_text(text, encoding="utf-8")
check("ohne Kopfzeile + Standard Text → Textmodus", core.file_in_raw_mode(cfg / "a.nix"))
check("#p + Standard Text → Panel", not core.file_in_raw_mode(cfg / "b.nix"))
config_manager.save_config_settings(cfg, {**config_manager.load_config_settings(cfg),
                                          "panel_default": "p"})
check("ohne Kopfzeile + Standard Panel → Panel", not core.file_in_raw_mode(cfg / "a.nix"))
(cfg / "a.nix").unlink()
(cfg / "b.nix").unlink()

# ── 3. Import im Textmodus ───────────────────────────────────────────────────
app = server.create_app()
client = app.test_client()
page = client.get("/").get_data(as_text=True)
m = re.search(r'<meta name="csrf-token" content="([^"]+)"', page)
token = m.group(1) if m else ""
hdr = {"X-CSRF-Token": token, "Content-Type": "application/json"}

print("3. Import (Verzeichnis) im Textmodus")
reset_cfg()
src = write_source()
r = client.post("/api/import/run", headers=hdr, data=json.dumps(
    {"config_path": str(src / "configuration.nix"), "confirmed": True, "edit_mode": "r"}))
d = r.get_json()
check("Import erfolgreich, edit_mode=r", r.status_code == 200 and d.get("success")
      and d.get("edit_mode") == "r", (r.status_code, d))
unchanged("alle Dateien unverändert nach Import")
check("panel_default=r in config.json",
      config_manager.load_config_settings(cfg).get("panel_default") == "r")

# ── 4. Danach ändert nichts ──────────────────────────────────────────────────
print("4. Danach ändert nichts")
r = client.post("/api/categorize", headers=hdr, data="{}")
check("categorize läuft", r.status_code == 200, r.status_code)
unchanged("unverändert nach categorize")
r = client.get("/api/config", headers=hdr)
check("GET /api/config läuft", r.status_code == 200, r.status_code)
unchanged("unverändert nach GET /api/config")
for rel in SOURCE:
    r = client.get(f"/api/file?path={rel}", headers={"X-CSRF-Token": token})
    check(f"GET /api/file {rel}: Text wie auf der Platte",
          r.status_code == 200 and r.get_json().get("content") == SOURCE[rel],
          (r.status_code, (r.get_json() or {}).get("content", "")[:60]))
unchanged("unverändert nach GET /api/file")
form = client.get("/api/config", headers=hdr).get_json()
form.update({"_co_ready": True, "_co_path": "configuration.nix", "hostname": "anders"})
r = client.post("/api/config", headers=hdr, data=json.dumps(form))
check("Panel-Speichern antwortet", r.status_code in (200, 409), (r.status_code, r.get_json()))
unchanged("unverändert nach Panel-Speichern")

edited = SOURCE["configuration.nix"].replace("git vim", "git vim htop")
r = client.post("/api/file", headers=hdr, data=json.dumps(
    {"files": [{"path": "configuration.nix", "content": edited}]}))
check("Speichern im Textmodus erlaubt (kein ERR_MANAGED_FILE)",
      r.status_code == 200 and r.get_json().get("success"), (r.status_code, r.get_json()))
check("geschrieben wird genau der Text, ohne Kopfzeile",
      (cfg / "configuration.nix").read_text(encoding="utf-8") == edited)
(cfg / "configuration.nix").write_text(SOURCE["configuration.nix"], encoding="utf-8")

# ── 5. Später aufs Panel umschalten ──────────────────────────────────────────
print("5. Aufs Panel umschalten")
r = client.post("/api/file/panel-mode", headers=hdr, data=json.dumps(
    {"path": "modules/desktop.nix", "mode": "p"}))
check("Umschalten erlaubt", r.status_code == 200 and r.get_json().get("ok"),
      (r.status_code, r.get_json()))
desk = (cfg / "modules/desktop.nix").read_text(encoding="utf-8")
check("danach Kopfzeile in der Datei", "# nico-version:" in desk, desk[:80])
check("danach Panel", not core.file_in_raw_mode(cfg / "modules/desktop.nix"))
(cfg / "modules/desktop.nix").write_text(SOURCE["modules/desktop.nix"], encoding="utf-8")
r = client.post("/api/file/panel-mode", headers=hdr, data=json.dumps(
    {"path": "modules/desktop.nix", "mode": "r"}))
check("„Im Textmodus öffnen“ bei Standard Text: Datei bleibt, wie sie ist",
      r.status_code == 200 and (cfg / "modules/desktop.nix").read_text(encoding="utf-8")
      == SOURCE["modules/desktop.nix"], (r.status_code, r.get_json()))

# ── 6. ZIP-Import im Textmodus ───────────────────────────────────────────────
print("6. ZIP-Import im Textmodus")
reset_cfg()
buf = io.BytesIO()
with zipfile.ZipFile(buf, "w") as zf:
    for rel, t in SOURCE.items():
        zf.writestr(rel, t)
r = client.post("/api/import/zip/apply", headers={"X-CSRF-Token": token},
                data={"file": (io.BytesIO(buf.getvalue()), "c.zip"),
                      "confirmed": "true", "edit_mode": "r"},
                content_type="multipart/form-data")
check("ZIP-Import erfolgreich", r.status_code == 200 and r.get_json().get("success"),
      (r.status_code, r.get_json()))
unchanged("alle Dateien unverändert nach ZIP-Import")

# ── 7. Import mit Panel ──────────────────────────────────────────────────────
print("7. Import mit Panel")
reset_cfg()
src = write_source()
r = client.post("/api/import/run", headers=hdr, data=json.dumps(
    {"config_path": str(src / "configuration.nix"), "confirmed": True, "edit_mode": "p"}))
check("Import erfolgreich", r.status_code == 200 and r.get_json().get("success"), r.get_json())
co = (cfg / "configuration.nix").read_text(encoding="utf-8")
check("configuration.nix umgebaut, mit #p",
      re.search(r"^# nico-version: co#[0-9a-f]{8}#p#f\d+$", co, re.MULTILINE) is not None,
      co[:200])
check("panel_default=p", config_manager.load_config_settings(cfg).get("panel_default") == "p")

print("\n" + ("ALLE PRÜFUNGEN BESTANDEN" if not fails else f"FEHLGESCHLAGEN: {fails}"))
sys.exit(1 if fails else 0)
