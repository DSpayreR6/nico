#!/usr/bin/env python3
"""Doppelte Attribute werden auch in flake.nix und Host-Dateien abgewiesen (B28).

Die Sperre lief nur über die erzeugte configuration.nix, der Changelog von 0.14.0
versprach sie für jede Datei. Geprüft wird vor allem, dass bei einer Ablehnung
keine Datei angefasst wurde, auch nicht die andere der beiden.

Der Generator wird ersetzt, damit der Doppelfehler sicher im Text steht; das
Urteil spricht das echte nix-instantiate.

Lauf: nix-shell shell.nix --run "python testing/routes/test_duplicate_attrs_write.py"
"""
import json, os, re, shutil, sys, tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

xdg = Path(tempfile.mkdtemp(prefix="nico-xdg-"))
os.environ["XDG_CONFIG_HOME"] = str(xdg)
cfg = Path(tempfile.mkdtemp(prefix="nico-cfg-"))

FLAKE = '{\n  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";\n  outputs = { self, nixpkgs, ... }: { };\n}\n'
CONF = "{ config, pkgs, ... }:\n{\n}\n"
HOST = "{ config, pkgs, ... }:\n{\n}\n"
DUP_FLAKE = '{\n  inputs.a.url = "x";\n  inputs = { a.url = "y"; };\n  outputs = { self, ... }: { };\n}\n'
DUP_HOST = '{ ... }:\n{\n  networking.hostName = "a";\n  networking.hostName = "b";\n}\n'

(cfg / "flake.nix").write_text(FLAKE, encoding="utf-8")
(cfg / "configuration.nix").write_text(CONF, encoding="utf-8")
(cfg / "hosts" / "nix-vm").mkdir(parents=True)
(cfg / "hosts" / "nix-vm" / "default.nix").write_text(HOST, encoding="utf-8")
(cfg / "config.json").write_text(json.dumps({"hosts_dir": "hosts", "flakes": True}), encoding="utf-8")
(xdg / "nico").mkdir(parents=True)
(xdg / "nico" / "settings.json").write_text(
    json.dumps({"nixos_config_dir": str(cfg), "language": "de", "git_sync": False}), encoding="utf-8")

from nico import config_manager, core, generator, server, validator  # noqa: E402

# NiCo only rewrites a flake.nix that carries its own stamp.
FLAKE = generator.generate_flake_nix(dict(config_manager.DEFAULT_NIXOS_CONFIG, flakes=True), nixos_dir=str(cfg))
(cfg / "flake.nix").write_text(FLAKE, encoding="utf-8")

fails = []
def check(label, cond, got=None):
    print(("  OK   " if cond else "  FAIL ") + label + ("" if cond or got is None else f"  (war: {got})"))
    if not cond: fails.append(label)


def snapshot() -> dict:
    return {str(p.relative_to(cfg)): p.read_bytes() for p in cfg.rglob("*") if p.is_file()}


if shutil.which("nix-instantiate") is None:
    print("ÜBERSPRUNGEN: nix-instantiate fehlt.")
    sys.exit(0)

print("Urteil von Nix")
check("doppelt gefunden", validator.duplicate_attrs_in(DUP_FLAKE) == ["inputs.a.url"],
      validator.duplicate_attrs_in(DUP_FLAKE))
check("Host doppelt gefunden", validator.duplicate_attrs_in(DUP_HOST) == ["networking.hostName"])
check("sauberer Text", validator.duplicate_attrs_in(FLAKE) == [])
check("anderer Syntaxfehler zählt nicht als doppelt", validator.duplicate_attrs_in("{ a = ; }") == [])

client = server.create_app().test_client()
page = client.get("/").get_data(as_text=True)
csrf = re.search(r'<meta name="csrf-token" content="([^"]+)"', page).group(1)
hdr = {"X-CSRF-Token": csrf, "Content-Type": "application/json"}

real_flake, real_host = generator.generate_flake_nix, generator.generate_host_nix
generator.generate_flake_nix = lambda *a, **k: DUP_FLAKE
generator.generate_host_nix = lambda *a, **k: DUP_HOST

print("Speichern: configuration.nix und flake.nix")
before = snapshot()
data = dict(config_manager.DEFAULT_NIXOS_CONFIG, flakes=True)
r = core.write_config_files(str(cfg), data, commit=False)
check("abgewiesen", r.get("error") == "ERR_DUPLICATE_ATTR", r)
check("Attribut genannt", r.get("duplicates") == ["inputs.a.url"], r.get("duplicates"))
check("auch configuration.nix unverändert", snapshot() == before)

print("Flake-Panel")
r = client.post("/api/config/flake", headers=hdr, data=json.dumps({"flake_description": "x"}))
check("abgewiesen", r.status_code == 409 and r.get_json().get("error") == "ERR_DUPLICATE_ATTR",
      (r.status_code, r.get_json()))
check("flake.nix unverändert", (cfg / "flake.nix").read_text(encoding="utf-8") == FLAKE)

print("Host speichern und schreiben")
path = "hosts/nix-vm/default.nix"
r = client.post("/api/host/nix-vm/config", headers=hdr,
                data=json.dumps({"_co_ready": True, "_co_path": path}))
check("Speichern abgewiesen", r.status_code == 409 and r.get_json().get("duplicates") == ["networking.hostName"],
      (r.status_code, r.get_json()))
check("Host-Datei unverändert", (cfg / path).read_text(encoding="utf-8") == HOST)
r = client.post("/api/host/nix-vm/write", headers=hdr, data=json.dumps({"commit": False}))
check("Schreiben abgewiesen", r.status_code == 409 and r.get_json().get("error") == "ERR_DUPLICATE_ATTR",
      (r.status_code, r.get_json()))
check("Host-Datei weiter unverändert", (cfg / path).read_text(encoding="utf-8") == HOST)

print("Gegenprobe ohne Doppelfehler")
generator.generate_flake_nix, generator.generate_host_nix = real_flake, real_host
r = client.post("/api/host/nix-vm/write", headers=hdr, data=json.dumps({"commit": False}))
check("Host wird geschrieben", r.status_code == 200, (r.status_code, r.get_json()))
r = core.write_config_files(str(cfg), dict(config_manager.DEFAULT_NIXOS_CONFIG, flakes=True), commit=False)
check("Speichern gelingt", r.get("success") is True, r)
check("beide Dateien geschrieben", set(r.get("written", [])) >= {"configuration.nix", "flake.nix"}, r.get("written"))

print()
if fails:
    print(f"{len(fails)} FEHLER:", *fails, sep="\n  ")
    sys.exit(1)
print("Alle Prüfungen bestanden.")
