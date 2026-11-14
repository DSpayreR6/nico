"""Browser-Test: Pakete deaktivieren, AppImage-Bibliotheken, Firefox-Felder.

Prüft, was sich nur im echten Browser zeigt (alles vom 2026-11-14):

  * ein deaktiviertes Paket steht ausgegraut in der Paketliste, sein Schalter
    ist aus
  * der Schalter wirkt sofort in der Vorschau: das Paket wandert zwischen
    Paketliste und dem Nix-Brick `nico-inactive-packages`
  * der Nix-Brick ist sichtbar, als verwaltet gekennzeichnet, und sein Menü
    bietet nur „Löschen"
  * Speichern schreibt den Zustand in die Datei, Neuladen zeigt ihn wieder
  * die Suche legt ein deaktiviertes Paket nicht doppelt an (addPackage)
  * das Feld „Zusätzliche Bibliotheken" wird geladen, erscheint nur bei
    aktivem AppImage-Support und entfernt ungültige Namen
  * die Firefox-Felder „Sprachpakete" und „Einstellungen" werden beim Laden
    gefüllt – vorher blieben sie leer, und das nächste Speichern strich
    beides aus der Datei

Legt sich seine Wegwerf-Config selbst an (eigenes XDG_CONFIG_HOME). Aufruf
sonst wie in testing/ui/README.md.
"""
import json, os, pathlib, sys, tempfile, threading, time

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

xdg = pathlib.Path(tempfile.mkdtemp(prefix="nico-xdg-"))
cfg = pathlib.Path(tempfile.mkdtemp(prefix="nico-cfg-"))
os.environ["XDG_CONFIG_HOME"] = str(xdg)
os.environ["XDG_CACHE_HOME"] = str(xdg / "cache")
(xdg / "nico").mkdir(parents=True)
(xdg / "nico" / "settings.json").write_text(
    json.dumps({"nixos_config_dir": str(cfg), "language": "de", "git_sync": False}),
    encoding="utf-8")

from nico import brix, config_manager, generator          # noqa: E402
from nico.server import create_app                        # noqa: E402
from selenium import webdriver                            # noqa: E402
from selenium.webdriver.common.by import By               # noqa: E402
from selenium.webdriver.chrome.service import Service     # noqa: E402

NAME = brix.INACTIVE_PKG_BRICK
CONF = cfg / "configuration.nix"
start = config_manager.get_defaults()
start.update({
    "packages": [{"attr": "git", "enabled": True}, {"attr": "htop", "enabled": True},
                 {"attr": "codex", "enabled": False}],
    brix.PKG_INACTIVE_KNOWN: True,
    "appimage": True, "appimage_libs": "libgpg-error",
    "firefox": True, "firefox_lang_packs": "de, en-US",
    "firefox_prefs": '"intl.locale.requested" = "de-DE,de";',
})
CONF.write_text(generator.generate_configuration_nix(start), encoding="utf-8")

PORT = 8811
app = create_app()
threading.Thread(target=lambda: app.run(port=PORT, threaded=True), daemon=True).start()
time.sleep(2)

opts = webdriver.ChromeOptions()
for f in ("--headless=new", "--no-sandbox", "--disable-dev-shm-usage", "--window-size=1600,1200"):
    opts.add_argument(f)
opts.binary_location = os.environ["CHROME_BIN"]
d = webdriver.Chrome(service=Service(os.environ["CHROMEDRIVER"]), options=opts)
d.set_script_timeout(20)

fails = []
def check(label, cond):
    try:
        ok = bool(cond() if callable(cond) else cond)
        note = ""
    except Exception as exc:
        ok, note = False, f"  ({type(exc).__name__}: {exc})"[:160]
    print(("  OK   " if ok else "  FAIL ") + label + note)
    if not ok:
        fails.append(label)

def js(script, *args):
    return d.execute_script(script, *args)

def dismiss_dialogs():
    js("""document.querySelectorAll('.overlay:not(.hidden)').forEach(o => {
            if (o.id) o.classList.add('hidden'); else o.remove(); });""")

def item(attr):
    return js("""const i = [...document.querySelectorAll('#packages-list .pkg-item')]
                   .find(e => e.dataset.attr === arguments[0]);
                 return i ? {disabled: i.classList.contains('disabled'),
                             on: i.querySelector('.pkg-toggle').classList.contains('active')} : null;""", attr)

def toggle(attr):
    js("""[...document.querySelectorAll('#packages-list .pkg-item')]
            .find(e => e.dataset.attr === arguments[0]).querySelector('.pkg-toggle').click();""", attr)
    time.sleep(2)            # the preview is debounced

def preview():
    return js("return document.getElementById('preview-configuration').dataset.sourceCode || '';")

def in_list(text, attr):
    import re
    m = re.search(r"environment\.systemPackages = with pkgs; \[(.*?)\];", text, re.S)
    return bool(m) and attr in m.group(1).split()

def in_brick(text, attr):
    return attr in brix.inactive_package_names(brix.extract_brick_blocks(text))

def save():
    return d.execute_async_script("const done = arguments[0]; saveConfig().then(done, () => done(false));")

def val(element_id):
    return d.find_element(By.ID, element_id).get_attribute("value")

def hidden(element_id):
    return "hidden" in (d.find_element(By.ID, element_id).get_attribute("class") or "")

def load():
    d.get(f"http://127.0.0.1:{PORT}/")
    time.sleep(5)
    dismiss_dialogs()

try:
    load()

    print("\n1. Paketliste nach dem Laden")
    check("drei Pakete", lambda: js("return document.querySelectorAll('#packages-list .pkg-item').length") == 3)
    check("git aktiv", lambda: item("git") == {"disabled": False, "on": True})
    check("codex ausgegraut, Schalter aus", lambda: item("codex") == {"disabled": True, "on": False})

    print("\n2. Der Nix-Brick in der Vorschau")
    brick = lambda: d.find_element(By.CSS_SELECTOR, f'.code-brix[data-brick-name="{NAME}"]')
    check("sichtbar", lambda: bool(brick()))
    check("als verwaltet gekennzeichnet",
          lambda: "von NiCo verwaltet" in brick().find_element(By.CSS_SELECTOR, ".code-brix-label").get_attribute("textContent"))
    check("Menü bietet genau einen Eintrag",
          lambda: len(brick().find_elements(By.CSS_SELECTOR, ".brix-menu-item")) == 1)
    check("und der ist Löschen",
          lambda: brick().find_element(By.CSS_SELECTOR, ".brix-menu-item").get_attribute("textContent") == "Löschen")
    js("arguments[0].querySelector('.code-with-lines').click()", brick())
    time.sleep(0.5)
    check("Klick auf den Inhalt öffnet keinen Editor",
          lambda: not brick().find_elements(By.CSS_SELECTOR, ".brix-inline-editor"))
    other = lambda: d.find_elements(By.CSS_SELECTOR, f'.code-brix:not([data-brick-name="{NAME}"])')
    check("codex steht im Brick, nicht in der Liste",
          lambda: in_brick(preview(), "codex") and not in_list(preview(), "codex"))

    print("\n3. Schalter wirkt sofort in der Vorschau")
    toggle("htop")
    check("htop ausgegraut", lambda: item("htop") == {"disabled": True, "on": False})
    check("htop im Brick", lambda: in_brick(preview(), "htop"))
    check("htop nicht mehr in der Liste", lambda: not in_list(preview(), "htop"))
    toggle("codex")
    check("codex wieder aktiv", lambda: item("codex") == {"disabled": False, "on": True})
    check("codex in der Liste", lambda: in_list(preview(), "codex"))
    check("codex nicht mehr im Brick", lambda: not in_brick(preview(), "codex"))
    check("die Datei ist noch unverändert",
          lambda: in_brick(CONF.read_text(encoding="utf-8"), "codex"))

    print("\n4. Kein doppelter Eintrag")
    check("addPackage lehnt ein deaktiviertes Paket ab",
          lambda: d.execute_async_script(
              "const done = arguments[0]; addPackage({attr: 'htop'}).then(done);") is False)
    check("weiter drei Pakete", lambda: js("return document.querySelectorAll('#packages-list .pkg-item').length") == 3)

    print("\n5. Speichern und neu laden")
    check("Speichern meldet Erfolg", lambda: save() is True)
    disk = lambda: CONF.read_text(encoding="utf-8")
    check("Datei: htop im Brick", lambda: in_brick(disk(), "htop") and not in_list(disk(), "htop"))
    check("Datei: codex in der Liste", lambda: in_list(disk(), "codex") and not in_brick(disk(), "codex"))
    load()
    check("nach dem Laden: htop aus", lambda: item("htop") == {"disabled": True, "on": False})
    check("nach dem Laden: codex an", lambda: item("codex") == {"disabled": False, "on": True})
    toggle("htop")
    check("letztes Paket aktiviert: Brick verschwindet aus der Vorschau",
          lambda: NAME not in brix.extract_brick_blocks(preview()))
    check("Speichern", lambda: save() is True)
    check("Datei: kein Brick mehr", lambda: NAME not in brix.extract_brick_blocks(disk()))
    check("Datei: Formatnummer 2", lambda: "#f2" in disk().splitlines()[1])

    print("\n6. AppImage-Bibliotheken")
    check("Feld sichtbar bei aktivem AppImage-Support", lambda: not hidden("appimage-detail"))
    check("Wert geladen", lambda: val("appimage_libs") == "libgpg-error")
    js("""const el = document.getElementById('appimage_libs');
          el.value = 'pkgs.libgpg-error libepoxy, libz.so.1; x';
          el.dispatchEvent(new Event('change', {bubbles: true}));""")
    time.sleep(0.5)
    check("ungültige Namen entfernt, pkgs. gestrichen",
          lambda: val("appimage_libs") == "libgpg-error, libepoxy, x")
    js("document.getElementById('appimage').click()")
    time.sleep(0.4)
    check("Feld verborgen ohne AppImage-Support", lambda: hidden("appimage-detail"))
    js("document.getElementById('appimage').click()")

    print("\n7. Firefox-Felder")
    check("Sprachpakete geladen", lambda: val("firefox_lang_packs") == "de, en-US")
    check("Einstellungen geladen", lambda: val("firefox_prefs") == '"intl.locale.requested" = "de-DE,de";')
    check("Speichern", lambda: save() is True)
    check("Datei behält die Sprachpakete", lambda: 'languagePacks = [ "de" "en-US" ];' in disk())
    check("Datei behält die Einstellungen", lambda: '"intl.locale.requested" = "de-DE,de";' in disk())
    check("Datei: beide Bibliotheken",
          lambda: "extraPkgs = pkgs: [ pkgs.libgpg-error pkgs.libepoxy pkgs.x ];" in disk())

finally:
    d.quit()

print("\n" + ("ALLE PRÜFUNGEN BESTANDEN" if not fails else f"FEHLGESCHLAGEN: {len(fails)}"))
sys.stdout.flush()
os._exit(1 if fails else 0)
