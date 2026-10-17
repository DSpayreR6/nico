"""Browser-Test: Sprachwechsel – keine __TODO__ sichtbar, CSS-Hinweis übersetzt."""
import os, pathlib, sys, threading, time
ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))
from nico.server import create_app
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.service import Service

PORT = 8798
app = create_app()
threading.Thread(target=lambda: app.run(port=PORT, threaded=True), daemon=True).start()
time.sleep(2)
opts = webdriver.ChromeOptions()
for f in ("--headless=new", "--no-sandbox", "--disable-dev-shm-usage", "--window-size=1600,1200"):
    opts.add_argument(f)
opts.binary_location = os.environ["CHROME_BIN"]
d = webdriver.Chrome(service=Service(os.environ["CHROMEDRIVER"]), options=opts)

fails = []
def check(label, cond):
    print(("  OK   " if cond else "  FAIL ") + label)
    if not cond: fails.append(label)

EXPECT = {"de": "eingeklappt", "en": "collapsed", "es": "plegado",
          "fr": "replié", "ja": "折りたたみ中", "ru": "свёрнуто", "zh": "已折叠"}
try:
    d.get(f"http://127.0.0.1:{PORT}/"); time.sleep(4)
    for lang, word in EXPECT.items():
        d.execute_script("localStorage.setItem('nico_lang', arguments[0]);", lang)
        d.refresh(); time.sleep(3)
        var = d.execute_script(
            "return getComputedStyle(document.documentElement).getPropertyValue('--ni-collapsed-label');")
        check(f"{lang}: CSS-Hinweis = {var.strip()}", word in var)
        body = d.find_element(By.TAG_NAME, "body").text
        check(f"{lang}: kein __TODO__ in der Oberfläche", "__TODO__" not in body)
    print("\n" + ("ALLE PRÜFUNGEN BESTANDEN" if not fails else f"FEHLGESCHLAGEN: {fails}"))
finally:
    d.quit()
sys.exit(1 if fails else 0)
