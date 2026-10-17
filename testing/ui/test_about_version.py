"""Browser-Test: Die Version im Info-Dialog kommt vom Server, nicht aus den
Sprachdateien.

Hintergrund 2026-10-17: about.versionVal stand siebenmal in den Sprachdateien
und war bei 0.9.10 stehengeblieben, während flake.nix 0.11.0 sagte und die
Tags v0.10.0 und v0.11.0 längst existierten.
"""
import os, pathlib, sys, threading, time, re, pathlib, json
ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))
from nico.server import create_app
from nico.version import get_version
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.service import Service

PORT = 8794
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

flake = pathlib.Path(str(ROOT) + "/flake.nix").read_text(encoding="utf-8")
expected = re.search(r'^\s*version\s*=\s*"([^"]+)"', flake, re.M).group(1)
print(f"  flake.nix sagt: {expected}")
try:
    check("get_version() stimmt mit flake.nix überein", get_version() == expected, get_version())

    d.get(f"http://127.0.0.1:{PORT}/"); time.sleep(4)
    d.execute_script("document.getElementById('about-btn').click();")
    time.sleep(1)
    shown = d.find_element(By.ID, "about-version").text.strip()
    check(f"Dialog zeigt {expected}", shown == expected, shown)

    for lang in ("en", "ja"):
        d.execute_script("localStorage.setItem('nico_lang', arguments[0]);", lang)
        d.refresh(); time.sleep(3)
        d.execute_script("document.getElementById('about-btn').click();")
        time.sleep(1)
        shown = d.find_element(By.ID, "about-version").text.strip()
        check(f"{lang}: zeigt dieselbe Version", shown == expected, shown)

    for p in sorted(pathlib.Path(str(ROOT) + "/nico/static/lang").glob("*.json")):
        keys = json.loads(p.read_text(encoding="utf-8"))
        check(f"{p.stem}: keine Version mehr in der Sprachdatei",
              not any(k.startswith("about.version") and k != "about.version" for k in keys))

    print("\n" + ("ALLE PRÜFUNGEN BESTANDEN" if not fails else f"FEHLGESCHLAGEN: {fails}"))
finally:
    d.quit()
sys.exit(1 if fails else 0)
