"""Browser-Test: „flake.nix anlegen" ist ein eigener, ausdrücklicher Schritt.

Hintergrund in testing/routes/test_rev1110_flake_mode.py (Revision
2026-11-10, B34): Flake-Modus heißt „flake.nix existiert", der Schalter im
Abschnitt „Nix & System" steuert nur noch die experimental-features-Zeile.

Geprüft wird in der Oberfläche:
  1. Klassische Config: die Schaltfläche ist da, der Schalter heißt wie
     entschieden, und ihn einzuschalten legt keine flake.nix an.
  2. Der Dialog nennt Verzeichnis und Host und schließt nur per Schaltfläche;
     Abbrechen ändert nichts.
  3. Anlegen: flake.nix, Host-Verzeichnis, verschobene Hardware-Datei; danach
     ist die Schaltfläche weg und der Rebuild kennt den Host.

Der Test legt sich Config und XDG_CONFIG_HOME selbst an – er verändert die
Config, eine geteilte Wegwerf-Config wäre danach für andere Tests eine andere.
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

from nico import config_manager, generator
from nico.server import create_app
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.service import Service

HW = '{ ... }: { fileSystems."/" = { device = "/dev/sda1"; fsType = "ext4"; }; }\n'
data = config_manager.get_defaults()
data.update({"hostname": "kiste", "hardware_config": True})
(cfg / "hardware-configuration.nix").write_text(HW, encoding="utf-8")
(cfg / "configuration.nix").write_text(generator.generate_configuration_nix(data), encoding="utf-8")

PORT = 8798
app = create_app()
threading.Thread(target=lambda: app.run(port=PORT, threaded=True), daemon=True).start()
time.sleep(2)
opts = webdriver.ChromeOptions()
for f in ("--headless=new", "--no-sandbox", "--disable-dev-shm-usage", "--window-size=1600,1400"):
    opts.add_argument(f)
opts.binary_location = os.environ["CHROME_BIN"]
d = webdriver.Chrome(service=Service(os.environ["CHROMEDRIVER"]), options=opts)

fails = []
def check(label, cond, got=None):
    print(("  OK   " if cond else "  FAIL ") + label + ("" if cond or got is None else f"  (war: {got})"))
    if not cond: fails.append(label)

def row_visible():
    return d.execute_script(
        "const r=document.getElementById('flake-create-row');"
        "return !!r && !r.classList.contains('hidden');")

def clear_startup_dialogs():
    d.execute_script("document.querySelectorAll('.overlay').forEach(o => {"
                     " if (!o.id && !o.querySelector('#_fc-ok')) o.remove(); });")

try:
    d.get(f"http://127.0.0.1:{PORT}/")
    time.sleep(5)
    clear_startup_dialogs()

    print("1. Klassische Config")
    check("Schaltfläche „flake.nix anlegen“ ist sichtbar", row_visible())
    label = d.execute_script(
        "return document.querySelector('[data-i18n=\"field.flakes\"]')?.textContent.trim();")
    check("Schalter heißt wie entschieden",
          label == "Flake-Befehle erlauben (experimental-features)", label)
    btn = d.execute_script("return document.getElementById('flake-create-btn')?.textContent.trim();")
    check("Schaltfläche ist beschriftet", btn == "flake.nix anlegen", btn)
    d.execute_script("document.getElementById('flakes').click();")
    ok = d.execute_script("return _autoSave().then(a => a && _writeNix());")
    time.sleep(1)
    check("Schalter an und geschrieben", ok is True, ok)
    check("das legt keine flake.nix an", not (cfg / "flake.nix").exists())
    check("aber die Zeile steht in der Config",
          'experimental-features = [ "nix-command" "flakes" ]'
          in (cfg / "configuration.nix").read_text(encoding="utf-8"))
    check("der Hardware-Import ist geblieben",
          "./hardware-configuration.nix" in (cfg / "configuration.nix").read_text(encoding="utf-8"))

    print("\n2. Dialog")
    d.execute_script("document.getElementById('flake-create-btn').click();")
    time.sleep(0.6)
    text = d.execute_script("return document.querySelector('#_fc-ok')?.closest('.dialog-box')?.textContent || '';")
    check("Dialog ist offen", bool(text))
    check("nennt Verzeichnis und Host", "hosts/kiste" in text and "„kiste“" in text, text)
    check("keine offenen Platzhalter", "{0}" not in text and "{1}" not in text and "flake.create" not in text, text)
    d.execute_script("document.querySelector('#_fc-ok').closest('.overlay').click();")
    time.sleep(0.3)
    check("Klick auf den Hintergrund schließt nicht", bool(d.find_elements(By.ID, "_fc-ok")))
    d.execute_script("document.getElementById('_fc-cancel').click();")
    time.sleep(0.5)
    check("Abbrechen schließt", not d.find_elements(By.ID, "_fc-ok"))
    check("und ändert nichts", not (cfg / "flake.nix").exists() and not (cfg / "hosts").exists())

    print("\n3. Anlegen")
    d.execute_script("document.getElementById('flake-create-btn').click();")
    time.sleep(0.6)
    d.execute_script("document.getElementById('_fc-ok').click();")
    for _ in range(40):
        if (cfg / "flake.nix").exists(): break
        time.sleep(0.25)
    time.sleep(3)
    clear_startup_dialogs()
    check("flake.nix liegt da", (cfg / "flake.nix").is_file())
    check("Host-Verzeichnis mit default.nix", (cfg / "hosts" / "kiste" / "default.nix").is_file())
    check("Hardware-Datei verschoben",
          (cfg / "hosts" / "kiste" / "hardware-configuration.nix").is_file()
          and not (cfg / "hardware-configuration.nix").exists())
    check("Schaltfläche ist jetzt weg", not row_visible())
    toasts = d.execute_script("return [...document.querySelectorAll('.toast')].map(t => t.textContent);")
    check("Erfolgsmeldung erschienen", any("flake.nix angelegt" in t for t in toasts), toasts)
    check("der Schalter ist weiter an",
          d.execute_script("return document.getElementById('flakes').checked;") is True)
    info = d.execute_script("return _fetchFlakeHosts();")
    check("der Rebuild kennt den Host",
          info.get("flake_mode") is True
          and [h["name"] if isinstance(h, dict) else h for h in info.get("hosts") or []] == ["kiste"], info)
    d.execute_script("window._rbo = null; _showRebuildOptions({flake_mode: true}, {hostname: 'kiste'});")
    time.sleep(1)
    cmd = d.execute_script("return document.getElementById('_rbo-cmd')?.textContent || '';")
    check("der Rebuild-Befehl baut mit --flake", "--flake" in cmd and "#kiste" in cmd, cmd)
    if len(sys.argv) > 1:
        d.execute_script("document.getElementById('_rbo-cancel')?.click();")
        d.save_screenshot(sys.argv[1])
finally:
    d.quit()

print("\n" + ("ALLE PRÜFUNGEN BESTANDEN" if not fails else f"FEHLGESCHLAGEN: {fails}"))
sys.stdout.flush()
os._exit(1 if fails else 0)
