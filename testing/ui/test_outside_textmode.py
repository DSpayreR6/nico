"""Browser-Test: Textmodus, Datei außerhalb geändert (Revision 2026-11-25, F3).

Die Datei ist im Textmodus offen, ein anderes Programm (hier: der Test,
wie Kate) speichert sie. Beim Speichern in NiCo erscheint dieselbe Frage
wie im Panel, aber mit „Abbrechen“ statt „Neu laden“ (Entscheidung Martin
2026-11-26): Abbrechen lässt die Platte und den eigenen Text im Editor,
Überschreiben schreibt den eigenen Text. Das Gegenstück ohne Browser ist
`testing/routes/test_rev1125_paket1.py`.

Die Wegwerf-Config und XDG_CONFIG_HOME legt der Test selbst an.
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

from nico import config_manager, generator
from nico.server import create_app
data = config_manager.get_defaults(); data["_flake_mode"] = False
(cfg / "configuration.nix").write_text(generator.generate_configuration_nix(data), encoding="utf-8")
(cfg / "modules").mkdir()
MOD = cfg / "modules" / "m.nix"
MOD.write_text("{ ... }:\n{\n  # erste Fassung\n}\n", encoding="utf-8")

from selenium import webdriver
from selenium.webdriver.chrome.service import Service

PORT = 8841
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

def type_text(text):
    d.execute_script("""
      const ta = document.getElementById('raw-file-editor');
      ta.value = arguments[0];
      ta.dispatchEvent(new Event('input', {bubbles: true}));""", text)

def dialog():
    return d.execute_script("""
      const o = document.getElementById('outside-overlay');
      const vis = id => !document.getElementById(id).classList.contains('hidden');
      return {open: !o.classList.contains('hidden'), cancel: vis('outside-cancel-btn'),
              reload: vis('outside-reload-btn'),
              info: document.getElementById('outside-info').textContent};""")

def start_save():
    d.execute_script("window.__res = undefined; Sidebar.saveActive().then(r => window.__res = r);")
    time.sleep(1.5)

try:
    d.get(f"http://127.0.0.1:{PORT}/")
    time.sleep(4)
    d.execute_script("Sidebar.selectFile('modules/m.nix', 'm.nix', {force: true});")
    time.sleep(3)
    # The file-type question for a file without a NiCo line is not the subject.
    d.execute_script("document.querySelectorAll('.overlay').forEach(e => e.classList.add('hidden'))")
    check("Textmodus-Editor offen", d.execute_script("return !!document.getElementById('raw-file-editor')"))

    print("1. Außen geändert, Abbrechen")
    type_text("{ ... }:\n{\n  # aus NiCo\n}\n")
    MOD.write_text("{ ... }:\n{\n  # aus Kate\n}\n", encoding="utf-8")
    start_save()
    st = dialog()
    check("Frage erscheint", st["open"], st)
    check("… mit Abbrechen statt Neu laden", st["cancel"] and not st["reload"], st)
    check("… Text für den Textmodus", "Textmodus" in st["info"], st["info"])
    d.execute_script("document.getElementById('outside-cancel-btn').click()")
    time.sleep(1.5)
    check("Fenster zu", not dialog()["open"])
    check("Platte: Kate-Fassung", "aus Kate" in MOD.read_text())
    check("Editor: eigener Text",
          "aus NiCo" in d.execute_script("return document.getElementById('raw-file-editor').value"))

    print("\n2. Überschreiben")
    start_save()
    check("Frage erscheint erneut", dialog()["open"])
    d.execute_script("document.getElementById('outside-overwrite-btn').click()")
    time.sleep(2)
    check("Platte: eigener Text", "aus NiCo" in MOD.read_text(), MOD.read_text())

    print("\n3. Danach ohne fremde Änderung: keine Frage")
    type_text("{ ... }:\n{\n  # aus NiCo, zweimal\n}\n")
    start_save()
    check("keine Frage", not dialog()["open"])
    time.sleep(1)
    check("gespeichert", "zweimal" in MOD.read_text(), MOD.read_text())

    print("\n4. Panel-Dialog unverändert")
    d.execute_script("askChangedOutside('x.nix').then(() => {});")
    time.sleep(0.5)
    st = dialog()
    check("Panel: Neu laden statt Abbrechen", st["reload"] and not st["cancel"], st)
    check("Panel: Text für das Panel", "Panel" in st["info"], st["info"])
    d.execute_script("document.getElementById('outside-reload-btn').click()")
finally:
    d.quit()

print()
if fails:
    print(f"{len(fails)} FEHLER")
    sys.exit(1)
print("Alles grün.")
