#!/usr/bin/env python3
"""Nachprüfung 2026-11-26 (Codex), Paket B: Dateigrenze.

Verzeichnisimport – copy_nix_tree sammelte die Quelle mit rglob() und
is_file(), beide folgen Dateilinks: ein ausgelagertes Modul kam als
gewöhnliche Datei in die neue Config. Jetzt geht die Quelle durch
configfiles.scan() (Entscheidung Martin 2026-11-26: Links nach außen auch in
der Importquelle nicht lesen); /api/import/source-left nennt sie vorher.
Eine reguläre flake.lock muss weiter mitkommen (Präzisierung ChatGPT:
scan() liefert standardmäßig nur .nix).

Validator – host_orphaned folgte der Importkette über die Config hinaus,
andere Regeln lasen flake.nix/configuration.nix ohne Grenze. Geprüft wird,
welche Dateien tatsächlich geöffnet werden (Präzisierung ChatGPT: eine
korrekte Meldung allein ließe den Fehler durch).

Lauf: nix-shell shell.nix --run "python testing/routes/test_codex_1126_paket_b.py"
"""
import io, json, os, re, sys, tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

xdg = Path(tempfile.mkdtemp(prefix="nico-xdg-"))
os.environ["XDG_CONFIG_HOME"] = str(xdg)
cfg = Path(tempfile.mkdtemp(prefix="nico-cfg-"))
ext = Path(tempfile.mkdtemp(prefix="nico-ext-"))       # "elsewhere"
(xdg / "nico").mkdir(parents=True)
(xdg / "nico" / "settings.json").write_text(
    json.dumps({"nixos_config_dir": str(cfg), "language": "de", "git_sync": False}),
    encoding="utf-8")
(cfg / "config.json").write_text(json.dumps({"hosts_dir": "hosts"}), encoding="utf-8")
(cfg / "configuration.nix").write_text("{ ... }: { }\n", encoding="utf-8")

from nico import config_manager, importer, server, validator  # noqa: E402

fails = []
def check(label, cond, got=None):
    print(("  OK   " if cond else "  FAIL ") + label + ("" if cond or got is None else f"  (war: {got})"))
    if not cond: fails.append(label)

# Every file opened below `watched` is recorded: pathlib reads through io.open.
opened: list[str] = []
watched: list[Path] = []
_real_open = io.open
def _spy_open(file, *a, **kw):
    try:
        p = os.path.realpath(os.fspath(file))
        if any(p == str(w) or p.startswith(str(w) + os.sep) for w in watched):
            opened.append(p)
    except TypeError:
        pass
    return _real_open(file, *a, **kw)
io.open = _spy_open

client = server.create_app().test_client()
page = client.get("/").get_data(as_text=True)
csrf = re.search(r'<meta name="csrf-token" content="([^"]+)"', page).group(1)
H = {"X-CSRF-Token": csrf, "Content-Type": "application/json"}

def post(url, body):
    r = client.post(url, headers=H, data=json.dumps(body))
    return r.status_code, (r.get_json(silent=True) or {})

# ── Importquelle ────────────────────────────────────────────────────────────
src = Path(tempfile.mkdtemp(prefix="nico-src-"))
(src / "modules").mkdir()
(src / "configuration.nix").write_text("{ ... }: { imports = [ ./modules/intern.nix ]; }\n")
(src / "flake.lock").write_text('{"nodes": {}, "version": 7}\n')
(src / "modules/intern.nix").write_text("{ intern = true; }\n")
(ext / "aussen.nix").write_text("{ aussen = true; }\n")
(ext / "geteilt").mkdir()
(ext / "geteilt/x.nix").write_text("{ geteilt = true; }\n")
os.symlink(ext / "aussen.nix", src / "modules/extern.nix")
os.symlink(ext / "geteilt", src / "shared")
os.symlink(ext / "aussen.nix", src / "README")         # no .nix suffix, no dir: not news
watched[:] = [ext.resolve()]

print("Verzeichnisimport: Vorschau der Links nach außen")
r = client.get(f"/api/import/source-left?path={src}/configuration.nix")
names = sorted(x["file"] for x in (r.get_json() or {}).get("left", []))
check("source-left nennt modules/extern.nix und shared, nicht den README-Link",
      names == ["modules/extern.nix", "shared"], names)
check("… ohne ein Ziel zu öffnen", opened == [], opened)
r = client.get(f"/api/import/source-left?path={src}/gibtsnicht")
check("Nicht vorhandenes Verzeichnis: leere Liste, kein Fehler",
      r.status_code == 200 and r.get_json().get("left") == [], r.get_json())

print("Verzeichnisimport: Übernahme")
opened.clear()
s, r = post("/api/import/run", {"config_path": str(src / "configuration.nix"),
                                "confirmed": True, "edit_mode": "r"})
check("Import gelingt", s == 200 and r.get("success"), (s, r))
copied = sorted(r.get("files_copied") or [])
check("Hauptdatei, flake.lock und internes Modul kommen an",
      copied == ["configuration.nix", "flake.lock", "modules/intern.nix"], copied)
check("flake.lock bytegleich", (cfg / "flake.lock").read_text() == '{"nodes": {}, "version": 7}\n')
check("Externes Modul nicht im Ziel angelegt", not os.path.lexists(cfg / "modules/extern.nix"))
check("Verlinktes Verzeichnis nicht im Ziel", not os.path.lexists(cfg / "shared"))
check("Kein Ziel außerhalb geöffnet", opened == [], opened)
check("Externe Dateien unverändert", (ext / "aussen.nix").read_text() == "{ aussen = true; }\n")

print("Import aus /etc/nixos (gleicher Weg)")
importer.ETC_NIXOS = src
opened.clear()
s, r = post("/api/import/apply", {"confirmed": True, "edit_mode": "r"})
check("Import gelingt ohne externes Modul",
      s == 200 and "modules/extern.nix" not in (r.get("files_copied") or []), (s, r))
check("Kein Ziel außerhalb geöffnet", opened == [], opened)

print("Hauptdatei selbst ist ein Link nach außen")
src2 = Path(tempfile.mkdtemp(prefix="nico-src2-"))
(ext / "co.nix").write_text("{ ... }: { fremd = true; }\n")
os.symlink(ext / "co.nix", src2 / "configuration.nix")
before = (cfg / "configuration.nix").read_bytes()
opened.clear()
s, r = post("/api/import/run", {"config_path": str(src2 / "configuration.nix"),
                                "confirmed": True, "edit_mode": "r"})
check("Pfad-Import: ERR_IMPORT_NO_MAIN", s == 400 and r.get("error") == "ERR_IMPORT_NO_MAIN", (s, r))
importer.ETC_NIXOS = src2
r2 = client.post("/api/import/preview", headers=H)
s3, r3 = post("/api/import/apply", {"confirmed": True, "edit_mode": "r"})
check("/etc/nixos-Vorschau und -Import: ERR_IMPORT_NO_MAIN",
      r2.get_json().get("error") == "ERR_IMPORT_NO_MAIN" and r3.get("error") == "ERR_IMPORT_NO_MAIN",
      (r2.get_json(), r3))
check("… Ziel nicht geöffnet, Config unverändert",
      opened == [] and (cfg / "configuration.nix").read_bytes() == before, opened)

# ── Validator ───────────────────────────────────────────────────────────────
print("Validator: Non-Flake mit internem Host und externem Modul")
vcfg = Path(tempfile.mkdtemp(prefix="nico-vcfg-"))
(vcfg / "config.json").write_text(json.dumps({"hosts_dir": "hosts"}), encoding="utf-8")
for h in ("a", "b"):
    (vcfg / "hosts" / h).mkdir(parents=True)
    (vcfg / "hosts" / h / "default.nix").write_text("{ ... }: { }\n")
(vcfg / "modules").mkdir()
# The external module would bind host b - if it were read.
(ext / "bindet_b.nix").write_text("{ ... }: { imports = [ ../hosts/b ]; }\n")
os.symlink(ext / "bindet_b.nix", vcfg / "modules/ext.nix")
# A path above the config, next to it on disk.
oben = vcfg.parent / f"{vcfg.name}-oben.nix"
oben.write_text("{ ... }: { imports = [ ./" + vcfg.name + "/hosts/b ]; }\n")
(vcfg / "configuration.nix").write_text(
    "{ ... }: { imports = [ ./hosts/a ./modules/ext.nix ../" + oben.name + " ]; }\n")
watched[:] = [ext.resolve(), oben.resolve()]
opened.clear()
found = validator.run_validation(str(vcfg), {}, config_manager.load_config(str(vcfg)) or {})
orph = sorted(f["params"][0] for f in found if f["rule_id"] == "host_orphaned")
check("Interner Host a erreichbar, b als verwaist gemeldet", orph == ["b"], orph)
check("linked_outside nennt den externen Link",
      any(f["rule_id"] == "linked_outside" and "modules/ext.nix" in f["message"] for f in found),
      [f["rule_id"] for f in found])
check("Keine Regel öffnet das externe Modul oder den Pfad oberhalb", opened == [], opened)

print("Validator: Flake, flake.nix selbst ist ein Link nach außen")
fcfg = Path(tempfile.mkdtemp(prefix="nico-fcfg-"))
(fcfg / "config.json").write_text(json.dumps({"hosts_dir": "hosts"}), encoding="utf-8")
(ext / "flake.nix").write_text('{ outputs = { ... }: { nixosConfigurations.x = 1; }; }\n')
os.symlink(ext / "flake.nix", fcfg / "flake.nix")
(ext / "flake.lock").write_text('{"nodes": {}}\n')
os.symlink(ext / "flake.lock", fcfg / "flake.lock")
watched[:] = [ext.resolve()]
opened.clear()
found = validator.run_validation(str(fcfg), {}, {})
check("Keine Regel öffnet flake.nix/flake.lock außerhalb", opened == [], opened)
check("Kein Regelabbruch dadurch",
      not [f for f in found if f["rule_id"] != "linked_outside" and "konnte nicht" in f["message"]],
      [f["message"] for f in found if "konnte nicht" in f["message"]])

print("Gegenprobe: dieselbe Config ohne Links wird gelesen")
gcfg = Path(tempfile.mkdtemp(prefix="nico-gcfg-"))
(gcfg / "config.json").write_text(json.dumps({"hosts_dir": "hosts"}), encoding="utf-8")
for h in ("a", "b"):
    (gcfg / "hosts" / h).mkdir(parents=True)
    (gcfg / "hosts" / h / "default.nix").write_text("{ ... }: { }\n")
(gcfg / "modules").mkdir()
(gcfg / "modules/bindet_b.nix").write_text("{ ... }: { imports = [ ../hosts/b ]; }\n")
(gcfg / "configuration.nix").write_text("{ ... }: { imports = [ ./hosts/a ./modules/bindet_b.nix ]; }\n")
found = validator.run_validation(str(gcfg), {}, {})
check("Internes Modul wird gelesen: kein Host verwaist",
      not [f for f in found if f["rule_id"] == "host_orphaned"],
      [f["params"] for f in found if f["rule_id"] == "host_orphaned"])

io.open = _real_open
print()
print("ALLE TESTS BESTANDEN" if not fails else f"{len(fails)} FEHLGESCHLAGEN: {fails}")
sys.exit(1 if fails else 0)
