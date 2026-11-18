"""Browser-Test: außerhalb von NiCo geänderte Datei – erst fragen, dann umbauen.

Fall A, Entscheidung 2026-11-18 (Martin): Stimmt die Prüfsumme einer Datei
nicht, die ein Formular schreibt, baute NiCo sie bisher still um. Jetzt
erscheint vor dem Formular ein Dialog: Umbauen (Sicherungspunkt vorher),
Unterschiede ansehen, Im Raw-Modus öffnen. Ohne Antwort schreibt das Panel
nichts. Das Gegenstück ohne Browser ist
`testing/routes/test_outside_restructure.py`.

Die Wegwerf-Config und XDG_CONFIG_HOME legt der Test selbst an.
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
(cfg / "config.json").write_text(json.dumps({"hosts_dir": "hosts"}), encoding="utf-8")

from nico import config_manager, generator, git_manager
from nico.brix import extract_brick_blocks
from nico.server import create_app

data = config_manager.get_defaults()
data["_flake_mode"] = False
(cfg / "configuration.nix").write_text(generator.generate_configuration_nix(data), encoding="utf-8")
(cfg / "hosts" / "h1").mkdir(parents=True)
(cfg / "hosts" / "h1" / "default.nix").write_text(
    generator.generate_host_nix({"hostname": "h1"}, "h1"), encoding="utf-8")
git_manager.init_repo(str(cfg))
git_manager.auto_commit(str(cfg), label="start")

def edit_outside(rel, line):
    t = (cfg / rel).read_text(encoding="utf-8")
    i = t.rstrip().rfind("}")
    (cfg / rel).write_text(t[:i] + line + t[i:], encoding="utf-8")

from selenium import webdriver
from selenium.webdriver.chrome.service import Service

PORT = 8815
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

def dialog_open():
    return d.execute_script(
        "return !document.getElementById('restructure-overlay').classList.contains('hidden');")

def click(id_):
    d.execute_script(f"document.getElementById('{id_}').click();")

try:
    d.get(f"http://127.0.0.1:{PORT}/")
    time.sleep(4)
    check("Start ohne Dialog (von NiCo geschrieben)", not dialog_open())

    print("\n1. Host-Datei außen geändert → Umbauen")
    HOST = "hosts/h1/default.nix"
    edit_outside(HOST, "  # von Hand\n  services.uivonhand.enable = true;\n")
    before = (cfg / HOST).read_text(encoding="utf-8")
    d.execute_script("Sidebar.selectFile(arguments[0], 'default.nix', {force: true});", HOST)
    time.sleep(2)
    check("Dialog erscheint", dialog_open())
    check("… nennt die Datei", d.execute_script(
        "return document.getElementById('restructure-file').textContent;") == HOST)
    check("solange gefragt wird: Datei unverändert",
          (cfg / HOST).read_text(encoding="utf-8") == before)
    click("restructure-diff-btn")
    time.sleep(1.5)
    diff = d.execute_script("return document.getElementById('restructure-diff').innerText;")
    check("Unterschiede zeigen den neuen Nix-Brick", "brick:" in diff, diff[:200])
    check("Dialog bleibt dabei offen", dialog_open())
    click("restructure-ok-btn")
    time.sleep(2.5)
    check("Dialog geschlossen", not dialog_open())
    text = (cfg / HOST).read_text(encoding="utf-8")
    check("Einstellung steht im Nix-Brick", any(
        "services.uivonhand.enable" in b["text"] for b in extract_brick_blocks(text).values()))
    check("Prüfsumme stimmt", generator.check_version_hash(text) == "ok")
    log = subprocess.run(["git", "-C", str(cfg), "log", "--format=%s", "-5"],
                         capture_output=True, text=True).stdout
    check("Sicherungspunkt vor dem Umbau", f"NiCo: backup before restructuring {HOST}" in log, log)
    check("Formular ist offen", d.execute_script(
        "return !document.getElementById('config-form').classList.contains('hidden');"))

    print("\n2. configuration.nix außen geändert → Im Raw-Modus öffnen")
    CO = "configuration.nix"
    edit_outside(CO, "  services.uiraw.enable = true;\n")
    before = (cfg / CO).read_text(encoding="utf-8")
    d.execute_script("loadConfig();")
    time.sleep(2)
    check("Dialog erscheint beim Laden", dialog_open())
    click("restructure-raw-btn")
    time.sleep(2.5)
    check("Dialog geschlossen", not dialog_open())
    text = (cfg / CO).read_text(encoding="utf-8")
    strip = lambda t: [l for l in t.splitlines() if not l.startswith("# nico-version:")]
    check("Datei bis auf die Versionszeile unverändert", strip(text) == strip(before))
    check("… mit #r", "#r" in text.splitlines()[1] if len(text.splitlines()) > 1 else False)
    raw = d.execute_script("return document.getElementById('raw-file-editor')?.value || '';")
    check("Raw-Editor zeigt die Einstellung", "services.uiraw.enable" in raw)
    d.execute_async_script("const cb = arguments[0]; _autoSave().then(cb);")
    time.sleep(1)
    check("stilles Speichern lässt die Datei stehen",
          strip((cfg / CO).read_text(encoding="utf-8")) == strip(before))

    print("\n3. Raw-Änderung, dann aufs Panel: erst fragen (Martins Punkt)")
    ok = d.execute_script("""
      const ta = document.getElementById('raw-file-editor');
      if (!ta) return false;
      const v = ta.value, i = v.trimEnd().lastIndexOf('}');
      ta.value = v.slice(0, i) + '.\\n' + v.slice(i);
      return true;""")
    check("Punkt im Raw-Editor eingetragen (ungespeichert)", ok)
    d.execute_script("document.getElementById('panel-toggle-check').click();")
    time.sleep(2.5)
    check("Dialog erscheint beim Umschalten", dialog_open())
    check("… dritter Knopf heißt „Im Raw-Modus bleiben“", d.execute_script(
        "return document.getElementById('restructure-raw-btn').textContent;") == "Im Raw-Modus bleiben")
    check("… ohne Satz „außerhalb geändert“ (Änderung kam aus dem Raw-Editor)", d.execute_script(
        "return document.getElementById('restructure-outside-note').classList.contains('hidden');"))
    check("ungespeicherte Raw-Änderung wurde vorher gespeichert",
          "." in [l.strip() for l in (cfg / CO).read_text(encoding="utf-8").splitlines()])
    click("restructure-raw-btn")
    time.sleep(1.5)
    check("Im Raw-Modus bleiben: Dialog zu", not dialog_open())
    mode = d.execute_script("return document.getElementById('panel-toggle-btn')?.dataset.mode;")
    check("… Schalter steht weiter auf Raw", mode == "r", mode)
    check("… Punkt noch in der Datei",
          "." in [l.strip() for l in (cfg / CO).read_text(encoding="utf-8").splitlines()])
    d.execute_script("document.getElementById('panel-toggle-check').click();")
    time.sleep(2.5)
    check("zweiter Versuch: Dialog wieder da", dialog_open())
    click("restructure-ok-btn")
    time.sleep(3)
    check("Umbauen: Dialog zu", not dialog_open())
    text = (cfg / CO).read_text(encoding="utf-8")
    mode = d.execute_script("return document.getElementById('panel-toggle-btn')?.dataset.mode;")
    check("… Panel-Modus", mode == "p", mode)
    check("… Punkt weg", "." not in [l.strip() for l in text.splitlines()])
    check("… Einstellung aus Teil 2 als Nix-Brick", any(
        "services.uiraw.enable" in b["text"] for b in extract_brick_blocks(text).values()))

    print("\n4. Raw-Modus, außen geändert: nur eine Meldung")
    HOST = "hosts/h1/default.nix"
    r = d.execute_async_script("""
      const cb = arguments[arguments.length - 1];
      csrfFetch('/api/file/panel-mode', {method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({path: arguments[0], mode: 'r'})}).then(r => r.json()).then(cb);""", HOST)
    check("Host-Datei im Raw-Modus", bool(r.get("ok")), r)
    edit_outside(HOST, "  services.rawnotiz.enable = true;\n")
    d.execute_script("Sidebar.selectFile(arguments[0], 'default.nix', {force: true});", HOST)
    time.sleep(2)
    notice = d.execute_script("return !!document.getElementById('outside-notice-overlay');")
    check("Meldung erscheint", notice)
    check("… kein Umbau-Dialog", not dialog_open())
    d.execute_script("document.getElementById('outside-notice-ok')?.click();")
    time.sleep(1.5)
    raw = d.execute_script("return document.getElementById('raw-file-editor')?.value || '';")
    check("nach OK: Raw-Editor zeigt die Änderung", "services.rawnotiz.enable" in raw)
finally:
    d.quit()

print()
print("ALLE PRÜFUNGEN BESTANDEN" if not fails else f"FEHLGESCHLAGEN: {len(fails)}")
sys.exit(0 if not fails else 1)
