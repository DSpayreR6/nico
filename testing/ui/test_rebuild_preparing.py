"""Browser-Test: Der Rebuild reagiert sofort und läuft nicht zweimal an.

Gemeldet vom Laptop (2026-11-08): Nach dem Klick kam lange nichts, ein zweiter
Klick startete einen zweiten Durchlauf. `openRebuild` arbeitete Speichern,
Schreiben und Validator ohne Anzeige ab, und die Sperre gegen einen zweiten
Rebuild saß erst an `/api/rebuild/start`.

Die Schritte vor und nach dem Fenster werden ersetzt. Der Test startet keinen
Rebuild und schreibt nichts (hinweisliste.txt: "Nie eine Route scharf aufrufen,
die ins System eingreift"), und er läuft auf eigenen Temp-Verzeichnissen.
"""
import json, os, pathlib, sys, tempfile, threading, time

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

xdg = pathlib.Path(tempfile.mkdtemp(prefix="nico-xdg-"))
os.environ["XDG_CONFIG_HOME"] = str(xdg)
cfg = pathlib.Path(tempfile.mkdtemp(prefix="nico-cfg-"))
(cfg / "configuration.nix").write_text("{ config, pkgs, ... }:\n{\n}\n", encoding="utf-8")
(cfg / "config.json").write_text(json.dumps({"hosts_dir": "hosts"}), encoding="utf-8")
(xdg / "nico").mkdir(parents=True)
(xdg / "nico" / "settings.json").write_text(
    json.dumps({"nixos_config_dir": str(cfg), "language": "de", "git_sync": False}), encoding="utf-8")

from nico.server import create_app
from selenium import webdriver
from selenium.webdriver.chrome.service import Service

PORT = 8797
app = create_app()
threading.Thread(target=lambda: app.run(port=PORT, threaded=True), daemon=True).start()
time.sleep(2)
opts = webdriver.ChromeOptions()
for f in ("--headless=new", "--no-sandbox", "--disable-dev-shm-usage", "--window-size=1600,1200"):
    opts.add_argument(f)
opts.binary_location = os.environ["CHROME_BIN"]
d = webdriver.Chrome(service=Service(os.environ["CHROMEDRIVER"]), options=opts)

fails = []
def check(label, cond, got=None):
    print(("  OK   " if cond else "  FAIL ") + label + ("" if cond or got is None else f"  (war: {got})"))
    if not cond: fails.append(label)


STUBS = """
window._n = {guard: 0, options: 0, monitor: 0};
window._resolveOptions = null;
window._guardDelay = 300;
window._guardThrows = false;
checkForeignFilesBeforeCommit = async () => {
  window._n.guard++;
  await new Promise(r => setTimeout(r, window._guardDelay));
  if (window._guardThrows) throw new Error('guard');
  return true;
};
_fetchFlakeHosts = async () => ({flake_mode: false, hosts: []});
_showRebuildOptions = () => {
  window._n.options++;
  return new Promise(r => { window._resolveOptions = r; });
};
attachRebuildMonitor = () => { window._n.monitor++; };
"""

COUNT = "return document.querySelectorAll('#rebuild-preparing').length;"
ACTIVE = "return _rebuildFlowActive;"

try:
    d.get(f"http://127.0.0.1:{PORT}/")
    time.sleep(4)
    d.execute_script(STUBS)

    print("1. Sofort sichtbar")
    n = d.execute_script("window._p = openRebuild('switch'); " + COUNT)
    check("Fenster steht noch im selben Schritt wie der Klick", n == 1, n)
    text = d.execute_script("return document.getElementById('_rpr-status').textContent;")
    check("Text ist übersetzt", "vorbereitet" in text and "{0}" not in text and "__TODO__" not in text, text)

    print("\n2. Zweiter Klick")
    d.execute_script("openRebuild('switch');")
    check("kein zweites Fenster", d.execute_script(COUNT) == 1)
    toast = d.execute_script("return [...document.querySelectorAll('.toast')].map(e => e.textContent).join('|');")
    check("Hinweis erscheint", "bereits vorbereitet" in toast, toast)
    time.sleep(0.8)
    check("Vorprüfung lief nur einmal", d.execute_script("return window._n.guard;") == 1)
    check("Optionen nur einmal", d.execute_script("return window._n.options;") == 1)

    print("\n3. Dialog liegt nicht unter dem Fenster")
    hidden = d.execute_script("return document.getElementById('rebuild-preparing').classList.contains('hidden');")
    check("während der Optionen ausgeblendet", hidden is True, hidden)

    print("\n4. Abbruch löst alles wieder")
    d.execute_script("window._resolveOptions(null);")
    time.sleep(0.3)
    check("Fenster weg", d.execute_script(COUNT) == 0)
    check("Sperre gelöst", d.execute_script(ACTIVE) is False)

    print("\n5. Fehler löst alles wieder")
    d.execute_script("window._guardThrows = true; window._guardDelay = 10; openRebuild('switch').catch(() => {});")
    time.sleep(0.4)
    check("Fenster weg", d.execute_script(COUNT) == 0)
    check("Sperre gelöst", d.execute_script(ACTIVE) is False)
    d.execute_script("window._guardThrows = false;")

    print("\n6. Es läuft schon ein Rebuild")
    d.execute_script("""
      window._n.guard = 0;
      window._realFetch = window.fetch;
      window.fetch = (url, o) => String(url).includes('/api/rebuild/status')
        ? Promise.resolve(new Response(JSON.stringify({job: {running: true}})))
        : window._realFetch(url, o);
      openRebuild('switch');
    """)
    time.sleep(0.4)
    check("Monitor öffnet sich", d.execute_script("return window._n.monitor;") == 1)
    check("keine weiteren Schritte", d.execute_script("return window._n.guard;") == 0)
    check("Fenster weg", d.execute_script(COUNT) == 0)
    check("Sperre gelöst", d.execute_script(ACTIVE) is False)
finally:
    d.quit()

print()
if fails:
    print(f"{len(fails)} FEHLER:", *fails, sep="\n  ")
    sys.exit(1)
print("Alle Prüfungen bestanden.")
os._exit(0)
