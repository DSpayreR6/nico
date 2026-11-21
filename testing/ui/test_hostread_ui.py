"""Browser-Test: Freigaben zum Lesen dieses Rechners (hostread.py, 2026-11-21).

Martin: „lesen außerhalb ist nicht zulässig“ – drei Schalter, Standard aus,
mit Freigabe-Knopf dort, wo die Funktion es braucht. Geprüft wird die
Oberfläche:
  1. Einstellungen: Rubrik mit drei Schaltern, alle aus.
  2. Validator-Hinweis host_read_off trägt den Knopf „Freigeben“; der Klick
     speichert die Freigabe, ersetzt den Knopf und setzt den Schalter.
  3. Import-Dialog: kein automatischer Blick nach /etc/nixos, stattdessen
     „In /etc/nixos nachsehen“; Importieren erst danach.
  4. Keine JavaScript-Fehler.

Start wie die anderen Browser-Tests (testing/ui/README.md), Profil standard.
"""
import json, os, pathlib, sys, threading, time
ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))
from nico.server import create_app
from selenium import webdriver
from selenium.webdriver.chrome.service import Service

PORT = 8831
app = create_app()
threading.Thread(target=lambda: app.run(port=PORT, threaded=True), daemon=True).start()
time.sleep(2)
opts = webdriver.ChromeOptions()
for f in ("--headless=new", "--no-sandbox", "--disable-dev-shm-usage", "--window-size=1600,1200"):
    opts.add_argument(f)
opts.set_capability("goog:loggingPrefs", {"browser": "SEVERE"})
opts.binary_location = os.environ["CHROME_BIN"]
d = webdriver.Chrome(service=Service(os.environ["CHROMEDRIVER"]), options=opts)

fails = []
def check(label, cond, got=None):
    print(("  OK   " if cond else "  FAIL ") + label + ("" if cond or got is None else f"  (war: {got})"))
    if not cond: fails.append(label)


def app_settings():
    return d.execute_async_script(
        "const cb = arguments[0]; fetch('/api/app/settings').then(r => r.json()).then(cb);")


try:
    d.get(f"http://127.0.0.1:{PORT}/"); time.sleep(4)

    print("1. Einstellungen")
    d.execute_script("_loadAdminSettings();"); time.sleep(1)
    state = d.execute_script("""
      return ['read_host_validator', 'read_host_hardware', 'read_host_generations'].map(k => {
        const cb = document.querySelector(`input[data-host-read-setting="${k}"]`);
        return cb ? cb.checked : null;
      });""")
    check("drei Schalter vorhanden und aus", state == [False, False, False], state)
    s = app_settings()
    check("Server: alle drei aus",
          [s.get(k) for k in ("read_host_validator", "read_host_hardware", "read_host_generations")]
          == [False, False, False], s)

    print("2. Freigabe aus dem Validator-Hinweis")
    d.execute_script("""
      _showValidationResults([{rule_id: 'host_read_off', severity: 'info',
        message: 'x', message_key: 'validator.f.host_read_off_validator', params: ['3'],
        detail: '', detail_key: 'validator.d.host_read_off', detail_params: []}]);""")
    time.sleep(0.5)
    btn = d.execute_script("return document.querySelector('[data-host-read=\"read_host_validator\"]');")
    check("Knopf 'Freigeben' am Hinweis", btn is not None)
    if btn:
        d.execute_script("arguments[0].click();", btn); time.sleep(1)
        s = app_settings()
        check("Freigabe gespeichert", s.get("read_host_validator") is True, s)
        check("Knopf durch Bestätigung ersetzt",
              d.execute_script("return !document.querySelector('[data-host-read=\"read_host_validator\"]');"))
        check("Schalter in den Einstellungen mitgezogen",
              d.execute_script("return document.getElementById('setting-read-validator').checked;"))
    d.execute_script("closeValidationResults();")

    print("3. Import-Dialog")
    d.execute_script("performance.clearResourceTimings(); showImportOverlay(false);"); time.sleep(1)
    probed = d.execute_script(
        "return performance.getEntriesByType('resource').some(e => e.name.includes('/api/import/check'));")
    check("kein automatischer Blick nach /etc/nixos", probed is False, probed)
    look_visible = d.execute_script(
        "return !document.getElementById('import-look-btn').classList.contains('hidden');")
    apply_hidden = d.execute_script(
        "return document.getElementById('import-apply-btn').classList.contains('hidden');")
    check("'In /etc/nixos nachsehen' sichtbar, Importieren verborgen", look_visible and apply_hidden,
          (look_visible, apply_hidden))
    d.execute_script("document.getElementById('import-look-btn').click();"); time.sleep(1.5)
    probed = d.execute_script(
        "return performance.getEntriesByType('resource').some(e => e.name.includes('/api/import/check'));")
    check("nach dem Klick wird nachgesehen", probed is True, probed)
    d.execute_script("document.getElementById('import-overlay').classList.add('hidden');")

    print("4. Keine JavaScript-Fehler")
    errs = [e["message"] for e in d.get_log("browser") if e.get("level") == "SEVERE"
            and "favicon" not in e["message"]]
    check("Konsole ohne Fehler", not errs, errs[:3])

    print("\n" + ("ALLE PRÜFUNGEN BESTANDEN" if not fails else f"FEHLGESCHLAGEN: {fails}"))
finally:
    d.quit()
sys.exit(1 if fails else 0)
