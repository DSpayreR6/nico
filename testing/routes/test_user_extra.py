"""Test: Unbekannte Zeilen im users-Block gehen beim Speichern nicht verloren.

Hintergrund (2026-10-24): Der Importer stempelte den ganzen
`users.users.<name>`-Block als verstanden ab, las daraus aber nur vier Felder.
Alles andere verschwand beim ersten Speichern, ohne Meldung – in der
Standard-Config jeder grafischen NixOS-Installation betraf das
`packages = with pkgs; [ kdePackages.kate ];`, bei gewachsenen Configs auch
`hashedPassword` und SSH-Schlüssel.

Läuft ohne Server und ohne Browser.
"""
import json, pathlib, sys

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from nico import generator, importer
from nico.validator import _rule_user_extra_conflict as conflict_rule

fails = []
def check(label, cond, got=None):
    print(("  OK   " if cond else "  FAIL ") + label + ("" if cond or got is None else f"  (war: {got})"))
    if not cond: fails.append(label)


CALAMARES = '''{ config, pkgs, ... }:
{
  networking.hostName = "nixos";

  users.users.nico = {
    isNormalUser = true;
    description = "nico";
    extraGroups = [ "networkmanager" "wheel" ];
    packages = with pkgs; [
      kdePackages.kate
    #  thunderbird
    ];
  };

  system.stateVersion = "25.11";
}
'''

print("1. Import rettet die unbekannten Zeilen")
data = importer.parse_config(CALAMARES)
extra = data.get("user_extra_nix", "")
check("Nutzer erkannt", data.get("username") == "nico", data.get("username"))
check("Formularfelder weiter erkannt", data.get("user_description") == "nico")
check("packages gerettet", "kdePackages.kate" in extra, extra)
check("Kommentar im Block erhalten", "#  thunderbird" in extra, extra)
check("kein Formularfeld im Freitext", "isNormalUser" not in extra and "extraGroups" not in extra, extra)

print("\n2. Schreiben bringt sie zurück")
out = generator.generate_configuration_nix(data)
check("packages steht wieder in der Datei", "kdePackages.kate" in out)
check("isNormalUser genau einmal", out.count("isNormalUser") == 1, out.count("isNormalUser"))
check("packages genau einmal", out.count("packages =") == 1, out.count("packages ="))

print("\n3. Mehrfaches Speichern ändert nichts mehr (keine Einrückungs-Eskalation)")
prev = out
for runde in (2, 3, 4):
    again = importer.parse_config(prev)
    nxt = generator.generate_configuration_nix(again)
    check(f"Runde {runde} identisch", nxt == prev)
    prev = nxt

print("\n4. Felder, für die es nie ein Formular geben wird")
WITH_SECRETS = CALAMARES.replace(
    '    packages = with pkgs; [',
    '    hashedPassword = "$6$rounds=100000$abc$xyz";\n'
    '    openssh.authorizedKeys.keys = [ "ssh-ed25519 AAAAC3Nza key@host" ];\n'
    '    packages = with pkgs; [',
)
d2 = importer.parse_config(WITH_SECRETS)
o2 = generator.generate_configuration_nix(d2)
for needle in ('hashedPassword', '$6$rounds=100000$abc$xyz', 'openssh.authorizedKeys.keys', 'ssh-ed25519'):
    check(f"{needle} erhalten", needle in o2)

print("\n5. Dienstnutzer bleibt unangetastet als Nix-Brick")
SERVICE = '''{ config, pkgs, ... }:
{
  users.users.dienst = {
    isSystemUser = true;
    group = "dienst";
  };
}
'''
d3 = importer.parse_config(SERVICE)
rest = importer.build_rest_brix(SERVICE, d3)
check("nicht als Formular-Nutzer übernommen", not d3.get("username"), d3.get("username"))
check("vollständig im Brick", "isSystemUser" in rest and "group = \"dienst\"" in rest, rest)
check("kein isNormalUser dazuerfunden", "isNormalUser" not in rest)

print("\n6. Validator meldet selbst getippte Dubletten")
check("sauberer Freitext ohne Befund",
      conflict_rule("/tmp", {"username": "nico", "user_extra_nix": 'packages = [ ];'}, False, None) == [])
hit = conflict_rule("/tmp", {"username": "nico",
                             "user_extra_nix": '# Kommentar\ndescription = "doppelt";'}, False, None)
check("getipptes description gemeldet", len(hit) == 1, hit)
check("Schweregrad error", bool(hit) and hit[0].severity == "error")
check("Attributname im Befund", bool(hit) and hit[0].params[1] == "description", hit[0].params if hit else None)
hit2 = conflict_rule("/tmp", {"username": "a",
                              "extra_users": [{"username": "b", "extra_nix": "shell = pkgs.zsh;"}]},
                     False, None)
check("weiterer Nutzer wird mitgeprüft", len(hit2) == 1 and hit2[0].params[0] == "b", hit2)

print("\n7. Meldungstexte in allen sieben Sprachen")
for p in sorted((ROOT / "nico" / "static" / "lang").glob("*.json")):
    d = json.loads(p.read_text(encoding="utf-8"))
    vals = [d.get(k) for k in ("validator.f.user_extra_conflict",
                               "validator.d.user_extra_conflict",
                               "validator.rule.user_extra_conflict.label",
                               "validator.rule.user_extra_conflict.desc")]
    check(f"{p.stem}", all(v and v != "__TODO__" for v in vals), vals)

print("\n" + ("ALLE PRÜFUNGEN BESTANDEN" if not fails else f"FEHLGESCHLAGEN: {fails}"))
sys.exit(1 if fails else 0)
