#!/usr/bin/env python3
"""Rebuild-Monitor: Detailmeldungen von Nix nur noch auf Wunsch.

Anlass (Martin, 2026-11-15, erster VM-Durchlauf zu 0.17.0): Im Monitor läuft
sehr viel Text durch, der einem Einsteiger nichts sagt – die Linking- und
„evaluating file"-Zeilen. Warnungen und Hinweise gehen darin unter. Ein
Rebuild im Terminal zeigt sie nicht. Ursache: NiCo ruft `nixos-rebuild` mit
`-v` auf und reichte jede Meldung durch.

Gemessen am selben Tag (`nix build --dry-run` an der Flake-Config und
`nix-instantiate`, je mit und ohne `-v`): `-v` bringt nur `msg`-Zeilen der
Stufe 4 dazu. Die Aktivitäten, aus denen Phasen und Fortschritt gelesen
werden, tragen zwar auch eine Stufe (4 bis 6), kommen aber immer.

Erste Fassung: `-v` weglassen. Verworfen am selben Tag (Martin): der
Schalter gehört in den Monitor, dort ist er intuitiv – und dort muss er im
laufenden Rebuild wirken. Darum läuft `-v` weiter immer mit, und der Server
verwirft die Detailmeldungen, solange der Schalter aus ist: weder Monitor
noch Logdatei bekommen sie.

Geprüft wird:
  1. Der Befehl trägt immer `-v` und `--log-format internal-json`.
  2. Was eine Detailmeldung ist: nur `msg` ab Stufe 4. Fehler, Warnungen,
     Hinweise, Aktivitäten und Builder-Ausgabe sind es nie.
  3. App-Settings: Vorgabe aus, gespeichert wird nur ein Wahrheitswert.
  4. /api/rebuild/verbose: merkt die Wahl und schaltet den laufenden Job um;
     ein beendeter Job bleibt unberührt.
  5. Dialog und Monitor kennen den Schalter.
  6. Der Text steht in allen sieben Sprachen.
  7. nixos-rebuilds eigene Zeilen (`debug: …`, Python-Traceback) – Martins
     VM-Lauf vom selben Tag: Sie sind kein Nix-Text, der Filter aus 2. sah
     sie nie. Bei ausgeschaltetem Schalter zeigt der Monitor sie nicht, vom
     Traceback bleibt die letzte Zeile. In die Logdatei gehen sie immer:
     ein Fehler, der beim zweiten Lauf nicht wiederkommt, wäre sonst weg.

Lauf: nix-shell shell.nix --run "python testing/routes/test_rebuild_verbose.py"
"""
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

xdg = Path(tempfile.mkdtemp(prefix="nico-xdg-"))
os.environ["XDG_CONFIG_HOME"] = str(xdg)
cfg = Path(tempfile.mkdtemp(prefix="nico-cfg-"))
(xdg / "nico").mkdir(parents=True)
(xdg / "nico" / "settings.json").write_text(
    json.dumps({"nixos_config_dir": str(cfg), "language": "de", "git_sync": False}),
    encoding="utf-8")

from nico.routes import rebuild as rb  # noqa: E402

fails = []
def check(label, cond, got=None):
    print(("  OK   " if cond else "  FAIL ") + label + ("" if cond or got is None else f"  (war: {got})"))
    if not cond:
        fails.append(label)

print("1. Der Befehl")
flake = rb.rebuild_cmd("switch", "/cfg#nix-vm", "/cfg/configuration.nix")
check("Flake: genau ein -v", flake.count("-v") == 1, flake)
check("Flake: --flake mit Ziel", flake[flake.index("--flake") + 1] == "/cfg#nix-vm", flake)
check("Flake: internal-json",
      flake[flake.index("--log-format") + 1] == "internal-json", flake)
plain = rb.rebuild_cmd("boot", "", "/cfg/configuration.nix")
check("Ohne Flake: genau ein -v", plain.count("-v") == 1, plain)
check("Ohne Flake: -I nixos-config, kein --flake",
      "--flake" not in plain and "nixos-config=/cfg/configuration.nix" in plain, plain)
check("Modus wird durchgereicht", plain[:4] == ["sudo", "-S", "nixos-rebuild", "boot"], plain)

print("\n2. Was eine Detailmeldung ist")
def msg(level, text="x"):
    return json.dumps({"action": "msg", "level": level, "msg": text})
# Wörtlich aus den Messungen vom 2026-11-15 bzw. dem Lauf vom 2026-11-01.
check("evaluating file (Stufe 4) ist Detail",
      rb.is_detail_msg(msg(4, "evaluating file '«nix-internal»/derivation-internal.nix'")))
check("linking (Stufe 4) ist Detail",
      rb.is_detail_msg(msg(4, "linking '/nix/store/abc' to '/nix/store/.links/xyz'")))
check("Stufe 5 bis 7 ebenfalls", all(rb.is_detail_msg(msg(n)) for n in (5, 6, 7)))
for level, name in ((0, "Fehler"), (1, "Warnung"), (2, "Hinweis"), (3, "Info")):
    check(f"{name} (Stufe {level}) bleibt", not rb.is_detail_msg(msg(level, "warning: Git tree is dirty")))
check("Aktivität Stufe 4 bleibt (evaluating derivation → Phase)",
      not rb.is_detail_msg(json.dumps({"action": "start", "id": 1, "level": 4, "type": 0,
                                       "text": "evaluating derivation 'x'"})))
check("Aktivität Stufe 5 mit dem Wort msg im Text bleibt",
      not rb.is_detail_msg(json.dumps({"action": "start", "id": 2, "level": 5, "type": 0,
                                       "text": "hashing '/src/\"msg\".nix'"})))
check("Bau-Aktivität bleibt",
      not rb.is_detail_msg(json.dumps({"action": "start", "id": 3, "level": 3, "type": 105,
                                       "text": "building '/nix/store/a-hello.drv'"})))
check("stop bleibt", not rb.is_detail_msg(json.dumps({"action": "stop", "id": 3})))
check("Builder-Ausgabe (result 101) bleibt",
      not rb.is_detail_msg(json.dumps({"action": "result", "id": 3, "type": 101,
                                       "fields": ["error: msg failed"]})))
check("Meldung ohne Stufe bleibt", not rb.is_detail_msg(json.dumps({"action": "msg", "msg": "x"})))
check("Stufe als Text bleibt", not rb.is_detail_msg(json.dumps({"action": "msg", "level": "4", "msg": "x"})))
check("kaputtes JSON bleibt (wird wie bisher roh gezeigt)", not rb.is_detail_msg('{"action":"msg","level":4'))
check("kein Objekt bleibt", not rb.is_detail_msg('["msg", 4]'))

print("\n3. App-Settings")
from nico.server import create_app  # noqa: E402
from nico import config_manager  # noqa: E402
app = create_app()
app.config["TESTING"] = True
c = app.test_client()
import re  # noqa: E402
m = re.search(r'name="csrf-token" content="([^"]+)"', c.get("/").get_data(as_text=True))
H = {"X-CSRF-Token": m.group(1) if m else "", "Content-Type": "application/json"}
check("CSRF-Token gefunden", bool(m))
def saved():
    return c.get("/api/app/settings").get_json().get("rebuild_verbose")

check("Vorgabe: aus", saved() is False, saved())
r = c.patch("/api/app/settings", json={"rebuild_verbose": True}, headers=H)
check("Speichern gelingt", r.status_code == 200 and r.get_json().get("success"), r.get_data(as_text=True))
check("gemerkt: an", saved() is True)
c.patch("/api/app/settings", json={"rebuild_verbose": "nein"}, headers=H)
check("kein Wahrheitswert wird nicht gespeichert", saved() is True)
c.patch("/api/app/settings", json={"rebuild_verbose": False}, headers=H)
check("gemerkt: aus", saved() is False)

print("\n4. /api/rebuild/verbose")
r = c.post("/api/rebuild/verbose", json={"on": True})
check("ohne Token abgelehnt", r.status_code in (400, 403), r.status_code)
check("und nichts gespeichert", saved() is False)
r = c.post("/api/rebuild/verbose", json={"on": True}, headers=H)
check("ohne Job: gelingt", r.status_code == 200 and r.get_json().get("verbose") is True, r.get_data(as_text=True))
check("ohne Job: Wahl gemerkt", saved() is True)
check("ohne Job: nichts zu zeigen", rb._job_verbose() is False)
r = c.post("/api/rebuild/verbose", json={"on": "ja"}, headers=H)
check("kein Wahrheitswert: 400", r.status_code == 400, r.status_code)
check("Wahl bleibt", saved() is True)
c.post("/api/rebuild/verbose", json={"on": False}, headers=H)

# A job as rebuild_start creates it – no process behind it, only the state.
def fake_job(running):
    return {"id": "t", "running": running, "events": [], "dropped": 0, "success": None,
            "acked": False, "mode": "switch", "hostname": "", "verbose": False,
            "started": 0.0, "finished": None}
rb._job = fake_job(True)
check("laufender Job startet ohne Details", rb._job_verbose() is False)
c.post("/api/rebuild/verbose", json={"on": True}, headers=H)
check("an: der laufende Job zeigt sie sofort", rb._job_verbose() is True)
check("und die Wahl ist gemerkt", saved() is True)
check("Status nennt den Stand", c.get("/api/rebuild/status").get_json()["job"].get("verbose") is True)
c.post("/api/rebuild/verbose", json={"on": False}, headers=H)
check("aus: der laufende Job verwirft sie wieder", rb._job_verbose() is False)
check("und gemerkt: aus", saved() is False)
rb._job = fake_job(False)
c.post("/api/rebuild/verbose", json={"on": True}, headers=H)
check("beendeter Job bleibt unberührt", rb._job["verbose"] is False)
check("die Wahl gilt trotzdem für den nächsten", saved() is True)
rb._job = None

print("\n5. Dialog und Monitor")
js = (ROOT / "nico" / "static" / "js" / "rebuild.js").read_text(encoding="utf-8")
html = (ROOT / "nico" / "templates" / "index.html").read_text(encoding="utf-8")
check("Schalter im Dialog", 'id="_rbo-verbose"' in js)
check("Dialog: Stand aus den App-Settings", "defaultVerbose     = !!app.rebuild_verbose" in js)
check("Dialog: Stand wird gespeichert", "rebuild_verbose: verbose" in js)
check("Start schickt ihn mit", "verbose:      !!opts.verbose" in js)
check("Schalter im Monitor", 'id="rebuild-verbose-toggle"' in html)
check("Monitor: mit übersetztem Text", 'data-i18n="rebuild.optVerbose"' in html)
check("Monitor: schaltet den laufenden Job um", "'/api/rebuild/verbose'" in js)

print("\n6. Sieben Sprachen")
langs = sorted((ROOT / "nico" / "static" / "lang").glob("*.json"))
check("sieben Sprachdateien", len(langs) == 7, len(langs))
for p in langs:
    val = json.loads(p.read_text(encoding="utf-8")).get("rebuild.optVerbose")
    # `-v` läuft immer mit; der Schalter verspricht es darum nicht mehr.
    check(f"{p.stem}: rebuild.optVerbose", bool(val) and val != "__TODO__" and "-v" not in val, val)

print("\n7. nixos-rebuilds eigene Zeilen")
# Wörtlich aus Martins VM-Lauf vom 2026-11-15 (NixOS 26.05), Pfade gekürzt.
NG = "/nix/store/937n-nixos-rebuild-ng-26.05/lib/python3.13/site-packages/nixos_rebuild"
VM = f"""debug: nixos_rebuild.process: calling run with args=['test', '-f', PosixPath('/nix/store/r659-nixos-system-nix-vm-26.05/nixos-version')], kwargs={{}}, env=None, append_local_env=None
debug: nixos_rebuild.process: captured output with stdout='', stderr=None
activating the configuration...
Nov 15 15:09:34 nix-vm systemd[1]: btrfs-dynamic-reclaim.service: Failed with result 'exit-code'.
Nov 15 15:09:34 nix-vm systemd[1]: Failed to start Enable Btrfs dynamic block group reclaim for data chunks (all volumes).
Traceback (most recent call last):
  File "{NG}/__init__.py", line 400, in main
    execute(sys.argv)
    ~~~~~~~^^^^^^^^^^
  File "{NG}/services.py", line 335, in build_and_activate_system
    _activate_system(
    ~~~~~~~~~~~~~~~~^
        path_to_config=path_to_config,
        ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
    ...<6 lines>...
    )
    ^
  File "{NG}/process.py", line 211, in run_wrapper
    r = subprocess.run(
        final_args,
    ...<5 lines>...
        **kwargs,
    )
  File "/nix/store/kp6p-python3-3.13.17/lib/python3.13/subprocess.py", line 578, in run
    raise CalledProcessError(retcode, process.args,
                             output=stdout, stderr=stderr)
subprocess.CalledProcessError: Command '['systemd-run', '-E', 'LOCALE_ARCHIVE']' returned non-zero exit status 4.
── log saved ──""".splitlines()

Chatter = getattr(rb, "_OwnChatter", None)
check("es gibt _OwnChatter", Chatter is not None)
if Chatter:
    ch = Chatter()
    shown = [ln for ln in VM if not ch.hidden(ln)]
    check("debug:-Zeilen verborgen", not any(ln.startswith("debug:") for ln in shown), shown)
    check("Traceback-Kopf und Rahmen verborgen",
          not any("Traceback" in ln or ln.startswith(" ") for ln in shown), shown)
    check("letzte Zeile des Tracebacks bleibt",
          any(ln.startswith("subprocess.CalledProcessError:") for ln in shown), shown)
    check("was danach kommt, bleibt", shown[-1] == "── log saved ──", shown[-1])
    check("Aktivierung und systemd-Zeilen bleiben",
          shown[:3] == VM[2:5], shown[:3])
    check("genau fünf Zeilen übrig", len(shown) == 5, len(shown))

    check("debug mitten im Satz ist keine debug-Zeile",
          not Chatter().hidden("warning: debug: is only a word here"))
    check("eingerückte Zeile ohne Traceback bleibt", not Chatter().hidden("  setting up /etc..."))
    check("farbige debug-Zeile verborgen", Chatter().hidden("\x1b[2mdebug: nixos_rebuild.process: x\x1b[0m"))

    # Chained exceptions: each traceback is cut, each exception line stays.
    ch = Chatter()
    chain = ["Traceback (most recent call last):", '  File "a.py", line 1, in f', "    g()",
             "KeyError: 'x'", "",
             "During handling of the above exception, another exception occurred:", "",
             "Traceback (most recent call last):", '  File "b.py", line 2, in h',
             "RuntimeError: kaputt", "danach"]
    shown = [ln for ln in chain if not ch.hidden(ln)]
    check("verkettete Ausnahmen: beide Ursachen bleiben",
          "KeyError: 'x'" in shown and "RuntimeError: kaputt" in shown and shown[-1] == "danach", shown)
    check("verkettete Ausnahmen: keine Rahmen", not any(ln.startswith("  ") for ln in shown), shown)

# The loop itself is a closure of the job; its wiring is read from the source.
src = (ROOT / "nico" / "routes" / "rebuild.py").read_text(encoding="utf-8")
loop = src[src.index("for raw_line in proc.stdout:"):src.index("proc.wait()")]
plain = loop[loop.index("else:"):]
check("jede Zeile wird befragt, unabhängig vom Schalter",
      "own_detail = chatter.hidden(line)" in plain)
check("Monitor: verborgen nur bei ausgeschaltetem Schalter",
      "if not own_detail or _job_verbose():" in plain)
check("Logdatei bekommt die Zeile vor jeder Entscheidung",
      plain.index("log_lines.append(line)") < plain.index("chatter.hidden(line)"))
check("Fehlerzeilen und Aktivierung sehen sie ebenfalls",
      plain.index("activation.consider(line)") < plain.index("chatter.hidden(line)")
      and plain.index("err_lines.consider_raw(line)") < plain.index("chatter.hidden(line)"))

print("\n" + ("ALLE PRÜFUNGEN BESTANDEN" if not fails else f"FEHLGESCHLAGEN: {fails}"))
sys.exit(1 if fails else 0)
