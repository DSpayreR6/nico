#!/usr/bin/env python3
"""Revision 2026-11-25, Paket 5: Import (F4, S4, W2).

F4 – Die ZIP-Vorprüfung nahm ein Verzeichnis `configuration.nix/` als
Hauptdatei, die Übernahme prüfte gar nicht: Die alten .nix-Dateien gingen,
nichts Baubares kam, im Textmodus mit Erfolgsmeldung. Jetzt prüft
replace_nix_tree (jeder Import läuft dort durch) und die ZIP-Route vor der
Backup-Frage.

S4 – Die Rückabwicklung eines gescheiterten Imports merkte sich nur Bytes:
Aus einem Link wurde eine Kopie, aus 0600 wurde 0644, gemeldet wurde
„wiederhergestellt“. Und für Links nach außen las sie die Datei am anderen
Ende. Jetzt Linkziel und Rechte, das Ziel wird nicht gelesen.

W2 – Die Wahl Panel/Text wurde nach dem Import gespeichert, ein Fehler
verschluckt. Jetzt zuerst speichern; scheitert das, kein Import; scheitert
der Import, wird die Wahl zurückgesetzt (Martin 2026-11-26).

Lauf: nix-shell shell.nix --run "python testing/routes/test_rev1125_paket5.py"
"""
import hashlib, io, json, os, re, stat, sys, tempfile, zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

xdg = Path(tempfile.mkdtemp(prefix="nico-xdg-"))
os.environ["XDG_CONFIG_HOME"] = str(xdg)
cfg = Path(tempfile.mkdtemp(prefix="nico-cfg-"))
ext = Path(tempfile.mkdtemp(prefix="nico-ext-"))
(xdg / "nico").mkdir(parents=True)
(xdg / "nico" / "settings.json").write_text(
    json.dumps({"nixos_config_dir": str(cfg), "language": "de", "git_sync": False}),
    encoding="utf-8")

from nico import atomic, config_manager, importer, server  # noqa: E402

fails = []
def check(label, cond, got=None):
    print(("  OK   " if cond else "  FAIL ") + label + ("" if cond or got is None else f"  (war: {got})"))
    if not cond: fails.append(label)

def setup_cfg():
    for p in sorted(cfg.rglob("*"), reverse=True):
        if p.is_symlink() or p.is_file():
            p.unlink()
        elif p.is_dir():
            p.rmdir()
    (cfg / "config.json").write_text(json.dumps({"hosts_dir": "hosts", "panel_default": "p"}))
    (cfg / "modules").mkdir()
    (cfg / "configuration.nix").write_text("{ ... }: { imports = [ ./modules/a.nix ]; }\n")
    (cfg / "flake.lock").write_text('{"nodes": {}}\n')
    (cfg / "modules" / "a.nix").write_text("{ }\n")
    (cfg / "modules" / "geheim.nix").write_text("{ wlan = \"x\"; }\n")
    os.chmod(cfg / "modules" / "geheim.nix", 0o600)
    (cfg / "modules" / "link.nix").symlink_to("a.nix")
    (ext / "aussen.nix").write_text("{ aussen = true; }\n")
    lnk = cfg / "modules" / "aussen.nix"
    lnk.symlink_to(ext / "aussen.nix")

def snapshot():
    out = {}
    for dp, dn, fn in os.walk(cfg):
        for n in fn:
            p = os.path.join(dp, n)
            rel = os.path.relpath(p, cfg)
            if os.path.islink(p):
                out[rel] = ("link", os.readlink(p))
            else:
                out[rel] = ("file", hashlib.sha256(open(p, "rb").read()).hexdigest(),
                            stat.S_IMODE(os.stat(p).st_mode))
    return out

def make_zip(entries):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, data in entries.items():
            if name.endswith("/"):
                z.writestr(zipfile.ZipInfo(name), b"")
            else:
                z.writestr(name, data)
    return buf.getvalue()

client = server.create_app().test_client()
page = client.get("/").get_data(as_text=True)
csrf = re.search(r'<meta name="csrf-token" content="([^"]+)"', page).group(1)

def zip_post(url, data, **form):
    fields = {"file": (io.BytesIO(data), "x.zip"), **form}
    r = client.post(url, headers={"X-CSRF-Token": csrf}, data=fields,
                    content_type="multipart/form-data")
    return r.status_code, (r.get_json(silent=True) or {})

def panel_default():
    return json.loads((cfg / "config.json").read_text()).get("panel_default")

BAD = make_zip({"configuration.nix/": b"", "configuration.nix/x.nix": b"{ }\n", "readme.txt": b"hi"})
GOOD = make_zip({"configuration.nix": b"{ ... }: { networking.hostName = \"neu\"; }\n",
                 "modules/b.nix": b"{ }\n"})

# ── F4 ──────────────────────────────────────────────────────────────────────
print("F4: ZIP ohne Hauptdatei")
setup_cfg()
before = snapshot()
s, r = zip_post("/api/import/zip/check", BAD)
check("Vorprüfung: Verzeichnis 'configuration.nix/' ist keine Hauptdatei", r.get("valid") is False, r)
s, r = zip_post("/api/import/zip/apply", BAD, confirmed="true", edit_mode="r")
check("Übernahme abgelehnt: 400 ERR_IMPORT_NO_MAIN", s == 400 and r.get("error") == "ERR_IMPORT_NO_MAIN", (s, r))
check("… alle Dateien bytegleich, flake.lock inklusive", snapshot() == before)
check("… kein Backup-ZIP angelegt", not list(cfg.glob("nixos-config-*.zip")))
check("… Wahl Panel/Text nicht gespeichert", panel_default() == "p")
try:
    importer.replace_nix_tree(cfg, {})
    check("leere Übernahmeliste abgelehnt", False)
except importer.ImportNoMainError:
    check("leere Übernahmeliste abgelehnt (ImportNoMainError)", True)
check("… nichts angefasst", snapshot() == before)
s, r = zip_post("/api/import/zip/check", GOOD)
check("Gegenprobe Vorprüfung: gültig", r.get("valid") is True and r.get("found_file") == "configuration.nix", r)

# ── S4 ──────────────────────────────────────────────────────────────────────
print("\nS4: Rückabwicklung stellt Rechte und Links wieder her")
setup_cfg()
before = snapshot()
os.chmod(ext / "aussen.nix", 0o000)     # the old code read it - now it must not
real_write = atomic.write_bytes
calls = {"n": 0}
def write_fails_second(path, data, mode=None):
    calls["n"] += 1
    if calls["n"] == 2:
        raise OSError("Platte voll")
    return real_write(path, data, mode=mode)
atomic.write_bytes = write_fails_second
try:
    importer.replace_nix_tree(cfg, {"configuration.nix": b"{ }\n", "modules/b.nix": b"{ }\n",
                                    "modules/c.nix": b"{ }\n"})
    check("Import scheitert", False)
except importer.ImportWriteError as exc:
    check("Import scheitert, Rückabwicklung meldet restored", exc.restored, exc)
except importer.ImportReadError as exc:
    check("Datei außerhalb nicht gelesen", False, exc)
finally:
    atomic.write_bytes = real_write
    os.chmod(ext / "aussen.nix", 0o644)
after = snapshot()
check("Modus 0600 wiederhergestellt", after.get("modules/geheim.nix", ("",))[-1] == 0o600,
      after.get("modules/geheim.nix"))
check("interner Link wieder ein Link", after.get("modules/link.nix") == ("link", "a.nix"), after.get("modules/link.nix"))
check("Link nach außen wieder ein Link", after.get("modules/aussen.nix") == ("link", str(ext / "aussen.nix")),
      after.get("modules/aussen.nix"))
check("Datei außerhalb unverändert", (ext / "aussen.nix").read_text() == "{ aussen = true; }\n")
check("Ausgangsstand vollständig", after == before, set(after.items()) ^ set(before.items()))

# ── W2 ──────────────────────────────────────────────────────────────────────
print("\nW2: Wahl Panel/Text zuerst")
setup_cfg()
before = snapshot()
real_save = config_manager.save_config_settings
def save_fails(*a, **k):
    raise OSError("schreibgeschützt")
config_manager.save_config_settings = save_fails
s, r = zip_post("/api/import/zip/apply", GOOD, confirmed="true", edit_mode="r")
config_manager.save_config_settings = real_save
check("Wahl nicht speicherbar: 500 ERR_EDIT_MODE_SAVE", s == 500 and r.get("error") == "ERR_EDIT_MODE_SAVE", (s, r))
# Since the Codex re-check of 2026-11-26 the backups come first, before
# the choice is stored - they must hold the mode as it was. So a backup
# ZIP may be there; the config itself is untouched.
def _no_backup(snap):
    return {k: v for k, v in snap.items() if not k.startswith("nixos-config-")}
check("… nichts importiert", _no_backup(snapshot()) == _no_backup(before))

def nix_write_fails(p, d, mode=None):
    if str(p).endswith(".nix"):           # config.json goes the same way
        raise OSError("Platte voll")
    return real_write(p, d, mode=mode)
atomic.write_bytes = nix_write_fails
s, r = zip_post("/api/import/zip/apply", GOOD, confirmed="true", edit_mode="r")
atomic.write_bytes = real_write
check("Import scheitert danach", s == 500 and r.get("error") in ("ERR_IMPORT_WRITE", "ERR_IMPORT_PARTIAL"), (s, r))
check("… Wahl auf den alten Wert zurückgesetzt", panel_default() == "p", panel_default())

for z in cfg.glob("nixos-config-*.zip"):
    z.unlink()
s, r = zip_post("/api/import/zip/apply", GOOD, confirmed="true", edit_mode="r")
check("Gegenprobe: Textimport gelingt", s == 200 and r.get("success") and r.get("edit_mode") == "r", (s, r))
check("… Wahl gespeichert", panel_default() == "r")
check("… Datei byte für byte übernommen",
      (cfg / "configuration.nix").read_bytes() == b"{ ... }: { networking.hostName = \"neu\"; }\n")

print()
if fails:
    print(f"{len(fails)} FEHLER")
    sys.exit(1)
print("Alles grün.")
