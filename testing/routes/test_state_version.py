"""Test: stateVersion – vier Regeln statt einer falschen.

Hintergrund (2026-10-25): `state_version_match` verlangte, dass
`system.stateVersion` zur nixpkgs-Release-Version passt, und
`hm_state_version_match` verlangte Gleichheit von `home.stateVersion` und
`system.stateVersion`. Beides widerspricht NixOS: stateVersion ist das Release,
mit dem die Maschine **installiert** wurde, und wird nie hochgezogen;
home.stateVersion zählt den Beginn des Home-Manager-Zustands eines Nutzers und
damit etwas anderes. In der echten Config von Martin haben die beiden Regeln
genau das angerichtet – beide Werte standen auf 25.11 und wurden am 2026-07-12
von NiCo auf 26.05 gezogen.

Geprüft wird darum vor allem, was die Regeln **nicht** mehr melden.

Läuft ohne Server und ohne Browser.
"""
import pathlib, sys, tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from nico import validator

fails = []
def check(label, cond, got=None):
    print(("  OK   " if cond else "  FAIL ") + label + ("" if cond or got is None else f"  (war: {got})"))
    if not cond: fails.append(label)


def make(files: dict) -> str:
    tmp = tempfile.mkdtemp(prefix="nico-sv-")
    for name, content in files.items():
        target = pathlib.Path(tmp) / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    return tmp


def run(rule, nixos_dir, running="26.05", this_host="nix-desktop", host=None):
    """Run one rule with a fixed view of the running machine."""
    orig_rel, orig_host = validator._running_release, validator.running_host
    validator._running_release = lambda: running
    validator.running_host = lambda: this_host
    try:
        return rule(nixos_dir, {}, True, host)
    finally:
        validator._running_release, validator.running_host = orig_rel, orig_host


FLAKE_TWO_HOSTS = '''{
  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-26.05";
    home-manager = {
      url = "github:nix-community/home-manager/release-26.05";
      inputs.nixpkgs.follows = "nixpkgs";
    };
  };
  outputs = { self, nixpkgs, home-manager, ... }: {
    nixosConfigurations = {
      nix-desktop = nixpkgs.lib.nixosSystem { modules = [ ./configuration.nix ]; };
      nix-laptop  = nixpkgs.lib.nixosSystem { modules = [ ./configuration.nix ]; };
    };
  };
}
'''

def base(state_version='  system.stateVersion = "26.05";'):
    return '{ config, pkgs, ... }:\n{\n' + state_version + '\n}\n'


print("1. Release-Vergleich")
check("25.11 → (25, 11)", validator._release_tuple("25.11") == (25, 11))
check("unstable → None", validator._release_tuple("unstable") is None)
check("leer → None", validator._release_tuple("") is None)
check("25.11 < 26.05",
      validator._release_tuple("25.11") < validator._release_tuple("26.05"))

print("\n2. Wert aus der Zukunft (der VM-Fall)")
d = make({"flake.nix": FLAKE_TWO_HOSTS, "configuration.nix": base()})
f = run(validator._rule_state_version_future, d, running="25.11")
check("ein Fund", len(f) == 1, [x.message for x in f])
check("Stufe warning", f and f[0].severity == "warning", f and f[0].severity)
check("nennt beide Werte", f and f[0].detail_params == ["26.05", "25.11"],
      f and f[0].detail_params)

print("\n3. Älterer Wert ist normal und bleibt still")
d_old = make({"flake.nix": FLAKE_TWO_HOSTS,
              "configuration.nix": base('  system.stateVersion = "24.11";')})
f = run(validator._rule_state_version_future, d_old, running="26.05")
check("kein Fund", f == [], [x.message for x in f])
f = run(validator._rule_state_version_match, d_old, running="26.05")
check("auch nicht gegen nixpkgs 26.05", f == [], [x.message for x in f])

print("\n4. Fremder Rechner: Hinweis statt Urteil")
f = run(validator._rule_state_version_future, d, running="25.11",
        this_host="nix-desktop", host="nix-laptop")
check("ein Hinweis", len(f) == 1 and f[0].severity == "info",
      [(x.severity, x.message) for x in f])
check("gemeinsamer Schlüssel",
      f and f[0].message_key == "validator.f.host_other", f and f[0].message_key)

print("\n5. Gegen nixpkgs zählt nur 'neuer'")
d_future = make({"flake.nix": FLAKE_TWO_HOSTS,
                 "configuration.nix": base('  system.stateVersion = "26.11";')})
f = run(validator._rule_state_version_match, d_future)
check("neuer als nixpkgs → Fund", len(f) == 1, [x.message for x in f])
check("Detail hat 5 Parameter", f and len(f[0].detail_params) == 5,
      f and f[0].detail_params)

print("\n6. Wert fehlt ganz")
d_none = make({"flake.nix": FLAKE_TWO_HOSTS, "configuration.nix": base("")})
f = run(validator._rule_state_version_missing, d_none)
check("ein Fund", len(f) == 1, [x.message for x in f])
f = run(validator._rule_state_version_missing, d)
check("mit Wert still", f == [], [x.message for x in f])
check("leeres Verzeichnis still",
      run(validator._rule_state_version_missing, make({})) == [])

# Der Rat muss zu state_version_per_host passen: mit Host-Verzeichnissen ist
# die Basis-Config der falsche Ort, und beide Regeln dürfen den Nutzer nicht
# gegeneinander schicken.
d_none_hosts = make({
    "flake.nix": FLAKE_TWO_HOSTS,
    "configuration.nix": base(""),
    "hosts/nix-desktop/default.nix": base(""),
    "hosts/nix-laptop/default.nix": base(""),
})
f = run(validator._rule_state_version_missing, d_none_hosts, host="nix-desktop")
check("Rat nennt die Host-Datei",
      f and f[0].detail_params == ["hosts/nix-desktop/default.nix"],
      f and f[0].detail_params)
f = run(validator._rule_state_version_missing, d_none_hosts)
check("ohne Host-Angabe das Muster",
      f and f[0].detail_params == ["hosts/<name>/default.nix"],
      f and f[0].detail_params)
f = run(validator._rule_state_version_missing, make({"configuration.nix": base("")}))
check("ohne Hosts weiter configuration.nix",
      f and f[0].detail_params == ["configuration.nix"], f and f[0].detail_params)

print("\n7. Ein Wert für mehrere Hosts")
f = run(validator._rule_state_version_per_host, d)
check("ein Fund", len(f) == 1, [x.message for x in f])
check("zählt die Hosts", f and f[0].params[3] == 2, f and f[0].params)
check("nennt beide Namen",
      f and f[0].detail_params[0] == "nix-desktop, nix-laptop",
      f and f[0].detail_params)

d_per_host = make({
    "flake.nix": FLAKE_TWO_HOSTS,
    "configuration.nix": base(""),
    "hosts/nix-desktop/default.nix": base(),
    "hosts/nix-laptop/default.nix": base('  system.stateVersion = "24.11";'),
})
f = run(validator._rule_state_version_per_host, d_per_host, host="nix-desktop")
check("im Host-Verzeichnis still", f == [], [x.message for x in f])
check("und dort auch nicht 'fehlt'",
      run(validator._rule_state_version_missing, d_per_host,
          host="nix-desktop") == [])

print("\n7b. Host-Wert hat Vorrang – und wird gegengeprüft")
# Basis-Wert ohne mkDefault plus Host-Wert: Nix bricht ab.
d_both = make({
    "flake.nix": FLAKE_TWO_HOSTS,
    "configuration.nix": base(),
    "hosts/nix-laptop/default.nix": base('  system.stateVersion = "24.11";'),
})
f = run(validator._rule_state_version_per_host, d_both)
conflict = [x for x in f if x.message_key == "validator.f.state_version_conflict"]
check("doppelte Definition wird gemeldet", len(conflict) == 1,
      [x.message_key for x in f])
check("als Fehler, nicht als Warnung",
      conflict and conflict[0].severity == "error",
      conflict and conflict[0].severity)
check("nennt beide Stellen",
      conflict and conflict[0].params[0] == "configuration.nix"
      and conflict[0].params[2] == "hosts/nix-laptop/default.nix",
      conflict and conflict[0].params)
check("nur noch ein Host erbt, also keine Streuungs-Warnung",
      not [x for x in f if x.message_key == "validator.f.state_version_per_host"],
      [x.message_key for x in f])

# Mit lib.mkDefault in der Basis gewinnt der Host-Wert – das ist erlaubt.
d_default = make({
    "flake.nix": FLAKE_TWO_HOSTS,
    "configuration.nix": base('  system.stateVersion = lib.mkDefault "26.05";'),
    "hosts/nix-laptop/default.nix": base('  system.stateVersion = "24.11";'),
})
f = run(validator._rule_state_version_per_host, d_default)
check("mkDefault erzeugt keinen Konflikt",
      not [x for x in f if x.message_key == "validator.f.state_version_conflict"],
      [x.message_key for x in f])
check("mkDefault-Wert wird überhaupt gelesen",
      [s for s in validator._state_version_sites(d_default, None)
       if s[3] == "mkDefault"],
      validator._state_version_sites(d_default, None))

# Jeder Host mit eigenem Wert, Basis ohne: nichts zu melden.
d_all_own = make({
    "flake.nix": FLAKE_TWO_HOSTS,
    "configuration.nix": base(""),
    "hosts/nix-desktop/default.nix": base(),
    "hosts/nix-laptop/default.nix": base('  system.stateVersion = "24.11";'),
})
f = run(validator._rule_state_version_per_host, d_all_own)
check("alle Hosts eigenständig → still", f == [], [x.message for x in f])

ONE_HOST = FLAKE_TWO_HOSTS.replace(
    '      nix-laptop  = nixpkgs.lib.nixosSystem { modules = [ ./configuration.nix ]; };\n', '')
d_one = make({"flake.nix": ONE_HOST, "configuration.nix": base()})
f = run(validator._rule_state_version_per_host, d_one)
check("ein einzelner Host ist still", f == [], [x.message for x in f])

print("\n8. Home-State-Version wird nicht mehr mit dem System verglichen")
d_hm = make({
    "flake.nix": FLAKE_TWO_HOSTS,
    "configuration.nix": base(),
    "home/martin.nix": '{ ... }:\n{\n  home.stateVersion = "25.11";\n}\n',
})
f = run(validator._rule_hm_state_version_match, d_hm)
check("25.11 neben system 26.05 ist still", f == [], [x.message for x in f])

d_hm_future = make({
    "flake.nix": FLAKE_TWO_HOSTS,
    "configuration.nix": base(),
    "home/martin.nix": '{ ... }:\n{\n  home.stateVersion = "26.11";\n}\n',
})
f = run(validator._rule_hm_state_version_match, d_hm_future)
check("neuer als der HM-Zweig → Fund", len(f) == 1, [x.message for x in f])
check("nennt den Zweig", f and f[0].params[3] == "release-26.05",
      f and f[0].params)

print("\n8b. Host-Formular: eingetragener Wert bleibt stehen")
# Vorher schrieb der Generator im host_mode keine stateVersion - das Feld war
# da, der Parser las es zurueck, nur beim Speichern verschwand der Wert.
from nico import generator, importer

host_nix = generator.generate_host_nix(
    {"hostname": "nix-vm", "state_version": "25.11"}, "nix-vm", False)
check("Wert steht in der Host-Datei",
      'system.stateVersion = "25.11";' in host_nix, host_nix[:200])
check("Roundtrip: Parser liest ihn zurueck",
      importer.parse_config(host_nix).get("state_version") == "25.11",
      importer.parse_config(host_nix).get("state_version"))

leer = generator.generate_host_nix({"hostname": "nix-vm"}, "nix-vm", False)
check("ohne Wert keine Zeile", "stateVersion" not in leer, leer[:200])
check("und kein TODO-Kommentar", "TODO" not in leer, leer[:200])

ganz_leer = generator.generate_host_nix({}, "nix-vm", False)
check("ohne Hostname und ohne Wert kein SYST-Abschnitt",
      "SYST" not in ganz_leer, ganz_leer[:200])

print("\n9. Regeln sind registriert und standardmäßig an")
for rid in ("state_version_future", "state_version_missing",
            "state_version_per_host", "state_version_match",
            "hm_state_version_match"):
    check(f"{rid} im Katalog", rid in validator.RULE_MAP)
    check(f"{rid} hat eine Funktion", rid in validator._RULE_FNS)
    check(f"{rid} standardmäßig an",
          validator.default_validation_rules().get(rid) is True)

print("\n" + ("ALLE PRÜFUNGEN BESTANDEN" if not fails else f"FEHLGESCHLAGEN: {len(fails)}"))
sys.exit(1 if fails else 0)
