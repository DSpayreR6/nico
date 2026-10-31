#!/usr/bin/env python3
"""Unlesbare Einstellungsdateien, und der Push-Schalter, der ignoriert wurde.

Aus der Revision vom 2026-10-31, zwei Befunde in einer Datei, weil beide an den
Einstellungen hängen:

  1. `load_config_settings` fing jeden Parse-Fehler ab und gab die Defaults
     zurück. Der nächste Schreibvorgang schrieb damit die Defaults fest.
     Gemessen an einer config.json mit `hosts_dir: maschinen` und
     `hm_dir: benutzer`: nach einer einzigen, unbeteiligten Änderung standen
     beide auf den Defaults, und NiCo suchte die Hosts in einem Verzeichnis,
     in dem nichts liegt. Die Datei ist getrackt und wandert mit der Config
     mit – ein `git pull` mit Konfliktmarkern genügt als Auslöser.
     Entscheidung Martin (2026-10-31): kaputte Datei beiseitelegen und mit
     Defaults weiterlaufen. Der Inhalt bleibt damit lesbar, und im
     Config-Verzeichnis fragt der Fremddatei-Dialog von selbst nach ihr.
  2. Der Formular-Schreibweg lud hoch, obwohl "Git-Sync aktiviert" aus war –
     `write_config_files` prüfte nur `push_after_save`. Eine stille
     Remote-Aktion gegen eine ausdrückliche Einstellung, was `vorgaben.txt`
     ausschließt. Die beiden Schalter liegen auf verschiedenen Ebenen:
     `git_sync` gehört zu diesem Rechner, `push_after_save` wandert mit der
     Config – eine mitgebrachte Config trifft also auf einen Rechner, der in
     Ruhe gelassen werden will.

Lauf: nix-shell shell.nix --run "python testing/routes/test_settings_recovery.py"
"""
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

xdg = Path(tempfile.mkdtemp(prefix="nico-xdg-"))
os.environ["XDG_CONFIG_HOME"] = str(xdg)

from nico import config_manager as cm, core, git_manager  # noqa: E402

fails = []


def check(label, cond, got=None):
    print(("  OK   " if cond else "  FAIL ") + label
          + ("" if cond or got is None else f"  (war: {got})"))
    if not cond:
        fails.append(label)


STORED = {
    "hosts_dir": "maschinen",
    "hm_dir": "benutzer",
    "validation_rules": {"swap_in_snapshot": True},
    "host_checks": {"nix-desktop": "abc"},
}

# ── 1. Defekte config.json ───────────────────────────────────────────────────
print("\n1. config.json mit Konfliktmarkern")
d = tempfile.mkdtemp(prefix="nico-cfg-")
p = Path(d) / "config.json"
p.write_text(json.dumps(STORED, indent=2), encoding="utf-8")
check("Ausgangsstand wird gelesen",
      cm.load_config_settings(d)["hosts_dir"] == "maschinen",
      cm.load_config_settings(d)["hosts_dir"])

p.write_text("<<<<<<< HEAD\n" + json.dumps(STORED) + "\n=======\n{}\n>>>>>>> other\n",
             encoding="utf-8")
loaded = cm.load_config_settings(d)
check("läuft mit Defaults weiter", loaded["hosts_dir"] == "hosts", loaded["hosts_dir"])

parked = sorted(Path(d).glob("config.json.kaputt-*"))
check("kaputte Datei liegt beiseite", len(parked) == 1, [x.name for x in parked])
if parked:
    text = parked[0].read_text(encoding="utf-8")
    check("ihr Inhalt ist vollständig erhalten",
          "maschinen" in text and "benutzer" in text and "nix-desktop" in text)
check("config.json selbst ist weg", not p.exists())

# Der eigentliche Schaden: eine unbeteiligte Änderung schrieb die Defaults fest
cm.save_config_settings(d, {"flake_update_on_rebuild": True})
after = json.loads(p.read_text(encoding="utf-8"))
check("neue config.json enthält die Änderung", after.get("flake_update_on_rebuild") is True)
if parked:
    check("der alte Stand ist weiterhin nachlesbar",
          "maschinen" in parked[0].read_text(encoding="utf-8"))

# ── 2. Nicht jeder Fehler ist Beschädigung ───────────────────────────────────
print("\n2. Unlesbar ist nicht beschädigt")
d2 = tempfile.mkdtemp(prefix="nico-cfg-")
p2 = Path(d2) / "config.json"
p2.write_text(json.dumps({"hosts_dir": "maschinen"}), encoding="utf-8")
os.chmod(p2, 0o000)
try:
    loaded = cm.load_config_settings(d2)
    unreadable = os.geteuid() != 0
finally:
    os.chmod(p2, 0o644)
if unreadable:
    check("nicht lesbar → Datei bleibt liegen", p2.exists())
    check("nicht beiseitegelegt", not list(Path(d2).glob("config.json.kaputt-*")),
          [x.name for x in Path(d2).glob("config.json.kaputt-*")])
    check("danach wieder normal lesbar",
          cm.load_config_settings(d2)["hosts_dir"] == "maschinen")
else:
    print("  --   als root ausgeführt, Rechteprüfung übersprungen")

# ── 3. Eine Liste statt eines Objekts gilt als beschädigt ────────────────────
print("\n3. config.json mit falscher Struktur")
d3 = tempfile.mkdtemp(prefix="nico-cfg-")
p3 = Path(d3) / "config.json"
p3.write_text("[1, 2, 3]", encoding="utf-8")
loaded = cm.load_config_settings(d3)
check("Defaults statt Absturz", loaded["hosts_dir"] == "hosts", loaded["hosts_dir"])
check("beiseitegelegt", bool(list(Path(d3).glob("config.json.kaputt-*"))))

# ── 4. Push folgt beiden Schaltern ───────────────────────────────────────────
print("\n4. Push nach dem Speichern")
pushes = []
git_manager.git_push = lambda nixos_dir: (pushes.append(nixos_dir), (True, "", ""))[1]

d4 = tempfile.mkdtemp(prefix="nico-cfg-")
cm.save_config_settings(d4, {"push_after_save": True})

cm.save_app_settings({"git_sync": False})
pushes.clear()
out = core.push_after_save(d4)
check("Git-Sync aus → kein Push", pushes == [], pushes)
check("und keine Push-Felder in der Antwort", out == {}, out)

cm.save_app_settings({"git_sync": True})
pushes.clear()
out = core.push_after_save(d4)
check("Git-Sync an + push_after_save → Push", len(pushes) == 1, pushes)
check("Antwort meldet den Push", out.get("pushed") is True, out)

cm.save_config_settings(d4, {"push_after_save": False})
pushes.clear()
out = core.push_after_save(d4)
check("push_after_save aus → kein Push", pushes == [], pushes)

print()
if fails:
    print("FEHLGESCHLAGEN:", len(fails))
    for f in fails:
        print("  -", f)
    sys.exit(1)
print("Alle Prüfungen bestanden.")
