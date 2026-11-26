#!/usr/bin/env python3
"""Nachprüfung 2026-11-26 (Codex), Paket C: Terminal-Rebuild belegt die Config.

Bis dahin reservierte der Terminal-Rebuild nichts: sobald das Fenster offen
war, konnten Umzug oder Dry-Run mit Flake-Update neben dem Bau starten.
Jetzt reserviert die Route vor dem Git-Staging und übergibt ohne Lücke an
das Skript (Startbestätigung mit ID, flock bis nach dem letzten Befehl).

Ein Ersatz-Terminal (`konsole`) startet das Skript, ein Ersatz-`sudo`
ersetzt nixos-rebuild. Fälle (Abnahme mit ChatGPT abgestimmt):
  1. regulär: während des Baus Umzug und Dry-Run belegt, danach frei
  2. Sofortlauf: Befehl endet sofort – trotzdem sauber als beendet erkannt
  3. Fenster bleibt offen („Enter“): Config schon frei
  4. Abbruch (kill): frei
  5. Terminal startet nach der Frist: frei, das Skript läuft nicht los
  6. ohne flock: abgelehnt, nichts reserviert
  7. Sperrdatei nicht zu öffnen (Codex C3): „dead“, der Bau läuft nicht an
  8. Beobachtung nach „go“ scheitert zeitweise (C3): belegt bis zum Ende

Aufräumen nur in den eigenen Prozessgruppen (Codex C1): jedes Ersatz-
Terminal startet per setsid eine eigene Gruppe und trägt sie ein; ein
fremder Prozess mit demselben Suchtext bleibt unberührt.

Lauf: nix-shell shell.nix --run "python testing/routes/test_codex_1126_terminal.py"
"""
import json, os, re, shutil, signal, sys, tempfile, time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

xdg = Path(tempfile.mkdtemp(prefix="nico-xdg-"))
os.environ["XDG_CONFIG_HOME"] = str(xdg)
run = Path(tempfile.mkdtemp(prefix="nico-run-"))
os.environ["XDG_RUNTIME_DIR"] = str(run)
cfg = Path(tempfile.mkdtemp(prefix="nico-cfg-"))
(xdg / "nico").mkdir(parents=True)
(xdg / "nico" / "settings.json").write_text(
    json.dumps({"nixos_config_dir": str(cfg), "language": "de", "git_sync": False}),
    encoding="utf-8")
(cfg / "config.json").write_text(json.dumps({"hosts_dir": "hosts"}), encoding="utf-8")
(cfg / "configuration.nix").write_text("{ ... }: { }\n", encoding="utf-8")

# A PATH of its own: the tools the script needs, a fake terminal, a fake sudo.
fake = Path(tempfile.mkdtemp(prefix="nico-bin-"))
for tool in ("bash", "sh", "sleep", "cat", "mv", "rm", "seq", "flock", "touch", "setsid"):
    src = shutil.which(tool)
    assert src, tool
    os.symlink(src, fake / tool)
BASH = shutil.which("bash")
(fake / "konsole").write_text(f"""#!{BASH}
# Own process group: the test cleans up exactly this group (Codex C1).
if [ -z "$FAKE_IN_SID" ]; then export FAKE_IN_SID=1; exec setsid "$0" "$@"; fi
echo $$ >> "$FAKE_PGIDS"
[ -n "$FAKE_TERM_DELAY" ] && sleep "$FAKE_TERM_DELAY"
shift
if [ -n "$FAKE_TERM_HOLD" ]; then
    sleep 1000 | "$@" >> "$FAKE_LOG" 2>&1
else
    "$@" < /dev/null >> "$FAKE_LOG" 2>&1
fi
""")
(fake / "sudo").write_text(f"""#!{BASH}
touch "$FAKE_MARK"
sleep "${{FAKE_SLEEP:-0}}"
""")
for f in ("konsole", "sudo"):
    os.chmod(fake / f, 0o755)
os.environ["PATH"] = str(fake)
LOG = run / "term.log"
os.environ["FAKE_LOG"] = str(LOG)
PGIDS = run / "pgids"
os.environ["FAKE_PGIDS"] = str(PGIDS)

from nico import server  # noqa: E402
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

def is_free():
    if rb.reserve("move") is None:
        rb.release()
        return True
    return False

def wait(cond, secs):
    end = time.monotonic() + secs
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.05)
    return cond()

def groups():
    """The process groups of the fake terminals started so far."""
    try:
        return [int(x) for x in PGIDS.read_text().split()]
    except OSError:
        return []

def group_alive(pgs=None):
    """A process of these groups still runs. Read from /proc, not with
    killpg(pg, 0): the fake terminal is a child nobody waits for, and its
    zombie answers that call as if it were alive."""
    want = set(groups() if pgs is None else pgs)
    for d in Path("/proc").iterdir():
        if not d.name.isdigit():
            continue
        try:
            rest = (d / "stat").read_text().rsplit(")", 1)[1].split()
        except (OSError, IndexError):
            continue
        state, pgrp = rest[0], int(rest[2])
        if pgrp in want and state not in ("Z", "X"):
            return True
    return False

def kill_groups():
    """End the fake terminals and everything they started - nothing else.
    Returns the groups it ended, so a check can look at exactly those
    (Codex: checking the emptied list afterwards always passed)."""
    pgs = groups()
    for pg in pgs:
        try:
            os.killpg(pg, signal.SIGKILL)
        except OSError:
            pass
    PGIDS.write_text("")
    return pgs

import atexit  # noqa: E402
atexit.register(kill_groups)      # also when the test breaks off (Codex C1)

def start(sleep, mark, delay="", hold=""):
    os.environ.update(FAKE_SLEEP=str(sleep), FAKE_MARK=str(mark),
                      FAKE_TERM_DELAY=delay, FAKE_TERM_HOLD=hold)
    return post("/api/rebuild/open-terminal", {"mode": "switch"})

# ── 1. regulär ──────────────────────────────────────────────────────────────
print("1. Regulärer Lauf")
mark = run / "m1"
s, r = start(1.5, mark)
check("Terminal gestartet", s == 200 and r.get("success"), (s, r))
check("Belegt ab dem Start der Route", not is_free())
check("Befehl läuft", wait(mark.exists, 10))
check("Umzug während des Baus: belegt", rb.reserve("move") == "ERR_REBUILD_RUNNING")
s, r = post("/api/dry-run")
check("Dry-Run während des Baus: 409", s == 409, (s, r))
check("Nach dem Ende wieder frei", wait(is_free, 10))

# ── 2. Sofortlauf ───────────────────────────────────────────────────────────
print("2. Befehl endet sofort")
mark = run / "m2"
s, r = start(0, mark)
check("Gestartet", s == 200, (s, r))
check("Befehl lief und Config wird frei", wait(lambda: mark.exists() and is_free(), 10))

# ── 3. Fenster bleibt offen ─────────────────────────────────────────────────
print("3. Befehl fertig, Fenster wartet auf Enter")
mark = run / "m3"
s, r = start(0.3, mark, hold="1")
check("Gestartet", s == 200, (s, r))
check("Config frei, obwohl das Fenster noch offen ist",
      wait(lambda: mark.exists() and is_free(), 10))
check("… das Skript wartet tatsächlich noch", group_alive())
import subprocess  # noqa: E402
decoy = subprocess.Popen([BASH, "-c", "sleep 1000.5; : nico-rebuild-decoy"], start_new_session=True)
ended = kill_groups()
check("Aufräumen beendet das Ersatz-Terminal", ended and wait(lambda: not group_alive(ended), 5), ended)
check("… ein fremder Prozess mit demselben Suchtext läuft weiter", decoy.poll() is None)
os.killpg(decoy.pid, signal.SIGKILL); decoy.wait()   # its sleep, too

# ── 4. Abbruch ──────────────────────────────────────────────────────────────
print("4. Abbruch mitten im Bau")
mark = run / "m4"
s, r = start(31.7, mark)
check("Gestartet, Befehl läuft", s == 200 and wait(mark.exists, 10), (s, r))
check("Belegt", not is_free())
kill_groups()
check("Nach dem Abbruch frei", wait(is_free, 10))

# ── 5. Terminal startet nach der Frist ──────────────────────────────────────
print("5. Terminal startet erst nach der Frist")
rb.TERMINAL_START_TIMEOUT = 1.0
mark = run / "m5"
LOG.write_text("")
s, r = start(0, mark, delay="2.5")
check("Gestartet", s == 200, (s, r))
check("Nach der Frist frei", wait(is_free, 4))
time.sleep(3.5)
check("Das verspätete Skript hat nichts ausgeführt", not mark.exists())
check("… und sagt, dass der Start verfallen ist", "expired" in LOG.read_text(), LOG.read_text())
answers = [a.read_text().strip() for a in (run / "nico").glob("*.answer")]
check("Antwort für die ID: dead", any(a.startswith("dead ") for a in answers), answers)
rb.TERMINAL_START_TIMEOUT = 60.0

# ── 6. ohne flock ───────────────────────────────────────────────────────────
print("6. flock fehlt")
_FLOCK = os.readlink(fake / "flock")
(fake / "flock").unlink()
s, r = start(0, run / "m6")
check("Abgelehnt mit ERR_NO_FLOCK", s == 409 and r.get("error") == "ERR_NO_FLOCK", (s, r))
check("… nichts reserviert", is_free())

# ── 7. Sperrdatei nicht zu öffnen ───────────────────────────────────────────
print("7. NiCo kann die Sperrdatei nicht öffnen")
os.symlink(_FLOCK, fake / "flock")
real_open = os.open
def failing_open(path, *a, **kw):
    if str(path).endswith("terminal.lock"):
        raise PermissionError("simuliert")
    return real_open(path, *a, **kw)
os.open = failing_open
mark = run / "m7"
LOG.write_text("")
s, r = start(0, mark)
check("Gestartet", s == 200, (s, r))
check("Config wird frei", wait(is_free, 10))
os.open = real_open
time.sleep(1.0)
check("Der Bau ist nicht angelaufen", not mark.exists())
check("… das Skript meldet den verfallenen Start", wait(lambda: "expired" in LOG.read_text(), 5),
      LOG.read_text())

# ── 8. Beobachtung scheitert zeitweise ──────────────────────────────────────
print("8. Beobachtung nach 'go' scheitert zweimal")
import fcntl  # noqa: E402
real_flock = fcntl.flock
fails_left = {"n": 2}
def flaky_flock(fd, op):
    if op == fcntl.LOCK_EX and fails_left["n"] > 0:
        fails_left["n"] -= 1
        raise OSError("simuliert")
    return real_flock(fd, op)
fcntl.flock = flaky_flock
mark = run / "m8"
s, r = start(3.5, mark)
check("Gestartet, Befehl läuft", s == 200 and wait(mark.exists, 10), (s, r))
time.sleep(1.5)
check("Trotz Beobachtungsfehler belegt", fails_left["n"] == 0 and not is_free(), fails_left)
s, r = post("/api/dry-run")
check("… Dry-Run: 409", s == 409, (s, r))
check("Nach bestätigtem Ende frei", wait(is_free, 10))
fcntl.flock = real_flock

# ── 9. Aufräumen nach "go" scheitert ────────────────────────────────────────
print("9. Löschen der temporären Antwortdatei scheitert nach 'go'")
real_unlink = Path.unlink
def failing_unlink(self, *a, **kw):
    if ".answer." in self.name and self.name.endswith(".tmp"):
        raise PermissionError("simuliert")
    return real_unlink(self, *a, **kw)
Path.unlink = failing_unlink
mark = run / "m9"
s, r = start(3.0, mark)
check("Gestartet, Befehl läuft", s == 200 and wait(mark.exists, 10), (s, r))
time.sleep(1.0)
check("Trotz Aufräumfehler belegt, solange gebaut wird", not is_free())
s, r = post("/api/dry-run")
check("… Dry-Run: 409", s == 409, (s, r))
check("Nach dem Ende frei", wait(is_free, 10))
Path.unlink = real_unlink

kill_groups()
print()
print("ALLE TESTS BESTANDEN" if not fails else f"{len(fails)} FEHLGESCHLAGEN: {fails}")
sys.exit(1 if fails else 0)
