"""Test: Git-Aktionen in den drei möglichen Lagen.

Gemeldet aus der VM (2026-10-24): Nach einem Rebuild bot NiCo „Commit & Push"
an, obwohl das Config-Verzeichnis gar kein Git-Repository war – der Knopf
konnte nur scheitern. Dahinter steckten drei Dinge:

  * `rebuild.js` blendete den Knopf bedingungslos ein
  * `"Kein Git-Repository."` stand 20-mal hartkodiert deutsch im git_manager,
    lief also nie durch `t()` – ein englischsprachiger Nutzer las Deutsch
  * eine reine Commit-Route fehlte: committen braucht ein Repository, pushen
    zusätzlich eine Remote

Läuft ohne Server-Prozess und ohne Browser, legt eigene Wegwerf-Repos an.
"""
import json, os, pathlib, re, subprocess, sys, tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

fails = []
def check(label, cond, got=None):
    print(("  OK   " if cond else "  FAIL ") + label + ("" if cond or got is None else f"  (war: {got})"))
    if not cond: fails.append(label)


def make_env(with_repo: bool, with_remote: bool):
    """Wegwerf-Config mit eigenem XDG_CONFIG_HOME; gibt (client, dir) zurück."""
    tmp = tempfile.mkdtemp()
    cfg_home = pathlib.Path(tmp) / "xdg"
    (cfg_home / "nico").mkdir(parents=True)
    nixos_dir = pathlib.Path(tmp) / "config"
    nixos_dir.mkdir()
    os.environ["XDG_CONFIG_HOME"] = str(cfg_home)
    (cfg_home / "nico" / "settings.json").write_text(
        json.dumps({"nixos_config_dir": str(nixos_dir), "language": "de"}), encoding="utf-8")

    # APP_SETTINGS_FILE wird beim Modulimport aus XDG_CONFIG_HOME berechnet
    # (config_manager.py:40). Dieser Test baut mehrere Lagen in einem Prozess,
    # der Pfad muss also mitgezogen werden – sonst sehen alle Lagen die
    # Einstellungen der ersten. Im Betrieb ändert sich die Umgebung nicht.
    from nico import config_manager
    config_manager.APP_SETTINGS_FILE = cfg_home / "nico" / "settings.json"

    from nico import generator
    (nixos_dir / "configuration.nix").write_text(
        generator.generate_configuration_nix({"hostname": "vm", "username": "nico"}),
        encoding="utf-8")

    if with_repo:
        for args in (["init", "-q"], ["config", "user.email", "t@t"], ["config", "user.name", "t"],
                     ["add", "-A"], ["commit", "-q", "-m", "init"]):
            subprocess.run(["git"] + args, cwd=nixos_dir, capture_output=True)
        if with_remote:
            subprocess.run(["git", "remote", "add", "origin", "https://example.invalid/x.git"],
                           cwd=nixos_dir, capture_output=True)

    from nico.server import create_app
    app = create_app()
    c = app.test_client()
    csrf = re.search(r'name="csrf-token" content="([^"]+)"',
                     c.get("/").get_data(as_text=True)).group(1)
    c.environ_base["HTTP_X_CSRF_TOKEN"] = csrf
    return c, nixos_dir, {"X-CSRF-Token": csrf}


print("1. Kein Repository: der Zustand wird gemeldet, nicht geraten")
c, d, hdr = make_env(with_repo=False, with_remote=False)
rs = c.get("/api/git/remote-status").get_json()
check("has_git = False", rs.get("has_git") is False, rs)
check("has_remote = False", rs.get("has_remote") is False, rs)
r = c.post("/api/git/commit-push", json={}, headers=hdr).get_json()
check("commit-push lehnt ab", r.get("success") is False)
check("Meldung ist ein Code, kein deutscher Satz",
      r.get("message") == "ERR_NO_GIT_REPO", r.get("message"))
check("error_code gesetzt statt UNKNOWN",
      r.get("error_code") == "ERR_NO_GIT_REPO", r.get("error_code"))
r = c.post("/api/git/commit", json={}, headers=hdr).get_json()
check("reine Commit-Route lehnt ebenfalls ab", r.get("success") is False)
check("auch dort der Code", r.get("message") == "ERR_NO_GIT_REPO", r.get("message"))

print("\n2. Repository ohne Remote: Commit geht, Push nicht")
c, d, hdr = make_env(with_repo=True, with_remote=False)
rs = c.get("/api/git/remote-status").get_json()
check("has_git = True", rs.get("has_git") is True, rs)
check("has_remote = False", rs.get("has_remote") is False, rs)
(d / "configuration.nix").write_text(
    (d / "configuration.nix").read_text(encoding="utf-8") + "\n# Änderung\n", encoding="utf-8")
r = c.post("/api/git/commit", json={"label": "Test"}, headers=hdr).get_json()
check("Commit gelingt", r.get("success") is True, r)
log = subprocess.run(["git", "log", "--oneline", "-1"], cwd=d,
                     capture_output=True, text=True).stdout
check("Commit liegt im Log", "Test" in log, log.strip())
r = c.post("/api/git/push", json={}, headers=hdr).get_json()
check("Push meldet die fehlende Remote",
      r.get("success") is False and "REMOTE" in str(r.get("message")) + str(r.get("error_code")), r)

print("\n3. Nichts zu committen ist kein Fehler")
r = c.post("/api/git/commit", json={}, headers=hdr).get_json()
check("zweiter Commit ohne Änderung bleibt erfolgreich", r.get("success") is True, r)

print("\n4. Texte für die neuen Codes in allen sieben Sprachen")
for p in sorted((ROOT / "nico" / "static" / "lang").glob("*.json")):
    data = json.loads(p.read_text(encoding="utf-8"))
    vals = [data.get(k) for k in ("errors.ERR_NO_GIT_REPO", "errors.ERR_NO_REMOTE",
                                  "rebuild.commitOnly", "rebuild.commitDone")]
    check(f"{p.stem}", all(v and v != "__TODO__" for v in vals), vals)

print("\n5. Keine deutschen Git-Meldungen mehr im Code")
src = (ROOT / "nico" / "git_manager.py").read_text(encoding="utf-8")
check("'Kein Git-Repository' entfernt", "Kein Git-Repository" not in src)
check("'Kein Remote konfiguriert' entfernt", "Kein Remote konfiguriert" not in src)

print("\n6. Oberfläche nimmt im Zweifel nichts an")
gitjs = (ROOT / "nico" / "static" / "js" / "git.js").read_text(encoding="utf-8")
check("_gitStatus startet pessimistisch",
      "git_installed: false, has_git: false" in gitjs)
rebuildjs = (ROOT / "nico" / "static" / "js" / "rebuild.js").read_text(encoding="utf-8")
check("Rebuild-Knopf hängt an den Fähigkeiten", "git.canCommit" in rebuildjs)
check("Push-Knopf nur mit Remote", "git.canPush" in rebuildjs)
uijs = (ROOT / "nico" / "static" / "js" / "ui.js").read_text(encoding="utf-8")
check("Fehlermodal kennt die neuen Codes", "ERR_NO_GIT_REPO:" in uijs)

print("\n" + ("ALLE PRÜFUNGEN BESTANDEN" if not fails else f"FEHLGESCHLAGEN: {fails}"))
sys.exit(1 if fails else 0)
