"""Browser-Test: eine Diskette für Panel und Raw, roter Punkt und Stern.

Martin, 2026-11-20: Im Panel gab es kein sichtbares Speichern – nur beim
Dateiwechsel, vor Rebuild usw. –, und man sah nicht, ob noch etwas offen
ist. Jetzt ein Knopf für beide Modi; solange etwas ungespeichert ist, trägt
er einen roten Punkt und die Kopfzeile einen Stern vor dem Pfad
(Konvention: nur „ungespeichert“ wird markiert). Strg+S speichert ebenso.

Geprüft:
  1. Kopfzeile zeigt den Pfad relativ zum Config-Verzeichnis
  2. Panel: tippen → Punkt + Stern; Diskette → beide weg, Datei geschrieben
  3. Panel: Strg+S dasselbe
  4. Raw: tippen → Punkt + Stern; Strg+S → weg, Datei geschrieben
  5. Rückfrage beim Verlassen nur mit offenen Änderungen (hasUnsaved)

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

data = config_manager.get_defaults()
data["_flake_mode"] = False
(cfg / "configuration.nix").write_text(generator.generate_configuration_nix(data), encoding="utf-8")
(cfg / "hosts" / "h1").mkdir(parents=True)
(cfg / "hosts" / "h1" / "default.nix").write_text(
    generator.generate_host_nix({"hostname": "h1"}, "h1"), encoding="utf-8")
(cfg / "notiz.nix").write_text("{ ... }: {\n}\n", encoding="utf-8")

from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.common.action_chains import ActionChains

PORT = 8817
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

def state():
    return d.execute_script("""
      const b = document.getElementById('save-tab-btn');
      const s = document.querySelector('#active-file-label .active-file-star');
      return {visible: !b.classList.contains('hidden'),
              dot: b.classList.contains('unsaved'),
              star: !!s && !s.hidden,
              path: document.querySelector('#active-file-label .active-file-path')?.textContent,
              unsaved: Sidebar.hasUnsaved()};""")

def open_file(rel):
    d.execute_script("Sidebar.selectFile(arguments[0], arguments[0].split('/').pop(), {force: true});", rel)
    time.sleep(2.5)

def type_into(el_id, value):
    """Like typing: set the value and fire the input event the form listens to."""
    d.execute_script("""
      const el = document.getElementById(arguments[0]);
      el.value = arguments[1];
      el.dispatchEvent(new Event('input', {bubbles: true}));
      el.dispatchEvent(new Event('change', {bubbles: true}));""", el_id, value)
    time.sleep(0.5)

def ctrl_s():
    ActionChains(d).key_down(Keys.CONTROL).send_keys("s").key_up(Keys.CONTROL).perform()
    time.sleep(2)

try:
    d.get(f"http://127.0.0.1:{PORT}/")
    time.sleep(4)

    print("1. Kopfzeile")
    open_file("hosts/h1/default.nix")
    st = state()
    check("voller relativer Pfad", st["path"] == "hosts/h1/default.nix", st)
    check("Diskette im Panel sichtbar", st["visible"], st)
    check("frisch geöffnet: kein Punkt, kein Stern", not st["dot"] and not st["star"], st)

    print("\n2. Panel: tippen, Diskette")
    open_file("configuration.nix")
    type_into("timezone", "Europe/Lisbon")
    st = state()
    check("nach Tippen: roter Punkt", st["dot"], st)
    check("nach Tippen: Stern", st["star"], st)
    d.execute_script("document.getElementById('save-tab-btn').click();")
    time.sleep(2.5)
    st = state()
    check("nach Diskette: Punkt und Stern weg", not st["dot"] and not st["star"], st)
    check("Datei geschrieben", "Europe/Lisbon" in (cfg / "configuration.nix").read_text(encoding="utf-8"))

    print("\n3. Panel: Strg+S")
    type_into("timezone", "Europe/Oslo")
    check("wieder ungespeichert", state()["dot"])
    ctrl_s()
    st = state()
    check("nach Strg+S: sauber", not st["dot"] and not st["star"], st)
    check("Datei geschrieben", "Europe/Oslo" in (cfg / "configuration.nix").read_text(encoding="utf-8"))

    print("\n4. Raw: tippen, Strg+S")
    open_file("notiz.nix")
    st = state()
    check("Diskette im Raw sichtbar", st["visible"], st)
    ta = d.find_element("id", "raw-file-editor")
    ta.send_keys(Keys.CONTROL, Keys.END)
    ta.send_keys("\n# merken")
    time.sleep(0.5)
    st = state()
    check("Raw: Punkt und Stern", st["dot"] and st["star"], st)
    ctrl_s()
    st = state()
    check("Raw nach Strg+S: sauber", not st["dot"] and not st["star"], st)
    check("Raw-Datei geschrieben", "# merken" in (cfg / "notiz.nix").read_text(encoding="utf-8"))

    print("\n5. Rückfrage beim Verlassen")
    check("ohne Änderungen: keine", d.execute_script("return Sidebar.hasUnsaved();") is False)
    ta = d.find_element("id", "raw-file-editor")
    ta.send_keys("x")
    time.sleep(0.3)
    check("mit Änderungen: ja", d.execute_script("return Sidebar.hasUnsaved();") is True)
    # leave the page clean so the driver can quit without the dialog
    d.execute_script("document.getElementById('raw-file-editor').value = "
                     "document.getElementById('raw-file-editor').value.slice(0, -1);")
finally:
    d.quit()

print()
if fails:
    print(f"{len(fails)} FEHLER")
    sys.exit(1)
print("Alle Prüfungen bestanden.")
