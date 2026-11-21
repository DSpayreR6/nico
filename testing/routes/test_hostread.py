#!/usr/bin/env python3
"""Lesen außerhalb des Config-Verzeichnisses nur mit Freigabe (hostread.py).

Martin, 2026-11-21: „lesen außerhalb ist nicht zulässig“ – drei Schalter in
den App-Settings, alle aus, bis der Nutzer sie einschaltet: Validator
(Benutzer, Gruppen, Systemversion, Installationsdatum, Swap, Dateisysteme),
Hardware (/etc/nixos, /dev/disk, Suche) und Systemstände.

Geprüft wird, dass ohne Freigabe keine der Lesefunktionen aufgerufen wird –
sie sind durch Fallen ersetzt, die sich jeden Aufruf merken –, und dass mit
Freigabe wieder gelesen wird:
  1. Vorgaben: alle drei aus; PATCH nimmt nur true/false an.
  2. Validator ohne Freigabe: betroffene Regeln übersprungen, je Freigabe
     ein Hinweis host_read_off, keine Falle ausgelöst.
  3. Validator mit Freigabe: die Regeln lesen wieder, kein Hinweis.
  4. Hardware ohne Freigabe: Prüfung allowed=false und nicht blockierend,
     Übernehmen verweigert, Suche leer, Dateien UNKNOWN ohne /dev/disk.
  5. Dateisystem-Hinweis im Formular: ohne Freigabe kein findmnt.

Lauf: nix-shell shell.nix --run "python testing/routes/test_hostread.py"
"""
import json
import os
import re
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

from nico import config_manager, hardware, hostread, importer, server, validator  # noqa: E402

fails = []


def check(label, cond, got=None):
    print(("  OK   " if cond else "  FAIL ") + label
          + ("" if cond or got is None else f"  (war: {got})"))
    if not cond:
        fails.append(label)


# A config with a user (uid), a group (gid), btrfs maintenance, snapper,
# stateVersion and a hardware config – every reading rule has work to do.
(cfg / "configuration.nix").write_text('''{ config, pkgs, ... }:
{
  imports = [ ./hardware-configuration.nix ];
  system.stateVersion = "24.05";
  users.users.anna = { isNormalUser = true; uid = 1001; extraGroups = [ "wheel" ]; };
  users.groups.media.gid = 990;
  services.btrfs.autoScrub.enable = true;
  swapDevices = [ { device = "/swap/swapfile"; } ];
}
''', encoding="utf-8")
(cfg / "hardware-configuration.nix").write_text(
    '{ ... }: { fileSystems."/" = { device = "/dev/disk/by-uuid/1234-abcd"; fsType = "btrfs"; }; }\n',
    encoding="utf-8")

# Traps: every function that reads this machine.
calls: list = []


def trap(name, result):
    def f(*a, **k):
        calls.append(name)
        return result
    return f


originals = {}
TRAPS = [
    (validator, "_system_uids", {}), (validator, "_system_gids", {}),
    (validator, "_running_release", ""), (validator, "_birth_date", None),
    (validator, "_swap_files", []), (validator, "_mounted_fstypes", set()),
    (validator, "_findmnt_of", None),
    (hardware, "local_device_ids", set()),
    (importer, "find_hardware_configs", []),
]
for mod, name, result in TRAPS:
    originals[(mod, name)] = getattr(mod, name)
    setattr(mod, name, trap(name, result))

app = server.create_app()
client = app.test_client()
page = client.get("/").get_data(as_text=True)
m = re.search(r'<meta name="csrf-token" content="([^"]+)"', page)
hdr = {"X-CSRF-Token": m.group(1) if m else "", "Content-Type": "application/json"}

ALL = validator.default_validation_rules()

# ── 1. Vorgaben ──────────────────────────────────────────────────────────────
print("1. Vorgaben")
check("alle drei aus", hostread.state() == {k: False for k in hostread.KINDS}, hostread.state())
check("Vorgabe in DEFAULT_APP_SETTINGS",
      all(config_manager.DEFAULT_APP_SETTINGS.get(k) is False for k in hostread.KINDS))
client.patch("/api/app/settings", headers=hdr, data=json.dumps({"read_host_validator": "ja"}))
check("PATCH nimmt nur true/false", not hostread.allowed(hostread.VALIDATOR))

# ── 2. Validator ohne Freigabe ───────────────────────────────────────────────
print("2. Validator ohne Freigabe")
calls.clear()
found = validator.run_validation(str(cfg), ALL, config_manager.load_config(str(cfg)) or {})
off = [f for f in found if f["rule_id"] == "host_read_off"]
check("keine Falle ausgelöst", not calls, calls)
check("je Freigabe ein Hinweis",
      sorted(f["message_key"] for f in off)
      == ["validator.f.host_read_off_hardware", "validator.f.host_read_off_validator"], off)
check("Hinweis ist info", all(f["severity"] == "info" for f in off))
skipped_ids = {"user_no_password", "user_uid_system", "group_gid_system", "hardware_matches",
               "state_version_future", "snapper_btrfs", "btrfs_without_btrfs", "swap_in_snapshot"}
check("übersprungene Regeln liefern nichts",
      not [f for f in found if f["rule_id"] in skipped_ids], [f["rule_id"] for f in found])
check("stateVersion-Vorschlag ohne Lesen", validator._install_release() == ("", None) and not calls)

# ── 3. Validator mit Freigabe ────────────────────────────────────────────────
print("3. Validator mit Freigabe")
client.patch("/api/app/settings", headers=hdr,
             data=json.dumps({"read_host_validator": True, "read_host_hardware": True}))
check("Freigaben gespeichert",
      hostread.allowed(hostread.VALIDATOR) and hostread.allowed(hostread.HARDWARE))
calls.clear()
found = validator.run_validation(str(cfg), ALL, config_manager.load_config(str(cfg)) or {})
check("liest wieder (uids, gids, Hardware)",
      {"_system_uids", "_system_gids", "local_device_ids"} <= set(calls), sorted(set(calls)))
check("kein Hinweis host_read_off", not [f for f in found if f["rule_id"] == "host_read_off"])
client.patch("/api/app/settings", headers=hdr,
             data=json.dumps({"read_host_validator": False, "read_host_hardware": False}))

# ── 4. Hardware ohne Freigabe ────────────────────────────────────────────────
print("4. Hardware ohne Freigabe")
calls.clear()
d = client.get("/api/hardware/check").get_json()
check("Prüfung: allowed=false, nicht blockierend",
      d.get("allowed") is False and d.get("blocking") is False, d)
r = client.post("/api/hardware/apply", headers=hdr, data=json.dumps({"action": "confirm"}))
check("Übernehmen verweigert", r.status_code == 403
      and (r.get_json() or {}).get("error") == "ERR_HOST_READ_OFF", r.get_json())
s = client.get("/api/files/hardware-configs").get_json()
check("Suche leer, allowed=false", s.get("allowed") is False and s.get("configs") == [], s)
files = hardware.find_hw_files(str(cfg))
check("Dateien UNKNOWN", files and all(f.verdict == hardware.UNKNOWN for f in files))
check("keine Falle ausgelöst", not calls, calls)

# ── 5. Dateisystem-Hinweis ───────────────────────────────────────────────────
print("5. Dateisystem-Hinweis im Formular")
calls.clear()
st = client.get("/api/status").get_json()
check("root_fstype leer, kein findmnt", st.get("root_fstype") == "" and "_findmnt_of" not in calls,
      (st.get("root_fstype"), calls))

for (mod, name), fn in originals.items():
    setattr(mod, name, fn)

print()
if fails:
    print(f"{len(fails)} Prüfung(en) fehlgeschlagen.")
    sys.exit(1)
print("Alle Prüfungen bestanden.")
