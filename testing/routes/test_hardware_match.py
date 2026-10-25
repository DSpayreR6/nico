#!/usr/bin/env python3
"""Gehört die hardware-configuration.nix zu diesem Rechner? – ohne Browser.

Hintergrund (2026-10-25): Eine Config, die von einem anderen Rechner kommt –
per Import, per Rücksicherung oder per `git clone` – bringt die
hardware-configuration.nix dieses anderen Rechners mit. Gebaut wird dann eine
Config, die hier keine Festplatte findet. Die alte Regel konnte das nicht
melden: sie las die Datenträger über `blkid`, und `blkid` liefert einem
normalen Benutzer eine leere Liste – womit jede korrekte Hardware-Datei als
fremd galt.

Geprüft wird hier die komplette Matrix aus nico/hardware.py, und besonders:
die Datei eines fremden Hosts wird nie angefasst.

Lauf: nix-shell shell.nix --run "python testing/routes/test_hardware_match.py"
"""
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from nico import hardware as hw, validator  # noqa: E402

fails: list[str] = []


def check(label, cond, got=None):
    print(("  OK   " if cond else "  FAIL ") + label
          + ("" if cond or got is None else f"  (war: {got})"))
    if not cond:
        fails.append(label)


# ── Testdaten ────────────────────────────────────────────────────────────────

HERE_UUID = "11111111-2222-3333-4444-555555555555"
HERE_ESP = "AAAA-BBBB"
THERE_UUID = "99999999-8888-7777-6666-555555555555"

LOCAL_IDS = {HERE_UUID, HERE_ESP.lower(), "deadbeef-01"}


def hw_text(root_uuid, esp=HERE_ESP, extra=""):
    return f'''{{ config, lib, pkgs, modulesPath, ... }}:
{{
  fileSystems."/" = {{
    device = "/dev/disk/by-uuid/{root_uuid}";
    fsType = "btrfs";
  }};

  fileSystems."/boot" = {{
    device = "/dev/disk/by-uuid/{esp}";
    fsType = "vfat";
  }};
{extra}
  swapDevices = [ ];
}}
'''


LABEL_ONLY = '''{ config, ... }:
{
  fileSystems."/" = {
    device = "/dev/sda2";
    fsType = "ext4";
  };

  fileSystems."/media/sf_Datentransfer" = {
    device = "Datentransfer";
    fsType = "vboxsf";
  };

  swapDevices = [ ];
}
'''


def make_dir(hosts: dict, root_hw: str | None = None, hosts_dir="hosts") -> Path:
    """Config directory with one hardware file per named host."""
    d = Path(tempfile.mkdtemp(prefix="nico-hw-"))
    (d / "config.json").write_text(
        json.dumps({"hosts_dir": hosts_dir, "flakes": True}), encoding="utf-8")
    (d / "flake.nix").write_text("{ }\n", encoding="utf-8")
    for name, content in hosts.items():
        hd = d / hosts_dir / name
        hd.mkdir(parents=True)
        (hd / "default.nix").write_text("{ }\n", encoding="utf-8")
        if content is not None:
            (hd / hw.HW_NAME).write_text(content, encoding="utf-8")
    if root_hw is not None:
        (d / hw.HW_NAME).write_text(root_hw, encoding="utf-8")
    return d


class Machine:
    """Pretend to be a given machine with a given set of disks."""

    def __init__(self, host, ids=LOCAL_IDS, etc_nixos_hw=None):
        self.host, self.ids, self.etc = host, ids, etc_nixos_hw

    def __enter__(self):
        self._saved = (hw.local_device_ids, hw.running_host, hw.ETC_NIXOS_HW)
        hw.local_device_ids = lambda: set(self.ids)
        hw.running_host = lambda: self.host
        hw.ETC_NIXOS_HW = self.etc if self.etc else Path("/nonexistent/hw.nix")
        return self

    def __exit__(self, *a):
        hw.local_device_ids, hw.running_host, hw.ETC_NIXOS_HW = self._saved


def etc_file(content) -> Path:
    p = Path(tempfile.mkdtemp(prefix="nico-etc-")) / hw.HW_NAME
    p.write_text(content, encoding="utf-8")
    return p


# ── 1 classify: die drei Urteile ─────────────────────────────────────────────

print("1. Einzelne Datei beurteilen")

p = etc_file(hw_text(HERE_UUID))
check("eigene UUIDs -> match", hw.classify(p, LOCAL_IDS)[0] == hw.MATCH,
      hw.classify(p, LOCAL_IDS)[0])

p = etc_file(hw_text(THERE_UUID))
v, missing, _ = hw.classify(p, LOCAL_IDS)
check("fremde UUID -> mismatch", v == hw.MISMATCH, v)
check("fehlende UUID wird genannt", THERE_UUID in missing, missing)

p = etc_file(LABEL_ONLY)
v, _, unres = hw.classify(p, LOCAL_IDS)
check("nur /dev/sda2 -> unknown", v == hw.UNKNOWN, v)
check("vboxsf-Freigabe gilt nicht als Datenträger",
      "Datentransfer" not in unres, unres)
check("/dev/sda2 als nicht prüfbar gemeldet", "/dev/sda2" in unres, unres)

# Ein fehlender Swap-Datenträger bricht die Aktivierung genauso.
p = etc_file(hw_text(HERE_UUID).replace(
    "swapDevices = [ ];",
    f'swapDevices = [ {{ device = "/dev/disk/by-uuid/{THERE_UUID}"; }} ];'))
check("fremder Swap -> mismatch", hw.classify(p, LOCAL_IDS)[0] == hw.MISMATCH,
      hw.classify(p, LOCAL_IDS)[0])


# ── 2 Dateien finden und zuordnen ────────────────────────────────────────────

print("2. Dateien im Verzeichnis finden")

d = make_dir({"nix-desktop": hw_text(HERE_UUID), "nix-laptop": hw_text(THERE_UUID)})
(d / "hosts" / "nix-vm").mkdir(parents=True)
(d / "hosts" / "nix-vm" / "default.nix").write_text("{ }\n", encoding="utf-8")
(d / "hosts" / "nix-vm" / (hw.HW_NAME + ".old")).write_text(hw_text(THERE_UUID),
                                                            encoding="utf-8")
with Machine("nix-desktop"):
    files = hw.find_hw_files(str(d))
check("zwei Dateien gefunden", len(files) == 2, len(files))
check(".old wird nicht mitgezählt",
      all(not f.path.name.endswith(".old") for f in files))
check("Host korrekt zugeordnet",
      sorted(f.host for f in files) == ["nix-desktop", "nix-laptop"],
      [f.host for f in files])


# ── 3 Die Matrix ─────────────────────────────────────────────────────────────

print("3. Entscheidungsmatrix")

# Keine Datei
d = make_dir({"nix-desktop": None})
with Machine("nix-desktop"):
    check("keine Hardware-Datei -> add", hw.inspect(str(d)).action == hw.ADD,
          hw.inspect(str(d)).action)

# Genau eine, passt
d = make_dir({}, root_hw=hw_text(HERE_UUID))
with Machine("nix-desktop"):
    dec = hw.inspect(str(d))
check("eine passende -> ok, keine Frage", dec.action == hw.OK, dec.action)

# Genau eine, passt nicht
d = make_dir({}, root_hw=hw_text(THERE_UUID))
with Machine("nix-desktop"):
    dec = hw.inspect(str(d))
check("eine unpassende -> replace", dec.action == hw.REPLACE, dec.action)

# Mehrere, Hostname trifft und passt
d = make_dir({"nix-desktop": hw_text(HERE_UUID), "nix-laptop": hw_text(THERE_UUID)})
with Machine("nix-desktop"):
    dec = hw.inspect(str(d))
check("Hostname trifft, Datei passt -> ok", dec.action == hw.OK, dec.action)
check("fremder Host loest keine Frage aus", dec.matched_host == "nix-desktop",
      dec.matched_host)

# Mehrere, Hostname trifft, Datei passt nicht  (der Fall aus der VM)
d = make_dir({"nix-vm": hw_text(THERE_UUID), "nix-desktop": hw_text(HERE_UUID)})
with Machine("nix-vm"):
    dec = hw.inspect(str(d))
check("Hostname trifft, Datei falsch -> replace", dec.action == hw.REPLACE, dec.action)
check("Ziel ist der eigene Host", dec.target.host == "nix-vm", dec.target.host)
check("Zuordnung kam vom Hostnamen", dec.host_name_hit is True)

# Mehrere, kein Hostname-Treffer, genau eine passt
d = make_dir({"alpha": hw_text(HERE_UUID), "beta": hw_text(THERE_UUID)})
with Machine("namenlos"):
    dec = hw.inspect(str(d))
check("genau eine passt -> ok, Zuordnung gemeldet",
      dec.action == hw.OK and dec.matched_host == "alpha",
      (dec.action, dec.matched_host))

# Mehrere, keine passt
d = make_dir({"alpha": hw_text(THERE_UUID), "beta": hw_text(THERE_UUID)})
with Machine("namenlos"):
    dec = hw.inspect(str(d))
check("keine passt -> pick_host", dec.action == hw.PICK_HOST, dec.action)
check("Hosts zur Auswahl angeboten", dec.hosts == ["alpha", "beta"], dec.hosts)

# unknown ist nicht milder als mismatch
d = make_dir({}, root_hw=LABEL_ONLY)
with Machine("nix-desktop"):
    dec = hw.inspect(str(d))
check("nicht prüfbar -> haelt genauso an", dec.action == hw.REPLACE, dec.action)


# ── 4 Quelle, die selbst nicht passt ─────────────────────────────────────────

print("4. Ersatzdatei")

d = make_dir({}, root_hw=hw_text(THERE_UUID))
with Machine("nix-desktop", etc_nixos_hw=etc_file(hw_text(HERE_UUID))):
    dec = hw.inspect(str(d))
check("passende Quelle ist brauchbar", dec.source_usable is True)

# Der uebliche Weg, auf dem eine fremde Config hereinkommt: jemand kopiert sie
# nach /etc/nixos. Dann ist die "lokale" Datei die fremde.
with Machine("nix-desktop", etc_nixos_hw=etc_file(hw_text(THERE_UUID))):
    dec = hw.inspect(str(d))
check("Quelle passt selbst nicht -> unbrauchbar", dec.source_usable is False)
check("Quelle wird als mismatch gemeldet", dec.source_verdict == hw.MISMATCH,
      dec.source_verdict)

bad = etc_file(hw_text(THERE_UUID))
with Machine("nix-desktop", etc_nixos_hw=bad):
    target = d / hw.HW_NAME
    before = target.read_text(encoding="utf-8")
    try:
        hw.replace_with_local(target, bad)
        raised = False
    except ValueError:
        raised = True
check("unpassende Quelle wird nicht geschrieben", raised)
check("Zieldatei unveraendert", target.read_text(encoding="utf-8") == before)


# ── 5 Fremde Hosts bleiben unberuehrt ────────────────────────────────────────

print("5. Fremde Host-Dateien")

d = make_dir({"nix-vm": hw_text(THERE_UUID), "nix-laptop": hw_text(THERE_UUID)})
laptop = d / "hosts" / "nix-laptop" / hw.HW_NAME
laptop_before = laptop.read_text(encoding="utf-8")
good = etc_file(hw_text(HERE_UUID))
with Machine("nix-vm", etc_nixos_hw=good):
    hw.replace_with_local(d / "hosts" / "nix-vm" / hw.HW_NAME, good)
check("Laptop-Datei unveraendert",
      laptop.read_text(encoding="utf-8") == laptop_before)
check("eigene Datei ersetzt",
      HERE_UUID in (d / "hosts" / "nix-vm" / hw.HW_NAME).read_text(encoding="utf-8"))
check("ersetzte Datei als .import.bak behalten",
      (d / "hosts" / "nix-vm" / (hw.HW_NAME + hw.IMPORT_BAK_SUFFIX)).is_file())
with Machine("nix-vm", etc_nixos_hw=good):
    dec = hw.inspect(str(d))
check("danach keine Frage mehr", dec.action == hw.OK, dec.action)


# ── 6 Bestätigung pro Host ───────────────────────────────────────────────────

print("6. Einmalige Bestätigung")

d = make_dir({}, root_hw=LABEL_ONLY)
with Machine("nix-desktop"):
    check("vorher rot", hw.inspect(str(d)).action == hw.REPLACE)
    hw.ack_hardware(str(d), None, d / hw.HW_NAME)
    check("nach Bestätigung gruen", hw.inspect(str(d)).action == hw.OK,
          hw.inspect(str(d)).action)
    stored = json.loads((d / "config.json").read_text(encoding="utf-8"))
    check("Bestätigung steht in config.json", "hardware_ack" in stored,
          list(stored))

    # Eine geänderte Datei ist nicht mehr die bestätigte.
    (d / hw.HW_NAME).write_text(LABEL_ONLY + "\n# geaendert\n", encoding="utf-8")
    check("Änderung macht die Bestätigung ungueltig",
          hw.inspect(str(d)).action == hw.REPLACE, hw.inspect(str(d)).action)


# ── 7 Die Validator-Regel ────────────────────────────────────────────────────

print("7. Validator-Regel")

d = make_dir({"nix-vm": hw_text(THERE_UUID), "nix-laptop": hw_text(THERE_UUID)})
with Machine("nix-vm"):
    own = validator._rule_hardware_matches(str(d), {}, True, host="nix-vm")
    other = validator._rule_hardware_matches(str(d), {}, True, host="nix-laptop")
check("eigener Host: roter Fund",
      len(own) == 1 and own[0].severity == "error",
      [(f.rule_id, f.severity) for f in own])
check("fremder Host: kein Fund", other == [], other)

d = make_dir({"alpha": hw_text(THERE_UUID), "beta": hw_text(THERE_UUID)})
with Machine("namenlos"):
    amb = validator._rule_hardware_matches(str(d), {}, True, host=None)
check("unklare Zuordnung: roter Fund",
      len(amb) == 1 and amb[0].message_key == "validator.f.hardware_ambiguous",
      [(f.message_key, f.severity) for f in amb])

d = make_dir({"nix-desktop": hw_text(HERE_UUID)})
with Machine("nix-desktop"):
    clean = validator._rule_hardware_matches(str(d), {}, True, host="nix-desktop")
check("passende Datei: kein Fund", clean == [], clean)

# Die Regel steht im Katalog als Fehler, sonst haelt sie den Rebuild nicht auf.
rule = next(r for r in validator.ALL_RULES if r.id == "hardware_matches")
check("Regel ist rot", rule.severity == "error", rule.severity)
check("Regel ist rechnerabhaengig", rule.host_local is True)


# ── Ergebnis ─────────────────────────────────────────────────────────────────

print()
if fails:
    print(f"FEHLGESCHLAGEN: {len(fails)}")
    for f in fails:
        print("  -", f)
    sys.exit(1)
print("Alle Prüfungen bestanden.")
