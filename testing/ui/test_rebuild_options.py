"""Browser-Test: Der Optionen-Dialog vor dem Rebuild erscheint auch ohne Flakes.

Gemeldet aus der VM (2026-10-24): Bei einer Nicht-Flake-Config sprang NiCo
direkt in den Rebuild. Damit fehlten auch "im Terminal starten", "Befehl
kopieren" und der Schonmodus – alles Dinge, die nichts mit Flakes zu tun haben.
Ursache war ein früher Rücksprung in `_showRebuildOptions`, sobald
`flake_mode` falsch war.

Entschieden (Martin): Dialog immer zeigen, nur das Flake-Update ausgrauen statt
verstecken – so sieht man die Möglichkeit, ohne davon gestört zu werden.

Der Test klickt den Rebuild **nicht** an: er prüft den Dialog und bricht dann
ab. Ein scharfer Rebuild würde in das laufende System eingreifen
(hinweisliste.txt: "Nie eine Route scharf aufrufen, die ins System eingreift").
"""
import json, os, pathlib, sys, threading, time

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))
from nico.server import create_app
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.service import Service

PORT = 8795
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

try:
    d.get(f"http://127.0.0.1:{PORT}/")
    time.sleep(4)

    print("1. Dialog erscheint ohne Flakes")
    # Direkt die Dialogfunktion aufrufen: der Weg über das Menü würde den
    # Fremddatei-Guard und das Speichern mitnehmen, die hier nichts prüfen.
    d.execute_script("window._rboResult = null;"
                     "_showRebuildOptions({flake_mode: false}, {mode: 'switch'})"
                     "  .then(r => { window._rboResult = r; });")
    time.sleep(1)
    box = d.find_elements(By.CSS_SELECTOR, ".dialog-box")
    check("Dialog ist offen", len(box) > 0)

    print("\n2. Alles Flake-unabhängige ist bedienbar")
    for element_id, label in (("_rbo-safe-mode", "Schonmodus"),
                              ("_rbo-terminal", "im Terminal starten"),
                              ("_rbo-copy", "Befehl kopieren"),
                              ("_rbo-ok", "Starten"),
                              ("_rbo-cancel", "Abbrechen")):
        els = d.find_elements(By.ID, element_id)
        enabled = bool(els) and els[0].is_enabled()
        check(f"{label} vorhanden und aktiv", enabled, f"gefunden={bool(els)}")

    print("\n3. Flake-Update ist ausgegraut, nicht versteckt")
    flake = d.find_elements(By.ID, "_rbo-flake-update")
    check("Schalter ist da", len(flake) > 0)
    check("aber deaktiviert", bool(flake) and not flake[0].is_enabled())
    check("und nicht angehakt", bool(flake) and not flake[0].is_selected())
    row_classes = d.execute_script(
        "return document.getElementById('_rbo-flake-update')?.closest('label')?.className || '';")
    check("Zeile ist als inaktiv markiert", "row-inactive" in row_classes, row_classes)
    hint = d.execute_script(
        "const n = document.getElementById('_rbo-flake-update')?.closest('label')?.nextElementSibling;"
        "return n ? n.textContent.trim() : '';")
    check("Hinweis erklärt warum", bool(hint) and "__TODO__" not in hint, hint)

    print("\n4. Der angezeigte Befehl passt zum Nicht-Flake-Weg")
    cmd = d.execute_script("return document.getElementById('_rbo-cmd')?.textContent || '';")
    check("nennt nixos-rebuild switch", "nixos-rebuild switch" in cmd, cmd)
    check("kein --flake", "--flake" not in cmd, cmd)
    check("nennt -I nixos-config", "-I nixos-config=" in cmd, cmd)

    print("\n5. Schonmodus wirkt auf den Befehl")
    d.execute_script("document.getElementById('_rbo-safe-mode').click();")
    time.sleep(0.3)
    cmd2 = d.execute_script("return document.getElementById('_rbo-cmd')?.textContent || '';")
    check("--max-jobs 1 erscheint", "--max-jobs 1" in cmd2, cmd2)

    print("\n6. Abbrechen startet nichts")
    d.execute_script("document.getElementById('_rbo-cancel').click();")
    time.sleep(0.5)
    check("Dialog geschlossen", not d.find_elements(By.ID, "_rbo-ok"))
    check("Ergebnis ist null (abgebrochen)",
          d.execute_script("return window._rboResult;") is None)

    print("\n7. Mit Flakes ist der Schalter bedienbar")
    d.execute_script("_showRebuildOptions({flake_mode: true}, {mode: 'switch', hostname: 'vm'});")
    time.sleep(1)
    flake2 = d.find_elements(By.ID, "_rbo-flake-update")
    check("Schalter aktiv", bool(flake2) and flake2[0].is_enabled())
    cmd3 = d.execute_script("return document.getElementById('_rbo-cmd')?.textContent || '';")
    check("Befehl nennt --flake", "--flake" in cmd3, cmd3)
    d.execute_script("document.getElementById('_rbo-cancel').click();")

    print("\n8. Hinweistext in allen sieben Sprachen")
    for p in sorted((ROOT / "nico" / "static" / "lang").glob("*.json")):
        val = json.loads(p.read_text(encoding="utf-8")).get("rebuild.optFlakeUpdateNoFlake")
        check(f"{p.stem}", bool(val) and val != "__TODO__", val)
finally:
    d.quit()

print("\n" + ("ALLE PRÜFUNGEN BESTANDEN" if not fails else f"FEHLGESCHLAGEN: {fails}"))
sys.exit(1 if fails else 0)
