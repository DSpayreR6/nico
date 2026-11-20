"""Browser-Test: eine Suche für jede Datei, die NiCo anzeigt.

VM-Test 2026-11-19, A; Zuschnitt Martin 2026-11-20: Es gab zwei Suchen mit
eigenem Code. Strg+F durchsuchte im Raw-Modus und bei flake.nix/Home Manager
eine veraltete Vorschau (zählte Treffer, die niemand sah). Jetzt ein Modul
(search.js), ein Einstieg (Strg+F und Werkzeug-Menü), ein Suchkern über den
Text dessen, was gerade angezeigt wird.

Für jede Anzeigeart: Trefferzahl stimmt, Treffer sichtbar – in Code-Ansichten
markiert, im Raw-Editor ausgewählt (ohne dass das Suchfeld den Fokus
verliert). Dazu: Weiterspringen, Dateiwechsel bei offener Suche, Schließen.

Die Wegwerf-Config und XDG_CONFIG_HOME legt der Test selbst an.
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

from nico import config_manager, generator, hm_generator
from nico.server import create_app

data = config_manager.get_defaults()
data.update({"flakes": True, "hostname": "h1", "flake_hosts": [{"name": "h1"}]})
config_manager.save_config(str(cfg), data)
(cfg / "configuration.nix").write_text(generator.generate_configuration_nix(data), encoding="utf-8")
(cfg / "flake.nix").write_text(generator.generate_flake_nix(data, nixos_dir=str(cfg)), encoding="utf-8")
config_manager.save_host_config(str(cfg), "h1", {})
(cfg / "home").mkdir()
(cfg / "home" / "martin.nix").write_text(
    hm_generator.generate_home_nix({"username": "martin", "home_dir": "/home/martin"}), encoding="utf-8")
(cfg / "modules").mkdir()
(cfg / "modules" / "extra.nix").write_text(
    "{ ... }: {\n  # wortsuche eins\n  # wortsuche zwei\n  # wortsuche drei\n}\n", encoding="utf-8")
(cfg / "flake.lock").write_text('{\n  "nodes": { "lockwort": {} },\n  "version": 7\n}\n', encoding="utf-8")

from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.common.action_chains import ActionChains

PORT = 8823
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

def open_file(rel):
    d.execute_script("Sidebar.selectFile(arguments[0], arguments[0].split('/').pop(), {force: true});", rel)
    time.sleep(3)

def search(q):
    d.execute_script("""
      Search.open();
      const i = document.getElementById('search-input');
      i.value = arguments[0];
      i.dispatchEvent(new Event('input', {bubbles: true}));""", q)
    time.sleep(0.6)
    return state()

def state():
    return d.execute_script("""
      const ta = document.querySelector('#raw-file-view textarea');
      const sel = ta ? ta.value.substring(ta.selectionStart, ta.selectionEnd) : '';
      return {count: document.getElementById('search-count').textContent,
              marks: [...document.querySelectorAll('mark.search-match')].filter(m => m.offsetParent !== null).length,
              active: document.querySelectorAll('mark.search-match-active').length,
              selected: sel,
              focusInSearch: document.activeElement?.id === 'search-input'};""")

def expect_hits(name, rel, q, n, kind):
    print(f"\n{name}: {rel}")
    open_file(rel)
    st = search(q)
    check(f"„{q}“: {n} Treffer gezählt", st["count"] == f"1/{n}", st)
    if kind == "code":
        check("sichtbar markiert", st["marks"] >= n and st["active"] >= 1, st)
    else:
        check("im Raw-Editor ausgewählt", st["selected"].lower() == q.lower(), st)
        check("Fokus bleibt im Suchfeld", st["focusInSearch"], st)
    d.execute_script("Search.close();")

try:
    d.get(f"http://127.0.0.1:{PORT}/")
    time.sleep(4)
    d.execute_script("document.querySelectorAll('.overlay').forEach(e => e.classList.add('hidden'))")

    expect_hits("Panel-Vorschau configuration.nix", "configuration.nix", "experimental-features", 1, "code")
    expect_hits("Panel-Vorschau flake.nix", "flake.nix", "nixosSystem", 1, "code")
    expect_hits("Panel-Vorschau Home Manager", "home/martin.nix", "home.username", 1, "code")
    expect_hits("Raw-Editor nd-Datei", "modules/extra.nix", "wortsuche", 3, "text")
    expect_hits("flake.lock-Ansicht", "flake.lock", "lockwort", 1, "code")

    print("\nWeiterspringen im Raw-Editor")
    open_file("modules/extra.nix")
    search("wortsuche")
    d.execute_script("Search.step(1);"); time.sleep(0.3)
    st = state()
    check("zweiter Treffer", st["count"] == "2/3", st)
    sel = d.execute_script("const ta=document.querySelector('#raw-file-view textarea');"
                           "return ta.value.substring(ta.selectionEnd, ta.selectionEnd + 5);")
    check("… Auswahl beim zweiten Wort", sel == " zwei", sel)
    d.execute_script("Search.step(-1); Search.step(-1);"); time.sleep(0.3)
    check("rückwärts über den Anfang: letzter", state()["count"] == "3/3", state())

    print("\nDateiwechsel bei offener Suche")
    open_file("configuration.nix")
    st = state()
    check("Suche folgt der neuen Anzeige (kein alter Treffer)", st["count"] == "Keine Treffer" and st["marks"] == 0, st)

    print("\nStrg+F öffnet dieselbe Suche, Esc schließt")
    d.execute_script("Search.close();")
    ActionChains(d).key_down(Keys.CONTROL).send_keys("f").key_up(Keys.CONTROL).perform()
    time.sleep(0.4)
    check("Suchleiste offen", d.execute_script("return Search.isOpen();"))
    d.find_element("id", "search-input").send_keys(Keys.ESCAPE)
    time.sleep(0.3)
    check("Esc schließt, keine Markierung bleibt", not d.execute_script("return Search.isOpen();")
          and state()["marks"] == 0)
    check("Werkzeug-Menü „Im Code suchen“ öffnet dieselbe Leiste", d.execute_script(
        "document.getElementById('tool-code-search').click(); return Search.isOpen();"))
finally:
    d.quit()

print()
if fails:
    print(f"{len(fails)} FEHLER")
    sys.exit(1)
print("Alle Prüfungen bestanden.")
