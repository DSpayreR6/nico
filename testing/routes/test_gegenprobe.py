#!/usr/bin/env python3
"""Gegenprobe: NiCo erfindet nichts, was der Nutzer nicht geschrieben hat.

Entscheidung Martin 2026-11-19 (manifest „Gegenprobe"), Anlass Befund 17 der
Revision 2026-11-18: NiCo hakte beim Einlesen nach Namen ab und rettete nach
Namen – `openFirewall = false` wurde still zu `true`, `gdm.enable = false`
ebenso, NiCos Vorgaben (NetworkManager) kamen dazu. Jetzt schreibt NiCo nach
dem Einlesen probeweise und vergleicht Blatt für Blatt; ein Formularfeld, das
etwas anderes oder mehr schreiben würde, wird nicht übernommen, die Zeilen
der Datei bleiben Nix-Brix. Was das Panel nur zusätzlich setzen würde, steht
auskommentiert als Vorschlag im Brick.

Geprüft über die echten Routen: Import (/api/import/run, Inhaltsmodus) und
Umbauen einer Host-Datei (/api/file/restructure). Dazu die Validator-Regel
brix_redundant, die jetzt exakt fragt.

Lauf: nix-shell shell.nix --run "python testing/routes/test_gegenprobe.py"
"""
import json, os, re, shutil, subprocess, sys, tempfile
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

from nico import fidelity, generator, importer, server, validator  # noqa: E402
from nico.brix import strip_brick_blocks  # noqa: E402

fails = []
def check(label, cond, got=None):
    print(("  OK   " if cond else "  FAIL ") + label + ("" if cond or got is None else f"  (war: {got})"))
    if not cond: fails.append(label)

client = server.create_app().test_client()
page = client.get("/").get_data(as_text=True)
csrf = re.search(r'<meta name="csrf-token" content="([^"]+)"', page).group(1)
H = {"X-CSRF-Token": csrf, "Content-Type": "application/json"}

def post(url, body):
    r = client.post(url, headers=H, data=json.dumps(body))
    return r.status_code, (r.get_json(silent=True) or {})

def mod(body):
    return "{ config, pkgs, ... }:\n{\n" + body + "}\n"

def code(text):
    """Active code only – every comment and brick marker dropped."""
    return fidelity._code(text)

def nix_parses(text):
    if not shutil.which("nix-instantiate"):
        return True
    r = subprocess.run(["nix-instantiate", "--parse", "-E", text], capture_output=True, text=True)
    return r.returncode == 0

def imported(body):
    """Import one configuration.nix through the route; returns the file."""
    s, r = post("/api/import/run", {"confirmed": True,
                                    "files": [{"path": "configuration.nix", "content": mod(body)}]})
    assert s == 200 and r.get("success"), r
    return (cfg / "configuration.nix").read_text(encoding="utf-8")

def form(text):
    """What the form shows for the written file."""
    return importer.parse_config(strip_brick_blocks(text))

def sound(label, text):
    check(f"{label}: Nix liest die Datei", nix_parses(text))
    check(f"{label}: nichts doppelt definiert", not importer.duplicate_leaf_paths(text),
          importer.duplicate_leaf_paths(text))


print("1. Befund 17: Avahi mit openFirewall = false")
for label, body in [
        ("Block-Form", "  services.avahi = { enable = true; openFirewall = false; };\n"),
        ("Punkt-Form", "  services.avahi.enable = true;\n  services.avahi.openFirewall = false;\n")]:
    out = imported(body)
    check(f"{label}: openFirewall bleibt false", "openFirewall = false" in code(out), code(out))
    check(f"{label}: kein openFirewall = true", "openFirewall = true" not in code(out), code(out))
    check(f"{label}: Avahi-Schalter im Formular aus", not form(out).get("avahi"), form(out).get("avahi"))
    sound(label, out)

print("\n2. Befund 17: GNOME mit ausgeschaltetem GDM")
out = imported("  services.desktopManager.gnome.enable = true;\n"
               "  services.displayManager.gdm.enable = false;\n")
check("gdm bleibt false", "gdm.enable = false" in code(out), code(out))
check("kein gdm.enable = true", "gdm.enable = true" not in code(out), code(out))
check("GNOME bleibt an", "gnome.enable = true" in code(out), code(out))
sound("GNOME", out)

print("\n3. Befund 15 läuft weiter: autoLogin.enable = false")
out = imported('  services.displayManager.autoLogin = { enable = false; user = "bob"; };\n')
check("bleibt aus", "enable = true" not in code(out) and "enable = false" in code(out), code(out))

print("\n4. Nichts dazuerfinden")
out = imported('  networking.hostName = "box";\n')
check("kein NetworkManager aus der Vorgabe", "networkmanager" not in code(out), code(out))
check("Hostname im Formular", form(out).get("hostname") == "box", form(out).get("hostname"))
check("… und nicht zusätzlich als Nix-Brick", '<brick' not in out or 'hostName' not in
      "\n".join(l for l in out.splitlines() if "brick" in l.lower()), out)

print("\n5. Vorschlag statt Ergänzung: Avahi ohne Unteroptionen")
out = imported("  services.avahi.enable = true;\n")
check("nssmdns4 nicht aktiv gesetzt", "nssmdns4" not in code(out), code(out))
check("Vorschlagszeile im Brick", fidelity.HINT_LINE in out, out)
check("nssmdns4 als auskommentierter Vorschlag", "# services.avahi.nssmdns4 = true;" in out, out)
sound("Avahi", out)

print("\n5b. Vorschläge gehören zum Brick des Feldes (PipeWire)")
out = imported("  services.pipewire.enable = true;\n")
check("rtkit als Vorschlag, obwohl kein gemeinsamer Pfad",
      "# security.rtkit.enable = true;" in out, out)
check("pulseaudio als Vorschlag", "# services.pulseaudio.enable = false;" in out, out)
check("einkommentiert: Panel schreibt genau das",
      fidelity.panel_writes_exactly(
          "services.pipewire.enable = true;\nsecurity.rtkit.enable = true;\n"
          "services.pipewire.alsa.enable = true;\nservices.pipewire.pulse.enable = true;\n"
          "services.pulseaudio.enable = false;", generator.generate_configuration_nix))
out = imported('  users.users.vk = { isNormalUser = true; extraGroups = [ "audio" ]; };\n')
check("keine Vorschlags-Überschrift ohne Vorschlag", fidelity.HINT_LINE not in out, out)
out = imported('  users.users.vk = {\n    isNormalUser = true;\n    extraGroups = [\n'
               '      "audio" # warum audio\n    ];\n  };\n')
check("Kommentar am Zeilenende nicht zusätzlich in imported-notes",
      out.count("warum audio") == 1, out.count("warum audio"))

print("\n6. Kommentar wird keine Gruppe")
out = imported('  users.users.vk = {\n    isNormalUser = true;\n    extraGroups = [\n'
               '      "wheel" # sudo\n      "docker" # Access to the "/run/docker.sock"\n    ];\n  };\n')
check("keine Gruppe /run/docker.sock", '"/run/docker.sock"' not in code(out), code(out))
check("docker bleibt Gruppe", '"docker"' in code(out), code(out))
sound("Gruppen", out)

print("\n7. Was das Panel ganz abbildet, nimmt es")
out = imported('  networking.hostName = "box";\n  services.printing.enable = true;\n')
check("Drucken im Formular", form(out).get("printing") is True, form(out))
check("keine Nix-Brix nötig", "<brick:" not in out, out)

print("\n8. Host-Datei umbauen: Teilblock bleibt (useDHCP)")
(cfg / "config.json").write_text(json.dumps({"hosts_dir": "hosts"}), encoding="utf-8")
(cfg / "hosts" / "atlas").mkdir(parents=True, exist_ok=True)
(cfg / "hosts" / "atlas" / "default.nix").write_text(
    mod('  networking = {\n    hostName = "atlas";\n    useDHCP = true;\n  };\n'), encoding="utf-8")
s, r = post("/api/file/restructure", {"path": "hosts/atlas/default.nix"})
host = (cfg / "hosts" / "atlas" / "default.nix").read_text(encoding="utf-8")
check("Umbauen ok", s == 200 and r.get("success"), r)
check("useDHCP = true bleibt", "useDHCP = true" in code(host), code(host))
check("Hostname bleibt", 'hostName = "atlas"' in code(host), code(host))
sound("Host", host)

print("\n9. brix_redundant fragt exakt")
render = generator.generate_configuration_nix
check("Avahi ohne nssmdns4: nicht im Panel abbildbar",
      not fidelity.panel_writes_exactly("services.avahi.enable = true;", render))
check("Vorschlag einkommentiert: jetzt abbildbar",
      fidelity.panel_writes_exactly("services.avahi.enable = true;\nservices.avahi.nssmdns4 = true;\n"
                                    "services.avahi.openFirewall = true;", render))
check("Drucken: abbildbar", fidelity.panel_writes_exactly("services.printing.enable = true;", render))
check("openFirewall = false: nie abbildbar",
      not fidelity.panel_writes_exactly("services.avahi = { enable = true; nssmdns4 = true; "
                                        "openFirewall = false; };", render))
out = imported("  services.avahi.enable = true;\n")
found = [f for f in validator._rule_brix_redundant(str(cfg), {}, False)]
check("nach dem Import meldet die Regel den Avahi-Brick nicht", not found, [f.params for f in found])

print("\n10. Befund 18: ausgeschalteter NetworkManager bleibt aus")
from nico import config_manager  # noqa: E402
(cfg / "config.json").unlink(missing_ok=True)
shutil.rmtree(cfg / "hosts", ignore_errors=True)
def nm_on():
    return "networkmanager.enable = true" in code((cfg / "configuration.nix").read_text(encoding="utf-8"))
for nm in (False, True):
    d = config_manager.get_defaults()
    d.update(hostname="box", networkmanager=nm, _flake_mode=False)
    config_manager.save_config(str(cfg), d)
    word = "an" if nm else "aus"
    check(f"Formular speichert {word}", nm_on() is nm)
    s, r = post("/api/write", {"commit": False})
    check(f"… nach /api/write weiter {word}", s == 200 and nm_on() is nm, r)
    s, r = post("/api/packages/add", {"attr": "git", "name": "git"})
    check(f"… nach Paket hinzufügen weiter {word}", s == 200 and nm_on() is nm, r)
    r = client.delete("/api/packages/git", headers=H)
    check(f"… nach Paket entfernen weiter {word}", r.status_code == 200 and nm_on() is nm)
shutil.rmtree(cfg / "configuration.nix", ignore_errors=True)
(cfg / "configuration.nix").unlink(missing_ok=True)
check("neue Config ohne Datei: Vorgabe NetworkManager an",
      config_manager.load_config(str(cfg)).get("networkmanager") is True)

print("\n11. Home Manager (Befunde 11, 12, 13)")
from nico.core import normalize_hm_content  # noqa: E402
for label, body, must, mustnot in [
        ("11 zsh bleibt aus", '  programs.zsh = { enable = false; };\n', ["enable = false"], ["enable = true"]),
        ("11 fish bleibt aus", '  programs.fish = { enable = false; };\n', ["enable = false"], ["enable = true"]),
        ("12 Kommentar schaltet Firefox nicht ein",
         '  home.username = "bob";\n  # programs.firefox.enable = true;\n', [], ["firefox"]),
        ("13 Leerraum im Git-Namen bleibt",
         '  programs.git = { enable = true; userName = "Team  Archiv"; };\n', ['"Team  Archiv"'], []),
        ("13 Leerraum im XDG-Pfad bleibt",
         '  xdg.userDirs = { enable = true; createDirectories = true; '
         'download = "${config.home.homeDirectory}/Team  Archiv"; };\n', ["Team  Archiv"], [])]:
    out, _ = normalize_hm_content(mod(body))
    check(f"{label}", all(m in code(out) for m in must) and not any(m in code(out) for m in mustnot),
          code(out))
    check(f"… neu geschrieben, nicht nur stehen gelassen", "nico-version" in out)
    d = fidelity.compare(mod(body), out)
    check(f"… sagt dasselbe", not fidelity.offending(d) and not d.missing, d)
out, _ = normalize_hm_content(mod('  home.username = "bob";\n  home.homeDirectory = "/home/bob";\n'))
check("Abbildbares HM braucht keinen Nix-Brick", "<brick:" not in out, out)

print("\n12. flake.nix: sagt NiCo etwas anderes, bleibt die Datei")
from nico import core  # noqa: E402
fd = Path(tempfile.mkdtemp(prefix="nico-flk-"))
(fd / "configuration.nix").write_text(mod('  networking.hostName = "alpha";\n'), encoding="utf-8")
def flake_with(hm_url):
    return ('{\n  description = "test";\n  inputs = {\n'
            '    nixpkgs.url = "github:nixos/nixpkgs/nixos-unstable";\n'
            f'    home-manager = {{\n      url = "{hm_url}";\n'
            '      inputs.nixpkgs.follows = "nixpkgs";\n    };\n  };\n'
            '  outputs = { self, nixpkgs, home-manager, ... }: {\n'
            '    nixosConfigurations = {\n      alpha = nixpkgs.lib.nixosSystem {\n'
            '        system = "x86_64-linux";\n        modules = [ ./configuration.nix ];\n'
            '      };\n    };\n  };\n}\n')
src = flake_with("github:nix-community/home-manager")
(fd / "flake.nix").write_text(src, encoding="utf-8")
took = core.reimport_flake(str(fd), config_manager.load_config(str(fd)))
check("HM-Quelle ohne Zweig: Flake bleibt wie sie kam",
      not took and (fd / "flake.nix").read_text(encoding="utf-8") == src)
src = flake_with("github:nix-community/home-manager/master")
(fd / "flake.nix").write_text(src, encoding="utf-8")
took = core.reimport_flake(str(fd), config_manager.load_config(str(fd)))
after = (fd / "flake.nix").read_text(encoding="utf-8")
check("abbildbare Flake wird übernommen", took and "nico-version" in after, after)
d = fidelity.compare(src, after)
check("… und sagt dasselbe", not fidelity.offending(d) and not d.missing, d)

print("\n13. ZIP-Import + categorize: fremde Host-Datei bleibt ungestempelt (VM-Test)")
import io, zipfile as _zf  # noqa: E402
(cfg / "config.json").write_text(json.dumps({"hosts_dir": "hosts"}), encoding="utf-8")
buf = io.BytesIO()
with _zf.ZipFile(buf, "w") as z:
    z.writestr("configuration.nix", mod('  networking.hostName = "box";\n'))
    z.writestr("config.json", json.dumps({"hosts_dir": "hosts"}))
    z.writestr("hosts/vm/default.nix",
               mod('  networking = {\n    hostName = "vm";\n    useDHCP = true;\n  };\n'))
r = client.post("/api/import/zip/apply", headers={"X-CSRF-Token": csrf},
                data={"confirmed": "true", "file": (io.BytesIO(buf.getvalue()), "t.zip")},
                content_type="multipart/form-data")
check("ZIP importiert", r.status_code == 200, r.get_json())
post("/api/categorize", {})
VMH = "hosts/vm/default.nix"
r = client.get(f"/api/file?path={VMH}", headers=H).get_json()
check("Host gilt nach categorize nicht als von NiCo geschrieben", r.get("outside") == "no_hash",
      r.get("outside"))
s, r = post("/api/file/restructure", {"path": VMH})
check("Umbauen ok", s == 200 and r.get("success"), r)
check("useDHCP bleibt", "useDHCP = true" in code((cfg / VMH).read_text(encoding="utf-8")))

print("\n14. Validator: Benutzer in Formular und Nix-Brick zugleich (VM-Test)")
out = imported('  users.users.tester = {\n    isNormalUser = true;\n    initialPassword = "test";\n'
               '    extraGroups = [\n      "wheel"\n      "audio" # access to "/run/audio-socket"\n    ];\n  };\n')
check("Ausgangslage: Benutzer steht in zwei Blöcken",
      code(out).count("users.users.tester = {") == 2, code(out))
found = validator._rule_user_no_password(str(cfg), {}, False)
check("keine Meldung „kein Passwort“", not [f for f in found if f.params and f.params[0] == "tester"],
      [f.params for f in found])
found = validator._rule_no_admin_user(str(cfg), {}, False)
check("wheel aus dem Brick zählt", not found, [f.params for f in found])

print("\n15. Text nach der letzten Klammer: Punkt wie Buchstabe (VM-Test)")
body = mod('  networking.hostName = "box";\n')
check("Punkt nach }: nicht abbildbar", importer.config_unmappable(body.rstrip() + "\n.\n"))
check("Buchstabe nach }: nicht abbildbar", importer.config_unmappable(body.rstrip() + "\nx\n"))
check("Punkt vor }: weiter abbildbar (Fall A, Umbauen lässt ihn weg)",
      not importer.config_unmappable(body.rstrip()[:-1] + ".\n}\n"))
check("Kommentar nach }: abbildbar", not importer.config_unmappable(body.rstrip() + "\n# ende\n"))

print("\n16. brix_redundant: reine Kommentar-Bricks (VM-Test 2026-11-21)")
from nico import brix as _brix  # noqa: E402
def redundant(inner):
    content = mod("  # <brick: MISC / #1 notes>\n" + inner + "  # </brick: notes>\n")
    return validator._brix_redundant_in(content, render, importer, fidelity,
                                        _brix.extract_brick_blocks)
notes = ("  # Enable the X11 windowing system.\n"
         "  # services.xserver.enable = true;\n"
         "  # services.printing.enable = true;\n")
check("nur Kommentare (wie imported-notes): kein Fund", not redundant(notes),
      [f.params for f in redundant(notes)])
check("Blockkommentar allein: kein Fund", not redundant("  /* services.printing.enable = true; */\n"))
check("echter Panel-Brick meldet weiter",
      bool(redundant("  services.printing.enable = true;\n")))
check("Panel-Brick mit Kommentar meldet weiter",
      bool(redundant("  # Drucker\n  services.printing.enable = true;\n")))

print()
if fails:
    print(f"{len(fails)} FEHLER:", *fails, sep="\n  ")
    sys.exit(1)
print("Alle Prüfungen bestanden.")
