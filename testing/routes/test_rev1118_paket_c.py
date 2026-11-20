#!/usr/bin/env python3
"""Revision 2026-11-18, Paket C: Befunde 5, 6, 23 und 26.

5  Zwei Hardware-Dateien außerhalb von hosts/<h>/ teilen sich im
   Zwischenlager nicht mehr den Namen _root.nix.
6  ZIP-Sicherung und ZIP-Export packen keine Datei-Links, deren Ziel außerhalb
   des Config-Verzeichnisses liegt; Links innerhalb bleiben. Der Export nennt
   die ausgelassenen Dateien (Kopfzeile für den Hinweis), die Sicherung im
   Archivkommentar.
23 Der letzte Flake-Host lässt sich nicht löschen (früher: Erfolg gemeldet
   und der Host wieder geschrieben).
26 Der Dry-Run liest im Git-Repository dieselbe Flake wie der Rebuild (`.`,
   vorher gestaged) statt path:, ohne Git weiter path:.

Lauf: nix-shell shell.nix --run "python testing/routes/test_rev1118_paket_c.py"
"""
import io, json, os, re, subprocess, sys, tempfile, zipfile
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

from nico import config_manager, generator, git_manager, hardware, importer, prebuild, server  # noqa: E402

fails = []
def check(label, cond, got=None):
    print(("  OK   " if cond else "  FAIL ") + label + ("" if cond or got is None else f"  (war: {got})"))
    if not cond: fails.append(label)

data = config_manager.get_defaults()
data.update({"flakes": True, "hostname": "h1", "flake_hosts": [{"name": "h1"}]})
config_manager.save_config(str(cfg), data)
(cfg / "configuration.nix").write_text(generator.generate_configuration_nix(data), encoding="utf-8")
(cfg / "flake.nix").write_text(generator.generate_flake_nix(data, nixos_dir=str(cfg)), encoding="utf-8")
config_manager.save_host_config(str(cfg), "h1", {})

client = server.create_app().test_client()
page = client.get("/").get_data(as_text=True)
csrf = re.search(r'<meta name="csrf-token" content="([^"]+)"', page).group(1)
H = {"X-CSRF-Token": csrf, "Content-Type": "application/json"}

# ── 5 ───────────────────────────────────────────────────────────────────────
print("5. Zwei Hardware-Dateien außerhalb des Hostschemas")
check("Hostdatei behält ihren Namen", hardware._stash_name("h1", Path("hosts/h1/x.nix")) == "h1.nix")
check("Wurzel: @.nix", hardware._stash_name(None, Path("hardware-configuration.nix")) == "@.nix")
check("modules/: @modules.nix",
      hardware._stash_name(None, Path("modules/hardware-configuration.nix")) == "@modules.nix")
check("tiefer: @a@b.nix", hardware._stash_name(None, Path("a/b/hardware-configuration.nix")) == "@a@b.nix")
check("Host-Name kann kein @ tragen", "@" not in hardware._stash_name("x@y", None))

HW_A = b"{ ... }: { boot.kernelModules = [ \"wurzel\" ]; }\n"
HW_B = b"{ ... }: { boot.kernelModules = [ \"module\" ]; }\n"
(cfg / "hardware-configuration.nix").write_bytes(HW_A)
(cfg / "modules").mkdir()
(cfg / "modules" / "hardware-configuration.nix").write_bytes(HW_B)
real_classify = hardware.classify
hardware.classify = lambda path, ids=None: (hardware.UNKNOWN, [], [])
try:
    kept = hardware.stash_hw_files(str(cfg))
finally:
    hardware.classify = real_classify
names = sorted(e["file"] for e in kept)
check("beide im Zwischenlager, verschiedene Namen", names == ["@.nix", "@modules.nix"], names)
stash = config_manager.HW_STASH_DIR
check("beide Inhalte rückholbar", (stash / "@.nix").read_bytes() == HW_A
      and (stash / "@modules.nix").read_bytes() == HW_B)
(cfg / "hardware-configuration.nix").unlink()
(cfg / "modules" / "hardware-configuration.nix").unlink()

# ── 6 ───────────────────────────────────────────────────────────────────────
print("\n6. Links nach außen")
secret = Path(tempfile.mkdtemp(prefix="nico-outside-")) / "geheim.txt"
secret.write_text("GEHEIMNIS", encoding="utf-8")
(cfg / "geheim.nix").symlink_to(secret)
(cfg / "intern.nix").symlink_to(cfg / "configuration.nix")

name = importer.backup_to_zip(cfg)
with zipfile.ZipFile(cfg / name) as zf:
    members = zf.namelist()
    content = b"".join(zf.read(m) for m in members)
    comment = zf.comment.decode("utf-8")
(cfg / name).unlink()
check("Sicherung: Link nach außen fehlt", "geheim.nix" not in members, members)
check("Sicherung: kein fremder Inhalt", b"GEHEIMNIS" not in content)
check("Sicherung: Link nach innen bleibt", "intern.nix" in members, members)
check("Sicherung: Kommentar nennt die Datei", "geheim.nix" in comment, comment)

r = client.get("/api/export", headers=H)
with zipfile.ZipFile(io.BytesIO(r.data)) as zf:
    members = zf.namelist()
    content = b"".join(zf.read(m) for m in members)
check("Export: Link nach außen fehlt", "geheim.nix" not in members and b"GEHEIMNIS" not in content, members)
check("Export: Link nach innen bleibt", "intern.nix" in members, members)
check("Export: Kopfzeile nennt die Datei",
      json.loads(r.headers.get("X-NiCo-Skipped-Links", "[]")) == ["geheim.nix"],
      r.headers.get("X-NiCo-Skipped-Links"))
(cfg / "geheim.nix").unlink()
(cfg / "intern.nix").unlink()
r = client.get("/api/export", headers=H)
check("Export ohne Links: leere Liste", r.headers.get("X-NiCo-Skipped-Links") == "[]",
      r.headers.get("X-NiCo-Skipped-Links"))

# ── 23 ──────────────────────────────────────────────────────────────────────
print("\n23. Letzten Flake-Host löschen")
before = (cfg / "flake.nix").read_text(encoding="utf-8")
r = client.post("/api/flake/host/delete", headers=H, data=json.dumps({"name": "h1"}))
body = r.get_json() or {}
check("verweigert mit ERR_LAST_FLAKE_HOST", r.status_code == 409
      and body.get("error") == "ERR_LAST_FLAKE_HOST", (r.status_code, body))
check("flake.nix unverändert", (cfg / "flake.nix").read_text(encoding="utf-8") == before)

r = client.post("/api/flake/host/add", headers=H, data=json.dumps({"name": "h2"}))
check("zweiten Host angelegt", r.status_code == 200, (r.status_code, r.get_json()))
r = client.post("/api/flake/host/delete", headers=H, data=json.dumps({"name": "h2"}))
body = r.get_json() or {}
check("einen von zwei löschen geht", r.status_code == 200 and body.get("success"), (r.status_code, body))
check("h2 weg, h1 bleibt", [h["name"] for h in body.get("hosts", [])] == ["h1"], body.get("hosts"))

# ── 26 ──────────────────────────────────────────────────────────────────────
print("\n26. Dry-Run liest dieselbe Flake wie der Rebuild")
check("ohne Git: path:", prebuild.flake_base(str(cfg), False).startswith("path:"))
check("mit Git: .", prebuild.flake_base(str(cfg), True) == ".")
check("flake_ref baut darauf auf", prebuild.flake_ref(str(cfg), "h1", True) == ".#h1")

seen = []
real_run = subprocess.run
def fake_run(cmd, *a, **kw):
    if cmd and cmd[0] == "nix" and "build" in cmd:
        seen.append(list(cmd))
        return subprocess.CompletedProcess(cmd, 0, "", "")
    return real_run(cmd, *a, **kw)

subprocess.run = fake_run
try:
    r = client.post("/api/dry-run", headers=H, data=json.dumps({"hostname": "h1"}))
finally:
    subprocess.run = real_run
check("ohne Git: Ziel path:…#…h1", seen and seen[-1][2].startswith("path:")
      and seen[-1][2].endswith("#nixosConfigurations.h1.config.system.build.toplevel"), seen)

ok, _ = git_manager.init_repo(str(cfg))
check("Git eingerichtet", ok)
git_manager.auto_commit(str(cfg), label="start")
(cfg / "neu.nix").write_text("{ ... }: { }\n", encoding="utf-8")
seen.clear()
subprocess.run = fake_run
try:
    r = client.post("/api/dry-run", headers=H, data=json.dumps({"hostname": "h1"}))
finally:
    subprocess.run = real_run
check("mit Git: Ziel .#…h1 (Git-Flake wie der Rebuild)",
      seen and seen[-1][2] == ".#nixosConfigurations.h1.config.system.build.toplevel", seen)
tracked = subprocess.run(["git", "ls-files", "--cached", "neu.nix"], cwd=cfg,
                         capture_output=True, text=True).stdout.strip()
check("neue Datei vorher gestaged, wie beim Rebuild", tracked == "neu.nix", tracked)

print()
if fails:
    print(f"{len(fails)} FEHLER")
    sys.exit(1)
print("Alle Prüfungen bestanden.")
