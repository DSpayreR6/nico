#!/usr/bin/env python3
"""Zeitmaschine, Seite System: Systemstände (Generationen), 2026-11-21.

Martins Wunsch: einen älteren Systemstand auswählen und ihn laufen lassen –
jetzt oder ab dem nächsten Start; dazu ein schneller Rollback. Gelesen wird
nur mit der Freigabe read_host_generations (hostread.py).

Geprüft wird – mit einem nachgebauten Profilverzeichnis, am echten System
wird nichts umgestellt (die Befehle sind im Test durch echo ersetzt):
  1. Liste: Nummern absteigend, Datum, NixOS-Version, Kernel, Markierungen
     läuft jetzt / gestartet / nächster Start.
  2. Befehle: nix-env --switch-generation N, dann switch-to-configuration
     switch|boot; ein anderer Modus wird abgelehnt.
  3. Routen ohne Freigabe: Liste allowed=false, Umschalten 403.
  4. Routen mit Freigabe: unbekannte Nummer 400, gültige Nummer läuft als
     Job im Rebuild-Monitor und endet mit kind=generation. Einen eigenen
     Rollback gibt es nicht (Martin, 2026-11-21) – rollback: true ohne
     Nummer wird abgelehnt.

Lauf: nix-shell shell.nix --run "python testing/routes/test_generations.py"
"""
import json
import os
import re
import sys
import tempfile
import time
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
(cfg / "configuration.nix").write_text("{ ... }:\n{\n}\n", encoding="utf-8")

from nico import generations, hostread, server  # noqa: E402
from nico.routes import rebuild as rb  # noqa: E402

fails = []


def check(label, cond, got=None):
    print(("  OK   " if cond else "  FAIL ") + label
          + ("" if cond or got is None else f"  (war: {got})"))
    if not cond:
        fails.append(label)


# A fake /nix/var/nix/profiles with three generations.
base = Path(tempfile.mkdtemp(prefix="nico-gen-"))
store = base / "store"
profiles = base / "profiles"
profiles.mkdir()
for n, ver, kern in ((1, "25.11.1", "6.12.1"), (2, "26.05.2", "6.18.60"), (3, "26.11.3", "6.18.63")):
    sysdir = store / f"aaaa{n}-nixos-system-{n}"
    (store / f"kern{n}-linux-{kern}").mkdir(parents=True)
    (store / f"kern{n}-linux-{kern}" / "bzImage").write_text("k")
    sysdir.mkdir(parents=True)
    (sysdir / "nixos-version").write_text(ver + "\n")
    os.symlink(store / f"kern{n}-linux-{kern}" / "bzImage", sysdir / "kernel")
    os.symlink(sysdir, profiles / f"system-{n}-link")
os.symlink("system-3-link", profiles / "system")
current = base / "current"
booted = base / "booted"
os.symlink(store / "aaaa2-nixos-system-2", current)
os.symlink(store / "aaaa1-nixos-system-1", booted)

# ── 1. Liste ─────────────────────────────────────────────────────────────────
print("1. Liste")
gens = generations.list_generations(profiles, current, booted)
check("drei Stände, absteigend", [g.number for g in gens] == [3, 2, 1], [g.number for g in gens])
g3, g2, g1 = gens
check("Version und Kernel", (g3.nixos_version, g3.kernel) == ("26.11.3", "6.18.63"),
      (g3.nixos_version, g3.kernel))
check("Datum im Format JJJJ-MM-TT HH:MM", re.fullmatch(r"\d{4}-\d\d-\d\d \d\d:\d\d", g3.date), g3.date)
check("läuft jetzt: 2", [g.number for g in gens if g.running] == [2])
check("gestartet: 1", [g.number for g in gens if g.booted] == [1])
check("nächster Start: 3", [g.number for g in gens if g.default] == [3])
check("fehlendes Verzeichnis: leere Liste", generations.list_generations(base / "nix") == [])

# ── 2. Befehle ───────────────────────────────────────────────────────────────
print("2. Befehle")
cmds = generations.switch_commands(2, "boot", profiles / "system")
check("nix-env --switch-generation 2",
      cmds[0] == ["sudo", "-S", "nix-env", "-p", str(profiles / "system"), "--switch-generation", "2"],
      cmds[0])
check("switch-to-configuration boot",
      cmds[1] == ["sudo", "-S", str(profiles / "system" / "bin" / "switch-to-configuration"), "boot"],
      cmds[1])
try:
    generations.switch_commands(2, "test")
    check("anderer Modus abgelehnt", False)
except ValueError:
    check("anderer Modus abgelehnt", True)

# ── 3. Routen ohne Freigabe ──────────────────────────────────────────────────
app = server.create_app()
client = app.test_client()
page = client.get("/").get_data(as_text=True)
m = re.search(r'<meta name="csrf-token" content="([^"]+)"', page)
hdr = {"X-CSRF-Token": m.group(1) if m else "", "Content-Type": "application/json"}

print("3. Routen ohne Freigabe")
real_list = generations.list_generations
calls = []
generations.list_generations = lambda *a, **k: (calls.append(1), gens)[1]
d = client.get("/api/generations").get_json()
check("Liste allowed=false, nichts gelesen", d.get("allowed") is False and not calls, (d, calls))
r = client.post("/api/generations/switch", headers=hdr, data=json.dumps({"number": 2, "mode": "switch"}))
check("Umschalten 403", r.status_code == 403 and r.get_json().get("error") == "ERR_HOST_READ_OFF",
      (r.status_code, r.get_json()))

# ── 4. Routen mit Freigabe ───────────────────────────────────────────────────
print("4. Routen mit Freigabe")
hostread.allowed = lambda kind: True
d = client.get("/api/generations").get_json()
check("Liste mit drei Ständen",
      d.get("allowed") is True and len(d.get("generations", [])) == 3, d)
r = client.post("/api/generations/switch", headers=hdr, data=json.dumps({"number": 99, "mode": "switch"}))
check("unbekannte Nummer 400", r.status_code == 400
      and r.get_json().get("error") == "ERR_GENERATION_UNKNOWN", r.get_json())
r = client.post("/api/generations/switch", headers=hdr, data=json.dumps({"number": 2, "mode": "test"}))
check("Modus test abgelehnt", r.status_code == 400, r.get_json())

# Harmless stand-ins for the two root commands.
ran = []


def fake_commands(number, mode, profile=None):
    ran.append((number, mode))
    return [["sh", "-c", f"echo nix-env {number}"],
            ["sh", "-c", f"echo switch-to-configuration {mode}"]]


generations.switch_commands = fake_commands


def run_job(payload):
    r = client.post("/api/generations/switch", headers=hdr, data=json.dumps(payload))
    if r.status_code != 200:
        return r, None
    for _ in range(100):
        st = client.get("/api/rebuild/status").get_json()
        if st.get("job") and not st["job"]["running"]:
            break
        time.sleep(0.1)
    with rb._job_cv:
        events = list(rb._job["events"])
    done = [json.loads(e[6:]) for e in events if '"type": "done"' in e]
    return r, (done[0] if done else None)


r, done = run_job({"number": 1, "mode": "boot"})
check("Stand 1 ab nächstem Start: Job gestartet", r.status_code == 200, r.get_json())
check("Befehle für 1/boot", ran[-1:] == [(1, "boot")], ran)
check("Abschluss kind=generation, erfolgreich",
      done and done.get("kind") == "generation" and done.get("success") is True
      and done.get("number") == 1 and done.get("mode") == "boot", done)
client.post("/api/rebuild/ack", headers=hdr, data="{}")

r, done = run_job({"number": 2, "mode": "switch"})
check("Stand 2 jetzt: Befehle für 2/switch", ran[-1:] == [(2, "switch")], ran)
check("Stand 2 abgeschlossen", done and done.get("success") is True, done)
client.post("/api/rebuild/ack", headers=hdr, data="{}")
r = client.post("/api/generations/switch", headers=hdr,
                data=json.dumps({"rollback": True, "mode": "switch"}))
check("rollback ohne Nummer abgelehnt", r.status_code == 400, r.get_json())

generations.list_generations = real_list
print()
if fails:
    print(f"{len(fails)} Prüfung(en) fehlgeschlagen.")
    sys.exit(1)
print("Alle Prüfungen bestanden.")
