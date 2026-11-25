"""Browser-Test: Reiter NiCo-Einstellungen mit ⓘ und einklappbaren Rubriken.

Martin, 2026-11-25: eine Zeile pro Option, Erklärungen hinter einem ⓘ,
Rubriken einklappbar und beim Öffnen des Admin-Bereichs zu. Geprüft:
  1. Kopf: keine doppelte Überschrift, Hinweis in Klammern, kein Fließtext
     mehr unter den Optionen
  2. Rubriken: beim Öffnen alle zu, Klick und Enter klappen auf, nach
     erneutem Öffnen wieder zu
  3. ⓘ: Hover zeigt den Text aus der Sprachdatei, Wegbewegen blendet aus;
     Klick hält ihn, ohne den Schalter umzulegen; Klick daneben und Escape
     schließen; das ⓘ in einer Rubrik-Überschrift klappt nicht
  4. Schalter speichern weiter (settings.json)
"""
import json, os, pathlib, sys, tempfile, threading, time
ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

xdg = pathlib.Path(tempfile.mkdtemp(prefix="nico-xdg-"))
cfg = pathlib.Path(tempfile.mkdtemp(prefix="nico-cfg-"))
os.environ["XDG_CONFIG_HOME"] = str(xdg)
os.environ["XDG_CACHE_HOME"] = str(xdg / "cache")
(xdg / "nico").mkdir(parents=True)
SETTINGS = xdg / "nico" / "settings.json"
SETTINGS.write_text(json.dumps(
    {"nixos_config_dir": str(cfg), "language": "de", "git_sync": False,
     "git_status_only": False}), encoding="utf-8")
(cfg / "config.json").write_text(json.dumps({"hosts_dir": "hosts"}), encoding="utf-8")

from nico import config_manager, generator
from nico.server import create_app
data = config_manager.get_defaults(); data["_flake_mode"] = False
(cfg / "configuration.nix").write_text(generator.generate_configuration_nix(data), encoding="utf-8")
LANG = json.loads((ROOT / "nico" / "static" / "lang" / "de.json").read_text(encoding="utf-8"))

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.chrome.service import Service

PORT = 8833
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

PANEL = "[data-subtab-panel=nico]"
def groups():
    return d.execute_script(f"""return [...document.querySelectorAll("{PANEL} .settings-group")]
      .map(g => g.classList.contains('collapsed'));""")
def popup():
    return d.execute_script("return document.querySelector('.info-tip-popup')?.textContent ?? null")
def settings():
    return json.loads(SETTINGS.read_text(encoding="utf-8"))
def open_admin():
    d.execute_script("closeAdmin?.(); openAdmin(); _switchAdminTab('einstellungen');")
    time.sleep(0.8)
def tip(key):
    return d.find_element(By.CSS_SELECTOR, f"{PANEL} .info-tip[data-tip='{key}']")
def open_group(i):
    d.find_elements(By.CSS_SELECTOR, f"{PANEL} .settings-group-toggle")[i].click()
    time.sleep(0.2)

try:
    d.get(f"http://127.0.0.1:{PORT}/")
    time.sleep(4)
    d.execute_script("document.querySelectorAll('.overlay:not(.hidden) .btn-primary').forEach(b => b.click());")
    time.sleep(1)
    open_admin()

    print("1. Kopf")
    titles = d.execute_script(f"""return [...document.querySelectorAll("{PANEL} .admin-section-title")]
      .map(e => e.textContent)""")
    check("keine Überschrift „NiCo-Einstellungen“ im Reiter", titles == [], titles)
    hint = d.execute_script(f"return document.querySelector('{PANEL} .settings-scope-hint').textContent")
    check("Hinweis in Klammern", hint == "(bleiben auf diesem Rechner)", hint)
    prose = d.execute_script(f"""return [...document.querySelectorAll("{PANEL} .raw-panel-hint")]
      .filter(e => !e.classList.contains('settings-scope-hint') && !e.classList.contains('settings-status-line')).length""")
    check("kein Fließtext mehr unter den Optionen", prose == 0, prose)

    sym = d.execute_script("""const b = document.getElementById('admin-symlink-btn');
      const g = b.closest('.settings-group');
      return { group: g.querySelector('.settings-group-toggle').textContent.trim(),
               afterPath: !!(document.querySelector('.settings-path-row').compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING),
               removeRed: document.getElementById('admin-symlink-remove-btn').classList.contains('btn-red') };""")
    check("Symlink in „Allgemein“ unter dem Config-Pfad, Entfernen rot",
          sym["group"] == "Allgemein" and sym["afterPath"] and sym["removeRed"], sym)
    tabs = d.execute_script("return [...document.querySelectorAll('.admin-tab')].map(b => b.dataset.adminTab)")
    check("Reiter: Einstellungen, Zeitmaschine, Administration",
          tabs == ["einstellungen", "zeitmaschine", "administration"], tabs)

    print("2. Rubriken")
    check("beim Öffnen alle vier zu", groups() == [True] * 4, groups())
    check("zugeklappt: Schalter nicht sichtbar",
          not d.find_element(By.ID, "setting-rebuild-log").find_element(By.XPATH, "..").is_displayed())
    open_group(2)
    check("Klick öffnet nur diese Rubrik", groups() == [True, True, False, True], groups())
    d.find_elements(By.CSS_SELECTOR, f"{PANEL} .settings-group-toggle")[0].send_keys(Keys.ENTER)
    time.sleep(0.2)
    check("Enter öffnet", groups()[0] is False, groups())
    open_admin()
    check("nach erneutem Öffnen wieder alle zu", groups() == [True] * 4, groups())

    print("3. ⓘ")
    open_group(2)
    t = tip("admin.settings.rebuildLogInfo")
    ActionChains(d).move_to_element(t).perform()
    time.sleep(0.3)
    check("Hover zeigt den Text aus der Sprachdatei", popup() == LANG["admin.settings.rebuildLogInfo"], popup())
    ActionChains(d).move_to_element(d.find_element(By.CSS_SELECTOR, f"{PANEL} .settings-scope-hint")).perform()
    time.sleep(0.3)
    check("Wegbewegen blendet aus", popup() is None, popup())

    before = settings().get("rebuild_log", False)
    checked = d.execute_script("return document.getElementById('setting-rebuild-log').checked")
    t.click()
    time.sleep(0.4)
    check("Klick hält den Text", popup() == LANG["admin.settings.rebuildLogInfo"], popup())
    ActionChains(d).move_to_element(d.find_element(By.CSS_SELECTOR, f"{PANEL} .settings-scope-hint")).perform()
    time.sleep(0.3)
    check("festgehalten bleibt er beim Wegbewegen", popup() is not None)
    check("Schalter nicht umgelegt",
          d.execute_script("return document.getElementById('setting-rebuild-log').checked") == checked
          and settings().get("rebuild_log", False) == before)
    d.find_element(By.CSS_SELECTOR, f"{PANEL} .settings-scope-hint").click()
    time.sleep(0.3)
    check("Klick daneben schließt", popup() is None, popup())
    t.click(); time.sleep(0.3)
    d.find_element(By.TAG_NAME, "body").send_keys(Keys.ESCAPE)
    time.sleep(0.3)
    check("Escape schließt", popup() is None, popup())
    check("Escape lässt den Admin-Bereich offen",
          d.execute_script("return !document.getElementById('admin-overlay').classList.contains('hidden')"))

    state = groups()[3]
    tip("admin.settings.hostReadHint").click()
    time.sleep(0.3)
    check("ⓘ in der Rubrik-Überschrift zeigt Text", popup() == LANG["admin.settings.hostReadHint"], popup())
    check("… und klappt die Rubrik nicht", groups()[3] == state, groups())
    d.find_element(By.CSS_SELECTOR, f"{PANEL} .settings-scope-hint").click()
    time.sleep(0.2)

    missing = d.execute_script(f"""return [...document.querySelectorAll("{PANEL} .info-tip")]
      .map(b => b.dataset.tip).filter(k => !t(k) || t(k) === k)""")
    check("jedes ⓘ hat einen Text", missing == [], missing)

    print("4. Schalter speichern")
    for cb, key in (("setting-rebuild-log", "rebuild_log"),
                    ("setting-rebuild-terminal", "rebuild_terminal")):
        old = bool(settings().get(key, False))
        d.find_element(By.ID, cb).find_element(By.XPATH, "..").click()
        time.sleep(0.8)
        check(f"{key} gespeichert", bool(settings().get(key, False)) == (not old), settings().get(key))
    open_group(1)
    old = bool(settings().get("show_flake_lock", False))
    d.find_element(By.ID, "setting-show-flake-lock").find_element(By.XPATH, "..").click()
    time.sleep(0.8)
    check("show_flake_lock gespeichert", bool(settings().get("show_flake_lock", False)) == (not old))
    # A label without `for` works on its first labelable element – that
    # would be the ⓘ button, not the switch (found 2026-11-25).
    old = bool(settings().get("rebuild_log", False))
    d.find_element(By.CSS_SELECTOR, "label[for=setting-rebuild-log] [data-i18n]").click()
    time.sleep(0.8)
    check("Klick auf die Bezeichnung legt den Schalter um", bool(settings().get("rebuild_log", False)) == (not old))
    check("alle Zeilen mit for", d.execute_script(f"return document.querySelectorAll('{PANEL} label.toggle-row:not([for])').length") == 0)

    print("5. Sichtbare Sektionen")
    open_admin(); open_group(0); open_group(1)
    row = d.execute_script(f"""const r = document.getElementById('sections-settings-btn').closest('.settings-action-row');
      return {{ label: r.querySelector('[data-i18n]').textContent,
               tip: !!r.querySelector('.info-tip'),
               btn: r.querySelector('#sections-settings-btn').className,
               btnText: r.querySelector('#sections-settings-btn').textContent.trim() }};""")
    check("Zeile: Bezeichnung, ⓘ, Knopf „Anpassen“", row["label"] == "Sichtbare Sektionen im Panel"
          and row["tip"] and row["btnText"] == "Anpassen", row)
    check("Knöpfe ausgefüllt (Anpassen, Ändern)", "btn-primary" in row["btn"] and "btn-primary" in
          d.find_element(By.ID, "settings-change-btn").get_attribute("class"))
    d.find_element(By.ID, "sections-settings-btn").click()
    time.sleep(0.4)
    dlg = d.execute_script("""const rows = [...document.querySelectorAll('#sections-settings-list .toggle-row')];
      return { names: rows.map(r => r.querySelector('span').childNodes[0].textContent.trim()),
               note: document.querySelector('#sections-settings-overlay [data-i18n="admin.sections.alwaysShown"]')?.textContent || '',
               actions: [...document.querySelectorAll('#sections-settings-overlay .dialog-actions button')]
                        .map(b => [b.id, b.className]) };""")
    check("Kürzel mit übersetztem Namen", "I18N – Lokalisierung" in dlg["names"], dlg["names"][:4])
    # The panel decides "has content" per shown file (host files too), so the
    # dialog says it once below the list instead of marking single sections.
    check("Hinweis unten statt Kennzeichnung je Sektion",
          dlg["note"] == "(Sektionen mit Inhalt zeigt das Panel immer an.)", dlg["note"])
    check("unten Abbrechen, dann ausgefülltes Speichern",
          dlg["actions"] == [["sections-settings-cancel", "btn-secondary"],
                             ["sections-settings-save", "btn-primary"]], dlg["actions"])
    before = settings().get("hidden_sections")
    d.execute_script("document.querySelector('#sections-settings-list input').click()")
    d.find_element(By.ID, "sections-settings-cancel").click()
    time.sleep(0.5)
    check("Abbrechen schließt ohne zu speichern",
          d.execute_script("return document.getElementById('sections-settings-overlay').classList.contains('hidden')")
          and settings().get("hidden_sections") == before)

    print("\n" + ("Alle Prüfungen bestanden." if not fails else f"FEHLGESCHLAGEN: {fails}"))
finally:
    d.quit()
sys.exit(1 if fails else 0)
