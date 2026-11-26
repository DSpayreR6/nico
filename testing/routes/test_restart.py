#!/usr/bin/env python3
"""NiCo aus der Oberfläche neu starten, 2026-11-25.

Angeboten am Ende eines Updates und im Beenden-Dialog. Die laufende NiCo
startet eine neue (nico/restart.py) und beendet sich; die neue wartet auf
den freien Port und übernimmt ihn (main._restart_port), der Browser-Tab
lädt neu, sobald /api/status eine andere Instanz meldet. Gestartet wird im
Test nichts – Popen und os._exit sind ersetzt.

Geprüft wird:
  1. Befehl: aus dem Quellbaum derselbe Interpreter mit -m nico.main; aus
     dem Nix-Store das `nico` auf PATH, ohne eines derselbe Interpreter.
  2. spawn: Port in der Umgebung, eigene Sitzung, kein Shell, stdin zu.
  3. main._restart_port: ohne Variable None; freier Port wird genommen und
     die Variable entfernt; belegter Port nach der Wartezeit None.
  4. Route: ohne CSRF abgelehnt; laufender Job 409; Erfolg startet und
     beendet, meldet die Instanz von /api/status; Startfehler 500
     ERR_RESTART_FAILED, ohne sich zu beenden.

Lauf: nix-shell shell.nix --run "python testing/routes/test_restart.py"
"""
import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import time
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
(cfg / "configuration.nix").write_text("{ ... }:\n{\n}\n", encoding="utf-8")

from nico import main, restart, server  # noqa: E402
from nico.routes import rebuild as rb  # noqa: E402

fails = []


def check(label, cond, got=None):
    print(("  OK   " if cond else "  FAIL ") + label
          + ("" if cond or got is None else f"  (war: {got})"))
    if not cond:
        fails.append(label)


# ── 1. Befehl ────────────────────────────────────────────────────────────────
print("1. Befehl")
real_file, real_which = restart.__file__, restart.shutil.which
check("Quellbaum: Interpreter -m nico.main",
      restart.restart_command() == [sys.executable, "-m", "nico.main"],
      restart.restart_command())
restart.__file__ = "/nix/store/abc-nico/lib/nico/nico/restart.py"
restart.shutil.which = lambda name: "/home/u/.nix-profile/bin/nico" if name == "nico" else None
check("Nix-Store: nico auf PATH (nach Update die neue Version)",
      restart.restart_command() == ["/home/u/.nix-profile/bin/nico"], restart.restart_command())
restart.shutil.which = lambda name: None
check("Nix-Store ohne nico auf PATH: derselbe Interpreter",
      restart.restart_command() == [sys.executable, "-m", "nico.main"], restart.restart_command())
restart.__file__, restart.shutil.which = real_file, real_which

# ── 2. spawn ─────────────────────────────────────────────────────────────────
print("2. spawn")
seen = {}
real_popen = restart.subprocess.Popen
restart.subprocess.Popen = lambda cmd, **kw: seen.update(cmd=cmd, **kw)
restart.spawn(8421)
restart.subprocess.Popen = real_popen
check("Port in der Umgebung", seen.get("env", {}).get(restart.ENV_PORT) == "8421", seen.get("env", {}).get(restart.ENV_PORT))
check("eigene Sitzung (überlebt das Ende des alten Prozesses)", seen.get("start_new_session") is True)
check("kein Shell", not seen.get("shell") and isinstance(seen.get("cmd"), list))
check("stdin geschlossen", seen.get("stdin") == subprocess.DEVNULL)
check("Umgebung des laufenden Prozesses sonst übernommen",
      seen.get("env", {}).get("XDG_CONFIG_HOME") == str(xdg))

# ── 3. main._restart_port ────────────────────────────────────────────────────
print("3. main._restart_port")
os.environ.pop(restart.ENV_PORT, None)
check("ohne Variable: normaler Start", main._restart_port() is None)
with socket.socket() as s:
    s.bind(("127.0.0.1", 0))
    free = s.getsockname()[1]
os.environ[restart.ENV_PORT] = str(free)
check("freier Port wird übernommen", main._restart_port() == free)
check("Variable danach entfernt", restart.ENV_PORT not in os.environ)
# Busy port stubbed: a socket that never accepts fills its backlog, and the
# real check would then wait in connect().
os.environ[restart.ENV_PORT] = "8421"
real_free, real_sleep = main._is_port_free, main.time.sleep
main._is_port_free = lambda port: False
main.time.sleep = lambda s: None
check("belegter Port nach der Wartezeit: normaler Start", main._restart_port() is None)
main._is_port_free, main.time.sleep = real_free, real_sleep

# ── 4. Route ─────────────────────────────────────────────────────────────────
print("4. Route")
app = server.create_app()
client = app.test_client()
page = client.get("/").get_data(as_text=True)
m = re.search(r'<meta name="csrf-token" content="([^"]+)"', page)
hdr = {"X-CSRF-Token": m.group(1) if m else "", "Content-Type": "application/json"}

spawned, exited = [], []
restart.spawn = lambda port: spawned.append(port)
server.os._exit = lambda code: exited.append(code)

r = client.post("/api/restart", headers={"Content-Type": "application/json"}, data="{}")
check("ohne CSRF abgelehnt", r.status_code in (400, 403) and not spawned, r.status_code)

with rb._job_cv:
    rb._job = {"running": True}
r = client.post("/api/restart", headers=hdr, data="{}")
check("laufender Job: 409 ERR_REBUILD_RUNNING",
      r.status_code == 409 and r.get_json().get("error") == "ERR_REBUILD_RUNNING" and not spawned,
      (r.status_code, r.get_json()))
with rb._job_cv:
    rb._job = None

inst = client.get("/api/status").get_json().get("instance")
check("Status meldet eine Instanz", bool(inst), inst)
r = client.post("/api/restart", headers=hdr, data="{}", base_url="http://127.0.0.1:8421")
time.sleep(0.6)
check("Erfolg: neue NiCo auf demselben Port gestartet", spawned == [8421], spawned)
check("Erfolg: alte beendet sich", exited == [0], exited)
check("Antwort nennt die alte Instanz", r.get_json() == {"ok": True, "instance": inst}, r.get_json())

# The reservation stays with a restart that succeeded (revision 2026-11-25,
# F6): the process ends, nothing may start in between - here it does not
# end (os._exit is replaced), so the next start is refused.
r = client.post("/api/restart", headers=hdr, data="{}", base_url="http://127.0.0.1:8421")
check("nach dem Neustart: nichts startet mehr (ERR_BUSY_RESTART)",
      r.status_code == 409 and r.get_json().get("error") == "ERR_BUSY_RESTART", r.get_json())
rb.release()   # the new process starts without it

spawned.clear(); exited.clear()


def boom(port):
    raise OSError("nope")


restart.spawn = boom
r = client.post("/api/restart", headers=hdr, data="{}", base_url="http://127.0.0.1:8421")
time.sleep(0.6)
check("Startfehler: 500 ERR_RESTART_FAILED",
      r.status_code == 500 and r.get_json().get("error") == "ERR_RESTART_FAILED",
      (r.status_code, r.get_json()))
check("Startfehler: alte bleibt am Leben", exited == [], exited)

if fails:
    print(f"\nFEHLGESCHLAGEN: {len(fails)}")
    sys.exit(1)
print("\nAlle Prüfungen bestanden.")
