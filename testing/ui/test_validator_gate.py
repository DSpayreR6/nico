"""Browser-Test: Der Validator hält Rebuild und Syntax-Prüfung auf.

Hintergrund (2026-10-25, Entscheidung von Martin): Vor dem Bauen läuft der
Validator still mit. Warnungen und Hinweise unterbrechen nichts; bei roten
Funden kommt die Ausgabe hoch und der Knopf zum Erzwingen sitzt **unten**,
hinter allem, was man dafür lesen sollte. Im Menü aufgerufen hat dieselbe
Ausgabe keine Knöpfe – dort gibt es nichts zu erzwingen.

Vorher gab es die Prüfung nur hinterher: ein Rebuild, der den Nutzer aussperrt,
lief durch und der Hinweis auf den Validator erschien erst danach.
"""
import os, pathlib, sys, threading, time

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))
from nico.server import create_app
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.service import Service

PORT = 8796
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


FINDINGS = """[
  {rule_id: 'git_large_log',    severity: 'warning', message: 'Grosses Log',   detail: '', params: [], detail_params: []},
  {rule_id: 'host_orphaned',    severity: 'info',    message: 'Verwaist',      detail: '', params: [], detail_params: []},
  {rule_id: 'user_uid_system',  severity: 'error',   message: 'uid belegt',    detail: '', params: [], detail_params: []},
  {rule_id: 'user_no_password', severity: 'error',   message: 'kein Passwort', detail: '', params: [], detail_params: []}
]"""

try:
    d.get(f"http://127.0.0.1:{PORT}/")
    time.sleep(4)

    print("1. Rote Funde: Ausgabe kommt, Knopf sitzt unten")
    d.execute_script(f"window._gate = null;"
                     f"showValidationGate({FINDINGS}, 'switch')"
                     f"  .then(r => {{ window._gate = r; }});")
    time.sleep(1)
    overlay = d.find_element(By.ID, "validation-results-overlay")
    check("Ausgabe ist offen", "hidden" not in overlay.get_attribute("class"))

    notice = d.execute_script(
        "return document.querySelector('.validate-gate-notice')?.textContent || '';")
    check("Hinweis oben vorhanden", bool(notice.strip()), notice)
    check("Hinweis nennt die Zahl der roten Funde", "2" in notice, notice)
    check("Hinweis sagt, dass unten erzwungen werden kann",
          "unten" in notice.lower(), notice)
    check("Hinweis ist übersetzt", "__TODO__" not in notice and
          "validator.gate" not in notice, notice)

    order = d.execute_script(
        "const b = document.getElementById('validation-results-body');"
        "return [...b.children].map(c => c.className);")
    check("Hinweis ist das erste Element",
          order and "validate-gate-notice" in order[0], order)
    check("Knöpfe sind das letzte Element",
          order and "validate-gate-actions" in order[-1], order)

    print("\n2. Rot steht vor Gelb und Blau")
    icons = d.execute_script(
        "return [...document.querySelectorAll('.git-guard-card')]"
        "  .map(c => c.querySelector('span')?.style.color || '');")
    reds = [i for i, c in enumerate(icons) if "red" in c]
    check("vier Karten", len(icons) == 4, icons)
    check("beide roten zuerst", reds == [0, 1], reds)

    print("\n3. Abbrechen bricht ab")
    d.find_element(By.ID, "validation-gate-cancel").click()
    time.sleep(0.6)
    check("Ausgabe ist zu",
          "hidden" in d.find_element(By.ID, "validation-results-overlay")
                       .get_attribute("class"))
    check("liefert false", d.execute_script("return window._gate;") is False,
          d.execute_script("return window._gate;"))

    print("\n4. Erzwingen liefert true")
    d.execute_script(f"window._gate = null;"
                     f"showValidationGate({FINDINGS}, 'switch')"
                     f"  .then(r => {{ window._gate = r; }});")
    time.sleep(1)
    force = d.find_element(By.ID, "validation-gate-force")
    check("Knopf trägt den Namen der Funktion",
          "bau" in force.text.lower() or "build" in force.text.lower(), force.text)
    force.click()
    time.sleep(0.6)
    check("liefert true", d.execute_script("return window._gate;") is True,
          d.execute_script("return window._gate;"))

    print("\n5. Syntax-Prüfung bekommt ihren eigenen Knopfnamen")
    d.execute_script(f"showValidationGate({FINDINGS}, 'dryrun');")
    time.sleep(1)
    dry = d.find_element(By.ID, "validation-gate-force").text
    check("nicht der Rebuild-Text", "system" not in dry.lower(), dry)
    check("übersetzt", "__TODO__" not in dry and "validator.gate" not in dry, dry)
    d.find_element(By.ID, "validation-gate-cancel").click()
    time.sleep(0.5)

    print("\n6. Im Menü aufgerufen: keine Knöpfe")
    d.execute_script(f"_showValidationResults({FINDINGS});")
    time.sleep(0.8)
    check("kein Hinweis oben",
          d.execute_script("return !document.querySelector('.validate-gate-notice');"))
    check("kein Erzwingen-Knopf",
          d.execute_script("return !document.getElementById('validation-gate-force');"))
    check("kein Abbrechen-Knopf",
          d.execute_script("return !document.getElementById('validation-gate-cancel');"))
    d.find_element(By.ID, "validation-results-close").click()
    time.sleep(0.5)

    print("\n7. Warnungen allein halten nichts auf")
    d.execute_script("""
      window._origFetch = window.csrfFetch;
      window.csrfFetch = async () => ({
        ok: true,
        json: async () => ({ findings: [
          {rule_id: 'git_large_log', severity: 'warning', message: 'Grosses Log',
           detail: '', params: [], detail_params: []},
          {rule_id: 'host_orphaned', severity: 'info', message: 'Verwaist',
           detail: '', params: [], detail_params: []}
        ]}),
      });
      window._pass = null;
      _validationGate(['nix-desktop'], 'switch').then(r => { window._pass = r; });
    """)
    time.sleep(1)
    check("lässt durch", d.execute_script("return window._pass;") is True,
          d.execute_script("return window._pass;"))
    check("ohne Ausgabe zu öffnen",
          "hidden" in d.find_element(By.ID, "validation-results-overlay")
                       .get_attribute("class"))

    print("\n8. Ein roter Fund hält auf")
    d.execute_script("""
      window.csrfFetch = async () => ({
        ok: true,
        json: async () => ({ findings: [
          {rule_id: 'user_no_password', severity: 'error', message: 'kein Passwort',
           detail: '', params: [], detail_params: []}
        ]}),
      });
      window._pass = null;
      _validationGate(['nix-desktop'], 'switch').then(r => { window._pass = r; });
    """)
    time.sleep(1)
    check("Ausgabe ist offen",
          "hidden" not in d.find_element(By.ID, "validation-results-overlay")
                           .get_attribute("class"))
    check("wartet noch auf die Antwort",
          d.execute_script("return window._pass;") is None)
    d.find_element(By.ID, "validation-gate-cancel").click()
    time.sleep(0.6)
    check("Abbrechen stoppt den Bau",
          d.execute_script("return window._pass;") is False)

    print("\n9. Derselbe Fund aus mehreren Hosts erscheint einmal")
    d.execute_script("""
      window.csrfFetch = async () => ({
        ok: true,
        json: async () => ({ findings: [
          {rule_id: 'state_version_per_host', severity: 'error',
           message: 'ein Wert fuer alle', detail: '', params: ['configuration.nix'],
           detail_params: []}
        ]}),
      });
      window._pass = null;
      _validationGate(['nix-desktop', 'nix-laptop', 'nix-vm'], 'dryrun')
        .then(r => { window._pass = r; });
    """)
    time.sleep(1.2)
    cards = d.execute_script(
        "return document.querySelectorAll('.git-guard-card').length;")
    check("eine Karte statt drei", cards == 1, cards)
    d.find_element(By.ID, "validation-gate-cancel").click()
    time.sleep(0.5)
    d.execute_script("window.csrfFetch = window._origFetch;")

finally:
    d.quit()

print("\n" + ("ALLE PRÜFUNGEN BESTANDEN" if not fails else f"FEHLGESCHLAGEN: {len(fails)}"))
sys.exit(1 if fails else 0)
