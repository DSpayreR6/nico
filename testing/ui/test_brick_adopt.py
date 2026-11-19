"""Browser-Test: „Ins Panel übernehmen" im Punkte-Menü eines Nix-Bricks.

Gegenprobe Phase 4 (Martin, 2026-11-19): Ein Nix-Brick, den das Panel genau
so schreiben würde, lässt sich ins Panel übernehmen. Geprüft wird, was sich
nur im Browser zeigt:

  * der Menüeintrag erscheint bei einem aktiven Brick in configuration.nix
  * nach dem Klick ist der Brick weg und der Schalter im Formular gesetzt
  * ein Brick, den das Panel anders schreiben würde, bleibt; die Meldung ist
    übersetzt und nennt den Pfad
  * bei einem deaktivierten Brick fehlt der Eintrag

Das Gegenstück ohne Browser: testing/routes/test_brick_adopt.py.
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

from nico import config_manager, generator
from nico.brix import extract_brick_blocks, format_brick
from nico.server import create_app

def brick(name, order, body, inactive=False):
    return {"section": "SERV", "order": order, "inactive": inactive,
            "text": format_brick("SERV", order, name, body, inactive)}

data = config_manager.get_defaults()
data.update(hostname="box", _flake_mode=False)
data["brick_blocks"] = {
    "druck": brick("druck", 1, "services.printing.enable = true;"),
    "avahi-zu": brick("avahi-zu", 2, "services.avahi = { enable = true; nssmdns4 = true; "
                                     "openFirewall = false; };"),
    "ruhend": brick("ruhend", 3, "# services.blueman.enable = true;", inactive=True),
}
(cfg / "configuration.nix").write_text(generator.generate_configuration_nix(data), encoding="utf-8")

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.service import Service

PORT = 8816
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

def wrap(name):
    found = d.find_elements(By.CSS_SELECTOR, f'.code-brix[data-brick-name="{name}"]')
    return found[0] if found else None

def labels(name):
    w = wrap(name)
    d.execute_script("arguments[0].querySelector('.brix-menu-btn').click()", w)
    time.sleep(0.3)
    out = [d.execute_script("return arguments[0].textContent", i).strip()
           for i in w.find_elements(By.CSS_SELECTOR, '.brix-menu-item')]
    d.execute_script("closeAllBrixMenus()")
    return out

def click(name, label):
    w = wrap(name)
    d.execute_script("arguments[0].querySelector('.brix-menu-btn').click()", w)
    time.sleep(0.3)
    for item in w.find_elements(By.CSS_SELECTOR, '.brix-menu-item'):
        if d.execute_script("return arguments[0].textContent", item).strip() == label:
            d.execute_script("arguments[0].click()", item)
            time.sleep(3)
            return True
    return False

def toasts():
    return " ".join(e.text for e in d.find_elements(By.CSS_SELECTOR, ".toast"))

try:
    d.get(f"http://127.0.0.1:{PORT}/")
    time.sleep(5)
    print("1. Menüeintrag")
    check("Brick „druck“ ist da", wrap("druck") is not None)
    check("Eintrag „Ins Panel übernehmen“ beim aktiven Brick",
          "Ins Panel übernehmen" in labels("druck"), labels("druck"))
    check("kein Eintrag beim deaktivierten Brick", "Ins Panel übernehmen" not in labels("ruhend"),
          labels("ruhend"))
    check("Drucken im Formular noch aus",
          d.execute_script("return document.getElementById('printing').checked") is False)

    print("\n2. Übernehmen")
    check("Klick geht", click("druck", "Ins Panel übernehmen"))
    text = (cfg / "configuration.nix").read_text(encoding="utf-8")
    check("Brick aus der Datei verschwunden", "druck" not in extract_brick_blocks(text))
    check("… und aus der Vorschau", wrap("druck") is None)
    check("Drucken im Formular an",
          d.execute_script("return document.getElementById('printing').checked") is True)
    check("Erfolgsmeldung übersetzt", "ins Panel übernommen" in toasts(), toasts())

    print("\n3. Panel würde anders schreiben")
    before = (cfg / "configuration.nix").read_text(encoding="utf-8")
    check("Klick geht", click("avahi-zu", "Ins Panel übernehmen"))
    check("Datei unverändert", (cfg / "configuration.nix").read_text(encoding="utf-8") == before)
    check("Brick bleibt in der Vorschau", wrap("avahi-zu") is not None)
    msg = toasts()
    check("Meldung übersetzt und nennt den Pfad",
          "Nicht übernommen" in msg and "openFirewall" in msg, msg)
    check("kein roher Schlüssel", "ERR_" not in msg and "errors." not in msg, msg)
finally:
    d.quit()

print()
if fails:
    print(f"{len(fails)} FEHLER:", *fails, sep="\n  ")
    sys.exit(1)
print("Alle Prüfungen bestanden.")
