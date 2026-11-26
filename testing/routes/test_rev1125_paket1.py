#!/usr/bin/env python3
"""Revision 2026-11-25, Paket 1: Überschreibschutz (F3, F2).

F3 – Textmodus: Ändert Kate, Syncthing oder ein zweiter Tab eine Datei,
während sie im Textmodus offen ist, schrieb das nächste Speichern in NiCo
den alten Text darüber. Jetzt bekommt der Editor beim Öffnen den Stand
(`_base`, outside.py) und schickt ihn beim Speichern mit; weicht die Platte
ab, antwortet der Server 409 ERR_CHANGED_OUTSIDE – dieselbe Frage wie im
Panel (Entscheidung Martin 2026-11-26: Abbrechen lässt Platte und Editor,
Überschreiben legt vorher einen Sicherungspunkt an).

F2 – Zeitmaschine: Der Sicherungspunkt vor dem Wiederherstellen lässt
Dateien über der Größengrenze aus. Wurden sie danach überschrieben, war ihr
Stand in keinem Commit. Jetzt bricht rollback() vor dem ersten
Schreibzugriff ab.

Lauf: nix-shell shell.nix --run "python testing/routes/test_rev1125_paket1.py"
"""
import json, os, re, subprocess, sys, tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

xdg = Path(tempfile.mkdtemp(prefix="nico-xdg-"))
os.environ["XDG_CONFIG_HOME"] = str(xdg)
cfg = Path(tempfile.mkdtemp(prefix="nico-cfg-"))
(cfg / "modules").mkdir(parents=True)
(cfg / "config.json").write_text(json.dumps({"hosts_dir": "hosts"}), encoding="utf-8")
(xdg / "nico").mkdir(parents=True)
(xdg / "nico" / "settings.json").write_text(
    json.dumps({"nixos_config_dir": str(cfg), "language": "de", "git_sync": False}),
    encoding="utf-8")

from nico import git_manager, server  # noqa: E402

fails = []
def check(label, cond, got=None):
    print(("  OK   " if cond else "  FAIL ") + label + ("" if cond or got is None else f"  (war: {got})"))
    if not cond: fails.append(label)

client = server.create_app().test_client()
page = client.get("/").get_data(as_text=True)
csrf = re.search(r'<meta name="csrf-token" content="([^"]+)"', page).group(1)
H = {"X-CSRF-Token": csrf, "Content-Type": "application/json"}

def post(url, body):
    r = client.post(url, headers=H, data=json.dumps(body))
    return r.status_code, (r.get_json(silent=True) or {})

def log():
    return subprocess.run(["git", "log", "--format=%s"], cwd=cfg,
                          capture_output=True, text=True).stdout

MOD = "modules/m.nix"
(cfg / MOD).write_text("{ ... }:\n{\n  # erste Fassung\n}\n", encoding="utf-8")
ok, _ = git_manager.init_repo(str(cfg))
check("Git eingerichtet", ok)
git_manager.auto_commit(str(cfg), label="start")

# ── F3 ──────────────────────────────────────────────────────────────────────
print("F3: Textmodus, Datei außerhalb geändert")
data = client.get(f"/api/file?path={MOD}", headers=H).get_json()
base = data.get("_base")
check("Öffnen liefert einen Stand (_base)", isinstance(base, str) and len(base) == 64, data)

mine = "{ ... }:\n{\n  # aus NiCo\n}\n"
s, r = post("/api/file", {"files": [{"path": MOD, "content": mine, "base": base}]})
check("Speichern ohne fremde Änderung gelingt", s == 200 and r.get("success"), r)
base = (r.get("bases") or {}).get(MOD)
check("… und liefert den neuen Stand", isinstance(base, str) and len(base) == 64, r)

s, r = post("/api/file", {"files": [{"path": MOD, "content": mine + "# zwei\n", "base": base}]})
check("Eigenes Speichern zählt nicht als fremde Änderung", s == 200 and r.get("success"), r)
base = (r.get("bases") or {}).get(MOD)

kate = "{ ... }:\n{\n  # aus Kate\n}\n"
(cfg / MOD).write_text(kate, encoding="utf-8")
s, r = post("/api/file", {"files": [{"path": MOD, "content": mine + "# drei\n", "base": base}]})
check("Fremd geändert: 409", s == 409, s)
check("… ERR_CHANGED_OUTSIDE mit Datei", r.get("error") == "ERR_CHANGED_OUTSIDE"
      and r.get("file") == MOD, r)
check("… Datei auf der Platte unverändert (Kate-Fassung)",
      (cfg / MOD).read_text(encoding="utf-8") == kate)

r = client.get(f"/api/file/outside-diff?path={MOD}&base={base}", headers=H).get_json()
hunks = (r.get("files") or [{}])[0].get("hunks", [])
check("Unterschiede zeigen die Kate-Zeile",
      any(h["type"] == "added" and "aus Kate" in h["content"] for h in hunks), hunks)

s, r = post("/api/file", {"files": [{"path": MOD, "content": mine + "# drei\n", "base": base}],
                          "overwrite": True})
check("Überschreiben gelingt", s == 200 and r.get("success"), r)
check("… schreibt den Text aus NiCo", "# drei" in (cfg / MOD).read_text(encoding="utf-8"))
check("… nach einem Sicherungspunkt vom Kate-Stand", "backup before overwriting" in log(), log())
show = subprocess.run(["git", "show", f"HEAD~1:{MOD}"], cwd=cfg,
                      capture_output=True, text=True).stdout
check("… der die Kate-Fassung enthält", "aus Kate" in show, show)

(cfg / MOD).write_text(kate, encoding="utf-8")
s, r = post("/api/file", {"files": [{"path": MOD, "content": mine}]})
check("Ohne Stand (älterer Browser): wie bisher, kein Vergleich", s == 200 and r.get("success"), r)

# ── F2 ──────────────────────────────────────────────────────────────────────
print("\nF2: Zeitmaschine mit zu großer Datei")
git_manager.MAX_STAGE_BYTES = 1000          # the 50 MiB limit, scaled down
big = cfg / "big.bin"
big.write_bytes(b"x" * 100)
# Not a config file: the foreign-file guard leaves it untracked unless the
# user versions it - which is how such a file gets into the history.
subprocess.run(["git", "add", "big.bin"], cwd=cfg, check=True)
git_manager.auto_commit(str(cfg), label="klein")
target = subprocess.run(["git", "rev-parse", "HEAD"], cwd=cfg,
                        capture_output=True, text=True).stdout.strip()
git_manager.auto_commit(str(cfg), label="weiter")
big.write_bytes(b"y" * 5000)                # tracked, now above the limit
(cfg / MOD).write_text(mine + "# ungesichert\n", encoding="utf-8")
before_big, before_mod = big.read_bytes(), (cfg / MOD).read_text(encoding="utf-8")

ok, msg = git_manager.rollback(str(cfg), target)
check("Wiederherstellen bricht ab", not ok, msg)
check("… mit ERR_ROLLBACK_SKIPPED und Dateiname",
      msg.startswith("ERR_ROLLBACK_SKIPPED") and "big.bin" in msg, msg)
check("… große Datei unverändert", big.read_bytes() == before_big)
check("… Modul unverändert", (cfg / MOD).read_text(encoding="utf-8") == before_mod)

big.write_bytes(b"x" * 100)                 # small again: nothing to leave out
ok, msg = git_manager.rollback(str(cfg), target)
check("Gegenprobe: ohne zu große Datei gelingt es", ok, msg)

print()
if fails:
    print(f"{len(fails)} FEHLER")
    sys.exit(1)
print("Alles grün.")
