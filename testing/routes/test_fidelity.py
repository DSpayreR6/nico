#!/usr/bin/env python3
"""Die Vergleichsfunktion der Gegenprobe (nico/fidelity.py), Phase 1.

Entscheidung 2026-11-19 (Martin, manifest „Gegenprobe"): Nach dem Einlesen
schreibt NiCo probeweise und vergleicht Original und Ergebnis Blatt für Blatt,
voller Pfad und Wert. Hier nur die Gleichheitsregeln: was als gleich gilt und
was als Abweichung erkannt werden muss – die Fälle aus Befund 15 und 17.

Lauf: nix-shell shell.nix --run "python testing/routes/test_fidelity.py"
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from nico import fidelity  # noqa: E402

fails = []
def check(label, cond, got=None):
    print(("  OK   " if cond else "  FAIL ") + label + ("" if cond or got is None else f"  (war: {got})"))
    if not cond: fails.append(label)

def mod(body):
    return "{ config, pkgs, ... }:\n{\n" + body + "\n}\n"

def same(a, b):
    return not fidelity.compare(mod(a), mod(b))

print("1. Gilt als gleich")
check("Block- und Punkt-Form",
      same("services.avahi = { enable = true; openFirewall = false; };",
           "services.avahi.enable = true;\nservices.avahi.openFirewall = false;"))
check("Leerraum und Kommentare",
      same('networking.hostName = "box"; # mein Rechner',
           'networking.hostName   =   "box";'))
check("Leerraum in Strings zählt", not same('a.b = "x  y";', 'a.b = "x y";'))
check("# in einem String ist kein Kommentar", not same('a.b = "#1";', 'a.b = "#2";'))
check("with pkgs gegen pkgs.-Präfix",
      same("environment.systemPackages = with pkgs; [ git vim ];",
           "environment.systemPackages = [ pkgs.git pkgs.vim ];"))
check("Einrückung von ''-Strings",
      same("a.b = ''\n    eins\n      zwei\n  '';", "a.b = ''\n  eins\n    zwei\n'';"))
check("deaktivierter Nix-Brick zählt nicht",
      same("# <brick: SERV / #1 x inactive>\n# services.foo.enable = true;\n# </brick: x>", ""))
check("aktiver Nix-Brick zählt wie jede Zeile",
      same("# <brick: SERV / #1 x>\nservices.foo.enable = true;\n# </brick: x>",
           "services.foo.enable = true;"))

print("\n2. Wird erkannt")
d = fidelity.compare(mod("services.avahi = { enable = true; openFirewall = false; };"),
                     mod("services.avahi = { enable = true; nssmdns4 = true; openFirewall = true; };"))
check("Befund 17: anderer Wert", ("services.avahi.openFirewall", "false", "true") in d.changed, d)
check("ergänzter Wert (nssmdns4)", ("services.avahi.nssmdns4", "true") in d.added, d)
d = fidelity.compare(mod('services.displayManager.autoLogin = { enable = false; user = "bob"; };'),
                     mod('services.displayManager.autoLogin = { enable = true; user = "bob"; };'))
check("Befund 15: enable false → true", len(d.changed) == 1 and not d.missing and not d.added, d)
d = fidelity.compare(mod("networking.networkmanager.enable = false;"), mod(""))
check("fehlende Zeile", d.missing == [("networking.networkmanager.enable", "false")], d)
d = fidelity.compare(mod('users.users.a.extraGroups = [ "wheel" "audio" ];'),
                     mod('users.users.a.extraGroups = [ "audio" "wheel" ];'))
check("nur Reihenfolge: eigene Kategorie", len(d.reordered) == 1 and not d.changed, d)
d = fidelity.compare(mod("users.users.a.uid = 1234 + 1;"), mod("users.users.a.uid = 1234;"))
check("Befund 16: Ausdruck gekürzt", len(d.changed) == 1, d)
d = fidelity.compare(mod("a.b = 1;\na.b = 2;"), mod("a.b = 2;"))
check("doppelte Definition zählt einzeln", d.missing or d.changed, d)

print()
if fails:
    print(f"{len(fails)} FEHLER:", *fails, sep="\n  ")
    sys.exit(1)
print("Alle Prüfungen bestanden.")
