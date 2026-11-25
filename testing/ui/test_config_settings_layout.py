"""Browser-Test: Reiter Config-Einstellungen mit ⓘ, Rubriken und Fenstern.

Martin, 2026-11-25: wie der Reiter NiCo-Einstellungen – eine Zeile pro
Option, Erklärungen hinter ⓘ, Rubriken zu beim Öffnen. Verzeichnisse und
Remote werden in einem Fenster geändert; jedes Fenster scrollt auf kleinen
Bildschirmen. Geprüft:
  1. Kopf und Rubriken (Verzeichnisse, Rebuild, Git, Editor)
  2. Verzeichnisse: Kästen nur lesbar; Fenster mit drei Feldern; ungültiger
     Name bleibt mit Meldung; Browser außerhalb der Config wird abgelehnt,
     innerhalb relativ eingetragen; Speichern schreibt config.json
  3. Flake-Update unter Rebuild
  4. Git: Remote-Zeile mit „keins“; Fenster mit Vorschau; ohne Angaben
     Meldung; Speichern trägt die URL ein und zeigt die Push-Schalter
  5. Editor und Dialog Validator-Regeln: eine Zeile pro Regel mit ⓘ, Klick
     auf die Zeile legt den Schalter um, Abbrechen speichert nichts
  6. Kleines Fenster (1280×600): Liste in Sektionen und Validator-Regeln
     scrollt, Speichern bleibt im Bild
"""
import json, os, pathlib, subprocess, sys, tempfile, threading, time
ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

xdg = pathlib.Path(tempfile.mkdtemp(prefix="nico-xdg-"))
cfg = pathlib.Path(tempfile.mkdtemp(prefix="nico-cfg-"))
os.environ["XDG_CONFIG_HOME"] = str(xdg)
os.environ["XDG_CACHE_HOME"] = str(xdg / "cache")
(xdg / "nico").mkdir(parents=True)
(xdg / "nico" / "settings.json").write_text(json.dumps(
    {"nixos_config_dir": str(cfg), "language": "de", "git_sync": False,
     "git_status_only": False}), encoding="utf-8")
CFGJSON = cfg / "config.json"
CFGJSON.write_text(json.dumps({"hosts_dir": "hosts"}), encoding="utf-8")

from nico import config_manager, generator
from nico.server import create_app
data = config_manager.get_defaults(); data["_flake_mode"] = False
(cfg / "configuration.nix").write_text(generator.generate_configuration_nix(data), encoding="utf-8")
(cfg / "eigene").mkdir()
subprocess.run(["git", "init", "-q", str(cfg)], check=True)
LANG = json.loads((ROOT / "nico" / "static" / "lang" / "de.json").read_text(encoding="utf-8"))

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.service import Service

PORT = 8835
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

PANEL = "[data-subtab-panel=config]"
def groups():
    return d.execute_script(f"""return [...document.querySelectorAll("{PANEL} .settings-group")]
      .map(g => [g.querySelector('.settings-group-toggle').textContent.trim(), g.classList.contains('collapsed')]);""")
def open_tab():
    d.execute_script("closeAdmin(); openAdmin(); _switchAdminTab('einstellungen');"
                     "document.querySelector('[data-subtab=config]').click();")
    time.sleep(1)
def open_group(name):
    d.execute_script(f"""[...document.querySelectorAll("{PANEL} .settings-group")]
      .find(g => g.querySelector('.settings-group-toggle').textContent.trim() === arguments[0])
      .classList.remove('collapsed')""", name)
    time.sleep(0.2)
def flow():
    return d.execute_script("""const o = document.querySelector('.flow-dialog');
      if (!o) return null;
      return { title: o.querySelector('.dialog-title').textContent,
               error: [...o.querySelectorAll('.flow-error')].filter(e => !e.classList.contains('hidden'))
                        .map(e => e.textContent).join(' '),
               inputs: [...o.querySelectorAll('input')].map(i => i.value),
               preview: o.querySelector('.flow-remote-preview')?.textContent ?? null };""")
def press(value):
    d.execute_script("document.querySelector(`.flow-dialog [data-flow='${arguments[0]}']`).click()", value)
    time.sleep(0.8)
def cfgjson():
    return json.loads(CFGJSON.read_text(encoding="utf-8"))

try:
    d.get(f"http://127.0.0.1:{PORT}/")
    time.sleep(4)
    d.execute_script("document.querySelectorAll('.overlay:not(.hidden) .btn-primary').forEach(b => b.click());")
    time.sleep(1)
    open_tab()

    print("1. Kopf und Rubriken")
    titles = d.execute_script(f"return document.querySelectorAll('{PANEL} .admin-section-title').length")
    check("keine doppelte Überschrift", titles == 0, titles)
    hint = d.execute_script(f"return document.querySelector('{PANEL} .settings-scope-hint').textContent")
    check("Hinweis in Klammern", hint == "(wandern mit der Config)", hint)
    g = groups()
    check("vier Rubriken, alle zu", [x[0] for x in g] == ["Verzeichnisse", "Rebuild", "Versionierung (Git)", "Editor"]
          and all(x[1] for x in g), g)
    prose = d.execute_script(f"""return [...document.querySelectorAll("{PANEL} .raw-panel-hint")]
      .filter(e => !e.classList.contains('settings-scope-hint') && e.id !== 'git-settings-nogit').length""")
    check("kein Fließtext unter den Optionen", prose == 0, prose)
    missing = d.execute_script(f"""return [...document.querySelectorAll("{PANEL} .info-tip")]
      .map(b => b.dataset.tip).filter(k => !t(k) || t(k) === k)""")
    check("jedes ⓘ hat einen Text", missing == [], missing)
    check("alle Schalterzeilen mit for", d.execute_script(
        f"return document.querySelectorAll('{PANEL} label.toggle-row:not([for])').length") == 0)

    print("2. Verzeichnisse")
    open_group("Verzeichnisse")
    boxes = d.execute_script("""return ['settings-hosts-dir','settings-modules-dir','settings-hm-dir']
      .map(id => [document.getElementById(id).value, document.getElementById(id).readOnly])""")
    check("Kästen nur lesbar mit Werten", boxes == [["hosts", True], ["modules", True], ["home", True]], boxes)
    d.find_element(By.ID, "settings-dirs-change-btn").click(); time.sleep(0.5)
    st = flow()
    check("Fenster mit drei Feldern", st and st["title"] == "Verzeichnisse ändern"
          and st["inputs"] == ["hosts", "modules", "home"], st)
    d.execute_script("document.querySelectorAll('.flow-dialog input')[1].value = '../raus'")
    press("ok")
    st = flow()
    check("ungültiger Name: Meldung, Fenster bleibt", st and st["error"], st)
    d.execute_script("document.querySelectorAll('.flow-dialog .btn-browse')[1].click()"); time.sleep(0.8)
    check("Verzeichnis-Browser liegt über dem Fenster", d.execute_script("""
      const b = document.getElementById('dirbrowser-overlay');
      return !b.classList.contains('hidden') &&
             +getComputedStyle(b).zIndex > +getComputedStyle(document.querySelector('.flow-dialog').parentElement).zIndex"""))
    d.execute_script("_dirBrowserCallback('/tmp'); document.getElementById('dirbrowser-overlay').classList.add('hidden')")
    time.sleep(0.2)
    st = flow()
    check("außerhalb der Config abgelehnt", st and "nicht im Config-Verzeichnis" in st["error"], st)
    d.execute_script("_dirBrowserCallback(arguments[0])", str(cfg / "eigene"))
    time.sleep(0.2)
    check("innerhalb relativ eingetragen", flow()["inputs"][1] == "eigene", flow())
    press("ok")
    check("Fenster zu nach Speichern", flow() is None, flow())
    check("config.json geschrieben", cfgjson().get("modules_dir") == "eigene", cfgjson())
    check("Kasten zeigt den neuen Wert",
          d.execute_script("return document.getElementById('settings-modules-dir').value") == "eigene")

    print("3. Rebuild")
    in_rebuild = d.execute_script("""return document.getElementById('flake-update-toggle')
      .closest('.settings-group').querySelector('.settings-group-toggle').textContent.trim()""")
    check("Flake-Update unter Rebuild", in_rebuild == "Rebuild", in_rebuild)

    print("4. Git")
    open_group("Versionierung (Git)")
    check("Remote-Zeile sichtbar, „keins“", d.execute_script("""return !document.getElementById('git-set-remote-row').classList.contains('hidden')
      && document.getElementById('git-remote-current').value === 'keins'"""))
    check("Push-Schalter ohne Remote ausgeblendet",
          d.execute_script("return document.getElementById('git-push-settings-row').classList.contains('hidden')"))
    d.find_element(By.ID, "git-remote-setup-btn").click(); time.sleep(0.5)
    press("ok")
    st = flow()
    check("ohne Angaben: Meldung", st and "Benutzer und Repository" in st["error"], st)
    d.execute_script("""const o = document.querySelector('.flow-dialog');
      o.querySelector('.flow-remote-user').value = 'martin';
      o.querySelector('.flow-remote-repo').value = 'nixos-config';
      o.querySelector('.flow-remote-repo').dispatchEvent(new Event('input', {bubbles: true}));""")
    time.sleep(0.2)
    check("Vorschau zeigt die Adresse", flow()["preview"] == "git@github.com:martin/nixos-config.git", flow())
    press("ok"); time.sleep(0.5)
    check("Fenster zu nach Speichern", flow() is None, flow())
    url = subprocess.run(["git", "-C", str(cfg), "remote", "get-url", "origin"],
                         capture_output=True, text=True).stdout.strip()
    check("Remote im Repository eingetragen", url == "git@github.com:martin/nixos-config.git", url)
    check("Kasten zeigt die URL", d.execute_script("return document.getElementById('git-remote-current').value") == url)
    check("Push-Schalter jetzt sichtbar",
          not d.execute_script("return document.getElementById('git-push-settings-row').classList.contains('hidden')"))

    print("4b. Pflege der Versionierung (aus der Zeitmaschine hierher)")
    st = d.execute_script("""return { maint: !document.getElementById('git-maint-rows').classList.contains('hidden'),
      nogit: !document.getElementById('git-settings-nogit').classList.contains('hidden'),
      guard: !!document.querySelector('[data-subtab-panel=config] #foreign-guard-toggle'),
      btn: document.getElementById('git-gitignore-edit-btn').textContent.trim() };""")
    check("mit Versionierung: Pflege-Zeilen da, kein Einrichten", st["maint"] and not st["nogit"], st)
    check("Fremddatei-Schalter in den Config-Einstellungen", st["guard"], st)
    # The label follows the file: "Create" without, "Edit" with a .gitignore
    # (setting up the versioning usually writes one already).
    exists = (cfg / ".gitignore").exists()
    check("Knopf passt zur Datei (Anlegen/Bearbeiten)", st["btn"] == ("Bearbeiten" if exists else "Anlegen"), st["btn"])
    if not exists:
        d.find_element(By.ID, "git-gitignore-edit-btn").click(); time.sleep(1)
        check(".gitignore angelegt, Knopf heißt jetzt „Bearbeiten“", (cfg / ".gitignore").exists() and
              d.execute_script("return document.getElementById('git-gitignore-edit-btn').textContent.trim()") == "Bearbeiten")
    d.find_element(By.ID, "git-gitignore-edit-btn").click(); time.sleep(0.8)
    st = flow()
    check("Fenster mit dem Inhalt der .gitignore", st and st["title"] == "Ausgenommene Dateien (.gitignore)"
          and d.execute_script("return document.querySelector('.flow-dialog textarea').value.length > 0"), st)
    d.execute_script("const t = document.querySelector('.flow-dialog textarea'); t.value += '\\neigene-notiz.txt\\n';")
    press("ok")
    check("Speichern schreibt die .gitignore, Fenster zu", flow() is None
          and "eigene-notiz.txt" in (cfg / ".gitignore").read_text(encoding="utf-8"))

    print("4c. Prüfen: wirkt sofort, Vorschau, erst nach Bestätigung")
    # Until 2026-11-25 the buttons were bound when the time machine tab
    # opened – after the move into the settings a click did nothing. And
    # "nothing found" was a green toast that looked like "done".
    d.get(f"http://127.0.0.1:{PORT}/"); time.sleep(4)
    d.execute_script("document.querySelectorAll('.overlay:not(.hidden):not(#admin-overlay) .btn-primary').forEach(b => b.click());")
    time.sleep(1)
    open_tab(); open_group("Versionierung (Git)")
    labels = d.execute_script("return ['git-foreign-cleanup-btn','git-large-cleanup-btn'].map(id => document.getElementById(id).textContent.trim())")
    check("beide Knöpfe heißen „Prüfen“", labels == ["Prüfen", "Prüfen"], labels)
    for btn, none_key in (("git-foreign-cleanup-btn", "git.foreign.cleanupNone"),
                          ("git-large-cleanup-btn", "git.large.none")):
        d.find_element(By.ID, btn).click(); time.sleep(1.5)
        st = flow()
        check(f"{btn}: nichts gefunden → Fenster mit Schließen", st and LANG[none_key] in
              d.execute_script("return document.querySelector('.flow-dialog .flow-body').textContent"), st)
        press("cancel")

    git = lambda *a: subprocess.run(["git", "-C", str(cfg), *a], check=True, capture_output=True)
    git("config", "user.email", "t@t"); git("config", "user.name", "t")
    (cfg / "skript.sh").write_text("echo hallo\n", encoding="utf-8")
    git("add", "-f", "skript.sh"); git("commit", "-q", "-m", "skript")
    d.find_element(By.ID, "git-foreign-cleanup-btn").click(); time.sleep(1.5)
    st = d.execute_script("""const m = [...document.querySelectorAll('.overlay .modal')].pop();
      return m ? { list: m.textContent.includes('skript.sh'),
                   btns: [...m.querySelectorAll('.dialog-actions button')].map(b => [b.id, b.className, b.textContent.trim()]) } : null;""")
    check("Fremddatei: erst die Liste", st and st["list"], st)
    check("Abbrechen zuerst, dann „Aufräumen“", st and st["btns"] == [["ff-cancel", "btn-secondary", "Abbrechen"],
                                                                    ["ff-ok", "btn-primary", "Aufräumen"]], st)
    d.find_element(By.ID, "ff-cancel").click(); time.sleep(0.5)
    tracked = lambda: "skript.sh" in subprocess.run(["git", "-C", str(cfg), "ls-files"], capture_output=True, text=True).stdout
    check("Abbrechen ändert nichts", tracked())
    d.find_element(By.ID, "git-foreign-cleanup-btn").click(); time.sleep(1.5)
    d.find_element(By.ID, "ff-ok").click(); time.sleep(1.5)
    check("Aufräumen trägt aus, Datei bleibt auf der Platte",
          not tracked() and (cfg / "skript.sh").exists() and "skript.sh" in (cfg / ".gitignore").read_text(encoding="utf-8"))

    with open(cfg / "gross.bin", "wb") as fh:   # sparse, costs no disk space
        fh.truncate(60 * 1024 * 1024)
    d.find_element(By.ID, "git-large-cleanup-btn").click(); time.sleep(1.5)
    st = d.execute_script("""const m = [...document.querySelectorAll('.overlay .modal')].pop();
      return m ? { list: m.textContent.includes('gross.bin'), del: document.getElementById('lf-delete')?.checked,
                   btns: [...m.querySelectorAll('.dialog-actions button')].map(b => [b.id, b.textContent.trim()]) } : null;""")
    check("große Datei: erst die Liste", st and st["list"], st)
    check("„Auch von der Festplatte löschen“ nicht vorab angehakt", st and st["del"] is False, st)
    check("Abbrechen zuerst, dann „Aufräumen“", st and st["btns"] == [["lf-cancel", "Abbrechen"], ["lf-ok", "Aufräumen"]], st)
    d.find_element(By.ID, "lf-cancel").click(); time.sleep(0.5)
    check("Abbrechen lässt die Datei liegen", (cfg / "gross.bin").exists())
    (cfg / "gross.bin").unlink()

    print("5. Editor und Validator-Regeln")
    open_group("Editor")
    d.find_element(By.ID, "validation-settings-btn").click(); time.sleep(0.8)
    rules = d.execute_script("""const rows = [...document.querySelectorAll('#validation-rules-list .toggle-row')]
        .filter(r => r.style.display !== 'none');
      return { n: rows.length,
               oneLine: rows.every(r => r.getBoundingClientRect().height < 34),
               tips: rows.filter(r => r.querySelector('.info-tip')).length,
               withFor: rows.every(r => r.htmlFor && document.getElementById(r.htmlFor)),
               actions: [...document.querySelectorAll('#validation-settings-overlay .dialog-actions button')]
                        .map(b => [b.id, b.className]) };""")
    check("Regeln einzeilig, jede mit ⓘ", rules["n"] > 5 and rules["oneLine"] and rules["tips"] == rules["n"], rules)
    # Visible legend lines only: the Flake line belongs to configs that show
    # flake rules, this test config has no flake.
    legend = d.execute_script("""return [...document.querySelectorAll('#validation-settings-overlay .validator-legend [data-i18n]')]
      .filter(e => !e.closest('.hidden')).map(e => [e.dataset.i18n, e.textContent])""")
    check("Legende ohne Flake: Fehler, Warnung, Hinweis, Aus",
          [k for k, _ in legend] == ["admin.validation.legendError", "admin.validation.legendWarning",
                                    "admin.validation.legendInfo", "admin.validation.legendOff"]
          and all(txt == LANG[k] for k, txt in legend), legend)
    # With a flake.nix the server reports flake_mode: flake rules with their
    # badge appear, and the legend explains it. Until 2026-11-25 the flag came
    # from a preview field the server no longer sent – always "no flake".
    d.execute_script("closeValidationSettings()")
    (cfg / "flake.nix").write_text('{ inputs = { }; outputs = { self }: { }; }\n', encoding="utf-8")
    d.find_element(By.ID, "validation-settings-btn").click(); time.sleep(1)
    fl = d.execute_script("""return { badges: [...document.querySelectorAll('#validation-rules-list .toggle-row')]
        .filter(r => r.style.display !== 'none' && r.querySelector('.rule-badge')).length,
      legend: !document.getElementById('validator-legend-flake').classList.contains('hidden') }""")
    check("mit flake.nix: Flake-Regeln sichtbar, Legende erklärt sie", fl["badges"] > 0 and fl["legend"], fl)
    d.execute_script("closeValidationSettings()")
    (cfg / "flake.nix").unlink()
    d.find_element(By.ID, "validation-settings-btn").click(); time.sleep(0.8)
    check("Abbrechen und ausgefülltes Speichern",
          rules["actions"] == [["validation-settings-cancel", "btn-secondary"],
                               ["validation-settings-save", "btn-primary"]], rules["actions"])
    before = cfgjson().get("validation_rules")
    first = d.execute_script("""const r = [...document.querySelectorAll('#validation-rules-list .toggle-row')]
        .find(r => r.style.display !== 'none');
      const cb = document.getElementById(r.htmlFor), was = cb.checked;
      r.querySelector('span span:not(.toggle-track):not(.toggle-thumb)').click();
      return [was, cb.checked];""")
    check("Klick auf die Regel legt den Schalter um", first[0] != first[1], first)
    d.find_element(By.ID, "validation-settings-cancel").click(); time.sleep(0.5)
    check("Abbrechen speichert nichts", cfgjson().get("validation_rules") == before)

    print("6. Kleines Fenster")
    d.set_window_size(1280, 600); time.sleep(0.5)
    for btn, overlay, save in (("validation-settings-btn", "validation-settings-overlay", "validation-settings-save"),):
        d.find_element(By.ID, btn).click(); time.sleep(0.8)
        st = d.execute_script("""const o = document.getElementById(arguments[0]), l = o.querySelector('.list-dialog-list'),
            s = document.getElementById(arguments[1]).getBoundingClientRect();
          return { scrolls: l.scrollHeight > l.clientHeight, saveVisible: s.bottom <= window.innerHeight && s.top >= 0 };""",
          overlay, save)
        check(f"{overlay}: Liste scrollt, Speichern im Bild", st["scrolls"] and st["saveVisible"], st)
        d.execute_script("arguments[0].click()", d.find_element(By.ID, save.replace("save", "cancel")))
        time.sleep(0.4)
    d.execute_script("document.querySelector('[data-subtab=nico]').click();")
    d.execute_script("[...document.querySelectorAll('[data-subtab-panel=nico] .settings-group')].forEach(g => g.classList.remove('collapsed'))")
    d.find_element(By.ID, "sections-settings-btn").click(); time.sleep(0.6)
    st = d.execute_script("""const l = document.getElementById('sections-settings-list'),
        s = document.getElementById('sections-settings-save').getBoundingClientRect();
      return { scrolls: l.scrollHeight > l.clientHeight, saveVisible: s.bottom <= window.innerHeight && s.top >= 0 };""")
    check("Sektionen: Liste scrollt, Speichern im Bild", st["scrolls"] and st["saveVisible"], st)

    print("\n" + ("Alle Prüfungen bestanden." if not fails else f"FEHLGESCHLAGEN: {fails}"))
finally:
    d.quit()
sys.exit(1 if fails else 0)
