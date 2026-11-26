#!/usr/bin/env python3
"""Nachprüfung 2026-11-26 (Codex), Paket C: Belegung der Config.

1. Dry-Run-Ergebnis je Job: Der Server hält einen Job. Startete ein zweiter
   Lauf zwischen dem Ende des ersten und dem Moment, in dem dessen Tab den
   Stream öffnete, bekam der Tab das fremde Ergebnis – und ein Schreiben mit
   vorgeschaltetem Dry-Run lief auf dem Erfolg eines anderen weiter. Jetzt
   führt der Tab die Job-ID bis zu Stream und Bestätigung mit; Ergebnisse
   bleiben je ID bis zur Abholung (höchstens acht), eine verdrängte ID
   antwortet „gone“.

Lauf: nix-shell shell.nix --run "python testing/routes/test_codex_1126_paket_c.py"
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

from nico import server  # noqa: E402
from nico.routes import rebuild as rb  # noqa: E402

fails = []
def check(label, cond, got=None):
    print(("  OK   " if cond else "  FAIL ") + label + ("" if cond or got is None else f"  (war: {got})"))
    if not cond: fails.append(label)

app = server.create_app()
client = app.test_client()
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
                return
        time.sleep(0.05)

def stream(job_id=None):
    """Events of /api/rebuild/stream for `job_id`, parsed."""
    url = f"/api/rebuild/stream?token={csrf}&from=0" + (f"&job={job_id}" if job_id else "")
    text = client.get(url).get_data(as_text=True)
    out = []
    for line in text.splitlines():
        if line.startswith("data: "):
            out.append(json.loads(line[6:]))
    return out

def dry_of(events):
    for ev in events:
        if ev.get("type") == "done" and ev.get("kind") == "dryrun":
            return ev["dry"]
        if ev.get("type") == "gone":
            return "gone"
    return None

# Dry runs without nix: nix-instantiate answers as `outcome` says.
outcome = {"ok": True}
real_run = subprocess.run
def fake_run(cmd, *a, **kw):
    if cmd and cmd[0] == "nix-instantiate":
        if outcome["ok"]:
            return subprocess.CompletedProcess(cmd, 0, '"/nix/store/x.drv"', "")
        return subprocess.CompletedProcess(cmd, 1, "", "error: undefined variable 'kaputt'")
    return real_run(cmd, *a, **kw)
subprocess.run = fake_run

def start_dry(ok):
    outcome["ok"] = ok
    s, r = post("/api/dry-run")
    wait_job(r.get("job_id"))
    return r.get("job_id")

# ── 1. Dry-Run je Job ───────────────────────────────────────────────────────
print("1. A scheitert, B gelingt, A holt sein Ergebnis erst danach ab")
a = start_dry(False)
b = start_dry(True)
check("Zwei verschiedene Jobs", a and b and a != b, (a, b))
res_a = dry_of(stream(a))
check("Tab A bekommt seinen eigenen Fehler, nicht den Erfolg von B",
      isinstance(res_a, dict) and res_a.get("ok") is False, res_a)
post("/api/rebuild/ack", {"job": a})
st = client.get("/api/rebuild/status").get_json()["job"]
check("Bestätigung aus A markiert B nicht als gesehen", st["id"] == b and not st["acked"], st)
res_b = dry_of(stream(b))
check("Tab B bekommt seinen Erfolg", isinstance(res_b, dict) and res_b.get("ok") is True, res_b)
post("/api/rebuild/ack")
check("Bestätigung ohne ID (Monitor) markiert einen Dry-Run nicht",
      not client.get("/api/rebuild/status").get_json()["job"]["acked"])
post("/api/rebuild/ack", {"job": b})
check("Bestätigung mit der eigenen ID markiert ihn",
      client.get("/api/rebuild/status").get_json()["job"]["acked"])

print("1b. Verdrängte ID: Ergebnis nicht mehr verfügbar")
c = start_dry(True)
for _ in range(rb._DRY_RESULTS_MAX):
    start_dry(True)
res_c = dry_of(stream(c))
check("Tab C bekommt 'gone' statt eines fremden Ergebnisses", res_c == "gone", res_c)
check("Unbekannte ID ebenso", dry_of(stream("0" * 32)) == "gone")
check("Höchstens acht Ergebnisse vorgehalten", len(rb._dry_results) <= rb._DRY_RESULTS_MAX,
      len(rb._dry_results))

print("1c. Stream ohne ID (Monitor) bleibt wie bisher")
d = start_dry(True)
check("Aktueller Job wird gestreamt", isinstance(dry_of(stream()), dict))
check("… und mit der eigenen ID ebenso", isinstance(dry_of(stream(d)), dict))

subprocess.run = real_run

# ── 2. Schreibschutz ────────────────────────────────────────────────────────
import inspect, threading  # noqa: E402
from nico import atomic, writeguard  # noqa: E402

# A save that stops in the middle of writing until released.
hold = {"path": None, "inside": threading.Event(), "go": threading.Event()}
real_write_locked = atomic._write_locked
def held_write(target, data, mode):
    if hold["path"] and str(target).endswith(hold["path"]):
        hold["inside"].set()
        hold["go"].wait(20)
    return real_write_locked(target, data, mode)
atomic._write_locked = held_write

MOD = "modules/m.nix"
(cfg / "modules").mkdir(exist_ok=True)
(cfg / MOD).write_text("{ ... }:\n{\n  # alt\n}\n", encoding="utf-8")
(cfg / "modules/b.nix").write_text("{ }\n", encoding="utf-8")

def save_text(rel, text, c=None):
    c = c or app.test_client()
    base = c.get(f"/api/file?path={rel}", headers=H).get_json().get("_base")
    r = c.post("/api/file", headers=H, data=json.dumps(
        {"files": [{"path": rel, "content": text, "base": base}]}))
    return r.status_code, (r.get_json(silent=True) or {})

def held_save(text, results, key):
    """Start a save of MOD in a thread that stops inside the write."""
    hold.update(path=MOD)
    hold["inside"].clear(); hold["go"].clear()
    t = threading.Thread(target=lambda: results.__setitem__(key, save_text(MOD, text)))
    t.start()
    hold["inside"].wait(10)
    return t

def release_save(t):
    hold["go"].set()
    t.join(10)
    hold.update(path=None)

print("\n2a. Speicherung hängt, Umzug: nach Ablauf 'belegt', danach sofort frei")
writeguard.WAIT_SECONDS = 0.6
res = {}
t = held_save("{ ... }:\n{\n  # gespeichert\n}\n", res, "save")
check("Speicherung läuft (als Schreiber gezählt)", writeguard.state()["writers"] == 1, writeguard.state())
target1 = Path(tempfile.mkdtemp(prefix="nico-ziel-")) / "neu"
t0 = time.monotonic()
s, r = post("/api/config/move", {"target": str(target1)})
waited = time.monotonic() - t0
check("Umzug: 409 ERR_BUSY_WRITE nach Ablauf der Wartezeit",
      s == 409 and r.get("error") == "ERR_BUSY_WRITE" and waited >= 0.5, (s, r, round(waited, 2)))
check("… nichts verschoben", (cfg / "configuration.nix").exists() and not target1.exists())
check("… Config sofort wieder frei", writeguard.state()["exclusive"] is None, writeguard.state())
s2, r2 = save_text("modules/b.nix", "{ b = 1; }\n")
check("Ein weiterer Tab speichert sofort wieder", s2 == 200 and r2.get("success"), (s2, r2))
release_save(t)
check("Die hängende Speicherung kommt danach an", res.get("save", (0,))[0] == 200, res)

print("\n2b. Umzug wartet: neue Schreiber abgewiesen, laufende dürfen fertig werden")
writeguard.WAIT_SECONDS = 5.0
res = {}
t = held_save("{ ... }:\n{\n  # zuletzt gespeichert\n}\n", res, "save")
target2 = Path(tempfile.mkdtemp(prefix="nico-ziel-")) / "neu"
mv = {}
mt = threading.Thread(target=lambda: mv.__setitem__("r", app.test_client().post(
    "/api/config/move", headers=H, data=json.dumps({"target": str(target2)}))))
mt.start()
for _ in range(50):
    if writeguard.state()["exclusive"] == "move":
        break
    time.sleep(0.05)
check("Umzug wartet (exklusiv angemeldet)", writeguard.state()["exclusive"] == "move", writeguard.state())
s3, r3 = save_text("modules/b.nix", "{ b = 2; }\n")
check("Neuer Schreiber während des Wartens: 409 ERR_BUSY_MOVE",
      s3 == 409 and r3.get("error") == "ERR_BUSY_MOVE", (s3, r3))
release_save(t)
mt.join(20)
mr = mv["r"]
check("Umzug gelingt, nachdem die Speicherung fertig ist",
      mr.status_code == 200 and (mr.get_json() or {}).get("success"), mr.get_json())
check("Am Ziel steht der zuletzt gespeicherte Inhalt",
      "zuletzt gespeichert" in (target2 / MOD).read_text(), (target2 / MOD).read_text()
      if (target2 / MOD).exists() else "fehlt")
cfg = target2      # NiCo works with the new place from here on
check("Danach ist die Config frei", writeguard.state() == {"writers": 0, "exclusive": None},
      writeguard.state())

print("\n2c. Mehrdatei-Aktion und Jobs: in beide Richtungen getrennt")
ZIP = None
import io, zipfile  # noqa: E402
buf = io.BytesIO()
with zipfile.ZipFile(buf, "w") as z:
    z.writestr("configuration.nix", "{ ... }: { }\n")
ZIP = buf.getvalue()
def zip_import(c=None):
    c = c or client
    r = c.post("/api/import/zip/apply", headers={"X-CSRF-Token": csrf},
               data={"file": (io.BytesIO(ZIP), "x.zip"), "confirmed": "true"},
               content_type="multipart/form-data")
    return r.status_code, (r.get_json(silent=True) or {})

check("Rebuild in Vorbereitung reserviert", rb.reserve("rebuild") is None)
s, r = zip_import()
check("Import während Rebuild-Vorbereitung: 409 ERR_REBUILD_RUNNING",
      s == 409 and r.get("error") == "ERR_REBUILD_RUNNING", (s, r))
rb.release()
with rb._job_cv:
    saved_job = rb._job
    rb._job = {**(saved_job or {}), "id": "x" * 32, "running": True, "mode": "dryrun",
               "events": [], "dropped": 0}
s, r = zip_import()
check("Import während laufendem Dry-Run: 409 ERR_BUSY_DRYRUN",
      s == 409 and r.get("error") == "ERR_BUSY_DRYRUN", (s, r))
with rb._job_cv:
    rb._job["mode"] = "generation-switch"
check("Systemstand-Wechsel läuft: Mehrdatei-Aktion erlaubt", rb.reserve_config_action("import") is None)
rb.release_config_action()
with rb._job_cv:
    rb._job = saved_job

check("Import zuerst reserviert", rb.reserve_config_action("import") is None)
check("… kein Rebuild", rb.reserve("rebuild") == "ERR_BUSY_CONFIG_ACTION")
check("… kein Dry-Run", rb.reserve("dryrun") == "ERR_BUSY_CONFIG_ACTION")
s, r = post("/api/rebuild/precheck", {})
check("… kein Rebuild-Vorabcheck (Git-Staging)", s == 409 and r.get("error") == "ERR_BUSY_CONFIG_ACTION",
      (s, r))
check("… kein Umzug", rb.reserve("move") == "ERR_BUSY_CONFIG_ACTION")
check("… zweite Mehrdatei-Aktion abgewiesen", rb.reserve_config_action("restore") == "ERR_BUSY_CONFIG_ACTION")
ok_gen = rb.reserve("generation")
check("… Systemstand-Wechsel bleibt frei", ok_gen is None, ok_gen)
if ok_gen is None:
    rb.release()
rb.release_config_action()
check("Nach dem Import ist ein Rebuild wieder möglich", rb.reserve("rebuild") is None)
rb.release()

print("\n2d. Mehrdatei-Aktion und Speichern")
writeguard.WAIT_SECONDS = 0.6
(cfg / MOD).write_text("{ ... }:\n{\n  # vor Import\n}\n", encoding="utf-8")
res = {}
t = held_save("{ ... }:\n{\n  # während Import\n}\n", res, "save")
s, r = zip_import()
check("Import, während gespeichert wird: 409 ERR_BUSY_WRITE, nichts importiert",
      s == 409 and r.get("error") == "ERR_BUSY_WRITE" and (cfg / MOD).exists(), (s, r))
check("… Job-Seite wieder frei", rb._config_action is None, rb._config_action)
release_save(t)
writeguard.WAIT_SECONDS = 5.0
# The import itself held in the middle: a save in between is refused.
imp_inside, imp_go = threading.Event(), threading.Event()
from nico import importer  # noqa: E402
real_replace = importer.replace_nix_tree
def held_replace(dst, files):
    imp_inside.set(); imp_go.wait(20)
    return real_replace(dst, files)
importer.replace_nix_tree = held_replace
UNSTAMPED = cfg / "modules/ohne_stempel.nix"
UNSTAMPED.write_text("{ ohne = true; }\n", encoding="utf-8")
unstamped_before = UNSTAMPED.read_bytes()
ir = {}
it = threading.Thread(target=lambda: ir.__setitem__("r", zip_import(app.test_client())))
it.start()
imp_inside.wait(10)
s4, r4 = save_text("modules/b.nix", "{ b = 3; }\n")
check("Speichern während des Imports: 409 ERR_BUSY_CONFIG_ACTION",
      s4 == 409 and r4.get("error") == "ERR_BUSY_CONFIG_ACTION", (s4, r4))
check("… auch eine schreibende GET-Route (Typstempel)",
      client.get(f"/api/file?path=configuration.nix", headers=H).status_code == 409)
check("… lesen ohne Token geht weiter",
      client.get("/api/file?path=configuration.nix").status_code == 200)
r_head = client.head("/api/file?path=modules/ohne_stempel.nix", headers=H)
check("… HEAD mit Token auf ein ungestempeltes Modul: 409, Datei bytegleich (Codex C4)",
      r_head.status_code == 409 and UNSTAMPED.read_bytes() == unstamped_before,
      (r_head.status_code, UNSTAMPED.read_bytes()))

# Codex C2: generation switch and update stay free next to an import - the
# real routes, up to their job reservation (stopped there, nothing runs).
from types import SimpleNamespace  # noqa: E402
from nico import generations, hostread, selfupdate  # noqa: E402
saved = (hostread.allowed, generations.list_generations, selfupdate.running_store_path,
         selfupdate.profile_entry, rb.reserve)
reached = []
hostread.allowed = lambda kind: True
generations.list_generations = lambda *a, **k: [SimpleNamespace(number=5, default=True)]
selfupdate.running_store_path = lambda *a, **k: "/nix/store/x-nico"
selfupdate.profile_entry = lambda *a, **k: ("nico", "github:x/nico")
def stop_at_reserve(kind):
    reached.append(kind)
    return "ERR_TEST_STOP"
rb.reserve = stop_at_reserve
try:
    s6, r6 = post("/api/generations/switch", {"number": 5, "mode": "switch"})
    check("Systemstand-Wechsel während des Imports erreicht seine Reservierung",
          "generation" in reached and r6.get("error") == "ERR_TEST_STOP", (s6, r6, reached))
    s7, r7 = post("/api/selfupdate", {})
    check("Update während des Imports erreicht seine Reservierung",
          "selfupdate" in reached and r7.get("error") == "ERR_TEST_STOP", (s7, r7, reached))
finally:
    (hostread.allowed, generations.list_generations, selfupdate.running_store_path,
     selfupdate.profile_entry, rb.reserve) = saved
s8, r8 = post("/api/rebuild/start", {"mode": "switch"})
check("Ein Rebuild während des Imports: 409", s8 == 409, (s8, r8))
imp_go.set()
it.join(20)
importer.replace_nix_tree = real_replace
check("Import gelingt danach", ir["r"][0] == 200, ir["r"])
check("Danach alles frei", writeguard.state() == {"writers": 0, "exclusive": None}
      and rb._config_action is None, (writeguard.state(), rb._config_action))

print("\n2e. Neustart hält die Config bis zum Ende")
from nico import restart as _restart  # noqa: E402
real_spawn, real_exit = _restart.spawn, os._exit
_restart.spawn = lambda port: None
# The route exits 0.3 s after answering. The fake stays until it was called
# - restored earlier, the real exit ended this test with status 0 before
# its last checks (Codex C5).
exit_called = threading.Event()
os._exit = lambda code: exit_called.set()
r = client.post("/api/restart", headers=H, environ_base={"SERVER_PORT": "8999"})
check("Neustart angenommen", r.status_code == 200, r.get_json())
check("… der verzögerte Exit wurde (simuliert) aufgerufen", exit_called.wait(5))
os._exit, _restart.spawn = real_exit, real_spawn
check("… Config bleibt gehalten", writeguard.state()["exclusive"] == "restart", writeguard.state())
s5, r5 = save_text("modules/b.nix", "{ b = 4; }\n")
check("… Speichern danach: 409 ERR_BUSY_RESTART", s5 == 409 and r5.get("error") == "ERR_BUSY_RESTART",
      (s5, r5))
writeguard.leave_exclusive()
rb.release()

print("\n2f. Routentabelle")
rules = {r.rule: r for r in app.url_map.iter_rules() if r.rule.startswith("/api/")}
listed = set(writeguard.EXCLUSIVE) | writeguard.GET_WRITERS | writeguard.NOT_CONFIG | writeguard.GET_NOT_CONFIG
check("Jeder Eintrag der Tabelle ist eine echte Route", listed <= set(rules), sorted(listed - set(rules)))
forgotten = []
for rule, r in rules.items():
    if r.methods & {"POST", "PUT", "PATCH", "DELETE"}:
        continue        # writing methods are a normal write unless listed otherwise
    src = inspect.getsource(app.view_functions[r.endpoint])
    if "_check_csrf" in src and rule not in writeguard.GET_WRITERS | writeguard.GET_NOT_CONFIG:
        forgotten.append(rule)
check("Jede GET-Route mit Token ist eingeordnet (schreibend oder nicht)", forgotten == [], forgotten)
for rule in ("/api/import/zip/apply", "/api/git/rollback", "/api/git/pull", "/api/git/large-files/clean",
             "/api/config/detach", "/api/categorize", "/api/config/move", "/api/restart"):
    check(f"exklusiv: {rule}", writeguard.classify(rule, "POST", True) == "exclusive")
check("normal: Speichern", writeguard.classify("/api/file", "POST", True) == "shared")
check("normal: GET /api/config mit Token", writeguard.classify("/api/config", "GET", True) == "shared")

atomic._write_locked = real_write_locked
print()
print("ALLE TESTS BESTANDEN" if not fails else f"{len(fails)} FEHLGESCHLAGEN: {fails}")
sys.exit(1 if fails else 0)
