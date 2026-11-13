#!/usr/bin/env python3
"""Abgesicherter Rebuild: Grenzen für Jobs und Kerne aus dem freien Speicher.

Anlass (Martin, 2026-11-13): Der abgesicherte Rebuild lief fest mit
`--max-jobs 1 --cores 4`. Auf einem Rechner mit 30 GB und 16 Kernen ist das
unnötig langsam, in einer VM mit 4 GB sind vier Compiler-Threads schon zu
viel. NiCo schlägt jetzt Werte vor, der Nutzer kann sie überschreiben.

Geprüft wird:
  1. /proc/meminfo wird gelesen (kB → Byte), Unlesbares ergibt None.
  2. Die Formel: (verfügbar − Reserve) / 2 GiB, Reserve = 10 % vom
     Gesamtspeicher, mindestens 2 GiB; gedeckelt auf die Kernzahl,
     mindestens 1; Jobs immer 1. Ohne Messung die alte Vorgabe (4).
  3. Eingaben: nur ganze Zahlen in 1..Kernzahl gelten, alles andere heißt
     „automatisch".
  4. Reihenfolge beim Auflösen: Anfrage, dann App-Settings, dann Vorschlag.
  5. Die Routen: der abgesicherte Rebuild ist an, solange er nie
     ausgeschaltet wurde; Vorschlag abrufbar, Settings speichern nur gültige Werte,
     das Terminal-Skript trägt die aufgelösten Grenzen.

Lauf: nix-shell shell.nix --run "python testing/routes/test_buildlimits.py"
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
cfg = Path(tempfile.mkdtemp(prefix="nico-cfg-"))
(xdg / "nico").mkdir(parents=True)
(xdg / "nico" / "settings.json").write_text(
    json.dumps({"nixos_config_dir": str(cfg), "language": "de", "git_sync": False}),
    encoding="utf-8")

from nico import buildlimits as bl, config_manager  # noqa: E402

GIB = bl.GIB
fails = []
def check(label, cond, got=None):
    print(("  OK   " if cond else "  FAIL ") + label + ("" if cond or got is None else f"  (war: {got})"))
    if not cond:
        fails.append(label)

print("1. /proc/meminfo lesen")
tmp = Path(tempfile.mkdtemp(prefix="nico-mem-"))
f = tmp / "meminfo"
f.write_text("MemTotal:       32000000 kB\nMemFree:         1000000 kB\n"
             "MemAvailable:   26000000 kB\nSwapTotal:      24000000 kB\n")
mi = bl.read_meminfo(f)
check("Gesamt in Byte", mi and mi["total"] == 32000000 * 1024, mi)
check("Verfügbar, nicht frei", mi and mi["available"] == 26000000 * 1024, mi)
check("Swap zählt nicht", mi is not None and set(mi) == {"total", "available"}, mi)
check("fehlende Datei → None", bl.read_meminfo(tmp / "gibtsnicht") is None)
f.write_text("MemTotal: 32000000 kB\n")
check("ohne MemAvailable → None", bl.read_meminfo(f) is None)
f.write_text("MemTotal: viel kB\nMemAvailable: 5 kB\n")
check("kaputte Zahl → None", bl.read_meminfo(f) is None)

print("\n2. Formel")
def s(total, avail, cpus):
    return bl.suggest({"total": int(total * GIB), "available": int(avail * GIB)}, cpus)
r = s(30, 26, 16)
check("30 GiB, 26 frei, 16 Kerne → 11", r["cores"] == 11, r)
check("Reserve ist 10 % (3 GiB)", r["reserve"] == 3 * GIB, r)
check("Jobs bleibt 1", r["jobs"] == 1, r)
check("als gemessen markiert", r["measured"] is True)
r = s(4, 3.2, 4)
check("4 GiB, 3,2 frei → 1 (Reserve mindestens 2 GiB)", r["cores"] == 1 and r["reserve"] == 2 * GIB, r)
check("8 GiB, 6,5 frei, 8 Kerne → 2", s(8, 6.5, 8)["cores"] == 2, s(8, 6.5, 8))
check("128 GiB frei, 8 Kerne → gedeckelt auf 8", s(128, 120, 8)["cores"] == 8)
check("nichts frei → trotzdem 1", s(16, 0.5, 8)["cores"] == 1)
r = bl.suggest(None, 16, measure=False)
check("ohne Messung: alte Vorgabe 1 × 4", (r["jobs"], r["cores"], r["measured"]) == (1, 4, False), r)
check("ohne Messung, 2 Kerne → 2", bl.suggest(None, 2, measure=False)["cores"] == 2)
live = bl.suggest()
check("auf diesem Rechner: 1 ≤ Kerne ≤ Kernzahl",
      1 <= live["cores"] <= live["cpus"] and live["jobs"] == 1, live)

print("\n3. Eingaben")
for val, want in ((3, 3), ("3", 3), (" 7 ", 7), (3.0, 3), (1, 1), (8, 8),
                  (0, None), (9, None), (-1, None), (2.5, None), ("", None),
                  ("abc", None), (None, None), (True, None), ([2], None), ("1e1", None)):
    got = bl.clean(val, 8)
    check(f"{val!r} → {want!r}", got == want, got)

print("\n4. Auflösen")
real = bl.cpu_count()
hint = bl.suggest()
check("nichts gegeben → Vorschlag", bl.resolve() == (hint["jobs"], hint["cores"]), bl.resolve())
check("Anfrage gewinnt", bl.resolve(1, 1, {"rebuild_safe_jobs": 1, "rebuild_safe_cores": real}) == (1, 1))
check("sonst App-Settings", bl.resolve(None, None, {"rebuild_safe_cores": 1})[1] == 1)
check("Unsinn in der Anfrage → Settings",
      bl.resolve("x", 99999, {"rebuild_safe_jobs": 1, "rebuild_safe_cores": 1}) == (1, 1))
check("Unsinn überall → Vorschlag",
      bl.resolve(0, -3, {"rebuild_safe_jobs": "viele", "rebuild_safe_cores": None})
      == (hint["jobs"], hint["cores"]))

print("\n5. Routen")
from nico.server import create_app  # noqa: E402
app = create_app()
app.config["TESTING"] = True
c = app.test_client()
tok = None
import re
m = re.search(r'name="csrf-token" content="([^"]+)"', c.get("/").get_data(as_text=True))
tok = m.group(1) if m else ""
H = {"X-CSRF-Token": tok, "Content-Type": "application/json"}

# Standard seit 2026-11-13: an, solange niemand den Schalter umgelegt hat.
# Die settings.json dieses Tests enthält den Schlüssel nicht.
check("abgesicherter Rebuild ist ohne gespeicherten Wert an",
      config_manager.get_app_settings().get("rebuild_safe") is True)
check("die Oberfläche bekommt das auch so",
      (c.get("/api/app/settings").get_json() or {}).get("rebuild_safe") is True)
c.patch("/api/app/settings", headers=H, data=json.dumps({"rebuild_safe": False}))
check("ausgeschaltet bleibt ausgeschaltet",
      config_manager.get_app_settings().get("rebuild_safe") is False)

r = c.get("/api/rebuild/safe-suggest")
j = r.get_json() or {}
check("Vorschlag abrufbar", r.status_code == 200 and j.get("jobs") == 1
      and 1 <= j.get("cores", 0) <= j.get("cpus", 0), j)

r = c.patch("/api/app/settings", headers=H,
            data=json.dumps({"rebuild_safe_jobs": 1, "rebuild_safe_cores": "1"}))
st = config_manager.get_app_settings()
check("gültige Werte werden gespeichert", r.status_code == 200
      and st.get("rebuild_safe_jobs") == 1 and st.get("rebuild_safe_cores") == 1,
      (r.status_code, st))
c.patch("/api/app/settings", headers=H,
        data=json.dumps({"rebuild_safe_jobs": 0, "rebuild_safe_cores": "--option x"}))
st = config_manager.get_app_settings()
check("ungültige werden zu null (automatisch)",
      st.get("rebuild_safe_jobs") is None and st.get("rebuild_safe_cores") is None, st)
c.patch("/api/app/settings", headers=H,
        data=json.dumps({"rebuild_safe_jobs": None, "rebuild_safe_cores": None}))
st = config_manager.get_app_settings()
check("leer bleibt null", st.get("rebuild_safe_jobs") is None, st)

src = (ROOT / "nico" / "routes" / "rebuild.py").read_text(encoding="utf-8")
check("keine festen Grenzen mehr im Rebuild", "--cores 4" not in src and '"--cores", "4"' not in src)
check("beide Wege lösen über buildlimits auf", src.count("buildlimits.resolve(") == 2,
      src.count("buildlimits.resolve("))
js = (ROOT / "nico" / "static" / "js" / "rebuild.js").read_text(encoding="utf-8")
check("auch die Befehlsvorschau nicht", "--cores 4" not in js)

print()
if fails:
    print(f"{len(fails)} FEHLER:", *fails, sep="\n  ")
    sys.exit(1)
print("Alle Prüfungen bestanden.")
