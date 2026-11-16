#!/usr/bin/env python3
"""Lay out the throwaway config a browser test expects, under <base>/{xdg,cfg}.

The browser tests read the config from XDG_CONFIG_HOME (settings.json points
at <base>/cfg; test_nested_dialogs finds it as XDG_CONFIG_HOME/../cfg). Until
2026-11-16 these configs were built by hand per session in a temp directory
and never kept – when the session was gone, so were they, and three tests
could not run any more. This script is the only source now.

Tests change the config they run on, so build a fresh one before each test:

    python3 testing/ui/make_test_config.py --for test_brix_dialogs /tmp/ui
    export XDG_CONFIG_HOME=/tmp/ui/xdg XDG_CACHE_HOME=/tmp/ui/cache

Profiles (PROFILE_FOR picks one per test, `standard` for every other):

  standard       configuration.nix with brick "basis-brick" and no imports
                 attribute (one would become a brick of its own and spoil the
                 lists), hosts/testhost with "host-brick-eins" and
                 "host-brick-zwei", home/martin.nix with "hm-brick",
                 bestand.nix with "huelle" holding "innen"
  nested         standard, plus brick "aussen" in configuration.nix and
                 bestand.nix as "huelle" holding "drinnen" (Services)
  state-version  a flake with nix-desktop (stateVersion 25.11), nix-laptop
                 and nix-vm, the base config without a value

Every profile has a hardware-configuration.nix in the root, confirmed the
way the start dialog does it (hardware.ack_hardware). Without it the dialog
"Hardware-Datei prüfen" covers the page at start and swallows the clicks.
German section names in the markers are deliberate: the files then go
through the path that reads files of older NiCo versions.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

PROFILE_FOR = {
    "test_nested_dialogs": "nested",
    "test_state_version_hint": "state-version",
}

HW = """{ config, lib, pkgs, modulesPath, ... }:
{
  imports = [ ];
  boot.initrd.availableKernelModules = [ "ahci" ];
  fileSystems."/" = { device = "/dev/disk/by-label/nico-test"; fsType = "ext4"; };
}
"""


def _bricks(*specs):
    from nico.brix import extract_brick_blocks
    text = "".join(f"# <brick: {sec} / #{i} {name}>\n  {body}\n# </brick: {name}>\n"
                   for i, (sec, name, body) in enumerate(specs, 1))
    return extract_brick_blocks(text)


def _bestand(inner: str, section: str) -> str:
    from nico.core import set_type_in_content
    text = ("{ config, pkgs, ... }:\n{\n"
            f"# <brick: {section} / #1 huelle>\n  services.a.enable = true;\n"
            f"# <brick: {section} / #2 {inner}>\n  services.b.enable = true;\n"
            f"# </brick: {inner}>\n# </brick: huelle>\n}}\n")
    return set_type_in_content(text, "nd")


def build(profile: str, base: Path) -> Path:
    cfg, xdg = base / "cfg", base / "xdg"
    shutil.rmtree(base, ignore_errors=True)
    (xdg / "nico").mkdir(parents=True)
    cfg.mkdir(parents=True)
    os.environ["XDG_CONFIG_HOME"] = str(xdg)
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from nico import config_manager, generator, hardware, hm_generator

    (xdg / "nico" / "settings.json").write_text(json.dumps(
        {"nixos_config_dir": str(cfg), "language": "de", "theme": "auto",
         "git_sync": False, "git_status_only": False}, indent=2), encoding="utf-8")
    (cfg / "config.json").write_text(json.dumps(
        {"hosts_dir": "hosts", "hm_dir": "home"}, indent=2), encoding="utf-8")

    data = config_manager.get_defaults()
    if profile == "state-version":
        hosts = ["nix-desktop", "nix-laptop", "nix-vm"]
        data.update({"_flake_mode": True, "flakes": True, "hostname": "nix-desktop",
                     "state_version": "", "flake_hosts": [{"name": h} for h in hosts]})
        (cfg / "configuration.nix").write_text(
            generator.generate_configuration_nix(data), encoding="utf-8")
        (cfg / "flake.nix").write_text(
            generator.generate_flake_nix(data, nixos_dir=str(cfg)), encoding="utf-8")
        for h in hosts:
            (cfg / "hosts" / h).mkdir(parents=True)
            host = {"hostname": h, "state_version": "25.11" if h == "nix-desktop" else ""}
            (cfg / "hosts" / h / "default.nix").write_text(
                generator.generate_host_nix(host, h), encoding="utf-8")
    else:
        data["_flake_mode"] = False
        co = [("Dienste", "basis-brick", "services.basis.enable = true;")]
        if profile == "nested":
            co.append(("Services", "aussen", "services.openssh.enable = true;"))
        data["brick_blocks"] = _bricks(*co)
        (cfg / "configuration.nix").write_text(
            generator.generate_configuration_nix(data), encoding="utf-8")
        (cfg / "hosts" / "testhost").mkdir(parents=True)
        (cfg / "hosts" / "testhost" / "default.nix").write_text(generator.generate_host_nix(
            {"hostname": "testhost",
             "brick_blocks": _bricks(
                 ("Dienste", "host-brick-eins", "services.eins.enable = true;"),
                 ("Dienste", "host-brick-zwei", "services.zwei.enable = true;"))},
            "testhost"), encoding="utf-8")
        (cfg / "home").mkdir()
        (cfg / "home" / "martin.nix").write_text(hm_generator.generate_home_nix(
            {"hm_username": "martin", "hm_homedir": "/home/martin",
             "hm_brick_blocks": _bricks(("TAIL", "hm-brick", "programs.bat.enable = true;"))}),
            encoding="utf-8")
        if profile == "nested":
            (cfg / "bestand.nix").write_text(_bestand("drinnen", "Services"), encoding="utf-8")
        else:
            (cfg / "bestand.nix").write_text(_bestand("innen", "Dienste"), encoding="utf-8")

    hw = cfg / "hardware-configuration.nix"
    hw.write_text(HW, encoding="utf-8")
    hardware.ack_hardware(str(cfg), None, hw)
    return cfg


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    group = ap.add_mutually_exclusive_group()
    group.add_argument("--profile", choices=["standard", "nested", "state-version"])
    group.add_argument("--for", dest="test", metavar="TEST",
                       help="test name (test_xyz or test_xyz.py); picks the profile")
    ap.add_argument("base", type=Path, help="directory to (re)create; gets xdg/ and cfg/")
    args = ap.parse_args()
    profile = args.profile or PROFILE_FOR.get(
        Path(args.test or "").stem, "standard")
    cfg = build(profile, args.base.resolve())
    print(f"{profile}: {cfg}")
    print(f"export XDG_CONFIG_HOME={args.base.resolve() / 'xdg'} "
          f"XDG_CACHE_HOME={args.base.resolve() / 'cache'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
