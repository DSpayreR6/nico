"""Test: removed_options – Optionen, die nixpkgs entfernt hat.

Hintergrund (2026-11-08): `boot.initrd.systemd.enable` bricht auf nixos-unstable
die Auswertung ab, egal ob true oder false. Solche Fälle kommen mit jedem
nixpkgs-Stand wieder, darum liest die Regel die Liste aus dem nixpkgs der Config
statt sie zu pflegen.

Das nixpkgs ist hier ein kleiner Nachbau. Läuft ohne Server, Browser und Netz.
"""
import pathlib, sys, tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from nico import validator

fails = []
def check(label, cond, got=None):
    print(("  OK   " if cond else "  FAIL ") + label + ("" if cond or got is None else f"  (war: {got})"))
    if not cond: fails.append(label)


MODULE = r'''{ lib, ... }:
{
  imports = [
    (lib.mkRemovedOptionModule [ "boot" "initrd" "systemd" "enable" ] ''
      Scripted initrd has been removed.
    '')
    (lib.mkRemovedOptionModule [ "services" "gone" ] "The whole service is gone.")
    (lib.mkRemovedOptionModule [ "programs" "homeonly" "enable" ] "x")
    (lib.mkRemovedOptionModule [ "boot" someVariable "computed" ] "cannot be read")
  ];
}
'''


def tree(files: dict) -> str:
    tmp = tempfile.mkdtemp(prefix="nico-ro-")
    for name, content in files.items():
        target = pathlib.Path(tmp) / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    return tmp


SOURCE = pathlib.Path(tree({"nixos/modules/x/a.nix": MODULE}))


def run(files: dict, source=SOURCE, host=None):
    d = tree(files)
    orig = validator._nixpkgs_source
    validator._nixpkgs_source = lambda *_: source
    try:
        return validator._rule_removed_options(d, {}, False, host)
    finally:
        validator._nixpkgs_source = orig


def conf(body: str) -> dict:
    return {"configuration.nix": "{ config, pkgs, ... }:\n{\n" + body + "\n}\n"}


print("Liste aus nixpkgs")
r = validator._removed_options(SOURCE)
check("Eintrag gefunden", ("boot", "initrd", "systemd", "enable") in r)
check("Grund ist die erste Textzeile", r.get(("boot", "initrd", "systemd", "enable")) == "Scripted initrd has been removed.", r)
check("berechneter Eintrag wird übersprungen", not any("computed" in k for k in r), r)

print("Treffer")
f = run(conf("  boot.initrd.systemd.enable = true;"))
check("true wird gemeldet", len(f) == 1, f)
check("Datei und Zeile", f and f[0].params[0] == "configuration.nix:3", f and f[0].params)
check("Option genannt", f and f[0].params[1] == "boot.initrd.systemd.enable")
check("Grund mitgegeben", f and f[0].detail_params == ["Scripted initrd has been removed."])
check("Warnung, kein Fehler", f and f[0].severity == "warning")
check("false wird gemeldet", len(run(conf("  boot.initrd.systemd.enable = false;"))) == 1)
check("geschachtelt", len(run(conf("  boot.initrd = { systemd.enable = true; };"))) == 1)
check("tief geschachtelt", len(run(conf("  boot = { initrd = { systemd = { enable = true; }; }; };"))) == 1)
check("config.-Präfix", len(run(conf("  config.boot.initrd.systemd.enable = true;"))) == 1)
check("unterhalb einer entfernten Option", len(run(conf("  services.gone.enable = true;"))) == 1)
check("im aktiven Nix-Brick", len(run(conf(
    "  # <brick: BOOT / #1 x>\n  boot.initrd.systemd.enable = true;\n  # </brick: x>"))) == 1)

print("Kein Treffer")
check("andere Option", run(conf("  boot.initrd.systemd.emergencyAccess = true;\n  boot.initrd.kernelModules = [ ];")) == [])
check("Kommentar", run(conf("  # boot.initrd.systemd.enable = true;\n  networking.hostName = \"a\";")) == [])
check("im Text", run(conf('  environment.etc.x.text = "boot.initrd.systemd.enable = true;";')) == [])
check("deaktivierter Nix-Brick", run(conf(
    "  # <brick: BOOT / #1 x inactive>\n  # boot.initrd.systemd.enable = true;\n  # </brick: x>")) == [])
check("Home-Manager-Datei (home.nix)", run({"home.nix": "{ ... }:\n{\n  programs.homeonly.enable = true;\n}\n"}) == [])
check("Home-Manager-Verzeichnis", run({"home/martin.nix": "{ ... }:\n{\n  programs.homeonly.enable = true;\n}\n"}) == [])
check("Home-Manager am Inhalt erkannt", run({"mods/a.nix": '{ ... }:\n{\n  home.stateVersion = "26.05";\n  programs.homeonly.enable = true;\n}\n'}) == [])
check("flake.nix bleibt außen vor", run({"flake.nix": "{\n  boot.initrd.systemd.enable = true;\n}\n"}) == [])

print("nixpkgs nicht verfügbar")
check("keine Quelle, keine Meldung", run(conf("  boot.initrd.systemd.enable = true;"), source=None) == [])
d = tree({"flake.nix": "{ }\n"})
check("flake ohne lock", validator._nixpkgs_source(d, True) is None)
d = tree({"flake.lock": "kein json"})
check("defektes lock", validator._nixpkgs_source(d, True) is None)
d = tree({"flake.lock": '{"root":"r","nodes":{"r":{"inputs":{"nixpkgs":["a","b"]}}}}'})
check("follows im lock", validator._nixpkgs_source(d, True) is None)

print("NiCo selbst schreibt die Option nicht mehr")
from nico import config_manager, generator, importer
data = dict(config_manager.DEFAULT_NIXOS_CONFIG)
data.update(boot_initrd_systemd=True, plymouth_enabled=True)
out = generator.generate_configuration_nix(data)
check("Generator lässt die Zeile weg", "boot.initrd.systemd" not in out, out)
check("Plymouth bleibt", "boot.plymouth.enable = true;" in out, out)

def imported(line):
    src = "{ config, pkgs, ... }:\n{\n  " + line + "\n  boot.plymouth.enable = true;\n}\n"
    return importer.parse_config(src), importer.build_rest_brix(src, {})

rec, rest = imported("boot.initrd.systemd.enable = true;")
check("Import: true fällt weg", "boot.initrd.systemd" not in rest, rest)
check("Import: kein Feld mehr", "boot_initrd_systemd" not in rec)
rec, rest = imported("boot.initrd.systemd.enable = false;")
check("Import: false bleibt als Nix-Brick", "boot.initrd.systemd.enable = false" in rest, rest)
check("Import: Plymouth erkannt", rec.get("plymouth_enabled") is True)

print("Katalog")
check("Regel eingetragen", "removed_options" in validator.RULE_MAP and "removed_options" in validator._RULE_FNS)

print()
if fails:
    print(f"{len(fails)} FEHLER:", *fails, sep="\n  ")
    sys.exit(1)
print("Alle Prüfungen bestanden.")
