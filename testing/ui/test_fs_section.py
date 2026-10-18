"""Browser-Test: Abschnitt "Wartung & Snapshots" und das Zahnrad als Umschalter.

Prüft die Teile, die sich nur im echten Browser zeigen:

  * das Zahnrad öffnet den Admin-Bereich und schließt ihn auch wieder
  * ein offener Unterdialog hat Vorrang – das Zahnrad zieht den Admin-Bereich
    nicht darunter weg
  * die /tmp-Größe erscheint nur bei aktivem tmpfs
  * "/tmp beim Booten leeren" wird bei tmpfs ausgegraut, behält aber seinen
    Wert (Ausschalten von tmpfs bringt die alte Einstellung zurück)

Aufruf wie in testing/ui/README.md beschrieben.
"""
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
    """cond darf ein Wert oder eine Funktion sein – fehlt ein Element, gilt die
    einzelne Prüfung als fehlgeschlagen, statt den ganzen Lauf abzubrechen."""
    try:
        ok = bool(cond() if callable(cond) else cond)
        note = ""
    except Exception as exc:
        ok, note = False, f"  ({type(exc).__name__})"
    print(("  OK   " if ok else "  FAIL ") + label + note)
    if not ok:
        fails.append(label)

def hidden(element_id):
    el = d.find_element(By.ID, element_id)
    return "hidden" in (el.get_attribute("class") or "")

def jsclick(element_id):
    d.execute_script("document.getElementById(arguments[0])?.click()", element_id)
    time.sleep(0.4)

try:
    d.get(f"http://127.0.0.1:{PORT}/")
    time.sleep(4)

    print("\n1. Gruppen im Abschnitt")
    sect = lambda: d.find_element(By.CSS_SELECTOR, 'section[data-section="FSYS"]')
    check("zwei Gruppen-Überschriften",
          lambda: len(sect().find_elements(By.CSS_SELECTOR, ".field-rubrik")) == 2)
    for ident in ("smartd", "tmp_use_tmpfs", "tmp_tmpfs_size", "tmp_clean_on_boot"):
        check(f"Feld {ident} vorhanden", lambda i=ident: bool(sect().find_elements(By.ID, i)))

    print("\n2. /tmp-Optionen")
    check("Größe zunächst verborgen", lambda: hidden("tmp-tmpfs-options"))
    jsclick("tmp_clean_on_boot")
    check("'/tmp leeren' ist an", lambda: d.find_element(By.ID, "tmp_clean_on_boot").is_selected())
    jsclick("tmp_use_tmpfs")
    check("Größe sichtbar bei tmpfs", lambda: not hidden("tmp-tmpfs-options"))
    check("Standardgröße 50%",
          lambda: d.find_element(By.ID, "tmp_tmpfs_size").get_attribute("value") == "50%")
    check("'/tmp leeren' ausgegraut",
          lambda: d.find_element(By.ID, "tmp_clean_on_boot").get_attribute("disabled") is not None)
    check("Wert bleibt erhalten", lambda: d.find_element(By.ID, "tmp_clean_on_boot").is_selected())
    check("Hinweis sichtbar", lambda: not hidden("tmp-clean-tmpfs-hint"))
    jsclick("tmp_use_tmpfs")
    check("nach tmpfs-aus wieder bedienbar",
          lambda: d.find_element(By.ID, "tmp_clean_on_boot").get_attribute("disabled") is None)
    check("alte Einstellung zurück", lambda: d.find_element(By.ID, "tmp_clean_on_boot").is_selected())
    check("Größe wieder verborgen", lambda: hidden("tmp-tmpfs-options"))
    jsclick("tmp_clean_on_boot")

    print("\n3. Zahnrad als Umschalter")
    check("Admin-Bereich zu", lambda: hidden("admin-overlay"))
    jsclick("admin-btn")
    time.sleep(1)
    check("Zahnrad öffnet", lambda: not hidden("admin-overlay"))
    jsclick("admin-btn")
    check("Zahnrad schließt", lambda: hidden("admin-overlay"))

    print("\n4. Unterdialog hat Vorrang")
    jsclick("admin-btn")
    time.sleep(1)
    jsclick("validation-settings-btn")
    time.sleep(0.6)
    check("Unterdialog offen", lambda: not hidden("validation-settings-overlay"))
    jsclick("admin-btn")
    check("Admin-Bereich bleibt offen", lambda: not hidden("admin-overlay"))
    jsclick("validation-settings-close")
    time.sleep(0.4)
    jsclick("admin-btn")
    check("danach schließt das Zahnrad wieder", lambda: hidden("admin-overlay"))

finally:
    d.quit()

print("\n" + ("ALLE PRÜFUNGEN BESTANDEN" if not fails else f"FEHLGESCHLAGEN: {len(fails)}"))
sys.exit(1 if fails else 0)
