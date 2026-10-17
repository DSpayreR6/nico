"""Browser-Test: Brick-Dialoge arbeiten auf der geöffneten Datei.

Deckt die beiden Fehler von 2026-10-17 ab:
- Verschieben zeigte die Bricks der Wurzel-configuration.nix statt der offenen
  Datei (Host-, Modul- und HM-Dateien hatten keine eigene Liste).
- Löschen über den Knopf listete und löschte immer in der Wurzel-Config,
  unabhängig davon, welche Datei offen war.
"""
import os, pathlib, sys, threading, time
ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))
from nico.server import create_app
from selenium import webdriver
from selenium.webdriver.common.by import By
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
    if not cond:
        fails.append(label)

# Datei -> erwartete Bricks
EXPECT = {
    "configuration.nix":          ["basis-brick"],
    "hosts/testhost/default.nix": ["host-brick-eins", "host-brick-zwei"],
    "home/martin.nix":            ["hm-brick"],
}

def blocks_for(path):
    """Fragt den Endpunkt so ab, wie es die Oberfläche tut."""
    return d.execute_async_script("""
        const done = arguments[arguments.length - 1];
        fetch('/api/brix/blocks?file=' + encodeURIComponent(arguments[0]))
          .then(r => r.json()).then(done).catch(e => done({error: String(e)}));
    """, path)

try:
    d.get(f"http://127.0.0.1:{PORT}/")
    time.sleep(4)
    d.set_script_timeout(20)

    print("\n1. Endpunkt liefert die Bricks der angefragten Datei")
    for path, expected in EXPECT.items():
        res = blocks_for(path)
        got = sorted((res or {}).get("blocks", {}).keys())
        check(f"{path}: {expected}", got == sorted(expected), got)

    print("\n2. Pfadprüfung")
    res = blocks_for("../../../etc/passwd")
    check("Pfad außerhalb wird abgelehnt", bool(res.get("error")), res)
    res = blocks_for("gibtsnicht.nix")
    check("fehlende Datei wird abgelehnt", res.get("error") == "ERR_NOT_FOUND", res)

    print("\n3. Verschieben-Dialog zeigt die Bricks der offenen Datei")
    for path, expected in EXPECT.items():
        ftype = "hm" if path.startswith("home/") else "co"
        d.execute_script("_brixContextFile = arguments[0]; _brixContextFtype = arguments[1];", path, ftype)
        d.execute_script("openBrixMove(arguments[0]);", expected[-1])
        time.sleep(1)
        shown = d.find_element(By.ID, "brix-move-source-name").text
        check(f"{path}: Dialog zeigt '{expected[-1]}'", expected[-1] in shown, shown)
        d.execute_script("document.getElementById('brix-move-overlay').classList.add('hidden');")

    print("\n4. Löschen-Dialog listet die Bricks der offenen Datei")
    for path, expected in EXPECT.items():
        ftype = "hm" if path.startswith("home/") else "co"
        d.execute_script("_brixContextFile = arguments[0]; _brixContextFtype = arguments[1];", path, ftype)
        d.execute_script("openBrixDelete();")
        time.sleep(1)
        sel = d.find_elements(By.CSS_SELECTOR, "#brix-delete-select option")
        got = sorted(o.get_attribute("value") for o in sel)
        check(f"{path}: Auswahl = {expected}", got == sorted(expected), got)
        for btn in d.find_elements(By.ID, "brix-delete-cancel"):
            d.execute_script("arguments[0].click();", btn)
        time.sleep(0.3)

    print("\n5. Echter Weg: Datei im Baum öffnen, dann Brick-Menü")
    for path, expected in EXPECT.items():
        name = path.split("/")[-1]
        d.execute_async_script("""
            const done = arguments[arguments.length - 1];
            Sidebar.selectFile(arguments[0], arguments[1], {force: true}).then(() => done(true));
        """, path, name)
        time.sleep(2)
        ctx = d.execute_script("return [_brixContextFile, _brixTargetFile, _brixContextFtype];")
        check(f"{path}: Oberfläche meldet diese Datei", ctx[0] == path, ctx)
        d.execute_script("openBrixMove();")
        time.sleep(1)
        shown = d.find_element(By.ID, "brix-move-source-name").text
        hit = any(b in shown for b in expected)
        check(f"{path}: Verschieben zeigt einen Brick dieser Datei", hit, shown)
        d.execute_script("document.getElementById('brix-move-overlay').classList.add('hidden');")
    print("\n" + ("ALLE PRÜFUNGEN BESTANDEN" if not fails else f"FEHLGESCHLAGEN: {fails}"))
finally:
    d.quit()
sys.exit(1 if fails else 0)
