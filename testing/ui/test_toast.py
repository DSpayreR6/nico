"""Browser-Test: Meldungen stapeln sich, bleiben je nach Art, schließen per Klick.

VM-Test 2026-11-20 (Martin): der gelbe Hinweis beim ZIP-Export war weg,
bevor man ihn lesen konnte, und mehrere Meldungen lagen übereinander.
showToast ist die eine Meldungsfunktion für ganz NiCo; geprüft wird sie
direkt: Stapel statt Überdeckung, Erfolg 3 s, Warnung 8 s, Fehler 10 s,
Klick schließt, höchstens fünf gleichzeitig.
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

from selenium import webdriver
from selenium.webdriver.chrome.service import Service

PORT = 8825
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

def open_toasts():
    return d.execute_script("return [...document.querySelectorAll('#toast-stack .toast:not([data-closing])')]"
                            ".map(e => e.textContent);")

try:
    d.get(f"http://127.0.0.1:{PORT}/")
    time.sleep(4)
    d.execute_script("document.querySelectorAll('#toast-stack .toast').forEach(e => e.remove());")

    print("1. Stapel statt Überdeckung")
    d.execute_script("showToast('eins','success'); showToast('zwei','warning'); showToast('drei','error');")
    time.sleep(0.5)
    tops = d.execute_script("return [...document.querySelectorAll('#toast-stack .toast')]"
                            ".map(e => Math.round(e.getBoundingClientRect().top));")
    check("drei Meldungen offen", open_toasts() == ["eins", "zwei", "drei"], open_toasts())
    check("untereinander, nicht übereinander", len(set(tops)) == 3, tops)

    print("\n2. Dauer je nach Art")
    time.sleep(3.5)
    check("Erfolg nach ~3 s weg, Warnung und Fehler noch da", open_toasts() == ["zwei", "drei"], open_toasts())
    time.sleep(5)
    check("Warnung nach ~8 s weg, Fehler noch da", open_toasts() == ["drei"], open_toasts())

    print("\n3. Klick schließt")
    d.execute_script("document.querySelector('#toast-stack .toast:not([data-closing])').click();")
    time.sleep(0.5)
    check("Fehler per Klick weg", open_toasts() == [], open_toasts())

    print("\n4. Höchstens fünf")
    d.execute_script("for (let i = 1; i <= 7; i++) showToast('m' + i, 'error');")
    time.sleep(0.5)
    check("fünf offen, die neuesten", open_toasts() == ["m3", "m4", "m5", "m6", "m7"], open_toasts())
finally:
    d.quit()

print()
if fails:
    print(f"{len(fails)} FEHLER")
    sys.exit(1)
print("Alle Prüfungen bestanden.")
