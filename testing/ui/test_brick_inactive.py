"""Browser-Test: Nix-Brick im Punkte-Menue deaktivieren und wieder aktivieren.

Aus Martins Wunsch vom 2026-11-01: Ein gelöschter Brick ist weg, obwohl man
ihn später wieder braucht. Statt Löschen wird der Körper an Ort und Stelle
auskommentiert - der Inhalt bleibt in der `.nix`, Nix sieht ihn nie.

Geprueft wird, was sich nur im Browser zeigt:

  * der Menueintrag heisst "Deaktivieren" und wechselt danach zu "Aktivieren"
  * die Brick-Zeile wird ausgegraut (Klasse brix-inactive) und traegt den
    Zusatz "deaktiviert"
  * der Brick bleibt in der Liste - genau das ist der Unterschied zum
    Loeschen, und wuerde der Marker nicht erkannt, waere er verschwunden
  * der Inhalt steht weiter in der Vorschau, nur auskommentiert
  * nach dem Aktivieren ist alles wie vorher
  * kein __TODO__ und kein roher Schluessel in den neuen Texten
  * ein geoeffnetes Menue haelt seine Aktionsleiste selbst sichtbar, statt
    sie an :hover zu haengen. Gemeldet von Martin am 2026-11-01: bei
    eingeklappter Sektion verschwand das Menue, sobald man mit der Maus zum
    ersten Eintrag fuhr.

Aufruf wie in testing/ui/README.md beschrieben.
"""
import os, pathlib, sys, threading, time
ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))
from nico.server import create_app
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.service import Service

PORT = 8792
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
    try:
        ok = bool(cond() if callable(cond) else cond)
        note = ""
    except Exception as exc:
        ok, note = False, f"  ({type(exc).__name__}: {exc})"
    print(("  OK   " if ok else "  FAIL ") + label + note)
    if not ok:
        fails.append(label)


def brick_wrap(name):
    return d.find_element(By.CSS_SELECTOR, f'.code-brix[data-brick-name="{name}"]')


def menu_labels(name):
    """Die Beschriftungen im Punkte-Menue dieses Bricks."""
    wrap = brick_wrap(name)
    d.execute_script("arguments[0].querySelector('.brix-menu-btn').click()", wrap)
    time.sleep(0.3)
    items = wrap.find_elements(By.CSS_SELECTOR, '.brix-menu-item')
    return [d.execute_script("return arguments[0].textContent", i).strip() for i in items]


def click_menu(name, label):
    wrap = brick_wrap(name)
    for item in wrap.find_elements(By.CSS_SELECTOR, '.brix-menu-item'):
        if d.execute_script("return arguments[0].textContent", item).strip() == label:
            d.execute_script("arguments[0].click()", item)
            time.sleep(2.5)
            return True
    return False


try:
    d.get(f"http://127.0.0.1:{PORT}/")
    time.sleep(5)

    # Die Wegwerf-Config aus der README traegt in configuration.nix den Brick
    # "basis-brick". Ohne ihn prueft der Test nichts, also erst feststellen.
    NAME = "basis-brick"
    print("\n1. Ausgangslage")
    check("der Brick ist da", lambda: bool(brick_wrap(NAME)))
    check("er ist nicht ausgegraut",
          lambda: "brix-inactive" not in brick_wrap(NAME).get_attribute("class"))

    labels = menu_labels(NAME)
    check("das Menue hat fuenf Eintraege", lambda: len(labels) == 5)
    check("darunter 'Deaktivieren'", lambda: "Deaktivieren" in labels)
    check("und nicht 'Aktivieren'", lambda: "Aktivieren" not in labels)
    check("kein __TODO__ im Menue", lambda: not any("__TODO__" in l for l in labels))
    check("kein roher Schluessel im Menue",
          lambda: not any(l.startswith("brix.") for l in labels))

    inhalt_vorher = d.execute_script(
        "return arguments[0].textContent", brick_wrap(NAME))

    print("\n2. Deaktivieren")
    check("Menueintrag laesst sich klicken", lambda: click_menu(NAME, "Deaktivieren"))
    check("der Brick ist weiterhin in der Liste - nicht geloescht",
          lambda: bool(brick_wrap(NAME)))
    check("die Zeile ist ausgegraut",
          lambda: "brix-inactive" in brick_wrap(NAME).get_attribute("class"))
    check("die Kopfzeile sagt 'deaktiviert'",
          lambda: "deaktiviert" in d.execute_script(
              "return arguments[0].textContent",
              brick_wrap(NAME).find_element(By.CSS_SELECTOR, '.code-brix-label')))
    check("der Marker traegt die Flagge",
          lambda: "inactive>" in d.execute_script(
              "return arguments[0].textContent", brick_wrap(NAME)))
    check("der Inhalt steht noch da, nur auskommentiert",
          lambda: "# " in d.execute_script(
              "return arguments[0].textContent", brick_wrap(NAME)))

    labels = menu_labels(NAME)
    check("das Menue bietet jetzt 'Aktivieren'", lambda: "Aktivieren" in labels)
    check("und nicht mehr 'Deaktivieren'", lambda: "Deaktivieren" not in labels)

    print("\n3. Wieder aktivieren")
    check("Menueintrag laesst sich klicken", lambda: click_menu(NAME, "Aktivieren"))
    check("nicht mehr ausgegraut",
          lambda: "brix-inactive" not in brick_wrap(NAME).get_attribute("class"))
    check("kein 'deaktiviert' mehr in der Kopfzeile",
          lambda: "deaktiviert" not in d.execute_script(
              "return arguments[0].textContent",
              brick_wrap(NAME).find_element(By.CSS_SELECTOR, '.code-brix-label')))
    check("der Marker ist wieder ohne Flagge",
          lambda: "inactive>" not in d.execute_script(
              "return arguments[0].textContent", brick_wrap(NAME)))
    check("der Inhalt ist wieder wie am Anfang",
          lambda: d.execute_script("return arguments[0].textContent",
                                   brick_wrap(NAME)) == inhalt_vorher)
    print("\n4. Das Menue haengt nicht mehr am Mauszeiger")
    # Der gemeldete Fehler (Martin, 2026-11-01): .brix-actions wird nur ueber
    # .code-brix:hover eingeblendet, und das Menue klappt mit top:100% darunter
    # auf. Bei eingeklappter Sektion ist der Brick eine Zeile hoch - wer mit
    # der Maus zum ersten Eintrag faehrt, verlaesst den Brick, :hover greift
    # nicht mehr, das Menue verschwindet, und der Kopf des naechsten Bricks
    # bekommt den Hover.
    #
    # Geprueft wird der Vertrag des Fixes, nicht die Mausbahn: Ein geoeffnetes
    # Menue haelt seine Aktionsleiste selbst sichtbar. Der Klick kommt hier
    # aus JavaScript, der Zeiger hat den Brick also nie beruehrt - :hover ist
    # garantiert nicht im Spiel. Vor dem Fix steht die Leiste damit auf
    # display:none und das Menue ist unerreichbar. (Eine echte Mausbahn mit
    # ActionChains scheitert daran, dass Selenium die Lage eines Elements mit
    # display:none nicht bestimmen kann - "element not interactable".)
    d.refresh()
    time.sleep(5)

    sec = d.find_element(By.CSS_SELECTOR, 'section[data-section="SERV"]')
    check("die Sektion startet zugeklappt",
          lambda: "collapsed" in (sec.get_attribute("class") or ""))

    wrap = brick_wrap(NAME)
    disp = lambda sel: d.execute_script(
        "return getComputedStyle(arguments[0]).display",
        wrap.find_element(By.CSS_SELECTOR, sel))

    check("ohne Hover und ohne Menue ist die Leiste verborgen",
          lambda: disp('.brix-actions') == 'none')

    d.execute_script("arguments[0].querySelector('.brix-menu-btn').click()", wrap)
    time.sleep(0.4)
    check("die Klasse brix-menu-open sitzt am Brick",
          lambda: "brix-menu-open" in brick_wrap(NAME).get_attribute("class"))
    check("das Menue ist offen",
          lambda: "hidden" not in wrap.find_element(
              By.CSS_SELECTOR, '.brix-context-menu').get_attribute("class"))
    # Das ist die Prüfung, die vor dem Fix umfällt.
    check("die Aktionsleiste bleibt sichtbar, obwohl der Zeiger nie da war",
          lambda: disp('.brix-actions') == 'flex')
    check("und die Menüeinträge sind damit erreichbar",
          lambda: all(e.is_displayed()
                      for e in wrap.find_elements(By.CSS_SELECTOR, '.brix-menu-item')))

    # Ein Klick woanders schliesst das Menue und gibt die Leiste wieder frei.
    d.execute_script("document.body.click()")
    time.sleep(0.4)
    check("nach dem Schliessen ist die Klasse weg",
          lambda: "brix-menu-open" not in brick_wrap(NAME).get_attribute("class"))
    check("und die Leiste haengt wieder am Hover",
          lambda: disp('.brix-actions') == 'none')

finally:
    d.quit()

print()
if fails:
    print("FEHLGESCHLAGEN:", len(fails))
    for f in fails:
        print("  -", f)
    sys.exit(1)
print("Alle Pruefungen bestanden.")
