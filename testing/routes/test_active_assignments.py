"""Test: Der Validator liest nur aktive Zuweisungen.

Hintergrund (Revision 2026-11-20 durch Codex, umgesetzt 2026-11-21): Die
Benutzer- und Gruppenregeln lasen ihre Werte mit Suchmustern aus dem Text.
Gegenprobe vom 2026-11-21 mit einer einzigen Datei:

  * `# hashedPassword = "…";` im Benutzerblock galt als gesetztes Passwort,
  * `# extraGroups = [ "wheel" ];` galt als Admin,
  * `users = { mutableUsers = false; };` wurde übersehen,
  * `uid = 1234 + 1;` wurde als 1234 gelesen.

Zusammen: ein Konto ohne Passwort und ohne sudo, ein Rebuild, der bestehende
Passwörter verwirft – und der Validator schwieg. Jetzt liefert
nix_parser.assignments() jede aktive Zuweisung mit vollem Pfad; Kommentare
und deaktivierte Nix-Brix sind für tree-sitter keine Zuweisungen.

Läuft ohne Server und ohne Browser.
Lauf: nix-shell shell.nix --run "python testing/routes/test_active_assignments.py"
"""
import pathlib, sys, tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from nico import nix_parser, validator

fails = []
def check(label, cond, got=None):
    print(("  OK   " if cond else "  FAIL ") + label + ("" if cond or got is None else f"  (war: {got})"))
    if not cond: fails.append(label)


def write_config(text: str) -> str:
    tmp = tempfile.mkdtemp(prefix="nico-assign-")
    (pathlib.Path(tmp) / "configuration.nix").write_text(text, encoding="utf-8")
    return tmp


def run(rule, nixos_dir, uids=None):
    original = validator._system_uids
    validator._system_uids = lambda: (uids if uids is not None else {})
    try:
        return rule(nixos_dir, {}, False, None)
    finally:
        validator._system_uids = original


def ids(findings):
    return [f.rule_id for f in findings]


# ── 1. Der Fall der Gegenprobe ───────────────────────────────────────────────
print("1. Gegenprobe: auskommentiert, Blockform, Ausdruck")
GEGENPROBE = '''{ config, pkgs, ... }:
{
  users = {
    mutableUsers = false;
  };
  users.users.anna = {
    isNormalUser = true;
    # hashedPassword = "$6$abc";
    # extraGroups = [ "wheel" ];
    uid = 1234 + 1;
  };
  system.stateVersion = "24.05";
}
'''
d = write_config(GEGENPROBE)
anna = validator._declared_users(d).get("anna") or {}
check("auskommentiertes Passwort zählt nicht", anna.get("has_login") is False, anna)
check("auskommentiertes wheel zählt nicht", anna.get("is_admin") is False, anna)
check("uid-Ausdruck wird nicht auf 1234 gekürzt", anna.get("uid") is None, anna)
check("mutableUsers = false in Blockform erkannt", validator._mutable_users_off(d) is True)
check("Regel: anna ohne Anmeldung (auch wenn es sie schon gibt)",
      "user_no_password" in ids(run(validator._rule_user_no_password, d, {"anna": 1235})))
check("Regel: kein Admin", "no_admin_user" in ids(run(validator._rule_no_admin_user, d)))
a = [x for x in nix_parser.assignments(GEGENPROBE) if x.key == "users.users.anna.uid"]
check("Ausdruck bleibt als Text erkennbar", a and a[0].value_text == "1234 + 1", a)

# ── 2. Literale werden weiter erkannt ────────────────────────────────────────
print("2. Literale Werte")
d = write_config('''{ ... }:
{
  users.users.bert = {
    isNormalUser = true;
    uid = 1001;
    hashedPassword = "$6$x";
    extraGroups = [ "video" "wheel" ];
  };
  users.groups.media.gid = 990;
}
''')
bert = validator._declared_users(d).get("bert") or {}
check("literale uid 1001", bert.get("uid") == 1001, bert)
check("Passwort gesetzt", bert.get("has_login") is True, bert)
check("wheel gesetzt", bert.get("is_admin") is True, bert)
check("gid 990 (Punktform)", (validator._declared_groups(d).get("media") or {}).get("gid") == 990)

# ── 3. mutableUsers: Kommentar und Priorität ─────────────────────────────────
print("3. mutableUsers")
d = write_config('{ ... }:\n{\n  # users.mutableUsers = false;\n  users.users.c.isNormalUser = true;\n}\n')
check("auskommentiert → nicht aus", validator._mutable_users_off(d) is False)
d = write_config('{ lib, ... }:\n{\n  users.mutableUsers = lib.mkForce false;\n}\n')
check("lib.mkForce false → aus", validator._mutable_users_off(d) is True)
d = write_config('{ ... }:\n{\n  users.mutableUsers = true;\n}\n')
check("true → nicht aus", validator._mutable_users_off(d) is False)

# ── 4. Nix-Brix: aktiv zählt, deaktiviert nicht ──────────────────────────────
print("4. Nix-Brix")
d = write_config('''{ ... }:
{
  users.users.dora.isNormalUser = true;
  # <brick: USER / #1 pw inactive>
  # users.users.dora.hashedPassword = "$6$x";
  # </brick: pw>
}
''')
check("deaktivierter Brick: kein Passwort",
      (validator._declared_users(d).get("dora") or {}).get("has_login") is False)
d = write_config('''{ ... }:
{
  users.users.dora.isNormalUser = true;
  # <brick: USER / #1 pw>
  users.users.dora.hashedPassword = "$6$x";
  # </brick: pw>
}
''')
check("aktiver Brick: Passwort",
      (validator._declared_users(d).get("dora") or {}).get("has_login") is True)

# ── 5. mkIf / let ────────────────────────────────────────────────────────────
print("5. mkIf und let")
d = write_config('''{ lib, ... }:
let pw = "x"; in
{
  users.users.emil = lib.mkIf true { isNormalUser = true; initialPassword = pw; };
}
''')
emil = validator._declared_users(d).get("emil") or {}
check("Passwort in mkIf gesehen", emil.get("has_login") is True, emil)
keys = [x.key for x in nix_parser.assignments(open(pathlib.Path(d) / "configuration.nix").read())]
check("let-Name ist keine Option", "pw" not in keys, keys)
check("hashedPassword = null zählt nicht",
      validator._declared_users(write_config(
          '{ ... }:\n{\n  users.users.f = { isNormalUser = true; hashedPassword = null; };\n}\n')
      ).get("f", {}).get("has_login") is False)

d = write_config('''{ config, ... }:
{
  users.users."${config.me}".isNormalUser = true;
}
''')
check("Name aus Variable wird nicht geprüft", validator._declared_users(d) == {},
      validator._declared_users(d))

# ── 6. stateVersion ──────────────────────────────────────────────────────────
print("6. stateVersion")
d = write_config('{ ... }:\n{\n  # system.stateVersion = "23.11";\n}\n')
check("auskommentiert → keine Fundstelle", validator._state_version_sites(d, None) == [])
d = write_config('{ ... }:\n{\n  system = {\n    stateVersion = "24.05";\n  };\n}\n')
check("Blockform erkannt, Zeile 4",
      validator._state_version_sites(d, None) == [("24.05", "configuration.nix", 4, "")],
      validator._state_version_sites(d, None))
d = write_config('{ lib, ... }:\n{\n  system.stateVersion = lib.mkDefault "25.05";\n}\n')
check("mkDefault mit Priorität",
      validator._state_version_sites(d, None) == [("25.05", "configuration.nix", 3, "mkDefault")],
      validator._state_version_sites(d, None))

d = write_config('''{ inputs, ... }:
{
  outputs = { nixpkgs, ... }: {
    nixosConfigurations.x = nixpkgs.lib.nixosSystem {
      modules = [ ({ ... }: { system.stateVersion = "23.05"; }) ];
    };
  };
}
''')
check("Modul als Funktion in der flake.nix",
      [s[0] for s in validator._state_version_sites(d, None)] == ["23.05"],
      validator._state_version_sites(d, None))
d = write_config('''{ lib, ... }:
{
  users.users = lib.genAttrs [ "a" "b" ] (name: { isNormalUser = true; });
}
''')
check("Vorlage (genAttrs) erzeugt keinen Benutzer", validator._declared_users(d) == {},
      validator._declared_users(d))

# ── 7. Ersatzweg ohne tree-sitter ────────────────────────────────────────────
print("7. Ersatzweg ohne tree-sitter")
rx = {a.key: a.value_text for a in nix_parser._rx_assignments(GEGENPROBE)}
check("Blockform verschachtelt", rx.get("users.mutableUsers") == "false", rx)
check("Kommentar nicht gelesen", "users.users.anna.hashedPassword" not in rx, rx)
check("Ausdruck als Text", rx.get("users.users.anna.uid") == "1234 + 1", rx)

print()
if fails:
    print(f"{len(fails)} Prüfung(en) fehlgeschlagen.")
    sys.exit(1)
print("Alle Prüfungen bestanden.")
