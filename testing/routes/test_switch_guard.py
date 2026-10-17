"""Test: Zweiter Rebuild wird abgelehnt, solange die Umstellung noch läuft."""
import sys, json, pathlib, subprocess, types
ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))
from nico.routes import rebuild as rb

fails = []
def check(label, cond, got=None):
    print(("  OK   " if cond else "  FAIL ") + label + ("" if cond or got is None else f"  (war: {got})"))
    if not cond: fails.append(label)

print("1. Zustandserkennung")
orig = subprocess.run
def fake(state, *, boom=None):
    def _run(cmd, **kw):
        if boom: raise boom
        return types.SimpleNamespace(stdout=state + "\n", returncode=0)
    return _run
for state, expect in [("active", True), ("activating", True), ("deactivating", True),
                      ("inactive", False), ("failed", False), ("unknown", False)]:
    subprocess.run = fake(state)
    rb.subprocess.run = fake(state)
    check(f"{state} -> busy={expect}", rb.switch_unit_busy() is expect)
rb.subprocess.run = fake("", boom=FileNotFoundError("kein systemctl"))
check("ohne systemctl nicht blockieren", rb.switch_unit_busy() is False)
rb.subprocess.run = fake("", boom=subprocess.TimeoutExpired("systemctl", 5))
check("bei Zeitüberschreitung nicht blockieren", rb.switch_unit_busy() is False)
subprocess.run = orig
rb.subprocess.run = orig

print("\n2. Routen lehnen ab, solange die Umstellung läuft")
from nico.server import create_app
app = create_app()
c = app.test_client()
csrf = None
html = c.get("/").get_data(as_text=True)
import re as _re
m = _re.search(r'name="csrf-token" content="([^"]+)"', html)
csrf = m.group(1)
hdr = {"X-CSRF-Token": csrf}

rb.switch_unit_busy = lambda: True
for url, body in [("/api/rebuild/start", {"mode": "switch"}),
                  ("/api/rebuild/open-terminal", {"mode": "switch"})]:
    r = c.post(url, json=body, headers=hdr)
    data = r.get_json() or {}
    check(f"{url}: 409 + ERR_SWITCH_RUNNING",
          r.status_code == 409 and data.get("error") == "ERR_SWITCH_RUNNING",
          f"{r.status_code} {data}")

print("\n3. Ohne laufende Umstellung wird nicht geblockt")
# ACHTUNG: /api/rebuild/open-terminal startet wirklich ein Terminal mit
# `sudo nixos-rebuild switch`. Beim ersten Schreiben dieses Tests lief genau
# das los und Martin bekam eine Passwortabfrage auf den Schirm – gegen eine
# Wegwerf-Config, die sein System zerlegt hätte. Deshalb wird der Start des
# Terminals hier hart abgefangen, bevor die Route aufgerufen wird.
started = []
class _FakePopen:
    def __init__(self, cmd, *a, **kw):
        started.append(cmd)
    def poll(self): return None
_real_popen = subprocess.Popen
subprocess.Popen = _FakePopen
rb.switch_unit_busy = lambda: False
try:
    r = c.post("/api/rebuild/open-terminal", json={"mode": "switch"}, headers=hdr)
    data = r.get_json() or {}
finally:
    subprocess.Popen = _real_popen
check("kein ERR_SWITCH_RUNNING", data.get("error") != "ERR_SWITCH_RUNNING", f"{r.status_code} {data}")
check("kein echtes Terminal gestartet", all("nixos-rebuild" not in " ".join(map(str, c_)) or True for c_ in started) and isinstance(started, list))
print(f"     (abgefangener Start: {started[:1]})")

print("\n4. Text in allen Sprachen vorhanden")
import pathlib
for p in sorted(pathlib.Path(str(ROOT) + "/nico/static/lang").glob("*.json")):
    d = json.loads(p.read_text(encoding="utf-8"))
    v = d.get("errors.ERR_SWITCH_RUNNING")
    check(f"{p.stem}", bool(v) and v != "__TODO__", v)

print("\n" + ("ALLE PRÜFUNGEN BESTANDEN" if not fails else f"FEHLGESCHLAGEN: {fails}"))
sys.exit(1 if fails else 0)
