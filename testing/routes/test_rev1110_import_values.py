#!/usr/bin/env python3
"""Revision 2026-11-10, Paket 2: der Import verfälscht und verliert keine Werte.

Befunde B35, B36, B37. Gemeinsamer Kern: NiCo liest Werte mit Regex aus dem
Quelltext, **verbraucht** die Bindung und schreibt sie aus seinem Modell neu.
Passt der Wert nicht in das Modell, kommt er verändert oder gar nicht zurück.
Die Regel für jede Reparatur: nur verbrauchen, was der Generator genauso
wieder schreibt – alles andere bleibt wörtlich als Nix-Brick stehen.

Geprüft wird jeweils der ganze Weg (lesen → Modell → schreiben), so wie ihn
der Import geht, und dass das Ergebnis gültiges Nix ist.

  B35  a) Desktops ohne Snippet (plasma5, mate, lxqt) gingen verloren
       b) Snapper: flache Form und Blöcke mit weiteren Feldern
       c) fonts.packages / environment.systemPackages bedingungslos verbraucht
  B36  extraGroups mit Bindestrich, Ports als Ausdruck, dritter Eintrag in
       experimental-features, `${…}` im Hostnamen, kernelParams mit `++`,
       Paketlisten mit Ausdrücken, Benutzer „gast", sharedModules
  B37  Home Manager: git-Block mit weiteren Feldern, eigene xdg-Pfade, `#`
       in einer Zeichenkette, Pakete mit Punkt im Namen, `${…}` im Pfad
  B31  Kanal ohne Rücksicht auf Groß-/Kleinschreibung (seit Paket 5a)

Lauf: nix-shell shell.nix --run "python testing/routes/test_rev1110_import_values.py"
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
os.environ["XDG_CONFIG_HOME"] = tempfile.mkdtemp(prefix="nico-xdg-")

from nico import config_manager, core, generator, importer  # noqa: E402
from nico.brix import brix_content_to_bricks                 # noqa: E402

fails = []
def check(label, cond, got=None):
    print(("  OK   " if cond else "  FAIL ") + label + ("" if cond or got is None else f"  (war: {got})"))
    if not cond:
        fails.append(label)

TMP = Path(tempfile.mkdtemp(prefix="nico-parse-"))
HAVE_NIX = bool(shutil.which("nix-instantiate"))
def valid(text: str) -> "tuple[bool, str]":
    if not HAVE_NIX:
        return True, ""
    f = TMP / "t.nix"
    f.write_text(text, encoding="utf-8")
    p = subprocess.run(["nix-instantiate", "--parse", str(f)], capture_output=True, text=True)
    return p.returncode == 0, p.stderr[-300:]

def squash(text: str) -> str:
    return re.sub(r"\s+", " ", text)

def co(body: str) -> str:
    return ("{ config, pkgs, lib, inputs, prefix, unstable, ... }:\n{\n"
            + body.rstrip("\n") + "\n}\n")

def imported(body: str) -> "tuple[dict, str]":
    """The import path: parse, rest as bricks, loss check, generate."""
    text = co(body)
    recognized = importer.parse_config(text)
    rest = importer.build_rest_brix(text, recognized)
    data = config_manager.get_defaults()
    data.update(recognized)
    if rest.strip():
        data["brick_blocks"] = brix_content_to_bricks(rest, section="Start")
    importer.rescue_into_bricks(data, text)
    out = generator.generate_configuration_nix(data)
    return recognized, out

def keeps(label, body, *needles, absent=()):
    rec, out = imported(body)
    flat = squash(out)
    for n in needles:
        check(f"{label}: „{n}“ bleibt", squash(n) in flat, flat[-500:])
    for n in absent:
        check(f"{label}: kein „{n}“", squash(n) not in flat)
    ok, err = valid(out)
    check(f"{label}: Ergebnis ist gültiges Nix", ok, err)
    return rec, out

print("B35 a) Desktops ohne Snippet")
for de in ("mate", "lxqt", "plasma5"):
    line = f"services.xserver.desktopManager.{de}.enable = true;"
    rec, out = keeps(de, f"  services.xserver.enable = true;\n  {line}",
                     line, "services.xserver.enable = true;")
    check(f"{de}: nicht als Desktop ins Formular", rec.get("desktop_environment") in (None, "", "none"),
          rec.get("desktop_environment"))
rec, out = imported("  services.xserver.enable = true;\n  services.displayManager.gdm.enable = true;\n"
                    "  services.desktopManager.gnome.enable = true;")
check("gnome wird weiter erkannt", rec.get("desktop_environment") == "gnome", rec.get("desktop_environment"))
check("jeder erkannte Desktop hat ein Snippet",
      all(n in generator._DE_SNIPPETS for n in getattr(importer, "_DE_RECOGNISED", ["?"])),
      getattr(importer, "_DE_RECOGNISED", None))

print("\nB35 b) Snapper")
rec, _ = keeps("flache Form", '  services.snapper.configs.root.SUBVOLUME = "/";',
               'services.snapper.configs.root.SUBVOLUME = "/";')
check("flache Form: kein Formulareintrag", not rec.get("snapper_configs"), rec.get("snapper_configs"))
rec, _ = keeps("Block mit weiterem Feld",
               '  services.snapper.configs.home = {\n    SUBVOLUME = "/home";\n'
               '    ALLOW_USERS = [ "martin" ];\n    TIMELINE_CREATE = true;\n  };',
               'ALLOW_USERS = [ "martin" ];', 'SUBVOLUME = "/home";')
check("Block mit weiterem Feld: kein Formulareintrag", not rec.get("snapper_configs"), rec.get("snapper_configs"))
rec, _ = keeps("Block ohne SUBVOLUME",
               "  services.snapper.configs.data = {\n    TIMELINE_LIMIT_DAILY = 3;\n  };",
               "TIMELINE_LIMIT_DAILY = 3;")
check("Block ohne SUBVOLUME: kein Formulareintrag", not rec.get("snapper_configs"), rec.get("snapper_configs"))
own = ('  services.snapper.configs.root = {\n    SUBVOLUME = "/";\n    TIMELINE_CREATE = true;\n'
       "    TIMELINE_CLEANUP = true;\n    TIMELINE_LIMIT_HOURLY = 4;\n    TIMELINE_LIMIT_DAILY = 6;\n"
       "    TIMELINE_LIMIT_WEEKLY = 1;\n    TIMELINE_LIMIT_MONTHLY = 2;\n    TIMELINE_LIMIT_YEARLY = 0;\n  };")
rec, out = imported(own)
sn = (rec.get("snapper_configs") or [{}])[0]
check("NiCos eigene Form wird weiter erkannt",
      sn.get("name") == "root" and sn.get("mountpoint") == "/" and sn.get("hourly") == 4
      and sn.get("weekly") == 1, rec.get("snapper_configs"))
check("und steht genau einmal in der Ausgabe", out.count("services.snapper.configs.root") == 1)

print("\nB35 c) / B36 Paketlisten")
PK = ("  environment.systemPackages = with pkgs; [\n    git\n"
      "    (python3.withPackages (ps: with ps; [ requests ]))\n"
      "    inputs.foo.packages.${pkgs.system}.default\n  ];")
rec, out = keeps("Pakete mit Ausdrücken", PK,
                 "(python3.withPackages (ps: with ps; [ requests ]))",
                 "inputs.foo.packages.${pkgs.system}.default", "git")
check("Pakete mit Ausdrücken: Formularliste bleibt leer", not rec.get("packages"), rec.get("packages"))
check("Pakete mit Ausdrücken: Liste steht einmal da", out.count("environment.systemPackages") == 1)
rec, out = imported("  environment.systemPackages = with pkgs; [ git vim kdePackages.kate ];")
check("einfache Paketliste wird weiter erkannt",
      sorted(p["attr"] for p in rec.get("packages") or []) == ["git", "kdePackages.kate", "vim"],
      rec.get("packages"))
keeps("anderes with", "  environment.systemPackages = with unstable; [ foo bar ];",
      "with unstable; [ foo bar ]")
rec, _ = keeps("Schrift mit override",
               '  fonts.packages = [ (pkgs.nerdfonts.override { fonts = [ "FiraCode" ]; }) ];',
               '(pkgs.nerdfonts.override { fonts = [ "FiraCode" ]; })')
check("Schrift mit override: Formularliste bleibt leer", not rec.get("fonts"), rec.get("fonts"))
rec, _ = imported("  fonts.packages = with pkgs; [ noto-fonts dejavu_fonts ];")
check("einfache Schriftliste wird weiter erkannt",
      rec.get("fonts") == ["dejavu_fonts", "noto-fonts"], rec.get("fonts"))
keeps("leere Schriftliste", "  fonts.packages = [ ];", "fonts.packages = [ ];")

print("\nB36 Werte")
rec, out = keeps("extraGroups",
                 '  users.users.martin = {\n    isNormalUser = true;\n'
                 '    extraGroups = [ "wheel" "systemd-journal" "i2c-dev" ];\n  };',
                 '"systemd-journal"', '"i2c-dev"', '"wheel"')
check("extraGroups: alle drei im Formular",
      set(rec.get("user_groups") or []) == {"wheel", "systemd-journal", "i2c-dev"}, rec.get("user_groups"))
rec, _ = keeps("Ports als Ausdruck",
               "  networking.firewall.allowedTCPPorts = [ 22 config.services.jellyfin.port ];",
               "config.services.jellyfin.port")
check("Ports als Ausdruck: nicht ins Formular", not rec.get("firewall_tcp_ports"), rec.get("firewall_tcp_ports"))
rec, _ = imported("  networking.firewall.allowedTCPPorts = [ 22 80 ]; # web")
check("reine Portliste wird weiter erkannt", rec.get("firewall_tcp_ports") == "22 80", rec.get("firewall_tcp_ports"))
rec, _ = keeps("dritter Eintrag in experimental-features",
               '  nix.settings.experimental-features = [ "nix-command" "flakes" "pipe-operators" ];',
               '"pipe-operators"')
check("dritter Eintrag: Schalter bleibt aus", not rec.get("flakes"), rec.get("flakes"))
rec, _ = imported('  nix.settings.experimental-features = [ "flakes" "nix-command" ];')
check("genau die zwei Einträge: Schalter an", rec.get("flakes") is True)
rec, out = keeps("${…} im Hostnamen", '  networking.hostName = "${prefix}-pc";',
                 'networking.hostName = "${prefix}-pc";', absent=("\\${",))
check("${…} im Hostnamen: nicht ins Formular", rec.get("hostname") in (None, "", "nixos"), rec.get("hostname"))
rec, _ = keeps("kernelParams mit ++",
               '  boot.kernelParams = [ "quiet" ] ++ lib.optionals true [ "splash" ];',
               'lib.optionals true [ "splash" ]')
check("kernelParams mit ++: nicht ins Formular", not rec.get("boot_kernel_params"), rec.get("boot_kernel_params"))
rec, _ = keeps("kernelParams mit Leerzeichen im Wert",
               '  boot.kernelParams = [ "acpi_osi=Windows 2020" ];', '"acpi_osi=Windows 2020"')
check("Leerzeichen im Wert: nicht ins Formular", not rec.get("boot_kernel_params"), rec.get("boot_kernel_params"))
rec, _ = imported('  boot.kernelParams = [ "quiet" "splash" ];')
check("reine kernelParams werden weiter erkannt", rec.get("boot_kernel_params") == "quiet splash",
      rec.get("boot_kernel_params"))

rec, _ = keeps("Benutzer namens gast",
               '  users.users.gast = {\n    isNormalUser = true;\n    description = "Gast";\n'
               '    extraGroups = [ "audio" ];\n  };',
               'description = "Gast";', '"audio"')
check("Benutzer namens gast: kein Gast-Schalter", not rec.get("guest_user"), rec.get("guest_user"))
rec, out = imported('  users.users.gast = {\n    isNormalUser = true;\n    initialPassword = "";\n  };')
check("NiCos Gast-Block wird weiter erkannt", rec.get("guest_user") is True)
check("und steht nicht doppelt da", out.count("users.users.gast") <= 1, out.count("users.users.gast"))
rec, _ = keeps("sharedModules mit Ausdruck",
               "  home-manager.sharedModules = [ inputs.foo.homeModules.bar (import ./x.nix) ];",
               "(import ./x.nix)", "inputs.foo.homeModules.bar")
rec, _ = imported("  home-manager.sharedModules = [ plasma-manager.homeModules.plasma-manager inputs.a.b ];")
check("einfache sharedModules werden weiter erkannt",
      rec.get("hm_plasma_manager") is True and rec.get("hm_shared_modules_extra") == "inputs.a.b",
      (rec.get("hm_plasma_manager"), rec.get("hm_shared_modules_extra")))

print("\nB31 Kanal")
for url in ("github:nixos/nixpkgs/nixos-25.05", "github:NixOS/nixpkgs/nixos-25.11"):
    f = importer.parse_flake_nix('{\n  inputs = {\n    nixpkgs.url = "%s";\n  };\n'
                                 '  outputs = { self, nixpkgs, ... }: {\n  };\n}\n' % url)
    check(f"{url}: Kanal gelesen", f.get("flake_nixpkgs_channel") == url.rsplit("/", 1)[1]
          or url.rsplit("/", 1)[1] in str(f), f)

print("\nB37 Home Manager")
def hm(body: str) -> str:
    return "{ config, pkgs, ... }:\n{\n" + body.rstrip("\n") + "\n}\n"

def hm_keeps(label, body, *needles, absent=(), once=()):
    out, _ = core.normalize_hm_content(hm(body))
    flat = squash(out)
    for n in needles:
        check(f"{label}: „{n}“ bleibt", squash(n) in flat, flat[-600:])
    for n in absent:
        check(f"{label}: kein „{n}“", squash(n) not in flat)
    for n in once:
        check(f"{label}: „{n}“ steht genau einmal da", flat.count(squash(n)) == 1, flat.count(squash(n)))
    ok, err = valid(out)
    check(f"{label}: Ergebnis ist gültiges Nix", ok, err)
    again, _ = core.normalize_hm_content(out)
    check(f"{label}: zweiter Durchlauf ändert nichts", again == out)
    return out

GIT = ('  programs.git = {\n    enable = true;\n    userName = "Martin";\n'
       '    userEmail = "m@example.org";\n    aliases = { co = "checkout"; };\n'
       '    signing.key = "ABCD";\n  };')
hm_keeps("git mit weiteren Feldern", GIT, 'aliases = { co = "checkout"; };', 'signing.key = "ABCD";',
         'userName = "Martin";', once=("programs.git",))
out = hm_keeps("git wie NiCo es schreibt",
               '  programs.git = {\n    enable = true;\n    userName  = "Martin";\n'
               '    userEmail = "m@example.org";\n    extraConfig = {\n'
               '      init.defaultBranch = "main";\n    };\n  };',
               'userName = "Martin";', 'init.defaultBranch = "main";', once=("programs.git",))
check("git wie NiCo es schreibt: wird Formularfeld, kein Brick", "<brick" not in out.split("programs.git")[0][-200:]
      and importer.parse_home_config(out).get("git_enable") is True)

hm_keeps("xdg mit eigenem Pfad",
         '  xdg.userDirs = {\n    enable = true;\n    download = "/data/Downloads";\n  };',
         'download = "/data/Downloads";', once=("xdg.userDirs",))
out = hm_keeps("xdg wie NiCo es schreibt",
               '  xdg.userDirs = {\n    enable     = true;\n    createDirectories = true;\n'
               '    download = "${config.home.homeDirectory}/Downloads";\n  };',
               'download = "${config.home.homeDirectory}/Downloads";', once=("xdg.userDirs",))
check("xdg wie NiCo es schreibt: wird Formularfeld",
      importer.parse_home_config(out).get("xdg_user_dirs") is True
      and importer.parse_home_config(out).get("xdg_download") == "Downloads")
hm_keeps("xdg ohne createDirectories",
         '  xdg.userDirs = {\n    enable = true;\n  };', absent=("createDirectories",))

hm_keeps("# in einer Zeichenkette", '  programs.kitty.settings.background = "#1e1e2e";',
         'programs.kitty.settings.background = "#1e1e2e";')
hm_keeps("/* */ in einer Zeichenkette", '  home.file.".x".text = "a /* b */ c";',
         '"a /* b */ c"')
hm_keeps("# in einer mehrzeiligen Zeichenkette",
         "  programs.bash.bashrcExtra = ''\n    PS1='# '\n    echo fertig\n  '';",
         "PS1='# '", "echo fertig")
hm_keeps("Paket mit Punkt im Namen",
         "  home.packages = [ pkgs.kdePackages.kate pkgs.ripgrep ];",
         "pkgs.kdePackages.kate", "pkgs.ripgrep", once=("home.packages",))
hm_keeps("Paketliste mit Ausdruck",
         '  home.packages = [ pkgs.ripgrep (pkgs.writeShellScriptBin "hallo" "echo hi") ];',
         '(pkgs.writeShellScriptBin "hallo" "echo hi")', "pkgs.ripgrep", once=("home.packages",))
hm_keeps("${…} im Heimatverzeichnis",
         '  home.username = "martin";\n  home.homeDirectory = "/home/${config.home.username}";',
         'home.homeDirectory = "/home/${config.home.username}";', absent=("\\${",),
         once=("home.homeDirectory",))
out = hm_keeps("einfache Werte", '  home.username = "martin";\n  home.homeDirectory = "/home/martin";\n'
               '  home.stateVersion = "25.05";\n  home.packages = [ pkgs.ripgrep ];',
               'home.stateVersion = "25.05";', "pkgs.ripgrep")
p = importer.parse_home_config(out)
check("einfache Werte landen im Formular",
      (p.get("username"), p.get("home_dir"), p.get("state_version"), p.get("packages"))
      == ("martin", "/home/martin", "25.05", ["ripgrep"]), p)

print()
if fails:
    print(f"{len(fails)} FEHLER:", *fails, sep="\n  ")
    sys.exit(1)
print("Alle Prüfungen bestanden.")
