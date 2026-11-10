#!/usr/bin/env python3
"""Revision 2026-11-10, Paket 1: Datenverlust in NiCo-eigenen Dateien.

Fünf Befunde aus dem Manifest („Befunde Revision 2026-11-10"), jeder hier mit
der Eingabe, an der er gemessen wurde:

  B32  Ein Brick-Startmarker ohne passenden Endmarker – ein Tippfehler im
       Namen genügt – ließ `strip_brick_blocks` alles bis zum Dateiende
       verwerfen. Der nächste Ladevorgang schrieb das fest: übrig blieb das
       Gerüst, ohne Hostname und ohne die Bricks dahinter.
  B38  Bricks einer Zonen-Section (HOST:<name>, OUTX, INPX) wurden nur
       eingesetzt, wenn ihre Kopfzeile in der Datei stand. Ohne sie
       verschwanden sie aus der Ausgabe, ohne Meldung.
  B39  Laden schrieb mehr als nötig: neben einer einzelnen flake.nix entstand
       eine configuration.nix aus Defaults, und eine von außen geänderte Datei
       wurde zuerst ohne ihre Rest-Bricks geschrieben und erst danach mit.
  B40  Die Zeitmaschine setzte zurück, ohne den Stand davor zu sichern. Was
       nicht committet war, stand danach in keinem Sicherungspunkt.
  S10  `clean_large_files` löschte jeden Pfad, den der Request nannte – auch
       außerhalb des Config-Verzeichnisses und auch kleine Dateien.

Lauf: nix-shell shell.nix --run "python testing/routes/test_rev1110_data_loss.py"
"""
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

xdg = Path(tempfile.mkdtemp(prefix="nico-xdg-"))
os.environ["XDG_CONFIG_HOME"] = str(xdg)
os.environ["XDG_CACHE_HOME"] = str(xdg / "cache")

from nico import brix, config_manager, core, generator, git_manager, importer  # noqa: E402
from nico.brix import format_brick  # noqa: E402

fails = []


def check(label, cond, got=None):
    print(("  OK   " if cond else "  FAIL ") + label
          + ("" if cond or got is None else f"  (war: {got})"))
    if not cond:
        fails.append(label)


def tmp(prefix="nico-cfg-") -> Path:
    return Path(tempfile.mkdtemp(prefix=prefix))


def git(args, cwd) -> str:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True).stdout


# ── B32 ──────────────────────────────────────────────────────────────────────
print("B32 – Startmarker ohne Endmarker")

data = config_manager.get_defaults()
data["hostname"] = "box"
data["brick_blocks"] = {
    "one": {"section": "HEAD", "order": 1,
            "text": format_brick("HEAD", 1, "one", "  services.foo.enable = true;")},
    "two": {"section": "TAIL", "order": 1,
            "text": format_brick("TAIL", 1, "two", "  services.bar.enable = true;")},
}
intact = generator.generate_configuration_nix(data)
broken = intact.replace("# </brick: one>", "# </brick: on>")
check("Testdatei trägt den Tippfehler", broken != intact)

stripped = brix.strip_brick_blocks(broken)
check("strip: Inhalt des offenen Bricks bleibt stehen", "services.foo.enable" in stripped)
check("strip: der Text dahinter bleibt stehen", 'networking.hostName = "box"' in stripped)
check("strip: die Datei endet weiter mit ihrer Klammer", stripped.rstrip().endswith("}"))
check("strip: der geschlossene Brick dahinter wird entfernt", "services.bar" not in stripped)
check("strip: kein Marker bleibt zurück", "<brick:" not in stripped and "</brick:" not in stripped)

blocks = brix.extract_brick_blocks(broken)
check("extract: der Brick hinter dem offenen wird gefunden", "two" in blocks, sorted(blocks))
check("extract: der offene Brick gilt nicht als Brick", "one" not in blocks, sorted(blocks))
check("nested: der folgende Brick gilt nicht als verschachtelt",
      brix.find_nested_bricks(broken) == [], brix.find_nested_bricks(broken))

check("unverändert: intakte Datei verliert beim Strip nur die Bricks",
      "services.foo" not in brix.strip_brick_blocks(intact)
      and 'networking.hostName = "box"' in brix.strip_brick_blocks(intact))
check("unverändert: intakte Datei liefert beide Bricks",
      sorted(brix.extract_brick_blocks(intact)) == ["one", "two"])

d = tmp()
(d / "configuration.nix").write_text(broken, encoding="utf-8")
core.load_and_normalize_config(str(d))
after = (d / "configuration.nix").read_text(encoding="utf-8")
check("Laden: Inhalt des offenen Bricks ist noch in der Datei", "services.foo.enable" in after)
check("Laden: der Brick dahinter ist noch in der Datei", "services.bar.enable" in after)
check("Laden: der Hostname ist noch in der Datei", 'networking.hostName = "box"' in after)
shutil.rmtree(d)

# Two open bricks in a row, then a closed one: every rewind must find the next.
multi = ("{ ... }: {\n# <brick: HEAD / #1 a>\n  x.a = 1;\n# <brick: HEAD / #2 b>\n  x.b = 2;\n"
         "# <brick: HEAD / #3 c>\n  x.c = 3;\n# </brick: c>\n}\n")
check("mehrere offene: der geschlossene Brick wird gefunden",
      sorted(brix.extract_brick_blocks(multi)) == ["c"], sorted(brix.extract_brick_blocks(multi)))
m_stripped = brix.strip_brick_blocks(multi)
check("mehrere offene: beide Inhalte bleiben stehen",
      "x.a = 1;" in m_stripped and "x.b = 2;" in m_stripped and "x.c" not in m_stripped, m_stripped)

# ── B38 ──────────────────────────────────────────────────────────────────────
print("B38 – Zonen-Brick ohne Kopfzeile")

base = brix.strip_brick_blocks(generator.generate_configuration_nix({"hostname": "a"}))
for sec in ("HOST:nope", "OUTX", "INPX"):
    out = brix.inject_brick_blocks(base, {"z": {
        "section": sec, "order": 1,
        "text": format_brick(sec, 1, "z", "  services.keepme.enable = true;")}})
    check(f"{sec}: der Inhalt steht in der Ausgabe", "services.keepme.enable" in out)
    check(f"{sec}: er steht vor der schließenden Klammer",
          "keepme" in out and out.rstrip().endswith("}")
          and out.index("keepme") < out.rstrip().rindex("}"))
    check(f"{sec}: die Section bleibt im Marker erhalten",
          sec in brix.extract_brick_blocks(out).get("z", {}).get("section", ""),
          brix.extract_brick_blocks(out).get("z"))

flake = generator.generate_flake_nix({"hostname": "kiste", "flake_hosts": [{"name": "kiste"}]})
fl_blocks = brix.extract_brick_blocks(flake)
again = brix.inject_brick_blocks(brix.strip_brick_blocks(flake), fl_blocks)
check("unverändert: Zonen-Brick mit Kopfzeile bleibt an seinem Platz", again == flake)

# ── B39 ──────────────────────────────────────────────────────────────────────
print("B39 – Laden schreibt nicht mehr als nötig")

d = tmp()
src = ROOT / "testing" / "configs" / "config-ms" / "original" / "flake.nix"
shutil.copy(src, d / "flake.nix")
flake_before = (d / "flake.nix").read_bytes()
loaded = core.load_and_normalize_config(str(d))
check("nur flake.nix: es entsteht keine configuration.nix", not (d / "configuration.nix").exists())
check("nur flake.nix: die Hosts werden trotzdem gelesen",
      [h["name"] for h in loaded.get("flake_hosts", [])] == ["nix-vm", "nix-desktop", "nix-laptop"],
      loaded.get("flake_hosts"))
shutil.rmtree(d)

d = tmp()
hand = ('{ config, pkgs, ... }:\n{\n  networking.hostName = "kiste";\n'
        '  services.alpha.enable = true;\n}\n')
(d / "configuration.nix").write_text(hand, encoding="utf-8")
real = importer.build_rest_brix


def boom(*a, **k):
    raise RuntimeError("parser died")


importer.build_rest_brix = boom
try:
    core.load_and_normalize_config(str(d))
    raised = False
except RuntimeError:
    raised = True
finally:
    importer.build_rest_brix = real
check("Abbruch beim Zerlegen kommt als Fehler an", raised)
check("Abbruch beim Zerlegen: die Datei auf Platte ist unverändert",
      (d / "configuration.nix").read_text(encoding="utf-8") == hand)
core.load_and_normalize_config(str(d))
normal = (d / "configuration.nix").read_text(encoding="utf-8")
check("ohne Abbruch wird wie bisher normalisiert",
      "services.alpha.enable" in normal and "# nico-version:" in normal)
shutil.rmtree(d)

# ── B40 ──────────────────────────────────────────────────────────────────────
print("B40 – Zeitmaschine sichert vor dem Zurücksetzen")

d = tmp("nico-git-")
f = d / "configuration.nix"
f.write_text("{ ... }: { a = 1; }\n", encoding="utf-8")
git_manager.init_repo(str(d))
f.write_text("{ ... }: { a = 2; }\n", encoding="utf-8")
git_manager.auto_commit(str(d))
first = git(["rev-list", "--max-parents=0", "HEAD"], d).strip()
f.write_text("{ ... }: { a = 3; }\n", encoding="utf-8")       # never committed
ok, msg = git_manager.rollback(str(d), first)
check("Rollback gelingt", ok, msg)
check("Rollback stellt den alten Stand her", "a = 1" in f.read_text(encoding="utf-8"))
history = git(["log", "-p", "--all"], d)
check("der ungesicherte Stand steht in einem Sicherungspunkt", "a = 3" in history)
check("der Sicherungspunkt ist als solcher benannt", "backup before rollback" in history)

n_before = len(git(["rev-list", "HEAD"], d).split())
ok, msg = git_manager.rollback(str(d), first)
n_after = len(git(["rev-list", "HEAD"], d).split())
check("sauberer Baum: kein zusätzlicher Sicherungspunkt", ok and n_after == n_before,
      (n_before, n_after, msg))
shutil.rmtree(d)

# ── S10 ──────────────────────────────────────────────────────────────────────
print("S10 – Aufräumen großer Dateien nimmt nur gemeldete Pfade")

d = tmp("nico-git-")
outside = tmp("nico-victim-")
victim = outside / "important.txt"
victim.write_text("keep me", encoding="utf-8")
(d / "configuration.nix").write_text("{ ... }: { }\n", encoding="utf-8")
git_manager.init_repo(str(d))
with open(d / "big.bin", "wb") as fh:
    fh.truncate(git_manager.MAX_STAGE_BYTES + 1024)
rel = os.path.relpath(victim, d)

ok, msg, result = git_manager.clean_large_files(str(d), [rel, "configuration.nix"])
check("nur fremde Pfade: abgelehnt", not ok, (ok, msg, result))
check("Datei außerhalb des Config-Verzeichnisses existiert noch", victim.exists())
check("kleine Config-Datei existiert noch", (d / "configuration.nix").exists())
ignore = (d / ".gitignore").read_text(encoding="utf-8")
check("nichts davon steht in .gitignore", "important.txt" not in ignore
      and "configuration.nix" not in ignore, ignore)

ok, msg, result = git_manager.clean_large_files(str(d), ["big.bin", rel, str(victim)])
check("gemischt: gelingt für die gemeldete Datei", ok, (ok, msg))
check("gemischt: die große Datei ist gelöscht", not (d / "big.bin").exists())
check("gemischt: gelöscht wurde nur sie", result.get("deleted") == ["big.bin"], result.get("deleted"))
check("gemischt: die Datei außerhalb existiert weiter", victim.exists())
shutil.rmtree(d)
shutil.rmtree(outside)

print()
if fails:
    print(f"{len(fails)} FEHLER:", *fails, sep="\n  ")
    sys.exit(1)
print("alle Prüfungen bestanden")
