"""Browser-Test: Textmodus mit zwei Schichten – eingefärbt, deckungsgleich, Suche markiert.

Martin, 2026-11-20: Syntax-Hervorhebung im Textmodus ist ein Muss, ohne
Fremdkomponente. Unten färbt Prism (dieselbe Funktion wie die Vorschau), oben
liegt das Eingabefeld mit durchsichtigem Text. Geprüft:
  1. Anzeige eingefärbt, gleiche Schrift und Zeilenhöhe wie das Eingabefeld
  2. Tippen färbt neu ein
  3. Scrollen bleibt deckungsgleich (senkrecht und waagerecht)
  4. Suchtreffer in der unteren Schicht markiert – auch ohne Fokus im Editor
  5. Speichern schreibt genau den eingegebenen Text
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

from nico import config_manager, generator
from nico.server import create_app
data = config_manager.get_defaults(); data["_flake_mode"] = False
(cfg / "configuration.nix").write_text(generator.generate_configuration_nix(data), encoding="utf-8")
(cfg / "modules").mkdir()
lines = ["{ config, pkgs, ... }:", "{", "  # Kommentar"]
lines += [f"  services.dienst{i}.enable = true;" for i in range(80)]
lines += ["  networking.firewall.allowedTCPPorts = [ 22 ];  # " + "lang " * 60 + "ENDWORT", "}"]
(cfg / "modules" / "lang.nix").write_text("\n".join(lines) + "\n", encoding="utf-8")

from selenium import webdriver
from selenium.webdriver.chrome.service import Service

PORT = 8827
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

JS_STATE = """
  const ta = document.getElementById('raw-file-editor');
  const v  = document.querySelector('#raw-file-view .text-editor-view');
  const c  = v.querySelector('code');
  const s  = e => getComputedStyle(e);
  return {tokens: c.querySelectorAll('.token').length,
          strings: c.querySelectorAll('.token.string').length,
          sameText: c.textContent === ta.value + '\\n',
          font: [s(ta).fontFamily === s(c).fontFamily, s(ta).fontSize === s(c).fontSize,
                 s(ta).lineHeight === s(c).lineHeight, s(ta).paddingTop === s(v).paddingTop,
                 s(ta).paddingLeft === s(v).paddingLeft],
          taColor: s(ta).color, viewOverflow: s(v).overflowX,
          scroll: [ta.scrollTop, v.scrollTop, ta.scrollLeft, v.scrollLeft],
          marks: c.querySelectorAll('mark.search-match').length,
          active: c.querySelectorAll('mark.search-match-active').length};"""

try:
    d.get(f"http://127.0.0.1:{PORT}/")
    time.sleep(4)
    d.execute_script("Sidebar.selectFile('modules/lang.nix', 'lang.nix', {force: true});")
    time.sleep(3)
    d.execute_script("document.querySelectorAll('.overlay').forEach(e => e.classList.add('hidden'))")

    print("1. Eingefärbt und gleich gesetzt")
    st = d.execute_script(JS_STATE)
    check("Prism-Token in der Anzeige", st["tokens"] > 50, st["tokens"])
    check("Anzeige zeigt genau den Editor-Text", st["sameText"])
    check("Schrift, Größe, Zeilenhöhe, Abstände gleich", all(st["font"]), st["font"])
    check("Text im Eingabefeld durchsichtig", st["taColor"] in ("rgba(0, 0, 0, 0)", "transparent"), st["taColor"])
    check("Anzeige ohne eigene Scrollleiste", st["viewOverflow"] == "hidden", st["viewOverflow"])

    print("\n2. Tippen färbt neu ein")
    before = st["strings"]
    d.execute_script("""
      const ta = document.getElementById('raw-file-editor');
      const i = ta.value.indexOf('# Kommentar');
      ta.value = ta.value.slice(0, i) + 'neu = "wert";\\n  ' + ta.value.slice(i);
      ta.dispatchEvent(new Event('input', {bubbles: true}));""")
    time.sleep(0.4)
    st = d.execute_script(JS_STATE)
    check("neuer String eingefärbt", st["strings"] == before + 1, (before, st["strings"]))
    check("Anzeige folgt dem Text", st["sameText"])

    print("\n3. Scrollen deckungsgleich")
    d.execute_script("const ta=document.getElementById('raw-file-editor'); ta.scrollTop = 600; ta.scrollLeft = 300;")
    time.sleep(0.3)
    sc = d.execute_script(JS_STATE)["scroll"]
    check("senkrecht gleich", sc[0] == sc[1] and sc[0] > 0, sc)
    check("waagerecht gleich", sc[2] == sc[3] and sc[2] > 0, sc)

    print("\n4. Suche markiert in der Anzeige")
    d.execute_script("""
      Search.open();
      const i = document.getElementById('search-input');
      i.value = 'ENDWORT'; i.dispatchEvent(new Event('input', {bubbles: true}));""")
    time.sleep(0.6)
    st = d.execute_script(JS_STATE)
    check("Treffer markiert, aktiv hervorgehoben", st["marks"] == 1 and st["active"] == 1, st)
    check("Fokus im Suchfeld", d.execute_script("return document.activeElement.id") == "search-input")
    vis = d.execute_script("""
      const m = document.querySelector('#raw-file-view mark.search-match').getBoundingClientRect();
      const t = document.getElementById('raw-file-editor').getBoundingClientRect();
      return m.left >= t.left && m.right <= t.right && m.top >= t.top && m.bottom <= t.bottom;""")
    check("Treffer im sichtbaren Bereich (seitlich mitgescrollt)", vis)
    d.execute_script("""
      const ta = document.getElementById('raw-file-editor');
      ta.value = ta.value.replace('ENDWORT', 'ENDWORT ENDWORT');
      ta.dispatchEvent(new Event('input', {bubbles: true}));""")
    time.sleep(0.4)
    st = d.execute_script(JS_STATE)
    check("nach Tippen: Markierungen neu gesetzt", st["marks"] == 2, st)
    check("Zähler folgt", d.execute_script("return document.getElementById('search-count').textContent").endswith("/2"))
    d.execute_script("Search.close();")

    print("\n5. Speichern schreibt den Text")
    expected = d.execute_script("return document.getElementById('raw-file-editor').value;")
    d.execute_script("document.getElementById('save-tab-btn').click();")
    time.sleep(2.5)
    on_disk = (cfg / "modules" / "lang.nix").read_text(encoding="utf-8")
    check("Datei = Editor-Text (Kopfzeilen ausgenommen)", on_disk.endswith(expected), on_disk[-80:])
finally:
    d.quit()

print()
if fails:
    print(f"{len(fails)} FEHLER")
    sys.exit(1)
print("Alle Prüfungen bestanden.")
