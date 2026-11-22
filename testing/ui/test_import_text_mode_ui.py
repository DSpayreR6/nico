"""Browser-Test: Import im Textmodus – Oberfläche bleibt im Texteditor.

Martins Fund in der VM (2026-11-22): nach dem Import im Textmodus öffnete
NiCo configuration.nix im Panel, fragte „Datei für das Panel umbauen?“, und
der Panel-Schalter tat nichts. Drei Ursachen in der Oberfläche: der Standard
der Seitenleiste war privat und wurde nie gesetzt (save.js und admin.js
schrieben im strikten Modus in eine nicht deklarierte Variable – das warf
und brach refreshAfterImport ab), der Seitenstart füllte das Formular auch
im Textmodus, und eine fremde Datei galt als „außerhalb geändert“.

Geprüft wird – über den echten Importweg im Admin-Bereich mit Dialog:
  1. Die Frage erscheint, „Im Textmodus“ wählen.
  2. Danach: kein weiterer Dialog, Texteditor offen, Schalter auf Text,
     Datei Byte für Byte wie die Quelle.
  3. Seite neu laden: dasselbe, auch nach Warten aufs Autosave.
  4. Panel-Schalter umlegen: NiCo fragt „Umbauen?“ – erst dann ändert sich
     die Datei.

Die Wegwerf-Config und XDG_CONFIG_HOME legt der Test selbst an.
"""
import json, os, pathlib, sys, tempfile, threading, time
ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

xdg = pathlib.Path(tempfile.mkdtemp(prefix="nico-xdg-"))
cfg = pathlib.Path(tempfile.mkdtemp(prefix="nico-cfg-"))
src = pathlib.Path(tempfile.mkdtemp(prefix="nico-src-"))
os.environ["XDG_CONFIG_HOME"] = str(xdg)
os.environ["XDG_CACHE_HOME"] = str(xdg / "cache")
(xdg / "nico").mkdir(parents=True)
(xdg / "nico" / "settings.json").write_text(json.dumps(
    {"nixos_config_dir": str(cfg), "language": "de", "git_sync": True,
     "git_status_only": False}), encoding="utf-8")

ORIG = """# Edit this configuration file to define what should be installed on
# your system.

{ config, pkgs, ... }:

{
  imports =
    [ # Include the results of the hardware scan.
      ./hardware-configuration.nix
    ];

  # Bootloader.
  boot.loader.systemd-boot.enable = true;
  boot.loader.efi.canTouchEfiVariables = true;

  networking.hostName = "nixos"; # Define your hostname.
  networking.networkmanager.enable = true;
  time.timeZone = "Europe/Berlin";
  system.stateVersion = "26.05"; # Did you read the comment?
}
"""
(src / "configuration.nix").write_text(ORIG, encoding="utf-8")
(src / "hardware-configuration.nix").write_text(
    "{ ... }:\n{\n  fileSystems.\"/\" = { device = \"/dev/sda1\"; fsType = \"ext4\"; };\n}\n",
    encoding="utf-8")

from nico import git_manager
from nico.server import create_app
git_manager.init_repo(str(cfg))
(cfg / "configuration.nix").write_text("{ ... }:\n{\n}\n", encoding="utf-8")
git_manager.auto_commit(str(cfg), label="start")

from selenium import webdriver
from selenium.webdriver.chrome.service import Service

PORT = 8817
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

def overlays():
    return d.execute_script("""
      return [...document.querySelectorAll('.overlay')]
        .filter(e => !e.classList.contains('hidden') && e.offsetParent !== null)
        .map(e => e.id || (e.querySelector('.dialog-title')?.textContent || 'overlay'));""")

def raw_open():
    return d.execute_script(
        "const e = document.getElementById('raw-file-editor'); return !!(e && e.offsetParent !== null);")

def raw_text():
    return d.execute_script("return document.getElementById('raw-file-editor')?.value || '';")

def toggle_panel_on():
    return d.execute_script("return document.getElementById('panel-toggle-check')?.checked;")

def disk():
    return (cfg / "configuration.nix").read_text(encoding="utf-8")

try:
    d.get(f"http://127.0.0.1:{PORT}/")
    time.sleep(4)
    # Close whatever the start shows for the placeholder config.
    d.execute_script("document.querySelectorAll('.overlay:not(.hidden) .btn-primary').forEach(b => b.click());")
    time.sleep(1)

    print("1. Import über den Admin-Weg, Frage beantworten")
    d.execute_script("""
      window.__errors = [];
      window.addEventListener('error', e => window.__errors.push(String(e.message)));
      window.addEventListener('unhandledrejection', e => window.__errors.push(String(e.reason)));
      const el = document.getElementById('import-result');
      window.__imp = _doAdminImport(arguments[0], false, el).then(() => 'fertig', e => 'Fehler: ' + e);
    """, str(src / "configuration.nix"))
    time.sleep(1.5)
    titles = d.execute_script(
        "return [...document.querySelectorAll('.dialog-title')].map(e => e.textContent);")
    check("Frage „Wie willst du …“ erscheint", any("bearbeiten" in t for t in titles), titles)
    d.execute_script("document.querySelector('.dialog [data-mode=\"r\"]').click();")
    time.sleep(2)
    # The target is not empty: the backup question, then the import itself.
    d.execute_script("window.__imp2 = new Promise(r => { const b = document.getElementById('admin-backup-yes'); if (!b) { r('keine Rückfrage'); return; } b.click(); setTimeout(() => r('fertig'), 4000); });")
    time.sleep(5)
    check("Rückfrage zum Backup beantwortet",
          d.execute_script("return await window.__imp2;") == "fertig")
    errs = d.execute_script("return window.__errors;")
    check("Import ohne JavaScript-Fehler (refreshAfterImport)", errs == [], errs)
    check("panel_default=r gespeichert", json.loads(
        (cfg / "config.json").read_text(encoding="utf-8")).get("panel_default") == "r")

    print("2. Nach dem Import")
    check("Datei unverändert", disk() == ORIG)
    check("kein Dialog offen", overlays() == [], overlays())
    d.execute_script("Sidebar.selectFile('configuration.nix', 'configuration.nix', {force: true});")
    time.sleep(3)
    check("kein Dialog beim Öffnen", overlays() == [], overlays())
    check("Texteditor offen", raw_open())
    check("Editor zeigt das Original, ohne NiCo-Zeile", raw_text().strip() == ORIG.strip(),
          raw_text()[:120])
    check("Schalter auf Text", toggle_panel_on() is False, toggle_panel_on())

    print("3. Seite neu laden")
    d.get(f"http://127.0.0.1:{PORT}/")
    time.sleep(5)
    check("kein Dialog nach dem Laden", overlays() == [], overlays())
    check("Texteditor offen nach dem Laden", raw_open())
    time.sleep(6)
    check("Datei unverändert nach Laden und Warten", disk() == ORIG)

    print("4. Aufs Panel umschalten")
    d.execute_script("document.getElementById('panel-toggle-check').click();")
    time.sleep(3)
    asked = d.execute_script(
        "return !document.getElementById('restructure-overlay').classList.contains('hidden');")
    check("NiCo fragt „Umbauen?“", asked)
    check("solange gefragt wird: Datei unverändert", disk() == ORIG)
    d.execute_script("document.getElementById('restructure-ok-btn').click();")
    time.sleep(4)
    check("nach „Umbauen“: NiCo-Zeile mit #p", "#p#f" in disk(), disk()[:200])
    check("… und Panel bleibt an", toggle_panel_on() is True, toggle_panel_on())
finally:
    d.quit()

print("\n" + ("ALLE PRÜFUNGEN BESTANDEN" if not fails else f"FEHLGESCHLAGEN: {fails}"))
sys.exit(1 if fails else 0)
