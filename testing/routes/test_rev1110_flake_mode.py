#!/usr/bin/env python3
"""Revision 2026-11-10, Paket 5b: „Flakes" hat nur noch eine Bedeutung (B34).

Bis hierher hieß das Feld `flakes` dreierlei: die experimental-features-Zeile
steht in der Config (Importer), NiCo erzeugt eine flake.nix
(write_config_files) und NiCo baut mit --flake (Rebuild-Routen). Der
Validator nahm dagegen „flake.nix existiert". Folgen, am 2026-11-10 gemessen:

  * Klassische Config mit `experimental-features = [ "nix-command" "flakes" ]`
    – nötig für `nix run github:…`, bei NiCo-Nutzern also üblich – bekam beim
    Schreiben eine flake.nix, die auf hosts/<hostname>/ zeigt. Das gibt es
    nicht, und der Import von ./hardware-configuration.nix fiel weg.
  * Dasselbe, wenn jemand den Schalter im Formular frisch einschaltet.
  * Umgekehrt: fremde Flake-Config ohne die Zeile → NiCo baute klassisch.

Entschieden (Martin, 2026-11-10/12/13): Flake-Modus = flake.nix existiert.
Der Schalter steuert nur die experimental-features-Zeile. Eine flake.nix
anzulegen ist ein eigener Schritt (POST /api/flake/create) mit
Sicherungspunkt, Host-Verzeichnis und verschobener
hardware-configuration.nix. Hosts einer nicht abbildbaren flake.nix werden
per Textsuche gefunden.

Geprüft wird:
  1. core.flake_mode richtet sich nach der Datei, nicht nach dem Feld.
  2. Schreiben legt keine flake.nix an – weder bei importierter Zeile noch
     bei frisch eingeschaltetem Schalter; der Hardware-Import bleibt.
  3. Mit flake.nix ohne die Zeile: Flake-Modus, die Routen bauen mit --flake.
  4. Der Generator richtet sich nach `_flake_mode`, das die Aufrufer setzen.
  5. reimport_flake setzt `flakes` nicht und legt neben einer reinen
     flake.nix keine configuration.nix an (Rest von B39).
  6. POST /api/flake/create: alle Schritte, die Abbrüche, nichts halb.
  7. Host-Suche in einer nicht abbildbaren flake.nix.
  8. Kein `get("flakes")` mehr dort, wo über den Bauweg entschieden wird;
     Texte in sieben Sprachen.

Lauf: nix-shell shell.nix --run "python testing/routes/test_rev1110_flake_mode.py"
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

xdg = Path(tempfile.mkdtemp(prefix="nico-xdg-"))
os.environ["XDG_CONFIG_HOME"] = str(xdg)
(xdg / "nico").mkdir(parents=True)
SETTINGS = xdg / "nico" / "settings.json"

from nico import config_manager, core, generator, importer, prebuild, server  # noqa: E402

fails = []
def check(label, cond, got=None):
    print(("  OK   " if cond else "  FAIL ") + label + ("" if cond or got is None else f"  (war: {got})"))
    if not cond:
        fails.append(label)

HW = '{ ... }: { fileSystems."/" = { device = "/dev/sda1"; fsType = "ext4"; }; }\n'
FEAT = 'nix.settings.experimental-features = [ "nix-command" "flakes" ];'

def use(cfg: Path):
    SETTINGS.write_text(json.dumps({"nixos_config_dir": str(cfg), "language": "de",
                                    "git_sync": False}), encoding="utf-8")

def classic(hostname="kiste", flakes=True, hw=True) -> Path:
    """A classic config as NiCo writes it, optionally with the feature line."""
    cfg = Path(tempfile.mkdtemp(prefix="nico-cfg-"))
    if hw:
        (cfg / "hardware-configuration.nix").write_text(HW, encoding="utf-8")
    data = config_manager.get_defaults()
    data.update({"hostname": hostname, "flakes": flakes, "hardware_config": hw})
    (cfg / "configuration.nix").write_text(
        generator.generate_configuration_nix(data), encoding="utf-8")
    return cfg

flake_mode = getattr(core, "flake_mode", None)
check("es gibt core.flake_mode", callable(flake_mode))
if not callable(flake_mode):
    flake_mode = lambda d: None                     # inert stand-in

print("1. Flake-Modus richtet sich nach der Datei")
cfg = classic()
check("Zeile in der Config, keine flake.nix → kein Flake-Modus", flake_mode(str(cfg)) is False,
      flake_mode(str(cfg)))
check("das Feld bleibt trotzdem an (es ist die Zeile)",
      config_manager.load_config(str(cfg)).get("flakes") is True)
(cfg / "flake.nix").write_text("{ outputs = { self, ... }: { }; }\n", encoding="utf-8")
check("flake.nix da → Flake-Modus", flake_mode(str(cfg)) is True)
(cfg / "flake.nix").unlink()
(cfg / "flake.nix").mkdir()
check("ein Verzeichnis namens flake.nix zählt nicht", flake_mode(str(cfg)) is False)

print("\n2. Schreiben legt keine flake.nix an")
cfg = classic()
before = (cfg / "configuration.nix").read_text(encoding="utf-8")
check("Ausgangslage: Zeile da, Hardware-Import da",
      FEAT in before and "./hardware-configuration.nix" in before, before[-400:])
res = core.write_config_files(str(cfg), config_manager.load_config(str(cfg)), commit=False)
check("Schreiben gelingt", res.get("success") is True, res)
check("keine flake.nix entstanden", not (cfg / "flake.nix").exists())
check("nur configuration.nix geschrieben", res.get("written") == ["configuration.nix"], res.get("written"))
after = (cfg / "configuration.nix").read_text(encoding="utf-8")
check("die Zeile bleibt", FEAT in after)
check("der Hardware-Import bleibt", "./hardware-configuration.nix" in after)

cfg = classic(flakes=False)
data = config_manager.load_config(str(cfg))
check("Ausgangslage: Schalter aus", data.get("flakes") is False)
data["flakes"] = True                                # the form switch, freshly on
res = core.write_config_files(str(cfg), data, commit=False)
after = (cfg / "configuration.nix").read_text(encoding="utf-8")
check("Schalter frisch an: keine flake.nix", not (cfg / "flake.nix").exists())
check("Schalter frisch an: die Zeile wird geschrieben", FEAT in after)
check("Schalter frisch an: Hardware-Import bleibt", "./hardware-configuration.nix" in after)
check("kein hosts-Verzeichnis nebenbei", not (cfg / "hosts").exists())

print("\n3. flake.nix ohne die Zeile: Flake-Modus")
cfg = classic(hostname="fremd", flakes=False, hw=False)
fd = config_manager.get_defaults()
fd.update({"hostname": "fremd", "flake_hosts": [{"name": "fremd"}]})
(cfg / "flake.nix").write_text(generator.generate_flake_nix(fd, nixos_dir=str(cfg)), encoding="utf-8")
use(cfg)
app = server.create_app()
app.config["TESTING"] = True
c = app.test_client()
m = re.search(r'name="csrf-token" content="([^"]+)"', c.get("/").get_data(as_text=True))
H = {"X-CSRF-Token": m.group(1) if m else "", "Content-Type": "application/json"}
check("Ausgangslage: Feld aus", config_manager.load_config(str(cfg)).get("flakes") is False)
j = c.get("/api/flake/hosts", headers=H).get_json() or {}
check("/api/flake/hosts meldet Flake-Modus", j.get("flake_mode") is True, j)
check("und nennt den Host", [h.get("name") for h in j.get("hosts") or []] == ["fremd"], j)
seen = {}
orig_precheck = prebuild.precheck
def fake_precheck(nixos_dir, use_flake, flake_arg, **kw):
    seen.update(use_flake=use_flake, flake_arg=flake_arg)
    return {"ok": True, "heavy": [], "fetch_mib": 0}
prebuild.precheck = fake_precheck
try:
    c.post("/api/rebuild/precheck", headers=H, data=json.dumps({"hostname": "fremd"}))
finally:
    prebuild.precheck = orig_precheck
check("die Vorschau rechnet mit --flake", seen.get("use_flake") is True, seen)
check("und mit dem Host", "#fremd" in str(seen.get("flake_arg")), seen)
res = core.write_config_files(str(cfg), config_manager.load_config(str(cfg)), commit=False)
check("Schreiben nimmt die vorhandene flake.nix mit", "flake.nix" in (res.get("written") or []), res)
check("und setzt die Zeile nicht von sich aus",
      FEAT not in (cfg / "configuration.nix").read_text(encoding="utf-8"))

print("\n4. Generator richtet sich nach _flake_mode")
d = config_manager.get_defaults()
d.update({"hostname": "a", "hardware_config": True, "flakes": True})
out = generator.generate_configuration_nix(d)
check("Feld an, kein Flake-Modus: Hardware-Import steht", "./hardware-configuration.nix" in out)
d["_flake_mode"] = True
out = generator.generate_configuration_nix(d)
check("Flake-Modus: Hardware-Import entfällt", "./hardware-configuration.nix" not in out)
d2 = config_manager.get_defaults()
d2.update({"hostname": "a", "flakes": True,
           "flake_hosts": [{"name": "a"}, {"name": "b"}]})
check("zwei Hosts ohne Flake-Modus: stateVersion nicht pro Host",
      generator._state_version_per_host(d2) is False)
d2["_flake_mode"] = True
check("zwei Hosts im Flake-Modus: stateVersion pro Host",
      generator._state_version_per_host(d2) is True)

print("\n5. reimport_flake")
only = Path(tempfile.mkdtemp(prefix="nico-cfg-"))
(only / "flake.nix").write_text(generator.generate_flake_nix(fd, nixos_dir=str(only)), encoding="utf-8")
data = config_manager.get_defaults()
core.reimport_flake(str(only), data)
check("setzt das Feld nicht", data.get("flakes") is not True, data.get("flakes"))
check("legt keine configuration.nix neben eine reine flake.nix",
      not (only / "configuration.nix").exists())
foreign = Path(tempfile.mkdtemp(prefix="nico-cfg-"))
FOREIGN = '''{
  inputs.nixpkgs.url = "github:nixos/nixpkgs/nixos-25.05";
  outputs = inputs@{ self, nixpkgs, ... }: let lib = nixpkgs.lib; in {
    nixosConfigurations.alpha = lib.nixosSystem { modules = [ ./a.nix ]; };
    nixosConfigurations = {
      beta = nixpkgs.lib.nixosSystem { modules = [ ./b.nix ]; };
      "gamma-1" = inputs.nixpkgs.lib.nixosSystem { modules = [ ./c.nix ]; };
    };
  };
}
'''
(foreign / "flake.nix").write_text(FOREIGN, encoding="utf-8")
data = config_manager.get_defaults()
ret = core.reimport_flake(str(foreign), data)
check("nicht abbildbar: unangetastet", ret is False
      and (foreign / "flake.nix").read_text(encoding="utf-8") == FOREIGN)
check("nicht abbildbar: Feld nicht gesetzt, keine configuration.nix",
      data.get("flakes") is not True and not (foreign / "configuration.nix").exists())

print("\n6. POST /api/flake/create")
def git(cfg, *args):
    return subprocess.run(["git", *args], cwd=cfg, capture_output=True, text=True)
cfg = classic(hostname="kiste", flakes=False)
git(cfg, "init", "-q"); git(cfg, "add", "-A")
git(cfg, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "start")
(cfg / "configuration.nix").write_text(
    (cfg / "configuration.nix").read_text(encoding="utf-8").replace("kiste", "kiste"), encoding="utf-8")
(cfg / "notiz.nix").write_text("{ }\n", encoding="utf-8")      # uncommitted, must reach the backup
use(cfg)
r = c.post("/api/flake/create", headers=H, data="{}")
j = r.get_json() or {}
check("Route antwortet mit Erfolg", r.status_code == 200 and j.get("success") is True, (r.status_code, j))
flake = (cfg / "flake.nix").read_text(encoding="utf-8") if (cfg / "flake.nix").is_file() else ""
check("flake.nix liegt da", bool(flake))
check("NiCo kann sie abbilden", bool(flake) and importer.flake_unmappable(flake) is None,
      importer.flake_unmappable(flake) if flake else None)
check("ein Host mit dem Namen des Rechners", "kiste = nixpkgs.lib.nixosSystem" in flake)
check("Host-Verzeichnis angelegt", (cfg / "hosts" / "kiste" / "default.nix").is_file())
check("hardware-configuration.nix verschoben",
      (cfg / "hosts" / "kiste" / "hardware-configuration.nix").is_file()
      and not (cfg / "hardware-configuration.nix").exists())
check("Inhalt der Hardware-Datei unverändert",
      (cfg / "hosts" / "kiste" / "hardware-configuration.nix").read_text(encoding="utf-8") == HW)
conf = (cfg / "configuration.nix").read_text(encoding="utf-8")
check("die experimental-features-Zeile ist eingeschaltet", FEAT in conf)
check("configuration.nix importiert die Hardware nicht mehr selbst",
      "./hardware-configuration.nix" not in conf)
check("flake.nix verweist auf die verschobene Datei",
      "./hosts/kiste/hardware-configuration.nix" in flake and "./configuration.nix" in flake)
log = git(cfg, "log", "--format=%s").stdout.splitlines()
check("Sicherungspunkt davor", any("backup before flake.nix" in l for l in log), log)
backup = next((l for l in git(cfg, "log", "--format=%H %s").stdout.splitlines()
               if "backup before flake.nix" in l), "").split(" ")[0]
if backup:
    names = git(cfg, "ls-tree", "-r", "--name-only", backup).stdout.split()
    check("der Sicherungspunkt hält den Stand davor fest",
          "hardware-configuration.nix" in names and "notiz.nix" in names
          and "flake.nix" not in names, names)
check("danach ist nichts Ungesichertes übrig",
      git(cfg, "status", "--porcelain").stdout.strip() == "", git(cfg, "status", "--porcelain").stdout)
check("Flake-Modus gilt jetzt", flake_mode(str(cfg)) is True)
if shutil.which("nix-instantiate"):
    for f in ("flake.nix", "configuration.nix", "hosts/kiste/default.nix"):
        p = subprocess.run(["nix-instantiate", "--parse", str(cfg / f)], capture_output=True, text=True)
        check(f"{f} ist gültiges Nix", p.returncode == 0, p.stderr[-300:])

r = c.post("/api/flake/create", headers=H, data="{}")
check("zweiter Aufruf wird abgelehnt", r.status_code == 409
      and (r.get_json() or {}).get("error") == "ERR_FLAKE_EXISTS", (r.status_code, r.get_json()))
check("und ändert nichts", (cfg / "flake.nix").read_text(encoding="utf-8") == flake)
r = c.post("/api/flake/create", data="{}", headers={"Content-Type": "application/json"})
check("ohne Token abgelehnt", r.status_code in (400, 403), r.status_code)

cfg = Path(tempfile.mkdtemp(prefix="nico-cfg-"))
UNMAP = '{ config, pkgs, ... }:\nlet x = 1; in {\n  networking.hostName = "kiste";\n}\n'
(cfg / "configuration.nix").write_text(UNMAP, encoding="utf-8")
(cfg / "hardware-configuration.nix").write_text(HW, encoding="utf-8")
use(cfg)
r = c.post("/api/flake/create", headers=H, data="{}")
check("nicht abbildbare configuration.nix: abgelehnt",
      r.status_code == 409 and (r.get_json() or {}).get("error") == "ERR_NOT_MAPPABLE",
      (r.status_code, r.get_json()))
check("und nichts angefasst", not (cfg / "flake.nix").exists() and not (cfg / "hosts").exists()
      and (cfg / "hardware-configuration.nix").is_file()
      and (cfg / "configuration.nix").read_text(encoding="utf-8") == UNMAP)

cfg = classic(hostname="kiste", flakes=False)
(cfg / "hosts" / "kiste").mkdir(parents=True)
(cfg / "hosts" / "kiste" / "hardware-configuration.nix").write_text("{ }: { }  # schon da\n", encoding="utf-8")
use(cfg)
r = c.post("/api/flake/create", headers=H, data="{}")
j = r.get_json() or {}
check("Hardware-Datei im Host-Verzeichnis schon da: Erfolg", j.get("success") is True, j)
check("sie wird nicht überschrieben",
      "schon da" in (cfg / "hosts" / "kiste" / "hardware-configuration.nix").read_text(encoding="utf-8"))
check("die im Wurzelverzeichnis bleibt liegen, und die Antwort sagt es",
      (cfg / "hardware-configuration.nix").is_file() and j.get("hardware") == "kept", j.get("hardware"))

cfg = classic(hostname="kiste", flakes=False, hw=False)
use(cfg)
j = c.post("/api/flake/create", headers=H, data="{}").get_json() or {}
check("ohne Hardware-Datei und ohne git: Erfolg, Antwort sagt 'missing'",
      j.get("success") is True and j.get("hardware") == "missing", j)

print("\n7. Host-Suche in einer nicht abbildbaren flake.nix")
use(foreign)
hosts = core.get_flake_hosts(str(foreign), config_manager.load_config(str(foreign)))
check("alle drei Schreibweisen gefunden", hosts == ["alpha", "beta", "gamma-1"], hosts)
j = c.get("/api/flake/hosts", headers=H).get_json() or {}
check("die Route nennt sie für den Rebuild",
      j.get("flake_mode") is True and j.get("build_hosts") == ["alpha", "beta", "gamma-1"], j)
check("als Panel-Hosts gelten sie nicht", (j.get("hosts") or []) == [], j.get("hosts"))
(foreign / "flake.nix").write_text(
    '{ outputs = inputs@{ self, ... }: { nixosConfigurations = import ./hosts inputs; }; }\n',
    encoding="utf-8")
hosts = core.get_flake_hosts(str(foreign), config_manager.load_config(str(foreign)))
check("berechnete Hosts: Rückfall auf den Hostnamen (bekannte Grenze)", hosts == ["nixos"], hosts)

print("\n8. Quelltext und Texte")
for rel in ("routes/rebuild.py", "routes/flake.py", "routes/files.py", "routes/config.py", "core.py"):
    src = (ROOT / "nico" / rel).read_text(encoding="utf-8")
    hits = [l.strip() for l in src.splitlines() if re.search(r'get\("flakes"', l)]
    check(f"{rel}: kein get(\"flakes\") mehr", not hits, hits)
gen = (ROOT / "nico" / "generator.py").read_text(encoding="utf-8")
check("generator.py: das Feld steuert nur noch die Zeile",
      len(re.findall(r'get\("flakes"\)', gen)) == 1, re.findall(r'.*get\("flakes"\).*', gen))
html = (ROOT / "nico" / "templates" / "index.html").read_text(encoding="utf-8")
check("Schaltfläche steht im Formular", 'id="flake-create-btn"' in html)
want = {"de": "Flake-Befehle erlauben (experimental-features)"}
for p in sorted((ROOT / "nico" / "static" / "lang").glob("*.json")):
    texts = json.loads(p.read_text(encoding="utf-8"))
    check(f"{p.stem}: Schalter nennt experimental-features",
          "experimental-features" in texts.get("field.flakes", ""), texts.get("field.flakes"))
    if p.stem in want:
        check(f"{p.stem}: Wortlaut wie entschieden", texts.get("field.flakes") == want[p.stem])
    for key in ("flake.create", "flake.createTitle", "flake.createBody", "flake.createHint",
                "flake.createOk", "flake.createDone", "errors.ERR_FLAKE_EXISTS"):
        check(f"{p.stem}: {key}", bool(texts.get(key)) and texts.get(key) != "__TODO__")

print()
if fails:
    print(f"{len(fails)} FEHLER:", *fails, sep="\n  ")
    sys.exit(1)
print("Alle Prüfungen bestanden.")
