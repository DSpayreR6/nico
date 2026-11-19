#!/usr/bin/env python3
"""Vor dem Überschreiben oder Umbauen liegt der alte Stand wirklich vor.

Revision 2026-11-18, Befund 2 (Codex, bestätigt von Claude Opus 5.5, der den
Code geschrieben hatte): Beim Überschreiben wurde das Ergebnis des
Sicherungs-Commits verworfen, ohne git gab es gar keine Sicherung. Beim
Umbauen galt „nichts zu committen“ als Erfolg – eine ignorierte oder nie
committete Datei stand in keinem Commit. Jetzt prüft
git_manager.backup_before_write den Commit gegen die Platte und legt sonst
eine ZIP an; scheitert der Commit selbst, wird nicht geschrieben.

Die drei Abnahmekriterien von Codex:
  1. Überschreiben mit gesperrtem git-Index: Fehler, Fassung von außen bleibt.
  2. Umbauen einer ignorierten, unversionierten Host-Datei: Die gemeldete
     Sicherung enthält genau den alten Inhalt.
  3. Erfolgsfall: alter Inhalt im gemeldeten Sicherungspunkt, auch wenn HEAD
     ihn schon vorher hielt; ohne git in der ZIP.

Lauf: nix-shell shell.nix --run "python testing/routes/test_backup_before_write.py"
"""
import json, os, re, subprocess, sys, tempfile, zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

xdg = Path(tempfile.mkdtemp(prefix="nico-xdg-"))
os.environ["XDG_CONFIG_HOME"] = str(xdg)
cfg = Path(tempfile.mkdtemp(prefix="nico-cfg-"))
os.environ["GIT_CEILING_DIRECTORIES"] = str(cfg.parent)
(cfg / "hosts" / "h1").mkdir(parents=True)
(cfg / "config.json").write_text(json.dumps({"hosts_dir": "hosts"}), encoding="utf-8")
(xdg / "nico").mkdir(parents=True)
(xdg / "nico" / "settings.json").write_text(
    json.dumps({"nixos_config_dir": str(cfg), "language": "de", "git_sync": False}),
    encoding="utf-8")

from nico import config_manager, core, git_manager, server  # noqa: E402

fails = []
def check(label, cond, got=None):
    print(("  OK   " if cond else "  FAIL ") + label + ("" if cond or got is None else f"  (war: {got})"))
    if not cond: fails.append(label)

client = server.create_app().test_client()
page = client.get("/").get_data(as_text=True)
csrf = re.search(r'<meta name="csrf-token" content="([^"]+)"', page).group(1)
H = {"X-CSRF-Token": csrf, "Content-Type": "application/json"}

def post(url, body):
    r = client.post(url, headers=H, data=json.dumps(body))
    return r.status_code, (r.get_json(silent=True) or {})

def read(rel):
    return (cfg / rel).read_text(encoding="utf-8")

def change_outside(rel, old, new):
    text = read(rel).replace(old, new)
    (cfg / rel).write_text(core.set_type_in_content(text, core.get_nico_type(text)),
                           encoding="utf-8")

def form(rel):
    data = client.get(f"/api/parse/co?path={rel}", headers=H).get_json()
    data.update({"_co_ready": True, "_co_path": rel})
    return data

def head(rel):
    r = subprocess.run(["git", "show", f"HEAD~1:./{rel}"], cwd=cfg, capture_output=True, text=True)
    return r.stdout if r.returncode == 0 else None

def in_zip(name, rel):
    with zipfile.ZipFile(cfg / name) as zf:
        return zf.read(rel).decode("utf-8")

CO = "configuration.nix"
HOST = "hosts/h1/default.nix"

s, r = post("/api/config", dict(config_manager.get_defaults(), hostname="alt",
                                _co_ready=True, _co_path=CO))
assert s == 200, r
git_manager.init_repo(str(cfg))
git_manager.auto_commit(str(cfg), label="start")

print("1. Überschreiben, git-Index gesperrt")
stale = form(CO)
change_outside(CO, '"alt"', '"vonaussen"')
outside_state = read(CO)
lock = cfg / ".git" / "index.lock"
lock.write_text("")
s, r = post("/api/config", dict(stale, _overwrite=True))
lock.unlink()
check("Sicherungsfehler gemeldet", s == 500 and r.get("error") == "ERR_RESTRUCTURE_BACKUP", (s, r))
check("Fassung von außen steht noch auf der Platte", read(CO) == outside_state)

print("\n   … und mit funktionierendem git")
s, r = post("/api/config", dict(stale, _overwrite=True))
check("Überschreiben ok", s == 200 and r.get("success"), r)
check("Fassung von außen im Sicherungspunkt davor",
      subprocess.run(["git", "log", "-p", "--all", "--", CO], cwd=cfg, capture_output=True,
                     text=True).stdout.count('"vonaussen"') >= 1)

print("\n2. Umbauen einer ignorierten, unversionierten Host-Datei")
(cfg / ".gitignore").write_text("hosts/h1/default.nix\n*.zip\n", encoding="utf-8")
git_manager.auto_commit(str(cfg), label="ignore")
host_text = '{ config, pkgs, ... }:\n{\n  networking.hostName = "h1";\n  services.foo.enable = true;\n}\n'
(cfg / HOST).write_text(host_text, encoding="utf-8")
s, r = post("/api/file/restructure", {"path": HOST})
check("Umbauen ok", s == 200 and r.get("success"), r)
check("Sicherung ist eine ZIP, nicht der leere Commit", r.get("backup") == "zip" and r.get("zip"), r)
check("ZIP enthält genau den alten Inhalt",
      r.get("zip") and in_zip(r["zip"], HOST) == host_text)

print("\n3. Erfolgsfall mit git")
co_text = '{ config, pkgs, ... }:\n{\n  networking.hostName = "drei";\n}\n'
(cfg / CO).write_text(co_text, encoding="utf-8")
s, r = post("/api/file/restructure", {"path": CO})
check("Umbauen ok, Sicherungspunkt", s == 200 and r.get("backup") == "git", r)
check("alter Inhalt im Sicherungspunkt", head(CO) == co_text, head(CO))

print("\n   HEAD hält den Stand schon (git pull)")
(cfg / CO).write_text(co_text, encoding="utf-8")
git_manager.auto_commit(str(cfg), label="pull")
s, r = post("/api/file/restructure", {"path": CO})
log = subprocess.run(["git", "log", "--format=%s", "-3"], cwd=cfg, capture_output=True, text=True).stdout
check("Umbauen ok, Sicherungspunkt", s == 200 and r.get("backup") == "git", r)
check("Stand davor ist der Commit vor dem Umbau", head(CO) == co_text, (head(CO), log))

print("\n4. Ohne git: ZIP")
cfg2 = Path(tempfile.mkdtemp(prefix="nico-cfg2-"))
(cfg2 / "configuration.nix").write_text(co_text, encoding="utf-8")
kind, detail = git_manager.backup_before_write(str(cfg2), [CO], label="x")
check("ZIP angelegt", kind == "zip" and (cfg2 / detail).is_file(), (kind, detail))
with zipfile.ZipFile(cfg2 / detail) as zf:
    check("… mit genau dem Inhalt", zf.read(CO).decode() == co_text)

print()
if fails:
    print(f"{len(fails)} FEHLER:", *fails, sep="\n  ")
    sys.exit(1)
print("Alle Prüfungen bestanden.")
