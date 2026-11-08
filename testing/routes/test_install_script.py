#!/usr/bin/env python3
"""install.sh ersetzt eine vorhandene Installation wirklich.

Gefunden in der VM (2026-11-08): `nix profile add` auf ein schon installiertes
NiCo ändert nichts, warnt nur und endet mit 0. Das Menü bot danach „Start NiCo
now?“ an und startete die alte Version.

`nix` wird durch ein kleines Skript ersetzt, das sich so verhält wie gemessen
und jeden Aufruf mitschreibt. Es läuft nichts gegen das echte Profil.

Lauf: nix-shell shell.nix --run "python testing/routes/test_install_script.py"
"""
import os, pathlib, stat, subprocess, sys, tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent

fails = []
def check(label, cond, got=None):
    print(("  OK   " if cond else "  FAIL ") + label + ("" if cond or got is None else f"  (war: {got})"))
    if not cond: fails.append(label)


OLD = "3162d8b4c380fd31b25572f450ec6553cb9caec8"
FAKE_NIX = r'''#!/bin/sh
echo "$*" >> "$STATE/calls"
case "$*" in
  *"flake metadata"*) exit 1 ;;
  *"--help"*) exit 0 ;;
  *"profile list"*)
    [ -f "$STATE/installed" ] && cat "$STATE/installed"
    exit 0 ;;
  *"profile rollback"*) cp "$STATE/backup" "$STATE/installed"; exit 0 ;;
  *"profile remove"*)
    [ -f "$STATE/remove_fails" ] && exit 1
    cp "$STATE/installed" "$STATE/backup"; rm "$STATE/installed"; exit 0 ;;
  *"profile add"*)
    if [ -f "$STATE/installed" ]; then echo "warning: 'nico' is already added" >&2; exit 0; fi
    [ -f "$STATE/add_fails" ] && exit 1
    printf 'Name: nico\nOriginal flake URL: github:DSpayreR6/nico/dev\nLocked flake URL: github:DSpayreR6/nico/df69bf22aaaa?narHash=x\nStore paths: /nix/store/abc-nico-0.16.0-dev\n' > "$STATE/installed"
    exit 0 ;;
esac
exit 0
'''


def installed_text(rev=OLD, version="0.14.0-dev", url="github:DSpayreR6/nico/dev"):
    return (f"\x1b[1mName:\x1b[0m nico\nOriginal flake URL: {url}\n"
            f"Locked flake URL: github:DSpayreR6/nico/{rev}?narHash=x\n"
            f"Store paths: /nix/store/abc-nico-{version}\n")


def run(keys: str, installed=None, **flags):
    state = pathlib.Path(tempfile.mkdtemp(prefix="nico-inst-"))
    bindir = state / "bin"
    bindir.mkdir()
    nix = bindir / "nix"
    nix.write_text(FAKE_NIX, encoding="utf-8")
    nix.chmod(nix.stat().st_mode | stat.S_IEXEC)
    if installed:
        (state / "installed").write_text(installed, encoding="utf-8")
        program = bindir / "nico"
        program.write_text("#!/bin/sh\necho NICO-GESTARTET\n", encoding="utf-8")
        program.chmod(program.stat().st_mode | stat.S_IEXEC)
    for name in flags:
        (state / name).write_text("", encoding="utf-8")
    env = dict(os.environ, PATH=f"{bindir}:{os.environ['PATH']}", STATE=str(state),
               HOME=str(state), NO_COLOR="1")
    r = subprocess.run(["sh", str(ROOT / "install.sh")], input=keys, env=env,
                       capture_output=True, text=True, timeout=30)
    calls = (state / "calls").read_text(encoding="utf-8").splitlines() if (state / "calls").exists() else []
    now = (state / "installed").read_text(encoding="utf-8") if (state / "installed").exists() else ""
    return r.stdout + r.stderr, [c for c in calls if "metadata" not in c and "--help" not in c and "profile list" not in c], now


# Eingaben: Menüpunkt, Enter für „Run this command?“, n für „Start NiCo now?“,
# Enter für „Press Enter“, 0 zum Beenden.
print("Menü zeigt die installierte Version")
out, calls, _ = run("0\n", installed_text())
check("Version und Commit bei Punkt 1", "the installed NiCo (0.14.0-dev, 3162d8b)" in out, out[-500:])
out, calls, _ = run("0\n")
check("ohne Installation: wie bisher", "the installed NiCo (not installed)" in out)

print("Frische Installation")
out, calls, now = run("6\n\nn\n\n0\n")
check("nur add", len(calls) == 1 and "profile add github:DSpayreR6/nico/dev --refresh" in calls[0], calls)
check("installiert", "0.16.0-dev" in now)
check("kein Ersetzen-Hinweis", "already installed" not in out)

print("Vorhandene Installation wird ersetzt")
out, calls, now = run("6\n\nn\n\n0\n", installed_text())
check("zuerst remove, dann add", len(calls) == 2 and "profile remove nico" in calls[0]
      and "profile add github:DSpayreR6/nico/dev --refresh" in calls[1], calls)
check("jetzt neue Version", "0.16.0-dev" in now and OLD not in now, now)
check("Hinweis nennt die Quelle", "already installed, from github:DSpayreR6/nico/dev" in out, out[-500:])
check("Befehl steht vollständig da",
      "profile remove nico && nix profile add github:DSpayreR6/nico/dev --refresh" in out, out[-500:])

print("Wechsel auf main")
out, calls, _ = run("3\n\nn\n\n0\n", installed_text())
check("main mit --refresh", any(c.endswith("github:DSpayreR6/nico --refresh") for c in calls), calls)

print("Abgelehnt")
out, calls, now = run("6\nn\n0\n", installed_text())
check("nichts ausgeführt", calls == [], calls)
check("alte Version bleibt", OLD in now)

print("Neu installieren scheitert")
out, calls, now = run("6\n\ny\n\n0\n", installed_text(), add_fails=1)
check("bietet Rückkehr an", any("profile rollback" in c for c in calls), calls)
check("alte Version wieder da", OLD in now, now)
out, calls, now = run("6\n\nn\n\n0\n", installed_text(), add_fails=1)
check("Rückkehr abgelehnt: nichts passiert", not any("rollback" in c for c in calls), calls)

print("Entfernen scheitert")
out, calls, now = run("6\n\n\n0\n", installed_text(), remove_fails=1)
check("keine Rückkehr angeboten", not any("rollback" in c for c in calls), calls)
check("add läuft nicht", not any("profile add" in c for c in calls), calls)
check("alte Version bleibt", OLD in now, now)

print()
if fails:
    print(f"{len(fails)} FEHLER:", *fails, sep="\n  ")
    sys.exit(1)
print("Alle Prüfungen bestanden.")
