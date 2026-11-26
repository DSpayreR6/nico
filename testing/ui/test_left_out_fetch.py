"""Browser-Test: Liste der nicht erfassten Dateien – Fehler halten an, Importquelle.

Codex-Nachprüfung 2026-11-26: Ein abgewiesener Fetch und eine Fehlerantwort
ohne `left` ergaben eine leere Liste, confirmLeftOut() gab true – die Aktion
lief ohne Warnliste weiter. Jetzt hält sie an und bietet „Erneut versuchen“.
Dazu nennt der Verzeichnis-Import vorher die Links nach außen in der Quelle
(Entscheidung Martin 2026-11-26).

Geprüft:
  1. confirmLeftOut bei Netzwerkfehler und bei HTTP 500: Fenster mit Fehler
     und „Erneut versuchen“; Abbrechen → false (kein Umzug, kein Export)
  2. Erneut versuchen nach dem Fehler mit `left: []` → true
  3. Gefüllte Liste → Fenster mit der Liste, „Trotzdem fortfahren“ → true
  4. Verzeichnis-Import: Quelle mit Link nach außen → erst die Liste, dann
     die Frage Panel/Text; Fehler beim Laden der Liste → Erneut versuchen
"""
import json, os, pathlib, sys, tempfile, threading, time
ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

xdg = pathlib.Path(tempfile.mkdtemp(prefix="nico-xdg-"))
cfg = pathlib.Path(tempfile.mkdtemp(prefix="nico-cfg-"))
work = pathlib.Path(tempfile.mkdtemp(prefix="nico-work-"))
os.environ["XDG_CONFIG_HOME"] = str(xdg)
os.environ["XDG_CACHE_HOME"] = str(xdg / "cache")
(xdg / "nico").mkdir(parents=True)
(xdg / "nico" / "settings.json").write_text(json.dumps(
    {"nixos_config_dir": str(cfg), "language": "de", "git_sync": False,
     "git_status_only": False}), encoding="utf-8")
(cfg / "config.json").write_text(json.dumps({"hosts_dir": "hosts"}), encoding="utf-8")

from nico import config_manager, generator
from nico.server import create_app
data = config_manager.get_defaults(); data["_flake_mode"] = False
(cfg / "configuration.nix").write_text(generator.generate_configuration_nix(data), encoding="utf-8")

ext = work / "aussen"
ext.mkdir()
(ext / "geheim.nix").write_text("{ }\n", encoding="utf-8")
src = work / "quelle"
(src / "modules").mkdir(parents=True)
(src / "configuration.nix").write_text(
    "{ config, pkgs, ... }:\n{\n  networking.hostName = \"quelle\";\n  system.stateVersion = \"25.05\";\n}\n",
    encoding="utf-8")
os.symlink(ext / "geheim.nix", src / "modules/extern.nix")

from selenium import webdriver
from selenium.webdriver.chrome.service import Service

PORT = 8833
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
    return d.execute_script("""const o = document.querySelector('.flow-dialog');
      if (!o) return null;
      return { text: o.querySelector('.flow-body').textContent.replace(/\\s+/g, ' ').trim(),
               error: o.querySelector('.flow-error')?.textContent || '',
               buttons: [...o.querySelectorAll('.dialog-actions button')].map(b => b.dataset.flow) };""")

def press(value):
    d.execute_script("document.querySelector(`.flow-dialog [data-flow='${arguments[0]}']`).click()", value)

def wait_flow(cond, secs=10):
    for _ in range(int(secs / 0.2)):
        st = flow()
        if cond(st):
            return st
        time.sleep(0.2)
    return flow()

def wait_result(secs=10):
    for _ in range(int(secs / 0.2)):
        r = d.execute_script("return window._res;")
        if r is not None:
            return r
        time.sleep(0.2)
    return "timeout"

# fetch for the left-out lists: 'net' rejects, '500' answers an error,
# 'empty' answers left: [], null passes through.
FAKE = """
window._mode = null;
if (!window._origFetch) window._origFetch = window.fetch;
window.fetch = (url, o) => {
  if (String(url).includes('left-out') || String(url).includes('source-left')) {
    if (window._mode === 'net') return Promise.reject(new TypeError('Failed to fetch'));
    if (window._mode === '500') return Promise.resolve(new Response(
      JSON.stringify({error: 'ERR_X'}), {status: 500, headers: {'Content-Type': 'application/json'}}));
    if (window._mode === 'empty') return Promise.resolve(new Response(
      JSON.stringify({left: []}), {status: 200, headers: {'Content-Type': 'application/json'}}));
    if (window._mode === 'full') return Promise.resolve(new Response(
      JSON.stringify({left: [{file: 'modules/x.nix', kind: 'link_out', target: '/elsewhere/x.nix', line: 0}]}),
      {status: 200, headers: {'Content-Type': 'application/json'}}));
  }
  return window._origFetch(url, o);
};
"""

def start_confirm():
    d.execute_script("window._res = null; confirmLeftOut(null, {all: true, move: true}).then(v => window._res = String(v));")

try:
    d.get(f"http://127.0.0.1:{PORT}/")
    time.sleep(4)
    d.execute_script("document.querySelectorAll('.overlay:not(.hidden):not(#admin-overlay) .btn-primary').forEach(b => b.click());")
    time.sleep(1)
    d.execute_script(FAKE)

    for mode, label in (("net", "Netzwerkfehler"), ("500", "HTTP 500")):
        print(f"1. confirmLeftOut bei {label}")
        d.execute_script("window._mode = arguments[0];", mode)
        start_confirm()
        st = wait_flow(lambda s: s and s["error"])
        check("Fenster mit Fehlermeldung und Erneut versuchen",
              st and "nicht geladen" in st["error"] and st["buttons"] == ["cancel", "retry"], st)
        check("… die Aktion wartet (kein Ergebnis)", d.execute_script("return window._res;") is None)
        press("cancel")
        check("Abbrechen → false, die Aktion startet nicht", wait_result() == "false")

    print("2. Erneut versuchen, dann leere Liste")
    d.execute_script("window._mode = 'net';")
    start_confirm()
    wait_flow(lambda s: s and s["error"])
    d.execute_script("window._mode = 'empty';")
    press("retry")
    check("Leere Liste nach Wiederholung → true", wait_result() == "true")
    check("… kein Fenster mehr offen", wait_flow(lambda s: s is None, 3) is None)

    print("3. Gefüllte Liste")
    d.execute_script("window._mode = 'full';")
    start_confirm()
    st = wait_flow(lambda s: s and "modules/x.nix" in s["text"])
    check("Liste erscheint vor dem Start", st and st["buttons"] == ["cancel", "ok"], st)
    press("ok")
    check("Trotzdem fortfahren → true", wait_result() == "true")

    print("4. Verzeichnis-Import mit Link nach außen in der Quelle")
    d.execute_script("window._mode = null;")
    d.execute_script("""openAdmin(); _switchAdminTab('administration');
      document.querySelectorAll('[data-admin-panel=administration] .settings-group')
        .forEach(g => g.classList.remove('collapsed'));""")
    time.sleep(0.5)
    d.execute_script("document.getElementById('admin-import-btn').click()")
    wait_flow(lambda s: s and "ok" in s["buttons"])
    d.execute_script("""const i = document.querySelector('.flow-dialog input');
      i.value = arguments[0]; i.dispatchEvent(new Event('input', {bubbles: true}));""", str(src))
    press("ok")
    st = wait_flow(lambda s: s and "modules/extern.nix" in s["text"])
    check("Zuerst die Liste mit modules/extern.nix", st and "modules/extern.nix" in st["text"]
          and st["buttons"] == ["cancel", "ok"], st)
    press("ok")
    st = wait_flow(lambda s: s and "modules/extern.nix" not in s["text"])
    check("Danach die Frage Panel/Text", st and len(st["buttons"]) >= 2
          and "modules/extern.nix" not in st["text"], st)
    press("cancel")
    wait_flow(lambda s: s is None, 3)
    check("Abgebrochen: nichts importiert", "quelle" not in (cfg / "configuration.nix").read_text())

    print("4b. Liste der Quelle nicht ladbar")
    d.execute_script("window._mode = 'net';")
    d.execute_script("document.getElementById('admin-import-btn').click()")
    wait_flow(lambda s: s and "ok" in s["buttons"])
    d.execute_script("""const i = document.querySelector('.flow-dialog input');
      i.value = arguments[0]; i.dispatchEvent(new Event('input', {bubbles: true}));""", str(src))
    press("ok")
    st = wait_flow(lambda s: s and s["error"])
    check("Fehler mit Erneut versuchen statt Import", st and st["buttons"] == ["cancel", "retry"], st)
    press("cancel")
    check("Fenster zu, nichts importiert", wait_flow(lambda s: s is None, 3) is None
          and "quelle" not in (cfg / "configuration.nix").read_text())
finally:
    d.quit()

print()
print("ALLE TESTS BESTANDEN" if not fails else f"{len(fails)} FEHLGESCHLAGEN: {fails}")
sys.exit(1 if fails else 0)
