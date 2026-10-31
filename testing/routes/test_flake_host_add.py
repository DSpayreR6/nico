#!/usr/bin/env python3
"""Ein neu angelegter Flake-Host muss baubar sein – oder gemeldet werden.

Aus der Revision vom 2026-10-31. `flake_host_add` schrieb den Host in die
flake.nix ("no filesystem creation") und legte keine Datei an. Der erzeugte
Hosteintrag verweist aber auf `./<hosts_dir>/<name>` und auf die
`hardware-configuration.nix` darin. Nix löst ein Verzeichnis über seine
default.nix auf – ohne sie bricht die Auswertung für genau den Host ab, der
gerade angelegt wurde.

Gemeldet hat das niemand: `flake_host_exists` findet den Host in der flake.nix,
und `imports_exist` war dreifach blind – es sah nur `imports = [...]` und nicht
die `modules`-Liste eines Flake-Hosts, es schnitt vorher den Brick-Inhalt weg
(ein Flake-Hostkörper *ist* ein Brick), und `re.search` hörte nach der ersten
Liste je Datei auf. Die volle Validierung mit 38 Regeln lief auf so einer Config
ohne einen einzigen Hinweis auf die fehlende Datei durch.

Geprüft wird:
  1. Host anlegen erzeugt hosts/<name>/default.nix mit NiCo-Kopfzeile.
  2. Die fehlende hardware-configuration.nix wird als Fehler gemeldet – die
     Zeile bleibt in der flake.nix, weil ein Host, der ohne sie baut, keine
     Dateisysteme hätte und der Hostkörper nur einmal erzeugt wird.
  3. Liegt die Hardware-Datei da, ist der Befund weg (keine Fehlalarme).
  4. `modules`-Listen, Brick-Inhalt und mehrere Listen pro Datei werden gesehen.
  5. Ein Pfad in einem Skript innerhalb eines Bricks ist kein Importpfad.

Lauf: nix-shell shell.nix --run "python testing/routes/test_flake_host_add.py"
"""
import json
import os
import re
import sys
import tempfile
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

from nico import config_manager, generator, server, validator  # noqa: E402

fails = []


def check(label, cond, got=None):
    print(("  OK   " if cond else "  FAIL ") + label
          + ("" if cond or got is None else f"  (war: {got})"))
    if not cond:
        fails.append(label)


# Eine Flake-Config mit einem Host, der alles hat
data = config_manager.get_defaults()
data.update({"flakes": True, "hostname": "nix-vm",
             "flake_hosts": [{"name": "nix-vm"}]})
config_manager.save_config(str(cfg), data)
(cfg / "configuration.nix").write_text(
    generator.generate_configuration_nix(data), encoding="utf-8")
(cfg / "flake.nix").write_text(
    generator.generate_flake_nix(data, nixos_dir=str(cfg)), encoding="utf-8")
config_manager.save_host_config(str(cfg), "nix-vm", {})
(cfg / "hosts" / "nix-vm" / "hardware-configuration.nix").write_text(
    "{ ... }: { fileSystems.\"/\" = { device = \"/dev/sda1\"; fsType = \"ext4\"; }; }\n",
    encoding="utf-8")

app = server.create_app()
client = app.test_client()
page = client.get("/").get_data(as_text=True)
m = re.search(r'<meta name="csrf-token" content="([^"]+)"', page)
hdr = {"X-CSRF-Token": m.group(1) if m else "", "Content-Type": "application/json"}

# ── 1. Host anlegen ──────────────────────────────────────────────────────────
print("\n1. Host anlegen")
r = client.post("/api/flake/host/add", headers=hdr, data=json.dumps({"name": "neuling"}))
check("200", r.status_code == 200, (r.status_code, r.get_json()))
host_default = cfg / "hosts" / "neuling" / "default.nix"
check("hosts/neuling/default.nix angelegt", host_default.is_file())
if host_default.is_file():
    text = host_default.read_text(encoding="utf-8")
    check("trägt die NiCo-Kopfzeile", "nico-version:" in text, text.splitlines()[:3])
flake_text = (cfg / "flake.nix").read_text(encoding="utf-8")
check("flake.nix verweist weiter auf die Hardware-Datei",
      "./hosts/neuling/hardware-configuration.nix" in flake_text)

# ── 2. Fehlende Hardware-Datei wird gemeldet ─────────────────────────────────
print("\n2. Fehlende hardware-configuration.nix")
data = config_manager.load_config(str(cfg)) or {}
findings = validator.run_validation(str(cfg), validator.default_validation_rules(),
                                    data, host="neuling")
hw_hits = [f for f in findings
           if f.get("rule_id") == "imports_exist"
           and "neuling/hardware-configuration.nix" in str(f.get("message", ""))]
check("imports_exist meldet sie", len(hw_hits) == 1, [f.get("message") for f in findings])
check("als Fehler, nicht als Hinweis",
      bool(hw_hits) and hw_hits[0].get("severity") == "error",
      hw_hits[0].get("severity") if hw_hits else None)
check("der vollständige Host erzeugt keinen Befund",
      not [f for f in validator.run_validation(
          str(cfg), validator.default_validation_rules(), data, host="nix-vm")
          if f.get("rule_id") == "imports_exist"
          and "nix-vm" in str(f.get("message", ""))])

# ── 3. Datei vorhanden → kein Befund ─────────────────────────────────────────
print("\n3. Hardware-Datei eingesetzt")
(cfg / "hosts" / "neuling" / "hardware-configuration.nix").write_text(
    "{ ... }: { fileSystems.\"/\" = { device = \"/dev/sdb1\"; fsType = \"ext4\"; }; }\n",
    encoding="utf-8")
findings = validator.run_validation(str(cfg), validator.default_validation_rules(),
                                    data, host="neuling")
check("kein imports_exist-Befund mehr",
      not [f for f in findings if f.get("rule_id") == "imports_exist"],
      [f.get("message") for f in findings if f.get("rule_id") == "imports_exist"])

# ── 4. Was die Pfadsuche jetzt sieht ─────────────────────────────────────────
print("\n4. Pfadsuche")
extract = validator._extract_imports_with_lines

modules_in_brick = """{
  # <brick: OUTH / #1 host-neuling>
  modules = [
    ./configuration.nix
    ./hosts/neuling/hardware-configuration.nix
  ];
  # </brick>
}
"""
paths = [p for p, _ in extract(modules_in_brick)]
check("modules-Liste innerhalb eines Bricks",
      "./hosts/neuling/hardware-configuration.nix" in paths, paths)

zwei_listen = """{
  imports = [ ./erste.nix ];
  foo = { modules = [ ./zweite.nix ]; };
}
"""
paths = [p for p, _ in extract(zwei_listen)]
check("mehrere Listen je Datei",
      "./erste.nix" in paths and "./zweite.nix" in paths, paths)

kommentiert = "{ imports = [ # ./weg.nix\n  ./da.nix ];\n}\n"
paths = [p for p, _ in extract(kommentiert)]
check("auskommentierter Pfad zählt nicht",
      paths == ["./da.nix"], paths)

skript_im_brick = """{
  # <brick: HEAD / #1 bauen>
  systemd.services.bauen.script = ''
    cd /src && ./configure --prefix=/usr && ./result/bin/x
  '';
  # </brick>
}
"""
paths = [p for p, _ in extract(skript_im_brick)]
check("Pfad in einem Skript ist kein Importpfad", paths == [], paths)

modules_pfad = '{ imports = [ (modulesPath + "/installer/scan/not-detected.nix") ]; }\n'
check("Ausdruckspfade werden übergangen",
      [p for p, _ in extract(modules_pfad)] == [],
      [p for p, _ in extract(modules_pfad)])

print()
if fails:
    print("FEHLGESCHLAGEN:", len(fails))
    for f in fails:
        print("  -", f)
    sys.exit(1)
print("Alle Prüfungen bestanden.")
