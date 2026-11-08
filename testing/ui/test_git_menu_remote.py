"""Browser-Test: Das Menü sagt, warum Git Push und Pull fehlen.

Gemeldet aus der VM (2026-11-08): Ohne Remote fehlten die Einträge kommentarlos.
Außerdem blendete sie nur der Admin-Bereich ein, nach dem Start waren sie auch
mit Remote nicht da.

Die Antwort des Servers wird ersetzt. Läuft auf eigenen Temp-Verzeichnissen.
"""
import json, os, pathlib, sys, tempfile, threading, time

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

xdg = pathlib.Path(tempfile.mkdtemp(prefix="nico-xdg-"))
os.environ["XDG_CONFIG_HOME"] = str(xdg)
cfg = pathlib.Path(tempfile.mkdtemp(prefix="nico-cfg-"))
(cfg / "configuration.nix").write_text("{ config, pkgs, ... }:\n{\n}\n", encoding="utf-8")
(cfg / "config.json").write_text(json.dumps({"hosts_dir": "hosts"}), encoding="utf-8")
(xdg / "nico").mkdir(parents=True)
(xdg / "nico" / "settings.json").write_text(
    json.dumps({"nixos_config_dir": str(cfg), "language": "de", "git_sync": False}), encoding="utf-8")

from nico.server import create_app
from selenium import webdriver
from selenium.webdriver.chrome.service import Service

PORT = 8798
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


def state(status: dict):
    d.execute_script("""
      window._stub = arguments[0];
      if (!window._realFetch) {
        window._realFetch = window.fetch;
        window.fetch = (url, o) => String(url).includes('/api/git/remote-status')
          ? Promise.resolve(new Response(JSON.stringify(window._stub)))
          : window._realFetch(url, o);
      }
      checkGitRemoteStatus();
    """, status)
    time.sleep(0.4)
    return d.execute_script("""
      const h = id => document.getElementById(id).classList.contains('hidden');
      return [h('nixos-git-push-btn'), h('nixos-git-pull-btn'), h('nixos-git-noremote')];
    """)


try:
    d.get(f"http://127.0.0.1:{PORT}/")
    time.sleep(4)

    print("1. Repository ohne Remote")
    got = state({"has_git": True, "has_remote": False, "behind": 0})
    check("Push und Pull aus, Hinweis an", got == [True, True, False], got)
    el = d.execute_script("""
      const b = document.getElementById('nixos-git-noremote');
      return [b.disabled, b.textContent.trim()];
    """)
    check("Eintrag ist nicht klickbar", el[0] is True)
    check("Text übersetzt", el[1] == "Git Push/Pull: kein Remote eingerichtet", el[1])

    print("\n2. Mit Remote, ohne dass der Admin-Bereich offen war")
    got = state({"has_git": True, "has_remote": True, "behind": 0})
    check("Push und Pull an, Hinweis aus", got == [False, False, True], got)

    print("\n3. Kein Repository")
    got = state({"has_git": False, "has_remote": False, "behind": 0})
    check("alles aus", got == [True, True, True], got)
finally:
    d.quit()

print()
if fails:
    print(f"{len(fails)} FEHLER:", *fails, sep="\n  ")
    sys.exit(1)
print("Alle Prüfungen bestanden.")
os._exit(0)
