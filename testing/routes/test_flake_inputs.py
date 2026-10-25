"""Test: Fremde Flake-Inputs verlieren ihre `url` nicht.

Hintergrund (2026-10-25, gefunden im VM-Test an einer echten Config):
`build_flake_brix()` legte die Bricks in einem Dict ab, dessen Schlüssel der
Input-Name war. Die flache Schreibweise verteilt einen Input aber auf mehrere
Anweisungen:

    yandex-browser.url = "github:Teu5us/nix-yandex-browser";
    yandex-browser.inputs.nixpkgs.follows = "nixpkgs";

Beide heißen `yandex-browser`, die zweite überschrieb die erste. Übrig blieb
ein Input ohne `url`; Nix suchte den nackten Namen daraufhin in den Flake-
Registries und der Rebuild brach ab mit

    error: cannot find flake 'flake:yandex-browser' in the flake registries

Geprüft wird gegen den *Inhalt* der erzeugten Bricks, nicht gegen deren
Anzahl: ob NiCo einen Input in einen oder zwei Bricks legt, ist eine Frage
der Darstellung – dass keine Zeile verschwindet, ist der Punkt.

Läuft ohne Server und ohne Browser.
"""
import pathlib, sys

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from nico.importer import build_flake_brix

fails = []
def check(label, cond, got=None):
    print(("  OK   " if cond else "  FAIL ") + label + ("" if cond or got is None else f"  (war: {got})"))
    if not cond: fails.append(label)


def brix_text(flake: str) -> str:
    return "\n".join(b["text"] for b in build_flake_brix(flake).values())


HEAD = '''{
  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-26.05";
'''
TAIL = '''  };
  outputs = { self, nixpkgs, ... }: { };
}'''


print("1. Flache Schreibweise – der Fehlerfall")
text = brix_text(HEAD + '''    yandex-browser.url = "github:Teu5us/nix-yandex-browser";
    yandex-browser.inputs.nixpkgs.follows = "nixpkgs";
''' + TAIL)
check("url erhalten", 'yandex-browser.url = "github:Teu5us/nix-yandex-browser";' in text, text)
check("follows erhalten", "yandex-browser.inputs.nixpkgs.follows" in text, text)

print("\n2. Attrset-Schreibweise")
text = brix_text(HEAD + '''    yandex-browser = {
      url = "github:Teu5us/nix-yandex-browser";
      inputs.nixpkgs.follows = "nixpkgs";
    };
''' + TAIL)
check("url erhalten", 'url = "github:Teu5us/nix-yandex-browser";' in text, text)
check("follows erhalten", "inputs.nixpkgs.follows" in text, text)

print("\n3. Gemischt, zwei fremde Inputs")
text = brix_text(HEAD + '''    yandex-browser.url = "github:Teu5us/nix-yandex-browser";
    yandex-browser.inputs.nixpkgs.follows = "nixpkgs";
    agenix = { url = "github:ryantm/agenix"; inputs.nixpkgs.follows = "nixpkgs"; };
''' + TAIL)
check("beide urls erhalten",
      'github:Teu5us/nix-yandex-browser' in text and 'github:ryantm/agenix' in text, text)

print("\n4. Drei Anweisungen zu einem Input")
text = brix_text(HEAD + '''    foo.url = "github:a/foo";
    foo.inputs.nixpkgs.follows = "nixpkgs";
    foo.inputs.flake-utils.follows = "flake-utils";
''' + TAIL)
for needle in ('foo.url = "github:a/foo";',
               "foo.inputs.nixpkgs.follows",
               "foo.inputs.flake-utils.follows"):
    check(f"erhalten: {needle}", needle in text, text)

print("\n5. Bekannte Inputs bleiben außen vor")
# nixpkgs, home-manager, nixos-hardware und plasma-manager erzeugt der
# Generator aus den Panel-Feldern – als Brick wären sie doppelt definiert
text = brix_text(HEAD + '''    home-manager.url = "github:nix-community/home-manager";
    home-manager.inputs.nixpkgs.follows = "nixpkgs";
''' + TAIL)
check("home-manager nicht als Brick", "home-manager" not in text, text)

print("\n6. Gleicher Name in Inputs und Outputs verdrängt sich nicht")
text = brix_text('''{
  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-26.05";
    formatter.url = "github:a/formatter";
  };
  outputs = { self, nixpkgs, ... }: {
    formatter.x86_64-linux = nixpkgs.legacyPackages.x86_64-linux.nixfmt;
  };
}''')
check("Input-Zeile erhalten", 'formatter.url = "github:a/formatter";' in text, text)
check("Output-Zeile erhalten", "formatter.x86_64-linux" in text, text)

print("\n" + ("ALLE PRÜFUNGEN BESTANDEN" if not fails else f"FEHLGESCHLAGEN: {fails}"))
sys.exit(1 if fails else 0)
