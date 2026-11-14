"""Test: zusätzliche Bibliotheken für AppImages (`programs.appimage.package`).

Hintergrund (2026-11-14): Ein AppImage brach mit
`libgpg-error.so.0: cannot open shared object file` ab – `appimage-run`
bringt die Bibliothek nicht mit. Der im NixOS-Handbuch vorgesehene Weg ist
`programs.appimage.package = pkgs.appimage-run.override { extraPkgs = … }`.
NiCo schreibt das jetzt aus einem Formularfeld.

Geprüft wird:
- Der Generator schreibt die Zeilen nur bei gefülltem Feld und nur mit
  einfachen Paketnamen.
- Der Importer liest genau diese Form zurück (Block und flache Schreibweise).
- Jede andere Form von `package` bleibt als Nix-Brick in der Datei.
- Die erzeugte Datei kann Nix lesen.

Läuft ohne Server und ohne Browser.
"""
import pathlib, shutil, subprocess, sys, tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from nico import config_manager, generator, importer
from nico.brix import strip_brick_blocks

fails = []
def check(label, cond, got=None):
    print(("  OK   " if cond else "  FAIL ") + label + ("" if cond or got is None else f"  (war: {got!r})"))
    if not cond: fails.append(label)


def gen(**fields) -> str:
    data = config_manager.get_defaults()
    data.update(fields)
    return generator.generate_configuration_nix(data)


def load(text: str) -> tuple[dict, str]:
    """Write *text* as configuration.nix, load it the way NiCo does, and
    return (data, file content after the load)."""
    d = pathlib.Path(tempfile.mkdtemp(prefix="nico-appimage-"))
    try:
        (d / "configuration.nix").write_text(text, encoding="utf-8")
        data = config_manager.load_config(str(d))
        return data, (d / "configuration.nix").read_text(encoding="utf-8")
    finally:
        shutil.rmtree(d, ignore_errors=True)


print("Generator")
out = gen(appimage=True, appimage_libs="libgpg-error, libepoxy")
check("package-Zeile mit beiden Namen",
      "package = pkgs.appimage-run.override {" in out
      and "extraPkgs = pkgs: [ pkgs.libgpg-error pkgs.libepoxy ];" in out, out)
check("enable und binfmt bleiben", "enable = true;" in out and "binfmt = true;" in out)

out = gen(appimage=True, appimage_libs="")
check("leeres Feld: kein package", "appimage-run" not in out, out)
out = gen(appimage=True)
check("Feld fehlt: kein package", "appimage-run" not in out)
out = gen(appimage=False, appimage_libs="libgpg-error")
check("AppImage aus: nichts geschrieben", "appimage" not in out, out)

check("pkgs.-Präfix und Leerzeichen als Trenner",
      generator.appimage_lib_names("pkgs.libgpg-error  libepoxy,libepoxy")
      == ["libgpg-error", "libepoxy"])
check("Name mit Punkt bleibt",
      generator.appimage_lib_names("kdePackages.kio") == ["kdePackages.kio"])
bad = generator.appimage_lib_names('ok, a b; rm, "x", ${y}, libz.so.1, [z]')
check("kein Nix-Code kommt durch", all(
      generator._ATTR_NAME_RE.fullmatch(n) for n in bad)
      and ";" not in " ".join(bad) and "$" not in " ".join(bad), bad)
out = gen(appimage=True, appimage_libs="a; boom = true")
check("Semikolon erzeugt kein zweites Attribut", "boom = true;" not in out, out)

print("Importer – eigene Form")
written = gen(appimage=True, appimage_libs="libgpg-error, libepoxy")
parsed = importer.parse_config(strip_brick_blocks(written))
check("Block: Namen zurückgelesen",
      parsed.get("appimage_libs") == "libgpg-error, libepoxy", parsed.get("appimage_libs"))
check("Block: Häkchen gesetzt", parsed.get("appimage") is True)

FLAT = '''{ config, pkgs, ... }:
{
  programs.appimage.enable = true;
  programs.appimage.binfmt = true;
  programs.appimage.package = pkgs.appimage-run.override {
    extraPkgs = pkgs: [ pkgs.libgpg-error ];
  };
  system.stateVersion = "25.11";
}
'''
parsed = importer.parse_config(FLAT)
check("flach: Namen zurückgelesen", parsed.get("appimage_libs") == "libgpg-error",
      parsed.get("appimage_libs"))
# Only the package statement is looked at: a flat `binfmt` is left as a
# rest brick by the importer, which is older than this field.
check("flach: package bleibt nicht als Rest",
      "appimage-run" not in importer.build_rest_brix(FLAT, parsed),
      importer.build_rest_brix(FLAT, parsed))

print("Roundtrip über die Datei")
data, after = load(written)
check("Feld nach dem Laden gefüllt",
      data.get("appimage_libs") == "libgpg-error, libepoxy", data.get("appimage_libs"))
again = generator.generate_configuration_nix(data)
check("zweimal geschrieben = gleich", again.count("appimage-run") == 1
      and "extraPkgs = pkgs: [ pkgs.libgpg-error pkgs.libepoxy ];" in again)
check("kein doppeltes Attribut", importer.duplicate_leaf_paths(again) == [],
      importer.duplicate_leaf_paths(again))

print("Fremde Form bleibt Nix-Brick")
FOREIGN = [
    ("anderes Paket", "package = pkgs.my-appimage-run;"),
    ("with pkgs", "package = pkgs.appimage-run.override {\n      extraPkgs = pkgs: with pkgs; [ libepoxy ];\n    };"),
    ("weiteres Argument", "package = pkgs.appimage-run.override {\n      extraPkgs = pkgs: [ pkgs.libepoxy ];\n      appimageTools = pkgs.appimageTools;\n    };"),
    ("Ausdruck in der Liste", "package = pkgs.appimage-run.override {\n      extraPkgs = pkgs: [ pkgs.libepoxy (pkgs.callPackage ./x.nix {}) ];\n    };"),
]
for label, stmt in FOREIGN:
    text = ('{ config, pkgs, ... }:\n{\n  programs.appimage = {\n    enable = true;\n'
            '    binfmt = true;\n    ' + stmt + '\n  };\n  system.stateVersion = "25.11";\n}\n')
    parsed = importer.parse_config(text)
    check(f"{label}: Feld bleibt leer", not parsed.get("appimage_libs"),
          parsed.get("appimage_libs"))
    d = pathlib.Path(tempfile.mkdtemp(prefix="nico-appimage-"))
    try:
        (d / "configuration.nix").write_text(text, encoding="utf-8")
        data = config_manager.load_config(str(d))
        rescued = importer.rescue_lost_bindings(text, data)
        kept = " ".join(s for _, s in rescued)
        # The distinctive part of each statement must survive somewhere.
        needle = stmt.split("\n")[1].strip() if "\n" in stmt else stmt
        check(f"{label}: Inhalt wird gerettet", needle in kept, kept)
    finally:
        shutil.rmtree(d, ignore_errors=True)

print("binfmt (2026-11-14)")
HEAD = '{ config, pkgs, ... }:\n{\n'
TAIL = '  system.stateVersion = "25.11";\n}\n'
text = HEAD + '  programs.appimage.enable = true;\n  programs.appimage.binfmt = true;\n' + TAIL
parsed = importer.parse_config(text)
check("flach true: Häkchen gesetzt", parsed.get("appimage") is True)
check("flach true: kein Rest-Brick neben der erzeugten Zeile",
      importer.build_rest_brix(text, parsed) == "", importer.build_rest_brix(text, parsed))
for label, text in [
    ("flach false", HEAD + '  programs.appimage.enable = true;\n  programs.appimage.binfmt = false;\n' + TAIL),
    ("Block false", HEAD + '  programs.appimage = {\n    enable = true;\n    binfmt = false;\n  };\n' + TAIL),
]:
    parsed = importer.parse_config(text)
    rest = importer.build_rest_brix(text, parsed)
    check(f"{label}: nicht ins Formular", not parsed.get("appimage"), parsed.get("appimage"))
    check(f"{label}: bleibt als Nix-Brick mit false",
          "binfmt = false;" in rest and "enable = true;" in rest, rest)

print("Nix liest die erzeugte Datei")
nix = shutil.which("nix-instantiate")
if nix:
    d = pathlib.Path(tempfile.mkdtemp(prefix="nico-appimage-"))
    try:
        f = d / "configuration.nix"
        f.write_text(gen(appimage=True, appimage_libs="libgpg-error, libepoxy"), encoding="utf-8")
        r = subprocess.run([nix, "--parse", str(f)], capture_output=True, text=True)
        check("nix-instantiate --parse", r.returncode == 0, r.stderr[-300:])
    finally:
        shutil.rmtree(d, ignore_errors=True)
else:
    print("  --   nix-instantiate nicht gefunden, übersprungen")

print()
if fails:
    print(f"{len(fails)} FEHLER:")
    for f in fails: print("  -", f)
    sys.exit(1)
print("Alle Prüfungen bestanden.")
