"""Browser-Test: Datei außerhalb geändert, während das Formular offen ist.

Entscheidung 2026-11-17 (Martin): Statt die alten Formularwerte über die
neuere Datei zu schreiben, fragt NiCo – „Neu laden“, „Unterschiede
ansehen“, „Überschreiben“. Dieser Test fährt beide Wege in der Oberfläche,
einmal über das Speichern im Formular, einmal über das stille Speichern
(_autoSave), das Rebuild, Validator und Dateiwechsel vorschalten.
Das Gegenstück ohne Browser ist `testing/routes/test_outside_change.py`.

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

from nico import config_manager, core, generator
from nico.server import create_app

data = config_manager.get_defaults()
data["_flake_mode"] = False
data["hostname"] = "alt"
(cfg / "configuration.nix").write_text(generator.generate_configuration_nix(data), encoding="utf-8")

from selenium import webdriver
from selenium.webdriver.chrome.service import Service

PORT = 8815
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

CO = cfg / "configuration.nix"

def change_outside(old, new):
    text = CO.read_text().replace(old, new)
    CO.write_text(core.set_type_in_content(text, core.get_nico_type(text)))

def edit_form(field, value):
    d.execute_script("""
      const el = document.getElementById(arguments[0]);
      el.value = arguments[1];
      el.dispatchEvent(new Event('input', {bubbles: true}));""", field, value)

def dialog_open():
    return d.execute_script(
        "return !document.getElementById('outside-overlay').classList.contains('hidden');")

def start(js):
    """Start a save without waiting for it – it waits for the dialog."""
    d.execute_script(f"window.__res = undefined; ({js}).then(r => window.__res = r);")
    time.sleep(1.5)

def result():
    time.sleep(1.5)
    return d.execute_script("return window.__res;")

try:
    d.get(f"http://127.0.0.1:{PORT}/")
    time.sleep(4)
    d.execute_script("document.querySelectorAll('.overlay').forEach(o => o.classList.add('hidden'))")

    print("1. Speichern im Formular → Unterschiede → Neu laden")
    edit_form("timezone", "Europe/Berlin")
    change_outside('"alt"', '"vonaussen"')
    outside_state = CO.read_text()
    start("saveConfig()")
    check("Dialog erscheint", dialog_open())
    check("nennt die Datei", d.execute_script(
        "return document.getElementById('outside-file').textContent;") == "configuration.nix")
    d.execute_script("document.getElementById('outside-diff-btn').click();")
    time.sleep(1)
    diff = d.execute_script("return document.getElementById('outside-diff').innerText;")
    check("Unterschiede zeigen alt und neu", "alt" in diff and "vonaussen" in diff, diff[:200])
    check("Dialog bleibt dabei offen", dialog_open())
    d.execute_script("document.getElementById('outside-reload-btn').click();")
    res = result()
    check("Speichern meldet kein Ergebnis", res is False, res)
    check("Dialog zu", not dialog_open())
    check("Datei unverändert", CO.read_text() == outside_state)
    check("Formular zeigt den Stand der Datei", d.execute_script(
        "return document.getElementById('hostname').value;") == "vonaussen")

    print("\n2. Stilles Speichern → Überschreiben")
    edit_form("hostname", "ausdempanel")
    change_outside('"vonaussen"', '"zweiter"')
    start("_autoSave()")
    check("Dialog erscheint", dialog_open())
    d.execute_script("document.getElementById('outside-overwrite-btn').click();")
    res = result()
    check("stilles Speichern meldet Erfolg", res is True, res)
    check("Panel-Wert steht in der Datei", '"ausdempanel"' in CO.read_text())

    print("\n3. Danach ohne Rückfrage")
    edit_form("hostname", "nochmal")
    start("_autoSave()")
    check("kein Dialog", not dialog_open())
    check("gespeichert", result() is True and '"nochmal"' in CO.read_text())
finally:
    d.quit()

print()
print("ALLE PRÜFUNGEN BESTANDEN" if not fails else f"FEHLGESCHLAGEN: {len(fails)}")
sys.exit(0 if not fails else 1)
