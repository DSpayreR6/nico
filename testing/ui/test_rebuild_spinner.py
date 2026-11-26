"""Browser-Test: drehender Strich mit Laufzeit in Rebuild-Monitor und Dry-Run.

Anlass (Martin, 2026-11-16): Beim Dry-Run wie beim Rebuild gibt es Strecken
ohne neue Zeile, und dann sieht es aus, als stünde NiCo. Die Paketsuche hatte
dafür schon `| / - \\`; jetzt auch diese beiden, mit der Laufzeit `m:ss`.
Beim Rebuild ist es die echte Laufzeit des Jobs (Startzeit vom Server), auch
nach einem Neuladen.

Es läuft kein Rebuild und kein Dry-Run: `EventSource` ist eine Attrappe, die
Antworten von `/api/rebuild/status` und `requestDryRun` werden im Browser
ersetzt.

  1. Monitor bei laufendem Job: Strich und Laufzeit ab Job-Start, dreht sich,
     steht unter der letzten Ausgabezeile.
  2. Ende des Laufs: Strich verschwindet.
  3. Monitor eines schon fertigen Jobs: kein Strich.
  4. Dry-Run: Strich bis zum Ergebnis, dann das Ergebnis.
  5. Der Baustein: stop() räumt nur eigenen Text weg.
  6. Schalter „Alle Meldungen“ steht in einer Zeile mit seinem Text, rechts.

Braucht ein eigenes XDG_CONFIG_HOME (setzt es nicht selbst), wie
`test_rebuild_options.py`.
"""
import json, os, pathlib, re, sys, threading, time

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))
from nico.server import create_app
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.service import Service

PORT = 8813
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

SPIN_RE = re.compile(r"^[|/\-\\] \d+:\d\d$")

# EventSource stand-in, and a status answer with a job that started
# `secsAgo` seconds ago (running) or a finished one.
FAKE = """
window.EventSource = function (url) { window._fakeES = this; this.close = () => {}; };
window._origFetch = window._origFetch || window.fetch;
window._statusJob = arguments[0];
window.fetch = (url, o) => String(url).startsWith('/api/rebuild/status')
  ? Promise.resolve({json: () => Promise.resolve({job: window._statusJob})})
  : window._origFetch(url, o);
"""
def job(running, secs_ago):
    return {"id": "t", "running": running, "success": None if running else True,
            "acked": False, "mode": "switch", "hostname": "", "verbose": False,
            "started": time.time() - secs_ago, "finished": None if running else time.time(),
            "events": 0}

def spinner():
    return d.execute_script(
        "const e = document.getElementById('rebuild-spinner');"
        "return {text: e.textContent, hidden: e.classList.contains('hidden')};")

def send(msg):
    d.execute_script("window._fakeES.onmessage({data: arguments[0]});", json.dumps(msg))

try:
    d.get(f"http://127.0.0.1:{PORT}/")
    time.sleep(4)

    print("1. Monitor bei laufendem Job")
    d.execute_script(FAKE + "attachRebuildMonitor(0);", job(True, 125))
    time.sleep(0.6)
    s = spinner()
    check("Strich sichtbar", not s["hidden"], s)
    check("Form „| m:ss“", bool(SPIN_RE.match(s["text"])), s["text"])
    check("Laufzeit ab Job-Start (2:05 … 2:09)",
          bool(re.search(r" 2:0[5-9]$", s["text"])), s["text"])
    frames = set()
    for _ in range(8):
        frames.add(spinner()["text"][:1])
        time.sleep(0.07)
    check("Strich dreht sich", len(frames) >= 2, frames)
    for i in range(5):
        send({"type": "output", "line": f"building step {i}"})
    time.sleep(0.4)
    pos = d.execute_script(
        "const l = document.querySelector('#rebuild-log span:last-child').getBoundingClientRect();"
        "const s = document.getElementById('rebuild-spinner').getBoundingClientRect();"
        "const w = document.getElementById('rebuild-log-window');"
        "return {lineBottom: l.bottom, spinTop: s.top, gap: s.top - l.bottom,"
        "        atEnd: w.scrollHeight - w.clientHeight - w.scrollTop};")
    check("steht direkt unter der letzten Zeile", -1 <= pos["gap"] <= 4, pos)
    check("Fenster ist ans Ende gescrollt", pos["atEnd"] <= 1, pos)

    print("\n2. Ende des Laufs")
    send({"type": "done", "success": True, "exit_code": 0, "log_written": False,
          "failed_units": [], "error_lines": [], "error_lines_dropped": 0,
          "activated_with_warnings": False, "unrestarted_units": []})
    time.sleep(0.4)
    s = spinner()
    check("Strich verschwunden", s["hidden"] and s["text"] == "", s)
    time.sleep(0.3)
    check("bleibt verschwunden", spinner()["text"] == "", spinner())

    print("\n3. Monitor eines fertigen Jobs")
    d.execute_script(FAKE + "attachRebuildMonitor(0);", job(False, 300))
    time.sleep(0.6)
    s = spinner()
    check("kein Strich", s["hidden"], s)

    print("\n4. Dry-Run")
    d.execute_script("""
      // A job since 2026-11-26: start and wait are one call (requestDryRun).
      requestDryRun = () => new Promise(r => setTimeout(
        () => r({ok: true, output: 'alles gut'}), 1500));
      _fetchFlakeHosts = () => Promise.resolve({flake_mode: false, hosts: []});
      Sidebar.flakeSave = () => Promise.resolve(true);
      _autoSave = () => Promise.resolve(true);
      _writeNix = () => Promise.resolve(true);
      _validationGate = () => Promise.resolve(true);
      document.getElementById('rebuild-overlay').classList.add('hidden');
      runDryRun();""")
    time.sleep(0.5)
    out = d.find_element(By.ID, "dryrun-output").text
    check("Strich während des Wartens", bool(SPIN_RE.match(out)), out)
    check("zählt ab dem Klick (0:00 … 0:01)", bool(re.search(r" 0:0[01]$", out)), out)
    time.sleep(1.6)
    out = d.find_element(By.ID, "dryrun-output").text
    check("danach das Ergebnis, kein Strich", "alles gut" in out and not SPIN_RE.search(out), out)
    time.sleep(0.3)
    check("Ergebnis bleibt stehen", "alles gut" in d.find_element(By.ID, "dryrun-output").text)
    d.execute_script("document.getElementById('dryrun-overlay').classList.add('hidden');")

    print("\n5. Der Baustein")
    r = d.execute_script("""
      const a = document.createElement('div'), b = document.createElement('div');
      const sa = startElapsedSpinner(a, Date.now() - 61000);
      const first = a.textContent;
      sa.stop();
      const sb = startElapsedSpinner(b);
      b.textContent = 'Ergebnis';
      sb.stop();
      return {first, afterA: a.textContent, afterB: b.textContent};""")
    check("Laufzeit über eine Minute: 1:01", r["first"].endswith(" 1:01"), r)
    check("stop() räumt eigenen Text weg", r["afterA"] == "", r)
    check("stop() lässt fremden Text stehen", r["afterB"] == "Ergebnis", r)

    print("\n6. Schalter „Alle Meldungen“ in einer Zeile")
    d.execute_script(FAKE + "attachRebuildMonitor(0);", job(False, 10))
    time.sleep(0.6)
    row = d.execute_script("""
      const l = document.getElementById('rebuild-verbose-toggle').closest('label');
      const t = l.querySelector('[data-i18n]').getBoundingClientRect();
      const w = l.querySelector('.toggle-wrap').getBoundingClientRect();
      const r = l.getBoundingClientRect();
      return {textMid: (t.top + t.bottom) / 2, wrapMid: (w.top + w.bottom) / 2,
              textLeft: t.left - r.left, wrapRight: r.right - w.right};""")
    check("Text und Schalter auf einer Höhe", abs(row["textMid"] - row["wrapMid"]) < 6, row)
    check("Text links", row["textLeft"] < 2, row)
    check("Schalter rechts", row["wrapRight"] < 2, row)
finally:
    d.quit()

print()
print("BESTANDEN" if not fails else f"FEHLGESCHLAGEN: {len(fails)}")
sys.exit(0 if not fails else 1)
