#!/usr/bin/env python3
"""Config verschieben, 2026-11-25.

Martin: „Echt verschieben" – das ganze Config-Verzeichnis samt Versionierung
an einen anderen Ort, danach ist der alte Ort weg und der Config-Pfad zeigt
auf den neuen. Zeigte /etc/nixos auf den alten Ort, meldet die Route das,
damit das Fenster das Umstellen anbietet.

Geprüft wird (alles in Temp-Verzeichnissen, /etc/nixos nachgebaut):
  1. ohne CSRF abgelehnt; laufender Job 409
  2. Ziel in der Config, Ziel enthält die Config, Ziel nicht leer,
     Systemverzeichnis: abgelehnt, nichts verändert
  3. abgebrochenes Kopieren: Quelle unverändert, Teilkopie entfernt
  4. Erfolg: alles am neuen Ort (inkl. .git), alter Ort weg, Config-Pfad
     umgestellt; leeres Zielverzeichnis wird genommen
  5. symlink_old: /etc/nixos zeigte auf den alten Ort → true, sonst false

Lauf: nix-shell shell.nix --run "python testing/routes/test_config_move.py"
"""
import json
import os
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

base = Path(tempfile.mkdtemp(prefix="nico-move-"))
xdg = base / "xdg"
os.environ["XDG_CONFIG_HOME"] = str(xdg)
(xdg / "nico").mkdir(parents=True)
SETTINGS = xdg / "nico" / "settings.json"


def make_config(name):
    cfg = base / name
    (cfg / ".git").mkdir(parents=True)
    (cfg / ".git" / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    (cfg / "configuration.nix").write_text("{ ... }:\n{\n}\n", encoding="utf-8")
    (cfg / "config.json").write_text(json.dumps({"hosts_dir": "hosts"}), encoding="utf-8")
    SETTINGS.write_text(json.dumps({"nixos_config_dir": str(cfg), "language": "de",
                                    "git_sync": False}), encoding="utf-8")
    return cfg


cfg = make_config("alt")

from nico import configmove, server  # noqa: E402
from nico.routes import rebuild as rb  # noqa: E402

fails = []


def check(label, cond, got=None):
    print(("  OK   " if cond else "  FAIL ") + label
          + ("" if cond or got is None else f"  (war: {got})"))
    if not cond:
        fails.append(label)


def setting():
    return json.loads(SETTINGS.read_text(encoding="utf-8"))["nixos_config_dir"]


app = server.create_app()
client = app.test_client()
page = client.get("/").get_data(as_text=True)
m = re.search(r'<meta name="csrf-token" content="([^"]+)"', page)
hdr = {"X-CSRF-Token": m.group(1) if m else "", "Content-Type": "application/json"}


def move(target, headers=hdr):
    r = client.post("/api/config/move", headers=headers, data=json.dumps({"target": str(target)}))
    return r.status_code, r.get_json()


print("1. Schutz")
code, d = move(base / "neu", {"Content-Type": "application/json"})
check("ohne CSRF abgelehnt", code in (400, 403) and cfg.exists(), code)
with rb._job_cv:
    rb._job = {"running": True}
code, d = move(base / "neu")
check("laufender Job: 409", code == 409 and d.get("error") == "ERR_REBUILD_RUNNING", (code, d))
with rb._job_cv:
    rb._job = None

print("2. Ungültige Ziele")
code, d = move(cfg / "unter")
check("Ziel in der Config", d.get("error") == "ERR_MOVE_INSIDE", d)
code, d = move(base)
check("Ziel enthält die Config", d.get("error") == "ERR_MOVE_CONTAINS", d)
full = base / "voll"
full.mkdir()
(full / "x").write_text("x", encoding="utf-8")
code, d = move(full)
check("Ziel nicht leer", d.get("error") == "ERR_MOVE_NOT_EMPTY", d)
code, d = move("/etc/nico-test")
check("Systemverzeichnis", d.get("error") == "ERR_SYSTEM_PATH", d)
check("nichts verändert", cfg.exists() and setting() == str(cfg))

print("3. Abgebrochenes Kopieren")
# Across filesystems (since 2026-11-26 its own copy - verify - delete,
# configmove._move_across): the rename reports EXDEV, the copy breaks off.
import errno  # noqa: E402
real_rename, real_copytree = configmove._rename, configmove.shutil.copytree


def exdev(src, dst):
    raise OSError(errno.EXDEV, "cross-device link")


def broken(src, dst, **kw):
    Path(dst).mkdir(parents=True)
    (Path(dst) / "halb").write_text("x", encoding="utf-8")
    raise OSError("Datenträger voll")


configmove._rename, configmove.shutil.copytree = exdev, broken
code, d = move(base / "abbruch")
configmove._rename, configmove.shutil.copytree = real_rename, real_copytree
check("Fehler ERR_MOVE_FAILED", d.get("error") == "ERR_MOVE_FAILED", d)
check("Quelle unverändert, Config-Pfad alt", (cfg / "configuration.nix").exists() and setting() == str(cfg))
check("Teilkopie entfernt", not (base / "abbruch").exists())

print("4. Erfolg")
configmove.ETC_NIXOS = base / "etc-nixos"          # no symlink
empty = base / "leer"
empty.mkdir()
code, d = move(empty)
check("verschoben", code == 200 and d.get("success"), (code, d))
check("alles am neuen Ort, mit Versionierung", (empty / "configuration.nix").exists()
      and (empty / ".git" / "HEAD").exists() and (empty / "config.json").exists())
check("alter Ort weg", not cfg.exists())
check("Config-Pfad umgestellt", setting() == str(empty.resolve()), setting())
check("ohne Symlink: symlink_old false", d.get("symlink_old") is False, d)

print("5. Symlink zeigte auf den alten Ort")
cfg = make_config("zweite")
link = base / "etc-nixos2"
link.symlink_to(cfg)
configmove.ETC_NIXOS = link
code, d = move(base / "ziel2")
check("verschoben", code == 200 and d.get("success"), (code, d))
check("symlink_old true", d.get("symlink_old") is True, d)

if fails:
    print(f"\nFEHLGESCHLAGEN: {len(fails)}")
    sys.exit(1)
print("\nAlle Prüfungen bestanden.")
