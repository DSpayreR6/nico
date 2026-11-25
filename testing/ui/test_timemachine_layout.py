"""Browser-Test: Zeitmaschine, Seite System – Liste füllt das Fenster.

Martin, 2026-11-25: Die Liste der Systemstände endete bei 340 Pixeln, ein
Drittel des Fensters blieb leer. Jetzt reicht sie bis an den unteren Rand und
scrollt in sich; der Erklärtext steht im ⓘ an der Überschrift. Geprüft:
  1. Überschrift mit ⓘ, kein Absatz darunter, ⓘ zeigt den Text
  2. großes Fenster: Liste endet am unteren Rand, scrollt in sich,
     Überschrift bleibt stehen
  3. kleines Fenster (1280×600): Liste behält eine Mindesthöhe
Die Systemstände sind nachgebaut (40 Stück), am System wird nichts gelesen.
"""
import json, os, pathlib, sys, tempfile, threading, time
ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

xdg = pathlib.Path(tempfile.mkdtemp(prefix="nico-xdg-"))
cfg = pathlib.Path(tempfile.mkdtemp(prefix="nico-cfg-"))
os.environ["XDG_CONFIG_HOME"] = str(xdg)
os.environ["XDG_CACHE_HOME"] = str(xdg / "cache")
(xdg / "nico").mkdir(parents=True)
(xdg / "nico" / "settings.json").write_text(json.dumps(
    {"nixos_config_dir": str(cfg), "language": "de", "git_sync": False,
     "read_host_generations": True}), encoding="utf-8")

from nico import config_manager, generator, generations
from nico.server import create_app
data = config_manager.get_defaults(); data["_flake_mode"] = False
(cfg / "configuration.nix").write_text(generator.generate_configuration_nix(data), encoding="utf-8")
LANG = json.loads((ROOT / "nico" / "static" / "lang" / "de.json").read_text(encoding="utf-8"))

FAKE = [generations.Generation(n, f"2026-11-{(n % 28) + 1:02d} 10:00", f"26.11.{n}", "6.18.63",
                               n == 40, n == 40, n == 40) for n in range(40, 0, -1)]
generations.list_generations = lambda *a, **k: list(FAKE)

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.chrome.service import Service

PORT = 8839
app = create_app()
threading.Thread(target=lambda: app.run(port=PORT, threaded=True), daemon=True).start()
time.sleep(2)
opts = webdriver.ChromeOptions()
for f in ("--headless=new", "--no-sandbox", "--disable-dev-shm-usage", "--window-size=1400,1000"):
    opts.add_argument(f)
opts.binary_location = os.environ["CHROME_BIN"]
d = webdriver.Chrome(service=Service(os.environ["CHROMEDRIVER"]), options=opts)

fails = []
def check(label, cond, got=None):
    print(("  OK   " if cond else "  FAIL ") + label + ("" if cond or got is None else f"  (war: {got})"))
    if not cond: fails.append(label)

GEO = """const l = document.querySelector('#generations-body .rollback-list'),
           p = document.querySelector('[data-admin-panel=zeitmaschine]'),
           h = document.querySelector('#tm-system .settings-rubrik');
  const lr = l.getBoundingClientRect(), pr = p.getBoundingClientRect();
  return { rows: l.querySelectorAll('.rollback-item').length,
           listBottom: lr.bottom, panelBottom: pr.bottom, height: lr.height,
           scrolls: l.scrollHeight > l.clientHeight,
           panelScroll: p.scrollHeight > p.clientHeight,
           headTop: h.getBoundingClientRect().top };"""

try:
    d.get(f"http://127.0.0.1:{PORT}/")
    time.sleep(4)
    d.execute_script("document.querySelectorAll('.overlay:not(.hidden):not(#admin-overlay) .btn-primary').forEach(b => b.click());")
    time.sleep(1)
    d.execute_script("openAdmin(); _switchAdminTab('zeitmaschine');")
    time.sleep(1.5)

    print("1. Überschrift mit ⓘ")
    paras = d.execute_script("return document.querySelectorAll('#tm-system .admin-hint').length")
    check("kein Absatz unter der Überschrift", paras == 0, paras)
    tip = d.find_element(By.CSS_SELECTOR, "#tm-system .info-tip[data-tip='generations.hint']")
    ActionChains(d).move_to_element(tip).perform(); time.sleep(0.3)
    txt = d.execute_script("return document.querySelector('.info-tip-popup')?.textContent ?? null")
    check("ⓘ zeigt den Erklärtext", txt == LANG["generations.hint"], txt)
    ActionChains(d).move_by_offset(0, 300).perform(); time.sleep(0.2)

    print("2. Großes Fenster")
    g = d.execute_script(GEO)
    check("alle 40 Systemstände in der Liste", g["rows"] == 40, g["rows"])
    check("Liste reicht bis an den unteren Rand", g["panelBottom"] - g["listBottom"] < 40, g)
    check("Liste scrollt in sich, nicht die Seite", g["scrolls"] and not g["panelScroll"], g)
    head = g["headTop"]
    d.execute_script("document.querySelector('#generations-body .rollback-list').scrollTop = 400")
    time.sleep(0.2)
    check("Überschrift bleibt beim Scrollen stehen", d.execute_script(GEO)["headTop"] == head)

    print("3. Kleines Fenster")
    d.set_window_size(1280, 600); time.sleep(0.5)
    g = d.execute_script(GEO)
    check("Liste behält Mindesthöhe", g["height"] >= 159, g)
    check("Liste scrollt weiter in sich", g["scrolls"], g)

    print("4. Seite Config ohne Git")
    d.set_window_size(1400, 1000); time.sleep(0.4)
    d.execute_script("closeAdmin(); openAdmin(); _switchAdminTab('zeitmaschine');"
                     "document.querySelector('[data-subtab=tm-config]').click();")
    time.sleep(1.5)
    st = d.execute_script("""const p = document.getElementById('tm-config');
      return { titles: p.querySelectorAll('.admin-section-title').length,
               groups: [...p.querySelectorAll('.settings-group')].map(g =>
                 [g.querySelector('.settings-group-toggle').textContent.trim(), g.classList.contains('collapsed')]),
               head: p.querySelector('.tm-list-head').textContent.trim(),
               statusShown: !document.getElementById('git-status-row').classList.contains('hidden'),
               initShown: !document.getElementById('git-init-btn').classList.contains('hidden'),
               prose: p.querySelectorAll('.admin-hint').length,
               noFor: p.querySelectorAll('label.toggle-row:not([for])').length,
               plainBtns: [...p.querySelectorAll('button:not(.info-tip)')].filter(b => !b.classList.contains('btn-primary')).map(b => b.id),
               missing: [...p.querySelectorAll('.info-tip')].map(b => b.dataset.tip).filter(k => !t(k) || t(k) === k) };""")
    check("kein Titel „Git / Zeitmaschine“", st["titles"] == 0, st["titles"])
    # Since 2026-11-25 .gitignore and foreign files live in the config
    # settings; the time machine keeps comparing and restoring.
    check("nur der Vergleich als Rubrik, zu", st["groups"] == [["Vergleich zweier Sicherungspunkte (Diff)", True]],
          st["groups"])
    check("keine .gitignore/Fremddateien mehr hier", d.execute_script(
        "return !document.querySelector('#tm-config #foreign-guard-toggle, #tm-config #git-gitignore-edit-btn')"))
    check("Einrichten-Knopf heißt „Versionierung einrichten (Git)“",
          d.execute_script("return document.getElementById('git-init-btn').textContent.trim()") == "Versionierung einrichten (Git)")
    check("Liste heißt „Sicherungspunkte (Commits)“", st["head"] == "Sicherungspunkte (Commits)", st["head"])
    check("ohne Git: Status mit „Git initialisieren“", st["statusShown"] and st["initShown"], st)
    check("kein Fließtext, alle Zeilen mit for", st["prose"] == 0 and st["noFor"] == 0, st)
    check("alle Knöpfe ausgefüllt", st["plainBtns"] == [], st["plainBtns"])
    check("jedes ⓘ hat einen Text", st["missing"] == [], st["missing"])

    print("5. Seite Config mit 40 Sicherungspunkten")
    import subprocess
    git = lambda *a: subprocess.run(["git", "-C", str(cfg), *a], check=True, capture_output=True)
    git("init", "-q"); git("config", "user.email", "t@t"); git("config", "user.name", "t")
    for i in range(40):
        (cfg / "notiz.nix").write_text(f"# {i}\n{{ }}\n", encoding="utf-8")
        git("add", "-A"); git("commit", "-q", "-m", f"Stand {i}")
    d.execute_script("closeAdmin(); openAdmin(); _switchAdminTab('zeitmaschine');"
                     "document.querySelector('[data-subtab=tm-config]').click();")
    time.sleep(2)
    g = d.execute_script("""const l = document.getElementById('admin-git-log'),
           p = document.querySelector('[data-admin-panel=zeitmaschine]');
      const lr = l.getBoundingClientRect(), pr = p.getBoundingClientRect();
      return { rows: l.querySelectorAll('.rollback-item').length, listBottom: lr.bottom, panelBottom: pr.bottom,
               scrolls: l.scrollHeight > l.clientHeight, panelScroll: p.scrollHeight > p.clientHeight,
               statusShown: !document.getElementById('git-status-row').classList.contains('hidden') };""")
    check("mit Git: keine Statuszeile", not g["statusShown"], g)
    # /api/git/log returns the latest 30 – unchanged behaviour.
    check("Liste zeigt die Sicherungspunkte (die letzten 30)", g["rows"] == 30, g["rows"])
    check("Liste reicht bis an den unteren Rand und scrollt in sich",
          g["panelBottom"] - g["listBottom"] < 40 and g["scrolls"] and not g["panelScroll"], g)
    more = d.execute_script("return !!document.querySelector('#admin-git-log .git-log-more')")
    check("„Mehr laden“ am Ende der Liste", more)
    d.execute_script("document.querySelector('#admin-git-log .git-log-more').click()"); time.sleep(1.5)
    st = d.execute_script("""return { rows: document.querySelectorAll('#admin-git-log .rollback-item').length,
      more: !!document.querySelector('#admin-git-log .git-log-more'),
      opts: document.getElementById('diff-from-select').options.length };""")
    check("nach „Mehr laden“ alle 40, Knopf weg, Vergleich kennt alle",
          st["rows"] == 40 and not st["more"] and st["opts"] == 40, st)

    print("\n" + ("Alle Prüfungen bestanden." if not fails else f"FEHLGESCHLAGEN: {fails}"))
finally:
    d.quit()
sys.exit(1 if fails else 0)
