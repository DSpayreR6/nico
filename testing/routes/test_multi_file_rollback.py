#!/usr/bin/env python3
"""Mehrdatei-Schritte: scheitert der letzte Schreibschritt, ist alles wie vorher.

Codex-Revision 2026-11-20: „Auf Flake umstellen“ legt das Host-Verzeichnis
an, verschiebt die Hardware-Datei und schreibt configuration.nix und
flake.nix; „Host hinzufügen“ legt default.nix an und schreibt flake.nix.
Scheiterte der letzte Schritt, blieb ein halber Stand: configuration.nix
schon umgeschrieben, ein Host-Verzeichnis, auf das nichts zeigt. Nur die
Hardware-Datei wurde zurückgeschoben. Jetzt merkt sich atomic.Rollback alle
Pfade vorher und stellt sie zurück.

Geprüft wird:
  1. Host hinzufügen, flake.nix scheitert → kein neues Verzeichnis, flake.nix
     unverändert, Meldung ERR_WRITE_RESTORED.
  2. Auf Flake umstellen, flake.nix scheitert → configuration.nix
     unverändert, Hardware-Datei wieder an ihrem Platz, kein Host-Verzeichnis,
     keine flake.nix.
  3. Scheitert auch das Zurückstellen → ERR_WRITE_PARTIAL.
  4. Ohne Fehler läuft das Umstellen danach normal durch.

Lauf: nix-shell shell.nix --run "python testing/routes/test_multi_file_rollback.py"
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
(xdg / "nico").mkdir(parents=True)
settings = xdg / "nico" / "settings.json"


def use_config(path: Path) -> None:
    settings.write_text(json.dumps({"nixos_config_dir": str(path), "language": "de",
                                    "git_sync": False}), encoding="utf-8")


from nico import atomic, config_manager, generator, server  # noqa: E402

fails = []


def check(label, cond, got=None):
    print(("  OK   " if cond else "  FAIL ") + label
          + ("" if cond or got is None else f"  (war: {got})"))
    if not cond:
        fails.append(label)


real_write_bytes = atomic.write_bytes
fail_names: set = set()


def flaky_write_bytes(path, data):
    if Path(path).name in fail_names:
        raise OSError(28, "No space left on device (test)")
    return real_write_bytes(path, data)


atomic.write_bytes = flaky_write_bytes

HW = b'{ ... }: { fileSystems."/" = { device = "/dev/sda1"; fsType = "ext4"; }; }\n'


def classic_config() -> Path:
    cfg = Path(tempfile.mkdtemp(prefix="nico-cfg-"))
    data = config_manager.get_defaults()
    data.update({"hostname": "nix-vm"})
    config_manager.save_config(str(cfg), data)
    (cfg / "configuration.nix").write_text(
        generator.generate_configuration_nix(data), encoding="utf-8")
    (cfg / "hardware-configuration.nix").write_bytes(HW)
    return cfg


def flake_config() -> Path:
    cfg = Path(tempfile.mkdtemp(prefix="nico-cfg-"))
    data = config_manager.get_defaults()
    data.update({"flakes": True, "hostname": "nix-vm", "flake_hosts": [{"name": "nix-vm"}]})
    config_manager.save_config(str(cfg), data)
    (cfg / "configuration.nix").write_text(
        generator.generate_configuration_nix(data), encoding="utf-8")
    (cfg / "flake.nix").write_text(
        generator.generate_flake_nix(data, nixos_dir=str(cfg)), encoding="utf-8")
    config_manager.save_host_config(str(cfg), "nix-vm", {})
    return cfg


use_config(flake_config())
app = server.create_app()
client = app.test_client()
page = client.get("/").get_data(as_text=True)
m = re.search(r'<meta name="csrf-token" content="([^"]+)"', page)
hdr = {"X-CSRF-Token": m.group(1) if m else "", "Content-Type": "application/json"}

# ── 1. Host hinzufügen ───────────────────────────────────────────────────────
print("1. Host hinzufügen, flake.nix scheitert")
cfg = flake_config()
use_config(cfg)
flake_before = (cfg / "flake.nix").read_bytes()
fail_names = {"flake.nix"}
r = client.post("/api/flake/host/add", headers=hdr, data=json.dumps({"name": "neu"}))
fail_names = set()
check("Meldung ERR_WRITE_RESTORED", (r.get_json() or {}).get("error") == "ERR_WRITE_RESTORED",
      r.get_json())
check("kein Verzeichnis hosts/neu", not (cfg / "hosts" / "neu").exists())
check("flake.nix unverändert", (cfg / "flake.nix").read_bytes() == flake_before)

# ── 2. Auf Flake umstellen ───────────────────────────────────────────────────
print("2. Auf Flake umstellen, flake.nix scheitert")
cfg = classic_config()
use_config(cfg)
conf_before = (cfg / "configuration.nix").read_bytes()
fail_names = {"flake.nix"}
r = client.post("/api/flake/create", headers=hdr, data="{}")
fail_names = set()
check("Meldung ERR_WRITE_RESTORED", (r.get_json() or {}).get("error") == "ERR_WRITE_RESTORED",
      r.get_json())
check("configuration.nix unverändert", (cfg / "configuration.nix").read_bytes() == conf_before)
check("Hardware-Datei wieder im Wurzelverzeichnis",
      (cfg / "hardware-configuration.nix").is_file()
      and (cfg / "hardware-configuration.nix").read_bytes() == HW)
check("kein Host-Verzeichnis", not (cfg / "hosts" / "nix-vm").exists())
check("keine flake.nix", not (cfg / "flake.nix").exists())

# ── 3. Auch das Zurückstellen scheitert ──────────────────────────────────────
print("3. Zurückstellen scheitert ebenfalls")
cfg = classic_config()
use_config(cfg)
real_restore = atomic.Rollback.restore


def restore_fails_on_conf(self):
    global fail_names
    fail_names = {"configuration.nix"}
    try:
        return real_restore(self)
    finally:
        fail_names = set()


atomic.Rollback.restore = restore_fails_on_conf
fail_names = {"flake.nix"}
r = client.post("/api/flake/create", headers=hdr, data="{}")
fail_names = set()
atomic.Rollback.restore = real_restore
check("Meldung ERR_WRITE_PARTIAL", (r.get_json() or {}).get("error") == "ERR_WRITE_PARTIAL",
      r.get_json())

# ── 4. Ohne Fehler ───────────────────────────────────────────────────────────
print("4. Ohne Fehler")
cfg = classic_config()
use_config(cfg)
r = client.post("/api/flake/create", headers=hdr, data="{}")
j = r.get_json() or {}
check("Umstellen gelingt", j.get("success") is True, j)
check("Hardware-Datei verschoben", j.get("hardware") == "moved"
      and (cfg / "hosts" / "nix-vm" / "hardware-configuration.nix").read_bytes() == HW, j)
check("flake.nix geschrieben", (cfg / "flake.nix").is_file())

print()
if fails:
    print(f"{len(fails)} Prüfung(en) fehlgeschlagen.")
    sys.exit(1)
print("Alle Prüfungen bestanden.")
