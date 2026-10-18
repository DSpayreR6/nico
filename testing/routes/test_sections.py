"""Test: Abschnitts-Kürzel, alte deutsche Namen und der Formatmarker.

Ohne Server und ohne Browser. Deckt die Stellen ab, an denen eine Datei von
einer anderen NiCo-Version kommt:

  * deutsche Marker aus 0.12.0 und älter werden gelesen und beim Schreiben
    auf Kürzel umgestellt – ein Brick darf dabei nicht in den falschen
    Abschnitt wandern
  * `Host: name` wird zu `HOST:name`
  * die Kopfzeile `CODE · Name` wird wieder auf das Kürzel zurückgeführt
  * der Formatmarker `#f<N>` überlebt Typ- und Panel-Modus-Änderungen und
    verfälscht den Inhalts-Hash nicht
"""
import pathlib, sys

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from nico import brix, core, generator  # noqa: E402

fails = []
def check(label, cond, got=None):
    print(("  OK   " if cond else "  FAIL ") + label + ("" if cond or got is None else f"  ({got})"))
    if not cond:
        fails.append(label)

print("1. Kürzel und Altnamen")
for name, want in (
    ("FSYS", "FSYS"),
    ("Dateisystem & Backup", "FSYS"),
    ("Nix & System", "NIXS"),
    ("Host: martin", "HOST:martin"),
    ("HOST:martin", "HOST:martin"),
    ("Inputs-Extra", "INPX"),
    ("Hosts", "OUTH"),
    ("Eigenbau", "Eigenbau"),          # unbekannt bleibt unbekannt
):
    got = brix.normalize_section(name)
    check(f'normalize_section("{name}") → {want}', got == want, got)

check("jedes Kürzel hat genau vier Zeichen",
      all(len(c) == 4 for c in brix.SECTIONS), [c for c in brix.SECTIONS if len(c) != 4])
check("jeder Altname zeigt auf ein bekanntes Kürzel",
      all(v in brix.SECTIONS for v in brix.LEGACY_SECTIONS.values()),
      [v for v in brix.LEGACY_SECTIONS.values() if v not in brix.SECTIONS])
check("Reihenfolge enthält nur bekannte Kürzel",
      all(c in brix.SECTIONS for c in brix.SECTION_ORDER))

print("\n2. Kopfzeile")
header = generator._section("FSYS")
check("Kopfzeile trägt Kürzel und englischen Namen",
      "FSYS · Maintenance & snapshots" in header, header.strip())
check("Kopfzeile wird auf das Kürzel zurückgeführt",
      brix.section_of_header("FSYS · Maintenance & snapshots") == "FSYS")
check("alte deutsche Kopfzeile wird erkannt",
      brix.section_of_header("Dateisystem & Backup") == "FSYS")
check("Host-Kopfzeile wird erkannt",
      brix.section_of_header("HOST:martin · Host martin") == "HOST:martin")

print("\n3. Brick mit altem deutschen Marker")
old_file = """{ config, pkgs, ... }:
{
  # ── Dateisystem & Backup ──────────────────────────────────────────
# <brick: Dateisystem & Backup / #2 mein-brick>
  services.irgendwas.enable = true;
# </brick: mein-brick>
}
"""
blocks = brix.extract_brick_blocks(old_file)
check("Brick gefunden", "mein-brick" in blocks, list(blocks))
if "mein-brick" in blocks:
    b = blocks["mein-brick"]
    check("Abschnitt ist jetzt FSYS", b["section"] == "FSYS", b["section"])
    check("Reihenfolge bleibt erhalten", b["order"] == 2, b["order"])
    check("Marker ist umgeschrieben", "<brick: FSYS / #2 mein-brick>" in b["text"],
          b["text"].splitlines()[0])
    check("Inhalt unverändert", "services.irgendwas.enable = true;" in b["text"])
    check("sortiert wie ein bekannter Abschnitt",
          brix._section_rank("FSYS") == brix.SECTION_ORDER.index("FSYS"))
    check("ein unbekannter Abschnitt landet dagegen vor TAIL",
          brix._section_rank("Eigenbau") == len(brix.SECTION_ORDER) - 2)

print("\n4. Formatmarker")
content = generator._add_version_hash("# head\nbody\n", "co")
line = content.splitlines()[1]
check(f"Marker steht in der Kopfzeile ({line})", f"#f{brix.FILE_FORMAT}" in line)
check("Format wird gelesen", core.get_file_format(content) == brix.FILE_FORMAT)
check("Hash bleibt gültig", generator.check_version_hash(content) == "ok")

with_mode = core.set_panel_mode(content, "p")
check("Panel-Modus erhält den Marker", core.get_file_format(with_mode) == brix.FILE_FORMAT,
      with_mode.splitlines()[1])
check("Panel-Modus bleibt lesbar", core.get_panel_mode(with_mode) == "p")
check("Hash bleibt nach Modus-Wechsel gültig",
      generator.check_version_hash(with_mode) == "ok")

retyped = core.set_type_in_content(with_mode, "nd")
check("Typwechsel erhält den Marker", core.get_file_format(retyped) == brix.FILE_FORMAT,
      retyped.splitlines()[1])
check("Hash bleibt nach Typwechsel gültig", generator.check_version_hash(retyped) == "ok")

print("\n5. Format wird aus dem Inhalt erkannt, nicht aus der Kopfzeile")
legacy_body = """# nico-version: co#aabbccdd#p
{
  # ── Dateisystem & Backup ────────────────────
# <brick: Dateisystem & Backup / #2 x>
  a = 1;
# </brick: x>
}
"""
code_body = (legacy_body
             .replace("Dateisystem & Backup / #2 x", "FSYS / #2 x")
             .replace("── Dateisystem & Backup ─", "── FSYS · Maintenance & snapshots ─"))
check("deutsche Marker → Format 1", brix.detect_content_format(legacy_body) == 1)
check("Kürzel-Marker → Format 2", brix.detect_content_format(code_body) == 2)
check("ohne Marker → aktuelles Format",
      brix.detect_content_format("{ }\n") == brix.FILE_FORMAT)
check("Stempel folgt dem Inhalt, nicht der alten Kopfzeile",
      core.get_file_format(core.set_type_in_content(code_body, "co")) == 2,
      core.set_type_in_content(code_body, "co").splitlines()[0])
check("nicht umgeschriebener Inhalt bleibt Format 1",
      core.get_file_format(core.set_type_in_content(legacy_body, "co")) == 1)
future = code_body.replace("#p", "#p#f7")
check("ein höheres Format wird nicht heruntergestempelt",
      core.get_file_format(core.set_type_in_content(future, "co")) == 7,
      core.set_type_in_content(future, "co").splitlines()[0])

print("\n6. Unterstützte Formate")
old_fmt = "# x\n# nico-version: co#aabbccdd#p\nbody\n"
check("Datei ohne Marker gilt als Format 1", core.get_file_format(old_fmt) == 1)
check("Format 1 ist unterstützt", 1 in brix.SUPPORTED_FORMATS)
check("ein höheres Format ist nicht unterstützt",
      (brix.FILE_FORMAT + 1) not in brix.SUPPORTED_FORMATS)

print("\n" + ("ALLE PRÜFUNGEN BESTANDEN" if not fails else f"FEHLGESCHLAGEN: {len(fails)}"))
sys.exit(1 if fails else 0)
