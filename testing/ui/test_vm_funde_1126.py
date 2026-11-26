"""Browser-Test: Funde aus dem VM-Test vom 2026-11-26 (Martin).

  1. Dry-Run vor dem Sicherungspunkt: Animation mit Laufzeit, Knopf gesperrt,
     solange er läuft (vorher kein Zeichen, erst der zweite Klick meldete ihn)
  2. „Config-Pfad ändern“ mit unverändertem Pfad: schließt nur, richtet
     nichts neu ein, lädt nicht neu
  5. Fensterreihenfolge: ein Fenster, das sich öffnet, liegt über allen
     offenen (die Frage zur geänderten Datei lag hinter dem
     Sicherungspunkt-Fenster)
  6. Textmodus-Konflikt: „Meine Fassung verwerfen“ mit Rückfrage lädt die
     Datei von der Platte in den Editor

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
SETTINGS = xdg / "nico" / "settings.json"
SETTINGS.write_text(json.dumps(
    {"nixos_config_dir": str(cfg), "language": "de", "git_sync": False,
     "git_status_only": False}), encoding="utf-8")
(cfg / "config.json").write_text(json.dumps({"hosts_dir": "hosts"}), encoding="utf-8")

from nico import config_manager, generator
from nico.server import create_app
data = config_manager.get_defaults(); data["_flake_mode"] = False
(cfg / "configuration.nix").write_text(generator.generate_configuration_nix(data), encoding="utf-8")
(cfg / "modules").mkdir()
MOD = cfg / "modules" / "m.nix"
MOD.write_text("{ ... }:\n{\n  # erste Fassung\n}\n", encoding="utf-8")

from selenium import webdriver
from selenium.webdriver.chrome.service import Service

PORT = 8847
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

def top_overlay_at_center():
    """id (or class) of the overlay that receives a click in the middle."""
    return d.execute_script("""
      const el = document.elementFromPoint(innerWidth / 2, innerHeight / 2);
      const o = el && el.closest('.overlay');
      return o ? (o.id || o.className) : null;""")

def close_all():
    d.execute_script("document.querySelectorAll('.overlay').forEach(e => e.classList.add('hidden'));"
                     "document.querySelectorAll('.flow-dialog').forEach(e => e.remove());")

try:
    d.get(f"http://127.0.0.1:{PORT}/")
    time.sleep(4)
    close_all()

    print("5. Das zuletzt geöffnete Fenster liegt oben")
    d.execute_script("openWriteConfirm();")
    time.sleep(0.3)
    check("Sicherungspunkt-Fenster offen und oben", top_overlay_at_center() == "write-overlay",
          top_overlay_at_center())
    d.execute_script("window.__oc = null; askChangedOutside('modules/m.nix', {textMode: true})"
                     ".then(v => window.__oc = v);")
    time.sleep(0.3)
    check("Frage zur geänderten Datei liegt darüber", top_overlay_at_center() == "outside-overlay",
          top_overlay_at_center())
    zs = d.execute_script("return [getComputedStyle(document.getElementById('write-overlay')).zIndex,"
                          " getComputedStyle(document.getElementById('outside-overlay')).zIndex];")
    check("… auch nach z-index", int(zs[1]) > int(zs[0]), zs)
    toast_z = d.execute_script("return getComputedStyle(document.querySelector('.toast-stack') "
                               "|| document.body).zIndex;")
    check("Meldungen (Toasts) bleiben darüber", toast_z in ("auto",) or int(zs[1]) < int(toast_z),
          (zs, toast_z))

    print("\n6. Meine Fassung verwerfen (Rückfrage liegt oben)")
    vis = d.execute_script("return !document.getElementById('outside-discard-btn').classList.contains('hidden');")
    check("Knopf im Textmodus sichtbar", vis)
    d.execute_script("document.getElementById('outside-discard-btn').click()")
    time.sleep(0.5)
    top = top_overlay_at_center()
    check("Rückfrage liegt über der Frage", top not in ("outside-overlay", "write-overlay", None), top)
    # The confirm dialog's OK button: the last button of the topmost window.
    d.execute_script("""const os = [...document.querySelectorAll('.overlay:not(.hidden)')]
        .sort((a, b) => (+getComputedStyle(b).zIndex) - (+getComputedStyle(a).zIndex));
      const btns = [...os[0].querySelectorAll('button')]; btns[btns.length - 1].click();""")
    time.sleep(0.5)
    check("Antwort 'discard'", d.execute_script("return window.__oc") == "discard",
          d.execute_script("return window.__oc"))
    close_all()

    print("\n6b. Im Editor: verwerfen lädt die Platte")
    d.execute_script("Sidebar.selectFile('modules/m.nix', 'm.nix', {force: true});")
    time.sleep(3)
    close_all()
    d.execute_script("""const ta = document.getElementById('raw-file-editor');
      ta.value = '{ ... }:\\n{\\n  # aus NiCo\\n}\\n'; ta.dispatchEvent(new Event('input', {bubbles: true}));""")
    MOD.write_text("{ ... }:\n{\n  # aus Kate\n}\n", encoding="utf-8")
    d.execute_script("window.__res = undefined; Sidebar.saveActive().then(r => window.__res = r);")
    time.sleep(1.5)
    d.execute_script("document.getElementById('outside-discard-btn').click()")
    time.sleep(0.5)
    d.execute_script("""const os = [...document.querySelectorAll('.overlay:not(.hidden)')]
        .sort((a, b) => (+getComputedStyle(b).zIndex) - (+getComputedStyle(a).zIndex));
      const btns = [...os[0].querySelectorAll('button')]; btns[btns.length - 1].click();""")
    time.sleep(2)
    editor = d.execute_script("return document.getElementById('raw-file-editor')?.value || ''")
    check("Editor zeigt die Fassung von der Platte", "aus Kate" in editor and "aus NiCo" not in editor, editor)
    check("Platte unverändert", "aus Kate" in MOD.read_text(), MOD.read_text())
    close_all()

    print("\n1. Dry-Run vor dem Sicherungspunkt: Animation, Knopf gesperrt")
    d.execute_script("""window.__realDry = requestDryRun;
      window.__dryGo = null;
      requestDryRun = () => new Promise(r => { window.__dryGo = r; });
      openWriteConfirm(); document.getElementById('write-dryrun-check').checked = true;
      writeFiles();""")
    time.sleep(2.5)
    st = d.execute_script("""return {text: document.getElementById('write-dryrun-result').textContent,
      disabled: document.getElementById('write-confirm-btn').disabled};""")
    check("Ergebnisfeld zeigt Animation mit Laufzeit", bool(__import__("re").search(r"\d+:\d\d", st["text"])), st)
    check("Knopf gesperrt, solange er läuft", st["disabled"], st)
    d.execute_script("writeFiles();")   # second click: nothing
    d.execute_script("window.__dryGo({ok: false, mode: 'semantic', output: 'ERR_X'});")
    time.sleep(1)
    st = d.execute_script("""return {text: document.getElementById('write-dryrun-result').textContent,
      disabled: document.getElementById('write-confirm-btn').disabled};""")
    check("Danach Ergebnis statt Animation, Knopf frei", st["text"] and ":" not in st["text"][:3]
          and not st["disabled"], st)
    d.execute_script("requestDryRun = window.__realDry;")
    close_all()

    print("\n2. Config-Pfad unverändert übernehmen")
    before = SETTINGS.read_text()
    d.execute_script("""window.__setupCalls = 0; window.__marker = 1;
      const real = window.csrfFetch;
      window.csrfFetch = (url, o) => { if (String(url).includes('/api/setup')) window.__setupCalls++;
                                       return real(url, o); };
      loadSettingsPath().then(() => _changeConfigPathFlow());""")
    time.sleep(1.2)
    check("Feld zeigt den aktuellen Pfad", d.execute_script(
        "return document.querySelector('.flow-dialog input')?.value") == str(cfg),
        d.execute_script("return document.querySelector('.flow-dialog input')?.value"))
    d.execute_script("document.querySelector(`.flow-dialog [data-flow='ok']`).click()")
    time.sleep(1.5)
    st = d.execute_script("return {calls: window.__setupCalls, marker: window.__marker,"
                          " open: !!document.querySelector('.flow-dialog')};")
    check("Kein neues Einrichten (/api/setup nicht aufgerufen)", st["calls"] == 0, st)
    check("Fenster zu, Seite nicht neu geladen", not st["open"] and st["marker"] == 1, st)
    check("Einstellungen unverändert", SETTINGS.read_text() == before)
    print("\n7. Menüs: eines zur Zeit, Einträge einzeilig (de und ru)")
    close_all()
    def menus():
        return d.execute_script("""return ['nixos-dropdown', 'help-dropdown', 'lang-dropdown']
          .filter(id => !document.getElementById(id).classList.contains('hidden'));""")
    d.execute_script("document.getElementById('nixos-btn').click()")
    time.sleep(0.3)
    check("NixOS-Menü offen", menus() == ["nixos-dropdown"], menus())
    d.execute_script("document.getElementById('help-btn').click()")
    time.sleep(0.3)
    check("Hilfe öffnen schließt das NixOS-Menü", menus() == ["help-dropdown"], menus())
    d.execute_script("document.getElementById('lang-current-btn').click()")
    time.sleep(0.3)
    check("Sprache öffnen schließt das Hilfe-Menü", menus() == ["lang-dropdown"], menus())
    d.execute_script("document.getElementById('nixos-btn').click()")
    time.sleep(0.3)
    check("NixOS öffnen schließt das Sprachmenü", menus() == ["nixos-dropdown"], menus())
    d.execute_script("document.body.click()")
    time.sleep(0.3)
    check("Klick daneben schließt alles", menus() == [], menus())
    for lang in ("de", "ru", "fr"):
        d.execute_script("loadLang(arguments[0]).then(() => applyTranslations());", lang)
        time.sleep(0.8)
        wrapped = d.execute_script("""
          const out = [];
          for (const id of ['nixos-dropdown', 'help-dropdown']) {
            const m = document.getElementById(id); m.classList.remove('hidden');
            for (const b of m.querySelectorAll(':scope > button:not(.hidden)')) {
              const r = document.createRange(); r.selectNodeContents(b);
              const lines = new Set([...r.getClientRects()].map(x => Math.round(x.top)));
              if (lines.size > 1) out.push(b.textContent.trim());
            }
            m.classList.add('hidden');
          }
          return out;""")
        check(f"{lang}: alle Menüeinträge einzeilig", wrapped == [], wrapped)
    d.execute_script("loadLang('de').then(() => applyTranslations());")
finally:
    d.quit()

print()
print("ALLE TESTS BESTANDEN" if not fails else f"{len(fails)} FEHLGESCHLAGEN: {fails}")
sys.exit(1 if fails else 0)
