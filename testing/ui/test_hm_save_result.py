"""Browser-Test: ein abgelehntes Home-Manager-Speichern bleibt sichtbar ungespeichert.

Codex, 2026-11-20: Der HM-Speichervorgang lieferte false, die Diskette
meldete trotzdem „Gespeichert“, und nach dem automatischen Speichern war der
rote Punkt weg – die Änderung sah gespeichert aus. Geprüft (der Server wird
mit einer abgelehnten Antwort simuliert):
  1. Autospeichern abgelehnt → Punkt bleibt
  2. Diskette → meldet Misserfolg (false), Punkt bleibt
  3. Dateiwechsel hält an
  4. Server nimmt wieder an → Diskette speichert, Punkt weg
"""
import json, os, pathlib, sys, tempfile, threading, time
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

from nico import config_manager, generator, hm_generator
from nico.server import create_app
data = config_manager.get_defaults(); data["_flake_mode"] = False
(cfg / "configuration.nix").write_text(generator.generate_configuration_nix(data), encoding="utf-8")
(cfg / "home").mkdir()
(cfg / "home" / "martin.nix").write_text(
    hm_generator.generate_home_nix({"username": "martin", "home_dir": "/home/martin"}), encoding="utf-8")

from selenium import webdriver
from selenium.webdriver.chrome.service import Service

PORT = 8828
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

def dot():
    return d.execute_script("return document.getElementById('save-tab-btn').classList.contains('unsaved');")

try:
    d.get(f"http://127.0.0.1:{PORT}/")
    time.sleep(4)
    d.execute_script("document.querySelectorAll('.overlay').forEach(e => e.classList.add('hidden'))")
    d.execute_script("Sidebar.selectFile('home/martin.nix', 'martin.nix', {force: true});")
    time.sleep(3)
    check("HM-Panel offen", d.execute_script(
        "return !document.getElementById('panel-hm').classList.contains('hidden');"))

    # The server refuses every HM save from now on.
    d.execute_script("""
      window._realFetch = csrfFetch;
      csrfFetch = (url, o) => url.startsWith('/api/hm/patch')
        ? Promise.resolve(new Response(JSON.stringify({success: false, error: 'ERR_FILE_WRITE'}),
                                       {headers: {'Content-Type': 'application/json'}}))
        : window._realFetch(url, o);""")

    print("1. Autospeichern abgelehnt")
    d.execute_script("""
      const el = document.getElementById('hm-homeDir');
      el.value = '/home/martin2';
      el.dispatchEvent(new Event('input', {bubbles: true}));""")
    time.sleep(2)
    check("roter Punkt bleibt nach dem abgelehnten Speichern", dot())
    check("hasUnsaved", d.execute_script("return Sidebar.hasUnsaved();") is True)

    print("\n2. Diskette")
    res = d.execute_async_script("const cb = arguments[0]; Sidebar.saveActive().then(cb);")
    check("saveActive meldet Misserfolg", res is False, res)
    check("Punkt bleibt", dot())

    print("\n3. Dateiwechsel")
    d.execute_async_script("const cb = arguments[0]; Sidebar.selectFile('configuration.nix', 'configuration.nix').then(cb);")
    time.sleep(1)
    path = d.execute_script("return document.querySelector('#active-file-label .active-file-path')?.textContent;")
    check("Wechsel hält an, HM-Datei bleibt offen", path == "home/martin.nix", path)

    print("\n4. Server nimmt wieder an")
    d.execute_script("csrfFetch = window._realFetch;")
    res = d.execute_async_script("const cb = arguments[0]; Sidebar.saveActive().then(cb);")
    time.sleep(0.5)
    check("saveActive gelingt", res is True, res)
    check("Punkt weg", not dot())
    check("Datei geschrieben", "/home/martin2" in (cfg / "home" / "martin.nix").read_text(encoding="utf-8"))
finally:
    d.quit()

print()
if fails:
    print(f"{len(fails)} FEHLER")
    sys.exit(1)
print("Alle Prüfungen bestanden.")
