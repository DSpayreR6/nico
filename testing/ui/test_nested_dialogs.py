"""Browser-Test: die drei Dialoge zu verschachtelten Nix-Brix.

Offen seit 2026-09-16: Backend, Schreibwege und HTTP-Endpunkte waren getestet,
die Dialoge hatte nie jemand im Browser gesehen. Geprüft werden:

  refused  – ein Schreibvorgang würde eine Verschachtelung neu einführen:
             Dialog erscheint, auf der Platte ändert sich nichts
  existing – die Verschachtelung lag schon in der Datei: Dialog meldet sie,
             der Schreibvorgang läuft durch, und sie wird pro Seitenaufruf
             nur einmal gemeldet
  confirm  – Raw-Editor: Nachfrage mit zwei Schaltflächen. "Zurück zum Text"
             schreibt nichts, "Trotzdem speichern" schreibt
"""
import os, pathlib, sys, threading, time, pathlib
ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))
from nico.server import create_app
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.service import Service

PORT = 8793
CFG = pathlib.Path(os.environ["XDG_CONFIG_HOME"]).parent / "cfg"
app = create_app()
threading.Thread(target=lambda: app.run(port=PORT, threaded=True), daemon=True).start()
time.sleep(2)

opts = webdriver.ChromeOptions()
for f in ("--headless=new", "--no-sandbox", "--disable-dev-shm-usage", "--window-size=1600,1200"):
    opts.add_argument(f)
opts.binary_location = os.environ["CHROME_BIN"]
d = webdriver.Chrome(service=Service(os.environ["CHROMEDRIVER"]), options=opts)
d.set_script_timeout(30)

fails = []
def check(label, cond, got=None):
    print(("  OK   " if cond else "  FAIL ") + label + ("" if cond or got is None else f"  (war: {got})"))
    if not cond: fails.append(label)

def dialog():
    """Sichtbarer Verschachtelungs-Dialog oder None."""
    for ov in d.find_elements(By.CSS_SELECTOR, "body > .overlay"):
        if ov.is_displayed() and ov.find_elements(By.CSS_SELECTOR, "#_nested-ok, #_nested-save"):
            return ov
    return None

def click(sel):
    el = d.find_element(By.CSS_SELECTOR, sel)
    d.execute_script("arguments[0].click();", el)
    time.sleep(0.6)

try:
    d.get(f"http://127.0.0.1:{PORT}/"); time.sleep(4)

    # ── 1. refused ───────────────────────────────────────────────────────────
    print("\n1. Neu eingeführte Verschachtelung wird abgelehnt")
    before = (CFG / "configuration.nix").read_text(encoding="utf-8")
    # Brick-Körper so ändern, dass er die Marker eines zweiten Bricks enthält
    d.execute_async_script("""
        const done = arguments[arguments.length - 1];
        _brixContextFile = 'configuration.nix'; _brixContextFtype = 'co';
        saveBrixInline('aussen',
          'services.openssh.enable = true;\\n# <brick: Services / #9 eingeschmuggelt>\\nservices.x.enable = true;\\n# </brick: eingeschmuggelt>',
          'configuration.nix').then(() => done(1)).catch(() => done(0));
    """)
    time.sleep(1.5)
    ov = dialog()
    check("Dialog erscheint", ov is not None)
    if ov:
        txt = ov.text
        check("Titel lautet 'Nicht gespeichert'", "Nicht gespeichert" in txt, txt.split("\n")[0])
        check("nennt den inneren Brick", "eingeschmuggelt" in txt)
        check("nennt den äußeren Brick", "aussen" in txt)
        check("nur eine Schaltfläche (OK)",
              len(ov.find_elements(By.CSS_SELECTOR, "button")) == 1)
        click("#_nested-ok")
    check("Dialog ist zu", dialog() is None)
    after = (CFG / "configuration.nix").read_text(encoding="utf-8")
    check("auf der Platte hat sich nichts geändert", before == after)

    # ── 2. existing ──────────────────────────────────────────────────────────
    print("\n2. Bestehende Verschachtelung wird gemeldet, blockiert aber nicht")
    res = d.execute_async_script("""
        const done = arguments[arguments.length - 1];
        const csrf = document.querySelector('meta[name=csrf-token]').content;
        fetch('/api/brick/rename', {method:'POST',
          headers:{'Content-Type':'application/json','X-CSRF-Token':csrf},
          body: JSON.stringify({old_name: 'huelle', new_name: 'huelle2', file: 'bestand.nix'})
        }).then(r=>r.json()).then(j => { _handleNestedResponse(j); done(j); });
    """)
    time.sleep(1.5)
    check("Umbenennen lief durch", res.get("success") is True, res)
    ov = dialog()
    check("Dialog erscheint", ov is not None)
    if ov:
        check("Titel lautet 'Verschachtelte Nix-Brix'", "Verschachtelte Nix-Brix" in ov.text)
        check("nennt den inneren Brick", "drinnen" in ov.text)
        click("#_nested-ok")
    txt = (CFG / "bestand.nix").read_text(encoding="utf-8")
    check("Umbenennung steht auf der Platte", "huelle2" in txt)
    check("innerer Brick ist unangetastet", "# <brick: Services / #2 drinnen>" in txt)

    print("\n   erneut – darf kein zweites Mal aufpoppen")
    d.execute_async_script("""
        const done = arguments[arguments.length - 1];
        const csrf = document.querySelector('meta[name=csrf-token]').content;
        fetch('/api/brick/rename', {method:'POST',
          headers:{'Content-Type':'application/json','X-CSRF-Token':csrf},
          body: JSON.stringify({old_name: 'huelle2', new_name: 'huelle3', file: 'bestand.nix'})
        }).then(r=>r.json()).then(j => { _handleNestedResponse(j); done(j); });
    """)
    time.sleep(1.5)
    check("kein zweiter Dialog", dialog() is None)

    # ── 3. confirm ───────────────────────────────────────────────────────────
    print("\n3. Raw-Editor fragt nach")
    nested_text = ("# nico-version: nd#0\n{\n"
                   "  # <brick: Services / #1 rawaussen>\n  a = 1;\n"
                   "  # <brick: Services / #2 rawinnen>\n  b = 2;\n  # </brick: rawinnen>\n"
                   "  # </brick: rawaussen>\n}\n")
    (CFG / "raw.nix").write_text("# nico-version: nd#0\n{ }\n", encoding="utf-8")

    def raw_save(force):
        return d.execute_async_script("""
            const done = arguments[arguments.length - 1];
            const csrf = document.querySelector('meta[name=csrf-token]').content;
            fetch('/api/file/' + encodeURIComponent('raw.nix'), {method:'POST',
              headers:{'Content-Type':'application/json','X-CSRF-Token':csrf},
              body: JSON.stringify({content: arguments[0], force: arguments[1]})
            }).then(r=>r.json()).then(done);
        """, nested_text, force)

    data = raw_save(False)
    check("Server lehnt erst einmal ab", data.get("error") == "ERR_BRICK_NESTED", data)
    d.execute_script("window._nestedAnswer = _showNestedBricksModal(arguments[0], 'confirm');",
                     data.get("nested"))
    time.sleep(1)
    ov = dialog()
    check("Dialog erscheint", ov is not None)
    if ov:
        check("zwei Schaltflächen", len(ov.find_elements(By.CSS_SELECTOR, "button")) == 2)
        check("'Trotzdem speichern' vorhanden", bool(ov.find_elements(By.ID, "_nested-save")))
        check("'Zurück zum Text' vorhanden", bool(ov.find_elements(By.ID, "_nested-back")))
        click("#_nested-back")
    answer = d.execute_async_script(
        "const done = arguments[arguments.length-1]; window._nestedAnswer.then(done);")
    check("'Zurück zum Text' liefert false", answer is False, answer)
    check("nichts geschrieben", "rawinnen" not in (CFG / "raw.nix").read_text(encoding="utf-8"))

    print("\n   'Trotzdem speichern'")
    d.execute_script("window._nestedAnswer = _showNestedBricksModal(arguments[0], 'confirm');",
                     data.get("nested"))
    time.sleep(1)
    click("#_nested-save")
    answer = d.execute_async_script(
        "const done = arguments[arguments.length-1]; window._nestedAnswer.then(done);")
    check("liefert true", answer is True, answer)
    data = raw_save(True)
    check("mit force wird geschrieben", data.get("success") is True, data)
    check("Inhalt steht auf der Platte", "rawinnen" in (CFG / "raw.nix").read_text(encoding="utf-8"))

    print("\n" + ("ALLE PRÜFUNGEN BESTANDEN" if not fails else f"FEHLGESCHLAGEN: {fails}"))
finally:
    d.quit()
sys.exit(1 if fails else 0)
