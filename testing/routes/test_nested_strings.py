"""Test: `#` in einer Zeichenkette innerhalb von `${ … }` ist kein Kommentar.

Hintergrund (2026-11-21): Der Roundtrip über NiCos echte Wege strukturierte
eine Home-Manager-Datei mit

    color = "rgb(${lib.removePrefix "#" config.colorscheme.colors.on_surface})";

um und schrieb ungültiges Nix: Die Zeile endete nach `removePrefix "`. Alle
drei Text-Läufer im Importer (_strip_nix_comments, _blank_comments,
_mask_literals) beendeten die Zeichenkette am inneren Anführungszeichen und
hielten das `#` für einen Kommentar. Die Gegenprobe benutzte denselben
Läufer, schnitt beide Seiten gleich ab und sah keinen Unterschied.

Geprüft wird:
  1. Der gemeinsame Läufer (_nix_segments) kennt verschachtelte Strings,
     `''…''`-Escapes und `$${`.
  2. Die Home-Manager-Umstrukturierung schreibt die Zeile vollständig und
     gültig.
  3. Die Gegenprobe lehnt einen Text ab, den Nix nicht lesen kann, auch wenn
     der Blattvergleich nichts findet.
  4. Die unabhängigen Roundtrip-Prüfungen (tree-sitter) schlagen an:
     verlorenes Attribut, doppeltes Attribut; zusammengeführte Blöcke sind
     kein Doppel.

Lauf: nix-shell shell.nix --run "python testing/routes/test_nested_strings.py"
"""
import pathlib, sys

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "testing"))

from nico import core, fidelity, importer, nix_parser  # noqa: E402
import run as roundtrip  # noqa: E402

fails = []
def check(label, cond, got=None):
    print(("  OK   " if cond else "  FAIL ") + label + ("" if cond or got is None else f"  (war: {got!r})"))
    if not cond: fails.append(label)


LINE = 'color = "rgb(${lib.removePrefix "#" config.colorscheme.colors.on_surface})";'

# ── 1. Läufer ────────────────────────────────────────────────────────────────
print("1. Gemeinsamer Läufer")
src = (LINE + ' # weg\n'
       "b = \"#1e1e2e\"; c = '' x ${ \"#\" } ''$ y ''; /* k { */ d = \"$${x} # z\";\n"
       "e = ''\n  echo ''${HOME} # bleibt\n''; f = 1; # weg2\n")
stripped = importer._strip_nix_comments(src)
check("verschachtelter String bleibt ganz", LINE in stripped, stripped)
check("echter Kommentar entfernt", "# weg" not in stripped and "weg2" not in stripped, stripped)
check("$${ … # …} ist Text", '"$${x} # z"' in stripped, stripped)
check("''${ in ''…'' ist Text", "''${HOME} # bleibt" in stripped, stripped)
check("/* */ entfernt", "/* k" not in stripped, stripped)
blank = importer._blank_comments(src)
check("_blank_comments: Länge gleich, String ganz", len(blank) == len(src) and LINE in blank)
mask = importer._mask_literals(src)
check("_mask_literals: Länge und Zeilen gleich",
      len(mask) == len(src) and mask.count("\n") == src.count("\n"))
check("_mask_literals: kein # und keine { aus Strings/Kommentaren übrig",
      "#" not in mask and "{" not in mask, mask)

# ── 2. Home-Manager-Umstrukturierung ─────────────────────────────────────────
print("2. Home-Manager-Umstrukturierung")
hm = ('{config, lib, ...}: {\n  programs.hyprlock = {\n    enable = true;\n'
      '    settings.label = {\n      ' + LINE + '\n      # Kommentar\n    };\n  };\n}\n')
out = core.normalize_hm_content(hm)[0]
check("Zeile vollständig im Ergebnis", LINE in out, out)
tree = nix_parser.parse_tree(out)
check("Ergebnis ist gültiges Nix", tree is not None and not tree.root_node.has_error)

# ── 3. Gegenprobe ────────────────────────────────────────────────────────────
print("3. Gegenprobe lehnt unlesbares Nix ab")
good = "{ ... }:\n{\n  a = 1;\n}\n"
broken = "{ ... }:\n{\n  a = 1;\n  b = \"rgb(${f \"\n}\n"
check("breaks_syntax erkennt kaputtes Ergebnis", fidelity.breaks_syntax(good, broken))
check("kaputtes Original zählt nicht", not fidelity.breaks_syntax(broken, broken))
try:
    fidelity.require_faithful(good, broken)
    check("require_faithful wirft NotFaithful", False)
except fidelity.NotFaithful:
    check("require_faithful wirft NotFaithful", True)

# ── 4. Unabhängige Roundtrip-Prüfungen ───────────────────────────────────────
print("4. Roundtrip-Prüfungen mit tree-sitter")
names = {p[-1] for p, empty in roundtrip._ts_bindings(
    '{ a.b = 1; "q" = { c = 2; }; e = [ ]; s = "x # y = 1"; }') if not empty}
check("Bindungen gefunden, Leeres und Text im String nicht", names == {"b", "q", "c", "s"}, names)
check("doppeltes Blatt erkannt",
      roundtrip._ts_duplicates("{ a.b = 1; a = { b = 2; }; }") == {"a.b"})
check("zusammengeführte Blöcke sind kein Doppel",
      roundtrip._ts_duplicates("{ a.b = 1; a = { c = 2; }; }") == set())
check("Blatt, wo ein Block ist",
      roundtrip._ts_duplicates("{ a = 1; a.b = 2; }") == {"a"})
check("getrennte Mengen in einer Liste stören sich nicht",
      roundtrip._ts_duplicates("{ m = [ { x = 1; } { x = 2; } ]; }") == set())

print()
if fails:
    print(f"{len(fails)} Prüfung(en) fehlgeschlagen.")
    sys.exit(1)
print("Alle Prüfungen bestanden.")
