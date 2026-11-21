#!/usr/bin/env python3
"""Ein fremder Host wird auf diesem Rechner gebaut – passt seine Hardware?

Martin, 2026-11-20 (beim Befund 23 der Revision 2026-11-18): Gefährlich ist
nicht das Löschen des eigenen Flake-Hosts, sondern der Rebuild danach. NiCo
schlägt dann keinen Host mehr vor, man wählt einen anderen, und der Rechner
baut dessen Config mit dessen hardware-configuration.nix. `hardware_matches`
schwieg dazu: es prüft nur den Host, den NiCo diesem Rechner zuordnet.

Die Rebuild-Sperre schickt jetzt `build_here`. Geprüft wird:
  1. fremder Host mit unpassender Hardware, build_here → roter Befund
  2. derselbe Lauf ohne build_here (Validator-Lauf, Dry-Run) → still
  3. eigener Host, build_here → kein zusätzlicher Befund
  4. fremder Host mit passender Hardware (umbenannt) → still
  5. Regel abgeschaltet → still

Lauf: nix-shell shell.nix --run "python testing/routes/test_hardware_built_here.py"
"""
import json, os, re, sys, tempfile
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

from nico import config_manager, generator, hardware, server  # noqa: E402
# Reading this machine needs the user's release since 2026-11-21 (hostread.py);
# this test is about what happens with it.
from nico import hostread as _hostread  # noqa: E402
_hostread.allowed = lambda kind: True

fails = []
def check(label, cond, got=None):
    print(("  OK   " if cond else "  FAIL ") + label + ("" if cond or got is None else f"  (war: {got})"))
    if not cond: fails.append(label)

data = config_manager.get_defaults()
data.update({"flakes": True, "hostname": "h1",
             "flake_hosts": [{"name": "h1"}, {"name": "h2"}]})
config_manager.save_config(str(cfg), data)
(cfg / "configuration.nix").write_text(generator.generate_configuration_nix(data), encoding="utf-8")
(cfg / "flake.nix").write_text(generator.generate_flake_nix(data, nixos_dir=str(cfg)), encoding="utf-8")
for h in ("h1", "h2"):
    config_manager.save_host_config(str(cfg), h, {})
    (cfg / "hosts" / h / "hardware-configuration.nix").write_text(
        f'{{ ... }}: {{ fileSystems."/" = {{ device = "/dev/disk/by-uuid/{h}"; fsType = "ext4"; }}; }}\n',
        encoding="utf-8")

verdicts = {"h1": hardware.MATCH, "h2": hardware.MISMATCH}
def fake_classify(path, ids=None):
    v = verdicts[Path(path).parent.name]
    return (v, ["/dev/disk/by-uuid/h2"] if v == hardware.MISMATCH else [], [])
hardware.classify = fake_classify
hardware.running_host = lambda: "h1"

client = server.create_app().test_client()
page = client.get("/").get_data(as_text=True)
csrf = re.search(r'<meta name="csrf-token" content="([^"]+)"', page).group(1)
H = {"X-CSRF-Token": csrf, "Content-Type": "application/json"}

def keys(host, build_here):
    r = client.post("/api/validate", headers=H,
                    data=json.dumps({"host": host, "build_here": build_here}))
    return [(f.get("message_key"), f.get("severity"), f.get("params"))
            for f in (r.get_json() or {}).get("findings", [])
            if f.get("rule_id") == "hardware_matches"]

KEY = "validator.f.hardware_built_here"

print("1. Fremder Host, unpassende Hardware, Rebuild hier")
got = keys("h2", True)
hit = [k for k in got if k[0] == KEY]
check("roter Befund", len(hit) == 1 and hit[0][1] == "error", got)
check("nennt Host und Datei", hit and hit[0][2] == ["h2", "hosts/h2/hardware-configuration.nix"], hit)

print("\n2. Derselbe Host ohne build_here (Validator-Lauf, Dry-Run)")
got = keys("h2", False)
check("still", got == [], got)

print("\n3. Eigener Host, Rebuild hier")
got = keys("h1", True)
check("kein zusätzlicher Befund", not any(k[0] == KEY for k in got), got)

print("\n4. Fremder Host mit passender Hardware (umbenannt)")
verdicts["h2"] = hardware.MATCH
got = keys("h2", True)
check("still", not any(k[0] == KEY for k in got), got)
verdicts["h2"] = hardware.MISMATCH

print("\n5. Regel abgeschaltet")
config_manager.save_config_settings(str(cfg), {"validation_rules": {"hardware_matches": False}})
got = keys("h2", True)
check("still", got == [], got)

print()
if fails:
    print(f"{len(fails)} FEHLER")
    sys.exit(1)
print("Alle Prüfungen bestanden.")
