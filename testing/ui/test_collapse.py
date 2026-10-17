"""Browser-Test: Auf- und Zuklappen von Sektionen und Nix-Brix."""
import os, pathlib, sys, threading, time
ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))
from nico.server import create_app
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.service import Service

PORT = 8799
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
    if not cond:
        fails.append(label)

try:
    d.get(f"http://127.0.0.1:{PORT}/")
    time.sleep(4)

    brix = lambda: d.find_elements(By.CSS_SELECTOR, ".code-brix[data-brick-name]")
    collapsed = lambda els: [e for e in els if "collapsed" in e.get_attribute("class")]
    sections = lambda: d.find_elements(By.CSS_SELECTOR, ".code-section[data-section]")

    print(f"\nGefunden: {len(brix())} Brix, {len(sections())} Code-Sektionen")
    if not brix():
        print("ABBRUCH: keine Brix gerendert"); sys.exit(2)

    print("\n1. 'Alles aufklappen'")
    d.find_element(By.ID, "expand-all-btn").click(); time.sleep(1)
    check("alle Brix offen", len(collapsed(brix())) == 0)
    check("alle Sektionen offen", len(collapsed(sections())) == 0)

    print("\n2. 'Alles einklappen'")
    d.find_element(By.ID, "collapse-all-btn").click(); time.sleep(1)
    check("alle Sektionen zu", len(collapsed(sections())) == len(sections()))
    check("alle Brix zu (der gemeldete Fehler)", len(collapsed(brix())) == len(brix()))
    check("Zustand gemerkt", len(d.execute_script(
        "return JSON.parse(localStorage.getItem('nico-collapsed-brix') || '[]')")) == len(brix()))

    print("\n3. Klick auf einen einzelnen Brick in zugeklappter Sektion")
    first = brix()[0]
    name = first.get_attribute("data-brick-name")
    d.execute_script("arguments[0].querySelector('.code-brix-head').click()", first)
    time.sleep(1)
    els = {e.get_attribute("data-brick-name"): e for e in brix()}
    sec = els[name].find_element(By.XPATH, "ancestor::div[contains(@class,'code-section')]")
    check(f"Sektion von '{name}' offen", "collapsed" not in sec.get_attribute("class"))
    check(f"'{name}' aufgeklappt", "collapsed" not in els[name].get_attribute("class"))
    others = [n for n in els if n != name]
    check("andere Brix bleiben zu", all("collapsed" in els[n].get_attribute("class") for n in others))
    check("Brick-Körper sichtbar", els[name].find_element(
        By.CSS_SELECTOR, ".code-with-lines.brix-editable").is_displayed())

    print("\n4. Nochmal klicken → derselbe Brick zu")
    d.execute_script("arguments[0].querySelector('.code-brix-head').click()", els[name])
    time.sleep(1)
    els = {e.get_attribute("data-brick-name"): e for e in brix()}
    check(f"'{name}' wieder zu", "collapsed" in els[name].get_attribute("class"))

    print("\n5. Brick einzeln zuklappen, dann seine Sektion – dann Klick auf den Brick")
    d.find_element(By.ID, "expand-all-btn").click(); time.sleep(1)
    els = {e.get_attribute("data-brick-name"): e for e in brix()}
    target = els[name]
    d.execute_script("arguments[0].querySelector('.code-brix-head').click()", target)
    time.sleep(0.5)
    sec = target.find_element(By.XPATH, "ancestor::div[contains(@class,'code-section')]")
    d.execute_script("arguments[0].querySelector('.code-section-head').click()", sec)
    time.sleep(0.5)
    els = {e.get_attribute("data-brick-name"): e for e in brix()}
    check("Ausgangslage: Brick und Sektion zu", "collapsed" in els[name].get_attribute("class"))
    d.execute_script("arguments[0].querySelector('.code-brix-head').click()", els[name])
    time.sleep(1)
    els = {e.get_attribute("data-brick-name"): e for e in brix()}
    sec = els[name].find_element(By.XPATH, "ancestor::div[contains(@class,'code-section')]")
    check("Sektion offen", "collapsed" not in sec.get_attribute("class"))
    check("Brick offen (Fehler C)", "collapsed" not in els[name].get_attribute("class"))
    check("Körper sichtbar (Fehler C)", els[name].find_element(By.CSS_SELECTOR, ".code-with-lines.brix-editable").is_displayed())

    print("\n" + ("ALLE PRÜFUNGEN BESTANDEN" if not fails else f"FEHLGESCHLAGEN: {fails}"))
finally:
    d.quit()
sys.exit(1 if fails else 0)
