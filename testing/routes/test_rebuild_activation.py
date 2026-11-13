#!/usr/bin/env python3
"""Bau gelungen, neues System aktiv, aber ein Dienst startete nicht neu.

Aus Martins Lauf vom 2026-11-13 (und schon vom 2026-11-01): Der Rebuild
baute alles, setzte das neue System und schaltete um. Dabei stoppt NixOS
die VirtualBox-Schnittstelle `vboxnet0` und will drei Sekunden später
`network-addresses-vboxnet0.service` neu starten – die Schnittstelle ist in
dem Moment noch nicht wieder da. switch-to-configuration endet dafür mit
Status 4, nixos-rebuild mit einem Fehler, und NiCo zeigte rot „Rebuild
fehlgeschlagen". Das System lief aber auf dem neuen Stand, der Dienst war
Sekunden später wieder aktiv, und ein zweiter Rebuild ging ohne Meldung
durch. Ein Sicherungspunkt wurde nicht angeboten.

Entschieden (Martin, 2026-11-13): eigener Ausgang, gelb statt rot – Warnung,
nicht bloße Info, denn Status 4 kann genauso sshd treffen. Der
Sicherungspunkt wird angeboten wie nach einem gelungenen Rebuild.

Geprüft wird:
  1. Der echte Protokollausschnitt ergibt: Status 4, Dienst benannt,
     „teilweise" ja.
  2. Die vier harmlosen „the following …"-Zeilen zählen nicht als Dienst.
  3. Nur Status 4 ist dieser Fall: ein gescheitertes Aktivierungsskript
     (Status 2) oder ein gescheiterter Bau bleiben ein Fehler.
  4. Die Sammelzeile `the following units failed: a, b` wird gelesen,
     Doppelte fallen weg, ANSI-Farben stören nicht.
  5. Das done-Ereignis trägt den Ausgang, die Oberfläche wertet ihn aus
     und bietet den Sicherungspunkt an; Texte in sieben Sprachen.

Lauf: nix-shell shell.nix --run "python testing/routes/test_rebuild_activation.py"
"""
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
os.environ["XDG_CONFIG_HOME"] = tempfile.mkdtemp(prefix="nico-xdg-")

from nico.routes import rebuild as rb  # noqa: E402

fails = []
def check(label, cond, got=None):
    print(("  OK   " if cond else "  FAIL ") + label + ("" if cond or got is None else f"  (war: {got})"))
    if not cond:
        fails.append(label)

SYS = "/nix/store/6lcrpmz30ypisgnahjhrc0qalq73bsc2-nixos-system-nix-desktop-26.11.20261111.d4f25a3"
# Verbatim from ~/.cache/nico/nixos-rebuild.log, 2026-11-13 16:44.
REAL = f"""switching to system configuration {SYS}
stopping the following units: accounts-daemon.service, avahi-daemon.service, cups.service, docker.service
NOT restarting the following changed units: display-manager.service, plymouth-start.service
activating the configuration...
setting up /etc...
restarting systemd...
reloading user units for martin...
restarting the following units: home-manager-martin.service, network-addresses-vboxnet0.service, nix-daemon.service, sshd.service
starting the following units: accounts-daemon.service, avahi-daemon.socket, cups.socket
Failed to restart network-addresses-vboxnet0.service
the following new units were started: NetworkManager-dispatcher.service, sysinit-reactivation.target
switching to system configuration {SYS} failed (status 4)
nixos-rebuild-switch-to-configuration.service: Main process exited, code=exited, status=4/NOPERMISSION
nixos-rebuild-switch-to-configuration.service: Failed with result 'exit-code'.
Traceback (most recent call last):
subprocess.CalledProcessError: Command '['systemd-run', '-E', 'LOCALE_ARCHIVE']' returned non-zero exit status 4.
""".splitlines()

Act = getattr(rb, "_Activation", None)
check("es gibt _Activation", Act is not None)
if Act is None:
    class Act:                                   # inert stand-in, see errorlines test
        status = None; units = []; partial = False
        def consider(self, line): pass

def run(lines):
    a = Act()
    for l in lines:
        a.consider(l)
    return a

print("1. Der echte Lauf")
a = run(REAL)
check("Status 4 erkannt", a.status == 4, a.status)
check("gilt als teilweise gelungen", a.partial is True)
check("der Dienst ist benannt", a.units == ["network-addresses-vboxnet0.service"], a.units)

print("\n2. Harmlose Zeilen")
a = run([l for l in REAL if not l.startswith("Failed to restart")])
check("ohne die Failed-Zeile: kein Dienst, aber weiter Status 4", a.units == [] and a.partial, (a.units, a.status))
a = run(REAL[:9])
check("ohne Abschlusszeile: kein Sonderfall", a.partial is False and a.status is None)

print("\n3. Nur Status 4")
for st in (1, 2, 3, 100):
    a = run([f"switching to system configuration {SYS} failed (status {st})"])
    check(f"Status {st} bleibt ein Fehler", a.partial is False and a.status == st, (a.status, a.partial))
a = run(["error: builder for '/nix/store/abc-foo.drv' failed with exit code 1",
         "error: Cannot build '/nix/store/x.drv'."])
check("gescheiterter Bau: kein Sonderfall", a.partial is False)
a = run(["Activation script snippet 'users' failed (1)",
         f"switching to system configuration {SYS} failed (status 2)"])
check("gescheitertes Aktivierungsskript: kein Sonderfall", a.partial is False)

print("\n4. Sammelzeile, Doppelte, Farben")
a = run(["warning: the following units failed: foo.service, bar.mount",
         "Failed to start foo.service",
         "\x1b[31mFailed to reload baz.service\x1b[0m",
         "Failed to restart with spaces in it",
         f"switching to system configuration {SYS} failed (status 4)"])
check("alle drei, jeder einmal, in Reihenfolge",
      a.units == ["foo.service", "bar.mount", "baz.service"], a.units)

print("\n5. Ereignis, Oberfläche, Texte")
src = (ROOT / "nico" / "routes" / "rebuild.py").read_text(encoding="utf-8")
check("done-Ereignis trägt den Ausgang",
      "'activated_with_warnings'" in src and "'unrestarted_units'" in src)
js = (ROOT / "nico" / "static" / "js" / "rebuild.js").read_text(encoding="utf-8")
check("die Oberfläche wertet ihn aus", "activated_with_warnings" in js)
check("eigener gelber Zustand", "result-warning" in js)
check("Sicherungspunkt auch in diesem Fall", "msg.success || activatedWarn" in js)
css = (ROOT / "nico" / "static" / "style.css").read_text(encoding="utf-8")
check("Stil dafür ist gelb", ".rebuild-result.result-warning" in css
      and "var(--yellow)" in css.split(".rebuild-result.result-warning", 1)[-1][:80])
for p in sorted((ROOT / "nico" / "static" / "lang").glob("*.json")):
    texts = json.loads(p.read_text(encoding="utf-8"))
    for key in ("rebuild.activatedWarn", "rebuild.unrestartedUnits",
                "rebuild.unrestartedNone", "rebuild.unrestartedHint"):
        check(f"{p.stem}: {key}", bool(texts.get(key)) and texts.get(key) != "__TODO__")

print()
if fails:
    print(f"{len(fails)} FEHLER:", *fails, sep="\n  ")
    sys.exit(1)
print("Alle Prüfungen bestanden.")
