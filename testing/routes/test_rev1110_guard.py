#!/usr/bin/env python3
"""Revision 2026-11-10, Paket 5a: Sofort-Schutz für nicht abbildbare Dateien.

Drei hohe Befunde aus dem Manifest („Befunde Revision 2026-11-10") haben
denselben Kern: NiCo liest eine Datei, versteht nur einen Teil und erzeugt sie
aus seinem eigenen Modell neu. Was es nicht verstanden hat, ist danach weg.

  B33  `{ … }: let … in { … }` und `{ … }: lib.mkIf c { … }` – der Parser nahm
       das erste Attribut-Set im Baum, bei `let` also die Hilfsbindungen. Die
       eigentliche Config fiel weg, die Hilfsbindungen standen als Optionen in
       der Datei.
  B30  Der `outputs`-Kopf einer flake.nix wurde nur in der Form
       `outputs = { … }: {` erkannt. Bei `{ … }@inputs:`, `inputs@{ … }:` oder
       `…: let … in {` fand NiCo keine Hosts und ersetzte den ganzen Block
       durch einen Standard-Host `nixos`.
  B31  Der nixpkgs-Kanal wurde nur aus exakt
       `nixpkgs.url = "github:NixOS/nixpkgs/<kanal>"` gelesen. Schon
       `github:nixos/…` in Kleinschreibung machte aus einem Stable-System
       `nixos-unstable`.

Entscheidung Martin 2026-11-10: Was NiCo nicht vollständig abbilden kann,
bleibt unangetastet und ist nur im Raw-Editor zu bearbeiten. Geprüft wird hier
deshalb vor allem eines: die Datei auf der Platte ist nach jedem Weg, der
bisher neu erzeugt hat, Byte für Byte dieselbe.

Erkannt wird am Inhalt, nicht am fehlenden nico-Stempel. Der Stempel entsteht
schon beim ersten Öffnen einer Datei in der Oberfläche; eine Sperre, die an
ihm hängt, hält genau bis dahin.

Lauf: nix-shell shell.nix --run "python testing/routes/test_rev1110_guard.py"
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
os.environ["XDG_CACHE_HOME"] = str(xdg / "cache")
(xdg / "nico").mkdir(parents=True)
SETTINGS = xdg / "nico" / "settings.json"

from nico import config_manager, core, generator, importer, nix_parser, server  # noqa: E402

fails = []


def check(label, cond, got=None):
    print(("  OK   " if cond else "  FAIL ") + label
          + ("" if cond or got is None else f"  (war: {got})"))
    if not cond:
        fails.append(label)


def attempt(fn, default=None):
    """Run fn; a missing function or a crash counts as a failed check, not as
    an aborted test run – before the fix half of what is called here does not
    exist yet."""
    try:
        return fn()
    except Exception as exc:  # noqa: BLE001
        return default if default is not None else f"{type(exc).__name__}: {exc}"


def tmp(prefix="nico-cfg-") -> Path:
    return Path(tempfile.mkdtemp(prefix=prefix))


def use_dir(d: Path) -> None:
    SETTINGS.write_text(json.dumps(
        {"nixos_config_dir": str(d), "language": "de", "git_sync": False}),
        encoding="utf-8")


# ── Eingaben ─────────────────────────────────────────────────────────────────

PLAIN = '''{ config, pkgs, ... }:
{
  networking.hostName = "plain-pc";
  services.nginx.enable = true;
}
'''

LET = '''{ config, pkgs, ... }:
let
  unstable = import <nixos-unstable> { };
in
{
  networking.hostName = "let-pc";
  services.nginx.enable = true;
  environment.systemPackages = [ unstable.hello ];
}
'''

MKIF = '''{ config, lib, ... }:
lib.mkIf config.my.enable {
  networking.hostName = "mkif-pc";
}
'''

WITH = '''{ pkgs, lib, ... }:
with lib;
{
  networking.hostName = "with-pc";
  services.nginx.enable = mkDefault true;
}
'''

AT_ARGS = '''{ config, ... }@args:
{
  networking.hostName = "at-pc";
  imports = [ (import ./x.nix args) ];
}
'''

REC = '''{ pkgs, ... }:
rec {
  networking.hostName = "rec-pc";
}
'''

HM_LET = '''{ config, pkgs, ... }:
let
  myPkgs = with pkgs; [ ripgrep fd ];
in
{
  home.username = "martin";
  home.homeDirectory = "/home/martin";
  home.packages = myPkgs;
  home.stateVersion = "25.05";
}
'''


def flake(head: str, inputs: str = None, body: str = None, extra: str = "") -> str:
    inputs = inputs if inputs is not None else '''  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-25.05";
  };'''
    body = body if body is not None else '''    nixosConfigurations = {
      alpha = nixpkgs.lib.nixosSystem {
        system = "x86_64-linux";
        modules = [ ./configuration.nix ];
      };
    };'''
    return f'''{{
  description = "test";
{extra}{inputs}
  outputs = {head}
{body}
  }};
}}
'''


FLAKE_OK = flake("{ self, nixpkgs, ... }: {")
FLAKE_AT_AFTER = flake("{ self, nixpkgs, ... }@inputs: {")
FLAKE_AT_BEFORE = flake("inputs@{ self, nixpkgs, ... }: {")
FLAKE_AT_SPACED = flake("inputs @ { self, nixpkgs, ... }: {")
FLAKE_LET = '''{
  description = "test";
  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-25.05";
  };
  outputs = { self, nixpkgs, ... }:
    let
      system = "x86_64-linux";
    in {
      nixosConfigurations = {
        alpha = nixpkgs.lib.nixosSystem {
          inherit system;
          modules = [ ./configuration.nix ];
        };
      };
    };
}
'''
FLAKE_FLAT_INPUTS = flake(
    "{ self, nixpkgs, ... }: {",
    inputs='  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-25.05";\n'
           '  inputs.foo.url = "github:foo/bar";')
FLAKE_LOWER = flake(
    "{ self, nixpkgs, ... }: {",
    inputs='''  inputs = {
    nixpkgs.url = "github:nixos/nixpkgs/nixos-25.05";
  };''')
FLAKE_NIXPKGS_SET = flake(
    "{ self, nixpkgs, ... }: {",
    inputs='''  inputs = {
    nixpkgs = { url = "github:NixOS/nixpkgs/nixos-25.05"; };
  };''')
FLAKE_NIXPKGS_GIT = flake(
    "{ self, nixpkgs, ... }: {",
    inputs='''  inputs = {
    nixpkgs.url = "git+https://example.org/nixpkgs?ref=nixos-25.05";
  };''')
FLAKE_HM_PINNED = flake(
    "{ self, nixpkgs, home-manager, ... }: {",
    inputs='''  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-25.05";
    home-manager.url = "github:some-fork/home-manager/my-branch";
  };''')
FLAKE_NIXCONFIG = flake(
    "{ self, nixpkgs, ... }: {",
    extra='  nixConfig = { extra-substituters = [ "https://example.org" ]; };\n')
FLAKE_OTHER_NIXPKGS = flake(
    "{ self, nixpkgs, nixpkgs-stable, ... }: {",
    inputs='''  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-25.05";
    nixpkgs-stable.url = "github:NixOS/nixpkgs/nixos-24.11";
  };''',
    body='''    nixosConfigurations = {
      alpha = nixpkgs-stable.lib.nixosSystem {
        system = "x86_64-linux";
        modules = [ ./configuration.nix ];
      };
    };''')
FLAKE_QUOTED_HOST = flake(
    "{ self, nixpkgs, ... }: {",
    body='''    nixosConfigurations = {
      "my.host" = nixpkgs.lib.nixosSystem {
        system = "x86_64-linux";
        modules = [ ./configuration.nix ];
      };
    };''')
FLAKE_EXTRA_OUTPUT = flake(
    "{ self, nixpkgs, ... }: {",
    body='''    nixosConfigurations = {
      alpha = nixpkgs.lib.nixosSystem {
        system = "x86_64-linux";
        modules = [ ./configuration.nix ];
      };
    };
    formatter.x86_64-linux = nixpkgs.legacyPackages.x86_64-linux.nixfmt;''')


def stamped(content: str, ftype: str) -> str:
    """The file after its first opening in the interface: type stamp set."""
    return core.set_type_in_content(content, ftype)


# ── B33: Körperform ──────────────────────────────────────────────────────────
print("B33 – Körperform einer Config-Datei")

unm = lambda c: attempt(lambda: importer.config_unmappable(c))  # noqa: E731

check("einfaches Attribut-Set gilt als abbildbar", unm(PLAIN) is False, unm(PLAIN))
check("ohne Funktionskopf gilt als abbildbar",
      unm('{ networking.hostName = "x"; }\n') is False)
check("leere Datei gilt als abbildbar", unm("") is False)
for label, src in [("let … in", LET), ("lib.mkIf", MKIF), ("with lib;", WITH),
                   ("{ … }@args", AT_ARGS), ("rec { … }", REC)]:
    check(f"{label} gilt als nicht abbildbar", unm(src) is True, unm(src))
check("von NiCo erzeugte configuration.nix ist abbildbar",
      unm(generator.generate_configuration_nix(config_manager.get_defaults())) is False)
check("von NiCo erzeugte Host-Datei ist abbildbar",
      unm(generator.generate_host_nix({"hostname": "h"}, "h")) is False)
check("Regex-Ersatz ohne tree-sitter urteilt bei let und mkIf gleich",
      attempt(lambda: (importer._rx_plain_body(PLAIN), importer._rx_plain_body(LET),
                       importer._rx_plain_body(MKIF), importer._rx_plain_body(AT_ARGS)))
      == (True, False, False, False))

print("B33 – Parser gibt bei nicht abbildbarer Datei nichts zurück")
check("parse_config(let) erkennt nichts, statt die let-Bindungen zu lesen",
      importer.parse_config(LET) == {}, importer.parse_config(LET))
check("build_rest_brix(let) liefert nichts",
      importer.build_rest_brix(LET, {}) == "")
check("parse_config(einfach) liest weiter den Hostnamen",
      importer.parse_config(PLAIN).get("hostname") == "plain-pc")

print("B33 – Laden und Schreiben lassen die Datei unangetastet")
for label, src in [("let", LET), ("mkIf", MKIF)]:
    d = tmp()
    f = d / "configuration.nix"
    f.write_text(src, encoding="utf-8")
    data = core.load_and_normalize_config(str(d))
    check(f"{label}: nach dem Laden Byte für Byte dieselbe Datei",
          f.read_text(encoding="utf-8") == src)
    check(f"{label}: Laden meldet die Datei als nicht abbildbar",
          "configuration.nix" in (data.get("_unmappable") or []), data.get("_unmappable"))

    # … and again after the first opening has stamped it
    st = stamped(src, "co")
    f.write_text(st, encoding="utf-8")
    core.load_and_normalize_config(str(d))
    check(f"{label}: auch mit nico-Stempel unverändert",
          f.read_text(encoding="utf-8") == st)

    res = core.write_config_files(str(d), config_manager.get_defaults(), commit=False)
    check(f"{label}: Formular-Schreiben wird abgelehnt",
          res.get("error") == "ERR_NOT_MAPPABLE", res)
    check(f"{label}: Datei nach dem abgelehnten Schreiben unverändert",
          f.read_text(encoding="utf-8") == st)

    raised = attempt(lambda: config_manager.save_config(str(d), config_manager.get_defaults()))
    check(f"{label}: save_config verweigert",
          isinstance(raised, str) and raised.startswith("NotMappableError"), raised)
    check(f"{label}: Datei nach save_config unverändert",
          f.read_text(encoding="utf-8") == st)

d = tmp()
(d / "configuration.nix").write_text(PLAIN, encoding="utf-8")
data = core.load_and_normalize_config(str(d))
check("einfache fremde Config wird weiter übernommen (Hostname im Modell)",
      data.get("hostname") == "plain-pc")
check("… und als NiCo-Datei geschrieben",
      "# nico-version:" in (d / "configuration.nix").read_text(encoding="utf-8"))
check("… und nicht als nicht abbildbar gemeldet", not data.get("_unmappable"),
      data.get("_unmappable"))

print("B33 – Host-Datei")
d = tmp()
(d / "configuration.nix").write_text(PLAIN, encoding="utf-8")
hf = d / "hosts" / "alpha" / "default.nix"
hf.parent.mkdir(parents=True)
hf.write_text(LET, encoding="utf-8")
hd = config_manager.load_host_config(str(d), "alpha")
check("load_host_config liest keine let-Bindungen", "hostname" not in (hd or {}), hd)
raised = attempt(lambda: config_manager.save_host_config(str(d), "alpha", {"hostname": "x"}))
check("save_host_config verweigert",
      isinstance(raised, str) and raised.startswith("NotMappableError"), raised)
check("Host-Datei unverändert", hf.read_text(encoding="utf-8") == LET)

print("B33 – Home-Manager-Datei")
out = attempt(lambda: core.normalize_hm_content(HM_LET)[0])
check("normalize_hm_content gibt eine let-Datei unverändert zurück", out == HM_LET)

# ── B30/B31: flake.nix ───────────────────────────────────────────────────────
print("B30 – outputs-Kopf")

fun = lambda c: attempt(lambda: importer.flake_unmappable(c), default="ERR")  # noqa: E731

check("Standardform ist abbildbar", fun(FLAKE_OK) is None, fun(FLAKE_OK))
for label, src in [
    ("{ … }@inputs:", FLAKE_AT_AFTER),
    ("inputs@{ … }:", FLAKE_AT_BEFORE),
    ("inputs @ { … }:", FLAKE_AT_SPACED),
    ("…: let … in {", FLAKE_LET),
    ("flache Inputs ohne inputs = {", FLAKE_FLAT_INPUTS),
    ("nixConfig neben inputs und outputs", FLAKE_NIXCONFIG),
    ("Host aus einem anderen nixpkgs", FLAKE_OTHER_NIXPKGS),
    ("Hostname in Anführungszeichen", FLAKE_QUOTED_HOST),
]:
    r = fun(src)
    check(f"{label} gilt als nicht abbildbar", isinstance(r, str) and r != "ERR", r)
check("weitere Outputs neben nixosConfigurations bleiben abbildbar (werden Nix-Brix)",
      fun(FLAKE_EXTRA_OUTPUT) is None, fun(FLAKE_EXTRA_OUTPUT))

print("B31 – nixpkgs-Kanal und bekannte Inputs")
check("github:nixos/… in Kleinschreibung: Kanal wird gelesen",
      importer.parse_flake_nix(FLAKE_LOWER).get("flake_nixpkgs_channel") == "nixos-25.05",
      importer.parse_flake_nix(FLAKE_LOWER).get("flake_nixpkgs_channel"))
check("… und die Datei ist abbildbar", fun(FLAKE_LOWER) is None, fun(FLAKE_LOWER))
for label, src in [
    ("nixpkgs = { url = …; }", FLAKE_NIXPKGS_SET),
    ("nixpkgs über git+https", FLAKE_NIXPKGS_GIT),
    ("home-manager auf Fork und Branch", FLAKE_HM_PINNED),
]:
    r = fun(src)
    check(f"{label} gilt als nicht abbildbar", isinstance(r, str) and r != "ERR", r)

print("B30 – NiCos eigene Dateien bleiben abbildbar")
gdata = config_manager.get_defaults()
gdata.update({"hostname": "nix-vm", "flakes": True,
              "flake_hosts": [{"name": "nix-vm"}, {"name": "nix-laptop"}],
              "flake_nixpkgs_channel": "nixos-25.05",
              "flake_hm_input": True, "flake_hm_follows": True, "flake_hm_module": True,
              "flake_nixos_hardware": True, "flake_plasma_manager": True})
gen = generator.generate_flake_nix(gdata)
check("erzeugte flake.nix mit allen Panel-Inputs", fun(gen) is None, fun(gen))
gdata2 = dict(gdata, flake_hm_follows=False, flake_plasma_manager=False,
              flake_nixpkgs_channel="nixos-unstable")
gen2 = generator.generate_flake_nix(gdata2)
check("erzeugte flake.nix ohne follows, unstable", fun(gen2) is None, fun(gen2))
# home-manager without follows next to plasma-manager, which always has one:
# the old reading found plasma-manager's line and switched it on for both.
gen3 = generator.generate_flake_nix(dict(gdata, flake_hm_follows=False))
check("home-manager ohne follows neben plasma-manager: wird so gelesen",
      importer.parse_flake_nix(gen3).get("flake_hm_follows") is False)
check("… und die Datei ist abbildbar", fun(gen3) is None, fun(gen3))
own = ROOT / "testing" / "configs" / "config-ms" / "original" / "flake.nix"
if own.is_file():
    r = fun(own.read_text(encoding="utf-8"))
    check("Martins eigene flake.nix (config-ms)", r is None, r)

print("B30 – gemessen an den Fremd-Configs")
blocked = []
total = 0
for fl in sorted((ROOT / "testing" / "configs").glob("*/original/flake.nix")):
    total += 1
    r = fun(fl.read_text(encoding="utf-8"))
    if r is not None:
        blocked.append(fl.parent.parent.name)
print(f"       {len(blocked)} von {total} flake.nix bleiben unangetastet: {', '.join(blocked)}")
check("jede flake.nix, die NiCo bisher zerlegt hat, ist jetzt gesperrt",
      total == 0 or len(blocked) >= total - 1, f"{len(blocked)}/{total}")

print("B30 – jeder Weg, der die flake.nix neu erzeugt hat")
for label, src in [("@inputs", FLAKE_AT_AFTER), ("let", FLAKE_LET),
                   ("nixpkgs = { … }", FLAKE_NIXPKGS_SET)]:
    d = tmp()
    (d / "configuration.nix").write_text(PLAIN, encoding="utf-8")
    ff = d / "flake.nix"

    # import
    ff.write_text(src, encoding="utf-8")
    attempt(lambda: core.reimport_flake(str(d), config_manager.load_config(str(d))))
    check(f"{label}: Import lässt die Datei unangetastet",
          ff.read_text(encoding="utf-8") == src)

    # loading, once the first opening has stamped it
    st = stamped(src, "fl")
    ff.write_text(st, encoding="utf-8")
    data = core.load_and_normalize_config(str(d))
    check(f"{label}: Laden lässt die gestempelte Datei unangetastet",
          ff.read_text(encoding="utf-8") == st)
    check(f"{label}: Laden meldet flake.nix als nicht abbildbar",
          "flake.nix" in (data.get("_unmappable") or []), data.get("_unmappable"))

    # form write
    data = config_manager.load_config(str(d))
    data["flakes"] = True
    res = core.write_config_files(str(d), data, commit=False)
    check(f"{label}: Formular-Schreiben lässt die flake.nix unangetastet",
          ff.read_text(encoding="utf-8") == st, res)

d = tmp()
(d / "configuration.nix").write_text(PLAIN, encoding="utf-8")
(d / "flake.nix").write_text(FLAKE_LOWER, encoding="utf-8")
attempt(lambda: core.reimport_flake(str(d), config_manager.load_config(str(d))))
text = (d / "flake.nix").read_text(encoding="utf-8")
check("abbildbare flake.nix wird weiter übernommen, Kanal bleibt nixos-25.05",
      "# nico-version:" in text and "nixpkgs/nixos-25.05" in text
      and "nixos-unstable" not in text)
check("… und der Host bleibt erhalten", re.search(r"\balpha = nixpkgs\.lib\.nixosSystem", text) is not None)

# ── Routen ───────────────────────────────────────────────────────────────────
print("Routen")
d = tmp()
use_dir(d)
cf = d / "configuration.nix"
cf.write_text(LET, encoding="utf-8")
ff = d / "flake.nix"
ff.write_text(stamped(FLAKE_AT_AFTER, "fl"), encoding="utf-8")
flake_before = ff.read_text(encoding="utf-8")

app = server.create_app()
client = app.test_client()
page = client.get("/").get_data(as_text=True)
m = re.search(r'<meta name="csrf-token" content="([^"]+)"', page)
hdr = {"X-CSRF-Token": m.group(1) if m else "", "Content-Type": "application/json"}

r = client.get("/api/file?path=configuration.nix", headers=hdr).get_json()
check("GET /api/file meldet unmappable für die let-Config", r.get("unmappable") is True, r.get("unmappable"))
r = client.get("/api/file?path=flake.nix", headers=hdr).get_json()
check("GET /api/file meldet unmappable für die flake.nix", r.get("unmappable") is True, r.get("unmappable"))

r = client.get("/api/parse/co?path=configuration.nix", headers=hdr).get_json()
check("GET /api/parse/co antwortet mit ERR_NOT_MAPPABLE", r.get("error") == "ERR_NOT_MAPPABLE", r)

client.get("/api/config", headers=hdr)
check("GET /api/config lässt die let-Config unangetastet", cf.read_text(encoding="utf-8") == LET)

resp = client.post("/api/config", headers=hdr, data=json.dumps(
    {"_co_ready": True, "_co_path": "configuration.nix", "hostname": "x"}))
check("POST /api/config antwortet mit ERR_NOT_MAPPABLE",
      (resp.get_json() or {}).get("error") == "ERR_NOT_MAPPABLE", resp.get_json())
check("… und die Datei ist unverändert", cf.read_text(encoding="utf-8") == LET)

resp = client.post("/api/write", headers=hdr, data=json.dumps({"commit": False}))
check("POST /api/write antwortet mit ERR_NOT_MAPPABLE",
      (resp.get_json() or {}).get("error") == "ERR_NOT_MAPPABLE", resp.get_json())

resp = client.post("/api/config/flake", headers=hdr, data=json.dumps(
    {"flake_description": "neu"}))
check("POST /api/config/flake lässt die flake.nix unangetastet",
      ff.read_text(encoding="utf-8") == flake_before)

resp = client.post("/api/flake/host/add", headers=hdr, data=json.dumps({"name": "beta"}))
check("POST /api/flake/host/add wird abgelehnt",
      (resp.get_json() or {}).get("error") in ("ERR_EXTERNAL_FLAKE", "ERR_NOT_MAPPABLE"),
      resp.get_json())
check("… und die flake.nix ist unverändert", ff.read_text(encoding="utf-8") == flake_before)

resp = client.post("/api/file/panel-mode", headers=hdr, data=json.dumps(
    {"path": "flake.nix", "mode": "p"}))
check("Wechsel in den Panel-Modus wird abgelehnt",
      (resp.get_json() or {}).get("error") == "ERR_NOT_MAPPABLE", resp.get_json())

edited = LET.replace("let-pc", "let-pc-2")
resp = client.post("/api/file", headers=hdr, data=json.dumps(
    {"files": [{"path": "configuration.nix", "content": edited}]}))
check("der Raw-Editor darf die let-Config speichern",
      (resp.get_json() or {}).get("success") is True, resp.get_json())
check("… und schreibt genau den Text aus dem Editor",
      cf.read_text(encoding="utf-8") == edited)

# A managed file must stay locked for the raw write – the exception is for
# files NiCo cannot represent, not a hole in ERR_MANAGED_FILE.
d2 = tmp()
use_dir(d2)
(d2 / "configuration.nix").write_text(
    generator.generate_configuration_nix(config_manager.get_defaults()), encoding="utf-8")
resp = client.post("/api/file", headers=hdr, data=json.dumps(
    {"files": [{"path": "configuration.nix", "content": "{ }\n"}]}))
check("eine von NiCo verwaltete Datei bleibt für den Raw-Weg gesperrt",
      (resp.get_json() or {}).get("error") == "ERR_MANAGED_FILE", resp.get_json())

# ── Ergebnis ─────────────────────────────────────────────────────────────────
print()
if fails:
    print(f"{len(fails)} Prüfung(en) fehlgeschlagen:")
    for f in fails:
        print("  -", f)
    sys.exit(1)
print("Alle Prüfungen bestanden.")
