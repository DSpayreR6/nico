#!/usr/bin/env python3
"""Fehlerzeilen eines Rebuilds überleben die Kappung des Logs.

Aus Martins Fall vom 2026-11-01: Der Rebuild scheiterte, weil
`python3.13-fpdf2` seine eigene Testsuite nicht besteht. Zu sehen war davon
nichts. Zwei Ursachen, beide hier geprüft:

  1. Die Zeilen des Builders kommen als `@nix`-result-Typ 101 bzw. 107 und
     wurden ungelesen verworfen. Die pytest-Zeile mit dem Paketnamen hat den
     Bildschirm nie erreicht.
  2. `_write_rebuild_log` kappt auf 5 MB und behält das **Ende**. Bei
     `--log-format internal-json -v` besteht das Ende aus
     Store-Optimierungs-Zeilen; von 2.540.770 Zeilen fiel die eine mit der
     Ursache weg. Übrig blieb `Cannot build … 1 dependency failed`, das
     nichts benennt.

Geprüft wird:
   1. result-Typ 101 und 107 liefern ihren Text, andere nicht
   2. die pytest-FAILED-Zeile wird als Fehlerzeile erkannt – trotz
      ANSI-Einfärbung, an der ein `^FAILED` scheitern würde
   3. der Paketname steht davor (sonst nennt die Zeile das Paket nicht)
   4. harmlose Zeilen mit dem Wort "error" im Dateinamen zählen nicht
      (`femtools/errors.py` lief im echten Fall zehnmal durch)
   5. nix-Meldungen: nur level 0 zählt. Stufe 1 zählte zunächst mit und war
      falsch - `warning: Git tree … is dirty` kommt bei jedem Flake-Rebuild
      und verdrängte die Ursache (gemessen 2026-11-01)
  5b. die Aktivierungsphase schreibt reinen Text auf stderr, kein JSON; die
      Zeile `Failed to restart …` muss erkannt werden, die vier harmlosen
      "the following …"-Zeilen nicht
   6. die Fehlerzeilen stehen im Log **oberhalb** des gekappten Teils und
      überleben, wenn 5 MB Rauschen hinterherkommen
   7. mehr als 200 Fehlerzeilen: die ersten bleiben, der Rest wird gezählt –
      die erste ist die Ursache, der Rest die Kaskade
   8. ohne Fehlerzeilen bleibt der Kopf weg

Gegen den Stand vor dem Fix fallen 27 der Pruefungen (gemessen 2026-11-01).
Der Test laeuft dort absichtlich bis zum Ende durch: fehlende Teile werden
als Fund gezaehlt und durch wirkungslose Attrappen ersetzt, damit zu sehen
ist, was der fehlende Puffer anrichtet, und nicht nur, dass er fehlt.

Lauf: nix-shell shell.nix --run "python testing/routes/test_rebuild_errorlines.py"
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

from nico.routes import rebuild as rb  # noqa: E402

fails = []


def line(obj, i=0):
    """Die i-te Fehlerzeile, '' wenn es sie nicht gibt.

    Damit meldet der Test gegen einen Stand ohne den Fix Befunde statt mit
    IndexError abzubrechen.
    """
    return obj.lines[i] if len(obj.lines) > i else ""


def check(label, cond, got=None):
    print(("  OK   " if cond else "  FAIL ") + label
          + ("" if cond or got is None else f"  (war: {got})"))
    if not cond:
        fails.append(label)


# Gegen einen Stand ohne den Fix soll der Test seine Befunde melden statt mit
# AttributeError abzubrechen: sonst sieht man nur, dass etwas fehlt, und nicht,
# was dadurch kaputt ist. Fehlende Teile werden als Fund gezaehlt und durch
# wirkungslose Attrappen ersetzt, damit der Rest durchlaeuft.
import inspect

if not hasattr(rb._ErrorLines, "consider_raw"):
    check("_ErrorLines.consider_raw existiert", False)
    rb._ErrorLines.consider_raw = lambda self, line: False
if not hasattr(rb, "_RE_RAW_ERR"):
    check("rb._RE_RAW_ERR existiert", False)
    import re as _re_shim
    rb._RE_RAW_ERR = _re_shim.compile(r"(?!)")

if not hasattr(rb, "_build_log_text"):
    check("rb._build_log_text existiert", False)
    rb._build_log_text = lambda data: ""

if not hasattr(rb, "_ErrorLines"):
    check("rb._ErrorLines existiert", False)

    class _ErrorLinesStub:
        def __init__(self):
            self.lines, self.dropped = [], 0

        def add(self, line, pkg=""):
            pass

        def consider_msg(self, level, line):
            return False

        def consider_build(self, line, pkg=""):
            return False

        def consider_raw(self, line):
            return False

        def header(self):
            return []

    rb._ErrorLines = _ErrorLinesStub

if len(inspect.signature(rb._write_rebuild_log).parameters) < 3:
    check("_write_rebuild_log nimmt die Fehlerzeilen an", False)
    _old_write = rb._write_rebuild_log
    rb._write_rebuild_log = lambda path, lines, errors=None: _old_write(path, lines)


# Die echten Zeilen des Durchlaufs vom 2026-11-01, gekürzt. Die Einfärbung
# steht absichtlich drin: ohne sie wäre der Test blind für genau den Grund,
# aus dem ein `^FAILED` nicht greift.
PYTEST_FAILED = (
    "\x1b[31mFAILED\x1b[0m test/image/test_full_page_image.py::"
    "\x1b[1mtest_image_with_explicit_dimensions\x1b[0m - AssertionError: "
    "6732d6d8b5c10606bbcda99fc822c94c != c7b6e70b4b6b72d1b464c65"
)
PYTEST_SUMMARY = (
    "\x1b[31m= \x1b[31m\x1b[1m1 failed\x1b[0m, \x1b[32m1780 passed\x1b[0m "
    "in 80.33s\x1b[31m ="
)
LINK_NOISE = (
    'linking "/nix/store/33lzmha3paymhwfsvwa0qyvyqpd8wqkh-freecad-1.1.3/'
    'Mod/Fem/femtools/errors.py" to "/nix/store/.links/0cvmn55xzdq0y0bsbrgr"'
)
NIX_ERROR = (
    "\x1b[31;1merror:\x1b[0m Cannot build "
    "'\x1b[35;1m/nix/store/m66cl2fgxx3gcbd66ilv96jn7d8g57yv-system-path.drv"
    "\x1b[0m'.\n       Reason: \x1b[31;1m1 dependency failed\x1b[0m."
)

print("\n1) result-Typ: nur 101 und 107 tragen Builder-Ausgabe")

check("Typ 101 liefert den Text",
      rb._build_log_text({"action": "result", "type": 101,
                          "fields": [PYTEST_FAILED]}) == PYTEST_FAILED)
check("Typ 107 liefert den Text",
      rb._build_log_text({"action": "result", "type": 107,
                          "fields": ["post-build hook failed with exit code 1"]})
      == "post-build hook failed with exit code 1")
check("Typ 105 (Fortschritt) liefert nichts",
      rb._build_log_text({"action": "result", "type": 105,
                          "fields": [31, 31, 0, 1]}) == "")
check("Typ 106 (resSetExpected) liefert nichts – fields[0] ist dort eine Zahl",
      rb._build_log_text({"action": "result", "type": 106,
                          "fields": [101, 165979123]}) == "")
check("msg-Aktion liefert nichts",
      rb._build_log_text({"action": "msg", "level": 0, "msg": NIX_ERROR}) == "")
check("fields leer liefert nichts",
      rb._build_log_text({"action": "result", "type": 101, "fields": []}) == "")
check("kaputtes fields wirft nicht",
      rb._build_log_text({"action": "result", "type": 101}) == "")

print("\n2) Erkennung: was eine Fehlerzeile ist und was nicht")

e = rb._ErrorLines()
check("pytest-FAILED trotz ANSI erkannt",
      e.consider_build(PYTEST_FAILED, "python3.13-fpdf2-2.8.9"))
check("Paketname steht vor der Zeile",
      line(e).startswith("python3.13-fpdf2-2.8.9: FAILED test/image/"),
      line(e)[:60])
check("ANSI ist aus der behaltenen Zeile heraus",
      "\x1b" not in line(e))
check("der Hash-Vergleich bleibt lesbar erhalten",
      "6732d6d8b5c10606bbcda99fc822c94c" in line(e))

check("gcc-Fehler erkannt",
      e.consider_build("src/foo.cpp:76:23: error: 'bar' was not declared",
                       "bambu-studio"))
check("nix-Abbruch erkannt",
      e.consider_build("builder for '/nix/store/x.drv' failed with exit code 1"))

n_before = len(e.lines)
check("Verlinkungs-Rauschen mit errors.py im Pfad zählt nicht",
      not e.consider_build(LINK_NOISE, "freecad-1.1.3"))
check("gcc-Warnung zählt nicht",
      not e.consider_build("src/foo.cpp:76:23: warning: comparison of integer",
                           "bambu-studio"))
check("pytest-Zusammenfassung ohne FAILED am Anfang zählt nicht",
      not e.consider_build(PYTEST_SUMMARY, "python3.13-fpdf2-2.8.9"))
check("das Wort error mitten in Prosa zählt nicht",
      not e.consider_build("checking for references to error handling", "x"))
check("leere Zeile zählt nicht", not e.consider_build("   "))
check("keine der vier harmlosen Zeilen ist gelandet",
      len(e.lines) == n_before, len(e.lines) - n_before)

print("\n3) nix-Meldungen nach Loglevel")

m = rb._ErrorLines()
check("level 0 (error) zählt", m.consider_msg(0, NIX_ERROR))
# Geaendert am 2026-11-01 nach Martins echtem Lauf: Stufe 1 zaehlte mit, und
# `warning: Git tree … is dirty` kommt bei jedem Flake-Rebuild zweimal. Im
# Kasten stand damit nur diese Warnung, waehrend die Ursache fehlte.
check("level 1 (warning) zählt NICHT mehr",
      not m.consider_msg(1, "warning: system.stateVersion is not set"))
check("der Dauerläufer 'Git tree is dirty' bleibt draußen",
      not m.consider_msg(1, "warning: Git tree '/home/martin/.nixos-config' is dirty"))
check("level 4 (talkative) zählt nicht – das war das Rauschen",
      not m.consider_msg(4, LINK_NOISE))
check("level 7 zählt nicht", not m.consider_msg(7, "vomit"))
check("fehlendes level zählt nicht", not m.consider_msg(None, NIX_ERROR))
check("level 0 mehrzeilig behält beide Zeilen",
      "1 dependency failed" in line(m) and "Cannot build" in line(m))

print("\n3b) Die Aktivierungsphase schreibt reinen Text, kein JSON")

# Wörtlich aus Martins Lauf vom 2026-11-01, 12:53. Der Bau lief durch, die
# Umstellung auch - eine Unit liess sich nur nicht neu starten und war zwei
# Sekunden spaeter von selbst in Ordnung. Zu sehen bekam er einen
# Python-Traceback, weil die Aktivierungsausgabe gar nicht nach Fehlern
# durchsucht wurde.
AKTIV_FEHLER = "Failed to restart network-addresses-vboxnet0.service"
AKTIV_HARMLOS = [
    "stopping the following units: accounts-daemon.service, avahi-daemon.service",
    "NOT restarting the following changed units: display-manager.service",
    "restarting the following units: home-manager-martin.service",
    "the following new units were started: cups-browsed.service",
    "stopping the following user units: pipewire.service",
    "activating the configuration...",
    "restarting systemd...",
]

r = rb._ErrorLines()
check("die gescheiterte Unit wird erkannt", r.consider_raw(AKTIV_FEHLER))
check("und steht wörtlich da", line(r) == AKTIV_FEHLER, line(r))
for h in AKTIV_HARMLOS:
    check(f"ignoriert: {h[:42]}", not r.consider_raw(h))
check("nur die eine Zeile ist gelandet", len(r.lines) == 1, r.lines)

check("Zusammenfassung von switch-to-configuration zählt",
      rb._ErrorLines().consider_raw("warning: the following units failed: foo.service"))
check("gescheitertes Aktivierungsskript zählt",
      rb._ErrorLines().consider_raw("activation script snippet 'setupSecrets' failed (1)"))
check("Job for … failed zählt",
      rb._ErrorLines().consider_raw("Job for sshd.service failed because the control process exited"))
check("eine Fehlermeldung mit error: zählt",
      rb._ErrorLines().consider_raw("error: cannot coerce a set to a string"))
check("eine Leerzeile zählt nicht", not rb._ErrorLines().consider_raw("   "))
check("der eigene Log-Hinweis zählt nicht",
      not rb._ErrorLines().consider_raw("── Log gespeichert: /home/x/.cache/nico/nixos-rebuild.log ──"))

print("\n4) Die Fehlerzeilen überleben die Kappung")

log = Path(tempfile.mkdtemp(prefix="nico-log-")) / "nixos-rebuild.log"

# So lag der echte Fall: die Ursache am Anfang, danach 5 MB Rauschen. Eine
# Zeile ist ~100 Bytes, 70.000 davon reichen nicht – es muss über _LOG_MAX_BYTES
# gehen, sonst kappt nichts und der Test beweist nichts.
noise = ['@nix {"action":"msg","level":4,"msg":"' + "x" * 160 + '"}'] * 40000
total_bytes = sum(len(n.encode()) + 1 for n in noise)
check("das Rauschen überschreitet die 5-MB-Grenze wirklich",
      total_bytes > rb._LOG_MAX_BYTES, total_bytes)

raw = ['@nix {"action":"result","type":101,"id":1,"fields":["FAILED test/..."]}'] + noise

errs = rb._ErrorLines()
errs.consider_build(PYTEST_FAILED, "python3.13-fpdf2-2.8.9")
errs.consider_msg(0, NIX_ERROR)

check("Log geschrieben", rb._write_rebuild_log(log, raw, errs))
text = log.read_text(encoding="utf-8")
check("Kopfzeile steht drin", "── Error lines of this run ──" in text)
check("der Paketname steht im Log", "python3.13-fpdf2-2.8.9" in text)
check("die FAILED-Zeile steht im Log",
      "test_image_with_explicit_dimensions" in text)
check("der Kappungshinweis steht drin", "── Truncated:" in text)
check("Kappungshinweis ist englisch, nicht deutsch",
      "Gekürzt" not in text)
check("die Fehlerzeilen stehen VOR dem Kappungshinweis",
      0 <= text.find("python3.13-fpdf2-2.8.9") < text.find("── Truncated:"))
check("gekappt wurde tatsächlich",
      "older lines removed" in text)

# Der Nachweis, dass es ohne den Puffer schiefgeht: dasselbe Log ohne ihn.
check("ohne Fehlerzeilen-Puffer fehlt der Paketname – der alte Zustand",
      rb._write_rebuild_log(log, raw)
      and "python3.13-fpdf2-2.8.9" not in log.read_text(encoding="utf-8"))

print("\n5) Obergrenze: die ersten bleiben, der Rest wird gezählt")

many = rb._ErrorLines()
many.consider_build("error: die erste und damit die Ursache")
for i in range(400):
    many.consider_build(f"error: Kaskade {i}")
check("auf 200 Zeilen begrenzt", len(many.lines) == 200, len(many.lines))
check("die erste Zeile ist die Ursache, nicht die letzte Kaskade",
      line(many) == "error: die erste und damit die Ursache", line(many))
check("der Rest ist gezählt", many.dropped == 201, many.dropped)
check("der Kopf nennt die weggelassenen",
      "── 201 further error lines not kept ──" in many.header())

print("\n6) Ohne Fehlerzeilen kein Kopf")

empty = rb._ErrorLines()
check("header() ist leer", empty.header() == [])
log2 = log.parent / "zweites.log"
rb._write_rebuild_log(log2, ["nur eine Zeile"], empty)
t2 = log2.read_text(encoding="utf-8")
check("kein Kopf im Log", "Error lines of this run" not in t2)
check("die Zeile selbst steht drin", "nur eine Zeile" in t2)

print("\n7) Ein echter @nix-Strom, Zeile für Zeile wie im Job")

stream = [
    {"action": "start", "id": 7, "type": 105,
     "text": "building '/nix/store/yrc30xh4q2g7-python3.13-fpdf2-2.8.9.drv'"},
    {"action": "result", "type": 101, "id": 7, "fields": [LINK_NOISE]},
    {"action": "result", "type": 101, "id": 7, "fields": [PYTEST_FAILED]},
    {"action": "result", "type": 105, "id": 7, "fields": [1, 1, 0, 1]},
    {"action": "msg", "level": 4, "msg": LINK_NOISE},
    {"action": "msg", "level": 0, "msg": NIX_ERROR},
]
got = rb._ErrorLines()
for data in stream:
    if data.get("action") == "msg":
        got.consider_msg(data.get("level"), data.get("msg", ""))
    else:
        txt = rb._build_log_text(data)
        if txt.strip():
            got.consider_build(txt, "python3.13-fpdf2-2.8.9")
check("aus sechs Zeilen bleiben genau zwei", len(got.lines) == 2, got.lines)
check("die erste nennt Paket und Testfall",
      line(got).startswith("python3.13-fpdf2-2.8.9: FAILED"), line(got))
check("die zweite ist nix' eigener Abbruch",
      "Cannot build" in line(got, 1), line(got, 1))

print()
if fails:
    print("FEHLGESCHLAGEN:", len(fails))
    for f in fails:
        print("  -", f)
    sys.exit(1)
print("Alle Prüfungen bestanden.")
