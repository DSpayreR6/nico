#!/usr/bin/env python3
"""Revision 2026-11-25, Paket 4: Config verschieben (F1, F5, Speichersperre).

F1 – Über Dateisystemgrenzen kopierte und löschte shutil.move in einem
Aufruf. Brach das Löschen mittendrin ab, entfernte der Fehlerpfad die
vollständige Kopie, übrig blieb die halbe Quelle. Jetzt (Entscheidung
Martin 2026-11-26): kopieren → fsync → aus dem Seitencache werfen →
Prüfsummen vergleichen → erst dann Quelle löschen. Abweichung: Kopie weg,
Quelle unberührt. Löschen scheitert: Kopie bleibt, Reste werden gemeldet.

F5 – Scheiterte das Speichern des neuen Pfads, gab es einen 500er, die
Config lag am neuen Ort und niemand sagte wo. Jetzt meldet die Route
path_saved, und /api/config/path setzt nur den Pfad (neuer Ort, anderer
Pfad oder leer = Einrichtung neu) – für das Pflichtfenster nach dem Umzug.

Speichersperre – Während des Umzugs lehnt der Server jedes Schreiben ab.

Das andere Dateisystem wird nachgestellt: configmove._rename meldet EXDEV.

Lauf: nix-shell shell.nix --run "python testing/routes/test_rev1125_paket4.py"
"""
import errno, hashlib, json, os, re, sys, tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

base = Path(tempfile.mkdtemp(prefix="nico-mv4-"))
xdg = base / "xdg"
os.environ["XDG_CONFIG_HOME"] = str(xdg)
(xdg / "nico").mkdir(parents=True)
SETTINGS = xdg / "nico" / "settings.json"


def make_config(name):
    cfg = base / name
    (cfg / ".git" / "objects").mkdir(parents=True)
    (cfg / ".git" / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    (cfg / ".git" / "objects" / "ab").write_bytes(os.urandom(4096))
    (cfg / "modules").mkdir()
    (cfg / "configuration.nix").write_text("{ ... }:\n{\n}\n", encoding="utf-8")
    (cfg / "modules" / "a.nix").write_text("{ }\n", encoding="utf-8")
    (cfg / "modules" / "link.nix").symlink_to("a.nix")
    (cfg / "config.json").write_text(json.dumps({"hosts_dir": "hosts"}), encoding="utf-8")
    SETTINGS.write_text(json.dumps({"nixos_config_dir": str(cfg), "language": "de",
                                    "git_sync": False}), encoding="utf-8")
    return cfg


def tree(root):
    out = {}
    for dp, dn, fn in os.walk(root):
        for n in dn + fn:
            p = os.path.join(dp, n)
            rel = os.path.relpath(p, root)
            if os.path.islink(p):
                out[rel] = "link:" + os.readlink(p)
            elif os.path.isfile(p):
                out[rel] = hashlib.sha256(open(p, "rb").read()).hexdigest()
            else:
                out[rel] = "dir"
    return out


cfg = make_config("alt")
from nico import config_manager, configmove, server  # noqa: E402
from nico.routes import rebuild as rb  # noqa: E402

fails = []
def check(label, cond, got=None):
    print(("  OK   " if cond else "  FAIL ") + label + ("" if cond or got is None else f"  (war: {got})"))
    if not cond: fails.append(label)

def setting():
    return json.loads(SETTINGS.read_text(encoding="utf-8"))["nixos_config_dir"]

client = server.create_app().test_client()
page = client.get("/").get_data(as_text=True)
csrf = re.search(r'<meta name="csrf-token" content="([^"]+)"', page).group(1)
H = {"X-CSRF-Token": csrf, "Content-Type": "application/json"}

def post(url, body):
    r = client.post(url, headers=H, data=json.dumps(body))
    return r.status_code, (r.get_json(silent=True) or {})

def exdev(src, dst):
    raise OSError(errno.EXDEV, "cross-device link")

real_rename = configmove._rename
real_rmtree = configmove.shutil.rmtree
real_flush = configmove._flush_tree
configmove._rename = exdev

# ── 1. Erfolg über Dateisystemgrenzen ───────────────────────────────────────
print("1. Über Dateisystemgrenzen: kopieren, prüfen, löschen")
before = tree(cfg)
flushed = []
def flush_spy(root):
    flushed.append(str(root))
    real_flush(root)
configmove._flush_tree = flush_spy
s, r = post("/api/config/move", {"target": str(base / "neu1")})
configmove._flush_tree = real_flush
check("verschoben", s == 200 and r.get("success"), (s, r))
check("Ziel vor der Prüfung auf Platte geschrieben (fsync, Cache verworfen)", flushed == [str((base / "neu1").resolve())], flushed)
check("Ziel bytegleich, Links als Links, mit .git", tree(base / "neu1") == before)
check("Quelle weg", not cfg.exists())
check("keine Reste gemeldet", r.get("source_left") is False, r)
check("Pfad gespeichert und gemeldet", r.get("path_saved") is True and setting() == str((base / "neu1").resolve()), r)

# ── 2. Prüfung schlägt an ───────────────────────────────────────────────────
print("\n2. Kopie weicht ab")
cfg = make_config("alt2")
before = tree(cfg)
def corrupt(root):
    real_flush(root)
    (Path(root) / "modules" / "a.nix").write_text("{ kaputt }\n")
configmove._flush_tree = corrupt
s, r = post("/api/config/move", {"target": str(base / "neu2")})
configmove._flush_tree = real_flush
check("abgelehnt mit ERR_MOVE_VERIFY und Datei", s == 400 and r.get("error", "").startswith("ERR_MOVE_VERIFY")
      and "modules/a.nix" in r.get("error", ""), (s, r))
check("Quelle unverändert", tree(cfg) == before)
check("Kopie entfernt", not (base / "neu2").exists())
check("Pfad zeigt weiter auf die Quelle", setting() == str(cfg))

# ── 3. Löschen der Quelle scheitert mittendrin ─────────────────────────────
print("\n3. Löschen der Quelle bricht ab (der Fall aus F1)")
cfg = make_config("alt3")
before = tree(cfg)
def half_rmtree(path, *a, **kw):
    if Path(path).resolve() == cfg.resolve():
        (cfg / "configuration.nix").unlink()          # first file is gone …
        raise OSError("Gerät belegt")                 # … then it breaks off
    return real_rmtree(path, *a, **kw)
configmove.shutil.rmtree = half_rmtree
s, r = post("/api/config/move", {"target": str(base / "neu3")})
configmove.shutil.rmtree = real_rmtree
check("gilt als verschoben", s == 200 and r.get("success"), (s, r))
check("… mit Hinweis auf Reste an der Quelle", r.get("source_left") is True and r.get("old") == str(cfg), r)
check("Ziel vollständig und unangetastet", tree(base / "neu3") == before)
check("Pfad zeigt auf das Ziel", setting() == str((base / "neu3").resolve()))

# ── 4. Speichern des Pfads scheitert (F5) ──────────────────────────────────
print("\n4. Pfad kann nicht gespeichert werden")
configmove._rename = real_rename
cfg = make_config("alt4")
real_save = config_manager.save_app_settings
def save_fails(_):
    raise OSError("Platte voll")
config_manager.save_app_settings = save_fails
s, r = post("/api/config/move", {"target": str(base / "neu4")})
config_manager.save_app_settings = real_save
check("kein 500, sondern Erfolg mit path_saved false", s == 200 and r.get("path_saved") is False, (s, r))
check("… nennt das Ziel", r.get("target") == str((base / "neu4").resolve()), r)
check("Config liegt am Ziel", (base / "neu4" / "configuration.nix").exists())

print("\n5. /api/config/path: nur den Pfad setzen")
s, r = post("/api/config/path", {"path": str(base / "neu4")})
check("neuer Ort gesetzt", s == 200 and setting() == str((base / "neu4").resolve()), (s, r))
check("nichts angelegt (kein .git, keine Hardware-Datei)",
      not (base / "neu4" / ".git" / "config").exists()
      and not (base / "neu4" / "hardware-configuration.nix").exists())
s, r = post("/api/config/path", {"path": str(base / "gibtsnicht")})
check("nicht vorhandenes Verzeichnis abgelehnt", s == 400 and r.get("error") == "ERR_NOT_A_DIR", (s, r))
s, r = post("/api/config/path", {"path": "/etc"})
check("Systemverzeichnis abgelehnt", s == 400, (s, r))
s, r = post("/api/config/path", {"path": ""})
check("leer: Einrichtung startet neu", s == 200 and setting() == "", (s, r))
config_manager.save_app_settings = save_fails
s, r = post("/api/config/path", {"path": str(base / "neu4")})
config_manager.save_app_settings = real_save
check("Speichern scheitert: ERR_PATH_SAVE", s == 500 and r.get("error") == "ERR_PATH_SAVE", (s, r))
post("/api/config/path", {"path": str(base / "neu4")})

# ── 6. Speichersperre ───────────────────────────────────────────────────────
print("\n6. Während des Umzugs wird nichts geschrieben")
check("Reservierung für den Umzug", rb.reserve("move") is None)
s, r = post("/api/file", {"files": [{"path": "configuration.nix", "content": "{ }\n"}]})
check("Speichern im Textmodus: 409 ERR_BUSY_MOVE", s == 409 and r.get("error") == "ERR_BUSY_MOVE", (s, r))
s, r = post("/api/config", {})
check("Speichern aus dem Panel: 409 ERR_BUSY_MOVE", s == 409 and r.get("error") == "ERR_BUSY_MOVE", (s, r))
check("Datei unverändert", (base / "neu4" / "configuration.nix").read_text() == "{ ... }:\n{\n}\n")
r = client.get("/api/rebuild/status")
check("Lesen geht weiter", r.status_code == 200)
rb.release()
s, r = post("/api/file", {"files": [{"path": "modules/a.nix", "content": "{ x = 1; }\n"}]})
check("danach wieder speichern", s == 200 and r.get("success"), (s, r))

print()
if fails:
    print(f"{len(fails)} FEHLER")
    sys.exit(1)
print("Alles grün.")
