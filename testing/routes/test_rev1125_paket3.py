#!/usr/bin/env python3
"""Revision 2026-11-25, Paket 3: Rahmen und Sperre (D1, F6, Dry-Run).

F6 – Verschieben und Neustart fragten nur den angemeldeten Job ab. Ein
Rebuild in der Vorbereitung (Sperre gehalten, noch kein Job) lief neben
einem Umzug, der ihm das Verzeichnis wegnahm. Jetzt eine Reservierung
(rebuild.reserve) als erster Schritt für Rebuild, Dry-Run,
Systemstand-Wechsel, Update, Verschieben, Neustart; die Absage nennt, was
läuft.

D1 – Rebuild, Systemstand-Wechsel und Update hatten je eine Kopie von
Anmeldung, Worker und Abschluss; jetzt ein Rahmen (_start_job). Der Dry-Run
ist die vierte Aufgabe darin (Martin 2026-11-26): reserviert, sein Ergebnis
übersteht ein Neuladen.

Änderung während des Laufs – Dry-Run und Rebuild melden `changed_during`.

Lauf: nix-shell shell.nix --run "python testing/routes/test_rev1125_paket3.py"
"""
import json, os, re, subprocess, sys, tempfile, time
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
(cfg / "config.json").write_text(json.dumps({"hosts_dir": "hosts"}), encoding="utf-8")
(cfg / "configuration.nix").write_text("{ ... }: { }\n", encoding="utf-8")

from nico import configfiles, server  # noqa: E402
from nico.routes import rebuild as rb  # noqa: E402

fails = []
def check(label, cond, got=None):
    print(("  OK   " if cond else "  FAIL ") + label + ("" if cond or got is None else f"  (war: {got})"))
    if not cond: fails.append(label)

client = server.create_app().test_client()
page = client.get("/").get_data(as_text=True)
csrf = re.search(r'<meta name="csrf-token" content="([^"]+)"', page).group(1)
H = {"X-CSRF-Token": csrf, "Content-Type": "application/json"}

def post(url, body=None):
    r = client.post(url, headers=H, data=json.dumps(body or {}))
    return r.status_code, (r.get_json(silent=True) or {})

def wait_job(job_id):
    for _ in range(1200):
        with rb._job_cv:
            j = rb._job
            if j and j["id"] == job_id and not j["running"]:
                return list(j["events"])
        time.sleep(0.05)
    return []

def dry_result(events):
    for ev in events:
        if '"kind": "dryrun"' in ev:
            return json.loads(ev.split("data: ", 1)[1])
    return {}

target = str(Path(tempfile.mkdtemp(prefix="nico-ziel-")) / "neu")

# ── 1. Reservierung ─────────────────────────────────────────────────────────
print("1. Ein Rebuild in der Vorbereitung hält alles andere fern")
check("Reservieren gelingt", rb.reserve("rebuild") is None)
check("… ein zweites Mal nicht", rb.reserve("move") == "ERR_REBUILD_RUNNING")
s, r = post("/api/config/move", {"target": target})
check("Verschieben: 409 ERR_REBUILD_RUNNING", s == 409 and r.get("error") == "ERR_REBUILD_RUNNING", (s, r))
check("… Config nicht verschoben", (cfg / "configuration.nix").exists())
s, r = post("/api/restart")
check("Neustart: 409 ERR_REBUILD_RUNNING", s == 409 and r.get("error") == "ERR_REBUILD_RUNNING", (s, r))
s, r = post("/api/dry-run")
check("Dry-Run: 409 ERR_REBUILD_RUNNING", s == 409 and r.get("error") == "ERR_REBUILD_RUNNING", (s, r))
s, r = post("/api/rebuild/precheck", {"hostname": "h"})
check("Vorab-Analyse: 409", s == 409, (s, r))
rb.release()

print("\n2. Ein Umzug nennt sich")
check("Reservieren für den Umzug", rb.reserve("move") is None)
s, r = post("/api/rebuild/start", {"mode": "test"})
check("Rebuild: 409 ERR_BUSY_MOVE", s == 409 and r.get("error") == "ERR_BUSY_MOVE", (s, r))
s, r = post("/api/dry-run")
check("Dry-Run: 409 ERR_BUSY_MOVE", s == 409 and r.get("error") == "ERR_BUSY_MOVE", (s, r))
rb.release()

print("\n3. Ein laufender Job nennt seine Art")
for mode, code in (("dryrun", "ERR_BUSY_DRYRUN"), ("selfupdate", "ERR_BUSY_UPDATE"),
                   ("generation-boot", "ERR_BUSY_GENERATION"), ("switch", "ERR_REBUILD_RUNNING")):
    with rb._job_cv:
        rb._job = {"id": "x", "running": True, "mode": mode, "events": [], "dropped": 0}
    s, r = post("/api/config/move", {"target": target})
    check(f"{mode}: Verschieben → {code}", s == 409 and r.get("error") == code, (s, r))
with rb._job_cv:
    rb._job = None
check("nach dem Job ist die Reservierung frei", rb.reserve("move") is None)
rb.release()

# ── 4. Dry-Run als Aufgabe ──────────────────────────────────────────────────
print("\n4. Dry-Run läuft als Aufgabe im Rahmen")
real_run = subprocess.run
def fake_ok(cmd, *a, **kw):
    if cmd and cmd[0] == "nix-instantiate":
        return subprocess.CompletedProcess(cmd, 0, '"/nix/store/x.drv"', "")
    return real_run(cmd, *a, **kw)
subprocess.run = fake_ok
try:
    s, r = post("/api/dry-run")
    check("Start: Job-Kennung", s == 200 and r.get("job_id"), (s, r))
    ev = dry_result(wait_job(r.get("job_id")))
    check("Ergebnis als done-Ereignis der Art dryrun", ev.get("type") == "done" and ev.get("kind") == "dryrun", ev)
    check("… ok, semantisch geprüft", ev.get("dry", {}).get("ok") and ev["dry"].get("mode") == "semantic", ev)
    check("… nichts geändert gemeldet", ev.get("dry", {}).get("changed_during") is False, ev)
    st = client.get("/api/rebuild/status").get_json()["job"]
    check("Job bleibt bis zum Ansehen (mode dryrun, nicht acked)",
          st["mode"] == "dryrun" and not st["running"] and not st["acked"], st)
    post("/api/rebuild/ack")
    check("nach dem Ansehen acked", client.get("/api/rebuild/status").get_json()["job"]["acked"])
finally:
    subprocess.run = real_run

print("\n5. Änderung während des Dry-Runs")
def fake_change(cmd, *a, **kw):
    if cmd and cmd[0] == "nix-instantiate":
        (cfg / "configuration.nix").write_text("{ ... }: { x = 1; }\n", encoding="utf-8")
        return subprocess.CompletedProcess(cmd, 0, '"/nix/store/x.drv"', "")
    return real_run(cmd, *a, **kw)
subprocess.run = fake_change
try:
    s, r = post("/api/dry-run")
    ev = dry_result(wait_job(r.get("job_id")))
    check("changed_during gemeldet", ev.get("dry", {}).get("changed_during") is True, ev)
finally:
    subprocess.run = real_run

before = configfiles.nix_state(cfg)
(cfg / "neu.nix").write_text("{ }\n")
check("nix_state erkennt neue Datei", configfiles.nix_state(cfg) != before)
(cfg / "neu.nix").unlink()
check("… und den alten Stand wieder", configfiles.nix_state(cfg) == before)

# ── 6. Ein Rahmen ───────────────────────────────────────────────────────────
print("\n6. Ein Rahmen statt drei Kopien")
src = Path(rb.__file__).read_text(encoding="utf-8")
check("nur eine Stelle legt einen Job an", src.count("_job = {") == 1, src.count("_job = {"))
check("nur eine Stelle startet einen Worker", src.count("threading.Thread(") == 1, src.count("threading.Thread("))
check("Rebuild meldet changed_during", "'changed_during': changed_during" in src)
check("Systemstand-Wechsel und Update reservieren",
      'reserve("generation")' in src and 'reserve("selfupdate")' in src)
srv = Path(server.__file__).read_text(encoding="utf-8")
check("Verschieben und Neustart reservieren", 'reserve("move")' in srv and 'reserve("restart")' in srv)

print()
if fails:
    print(f"{len(fails)} FEHLER")
    sys.exit(1)
print("Alles grün.")
