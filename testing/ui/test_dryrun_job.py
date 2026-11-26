"""Browser-Test: Der Dry-Run übersteht ein Neuladen (Revision 2026-11-25, D1).

Seit 2026-11-26 läuft der Dry-Run als Aufgabe im selben Rahmen wie der
Rebuild. Lädt man die Seite neu, während er läuft, öffnet sich sein Fenster
wieder und zeigt das Ergebnis, sobald es da ist. Danach (gesehen) erscheint
es nicht mehr. Ändert sich die Config während des Laufs, steht der Hinweis
darunter. Nix selbst wird ersetzt (langsamer Erfolg).

Die Wegwerf-Config und XDG_CONFIG_HOME legt der Test selbst an.
"""
import json, os, pathlib, subprocess, sys, tempfile, threading, time

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

xdg = pathlib.Path(tempfile.mkdtemp(prefix="nico-xdg-"))
cfg = pathlib.Path(tempfile.mkdtemp(prefix="nico-cfg-"))
os.environ["XDG_CONFIG_HOME"] = str(xdg)
os.environ["XDG_CACHE_HOME"] = str(xdg / "cache")
(xdg / "nico").mkdir(parents=True)
(xdg / "nico" / "settings.json").write_text(json.dumps(
    {"nixos_config_dir": str(cfg), "language": "de", "git_sync": False,
     "git_status_only": False}), encoding="utf-8")
(cfg / "config.json").write_text(json.dumps({"hosts_dir": "hosts"}), encoding="utf-8")

from nico import config_manager, generator
from nico.server import create_app
data = config_manager.get_defaults(); data["_flake_mode"] = False
(cfg / "configuration.nix").write_text(generator.generate_configuration_nix(data), encoding="utf-8")

real_run = subprocess.run
change = {"on": False}
def fake_run(cmd, *a, **kw):
    if isinstance(cmd, list) and cmd and cmd[0] == "nix-instantiate":
        time.sleep(6)
        if change["on"]:
            p = cfg / "configuration.nix"
            p.write_text(p.read_text() + "\n# geändert\n")
        return subprocess.CompletedProcess(cmd, 0, '"/nix/store/x.drv"', "")
    return real_run(cmd, *a, **kw)
subprocess.run = fake_run

from selenium import webdriver
from selenium.webdriver.chrome.service import Service

PORT = 8843
app = create_app()
threading.Thread(target=lambda: app.run(port=PORT, threaded=True), daemon=True).start()
time.sleep(2)
opts = webdriver.ChromeOptions()
for f in ("--headless=new", "--no-sandbox", "--disable-dev-shm-usage", "--window-size=1400,900"):
    opts.add_argument(f)
opts.binary_location = os.environ["CHROME_BIN"]
d = webdriver.Chrome(service=Service(os.environ["CHROMEDRIVER"]), options=opts)

fails = []
def check(label, cond, got=None):
    print(("  OK   " if cond else "  FAIL ") + label + ("" if cond or got is None else f"  (war: {got})"))
    if not cond: fails.append(label)

def state():
    return d.execute_script("""
      const o = document.getElementById('dryrun-overlay');
      return {open: !!o && !o.classList.contains('hidden'),
              out: document.getElementById('dryrun-output')?.textContent || '',
              status: document.getElementById('dryrun-status')?.className || ''};""")

def wait_for(cond, secs=20):
    for _ in range(secs * 4):
        s = state()
        if cond(s):
            return s
        time.sleep(0.25)
    return state()

try:
    d.get(f"http://127.0.0.1:{PORT}/")
    time.sleep(4)

    print("1. Neuladen während des Dry-Runs")
    d.execute_script("requestDryRun({});")
    time.sleep(1.5)
    d.refresh()
    time.sleep(4)
    s = state()
    check("Fenster nach dem Neuladen wieder offen", s["open"], s)
    s = wait_for(lambda s: "status-ok" in s["status"])
    check("… zeigt das Ergebnis, sobald es da ist", "status-ok" in s["status"], s)
    check("… ohne Hinweis auf Änderung", "geändert" not in s["out"], s["out"])

    print("\n2. Gesehen: kein Fenster mehr")
    d.refresh()
    time.sleep(4)
    check("Fenster bleibt zu", not state()["open"], state())

    print("\n3. Config während des Laufs geändert")
    change["on"] = True
    d.execute_script("""window.__dr = undefined;
      requestDryRun({}).then(r => window.__dr = r);""")
    for _ in range(60):
        if d.execute_script("return window.__dr !== undefined"):
            break
        time.sleep(0.25)
    r = d.execute_script("return window.__dr")
    check("Ergebnis meldet changed_during", bool(r) and r.get("changed_during") is True, r)
    d.execute_script("""
      const o = document.getElementById('dryrun-output');
      document.getElementById('dryrun-overlay').classList.remove('hidden');
      _showDryRunResult(o, window.__dr);""")
    check("Hinweis im Fenster", "während der Prüfung geändert" in state()["out"], state()["out"])
finally:
    d.quit()

print()
if fails:
    print(f"{len(fails)} FEHLER")
    sys.exit(1)
print("Alles grün.")
