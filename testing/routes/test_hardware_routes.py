#!/usr/bin/env python3
"""Die Routen /api/hardware/* – gegen ein eigenes Config-Verzeichnis.

Laeuft mit `XDG_CONFIG_HOME` auf ein Temp-Verzeichnis, damit die echten
App-Settings in ~/.config/nico/settings.json nicht angefasst werden. Geprueft
wird der Weg, den die Oberflaeche nimmt: Frage lesen, Entscheidung abschicken,
und dass die Datei eines fremden Hosts dabei unberuehrt bleibt. Dazu die drei
Abweisungen, auf die sich der Dialog verlaesst – CSRF, unbekannter Host (ein
Pfad wie "../../etc" darf nicht durchkommen), unbekannte Aktion.

Lauf: nix-shell shell.nix --run "python testing/routes/test_hardware_routes.py"
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
etc = Path(tempfile.mkdtemp(prefix="nico-etc-"))

HERE = "11111111-2222-3333-4444-555555555555"
THERE = "99999999-8888-7777-6666-555555555555"


def hwtext(uuid):
    return ('{ ... }:\n{\n  fileSystems."/" = {\n'
            f'    device = "/dev/disk/by-uuid/{uuid}";\n'
            '    fsType = "ext4";\n  };\n  swapDevices = [ ];\n}\n')


# Two hosts: nix-vm is us and carries the wrong file, nix-laptop must stay put.
for host, uuid in (("nix-vm", THERE), ("nix-laptop", THERE)):
    d = cfg / "hosts" / host
    d.mkdir(parents=True)
    (d / "default.nix").write_text("{ }\n", encoding="utf-8")
    (d / "hardware-configuration.nix").write_text(hwtext(uuid), encoding="utf-8")
(cfg / "flake.nix").write_text("{ }\n", encoding="utf-8")
(cfg / "config.json").write_text(json.dumps({"hosts_dir": "hosts", "flakes": True}),
                                 encoding="utf-8")

(xdg / "nico").mkdir(parents=True)
(xdg / "nico" / "settings.json").write_text(
    json.dumps({"nixos_config_dir": str(cfg), "language": "de"}), encoding="utf-8")

src = etc / "hardware-configuration.nix"
src.write_text(hwtext(HERE), encoding="utf-8")

from nico import hardware as hwmod, server  # noqa: E402

hwmod.local_device_ids = lambda: {HERE}
hwmod.running_host = lambda: "nix-vm"
hwmod.ETC_NIXOS_HW = src

app = server.create_app()
client = app.test_client()

fails = []


def check(label, cond, got=None):
    print(("  OK   " if cond else "  FAIL ") + label
          + ("" if cond or got is None else f"  (war: {got})"))
    if not cond:
        fails.append(label)


# The token is generated per app instance; read it the way the page does.
import re
page = client.get("/").get_data(as_text=True)
m = re.search(r'<meta name="csrf-token" content="([^"]+)"', page)
csrf = m.group(1) if m else ""
check("CSRF-Token aus der Seite gelesen", bool(csrf), page[:0])

hdr = {"X-CSRF-Token": csrf, "Content-Type": "application/json"}

r = client.get("/api/hardware/check")
d = r.get_json()
check("check: 200", r.status_code == 200, r.status_code)
check("check: haelt an", d["blocking"] is True, d)
check("check: Ziel ist nix-vm", d["target"]["host"] == "nix-vm", d["target"])
check("check: Quelle brauchbar", d["source_usable"] is True, d)

r = client.post("/api/hardware/apply",
                data=json.dumps({"action": "replace", "host": "nix-vm"}), headers=hdr)
check("apply replace: 200", r.status_code == 200, (r.status_code, r.get_json()))
check("eigene Datei ersetzt",
      HERE in (cfg / "hosts/nix-vm/hardware-configuration.nix").read_text(encoding="utf-8"))
check("Laptop unberuehrt",
      THERE in (cfg / "hosts/nix-laptop/hardware-configuration.nix").read_text(encoding="utf-8"))
check(".import.bak angelegt",
      (cfg / "hosts/nix-vm/hardware-configuration.nix.import.bak").is_file())
check("danach keine Frage mehr", r.get_json()["hardware"]["blocking"] is False,
      r.get_json()["hardware"]["action"])

r = client.post("/api/hardware/apply",
                data=json.dumps({"action": "replace", "host": "../../etc"}), headers=hdr)
check("fremder Host abgewiesen", r.status_code == 400, r.status_code)

r = client.post("/api/hardware/apply",
                data=json.dumps({"action": "loeschen", "host": "nix-vm"}), headers=hdr)
check("unbekannte Aktion abgewiesen", r.status_code == 400, r.status_code)

r = client.post("/api/hardware/apply",
                data=json.dumps({"action": "replace", "host": "nix-vm"}),
                headers={"Content-Type": "application/json"})
check("ohne CSRF abgewiesen", r.status_code == 403, r.status_code)

print()
if fails:
    print("FEHLGESCHLAGEN:", len(fails))
    for f in fails:
        print("  -", f)
    sys.exit(1)
print("Routen-Rauchtest bestanden.")
