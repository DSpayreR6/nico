"""Browser-Test: Hinweiszeile und Platzhalter am Feld System-State-Version.

Hintergrund (Martins Frage am 2026-11-01): Das Formular hat ein Feld fuer
`system.stateVersion`, und nichts darin sagt, dass der Wert bei einem Flake
mit mehreren Hosts in die Host-Datei gehoert. Die naheliegende Annahme ist
dazu noch falsch - ein Host-Wert gewinnt nicht gegen den der Basis-Config,
Nix lehnt eine zweimal definierte Option ab und bricht die Auswertung ab.

Geprueft wird, was sich nur im Browser zeigt:

  * bei mehreren Hosts erscheint die Hinweiszeile, und der Platzhalter sagt
    "leer - steht in den Host-Dateien" statt YY.MM
  * die Zeile nennt den Host mit eigenem Wert und die ohne
  * sie warnt davor, dass zwei Definitionen die Auswertung abbrechen
  * mit geoeffneter Host-Datei nennt die Zeile genau diesen Host, und der
    Platzhalter ist wieder der normale - dort ist das Feld der eigene Wert
  * kein __TODO__ in der Oberflaeche

Aufruf wie in testing/ui/README.md beschrieben.
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
def check(label, cond):
    try:
        ok = bool(cond() if callable(cond) else cond)
        note = ""
    except Exception as exc:
        ok, note = False, f"  ({type(exc).__name__}: {exc})"
    print(("  OK   " if ok else "  FAIL ") + label + note)
    if not ok:
        fails.append(label)

def expand_syst():
    """Den Abschnitt System aufklappen.

    Sektionen starten zugeklappt (Entscheidung 2026-10-17), und Selenium gibt
    fuer ein Element in einem zugeklappten Abschnitt einen leeren .text
    zurueck - ohne das Aufklappen prueft der Test nichts.
    """
    d.execute_script(
        "document.querySelector('section[data-section=\"SYST\"] .sec-toggle')?.click()")
    time.sleep(0.5)


def syst_collapsed():
    sec = d.find_element(By.CSS_SELECTOR, 'section[data-section="SYST"]')
    return "collapsed" in (sec.get_attribute("class") or "")


def hint_text():
    el = d.find_element(By.ID, "state-version-hint")
    if "hidden" in (el.get_attribute("class") or ""):
        return ""
    # textContent statt .text: der Inhalt zaehlt, nicht ob gerade gescrollt ist.
    return (d.execute_script("return arguments[0].textContent", el) or "").strip()

def placeholder():
    return d.find_element(By.ID, "state_version").get_attribute("placeholder")

try:
    d.get(f"http://127.0.0.1:{PORT}/")
    time.sleep(5)

    print("\n0. Ausgangslage")
    check("der Abschnitt System startet zugeklappt", syst_collapsed)
    expand_syst()
    check("nach dem Klick ist er offen", lambda: not syst_collapsed())
    check("die Hinweiszeile ist dann auch wirklich sichtbar",
          lambda: d.find_element(By.ID, "state-version-hint").is_displayed())

    print("\n1. Basis-Config bei mehreren Hosts")
    check("die Hinweiszeile ist sichtbar", lambda: bool(hint_text()))
    check("sie sagt, dass der Wert in die Host-Dateien gehoert",
          lambda: "Host-Dateien" in hint_text())
    check("der Host mit eigenem Wert ist genannt",
          lambda: "nix-desktop 25.11" in hint_text())
    check("die Hosts ohne Wert sind genannt",
          lambda: "nix-laptop" in hint_text() and "nix-vm" in hint_text())
    # Das war Martins eigentliche Frage - genau diese Aussage muss dastehen.
    check("sie warnt vor der doppelten Definition",
          lambda: "lehnt Nix die Auswertung ab" in hint_text())
    check("sie widerspricht der Annahme 'Host gewinnt'",
          lambda: "gewinnt nicht" in hint_text())
    check("der Platzhalter ist nicht mehr YY.MM", lambda: placeholder() != "YY.MM")
    check("der Platzhalter verweist auf die Host-Dateien",
          lambda: "Host-Dateien" in placeholder())
    check("das Feld selbst ist leer",
          lambda: d.find_element(By.ID, "state_version").get_attribute("value") == "")

    print("\n2. Mit geoeffneter Host-Datei")
    # Ueber den Dateibaum, den Weg des Nutzers. Nicht ueber switchHost():
    # die Funktion hat keinen Aufrufer und laeuft in einen ReferenceError,
    # weil _formDirty im Closure von Sidebar liegt und nicht global ist
    # (gefunden 2026-11-01 beim Bau dieses Tests, eigener Befund).
    d.execute_script(
        "Sidebar.selectFile('hosts/nix-desktop/default.nix',"
        " 'default.nix')")
    time.sleep(4)
    expand_syst()
    check("die Zeile nennt genau diesen Host",
          lambda: "nix-desktop" in hint_text() and "nix-laptop" not in hint_text())
    check("der Platzhalter ist wieder der normale",
          lambda: placeholder() == "YY.MM")
    check("das Feld zeigt den Wert des Hosts",
          lambda: d.find_element(By.ID, "state_version").get_attribute("value") == "25.11")

    print("\n3. Zurueck zur Basis-Config")
    d.execute_script("Sidebar.selectFile('configuration.nix', 'configuration.nix')")
    time.sleep(4)
    expand_syst()
    check("die lange Zeile ist wieder da",
          lambda: "nix-laptop" in hint_text() and "nix-vm" in hint_text())
    check("und der Platzhalter wieder der Hinweis",
          lambda: placeholder() != "YY.MM")

    print("\n4. Keine Platzhalter-Reste")
    check("kein __TODO__ in der Hinweiszeile", lambda: "__TODO__" not in hint_text())
    check("kein __TODO__ im Platzhalter", lambda: "__TODO__" not in placeholder())
    check("kein roher Schluessel in der Zeile",
          lambda: "field.stateVersion" not in hint_text())
finally:
    d.quit()

print()
if fails:
    print("FEHLGESCHLAGEN:", len(fails))
    for f in fails:
        print("  -", f)
    sys.exit(1)
print("Alle Pruefungen bestanden.")
