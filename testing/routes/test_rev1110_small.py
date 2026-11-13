#!/usr/bin/env python3
"""Revision 2026-11-10, Paket 3: kleinere Fehler und Sicherheit.

  B42  Formatnummer: `#f3` nur, wenn wirklich ein Nix-Brick deaktiviert ist.
       Der Generator stempelte immer `#f3`, eine Brick-Aktion danach `#f2` –
       dieselbe Datei, zwei Nummern, und jede gespeicherte Datei sperrte
       eine 0.14.0 aus.
  B43  Das Rebuild-Log wird schon beim Sammeln begrenzt, nicht erst beim
       Schreiben (Lauf vom 2026-09-12: 2,1 GB im Speicher von NiCo).
  B44  hm_update_hash kennt das Suffix `#f<N>`.
  B45  import-hardware überschreibt kein vorhandenes Backup.
  B46  git init nimmt keine übergroßen Dateien auf.
  B47  Löschen und Umbenennen folgen keinem Symlink; versteckte Pfade
       werden abgelehnt.
  B49  Generator: Snapper-Limits, Kanal, Hostname; Paketname aus der Route.
  S11  Fehlertexte von nix gehen maskiert in die Oberfläche.
  S12  Verzeichnisnamen in config.json werden geprüft, falsche Typen in den
       Settings bringen NiCo nicht zum Absturz, Host-Routen nehmen kein `..`.
  S13  GETs mit Wirkung verlangen das Token; ohne „Mit Remote abgleichen"
       wird nicht gefetcht.
  T16  Zwei tote Endpunkte sind weg.

Lauf: nix-shell shell.nix --run "python testing/routes/test_rev1110_small.py"
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

xdg = Path(tempfile.mkdtemp(prefix="nico-xdg-"))
os.environ["XDG_CONFIG_HOME"] = str(xdg)
os.environ["XDG_CACHE_HOME"] = str(xdg / "cache")
(xdg / "nico").mkdir(parents=True)
SETTINGS = xdg / "nico" / "settings.json"

from nico import brix, config_manager, core, generator, git_manager, hm_generator, server  # noqa: E402
from nico.routes import rebuild as rb                                                      # noqa: E402

fails = []
def check(label, cond, got=None):
    print(("  OK   " if cond else "  FAIL ") + label + ("" if cond or got is None else f"  (war: {got})"))
    if not cond:
        fails.append(label)

def use(cfg: Path, **extra):
    SETTINGS.write_text(json.dumps({"nixos_config_dir": str(cfg), "language": "de",
                                    "git_sync": False, **extra}), encoding="utf-8")

def new_cfg(hostname="kiste") -> Path:
    cfg = Path(tempfile.mkdtemp(prefix="nico-cfg-"))
    data = config_manager.get_defaults()
    data["hostname"] = hostname
    (cfg / "configuration.nix").write_text(generator.generate_configuration_nix(data), encoding="utf-8")
    return cfg

def git(cfg, *args):
    return subprocess.run(["git", *args], cwd=cfg, capture_output=True, text=True)

HAVE_NIX = bool(shutil.which("nix-instantiate"))
def valid(text):
    if not HAVE_NIX:
        return True, ""
    f = Path(tempfile.mkdtemp(prefix="nico-parse-")) / "t.nix"
    f.write_text(text, encoding="utf-8")
    p = subprocess.run(["nix-instantiate", "--parse", str(f)], capture_output=True, text=True)
    return p.returncode == 0, p.stderr[-300:]

cfg = new_cfg()
use(cfg)
app = server.create_app()
app.config["TESTING"] = True
c = app.test_client()
m = re.search(r'name="csrf-token" content="([^"]+)"', c.get("/").get_data(as_text=True))
H = {"X-CSRF-Token": m.group(1) if m else "", "Content-Type": "application/json"}

print("B42 Formatnummer")
data = config_manager.get_defaults()
data["brick_blocks"] = {"eins": {"section": "HEAD", "order": 1,
                                 "text": brix.format_brick("HEAD", 1, "eins", "  a.b = 1;")}}
gen = generator.generate_configuration_nix(data)
check("Generator stempelt ohne deaktivierten Brick #f2", core.get_file_format(gen) == 2,
      gen.splitlines()[1])
check("der Hash stimmt", generator.check_version_hash(gen) == "ok")
restamped = core.set_type_in_content(gen, "co")
check("eine Brick-Aktion danach bleibt bei derselben Nummer",
      core.get_file_format(restamped) == core.get_file_format(gen),
      (gen.splitlines()[1], restamped.splitlines()[1]))
inactive = brix.set_brick_inactive(gen, "eins", True) if hasattr(brix, "set_brick_inactive") else None
if inactive is None:
    # Build the inactive form by hand: flag in the marker, body commented out.
    inactive = gen.replace("eins>", "eins inactive>", 1).replace("  a.b = 1;", "  # a.b = 1;", 1)
data2 = dict(data)
data2["brick_blocks"] = brix.extract_brick_blocks(inactive)
gen3 = generator.generate_configuration_nix(data2)
check("mit deaktiviertem Brick #f3", core.get_file_format(gen3) == 3, gen3.splitlines()[1])
hm = hm_generator.generate_home_nix({"username": "m", "home_dir": "/home/m"})
check("Home-Manager-Datei ohne deaktivierten Brick: #f2", core.get_file_format(hm) == 2, hm.splitlines()[1])
fl = generator.generate_flake_nix({"hostname": "kiste", "flake_hosts": [{"name": "kiste"}]})
check("flake.nix ohne deaktivierten Brick: #f2", core.get_file_format(fl) == 2, fl.splitlines()[1])

print("\nB43 Log-Puffer")
Buf = getattr(rb, "_LogBuffer", None)
check("es gibt _LogBuffer", Buf is not None)
if Buf is not None:
    buf = Buf(max_bytes=1000)
    for i in range(500):
        buf.append(f"zeile {i:04d} " + "x" * 20)
    size = sum(len(l.encode()) + 1 for l in buf)
    check("der Puffer bleibt unter der Grenze", size <= 1000, size)
    check("er behält das Ende", list(buf)[-1].startswith("zeile 0499"), list(buf)[-1][:12])
    check("er zählt, was er verworfen hat", buf.dropped == 500 - len(buf) and buf.dropped > 0,
          (buf.dropped, len(buf)))
    log = Path(tempfile.mkdtemp(prefix="nico-log-")) / "r.log"
    check("Log wird geschrieben", rb._write_rebuild_log(log, buf))
    head = log.read_text(encoding="utf-8").splitlines()[0]
    check("die Kopfzeile nennt die verworfenen Zeilen", str(buf.dropped) in head and "Truncated" in head, head)
    one = Buf(max_bytes=10)
    one.append("eine sehr lange einzelne Zeile")
    check("eine einzelne zu lange Zeile bleibt erhalten", len(one) == 1)
src = (ROOT / "nico" / "routes" / "rebuild.py").read_text(encoding="utf-8")
check("der Rebuild sammelt in den Puffer", "log_lines   = _LogBuffer()" in src or "log_lines = _LogBuffer()" in src)

print("\nB44 hm_update_hash")
hm_changed = hm.replace('"/home/m"', '"/home/martin"')
check("Ausgangslage: geänderte Datei gilt als geändert",
      generator.check_version_hash(hm_changed) == "modified")
check("nach hm_update_hash stimmt der Hash wieder",
      generator.check_version_hash(core.hm_update_hash(hm_changed)) == "ok")
check("die Formatnummer bleibt stehen",
      core.get_file_format(core.hm_update_hash(hm_changed)) == core.get_file_format(hm))

print("\nB45 import-hardware")
cfg = new_cfg(); use(cfg)
src_hw = Path(tempfile.mkdtemp(prefix="nico-hw-")) / "hardware-configuration.nix"
(cfg / "hardware-configuration.nix").write_text("{ }: { } # zweite\n", encoding="utf-8")
(cfg / "hardware-configuration.nix.bak").write_text("{ }: { } # erste\n", encoding="utf-8")
src_hw.write_text("{ }: { } # dritte\n", encoding="utf-8")
r = c.post("/api/files/import-hardware", headers=H,
           data=json.dumps({"target_dir": "", "source_path": str(src_hw)}))
check("Import gelingt", (r.get_json() or {}).get("success") is True, (r.status_code, r.get_json()))
texts = sorted(p.read_text(encoding="utf-8") for p in cfg.glob("hardware-configuration.nix*"))
check("alle drei Stände sind noch da",
      [t.split("#")[1].strip() for t in texts] == ["dritte", "erste", "zweite"], [p.name for p in cfg.glob("hardware*")])
check("das alte Backup ist unverändert",
      "erste" in (cfg / "hardware-configuration.nix.bak").read_text(encoding="utf-8"))

print("\nB46 git init")
cfg = new_cfg()
big = cfg / "abbild.iso"
with open(big, "wb") as fh:
    fh.truncate(60 * 1024 * 1024)
ok, msg = git_manager.init_repo(str(cfg))
tracked = git(cfg, "ls-files").stdout.split()
check("init gelingt", ok, msg)
check("die 60-MB-Datei ist nicht aufgenommen", "abbild.iso" not in tracked, tracked)
check("die Config schon", "configuration.nix" in tracked, tracked)
check("die Datei selbst bleibt liegen", big.exists())

print("\nB47 Löschen und Umbenennen")
cfg = new_cfg(); use(cfg)
outside = Path(tempfile.mkdtemp(prefix="nico-out-"))
(outside / "wichtig.nix").write_text("{ }\n", encoding="utf-8")
(cfg / "sub").mkdir()
(cfg / "sub" / "ziel.nix").write_text("{ }\n", encoding="utf-8")
(cfg / "link.nix").symlink_to(cfg / "sub" / "ziel.nix")
(cfg / "linkdir").symlink_to(cfg / "sub", target_is_directory=True)
(cfg / "raus.nix").symlink_to(outside / "wichtig.nix")
r = c.post("/api/files/delete", headers=H, data=json.dumps({"path": "link.nix"}))
check("Symlink löschen gelingt", (r.get_json() or {}).get("success") is True, (r.status_code, r.get_json()))
check("der Link ist weg", not os.path.lexists(cfg / "link.nix"))
check("das Ziel ist noch da", (cfg / "sub" / "ziel.nix").is_file())
r = c.post("/api/files/delete", headers=H, data=json.dumps({"path": "linkdir"}))
check("Link auf ein Verzeichnis: nur der Link verschwindet",
      not os.path.lexists(cfg / "linkdir") and (cfg / "sub" / "ziel.nix").is_file(),
      (r.status_code, r.get_json()))
r = c.post("/api/files/delete", headers=H, data=json.dumps({"path": "raus.nix"}))
check("Link nach außen: der Link verschwindet, die fremde Datei bleibt",
      not os.path.lexists(cfg / "raus.nix") and (outside / "wichtig.nix").is_file(),
      (r.status_code, r.get_json()))
(cfg / "link2.nix").symlink_to(cfg / "sub" / "ziel.nix")
r = c.post("/api/files/rename", headers=H, data=json.dumps({"path": "link2.nix", "new_name": "neu.nix"}))
check("Umbenennen trifft den Link, nicht das Ziel",
      (cfg / "neu.nix").is_symlink() and (cfg / "sub" / "ziel.nix").is_file()
      and not os.path.lexists(cfg / "link2.nix"), (r.status_code, r.get_json()))
(cfg / ".git").mkdir()
(cfg / ".git" / "config").write_text("x\n", encoding="utf-8")
for rel in (".git", ".git/config", "sub/../.git", "."):
    r = c.post("/api/files/delete", headers=H, data=json.dumps({"path": rel}))
    check(f"Löschen von „{rel}“ wird abgelehnt", r.status_code in (400, 403), (r.status_code, r.get_json()))
check(".git ist unberührt", (cfg / ".git" / "config").is_file())
r = c.post("/api/files/rename", headers=H, data=json.dumps({"path": ".git", "new_name": "x"}))
check("Umbenennen von .git wird abgelehnt", r.status_code in (400, 403) and (cfg / ".git").is_dir())
r = c.post("/api/files/delete", headers=H, data=json.dumps({"path": "../" + outside.name + "/wichtig.nix"}))
check("Pfad nach außen wird abgelehnt", r.status_code in (400, 403) and (outside / "wichtig.nix").is_file())
r = c.post("/api/files/delete", headers=H, data=json.dumps({"path": "sub/ziel.nix"}))
check("eine normale Datei lässt sich weiter löschen",
      (r.get_json() or {}).get("success") is True and not (cfg / "sub" / "ziel.nix").exists())

print("\nB49 Generator ohne Prüfung")
d = config_manager.get_defaults()
d.update({"snapper_enable": True, "snapper_configs": [
    {"name": "root", "mountpoint": "/", "hourly": "", "daily": None, "weekly": "x", "monthly": "3", "yearly": 2}]})
out = generator.generate_configuration_nix(d)
check("Snapper-Limits: leere und falsche Werte werden zur Vorgabe",
      "TIMELINE_LIMIT_HOURLY = 5;" in out and "TIMELINE_LIMIT_DAILY = 7;" in out
      and "TIMELINE_LIMIT_WEEKLY = 0;" in out and "TIMELINE_LIMIT_MONTHLY = 3;" in out
      and "TIMELINE_LIMIT_YEARLY = 2;" in out,
      [l.strip() for l in out.splitlines() if "TIMELINE_LIMIT" in l])
ok, err = valid(out)
check("Snapper-Limits: Ergebnis ist gültiges Nix", ok, err)
# A name Nix only takes quoted (leading digit), and a channel with quotes in it
fl = generator.generate_flake_nix({"hostname": "9lives", "flake_nixpkgs_channel": 'x"; evil = "'})
ok, err = valid(fl)
check("Hostname mit Ziffer vorn und Kanal mit Anführungszeichen: gültiges Nix", ok, err)
check("der Hostname steht in Anführungszeichen", '"9lives" = nixpkgs.lib.nixosSystem' in fl)
check("der Kanal kann die Zeichenkette nicht verlassen",
      'nixpkgs/x\\"; evil = \\"";' in fl, [l for l in fl.splitlines() if "nixpkgs.url" in l])
cfg = new_cfg(); use(cfg)
before = (cfg / "configuration.nix").read_text(encoding="utf-8")
for bad in ("git; evil = 1", "(import ./x.nix)", "a b", "${x}", ""):
    r = c.post("/api/packages/add", headers=H, data=json.dumps({"attr": bad}))
    check(f"Paketname „{bad}“ wird abgelehnt", r.status_code == 400, (r.status_code, r.get_json()))
check("und nichts wurde geschrieben", (cfg / "configuration.nix").read_text(encoding="utf-8") == before)
r = c.post("/api/packages/add", headers=H, data=json.dumps({"attr": "kdePackages.kate"}))
check("ein normaler Paketname geht weiter", (r.get_json() or {}).get("success") is True, (r.status_code, r.get_json()))

print("\nS11 Fehlertext maskiert")
js = (ROOT / "nico" / "static" / "js" / "rebuild.js").read_text(encoding="utf-8")
check("erste Fehlerzeile geht durch escHtml", "escHtml(firstErrorLine.substring(0, 80))" in js)
check("Fehlertext des error-Ereignisses geht durch escHtml",
      "niIcon('x-circle') + ' ' + escHtml(errText)" in js)
check("keine rohe Verkettung mehr", "': ' + firstErrorLine.substring" not in js
      and "niIcon('x-circle') + ' ' + errText)" not in js)

print("\nS12 Verzeichnisnamen und Typen")
cfg = new_cfg(); use(cfg)
for bad in ("../x", "/etc", "a/../../b", ".git", "a/.b", "", 5, None, ["hosts"], "a\\b", "a b"):
    r = c.patch("/api/config/settings", headers=H, data=json.dumps({"hosts_dir": bad}))
    check(f"hosts_dir = {bad!r} wird abgelehnt", r.status_code == 400, (r.status_code, r.get_json()))
check("und nichts davon steht in config.json",
      config_manager.load_config_settings(str(cfg)).get("hosts_dir") == "hosts",
      config_manager.load_config_settings(str(cfg)).get("hosts_dir"))
r = c.patch("/api/config/settings", headers=H, data=json.dumps({"hosts_dir": "rechner", "hm_dir": "nutzer/home"}))
st = config_manager.load_config_settings(str(cfg))
check("gültige Namen gehen weiter", r.status_code == 200 and st.get("hosts_dir") == "rechner"
      and st.get("hm_dir") == "nutzer/home", (r.status_code, st))
# A config.json that travelled with a cloned config
(cfg / "config.json").write_text(json.dumps({"hosts_dir": "../draussen", "modules_dir": 5,
                                             "hm_dir": "/tmp/x"}), encoding="utf-8")
st = config_manager.load_config_settings(str(cfg))
check("eine mitgebrachte config.json mit `..` wird beim Lesen entschärft",
      (st.get("hosts_dir"), st.get("modules_dir"), st.get("hm_dir")) == ("hosts", "modules", "home"), st)
r = c.get("/api/hosts")
check("und die Host-Liste antwortet", r.status_code == 200, r.status_code)
(cfg / "config.json").unlink()
for bad in ("..", ".versteckt", "a b"):
    r = c.post(f"/api/host/{bad}/config", headers=H,
               data=json.dumps({"_co_ready": True, "_co_path": f"hosts/{bad}/default.nix"}))
    check(f"Host-Name „{bad}“ wird abgelehnt", r.status_code == 400, (r.status_code, r.get_json()))
    r = c.post(f"/api/host/{bad}/write", headers=H, data="{}")
    check(f"Host-Name „{bad}“ wird auch beim Schreiben abgelehnt", r.status_code == 400, r.status_code)
check("nichts außerhalb von hosts/ entstanden", not (cfg / "default.nix").exists())
use(cfg, theme=5, language=["de"])
r = c.get("/")
check("theme: 5 in den App-Settings: Startseite antwortet", r.status_code == 200, r.status_code)
check("falsche Typen werden zu den Vorgaben",
      isinstance(config_manager.get_app_settings().get("theme"), str)
      and config_manager.get_app_settings().get("language") == "de", config_manager.get_app_settings())
use(cfg)
c.patch("/api/app/settings", headers=H, data=json.dumps({"theme": 5, "language": {"a": 1}}))
st = config_manager.get_app_settings()
check("und lassen sich nicht hineinschreiben",
      isinstance(st.get("theme"), str) and st.get("language") == "de", st)

print("\nS13 GETs mit Wirkung")
cfg = new_cfg(); use(cfg)
for url in ("/api/git/start-check", "/api/git/remote-status",
            "/api/packages/search?q=firefox", "/api/rebuild/default-host"):
    r = c.get(url)
    check(f"{url.split('?')[0]} ohne Token: abgelehnt", r.status_code == 403, r.status_code)
r = c.get("/api/git/remote-status", headers=H)
check("mit Token antwortet die Route", r.status_code == 200 and "has_git" in (r.get_json() or {}), r.status_code)
# A repository with a remote that would have to be fetched
remote = Path(tempfile.mkdtemp(prefix="nico-remote-"))
git(remote, "init", "-q", "--bare")
git(cfg, "init", "-q"); git(cfg, "add", "-A")
git(cfg, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "start")
git(cfg, "remote", "add", "origin", str(remote))
calls = []
orig_run = git_manager._run
def spy(args, *a, **kw):
    calls.append(list(args))
    return orig_run(args, *a, **kw)
git_manager._run = spy
try:
    use(cfg, git_sync=False)
    rs = c.get("/api/git/remote-status", headers=H).get_json() or {}
    sc = c.get("/api/git/start-check", headers=H).get_json() or {}
    off = [a for a in calls if a and a[0] == "fetch"]
    calls.clear()
    use(cfg, git_sync=True)
    c.get("/api/git/remote-status", headers=H)
    on = [a for a in calls if a and a[0] == "fetch"]
finally:
    git_manager._run = orig_run
check("ohne „Mit Remote abgleichen“: kein fetch", off == [], off)
check("die Antwort nennt Repository und Remote trotzdem",
      rs.get("has_git") is True and rs.get("has_remote") is True, rs)
check("start-check antwortet ohne Fehlerzustand", sc.get("has_git") is True and sc.get("state") != "error", sc)
check("mit eingeschaltetem Abgleich wird gefetcht", len(on) == 1, on)

print("\nT16 tote Endpunkte")
r = c.post("/api/import/from-path", headers=H, data=json.dumps({"path": "configuration.nix"}))
check("/api/import/from-path gibt es nicht mehr", r.status_code in (404, 405), r.status_code)
r = c.post("/api/packages/git/toggle", headers=H, data="{}")
check("/api/packages/<attr>/toggle gibt es nicht mehr", r.status_code in (404, 405), r.status_code)
jsall = "".join(p.read_text(encoding="utf-8") for p in (ROOT / "nico" / "static" / "js").glob("*.js"))
check("die Oberfläche ruft keinen von beiden", "/api/import/from-path" not in jsall and "/toggle" not in jsall)

print()
if fails:
    print(f"{len(fails)} FEHLER:", *fails, sep="\n  ")
    sys.exit(1)
print("Alle Prüfungen bestanden.")
