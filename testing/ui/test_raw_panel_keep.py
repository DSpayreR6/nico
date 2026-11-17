"""Browser-Test: was im Raw-Modus außerhalb eines Nix-Bricks steht, überlebt das Panel.

Befund 2026-11-16 (Claude Opus 5.5, manifest „Befund aus Schritt 0“): Im
Raw-Modus eine Einstellung außerhalb eines Bricks eingetragen, gespeichert,
auf Panel umgeschaltet, im Panel gespeichert – die Einstellung war weg. Bei
`configuration.nix`, weil Raw-Speichern den Hash neu setzt; bei Host-Dateien,
weil es dort gar keine Umwandlung gab.

Dieser Test fährt den Weg des Nutzers in der Oberfläche: Datei öffnen,
Panel-Schalter aus, im Raw-Editor eintragen, Raw speichern, Panel-Schalter an,
im Formular speichern. Danach muss die Einstellung als Nix-Brick in der Datei
stehen, genau einmal. Das Gegenstück ohne Browser, mit allen vier
Schreibwegen und dem Baustein selbst, ist
`testing/routes/test_keep_unmodelled.py`.

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
from nico.brix import extract_brick_blocks
from nico.server import create_app

data = config_manager.get_defaults()
data["_flake_mode"] = False
(cfg / "configuration.nix").write_text(generator.generate_configuration_nix(data), encoding="utf-8")
(cfg / "hosts" / "h1").mkdir(parents=True)
(cfg / "hosts" / "h1" / "default.nix").write_text(
    generator.generate_host_nix({"hostname": "h1"}, "h1"), encoding="utf-8")

from selenium import webdriver
from selenium.webdriver.chrome.service import Service

PORT = 8814
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

def mode():
    return d.execute_script("return document.getElementById('panel-toggle-btn')?.dataset.mode || 'p';")

def toggle():
    d.execute_script("document.getElementById('panel-toggle-check').click();")
    time.sleep(1.5)

def user_path(rel, stmt, name):
    print(f"\n{name}")
    d.execute_script("Sidebar.selectFile(arguments[0], arguments[0].split('/').pop(), {force: true});", rel)
    time.sleep(2)
    toggle()
    check("Raw-Modus an", mode() == "r", mode())
    ok = d.execute_script("""
      const ta = document.getElementById('raw-file-editor');
      if (!ta) return false;
      const v = ta.value, i = v.trimEnd().lastIndexOf('}');
      ta.value = v.slice(0, i) + arguments[0] + v.slice(i);
      ta.dispatchEvent(new Event('input'));
      document.getElementById('raw-save-tab-btn').click();
      return true;""", stmt)
    check("im Raw-Editor eingetragen und gespeichert", ok)
    time.sleep(1.5)
    key = stmt.strip().split(" =")[0]
    check("steht nach Raw-Speichern in der Datei", key in (cfg / rel).read_text())
    toggle()
    check("Panel-Modus an", mode() == "p", mode())
    saved = d.execute_script("return saveConfig();")
    time.sleep(1.5)
    check("im Formular gespeichert", saved is True, saved)
    text = (cfg / rel).read_text()
    check("Einstellung überlebt", key in text)
    check("… als Nix-Brick", any(key in b["text"] for b in extract_brick_blocks(text).values()))
    check("… genau einmal", text.count(key) == 1, text.count(key))

def panel_to_raw():
    """2026-11-17: Panel → raw saves the panel first; a background save
    afterwards leaves the raw file alone, and raw save still works."""
    print("\n3. Panel ändern → Raw: erst speichern, dann umschalten")
    rel = "configuration.nix"
    d.execute_script("Sidebar.selectFile(arguments[0], arguments[0], {force: true});", rel)
    time.sleep(2)
    check("Panel-Modus", mode() == "p", mode())
    d.execute_script("""
      const el = document.getElementById('hostname');
      el.value = 'umschaltwert';
      el.dispatchEvent(new Event('input', {bubbles: true}));""")
    toggle()
    check("Raw-Modus an", mode() == "r", mode())
    text = (cfg / rel).read_text()
    check("Panel-Änderung steht in der Datei", "umschaltwert" in text)
    check("… und #r", "#r#" in text.splitlines()[1] if len(text.splitlines()) > 1 else False)
    d.execute_async_script("const cb = arguments[0]; _autoSave().then(cb);")
    time.sleep(1)
    check("stilles Speichern lässt #r stehen", "#r#" in (cfg / rel).read_text())
    ok = d.execute_script("""
      const ta = document.getElementById('raw-file-editor');
      if (!ta) return false;
      const v = ta.value, i = v.trimEnd().lastIndexOf('}');
      ta.value = v.slice(0, i) + '  services.nachumschalt.enable = true;\\n' + v.slice(i);
      document.getElementById('raw-save-tab-btn').click();
      return true;""")
    time.sleep(1.5)
    check("Raw speichern danach geht", ok and "services.nachumschalt.enable" in (cfg / rel).read_text())

try:
    d.get(f"http://127.0.0.1:{PORT}/")
    time.sleep(4)
    user_path("configuration.nix", "  services.uiraw.enable = true;\n", "1. configuration.nix")
    user_path("hosts/h1/default.nix", "  services.uihost.enable = true;\n", "2. Host-Datei")
    panel_to_raw()
finally:
    d.quit()

print()
print("ALLE PRÜFUNGEN BESTANDEN" if not fails else f"FEHLGESCHLAGEN: {len(fails)}")
sys.exit(0 if not fails else 1)
