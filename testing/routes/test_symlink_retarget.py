#!/usr/bin/env python3
"""/etc/nixos-Symlink umstellen, 2026-11-22.

Martins Fund in der VM: /etc/nixos zeigte noch auf eine frühere Config.
Anlegen scheiterte mit ERR_SYMLINK_OTHER_TARGET, einen Weg zum Umstellen gab
es nicht, und „In /etc/nixos nachsehen“ las die alte Config. Jetzt stellt
/api/symlink/retarget den Link um; /etc/nixos.bak bleibt unberührt.

Am echten /etc/nixos wird nichts geändert: ETC_NIXOS zeigt auf einen Link im
Temp-Verzeichnis, sudo ist durch einen Ersatz ersetzt, der den Befehl ohne
sudo ausführt.

Lauf: nix-shell shell.nix --run "python testing/routes/test_symlink_retarget.py"
"""
import json
import os
import re
import subprocess
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
(cfg / "configuration.nix").write_text("{ ... }:\n{\n}\n", encoding="utf-8")

from nico import server  # noqa: E402
from nico.routes import symlink as sl  # noqa: E402

fails = []


def check(label, cond, got=None):
    print(("  OK   " if cond else "  FAIL ") + label
          + ("" if cond or got is None else f"  (war: {got})"))
    if not cond:
        fails.append(label)


etc = Path(tempfile.mkdtemp(prefix="nico-etc-"))
link = etc / "nixos"
other = Path(tempfile.mkdtemp(prefix="nico-other-"))
sl.ETC_NIXOS = link

ran = []
real_run = subprocess.run
fail_next = {"on": False}


def fake_run(cmd, *a, **k):
    if cmd[:2] == ["sudo", "-S"]:
        ran.append(cmd)
        if fail_next["on"]:
            return subprocess.CompletedProcess(cmd, 1, "", "ln: Zugriff verweigert")
        return real_run(cmd[2:], capture_output=True, text=True)
    return real_run(cmd, *a, **k)


subprocess.run = fake_run

app = server.create_app()
client = app.test_client()
page = client.get("/").get_data(as_text=True)
m = re.search(r'<meta name="csrf-token" content="([^"]+)"', page)
hdr = {"X-CSRF-Token": m.group(1) if m else "", "Content-Type": "application/json"}


def post():
    return client.post("/api/symlink/retarget", headers=hdr, data="{}")


print("1. kein Symlink")
link.mkdir()
r = post()
check("Verzeichnis statt Link: 400 ERR_NOT_A_SYMLINK",
      r.status_code == 400 and r.get_json().get("error") == "ERR_NOT_A_SYMLINK", r.get_json())
link.rmdir()

print("2. Link auf ein anderes Verzeichnis")
link.symlink_to(other)
r = post()
d = r.get_json()
check("umgestellt", r.status_code == 200 and d.get("ok"), d)
check("Befehl: sudo -S ln -sfn <config> <link>, ohne Shell",
      ran[-1:] == [["sudo", "-S", "ln", "-sfn", str(cfg.resolve()), str(link)]], ran)
check("Link zeigt jetzt aufs Config-Verzeichnis", link.resolve() == cfg.resolve(), link.resolve())
check("alter Ziel-Pfad gemeldet", d.get("old_target") == str(other.resolve()), d)
check("altes Verzeichnis unberührt", other.is_dir())

print("3. zeigt schon hierher")
n = len(ran)
r = post()
check("already_done, kein Befehl", r.get_json().get("already_done") and len(ran) == n, r.get_json())

print("4. Fehler von sudo")
link.unlink()
link.symlink_to(other)
fail_next["on"] = True
r = post()
check("ERR_SYMLINK_FAILED mit Meldung",
      r.status_code == 500 and r.get_json().get("error") == "ERR_SYMLINK_FAILED"
      and "verweigert" in r.get_json().get("detail", ""), r.get_json())
check("Link unverändert", link.resolve() == other.resolve())
fail_next["on"] = False

print("5. ohne CSRF")
r = client.post("/api/symlink/retarget", headers={"Content-Type": "application/json"}, data="{}")
check("abgelehnt", r.status_code in (400, 403), r.status_code)

subprocess.run = real_run
print("\n" + ("ALLE PRÜFUNGEN BESTANDEN" if not fails else f"FEHLGESCHLAGEN: {fails}"))
sys.exit(1 if fails else 0)
