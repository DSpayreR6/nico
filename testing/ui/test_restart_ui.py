"""Browser-Test: Beenden-Dialog mit Neustart, Speichern vorher, Monitor nach Update.

Martin, 2026-11-25: Nach dem Update soll der Monitor „NiCo neu starten“
anbieten, der Beenden-Dialog Abbrechen / Neu starten / Beenden, und vorher
wird gespeichert – kein Datenverlust. Geprüft:
  1. Beenden-Dialog: drei Knöpfe in dieser Reihenfolge, Abbrechen tut nichts
  2. Beenden speichert ungespeicherten Text im Textmodus (bis 2026-11-25
     ging er verloren), erst danach /api/shutdown
  3. Speichern schlägt fehl: Hinweis, NiCo beendet sich nicht
  4. Neu starten: speichert, ruft /api/restart, zeigt die Warteseite und lädt
     neu, sobald /api/status eine andere Instanz meldet
  5. Update: speichert vorher; danach im Monitor „NiCo neu starten“ und
     „Später“ statt „Schließen“
/api/shutdown und /api/restart sind im Browser ersetzt – der echte Aufruf
beendete den Testprozess (der echte Neustart: testing/routes/test_restart.py
und von Hand). Das Update läuft gegen ein ersetztes `nix profile`.
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
     "git_status_only": False}), encoding="utf-8")
(cfg / "config.json").write_text(json.dumps({"hosts_dir": "hosts"}), encoding="utf-8")

from nico import config_manager, generator, selfupdate
from nico.server import create_app
data = config_manager.get_defaults(); data["_flake_mode"] = False
(cfg / "configuration.nix").write_text(generator.generate_configuration_nix(data), encoding="utf-8")
(cfg / "modules").mkdir()
MOD = cfg / "modules" / "text.nix"
MOD.write_text("{ ... }:\n{\n  services.a.enable = true;\n}\n", encoding="utf-8")

# An installed NiCo whose upgrade just prints a line. Since 2026-11-26 it is
# known from its own store path; the profile entry is read on the click,
# with the release read_host_profile (revision 2026-11-25, S3/W1).
selfupdate.running_store_path = lambda here=None: "/nix/store/abc-nico-0.25.0"
selfupdate.profile_entry = lambda store=None: ("nico", "github:DSpayreR6/nico/dev")
selfupdate.upgrade_command = lambda name: ["sh", "-c", "echo upgraded"]

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.service import Service

PORT = 8829
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

# Records the calls that would end the process, answers them itself.
STUB = """
  sessionStorage.setItem("calls", "[]");
  const real = csrfFetch;
  window.csrfFetch = (url, opts) => {
    if (url === '/api/shutdown' || url === '/api/restart') {
      sessionStorage.setItem("calls", JSON.stringify([...JSON.parse(sessionStorage.getItem("calls")), url]));
      const body = url === '/api/restart' ? {ok: true, instance: 'alte-instanz'} : {ok: true};
      return Promise.resolve(new Response(JSON.stringify(body)));
    }
    return real(url, opts);
  };
  window._marker = 'vor-dem-neuladen';
"""

def load_text_file():
    d.get(f"http://127.0.0.1:{PORT}/")
    time.sleep(4)
    d.execute_script("document.querySelectorAll('.overlay').forEach(e => e.classList.add('hidden'))")
    d.execute_script("Sidebar.selectFile('modules/text.nix', 'text.nix', {force: true});")
    time.sleep(2)
    d.execute_script(STUB)

def type_text(s):
    d.execute_script("""const ta = document.getElementById('raw-file-editor');
      ta.value = ta.value.replace('}', arguments[0] + '\\n}');
      ta.dispatchEvent(new Event('input', {bubbles: true}));""", s)
    time.sleep(0.3)

# In sessionStorage: survives the reload after a restart.
def calls():
    return d.execute_script("return JSON.parse(sessionStorage.getItem('calls') || '[]')")

def dialog_buttons():
    return d.execute_script("""const o = [...document.querySelectorAll('.overlay')].pop();
      return o ? [...o.querySelectorAll('.dialog-actions button')].map(b => [b.dataset.nd, b.textContent]) : [];""")

def press(nd):
    d.execute_script("[...document.querySelectorAll('.overlay')].pop().querySelector(`[data-nd='${arguments[0]}']`).click()", nd)

try:
    load_text_file()
    check("Textmodus offen", d.execute_script("return !!document.getElementById('raw-file-editor')"))

    print("1. Beenden-Dialog")
    d.find_element(By.ID, "quit-btn").click(); time.sleep(0.4)
    btns = dialog_buttons()
    check("drei Knöpfe: Abbrechen, Neu starten, Beenden",
          btns == [["cancel", "Abbrechen"], ["alt", "Neu starten"], ["ok", "Beenden"]], btns)
    press("cancel"); time.sleep(0.4)
    check("Abbrechen: kein Aufruf, NiCo bleibt", calls() == [])

    print("2. Beenden speichert den Textmodus")
    type_text("  services.beenden.enable = true;")
    check("ungespeichert markiert", d.execute_script("return Sidebar.hasUnsaved()"))
    d.find_element(By.ID, "quit-btn").click(); time.sleep(0.4)
    press("ok"); time.sleep(1.5)
    check("Datei gespeichert", "services.beenden.enable" in MOD.read_text(encoding="utf-8"))
    check("danach /api/shutdown", calls() == ["/api/shutdown"],
          calls())

    print("3. Speichern schlägt fehl")
    load_text_file()
    type_text("  services.fehler.enable = true;")
    d.execute_script("Sidebar.saveActive = async () => false;")
    d.find_element(By.ID, "quit-btn").click(); time.sleep(0.4)
    press("ok"); time.sleep(0.8)
    txt = d.execute_script("const o = [...document.querySelectorAll('.overlay')].pop(); return o ? o.textContent : ''")
    check("Hinweis erscheint", "konnte nicht gespeichert werden" in txt, txt[:120])
    check("kein /api/shutdown", calls() == [])
    press("ok"); time.sleep(0.3)

    print("4. Neu starten")
    load_text_file()
    type_text("  services.neustart.enable = true;")
    d.find_element(By.ID, "quit-btn").click(); time.sleep(0.4)
    press("alt"); time.sleep(0.8)
    check("Datei gespeichert", "services.neustart.enable" in MOD.read_text(encoding="utf-8"))
    check("/api/restart aufgerufen", calls() == ["/api/restart"])
    # The stub answers with another instance than /api/status: the tab reloads.
    for _ in range(20):
        time.sleep(0.5)
        if d.execute_script("return window._marker === undefined"):
            break
    check("Seite neu geladen, sobald eine andere Instanz antwortet",
          d.execute_script("return window._marker === undefined"))
    check("NiCo wieder da", d.execute_script("return !!document.getElementById('quit-btn')"))

    print("5. Update: speichern, dann Neustart im Monitor anbieten")
    load_text_file()
    type_text("  services.update.enable = true;")
    for _ in range(20):
        if d.execute_script("return !document.getElementById('selfupdate-btn').classList.contains('hidden')"):
            break
        time.sleep(0.3)
    d.execute_script("document.getElementById('selfupdate-btn').click()"); time.sleep(0.6)
    # The release is off: first the question to read the installed programs.
    ask = d.execute_script("""const o = document.querySelector('.flow-dialog');
      return o ? [...o.querySelectorAll('.dialog-actions button')].map(b => b.dataset.flow) : null;""")
    check("Rückfrage: Abbrechen, Einmal, Immer", ask == ["cancel", "once", "always"], ask)
    d.execute_script("document.querySelector(\".flow-dialog [data-flow='once']\").click()"); time.sleep(0.6)
    press("ok")
    for _ in range(40):
        time.sleep(0.3)
        if d.execute_script("return !document.getElementById('rebuild-close-btn').disabled"):
            break
    check("Datei vor dem Update gespeichert", "services.update.enable" in MOD.read_text(encoding="utf-8"))
    st = d.execute_script("""const r = document.getElementById('rebuild-restart-btn'),
                                    c = document.getElementById('rebuild-close-btn');
      return {restart: !r.classList.contains('hidden'), restartText: r.textContent,
              close: c.textContent, closeCls: c.className};""")
    check("„NiCo neu starten“ sichtbar", st["restart"] and st["restartText"] == "NiCo neu starten", st)
    check("Schließen heißt „Später“", st["close"] == "Später", st["close"])
    d.execute_script("document.getElementById('rebuild-restart-btn').click()"); time.sleep(0.8)
    check("Knopf startet neu", calls() == ["/api/restart"],
          calls())

    print("\n" + ("Alle Prüfungen bestanden." if not fails else f"FEHLGESCHLAGEN: {fails}"))
finally:
    d.quit()
sys.exit(1 if fails else 0)
