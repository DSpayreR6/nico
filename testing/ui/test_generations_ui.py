"""Browser-Test: Zeitmaschine, Seite System (Generationen), 2026-11-21.

  1. Unterreiter System | Config wie in den Einstellungen: System steht
     vorn und ist beim Öffnen aktiv (Martin, 2026-11-21).
  2. Ohne Freigabe: Hinweis mit Knopf „Freigeben“; danach die Liste.
  3. Liste: Nummer, am Ende jeder Zeile ein Kasten gleicher Breite (VM-Test
     2026-11-21): grün „läuft“ ohne Knopf, sonst roter Knopf; keine
     Plaketten, kein eigener Rollback-Knopf.
  4. „Diesen Systemstand verwenden“ fragt jetzt / ab nächstem Start;
     Abbrechen schaltet nichts um.
  4b. Ein anderer Stand ist für den nächsten Start gewählt: er ist gelb, der
     laufende grüne wird ein Knopf; Klick fragt im NiCo-Dialog, Abbrechen
     schaltet nichts um.
  5. Keine JavaScript-Fehler.

Die Liste ist nachgebaut, am echten System wird nichts gelesen oder
umgestellt. Start wie die anderen Browser-Tests, Profil standard.
"""
import os, pathlib, sys, threading, time
ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))
from nico import generations
from nico.server import create_app
from selenium import webdriver
from selenium.webdriver.chrome.service import Service

FAKE = [
    generations.Generation(3, "2026-11-21 09:52", "26.11.3", "6.18.63", True, False, True),
    generations.Generation(2, "2026-11-20 20:08", "26.11.2", "6.18.60", False, True, False),
    generations.Generation(1, "2026-11-18 17:57", "26.05.1", "6.12.1", False, False, False),
]
generations.list_generations = lambda *a, **k: list(FAKE)
switched = []
_orig_cmds = generations.switch_commands
generations.switch_commands = lambda *a, **k: (switched.append(a), _orig_cmds(*a, **k))[1]

PORT = 8833
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


try:
    d.get(f"http://127.0.0.1:{PORT}/"); time.sleep(4)
    d.execute_script("openAdmin(); _switchAdminTab('zeitmaschine');"); time.sleep(1)

    print("1. Unterreiter System | Config")
    order = d.execute_script("""return [...document.querySelectorAll(
        '[data-admin-panel="zeitmaschine"] .settings-subtab')].map(b => b.dataset.subtab);""")
    check("Reihenfolge System, Config", order == ["tm-system", "tm-config"], order)
    vis = d.execute_script("""return [!document.getElementById('tm-config').classList.contains('hidden'),
                                      !document.getElementById('tm-system').classList.contains('hidden')];""")
    check("beim Öffnen: System sichtbar, Config verborgen", vis == [False, True], vis)
    # One sub-tab component for both admin tabs: switching here must not
    # touch the settings' own sub-tabs.
    nico_panel = d.execute_script("""return !document.querySelector(
        '.settings-subtab-panel[data-subtab-panel="nico"]').classList.contains('hidden');""")
    check("Einstellungen bleiben unberührt (NiCo-Einstellungen sichtbar)", nico_panel is True)

    print("2. Ohne Freigabe")
    btn = d.execute_script("return document.querySelector('#generations-body [data-host-read=\"read_host_generations\"]');")
    check("Hinweis mit Knopf 'Freigeben'", btn is not None)
    if btn:
        d.execute_script("arguments[0].click();", btn); time.sleep(1.5)

    print("3. Liste")
    nums = d.execute_script("return [...document.querySelectorAll('#generations-body .rollback-hash')].map(e => e.textContent);")
    check("drei Stände", nums == ["#3", "#2", "#1"], nums)
    badges = d.execute_script("return document.querySelectorAll('#generations-body .host-card-brix-badge').length;")
    check("keine Plaketten mehr", badges == 0, badges)
    use = d.execute_script("return [...document.querySelectorAll('#generations-body [data-gen]')].map(b => b.dataset.gen);")
    check("kein Knopf bei #3 (läuft und Startstandard)", use == ["2", "1"], use)
    run = d.execute_script("const e=document.querySelector('#generations-body .gen-state-running');"
                           "return e ? [e.tagName, e.closest('.rollback-item').querySelector('.rollback-hash').textContent] : null;")
    check("#3 grün, nur Anzeige", run == ["SPAN", "#3"], run)
    widths = d.execute_script("return [...document.querySelectorAll('#generations-body .gen-state')].map(e => e.offsetWidth);")
    check("Kästen gleich breit", len(widths) == 3 and len(set(widths)) == 1, widths)
    check("kein eigener Rollback-Knopf",
          not d.execute_script("return !!document.getElementById('gen-rollback-btn');"))
    if os.environ.get("SHOT_DIR"):
        d.save_screenshot(os.path.join(os.environ["SHOT_DIR"], "tm-system.png"))
        d.execute_script("_switchAdminTab('einstellungen');"); time.sleep(0.5)
        d.save_screenshot(os.path.join(os.environ["SHOT_DIR"], "settings.png"))
        d.execute_script("_switchAdminTab('zeitmaschine');"); time.sleep(0.5)

    print("4. Verwenden fragt, Abbrechen schaltet nichts")
    d.execute_script("document.querySelector('#generations-body [data-gen=\"1\"]').click();"); time.sleep(0.5)
    modes = d.execute_script("return [...document.querySelectorAll('.overlay [data-mode]')].map(b => b.dataset.mode);")
    check("Wahl: Abbrechen, ab nächstem Start, jetzt", modes == ["", "boot", "switch"], modes)
    d.execute_script("document.querySelector('.overlay [data-mode=\"\"]').click();"); time.sleep(0.5)
    check("Dialog zu, nichts umgeschaltet",
          not d.execute_script("return !!document.querySelector('.overlay [data-mode]');") and not switched,
          switched)

    print("4b. Anderer Stand für den nächsten Start gewählt")
    FAKE[0] = generations.Generation(3, "2026-11-21 09:52", "26.11.3", "6.18.63", True, True, False)
    FAKE[1] = generations.Generation(2, "2026-11-20 20:08", "26.11.2", "6.18.60", False, False, True)
    d.execute_script("loadGenerations();"); time.sleep(1)
    nxt = d.execute_script("const e=document.querySelector('#generations-body .gen-state-next');"
                           "return e ? e.closest('.rollback-item').querySelector('.rollback-hash').textContent : null;")
    check("#2 gelb (nächster Start)", nxt == "#2", nxt)
    back = d.execute_script("const e=document.querySelector('#generations-body [data-gen-back]');"
                            "return e ? [e.tagName, e.dataset.genBack, e.classList.contains('gen-state-running')] : null;")
    check("#3 grün als Knopf", back == ["BUTTON", "3", True], back)
    use = d.execute_script("return [...document.querySelectorAll('#generations-body [data-gen]')].map(b => b.dataset.gen);")
    check("roter Knopf nur bei #1", use == ["1"], use)
    d.execute_script("document.querySelector('#generations-body [data-gen-back]').click();"); time.sleep(0.5)
    txt = d.execute_script("const o=[...document.querySelectorAll('.overlay')].pop(); return o ? o.textContent : '';")
    check("NiCo-Dialog nennt #3 und #2", "#3" in txt and "#2" in txt, txt[:120])
    d.execute_script("document.querySelector('.overlay [data-nd=\"cancel\"]').click();"); time.sleep(0.5)
    check("Abbrechen: Dialog zu, nichts umgeschaltet",
          not d.execute_script("return !!document.querySelector('.overlay [data-nd]');") and not switched,
          switched)
    if os.environ.get("SHOT_DIR"):
        d.save_screenshot(os.path.join(os.environ["SHOT_DIR"], "tm-system-next.png"))

    d.execute_script("document.querySelector('.settings-subtab[data-subtab=\"tm-config\"]').click();")
    time.sleep(0.5)
    check("zurück auf Config", d.execute_script(
        "return !document.getElementById('tm-config').classList.contains('hidden');"))

    print("5. Keine JavaScript-Fehler")
    errs = [e["message"] for e in d.get_log("browser") if e.get("level") == "SEVERE"
            and "favicon" not in e["message"]]
    check("Konsole ohne Fehler", not errs, errs[:3])

    print("\n" + ("ALLE PRÜFUNGEN BESTANDEN" if not fails else f"FEHLGESCHLAGEN: {fails}"))
finally:
    d.quit()
sys.exit(1 if fails else 0)
