"""Test: Teilweise erkannte Blöcke verlieren ihre übrigen Felder nicht.

Hintergrund (2026-10-24): `_mark()` im Importer stempelte bei einem
verschachtelten Schlüssel den ganzen Elternblock als verstanden ab
(`importer.py`). NiCo kennt von `services.openssh` nur `enable` – alles
andere fiel beim ersten Speichern weg, ohne Meldung. Gemessen an einer
praxisnahen Config: 15 von 18 Nachbarfeldern, darunter
`settings.PasswordAuthentication = false` und `ports = [ 2222 ]`.

Gerettet wird gegen die *tatsächliche* Ausgabe des Generators, nicht gegen
eine gepflegte Liste bekannter Felder: eine Liste veraltet, sobald ein Feld
dazukommt, und jeder veraltete Eintrag erzeugt ein doppelt definiertes
Attribut, das Nix ablehnt.

Läuft ohne Server und ohne Browser.
"""
import json, pathlib, sys, tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from nico import core, generator, importer
from nico.brix import section_for_attrpath, strip_brick_blocks

fails = []
def check(label, cond, got=None):
    print(("  OK   " if cond else "  FAIL ") + label + ("" if cond or got is None else f"  (war: {got})"))
    if not cond: fails.append(label)


CONFIG = '''{ config, pkgs, ... }:
{
  services.openssh = {
    enable = true;
    settings.PasswordAuthentication = false;
    ports = [ 2222 ];
  };

  nix.gc = {
    automatic = true;
    dates = "weekly";
    options = "--delete-older-than 30d";
    persistent = true;
  };

  services.xserver.xkb = {
    layout = "de";
    variant = "";
    options = "ctrl:nocaps";
  };

  programs.firefox = {
    enable = true;
    policies.DisableTelemetry = true;
  };

  system.stateVersion = "25.11";
}
'''


def roundtrip(src: str) -> tuple[dict, str]:
    """Derselbe Weg wie die Import-Routen und testing/run.py.

    Wichtig ist das Einlesen der vorhandenen Bricks: ohne das verliert jede
    zweite Runde alle Bricks, und der Stabilitätsvergleich unten misst dann
    den Testaufbau statt des Codes.
    """
    from nico.brix import brix_content_to_bricks, extract_brick_blocks

    data = importer.parse_config(src)
    rest = importer.build_rest_brix(strip_brick_blocks(src), data)
    blocks = extract_brick_blocks(src)
    if rest.strip():
        blocks.update(brix_content_to_bricks(rest, section="HEAD", existing_blocks=blocks))
    data["brick_blocks"] = blocks
    importer.rescue_into_bricks(data, src)
    return data, generator.generate_configuration_nix(data)


print("1. Blattpfade werden vollständig erfasst")
paths = importer.content_leaf_paths(CONFIG)
for expected in ("services.openssh.enable", "services.openssh.settings.PasswordAuthentication",
                 "services.openssh.ports", "nix.gc.persistent", "programs.firefox.policies.DisableTelemetry"):
    check(expected, expected in paths)
check("Liste wird nicht zerlegt", "services.openssh.ports" in paths
      and not any(p.startswith("services.openssh.ports.") for p in paths))

print("\n2. Nichts geht verloren")
data, out = roundtrip(CONFIG)
for needle in ("PasswordAuthentication", "2222", "ctrl:nocaps", "persistent = true", "DisableTelemetry"):
    check(f"{needle} erhalten", needle in out)

print("\n3. Erkannte Felder stehen genau einmal")
for attr, count in (("enable = true", None), ("layout", 1), ("dates", 1), ("automatic", 1)):
    if count is None:
        continue
    check(f"{attr} einmal", out.count(attr) == count, out.count(attr))
check("keine doppelten Blattpfade", importer.duplicate_leaf_paths(out) == [],
      importer.duplicate_leaf_paths(out))

print("\n4. Mehrfaches Speichern bleibt stabil")
prev = out
for runde in (2, 3):
    _, nxt = roundtrip(prev)
    check(f"Runde {runde} identisch", nxt == prev)
    prev = nxt

print("\n5. Bricks landen im thematisch passenden Abschnitt")
for path, section in (("services.openssh", "NETW"), ("services.printing", "SERV"),
                      ("nix.gc", "NIXS"), ("programs.firefox", "PROG"),
                      ("services.pipewire", "AUDI"), ("boot.loader.systemd-boot", "BOOT"),
                      ("services.xserver.xkb", "I18N"), ("users.users.x", "USER")):
    check(f"{path} → {section}", section_for_attrpath(path) == section, section_for_attrpath(path))
check("Unbekanntes fällt auf HEAD zurück", section_for_attrpath("foo.bar") == "HEAD")

print("\n6. Strings und Kommentare verschieben keine Grenzen")
TRICKY = '''{ config, pkgs, ... }:
{
  environment.etc."test".text = \'\'
    these braces { } and semicolons ; are text
    font-5 = something
  \'\';
  # a comment with { and ; inside
  services.openssh = {
    enable = true;
    settings.PermitRootLogin = "no";
  };
  system.stateVersion = "25.11";
}
'''
tricky_paths = importer.content_leaf_paths(TRICKY)
check("String erzeugt kein Attribut", not any("font-5" in p for p in tricky_paths), tricky_paths)
check("Attribut hinter dem String erkannt",
      "services.openssh.settings.PermitRootLogin" in tricky_paths, sorted(tricky_paths))
_, tricky_out = roundtrip(TRICKY)
check("String bleibt erhalten", "font-5 = something" in tricky_out)
check("PermitRootLogin erhalten", "PermitRootLogin" in tricky_out)

print("\n7. Schreiben verweigert eine Datei mit doppeltem Attribut")
with tempfile.TemporaryDirectory() as tmp:
    d = pathlib.Path(tmp)
    # write_config_files liest die Bricks aus der Datei auf Platte, nicht aus
    # den übergebenen Daten – der Brick muss also dort stehen, sonst prüft man
    # am eigentlichen Weg vorbei.
    clash = {
        "hostname": "h", "username": "x",
        "brick_blocks": {"kaputt": {
            "section": "SYST", "order": 1,
            "text": '# <brick: SYST / #1 kaputt>\nnetworking.hostName = "anders";\n# </brick: kaputt>',
        }},
    }
    (d / "configuration.nix").write_text(
        generator.generate_configuration_nix(clash), encoding="utf-8")
    before = (d / "configuration.nix").read_text(encoding="utf-8")

    res = core.write_config_files(str(d), {"hostname": "h", "username": "x"}, commit=False)
    check("abgelehnt", res.get("success") is False, res.get("success"))
    check("Fehlercode ERR_DUPLICATE_ATTR", res.get("error") == "ERR_DUPLICATE_ATTR", res.get("error"))
    check("betroffenes Attribut genannt",
          "networking.hostName" in (res.get("duplicates") or []), res.get("duplicates"))
    check("Datei bleibt unangetastet",
          (d / "configuration.nix").read_text(encoding="utf-8") == before)

    (d / "configuration.nix").write_text(generator.generate_configuration_nix(
        {"hostname": "h", "username": "x"}), encoding="utf-8")
    res2 = core.write_config_files(str(d), {"hostname": "h", "username": "x"}, commit=False)
    check("saubere Daten werden geschrieben", res2.get("success") is True, res2)

print("\n8. Fehlertext in allen sieben Sprachen")
for p in sorted((ROOT / "nico" / "static" / "lang").glob("*.json")):
    val = json.loads(p.read_text(encoding="utf-8")).get("errors.ERR_DUPLICATE_ATTR")
    check(f"{p.stem}", bool(val) and val != "__TODO__", val)

print("\n" + ("ALLE PRÜFUNGEN BESTANDEN" if not fails else f"FEHLGESCHLAGEN: {fails}"))
sys.exit(1 if fails else 0)
