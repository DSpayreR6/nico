"""Test: Nach dem Rebuild muss eine Anmeldung noch möglich sein.

Hintergrund (2026-10-25, VM-Test mit der echten Config): Die importierte
Config deklariert `users.users.martin` mit `uid = 1001` und ohne jede
Passwortangabe. In der frisch installierten VM trat beides zu:

  * Der Nutzer der VM hieß anders – `martin` wurde also neu angelegt, ohne
    Passwort, und damit gesperrt (`!` in `/etc/shadow`). Kein Passwort wird
    akzeptiert, auch kein leeres.
  * Hieß er ebenfalls `martin`, dann wechselte seine uid von 1000 auf 1001,
    während `/home/martin` dem alten Eigentümer gehört – die Anmeldung gelingt,
    die Sitzung bricht sofort ab.

Beides merkt man erst am Anmeldeschirm, und `/etc/passwd` und `/etc/shadow`
sind veränderlicher Systemzustand: eine ältere Generation zu starten holt sie
nicht zurück. Deshalb müssen diese Regeln *vor* dem Rebuild greifen.

Abschnitt 15 prüft dasselbe für Gruppen (`users.groups.<name>.gid`, Martins
Nachfrage vom selben Tag): auch eine gid ist veränderlicher Zustand in
`/etc/group`. Eine gewechselte gid lässt die Dateien der Gruppe ohne Gruppe
zurück, eine doppelt belegte gid weitet still die Rechte aus.

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


def write_config(text: str, extra: "dict | None" = None) -> str:
    """Write a throwaway config directory and return its path."""
    tmp = tempfile.mkdtemp(prefix="nico-login-")
    (pathlib.Path(tmp) / "configuration.nix").write_text(text, encoding="utf-8")
    for name, content in (extra or {}).items():
        target = pathlib.Path(tmp) / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    return tmp


def run(rule, nixos_dir, uids=None, host=None):
    """Run one rule with a fixed view of the running machine's users."""
    original = validator._system_uids
    validator._system_uids = lambda: (uids if uids is not None else {})
    try:
        return rule(nixos_dir, {}, False, host)
    finally:
        validator._system_uids = original


# Der Fall aus der VM, wörtlich aus der echten configuration.nix.
REAL = '''{ config, pkgs, ... }:
{
  system.stateVersion = "26.05";

  users.users.martin = {
    isNormalUser = true;
    description = "Martin Schumacher";
    uid = 1001;
    extraGroups = [ "dialout" "docker" "networkmanager" "video" "wheel" ];
  };
  users.users.gast = {
    isNormalUser = true;
    initialPassword = "gast";
  };
}
'''

print("1. Nutzer-Blöcke einlesen")
d = write_config(REAL)
users = validator._declared_users(d)
check("martin gefunden", "martin" in users, sorted(users))
check("uid 1001 erkannt", users.get("martin", {}).get("uid") == 1001,
      users.get("martin", {}).get("uid"))
check("kein Anmeldeweg erkannt", users.get("martin", {}).get("has_login") is False)
check("gast bleibt außen vor (_EXCL_USERS)", "gast" not in users)

print("\n2. Gesperrtes Konto: Nutzer gibt es auf diesem Rechner nicht")
f = run(validator._rule_user_no_password, d, {"nixos": 1000, "root": 0})
check("ein Fund", len(f) == 1, [x.message for x in f])
check("Regel-ID", f and f[0].rule_id == "user_no_password")
check("Stufe error", f and f[0].severity == "error", f and f[0].severity)
check("nennt martin", f and "martin" in f[0].params[0], f and f[0].params)
check("Detail für den neuen Nutzer",
      f and f[0].detail_key == "validator.d.user_no_password", f and f[0].detail_key)

print("\n3. Derselbe Nutzer existiert hier schon → still")
f = run(validator._rule_user_no_password, d, {"martin": 1001, "root": 0})
check("kein Fund", f == [], [x.message for x in f])

print("\n4. mutableUsers = false → auch der vorhandene Nutzer verliert sein Passwort")
d2 = write_config(REAL.replace("{\n  system", "{\n  users.mutableUsers = false;\n  system"))
check("mutableUsers = false erkannt", validator._mutable_users_off(d2) is True)
f = run(validator._rule_user_no_password, d2, {"martin": 1001, "root": 0})
check("ein Fund", len(f) == 1, [x.message for x in f])
check("anderes Detail",
      f and f[0].detail_key == "validator.d.user_no_password_immutable",
      f and f[0].detail_key)

print("\n5. Passwort oder SSH-Schlüssel genügt")
for label, line in (
    ("initialPassword", '    initialPassword = "geheim";'),
    ("hashedPassword",  '    hashedPassword = "$y$j9T$abc";'),
    ("hashedPasswordFile", '    hashedPasswordFile = "/etc/pw";'),
    ("SSH-Schlüssel",   '    openssh.authorizedKeys.keys = [ "ssh-ed25519 AAAA" ];'),
):
    dx = write_config(REAL.replace("    uid = 1001;", "    uid = 1001;\n" + line))
    f = run(validator._rule_user_no_password, dx, {"nixos": 1000})
    check(f"{label} zählt als Anmeldeweg", f == [], [x.message for x in f])

dx = write_config(REAL.replace(
    "    uid = 1001;", '    openssh.authorizedKeys.keys = [ ];'))
f = run(validator._rule_user_no_password, dx, {"nixos": 1000})
check("leere Schlüsselliste zählt nicht", len(f) == 1, [x.message for x in f])

print("\n6. isSystemUser bleibt unbeachtet")
dx = write_config('''{ ... }:
{
  users.users.paperless = {
    isSystemUser = true;
    group = "paperless";
  };
}
''')
f = run(validator._rule_user_no_password, dx, {"nixos": 1000})
check("Dienstkonto erzeugt keinen Fund", f == [], [x.message for x in f])

print("\n7. uid doppelt vergeben")
dx = write_config('''{ ... }:
{
  users.users.martin = { isNormalUser = true; uid = 1001; initialPassword = "a"; };
  users.users.guenther = { isNormalUser = true; uid = 1001; initialPassword = "b"; };
}
''')
f = validator._rule_user_uid_duplicate(dx, {}, False, None)
check("ein Fund", len(f) == 1, [x.message for x in f])
check("nennt beide Namen",
      f and f[0].params[1] == "guenther, martin", f and f[0].params)
f = validator._rule_user_uid_duplicate(d, {}, False, None)
check("einzelne uid ist still", f == [], [x.message for x in f])

print("\n8. uid wechselt gegenüber diesem Rechner")
f = run(validator._rule_user_uid_system, d, {"martin": 1000, "root": 0})
check("ein Fund", len(f) == 1, [x.message for x in f])
check("Schlüssel user_uid_changed",
      f and f[0].message_key == "validator.f.user_uid_changed", f and f[0].message_key)
check("Parameter Name/hier/Config",
      f and f[0].params == ["martin", 1000, 1001], f and f[0].params)

print("\n9. uid gehört hier schon jemand anderem")
f = run(validator._rule_user_uid_system, d, {"guenther": 1001, "root": 0})
check("ein Fund", len(f) == 1, [x.message for x in f])
check("Schlüssel user_uid_taken",
      f and f[0].message_key == "validator.f.user_uid_taken", f and f[0].message_key)
check("nennt den Eigentümer", f and f[0].params[2] == "guenther", f and f[0].params)

print("\n10. uid passt → still")
f = run(validator._rule_user_uid_system, d, {"martin": 1001, "root": 0})
check("kein Fund", f == [], [x.message for x in f])

print("\n11. Nutzer in einem Nix-Brick wird gesehen")
dx = write_config('''{ ... }:
{
  # <brick:USER:1>
  users.users.martin = {
    isNormalUser = true;
    uid = 1001;
  };
  # </brick:USER:1>
}
''')
users = validator._declared_users(dx)
check("martin auch im Brick gefunden", "martin" in users, sorted(users))

print("\n12. Punktschreibweise wird gesehen")
dx = write_config('''{ ... }:
{
  users.users.martin.isNormalUser = true;
  users.users.martin.uid = 1001;
}
''')
users = validator._declared_users(dx)
check("uid aus der Punktform", users.get("martin", {}).get("uid") == 1001,
      users.get("martin", {}).get("uid"))

print("\n13. Andere Hosts bleiben außen vor")
MULTI = {
    "hosts/nix-desktop/default.nix": '{ ... }:\n{\n'
        '  users.users.martin = { isNormalUser = true; uid = 1001; };\n}\n',
    "hosts/nix-laptop/default.nix": '{ ... }:\n{\n'
        '  users.users.guenther = { isNormalUser = true; uid = 1003; };\n}\n',
}
dm = write_config('{ ... }:\n{\n  users.users.gemeinsam = '
                  '{ isNormalUser = true; initialPassword = "x"; };\n}\n', MULTI)
users = validator._declared_users(dm, "nix-desktop")
check("eigener Host dabei", "martin" in users, sorted(users))
check("fremder Host nicht dabei", "guenther" not in users, sorted(users))
check("Wurzel-Config gilt für alle", "gemeinsam" in users, sorted(users))
check("ohne Host-Angabe alles",
      {"martin", "guenther", "gemeinsam"} <= set(validator._declared_users(dm)),
      sorted(validator._declared_users(dm)))

print("\n14. Fremder Rechner: Hinweis statt Urteil")
original_host = validator.running_host
validator.running_host = lambda: "nix-desktop"
try:
    for rule in (validator._rule_user_no_password, validator._rule_user_uid_system):
        f = run(rule, dm, {"martin": 1001}, host="nix-laptop")
        check(f"{rule.__name__}: ein Hinweis für nix-laptop",
              len(f) == 1 and f[0].severity == "info",
              [(x.severity, x.message) for x in f])
        check(f"{rule.__name__}: gemeinsamer Schlüssel",
              f and f[0].message_key == "validator.f.host_other",
              f and f[0].message_key)
    # Der eigene Host wird dagegen wirklich geprüft.
    f = run(validator._rule_user_uid_system, dm, {"martin": 1000}, host="nix-desktop")
    check("eigener Host wird geprüft", any(
        x.message_key == "validator.f.user_uid_changed" for x in f),
        [x.message_key for x in f])
finally:
    validator.running_host = original_host

print("\n15. Gruppen: dasselbe eine Etage tiefer")
GROUPS = '''{ ... }:
{
  users.groups.docker.gid = 140;
  users.groups.medien = { gid = 3000; };
}
'''
dg = write_config(GROUPS)
groups = validator._declared_groups(dg)
check("beide Gruppen gefunden", {"docker", "medien"} <= set(groups), sorted(groups))
check("gid aus der Punktform", groups.get("docker", {}).get("gid") == 140,
      groups.get("docker", {}).get("gid"))
check("gid aus dem Block", groups.get("medien", {}).get("gid") == 3000,
      groups.get("medien", {}).get("gid"))

def run_groups(rule, nixos_dir, gids=None, host=None):
    original = validator._system_gids
    validator._system_gids = lambda: (gids if gids is not None else {})
    try:
        return rule(nixos_dir, {}, False, host)
    finally:
        validator._system_gids = original

f = run_groups(validator._rule_group_gid_system, dg, {"docker": 131, "users": 100})
check("gewechselte gid wird gemeldet",
      any(x.message_key == "validator.f.group_gid_changed" for x in f),
      [x.message_key for x in f])
check("Parameter Name/hier/Config",
      any(x.params == ["docker", 131, 140] for x in f), [x.params for x in f])

f = run_groups(validator._rule_group_gid_system, dg, {"video": 3000})
check("belegte gid wird gemeldet",
      any(x.message_key == "validator.f.group_gid_taken" for x in f),
      [x.message_key for x in f])
check("nennt die Gruppe, der sie gehört",
      any(x.params == [3000, "medien", "video"] for x in f), [x.params for x in f])

f = run_groups(validator._rule_group_gid_system, dg, {"docker": 140, "medien": 3000})
check("passende gids sind still", f == [], [x.message for x in f])
check("ohne /etc/group keine Aussage",
      run_groups(validator._rule_group_gid_system, dg, {}) == [])

dg_dup = write_config('''{ ... }:
{
  users.groups.medien.gid = 3000;
  users.groups.archiv.gid = 3000;
}
''')
f = validator._rule_group_gid_duplicate(dg_dup, {}, False, None)
check("doppelte gid: ein Fund", len(f) == 1, [x.message for x in f])
check("nennt beide Gruppen", f and f[0].params[1] == "archiv, medien",
      f and f[0].params)
check("einzelne gid ist still",
      validator._rule_group_gid_duplicate(dg, {}, False, None) == [],
      [x.message for x in validator._rule_group_gid_duplicate(dg, {}, False, None)])

check("Gruppe ohne gid wird nicht gemeldet",
      run_groups(validator._rule_group_gid_system,
                 write_config('{ ... }:\n{\n  users.groups.medien = { };\n}\n'),
                 {"medien": 3000}) == [])

original_host = validator.running_host
validator.running_host = lambda: "nix-desktop"
try:
    f = run_groups(validator._rule_group_gid_system, dg, {"docker": 131},
                   host="nix-laptop")
    check("fremder Rechner: Hinweis statt Urteil",
          len(f) == 1 and f[0].message_key == "validator.f.host_other",
          [(x.severity, x.message_key) for x in f])
finally:
    validator.running_host = original_host

print("\n16. Regeln sind registriert und standardmäßig an")
for rid in ("user_no_password", "user_uid_duplicate", "user_uid_system",
            "group_gid_duplicate", "group_gid_system"):
    check(f"{rid} im Katalog", rid in validator.RULE_MAP)
    check(f"{rid} hat eine Funktion", rid in validator._RULE_FNS)
    check(f"{rid} standardmäßig an",
          validator.default_validation_rules().get(rid) is True)

print("\n" + ("ALLE PRÜFUNGEN BESTANDEN" if not fails else f"FEHLGESCHLAGEN: {len(fails)}"))
sys.exit(1 if fails else 0)
