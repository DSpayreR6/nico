#!/usr/bin/env python3
"""Nachprüfung 2026-11-26 (Codex), Paket A: Datenverlust und Rechte.

F3-Rest – Zwei Tabs im Textmodus: Hatte Tab A gespeichert, galt sein Stand
als NiCos letzter Schreibstand, und Tab B überschrieb ihn mit älterer Basis
still. Jetzt zählt nur die Basis des Tabs; Vergleich und Schreiben halten
die Sperre der Datei, zwei gleichzeitige Anfragen mit derselben Basis
können nicht beide durchkommen.

S4 – Ein erfolgreicher ZIP-Import schrieb ersetzte Dateien mit 0644: ein
Skript verlor das Ausführungsrecht, eine 0600-Datei wurde lesbar. Jetzt
behält jede ersetzte Datei ihren Modus, gesetzt vor dem Austausch.

W2 – Brachte das ZIP eine config.json mit, ersetzte deren panel_default die
eben gewählte Bearbeitungsart; die Antwort meldete trotzdem Textmodus. Und
die Sicherungen vor dem Import enthielten schon die neue Wahl.

Lauf: nix-shell shell.nix --run "python testing/routes/test_codex_1126_paket_a.py"
"""
import hashlib, io, json, os, re, stat, subprocess, sys, tempfile, threading, time, zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

xdg = Path(tempfile.mkdtemp(prefix="nico-xdg-"))
os.environ["XDG_CONFIG_HOME"] = str(xdg)
cfg = Path(tempfile.mkdtemp(prefix="nico-cfg-"))
(xdg / "nico").mkdir(parents=True)
(xdg / "nico" / "settings.json").write_text(
    json.dumps({"nixos_config_dir": str(cfg), "language": "de", "git_sync": False}),
    encoding="utf-8")

from nico import atomic, git_manager, importer, outside, server  # noqa: E402

fails = []
def check(label, cond, got=None):
    print(("  OK   " if cond else "  FAIL ") + label + ("" if cond or got is None else f"  (war: {got})"))
    if not cond: fails.append(label)

app = server.create_app()
client = app.test_client()
page = client.get("/").get_data(as_text=True)
csrf = re.search(r'<meta name="csrf-token" content="([^"]+)"', page).group(1)
H = {"X-CSRF-Token": csrf, "Content-Type": "application/json"}

def post(url, body, c=None):
    r = (c or client).post(url, headers=H, data=json.dumps(body))
    return r.status_code, (r.get_json(silent=True) or {})

def git(*args):
    return subprocess.run(["git", *args], cwd=cfg, capture_output=True, text=True).stdout

def mode(rel):
    return stat.S_IMODE(os.stat(cfg / rel).st_mode)

def sha(rel):
    return hashlib.sha256((cfg / rel).read_bytes()).hexdigest()

def make_zip(entries):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, data in entries.items():
            z.writestr(name, data)
    return buf.getvalue()

def zip_post(data, **form):
    fields = {"file": (io.BytesIO(data), "x.zip"), **form}
    r = client.post("/api/import/zip/apply", headers={"X-CSRF-Token": csrf},
                    data=fields, content_type="multipart/form-data")
    return r.status_code, (r.get_json(silent=True) or {})

(cfg / "modules").mkdir(parents=True)
(cfg / "config.json").write_text(json.dumps({"hosts_dir": "hosts"}), encoding="utf-8")
MOD = "modules/m.nix"
(cfg / MOD).write_text("{ ... }:\n{\n  # erste Fassung\n}\n", encoding="utf-8")
(cfg / "configuration.nix").write_text("{ ... }: { }\n", encoding="utf-8")
ok, _ = git_manager.init_repo(str(cfg))
check("Git eingerichtet", ok)
git_manager.auto_commit(str(cfg), label="start")

# ── F3: zwei Tabs nacheinander ──────────────────────────────────────────────
print("F3: Tab A speichert, danach Tab B mit älterer Basis")
base = client.get(f"/api/file?path={MOD}", headers=H).get_json()["_base"]
base_a = base_b = base
text_a = "{ ... }:\n{\n  # Tab A\n}\n"
text_b = "{ ... }:\n{\n  # Tab B\n}\n"
s, r = post("/api/file", {"files": [{"path": MOD, "content": text_a, "base": base_a}]})
check("Tab A speichert", s == 200 and r.get("success"), r)
s, r = post("/api/file", {"files": [{"path": MOD, "content": text_b, "base": base_b}]})
check("Tab B bekommt 409 ERR_CHANGED_OUTSIDE",
      s == 409 and r.get("error") == "ERR_CHANGED_OUTSIDE", (s, r))
check("Auf Platte steht weiter der Text von Tab A", (cfg / MOD).read_text() == text_a)

s, r = post("/api/file", {"files": [{"path": MOD, "content": text_b, "base": base_b}],
                          "overwrite": True})
check("Ausdrückliches Überschreiben schreibt Tab B", s == 200 and r.get("success")
      and (cfg / MOD).read_text() == text_b, r)
check("… nach einem Sicherungspunkt mit dem Stand von Tab A",
      "Tab A" in git("show", "HEAD~1:" + MOD) or "Tab A" in git("log", "-p", "-3", "--", MOD))
new_base = (r.get("bases") or {}).get(MOD)
s, r = post("/api/file", {"files": [{"path": MOD, "content": text_b + "# weiter\n",
                                     "base": new_base}]})
check("Mit erneuerter Basis speichert Tab B normal weiter", s == 200 and r.get("success"), r)

# ── F3: zwei gleichzeitige Anfragen mit derselben Basis ─────────────────────
print("F3: zwei gleichzeitige Speicheranfragen mit derselben Basis")
base = client.get(f"/api/file?path={MOD}", headers=H).get_json()["_base"]
orig_differs = outside.differs_from_base
def slow_differs(path, b):
    # Widen the gap between comparison and write: without the file lock
    # both requests pass the comparison before either one writes.
    result = orig_differs(path, b)
    time.sleep(0.4)
    return result
outside.differs_from_base = slow_differs
results = {}
def save(name):
    c = app.test_client()
    results[name] = post("/api/file", {"files": [{"path": MOD, "content": f"# {name}\n",
                                                  "base": base}]}, c)
threads = [threading.Thread(target=save, args=(n,)) for n in ("eins", "zwei")]
for t in threads: t.start()
for t in threads: t.join()
outside.differs_from_base = orig_differs
codes = sorted(v[0] for v in results.values())
check("Genau eine Anfrage schreibt, die andere bekommt 409", codes == [200, 409], results)
winner = next((n for n, v in results.items() if v[0] == 200), None)
check("Auf Platte steht der Text der erfolgreichen Anfrage",
      winner is not None and (cfg / MOD).read_text() == f"# {winner}\n")

print("F3: atomic.write_bytes wartet auf die Sperre der Datei")
done = threading.Event()
lock = outside.path_lock(cfg / MOD)
lock.acquire()
t = threading.Thread(target=lambda: (atomic.write_text(cfg / MOD, "# wartend\n"), done.set()))
t.start()
time.sleep(0.2)
check("Schreiben wartet, solange ein Vergleich die Sperre hält", not done.is_set())
lock.release()
t.join(2)
check("… und schreibt danach", done.is_set() and (cfg / MOD).read_text() == "# wartend\n")

# ── S4 ──────────────────────────────────────────────────────────────────────
print("S4: ZIP-Import behält die Rechte ersetzter Dateien")
(cfg / "modules/script.nix").write_text("# alt\n", encoding="utf-8")
os.chmod(cfg / "modules/script.nix", 0o755)
(cfg / "modules/secret.nix").write_text("# alt geheim\n", encoding="utf-8")
os.chmod(cfg / "modules/secret.nix", 0o600)
git_manager.auto_commit(str(cfg), label="vor S4")

seen_modes = []
orig_replace = os.replace
def spy_replace(src, dst, *a, **kw):
    if str(dst).endswith(("script.nix", "secret.nix")):
        seen_modes.append((Path(dst).name, stat.S_IMODE(os.stat(src).st_mode)))
    return orig_replace(src, dst, *a, **kw)
os.replace = spy_replace
Z = make_zip({"configuration.nix": b"{ ... }: { }\n",
              "modules/script.nix": b"# neu\n",
              "modules/secret.nix": b"# neu geheim\n"})
s, r = zip_post(Z, confirmed="true")
os.replace = orig_replace
check("Import gelingt", s == 200 and r.get("success"), (s, r))
check("Skript hat den neuen Inhalt und weiter 0755",
      (cfg / "modules/script.nix").read_text() == "# neu\n" and mode("modules/script.nix") == 0o755,
      oct(mode("modules/script.nix")))
check("Geheime Datei hat den neuen Inhalt und weiter 0600",
      (cfg / "modules/secret.nix").read_text() == "# neu geheim\n" and mode("modules/secret.nix") == 0o600,
      oct(mode("modules/secret.nix")))
check("Der Modus stand schon vor dem Austausch auf der Ersatzdatei",
      ("secret.nix", 0o600) in seen_modes and ("script.nix", 0o755) in seen_modes, seen_modes)
check("Neue Datei ohne Vorgänger bekommt 0644", mode("configuration.nix") == 0o644,
      oct(mode("configuration.nix")))

# ── W2 ──────────────────────────────────────────────────────────────────────
def settings():
    return json.loads((cfg / "config.json").read_text())

def reset_mode(value):
    s = settings(); s["panel_default"] = value
    (cfg / "config.json").write_text(json.dumps(s), encoding="utf-8")

print("W2: NiCo-Export mit Panel-Vorgabe im Textmodus importieren")
reset_mode("p")
EXPORT = make_zip({"configuration.nix": b"{ ... }: { }\n",
                   "modules/m.nix": b"# aus dem Export\n",
                   "config.json": json.dumps({"hosts_dir": "rechner",
                                              "panel_default": "p"}).encode()})
zips_before = set(cfg.glob("nixos-config-*.zip"))
s, r = zip_post(EXPORT, confirmed="true", edit_mode="r")
check("Import gelingt und meldet Textmodus", s == 200 and r.get("edit_mode") == "r", (s, r))
check("config.json auf Platte: panel_default = r", settings().get("panel_default") == "r", settings())
check("… die übrigen Werte des Exports sind übernommen", settings().get("hosts_dir") == "rechner",
      settings())
new_zips = set(cfg.glob("nixos-config-*.zip")) - zips_before
backup_mode = None
if len(new_zips) == 1:
    with zipfile.ZipFile(next(iter(new_zips))) as z:
        backup_mode = json.loads(z.read("config.json")).get("panel_default")
check("Sicherungs-ZIP enthält den vorherigen Modus (p)", backup_mode == "p", backup_mode)
commit_cfg = git("show", "HEAD:config.json") if "backup before ZIP import" in git("log", "-1", "--format=%s") else ""
hashes = git("log", "--format=%H %s").splitlines()
backup_commit = next((h.split()[0] for h in hashes if "backup before ZIP import" in h), None)
committed_mode = None
if backup_commit:
    try:
        committed_mode = json.loads(git("show", f"{backup_commit}:config.json")).get("panel_default")
    except ValueError:
        pass
check("Sicherungspunkt vor dem Import enthält den vorherigen Modus (p)", committed_mode == "p",
      committed_mode)

print("W2: Fehler im letzten Schreibschritt – alles zurück, auch der Modus")
reset_mode("p")
(cfg / "config.json").write_text(json.dumps({"hosts_dir": "hosts", "panel_default": "p"}),
                                 encoding="utf-8")
(cfg / "modules/secret.nix").write_text("# alt geheim\n", encoding="utf-8")
os.chmod(cfg / "modules/secret.nix", 0o600)
cfg_before = (cfg / "config.json").read_bytes()
conf_before = (cfg / "configuration.nix").read_bytes()
mod_before = (cfg / MOD).read_bytes()
secret_before = (sha("modules/secret.nix"), mode("modules/secret.nix"))
FAILING = make_zip({"config.json": json.dumps({"hosts_dir": "x", "panel_default": "p"}).encode(),
                    "configuration.nix": b"{ ... }: { networking.hostName = \"neu\"; }\n",
                    "modules/m.nix": b"# neu\n",
                    "modules/secret.nix": b"# neu\n",
                    "modules/zuletzt.nix": b"# letzter Schritt\n"})
orig_write = atomic.write_bytes
written_before_fail = []
def failing_write(path, data, mode=None):
    if str(path).endswith("zuletzt.nix"):
        raise OSError("simulierter Schreibfehler")
    written_before_fail.append(Path(path).name)
    return orig_write(path, data, mode=mode)
importer.atomic.write_bytes = failing_write
s, r = zip_post(FAILING, confirmed="true", edit_mode="r")
importer.atomic.write_bytes = orig_write
check("Import meldet Fehler, Ausgangsstand wiederhergestellt",
      s == 500 and r.get("error") == "ERR_IMPORT_WRITE", (s, r))
check("Vor dem Fehler waren config.json und die Dateien schon übernommen",
      {"config.json", "configuration.nix", "m.nix"} <= set(written_before_fail), written_before_fail)
check("config.json bytegleich wie vorher (Modus p)", (cfg / "config.json").read_bytes() == cfg_before,
      (cfg / "config.json").read_text())
check("configuration.nix und Modul bytegleich wie vorher",
      (cfg / "configuration.nix").read_bytes() == conf_before and (cfg / MOD).read_bytes() == mod_before)
check("Geheime Datei: Inhalt und 0600 wie vorher",
      (sha("modules/secret.nix"), mode("modules/secret.nix")) == secret_before)
check("Keine halbe neue Datei übrig", not (cfg / "modules/zuletzt.nix").exists())

print("W2: ZIP ohne config.json – Wahl wird wie bisher gespeichert")
reset_mode("p")
s, r = zip_post(make_zip({"configuration.nix": b"{ ... }: { }\n"}), confirmed="true", edit_mode="r")
check("Import gelingt, panel_default = r", s == 200 and settings().get("panel_default") == "r",
      (s, r, settings()))

print("W2: kaputte config.json im ZIP – nichts geändert")
reset_mode("p")
cfg_before = (cfg / "config.json").read_bytes()
conf_before = (cfg / "configuration.nix").read_bytes()
s, r = zip_post(make_zip({"configuration.nix": b"# neu\n", "config.json": b"{kaputt"}),
                confirmed="true", edit_mode="r")
check("Abgelehnt mit ERR_IMPORT_READ", s == 400 and r.get("error") == "ERR_IMPORT_READ", (s, r))
check("config.json und configuration.nix unverändert",
      (cfg / "config.json").read_bytes() == cfg_before
      and (cfg / "configuration.nix").read_bytes() == conf_before)

print("W2: ohne Moduswahl gilt die config.json des Exports wie bisher")
reset_mode("r")
s, r = zip_post(EXPORT, confirmed="true")
check("panel_default aus dem Export (p)", s == 200 and settings().get("panel_default") == "p",
      (s, settings()))

print()
print("ALLE TESTS BESTANDEN" if not fails else f"{len(fails)} FEHLGESCHLAGEN: {fails}")
sys.exit(1 if fails else 0)
