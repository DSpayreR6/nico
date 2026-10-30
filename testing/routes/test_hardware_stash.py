#!/usr/bin/env python3
"""Zwischenparken der eigenen Hardware-Datei und die Auswahl von Quelle + Ziel.

Der Dialog lief bisher erst *nach* dem Import und kannte als Quelle nur
`/etc/nixos/hardware-configuration.nix`. Das scheitert in dem Setup, das NiCo
selbst anbietet: zeigt `/etc/nixos` per Symlink auf das Config-Verzeichnis, ist
die angebotene "lokale" Datei genau die gerade importierte fremde. Geprueft
wird deshalb:

  1. Vor jedem Import werden die eigenen Hardware-Dateien weggesichert,
     fremde (mismatch) nicht.
  2. Die weggesicherte Datei steht danach als Quelle zur Auswahl und laesst
     sich an ein frei gewaehltes Ziel schreiben.
  3. Quelle = Ziel wird abgewiesen, *bevor* irgendetwas umbenannt wird – vorher
     blieb die Config ohne hardware-configuration.nix zurueck.
  4. Eine Quelle, die nicht auf der Kandidatenliste steht, kommt nicht durch.
  5. Alle vier Importwege liefern die Hardware-Entscheidung mit zurueck.

Lauf: nix-shell shell.nix --run "python testing/routes/test_hardware_stash.py"
"""
import io
import json
import os
import re
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

xdg = Path(tempfile.mkdtemp(prefix="nico-xdg-"))
os.environ["XDG_CONFIG_HOME"] = str(xdg)

cfg = Path(tempfile.mkdtemp(prefix="nico-cfg-"))

HERE = "11111111-2222-3333-4444-555555555555"
THERE = "99999999-8888-7777-6666-555555555555"


def hwtext(uuid):
    return ('{ ... }:\n{\n  fileSystems."/" = {\n'
            f'    device = "/dev/disk/by-uuid/{uuid}";\n'
            '    fsType = "ext4";\n  };\n  swapDevices = [ ];\n}\n')


# nix-vm is this machine and has the right file; nix-laptop has another one.
for host, uuid in (("nix-vm", HERE), ("nix-laptop", THERE)):
    d = cfg / "hosts" / host
    d.mkdir(parents=True)
    (d / "default.nix").write_text("{ }\n", encoding="utf-8")
    (d / "hardware-configuration.nix").write_text(hwtext(uuid), encoding="utf-8")
(cfg / "configuration.nix").write_text("{ ... }:\n{\n}\n", encoding="utf-8")
(cfg / "config.json").write_text(
    json.dumps({"hosts_dir": "hosts", "flakes": False}), encoding="utf-8")

(xdg / "nico").mkdir(parents=True)
(xdg / "nico" / "settings.json").write_text(
    json.dumps({"nixos_config_dir": str(cfg), "language": "de", "git_sync": False}),
    encoding="utf-8")

from nico import config_manager, hardware as hwmod, server  # noqa: E402

hwmod.local_device_ids = lambda: {HERE}
hwmod.running_host = lambda: "nix-vm"
# The case this is all about: /etc/nixos *is* the config directory.
hwmod.ETC_NIXOS_HW = cfg / "hosts" / "nix-vm" / "hardware-configuration.nix"

app = server.create_app()
client = app.test_client()

fails = []


def check(label, cond, got=None):
    print(("  OK   " if cond else "  FAIL ") + label
          + ("" if cond or got is None else f"  (war: {got})"))
    if not cond:
        fails.append(label)


page = client.get("/").get_data(as_text=True)
m = re.search(r'<meta name="csrf-token" content="([^"]+)"', page)
csrf = m.group(1) if m else ""
hdr = {"X-CSRF-Token": csrf, "Content-Type": "application/json"}
check("CSRF-Token gelesen", bool(csrf))

# ── 1. Zwischenparken ────────────────────────────────────────────────────────
print("\n1. Zwischenparken vor dem Import")
kept = hwmod.stash_hw_files(str(cfg))
hosts_kept = {e["host"] for e in kept}
check("eigene Datei gesichert", "nix-vm" in hosts_kept, hosts_kept)
check("fremde Datei nicht gesichert", "nix-laptop" not in hosts_kept, hosts_kept)
check("liegt in ~/.config/nico/hw-stash",
      (config_manager.HW_STASH_DIR / "nix-vm.nix").is_file())
check("Inhalt ist die eigene Datei",
      HERE in (config_manager.HW_STASH_DIR / "nix-vm.nix").read_text(encoding="utf-8"))
check("stashed_files findet sie", any(e["host"] == "nix-vm" for e in hwmod.stashed_files()),
      hwmod.stashed_files())

# ── 2. Import macht die eigene Datei fremd, Zwischenlager rettet sie ─────────
print("\n2. Nach dem Import: Quelle waehlbar, Ziel waehlbar")
# Den Import nachstellen: fremde Datei an die Stelle des eigenen Hosts
(cfg / "hosts/nix-vm/hardware-configuration.nix").write_text(hwtext(THERE),
                                                             encoding="utf-8")

d = client.get("/api/hardware/check").get_json()
check("haelt an", d["blocking"] is True, d["action"])
src_ids = [s["id"] for s in d["sources"]]
check("Zwischenlager steht als Quelle", "stash:nix-vm.nix" in src_ids, src_ids)
stash_src = next(s for s in d["sources"] if s["id"] == "stash:nix-vm.nix")
check("Zwischenlager ist brauchbar", stash_src["usable"] is True, stash_src)
check("/etc/nixos jetzt unbrauchbar (zeigt auf die importierte Datei)",
      all(not s["usable"] for s in d["sources"] if s["kind"] == "etc"),
      [s for s in d["sources"] if s["kind"] == "etc"])
t_hosts = [e["host"] for e in d["targets"]]
# scan_hosts() liefert sortiert; die Wurzel fehlt zu Recht, dort liegt keine Datei
check("beide Hosts als Ziel", t_hosts == ["nix-laptop", "nix-vm"], t_hosts)
check("Wurzel nicht als Ziel (Config hat Hosts)", None not in t_hosts, t_hosts)
check("eigener Host markiert",
      any(e["is_this_host"] for e in d["targets"] if e["host"] == "nix-vm"))

r = client.post("/api/hardware/apply", headers=hdr, data=json.dumps(
    {"action": "replace", "host": "nix-vm", "source": "stash:nix-vm.nix"}))
check("apply mit Zwischenlager: 200", r.status_code == 200, (r.status_code, r.get_json()))
check("eigene Datei wieder da",
      HERE in (cfg / "hosts/nix-vm/hardware-configuration.nix").read_text(encoding="utf-8"))
check("importierte Datei als .import.bak erhalten",
      THERE in (cfg / "hosts/nix-vm/hardware-configuration.nix.import.bak")
      .read_text(encoding="utf-8"))
check("Laptop unberuehrt",
      THERE in (cfg / "hosts/nix-laptop/hardware-configuration.nix").read_text(encoding="utf-8"))
check("danach keine Frage mehr", r.get_json()["hardware"]["blocking"] is False,
      r.get_json()["hardware"]["action"])

# ── 3. Quelle = Ziel ─────────────────────────────────────────────────────────
print("\n3. Quelle und Ziel sind dieselbe Datei")
target = cfg / "hosts/nix-vm/hardware-configuration.nix"
before = target.read_text(encoding="utf-8")
r = client.post("/api/hardware/apply", headers=hdr, data=json.dumps(
    {"action": "replace", "host": "nix-vm",
     "source": "config:hosts/nix-vm/hardware-configuration.nix"}))
check("abgewiesen mit 409", r.status_code == 409, (r.status_code, r.get_json()))
check("Fehlercode ERR_HARDWARE_SAME_FILE",
      r.get_json().get("error") == "ERR_HARDWARE_SAME_FILE", r.get_json())
check("Datei noch da", target.is_file())
check("Datei unveraendert", target.read_text(encoding="utf-8") == before)

# Derselbe Fall direkt an der Funktion, ohne Route
try:
    hwmod.replace_with_local(target, target)
    check("replace_with_local lehnt ab", False, "keine Ausnahme")
except ValueError as exc:
    check("replace_with_local lehnt ab", str(exc) == "source_is_target", str(exc))
check("Datei auch danach da", target.is_file())

# ── 4. Quelle ausserhalb der Kandidatenliste ─────────────────────────────────
print("\n4. Erfundene Quelle")
for bad in ("stash:../../settings.json", "config:../../../etc/passwd",
            "datei:/etc/shadow", "stash:gibtsnicht.nix"):
    r = client.post("/api/hardware/apply", headers=hdr, data=json.dumps(
        {"action": "replace", "host": "nix-laptop", "source": bad}))
    check(f"abgewiesen: {bad}", r.status_code == 400, (r.status_code, r.get_json()))
check("Laptop immer noch unberuehrt",
      THERE in (cfg / "hosts/nix-laptop/hardware-configuration.nix").read_text(encoding="utf-8"))

# ── 5. Alle Importwege liefern die Entscheidung mit ──────────────────────────
print("\n5. Importwege geben 'hardware' zurueck")

# ZIP mit einer fremden Hardware-Datei fuer unseren Host
buf = io.BytesIO()
with zipfile.ZipFile(buf, "w") as zf:
    zf.writestr("configuration.nix", "{ ... }:\n{\n}\n")
    zf.writestr("hosts/nix-vm/default.nix", "{ }\n")
    zf.writestr("hosts/nix-vm/hardware-configuration.nix", hwtext(THERE))
buf.seek(0)
r = client.post("/api/import/zip/apply",
                data={"file": (buf, "cfg.zip"), "confirmed": "true"},
                headers={"X-CSRF-Token": csrf},
                content_type="multipart/form-data")
out = r.get_json()
check("ZIP-Import: 200", r.status_code == 200, (r.status_code, out))
check("ZIP-Import liefert hardware", isinstance(out.get("hardware"), dict), out.keys())
check("ZIP-Import: haelt an", out["hardware"]["blocking"] is True, out["hardware"]["action"])
check("ZIP-Import hat vorher gesichert",
      any(e["host"] == "nix-vm" for e in hwmod.stashed_files()), hwmod.stashed_files())
check("Zwischenlager ist die eigene Datei",
      HERE in (config_manager.HW_STASH_DIR / "nix-vm.nix").read_text(encoding="utf-8"))

# Manueller/Admin-Import aus einem Verzeichnis
other = Path(tempfile.mkdtemp(prefix="nico-src-"))
(other / "configuration.nix").write_text("{ ... }:\n{\n}\n", encoding="utf-8")
(other / "hardware-configuration.nix").write_text(hwtext(THERE), encoding="utf-8")
r = client.post("/api/import/run", headers=hdr, data=json.dumps(
    {"config_path": str(other / "configuration.nix"), "confirmed": True}))
out = r.get_json()
check("Pfad-Import: 200", r.status_code == 200, (r.status_code, out))
check("Pfad-Import liefert hardware", isinstance(out.get("hardware"), dict), out.keys())

print()
if fails:
    print("FEHLGESCHLAGEN:", len(fails))
    for f in fails:
        print("  -", f)
    sys.exit(1)
print("Alle Prüfungen bestanden.")
