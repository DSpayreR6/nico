#!/usr/bin/env python3
"""Speichern der übrigen Config lässt eine außen geänderte flake.nix stehen.

Revision 2026-11-18, Befund 3 (Codex, bestätigt von Claude Opus 5.5): Jedes
Schreiben aus dem Formular von configuration.nix erzeugte eine verwaltete
flake.nix neu – ohne Blick auf die Prüfsumme, ohne Rückfrage. Was von außen
dazugekommen war, war danach weg. Jetzt wird sie nur mitgeschrieben, wenn
NiCo den aktuellen Stand selbst geschrieben hat und die neue Fassung
dasselbe sagt (fidelity.require_faithful); sonst bleibt sie, und das Öffnen
fragt (Fall A).

Lauf: nix-shell shell.nix --run "python testing/routes/test_flake_carry.py"
"""
import json, os, sys, tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

xdg = Path(tempfile.mkdtemp(prefix="nico-xdg-"))
os.environ["XDG_CONFIG_HOME"] = str(xdg)
cfg = Path(tempfile.mkdtemp(prefix="nico-cfg-"))
(cfg / "hosts" / "h1").mkdir(parents=True)
(cfg / "hosts" / "h1" / "hardware-configuration.nix").write_text("{ ... }: { }\n", encoding="utf-8")
(cfg / "config.json").write_text(json.dumps({"hosts_dir": "hosts"}), encoding="utf-8")
(xdg / "nico").mkdir(parents=True)
(xdg / "nico" / "settings.json").write_text(
    json.dumps({"nixos_config_dir": str(cfg), "language": "de", "git_sync": False}),
    encoding="utf-8")

from nico import config_manager, core, generator  # noqa: E402

fails = []
def check(label, cond, got=None):
    print(("  OK   " if cond else "  FAIL ") + label + ("" if cond or got is None else f"  (war: {got})"))
    if not cond: fails.append(label)

FLAKE = cfg / "flake.nix"
d = config_manager.get_defaults()
d.update(hostname="h1", _flake_mode=True, flake_hosts=[{"name": "h1"}])
(cfg / "configuration.nix").write_text(generator.generate_configuration_nix(d), encoding="utf-8")
FLAKE.write_text(generator.generate_flake_nix(d, nixos_dir=str(cfg)), encoding="utf-8")

def save_rest():
    data = config_manager.load_config(str(cfg))
    data["timezone"] = "Europe/Berlin"
    return core.write_config_files(str(cfg), data, commit=False)

print("1. Unveränderte NiCo-Flake wird mitgeschrieben")
before = FLAKE.read_text(encoding="utf-8")
res = save_rest()
check("Speichern ok", res.get("success"), res)
check("flake.nix mitgeschrieben", "flake.nix" in res.get("written", []), res.get("written"))
check("… und inhaltlich gleich", FLAKE.read_text(encoding="utf-8") == before)

print("\n2. Kommentar von außen (Fall von Codex)")
text = FLAKE.read_text(encoding="utf-8")
i = text.index("  inputs = {")
outside = text[:i] + "  # von außen: Kanal bewusst so gewählt\n" + text[i:]
FLAKE.write_text(outside, encoding="utf-8")
res = save_rest()
check("Speichern ok", res.get("success"), res)
check("flake.nix nicht mitgeschrieben", "flake.nix" not in res.get("written", []), res.get("written"))
check("Flake Byte für Byte gleich", FLAKE.read_text(encoding="utf-8") == outside)
check("übrige Config geschrieben", "Europe/Berlin" in (cfg / "configuration.nix").read_text(encoding="utf-8"))

print("\n3. Einstellung von außen")
text = generator.generate_flake_nix(d, nixos_dir=str(cfg))
i = text.index("  inputs = {")
outside = text[:i] + '  nixConfig.extra-substituters = [ "https://example.org" ];\n' + text[i:]
FLAKE.write_text(outside, encoding="utf-8")
res = save_rest()
check("flake.nix nicht mitgeschrieben", "flake.nix" not in res.get("written", []), res.get("written"))
check("Einstellung bleibt", FLAKE.read_text(encoding="utf-8") == outside)

print("\n4. Einstellung von außen mit gültiger Prüfsumme (zweite NiCo)")
FLAKE.write_text(core.set_type_in_content(outside, "fl"), encoding="utf-8")
stamped = FLAKE.read_text(encoding="utf-8")
res = save_rest()
check("Prüfsumme gültig, aber NiCo würde etwas anderes schreiben: bleibt",
      FLAKE.read_text(encoding="utf-8") == stamped and "flake.nix" not in res.get("written", []),
      res.get("written"))

print()
if fails:
    print(f"{len(fails)} FEHLER:", *fails, sep="\n  ")
    sys.exit(1)
print("Alle Prüfungen bestanden.")
