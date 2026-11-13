"""Browser-Test: „gebaut und aktiv, aber ein Dienst startete nicht neu" ist gelb.

Hintergrund in testing/routes/test_rebuild_activation.py. Hier wird geprüft,
was der Monitor aus dem done-Ereignis macht: gelbes Ergebnis statt rotem,
der Dienst beim Namen, kein Validator-Hinweis (der gehört zu einem
gescheiterten Bau), und der Sicherungspunkt wird angeboten.

Es läuft kein Rebuild: `EventSource` wird durch eine Attrappe ersetzt, die
das Ereignis aus Martins Lauf vom 2026-11-13 einspielt. Ein scharfer Rebuild
würde ins laufende System eingreifen (hinweisliste.txt).

Braucht ein eigenes XDG_CONFIG_HOME und eine Wegwerf-Config mit git-Repo
(sonst gibt es keinen Sicherungspunkt anzubieten) – siehe testing/ui/README.md.
"""
import json, os, pathlib, subprocess, sys, threading, time

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))
from nico import config_manager
from nico.server import create_app
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.service import Service

cfg_dir = config_manager.get_nixos_config_dir()
if not (pathlib.Path(cfg_dir) / ".git").exists():
    for args in (["init", "-q"], ["add", "-A"],
                 ["-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "init"]):
        subprocess.run(["git", *args], cwd=cfg_dir, check=False)

PORT = 8797
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

FAKE = """
window._fakeES = null;
window.EventSource = function (url) { window._fakeES = this; this.close = () => {}; };
"""
def play(done):
    d.execute_script(FAKE + "attachRebuildMonitor(0);")
    time.sleep(0.5)
    d.execute_script("window._fakeES.onmessage({data: arguments[0]});", json.dumps(done))
    time.sleep(1.5)

def state():
    return d.execute_script("""
      const r = document.getElementById('rebuild-result');
      const u = document.getElementById('rebuild-unrestarted-units');
      const p = document.getElementById('rebuild-push-btn');
      return {cls: r.className, text: r.textContent.trim(),
              color: getComputedStyle(r).color,
              yellow: getComputedStyle(document.documentElement).getPropertyValue('--yellow').trim(),
              units: u ? u.textContent : null,
              errors: !!document.getElementById('rebuild-error-lines'),
              push: p && !p.classList.contains('hidden'),
              pushText: p ? p.textContent.trim() : ''};""")

BASE = {"type": "done", "success": False, "exit_code": 1, "log_written": True,
        "failed_units": [], "error_lines_dropped": 0,
        "error_lines": ["Failed to restart network-addresses-vboxnet0.service"]}
try:
    d.get(f"http://127.0.0.1:{PORT}/")
    time.sleep(4)

    print("1. Status 4 mit benanntem Dienst")
    play({**BASE, "activated_with_warnings": True,
          "unrestarted_units": ["network-addresses-vboxnet0.service"]})
    s = state()
    check("Ergebnis trägt den Warnzustand", "result-warning" in s["cls"] and "result-failed" not in s["cls"], s["cls"])
    check("Text ist übersetzt und nennt keinen Fehlschlag",
          s["text"] and not s["text"].startswith("rebuild.") and "fehlgeschlagen" not in s["text"], s["text"])
    probe = d.execute_script(
        "const e=document.createElement('span'); e.style.color='var(--yellow)';"
        "document.body.appendChild(e); const c=getComputedStyle(e).color; e.remove(); return c;")
    check("Farbe ist das Gelb des Themes", s["color"] == probe, (s["color"], probe))
    check("der Dienst steht beim Namen da",
          s["units"] is not None and "network-addresses-vboxnet0.service" in s["units"], s["units"])
    check("Hinweis ohne offene Platzhalter",
          s["units"] is not None and "systemctl status" in s["units"] and "{0}" not in s["units"]
          and "rebuild." not in s["units"], s["units"])
    check("die roten Fehlerzeilen wiederholen ihn nicht", s["errors"] is False)
    check("kein Validator-Hinweis im Ergebnis",
          not d.find_elements(By.CSS_SELECTOR, "#rebuild-result button, #rebuild-result a"))
    check("der Sicherungspunkt wird angeboten", s["push"] is True and bool(s["pushText"]), s)

    print("\n2. Status 4 ohne erkannten Dienst")
    d.get(f"http://127.0.0.1:{PORT}/"); time.sleep(4)
    play({**BASE, "activated_with_warnings": True, "unrestarted_units": []})
    s = state()
    check("weiter gelb", "result-warning" in s["cls"], s["cls"])
    check("Kasten sagt, dass der Dienst im Log steht",
          s["units"] is not None and "Log" in s["units"], s["units"])
    check("dann bleiben die Fehlerzeilen sichtbar", s["errors"] is True)

    print("\n3. Ein echter Fehler bleibt rot")
    d.get(f"http://127.0.0.1:{PORT}/"); time.sleep(4)
    play({**BASE, "activated_with_warnings": False, "unrestarted_units": [],
          "error_lines": ["error: builder for '/nix/store/x-foo.drv' failed with exit code 1"]})
    s = state()
    check("Ergebnis ist rot", "result-failed" in s["cls"] and "result-warning" not in s["cls"], s["cls"])
    check("kein Warnkasten", s["units"] is None)
    check("kein Sicherungspunkt", s["push"] is False)

    print("\n4. Ein gelungener Rebuild bleibt grün")
    d.get(f"http://127.0.0.1:{PORT}/"); time.sleep(4)
    play({**BASE, "success": True, "exit_code": 0, "error_lines": [],
          "activated_with_warnings": False, "unrestarted_units": []})
    s = state()
    check("Ergebnis ist grün", "result-success" in s["cls"], s["cls"])
    check("Sicherungspunkt wie bisher", s["push"] is True)

    print("\n5. Fehlertext von nix wird maskiert (Revision 2026-11-10, S11)")
    # A config from elsewhere can make nix say anything, e.g. through
    # throw "<img src=x onerror=…>". The first error line and the text of an
    # error event went into innerHTML as they were.
    EVIL = 'error: <img src=x onerror="window._xss=1">'
    d.get(f"http://127.0.0.1:{PORT}/"); time.sleep(4)
    d.execute_script(FAKE + "attachRebuildMonitor(0);")
    time.sleep(0.5)
    d.execute_script("window._fakeES.onmessage({data: arguments[0]});",
                     json.dumps({"type": "output", "line": EVIL}))
    d.execute_script("window._fakeES.onmessage({data: arguments[0]});",
                     json.dumps({**BASE, "error_lines": [EVIL]}))
    time.sleep(1.5)
    check("erste Fehlerzeile: kein Element im Ergebnis",
          not d.find_elements(By.CSS_SELECTOR, "#rebuild-result img"))
    check("der Text steht wörtlich da",
          "<img src=x" in d.find_element(By.ID, "rebuild-result").text)
    d.get(f"http://127.0.0.1:{PORT}/"); time.sleep(4)
    d.execute_script(FAKE + "attachRebuildMonitor(0);")
    time.sleep(0.5)
    d.execute_script("window._fakeES.onmessage({data: arguments[0]});",
                     json.dumps({"type": "error", "message": EVIL}))
    time.sleep(1.5)
    check("error-Ereignis: kein Element im Ergebnis",
          not d.find_elements(By.CSS_SELECTOR, "#rebuild-result img"))
    check("kein Skript gelaufen", d.execute_script("return window._xss;") is None)
    if len(sys.argv) > 1:
        d.get(f"http://127.0.0.1:{PORT}/"); time.sleep(4)
        play({**BASE, "activated_with_warnings": True,
              "unrestarted_units": ["network-addresses-vboxnet0.service"]})
        # Start-up dialogs (git hygiene of the throwaway config) would cover it.
        d.execute_script("document.querySelectorAll('.overlay, .modal-overlay').forEach("
                         "o => { if (o.id !== 'rebuild-overlay') o.remove(); });")
        d.find_element(By.ID, 'rebuild-overlay').screenshot(sys.argv[1])
finally:
    d.quit()

print("\n" + ("ALLE PRÜFUNGEN BESTANDEN" if not fails else f"FEHLGESCHLAGEN: {fails}"))
sys.stdout.flush()
os._exit(1 if fails else 0)
