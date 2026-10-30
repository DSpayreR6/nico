#!/usr/bin/env python3
"""btrfs-Einstellungen, die nur in einem Nix-Brick stehen – und gescheiterte Dienste.

Aus Martins VM-Durchlauf am 2026-10-30: In einem importierten Brick steckte
ein eigener Dienst `btrfs-dynamic-reclaim`. Die VM hat kein btrfs, der Dienst
scheiterte beim Start und riss den Rebuild mit. Der Validator schwieg, weil
`_rule_btrfs_without_btrfs` das NiCo-Modell fragte (`config["btrfs_scrub"]`)
und ein Brick dort nicht vorkommt. Ein Brick ist fuer das Modell undurch-
sichtiger Text - auf Platte aber ganz normales Nix.

Geprueft wird:
  1. Einstellung nur im Brick, Rechner ohne btrfs  -> Fund (der eigentliche Fall)
  2. Rechner MIT btrfs                             -> kein Fund
  3. Auskommentiert                                -> kein Fund
  4. btrfs-Datei eines fremden Hosts               -> kein Fund beim eigenen Host
  5. Formularfelder wie bisher                     -> unveraendert gemeldet
  6. failed_units(): robust ohne systemd, Form der Rueckgabe

Lauf: nix-shell shell.nix --run "python testing/routes/test_btrfs_in_brick.py"
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

from nico import validator  # noqa: E402
from nico.routes import rebuild as rebuild_routes  # noqa: E402

fails = []


def check(label, cond, got=None):
    print(("  OK   " if cond else "  FAIL ") + label
          + ("" if cond or got is None else f"  (war: {got})"))
    if not cond:
        fails.append(label)


# Genau der Brick aus Martins Config – ein eigener Dienst, kein Formularfeld.
BRICK = '''{ config, pkgs, ... }:
{
  boot.loader.systemd-boot.enable = true;

# <brick: FSYS / #1 btrfs-dynamic-reclaim>
systemd.services.btrfs-dynamic-reclaim = {
  description = "Enable Btrfs dynamic block group reclaim for data chunks";
  wantedBy = [ "multi-user.target" ];
  serviceConfig.Type = "oneshot";
  script = "${pkgs.btrfs-progs}/bin/btrfs filesystem usage /";
};
# </brick: btrfs-dynamic-reclaim>
}
'''

COMMENTED = '''{ config, pkgs, ... }:
{
  # systemd.services.btrfs-dynamic-reclaim = { };
  # services.btrfs.autoScrub.enable = true;
  boot.loader.systemd-boot.enable = true;
}
'''


def make_config(text, *, hosts=None):
    d = Path(tempfile.mkdtemp(prefix="nico-cfg-"))
    (d / "configuration.nix").write_text(text, encoding="utf-8")
    (d / "config.json").write_text(json.dumps({"hosts_dir": "hosts"}), encoding="utf-8")
    for host, content in (hosts or {}).items():
        hd = d / "hosts" / host
        hd.mkdir(parents=True)
        (hd / "default.nix").write_text(content, encoding="utf-8")
    return d


def run(d, config=None, host=None, fstypes=("ext4",), this_host="nix-vm"):
    validator._mounted_fstypes = lambda: set(fstypes)
    validator.running_host = lambda: this_host
    return validator._rule_btrfs_without_btrfs(str(d), config or {}, False, host)


print("1. Einstellung nur im Brick, Rechner ohne btrfs")
d = make_config(BRICK)
res = run(d)
check("Fund gemeldet", len(res) == 1, res)
check("Regel-Id stimmt", res and res[0].rule_id == "btrfs_without_btrfs")
check("Warnung, kein Fehler", res and res[0].severity == "warning",
      res[0].severity if res else None)
check("nennt die Datei", res and "configuration.nix" in res[0].message, res[0].message if res else None)
check("eigener Schluessel", res and res[0].message_key.endswith(".declared"),
      res[0].message_key if res else None)

print("\n2. Derselbe Brick auf einem Rechner mit btrfs")
check("kein Fund", run(d, fstypes=("btrfs", "ext4")) == [], run(d, fstypes=("btrfs",)))

print("\n3. Auskommentiert zaehlt nicht")
d2 = make_config(COMMENTED)
check("kein Fund", run(d2) == [], run(d2))

print("\n4. btrfs nur in der Datei eines fremden Hosts")
d3 = make_config("{ ... }:\n{\n}\n", hosts={
    "nix-vm":     "{ ... }:\n{\n  networking.hostName = \"nix-vm\";\n}\n",
    "nix-laptop": BRICK,
})
check("eigener Host sauber", run(d3, host="nix-vm") == [], run(d3, host="nix-vm"))
res = run(d3, host="nix-laptop", this_host="nix-laptop")
check("fremder Host meldet auf seinem eigenen Rechner", len(res) == 1, res)

print("\n5. Formularfelder weiterhin")
d4 = make_config("{ ... }:\n{\n  services.btrfs.autoScrub.enable = true;\n}\n")
res = run(d4, config={"btrfs_scrub": True})
keys = sorted(f.message_key for f in res)
check("Formular-Fund da", any(k.endswith("no_btrfs_scrub") for k in keys), keys)
check("Datei-Fund zusaetzlich", any(k.endswith(".declared") for k in keys), keys)

d5 = make_config("{ ... }:\n{\n}\n")
check("ohne alles kein Fund", run(d5, config={}) == [], run(d5, config={}))
check("Scrub an, btrfs da -> kein Fund",
      run(d5, config={"btrfs_scrub": True}, fstypes=("btrfs",)) == [],
      run(d5, config={"btrfs_scrub": True}, fstypes=("btrfs",)))

print("\n6. failed_units() ist belastbar")
units = rebuild_routes.failed_units()
check("liefert eine Liste", isinstance(units, list), type(units).__name__)
check("jeder Eintrag hat unit/description/journal",
      all(isinstance(u, dict) and {"unit", "description", "journal"} <= set(u)
          for u in units), units[:1])

names = rebuild_routes.failed_unit_names()
check("failed_unit_names liefert ein Set", isinstance(names, set), type(names).__name__)
check("beide Wege sehen dasselbe",
      {u["unit"] for u in units} <= names, (sorted(names)[:2], units[:1]))

# Der Kern: was vor dem Bau schon rot war, darf danach nicht gemeldet werden.
# Auf einem Desktop stehen Dienste im Fehlerzustand, die keinen Rebuild je
# gesehen haben - fuenf KDE-Absturzhelfer auf dem Rechner, auf dem das
# entstanden ist.
check("Ausschlussliste greift",
      rebuild_routes.failed_units(exclude=names) == [],
      rebuild_routes.failed_units(exclude=names))
if names:
    one = next(iter(names))
    rest = rebuild_routes.failed_units(exclude=names - {one})
    check("genau der eine bleibt uebrig",
          [u["unit"] for u in rest] == [one], [u["unit"] for u in rest])

_orig = rebuild_routes.subprocess.run
try:
    def boom(*a, **kw):
        raise OSError("kein systemd")
    rebuild_routes.subprocess.run = boom
    check("ohne systemd leere Liste, keine Ausnahme",
          rebuild_routes.failed_units() == [])
    check("ohne systemd leeres Set, keine Ausnahme",
          rebuild_routes.failed_unit_names() == set())
finally:
    rebuild_routes.subprocess.run = _orig

print()
if fails:
    print("FEHLGESCHLAGEN:", len(fails))
    for f in fails:
        print("  -", f)
    sys.exit(1)
print("Alle Prüfungen bestanden.")
