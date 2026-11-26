"""Browser-Test: Importe und Config-Pfad im Admin-Bereich als Fenster.

Martin, 2026-11-25: Import, ZIP-Import und Einstellungs-Import klappten unter
dem Knopf Zeilen auf; jetzt läuft jeder Vorgang in einem Fenster
(openFlowDialog), Schritt für Schritt. Ebenso „Config-Pfad ändern“.
Geprüft:
  1. ZIP-Import: Prüfen, Bestätigen, Textmodus/Panel, Sicherung, fertig –
     alles im selben Fenster, keine Zeile im Admin-Bereich
  2. ungültige ZIP: Fehler, „Manuell suchen“ wechselt im Fenster zum
     Verzeichnis-Import
  3. Verzeichnis-Import: leerer Pfad und falscher Pfad bleiben im Fenster
     mit Meldung; Escape schließt; richtiger Pfad importiert
  4. Einstellungs-Import: kaputtes JSON meldet im Fenster; gültiges
     übernimmt
  5. Config-Pfad ändern: Fenster mit aktuellem Pfad, leerer Pfad meldet,
     neues Verzeichnis fragt nach, Abbrechen ändert nichts, Übernehmen
     schreibt die App-Settings
"""
import json, os, pathlib, sys, tempfile, threading, time, urllib.request, zipfile
ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

xdg = pathlib.Path(tempfile.mkdtemp(prefix="nico-xdg-"))
cfg = pathlib.Path(tempfile.mkdtemp(prefix="nico-cfg-"))
work = pathlib.Path(tempfile.mkdtemp(prefix="nico-work-"))
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

# A config to import: as ZIP and as directory, each with a marker.
def plain_config(marker):
    return "{ config, pkgs, ... }:\n{\n  networking.hostName = \"" + marker + "\";\n  system.stateVersion = \"25.05\";\n}\n"
good_zip = work / "gut.zip"
with zipfile.ZipFile(good_zip, "w") as z:
    z.writestr("configuration.nix", plain_config("ausdemzip"))
bad_zip = work / "leer.zip"
with zipfile.ZipFile(bad_zip, "w") as z:
    z.writestr("liesmich.txt", "nichts")
src = work / "quelle"
src.mkdir()
(src / "configuration.nix").write_text(plain_config("ausdemverzeichnis"), encoding="utf-8")

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.chrome.service import Service

PORT = 8831
app = create_app()
threading.Thread(target=lambda: app.run(port=PORT, threaded=True), daemon=True).start()
time.sleep(2)
opts = webdriver.ChromeOptions()
for f in ("--headless=new", "--no-sandbox", "--disable-dev-shm-usage", "--window-size=1400,900"):
    opts.add_argument(f)
opts.binary_location = os.environ["CHROME_BIN"]
d = webdriver.Chrome(service=Service(os.environ["CHROMEDRIVER"]), options=opts)

fails = []
def check(label, cond, got=None):
    print(("  OK   " if cond else "  FAIL ") + label + ("" if cond or got is None else f"  (war: {got})"))
    if not cond: fails.append(label)

def flow():
    """State of the open step window, or None."""
    return d.execute_script("""const o = document.querySelector('.flow-dialog');
      if (!o) return null;
      return { title: o.querySelector('.dialog-title').textContent,
               text: o.querySelector('.flow-body').textContent.replace(/\\s+/g, ' ').trim(),
               error: o.querySelector('.flow-error')?.textContent || '',
               input: o.querySelector('input')?.value ?? null,
               buttons: [...o.querySelectorAll('.dialog-actions button')].map(b => b.dataset.flow),
               windows: document.querySelectorAll('.overlay:not(.hidden) .dialog').length };""")

def press(value):
    d.execute_script("document.querySelector(`.flow-dialog [data-flow='${arguments[0]}']`).click()", value)

def wait_flow(cond, secs=10):
    for _ in range(int(secs / 0.2)):
        st = flow()
        if cond(st):
            return st
        time.sleep(0.2)
    return flow()

def set_input(value):
    d.execute_script("""const i = document.querySelector('.flow-dialog input');
      i.value = arguments[0]; i.dispatchEvent(new Event('input', {bubbles: true}));""", value)

def pick_file(input_id, path):
    el = d.find_element(By.ID, input_id)
    d.execute_script("arguments[0].classList.remove('hidden')", el)
    el.send_keys(str(path))
    d.execute_script("arguments[0].classList.add('hidden')", el)

def admin(tab):
    # The groups start closed (2026-11-25); the flows below need their buttons.
    d.execute_script("""openAdmin(); _switchAdminTab(arguments[0]);
      document.querySelectorAll(`[data-admin-panel=${arguments[0]}] .settings-group`)
        .forEach(g => g.classList.remove('collapsed'));""", tab)
    time.sleep(0.5)

def inline_rows():
    """Rows that used to open in the admin panel – none may exist any more."""
    return d.execute_script("""return ['admin-zip-import-state', 'admin-settings-import-state',
      'settings-change-row', 'import-result'].filter(id => document.getElementById(id));""")

def disk():
    return (cfg / "configuration.nix").read_text(encoding="utf-8")

try:
    d.get(f"http://127.0.0.1:{PORT}/")
    time.sleep(4)
    d.execute_script("document.querySelectorAll('.overlay:not(.hidden):not(#admin-overlay) .btn-primary').forEach(b => b.click());")
    time.sleep(1)
    check("keine alten Zeilen im Admin-Bereich", inline_rows() == [], inline_rows())

    print("0. Aufbau der Administration")
    d.execute_script("openAdmin(); _switchAdminTab('administration');"); time.sleep(0.5)
    st = d.execute_script("""const p = document.querySelector('[data-admin-panel=administration]');
      return { groups: [...p.querySelectorAll('.settings-group')].map(g =>
                 [g.querySelector('.settings-group-toggle').textContent.trim(), g.classList.contains('collapsed')]),
               titles: p.querySelectorAll('.admin-section-title').length,
               prose: [...p.querySelectorAll('.raw-panel-hint, .admin-hint')].filter(e => !e.classList.contains('settings-status-line')).length,
               missing: [...p.querySelectorAll('.info-tip')].map(b => b.dataset.tip).filter(k => !t(k) || t(k) === k),
               plain: [...p.querySelectorAll('button:not(.info-tip)')].filter(b => !b.classList.contains('btn-primary') && !b.classList.contains('btn-red')).map(b => b.id) };""")
    check("drei Rubriken, alle zu", [g[0] for g in st["groups"]] == ["Config übertragen",
          "Programmeinstellungen", "Aus NiCo lösen"] and all(g[1] for g in st["groups"]), st["groups"])
    check("keine Abschnittstitel, kein Fließtext", st["titles"] == 0 and st["prose"] == 0, st)
    check("jedes ⓘ hat einen Text", st["missing"] == [], st["missing"])
    check("alle Knöpfe ausgefüllt", st["plain"] == [], st["plain"])
    red = d.execute_script("""return [...document.querySelectorAll('[data-admin-panel=administration] .btn-red')].map(b => b.id)""")
    # What replaces or removes something is red (Martin, 2026-11-25).
    check("rot: alle Importe, Lösen", sorted(red) == sorted([
        "admin-zip-import-btn", "admin-import-btn",
        "admin-settings-import-btn", "admin-detach-btn"]), red)
    d.execute_script("closeAdmin()")

    print("1. ZIP-Import")
    admin("administration")
    pick_file("admin-zip-input", good_zip)
    st = wait_flow(lambda s: s and "ok" in s["buttons"])
    check("Fenster „Aus ZIP importieren“", st and st["title"] == "Aus ZIP importieren", st)
    check("Bestätigung nennt die gefundene Datei", st and "configuration.nix gefunden" in st["text"], st)
    check("nur ein Fenster offen", st and st["windows"] == 1, st)
    press("ok")
    st = wait_flow(lambda s: s and "p" in s["buttons"])
    check("Frage Textmodus/Panel im selben Fenster", st and st["buttons"] == ["cancel", "r", "p"]
          and st["windows"] == 1 and "bearbeiten" in st["text"], st)
    press("p")
    st = wait_flow(lambda s: s is None or "ok" in s["buttons"])
    if st:
        check("Sicherungsfrage im selben Fenster", "gesichert" in st["text"], st)
        press("ok")
    st = wait_flow(lambda s: s is None, 15)
    check("Fenster nach dem Import zu", st is None, st)
    check("ZIP-Inhalt importiert", "ausdemzip" in disk())
    time.sleep(1)

    print("2. Ungültige ZIP → Manuell suchen")
    d.execute_script("document.querySelectorAll('.overlay:not(.hidden):not(#admin-overlay) .btn-primary').forEach(b => b.click());")
    admin("administration")
    pick_file("admin-zip-input", bad_zip)
    st = wait_flow(lambda s: s and s["error"])
    check("Fehler im Fenster", st and "keine configuration.nix" in st["error"], st)
    check("Abbrechen und Manuell suchen", st and st["buttons"] == ["cancel", "manual"], st)
    press("manual")
    st = wait_flow(lambda s: s and s["input"] is not None)
    check("wechselt im selben Fenster zum Verzeichnis", st and st["title"] == "Aus ZIP importieren"
          and st["input"] is not None, st)
    press("cancel")
    check("Abbrechen schließt", wait_flow(lambda s: s is None) is None)

    print("3. Verzeichnis-Import")
    d.find_element(By.ID, "admin-import-btn").click()
    st = wait_flow(lambda s: s and s["input"] is not None)
    check("Fenster mit Pfad, Vorgabe /etc/nixos", st and st["title"] == "Aus Verzeichnis importieren…"
          and st["input"] == "/etc/nixos", st)
    d.find_element(By.CSS_SELECTOR, ".flow-dialog input").send_keys(Keys.ESCAPE)
    check("Escape schließt", wait_flow(lambda s: s is None) is None)

    d.find_element(By.ID, "admin-import-btn").click()
    wait_flow(lambda s: s and s["input"] is not None)
    set_input("")
    press("ok")
    st = wait_flow(lambda s: s and s["error"])
    check("leerer Pfad: Meldung, Fenster bleibt", st and "Verzeichnis angeben" in st["error"], st)
    set_input(str(work / "gibtsnicht"))
    press("ok")
    st = wait_flow(lambda s: s and "p" in s["buttons"])
    press("p")
    # The server asks about the backup before it looks at the source path
    # (unchanged behaviour); the error comes after that.
    st = wait_flow(lambda s: s and (s["error"] or "gesichert" in s["text"]))
    if st and not st["error"]:
        press("ok")
    st = wait_flow(lambda s: s and s["input"] is not None and s["error"])
    check("falscher Pfad: zurück zum Pfad mit Meldung", st and st["error"] and
          st["input"].endswith("gibtsnicht"), st)
    set_input(str(src))
    press("ok")
    wait_flow(lambda s: s and "p" in s["buttons"])
    press("p")
    st = wait_flow(lambda s: s is None or "ok" in s["buttons"])
    if st and st["input"] is None:
        press("ok")
    st = wait_flow(lambda s: s is None, 15)
    check("Fenster nach dem Import zu", st is None, st)
    check("Verzeichnis importiert", "ausdemverzeichnis" in disk())
    time.sleep(1)

    print("4. Einstellungs-Import")
    d.execute_script("document.querySelectorAll('.overlay:not(.hidden):not(#admin-overlay) .btn-primary').forEach(b => b.click());")
    admin("administration")
    broken = work / "kaputt.json"
    broken.write_text("{ nicht json", encoding="utf-8")
    pick_file("admin-settings-import-input", broken)
    st = wait_flow(lambda s: s and s["error"])
    check("kaputtes JSON: Meldung im Fenster", st and st["title"] == "Einstellungen importieren"
          and "JSON" in st["error"] and st["buttons"] == ["cancel"], st)
    press("cancel")
    wait_flow(lambda s: s is None)
    exported = work / "einstellungen.json"
    exported.write_bytes(urllib.request.urlopen(f"http://127.0.0.1:{PORT}/api/settings/app/export").read())
    pick_file("admin-settings-import-input", exported)
    st = wait_flow(lambda s: s and "ok" in s["buttons"])
    check("Bestätigung im Fenster", st and "Config-Pfad bleibt unverändert" in st["text"], st)
    d.execute_script("window._marker = 1;")
    press("ok")
    for _ in range(30):
        time.sleep(0.3)
        if d.execute_script("return window._marker === undefined"):
            break
    check("übernommen, Seite lädt neu", d.execute_script("return window._marker === undefined"))
    time.sleep(3)

    print("5. Config-Pfad ändern")
    d.execute_script("document.querySelectorAll('.overlay:not(.hidden):not(#admin-overlay) .btn-primary').forEach(b => b.click());")
    admin("einstellungen")
    # Since 2026-11-25 the groups start closed: open "Allgemein" first.
    d.execute_script("document.getElementById('settings-change-btn').closest('.settings-group').classList.remove('collapsed')")
    d.find_element(By.ID, "settings-change-btn").click()
    st = wait_flow(lambda s: s and s["input"] is not None)
    check("Fenster mit aktuellem Pfad", st and st["title"] == "Config-Pfad ändern"
          and st["input"] == str(cfg), st)
    set_input("")
    press("ok")
    st = wait_flow(lambda s: s and s["error"])
    check("leerer Pfad: Meldung, Fenster bleibt", st and "Verzeichnis angeben" in st["error"], st)
    newdir = work / "neu-config"
    set_input(str(newdir))
    press("ok")
    st = wait_flow(lambda s: s and s["input"] is None)
    check("neues Verzeichnis: Rückfrage im Fenster", st and str(newdir) in st["text"], st)
    press("cancel")
    st = wait_flow(lambda s: s and s["input"] is not None)
    check("Abbrechen der Rückfrage: zurück zum Pfad", st and st["input"] == str(newdir), st)
    press("cancel")
    wait_flow(lambda s: s is None)
    check("Abbrechen ändert nichts",
          json.loads(SETTINGS.read_text(encoding="utf-8"))["nixos_config_dir"] == str(cfg))
    d.find_element(By.ID, "settings-change-btn").click()
    wait_flow(lambda s: s and s["input"] is not None)
    set_input(str(newdir))
    press("ok")
    wait_flow(lambda s: s and s["input"] is None)
    press("ok")
    time.sleep(2)
    check("Übernehmen schreibt den neuen Pfad",
          json.loads(SETTINGS.read_text(encoding="utf-8"))["nixos_config_dir"] == str(newdir))

    print("6. Config verschieben")
    time.sleep(2)
    d.get(f"http://127.0.0.1:{PORT}/"); time.sleep(4)
    d.execute_script("document.querySelectorAll('.overlay:not(.hidden):not(#admin-overlay) .btn-primary').forEach(b => b.click());")
    time.sleep(1)
    src = pathlib.Path(json.loads(SETTINGS.read_text(encoding="utf-8"))["nixos_config_dir"])
    (src / "merkmal.nix").write_text("{ }\n", encoding="utf-8")
    admin("administration")
    d.find_element(By.ID, "admin-move-btn").click(); time.sleep(0.8)
    st = flow()
    check("Fenster nennt den jetzigen Ort", st and st["title"] == "Config verschieben" and str(src) in st["text"], st)
    press("ok")
    st = flow()
    check("ohne Ziel: Meldung", st and "Verzeichnis angeben" in st["error"], st)
    dest = work / "verschoben"
    set_input(str(dest))
    press("ok")
    st = flow()
    check("Rückfrage nennt alt und neu", st and str(src) in st["text"] and str(dest) in st["text"], st)
    d.execute_script("window._marker = 1;")
    press("ok")
    # Since 2026-11-26 (revision 2026-11-25, F5) a window follows the move:
    # no cancel, three choices; "start over" asks once more with Back.
    st = wait_flow(lambda s: s and "liegt jetzt in" in s["text"])
    check("nach dem Umzug: Fenster nennt den neuen Ort", st and str(dest.resolve()) in st["text"], st)
    check("… drei Wahlen, kein Abbrechen", st and sorted(st["buttons"]) == ["fresh", "new", "other"], st)
    press("fresh")
    st = wait_flow(lambda s: s and "Einrichtung startet" in s["text"])
    check("„Ohne Config neu beginnen“: Hinweis mit Zurück", st and st["buttons"] == ["back", "ok"], st)
    press("back")
    st = wait_flow(lambda s: s and "liegt jetzt in" in s["text"])
    check("Zurück führt zu den drei Wahlen", st and "new" in st["buttons"], st)
    press("new")
    for _ in range(30):
        time.sleep(0.3)
        if d.execute_script("return window._marker === undefined"):
            break
    check("verschoben, alter Ort weg", (dest / "merkmal.nix").exists() and not src.exists())
    check("Config-Pfad umgestellt",
          json.loads(SETTINGS.read_text(encoding="utf-8"))["nixos_config_dir"] == str(dest.resolve()))
    check("Seite neu geladen", d.execute_script("return window._marker === undefined"))

    print("\n" + ("Alle Prüfungen bestanden." if not fails else f"FEHLGESCHLAGEN: {fails}"))
finally:
    d.quit()
sys.exit(1 if fails else 0)
