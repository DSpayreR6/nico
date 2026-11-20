"""Browser-Test: Warnung links bei Raw-only-Dateien, Neuprüfung nach dem Speichern.

VM-Test 2026-11-19, F und G (Zuschnitt Martin 2026-11-20): Eine Datei, die
NiCo nur im Raw-Editor öffnet, bekommt links eine Warnung mit Grund – bei
einem Syntaxfehler mit der Zeile, wie der Raw-Editor sie zählt (ohne NiCos
Kopfzeilen). Nach dem Raw-Speichern prüft NiCo neu: wieder lesbar →
Warnung weg, Datei bleibt Raw (#r), Panel-Schalter erscheint auf „Panel aus“;
wieder kaputt → Warnung zurück, Schalter weg.

Die Wegwerf-Config und XDG_CONFIG_HOME legt der Test selbst an.
"""
import json, os, pathlib, shutil, sys, tempfile, threading, time
ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

if not shutil.which("nix-instantiate"):
    print("nix-instantiate fehlt – Test übersprungen")
    sys.exit(0)

xdg = pathlib.Path(tempfile.mkdtemp(prefix="nico-xdg-"))
cfg = pathlib.Path(tempfile.mkdtemp(prefix="nico-cfg-"))
os.environ["XDG_CONFIG_HOME"] = str(xdg)
os.environ["XDG_CACHE_HOME"] = str(xdg / "cache")
(xdg / "nico").mkdir(parents=True)
(xdg / "nico" / "settings.json").write_text(json.dumps(
    {"nixos_config_dir": str(cfg), "language": "de", "git_sync": False,
     "git_status_only": False}), encoding="utf-8")
(cfg / "config.json").write_text(json.dumps({"hosts_dir": "hosts"}), encoding="utf-8")

from nico import config_manager, generator
from nico.server import create_app

data = config_manager.get_defaults()
data["_flake_mode"] = False
good = generator.generate_configuration_nix(data)
lines = good.rstrip("\n").split("\n")
close = max(i for i, l in enumerate(lines) if l.strip() == "}")
broken = "\n".join(lines[:close] + [
    "  # <brick: TAIL / #1 kaputt>",
    "  services.foo = {",
    "    enable = true;",
    "  };.",
    "  # </brick: kaputt>",
] + lines[close:]) + "\n"
(cfg / "configuration.nix").write_text(broken, encoding="utf-8")

from selenium import webdriver
from selenium.webdriver.chrome.service import Service

PORT = 8818
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

def ui():
    return d.execute_script("""
      const w = document.getElementById('unmappable-hint');
      const tg = document.getElementById('panel-toggle');
      const ta = document.getElementById('raw-file-editor');
      return {warn: !!w && !w.classList.contains('hidden') &&
                    !document.getElementById('raw-panel').classList.contains('hidden'),
              text: document.getElementById('unmappable-text')?.textContent || '',
              detail: document.getElementById('unmappable-detail')?.textContent || '',
              toggle: !!tg && !tg.classList.contains('hidden'),
              mode: document.getElementById('panel-toggle-btn')?.dataset.mode,
              editorLine: ta ? ta.value.split('\\n').indexOf('  };.') + 1 : -1};""")

def set_raw(old, new):
    d.execute_script("""
      const ta = document.getElementById('raw-file-editor');
      ta.value = ta.value.replace(arguments[0], arguments[1]);
      ta.dispatchEvent(new Event('input', {bubbles: true}));""", old, new)
    time.sleep(0.3)
    d.execute_script("document.getElementById('save-tab-btn').click();")
    time.sleep(3)

try:
    d.get(f"http://127.0.0.1:{PORT}/")
    time.sleep(4)
    d.execute_script("Sidebar.selectFile('configuration.nix', 'configuration.nix', {force: true});")
    time.sleep(3)

    print("1. Datei mit Syntaxfehler öffnen")
    st = ui()
    check("Warnung links sichtbar", st["warn"], st)
    check("Raw-Editor offen", st["editorLine"] > 0, st)
    check(f"Zeile wie im Editor ({st['editorLine']})", f"Zeile {st['editorLine']} " in st["text"], st["text"])
    check("„nicht baubar“ genannt", "nicht baubar" in st["text"], st["text"])
    check("Nix-Meldung darunter", "syntax error" in st["detail"], st["detail"])
    check("kein Panel-Schalter", not st["toggle"], st)

    print("\n2. Im Raw beheben und speichern")
    set_raw("  };.", "  };")
    st = ui()
    check("Warnung weg", not st["warn"] or "" == st["text"] or not st["warn"], st)
    check("Panel-Schalter da", st["toggle"], st)
    check("Schalter auf „Panel aus“", st["mode"] == "r", st)
    check("Raw-Editor bleibt offen", d.execute_script("return !!document.getElementById('raw-file-editor');"))
    text = (cfg / "configuration.nix").read_text(encoding="utf-8")
    check("Datei behoben", "};." not in text)

    print("\n3. Wieder kaputt speichern")
    set_raw("    enable = true;\n  };", "    enable = true;\n  };.")
    st = ui()
    check("Warnung wieder da", st["warn"], st)
    check("Schalter wieder weg", not st["toggle"], st)
    check("Zeile stimmt wieder", f"Zeile {st['editorLine']} " in st["text"], (st["text"], st["editorLine"]))
    # leave clean for quit
    d.execute_script("const ta=document.getElementById('raw-file-editor'); "
                     "ta.dispatchEvent(new Event('input',{bubbles:true}));")
finally:
    d.quit()

print()
if fails:
    print(f"{len(fails)} FEHLER")
    sys.exit(1)
print("Alle Prüfungen bestanden.")
