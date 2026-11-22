#!/usr/bin/env python3
"""Bootloader aus der Grundinstallation, 2026-11-22.

Martins Fund in der VM: Config der Grundinstallation importiert, im Panel
fehlte der Bootloader. Ursache: der Generator schrieb immer
`canTouchEfiVariables = false;` und bei systemd-boot immer
`configurationLimit = 5;` – beides steht in keiner frischen Installation, also
hielt die Gegenprobe den Bootloader aus dem Formular heraus.

Geprüft wird:
  1. Generator: canTouchEfiVariables nur bei gesetztem Haken; das Limit nur
     mit eingeschaltetem Schalter (fehlt der Schalter: an, wie bisher).
  2. Import der typischen Installer-Configs (systemd-boot, GRUB): Feld
     „Bootloader“ gesetzt, Schalter fürs Limit aus, die Bootloader-Zeilen
     der Datei unverändert. Eine Config mit Limit: Schalter an, Wert da.
  3. Validator boot_loader_missing: ohne Bootloader, nur grub.enable =
     false oder nur auskommentiert → Meldung; systemd-boot, GRUB mit
     Gerät, Container, WSL → keine.

Lauf: nix-shell shell.nix --run "python testing/routes/test_boot_loader.py"
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

from nico import config_manager, generator, importer, nix_parser, server, validator  # noqa: E402

fails = []


def check(label, cond, got=None):
    print(("  OK   " if cond else "  FAIL ") + label
          + ("" if cond or got is None else f"  (war: {got})"))
    if not cond:
        fails.append(label)


def loader_lines(text):
    return sorted(f"{'.'.join(a.path)}={a.value_text.strip()}"
                  for a in nix_parser.assignments(text) if a.path[:2] == ("boot", "loader"))


# ── 1. Generator ─────────────────────────────────────────────────────────────
print("1. Generator")
base = config_manager.get_defaults()
t = generator.generate_configuration_nix({**base, "boot_loader": "grub"})
check("GRUB ohne Haken: kein canTouchEfiVariables", "canTouchEfiVariables" not in t)
t = generator.generate_configuration_nix({**base, "boot_loader": "grub", "boot_efi_can_touch": True})
check("mit Haken: canTouchEfiVariables = true", "canTouchEfiVariables = true;" in t)
t = generator.generate_configuration_nix({**base, "boot_loader": "systemd-boot",
                                          "boot_config_limit_enabled": False})
check("Limit-Schalter aus: kein configurationLimit", "configurationLimit" not in t)
t = generator.generate_configuration_nix({**base, "boot_loader": "systemd-boot",
                                          "boot_config_limit": 7})
check("Schalter fehlt: Limit wie bisher geschrieben", "configurationLimit = 7;" in t)

# ── 2. Import ────────────────────────────────────────────────────────────────
HEAD = ("{ config, pkgs, ... }:\n\n{\n  imports =\n    [ # Include the results of the hardware scan.\n"
        "      ./hardware-configuration.nix\n    ];\n\n  # Bootloader.\n")
TAIL = ("\n  networking.hostName = \"nixos\"; # Define your hostname.\n"
        "  networking.networkmanager.enable = true;\n  time.timeZone = \"Europe/Berlin\";\n"
        "  system.stateVersion = \"26.05\"; # Did you read the comment?\n}\n")
INSTALLER = {
    "systemd-boot": HEAD + "  boot.loader.systemd-boot.enable = true;\n"
                           "  boot.loader.efi.canTouchEfiVariables = true;\n" + TAIL,
    "grub": HEAD + "  boot.loader.grub.enable = true;\n  boot.loader.grub.device = \"/dev/sda\";\n"
                   "  boot.loader.grub.useOSProber = true;\n" + TAIL,
    "systemd-boot+limit": HEAD + "  boot.loader.systemd-boot.enable = true;\n"
                                 "  boot.loader.systemd-boot.configurationLimit = 10;\n" + TAIL,
}
HW = "{ ... }:\n{\n  fileSystems.\"/\" = { device = \"/dev/sda1\"; fsType = \"ext4\"; };\n}\n"

app = server.create_app()
client = app.test_client()
page = client.get("/").get_data(as_text=True)
m = re.search(r'<meta name="csrf-token" content="([^"]+)"', page)
token = m.group(1) if m else ""
hdr = {"X-CSRF-Token": token, "Content-Type": "application/json"}

print("2. Import der Installer-Configs")
for name, text in INSTALLER.items():
    for p in cfg.rglob("*"):
        if p.is_file() and p.suffix in (".nix", ".zip"):
            p.unlink()
    src = Path(tempfile.mkdtemp(prefix="nico-src-"))
    (src / "configuration.nix").write_text(text, encoding="utf-8")
    (src / "hardware-configuration.nix").write_text(HW, encoding="utf-8")
    r = client.post("/api/import/run", headers=hdr, data=json.dumps(
        {"config_path": str(src / "configuration.nix"), "confirmed": True, "edit_mode": "p"}))
    check(f"{name}: Import erfolgreich", r.status_code == 200 and r.get_json().get("success"),
          r.get_json())
    d = client.get("/api/config", headers={"X-CSRF-Token": token}).get_json()
    want = name.split("+")[0]
    check(f"{name}: Feld Bootloader = {want}", d.get("boot_loader") == want, d.get("boot_loader"))
    written = (cfg / "configuration.nix").read_text(encoding="utf-8")
    check(f"{name}: Bootloader-Zeilen unverändert",
          loader_lines(written) == loader_lines(text),
          (loader_lines(written), loader_lines(text)))
    if want == "systemd-boot":
        limit = "limit" in name
        check(f"{name}: Limit-Schalter {'an' if limit else 'aus'}",
              d.get("boot_config_limit_enabled") is limit, d.get("boot_config_limit_enabled"))
        if limit:
            check(f"{name}: Limit-Wert 10", d.get("boot_config_limit") == 10, d.get("boot_config_limit"))

# ── 3. Validator ─────────────────────────────────────────────────────────────
print("3. Validator boot_loader_missing")
CASES = {
    "ohne Bootloader": ("{ ... }:\n{\n  networking.hostName = \"x\";\n}\n", True),
    "nur grub.enable = false": ("{ ... }:\n{\n  boot.loader.grub.enable = false;\n}\n", True),
    "nur auskommentiert": ("{ ... }:\n{\n  # boot.loader.systemd-boot.enable = true;\n}\n", True),
    "systemd-boot": ("{ ... }:\n{\n  boot.loader.systemd-boot.enable = true;\n}\n", False),
    "systemd-boot mit mkDefault": (
        "{ lib, ... }:\n{\n  boot.loader.systemd-boot.enable = lib.mkDefault true;\n}\n", False),
    "GRUB mit Gerät (Standard an)": ("{ ... }:\n{\n  boot.loader.grub.device = \"/dev/sda\";\n}\n", False),
    "Blockform": ("{ ... }:\n{\n  boot.loader = { systemd-boot.enable = true; };\n}\n", False),
    "Container": ("{ ... }:\n{\n  boot.isContainer = true;\n}\n", False),
    "WSL": ("{ ... }:\n{\n  wsl.enable = true;\n}\n", False),
}
for label, (text, expect) in CASES.items():
    d = Path(tempfile.mkdtemp(prefix="nico-val-"))
    (d / "configuration.nix").write_text(text, encoding="utf-8")
    found = validator._rule_boot_loader_missing(str(d), {}, False)
    check(f"{label}: {'Meldung' if expect else 'keine Meldung'}", bool(found) is expect, found)
check("leeres Verzeichnis: keine Meldung",
      not validator._rule_boot_loader_missing(tempfile.mkdtemp(), {}, False))

print("\n" + ("ALLE PRÜFUNGEN BESTANDEN" if not fails else f"FEHLGESCHLAGEN: {fails}"))
sys.exit(1 if fails else 0)
