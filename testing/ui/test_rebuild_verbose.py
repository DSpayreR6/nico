"""Browser-Test: Der Schalter „Alle Meldungen von Nix anzeigen" im Rebuild-Monitor.

Anlass (Martin, 2026-11-15): Der Schalter stand zuerst nur im Optionen-Dialog
vor dem Rebuild. Gesucht hat Martin ihn im Monitor – dort läuft der Text
durch, dort will man ihn abstellen.

Geprüft wird die Bedienung, nicht der Rebuild: der Monitor wird ohne Job
geöffnet. Dass der Server die Detailmeldungen eines laufenden Jobs verwirft
und der Schalter ihn umschaltet, prüft
`testing/routes/test_rebuild_verbose.py`.

  1. Der Schalter ist im Monitor sichtbar, mit Text, ohne gespeicherten Wert aus.
  2. Ein Klick merkt die Wahl in den App-Settings; der nächste Klick nimmt
     sie zurück.
  3. Monitor und Optionen-Dialog zeigen denselben Stand.

Braucht ein eigenes XDG_CONFIG_HOME (setzt es nicht selbst), wie
`test_rebuild_options.py`.
"""
import os, pathlib, sys, threading, time

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))
from nico.server import create_app
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.service import Service

PORT = 8812
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

SAVED = "return fetch('/api/app/settings').then(r => r.json()).then(a => a.rebuild_verbose);"
# The thumb is what a user hits; the input itself is hidden by the toggle styling.
CLICK = "document.getElementById('rebuild-verbose-toggle').click();"

try:
    d.get(f"http://127.0.0.1:{PORT}/")
    time.sleep(4)

    print("1. Der Schalter im Monitor")
    d.execute_script("attachRebuildMonitor();")
    time.sleep(1.5)
    check("Monitor ist offen",
          "hidden" not in d.find_element(By.ID, "rebuild-overlay").get_attribute("class"))
    box = d.find_elements(By.ID, "rebuild-verbose-toggle")
    check("Schalter ist da", len(box) == 1)
    row = d.execute_script(
        "const l = document.getElementById('rebuild-verbose-toggle')?.closest('label');"
        "return l ? {text: l.textContent.trim(), shown: l.offsetParent !== null} : null;")
    check("Zeile ist sichtbar", bool(row) and row["shown"], row)
    check("mit übersetztem Text", bool(row) and "Nix" in row["text"] and "__TODO__" not in row["text"]
          and "rebuild.optVerbose" not in row["text"], row)
    check("ohne gespeicherten Wert: aus", bool(box) and not box[0].is_selected())

    print("\n2. Klick merkt die Wahl")
    d.execute_script(CLICK)
    time.sleep(0.8)
    check("Schalter ist an", d.find_element(By.ID, "rebuild-verbose-toggle").is_selected())
    check("in den App-Settings gemerkt: an", d.execute_script(SAVED) is True, d.execute_script(SAVED))

    print("\n3. Der Optionen-Dialog zeigt denselben Stand")
    d.execute_script("document.getElementById('rebuild-overlay').classList.add('hidden');"
                     "_showRebuildOptions({flake_mode: false}, {mode: 'switch'});")
    time.sleep(1)
    dlg = d.find_elements(By.ID, "_rbo-verbose")
    check("Dialog-Schalter ist an", bool(dlg) and dlg[0].is_selected())
    d.execute_script("document.getElementById('_rbo-cancel').click();")
    time.sleep(0.3)

    print("\n4. Erneut geöffnet: gemerkt, und zurücknehmen")
    d.execute_script("attachRebuildMonitor();")
    time.sleep(1.5)
    check("Monitor zeigt: an", d.find_element(By.ID, "rebuild-verbose-toggle").is_selected())
    d.execute_script(CLICK)
    time.sleep(0.8)
    check("Schalter ist aus", not d.find_element(By.ID, "rebuild-verbose-toggle").is_selected())
    check("in den App-Settings gemerkt: aus", d.execute_script(SAVED) is False, d.execute_script(SAVED))
finally:
    d.quit()

print("\n" + ("ALLE PRÜFUNGEN BESTANDEN" if not fails else f"FEHLGESCHLAGEN: {fails}"))
sys.exit(1 if fails else 0)
