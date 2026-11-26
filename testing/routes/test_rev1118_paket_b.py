#!/usr/bin/env python3
"""Revision 2026-11-18, Paket B: Befunde 1, 4 und 25.

1  Import liest erst alles, dann wird ersetzt. Ein Lesefehler lässt die
   alte Config unberührt, ein Schreibfehler stellt sie wieder her, und
   keiner der Wege meldet mehr Erfolg mit halber Config.
4  Jedes Formular trägt seine eigene Basis (Prüfsumme). Ein zweiter Tab,
   der die neue Fassung lädt, verdeckt den Konflikt im ersten nicht mehr;
   eine gelöschte Datei ist ein Konflikt.
25 Zwei fast gleichzeitige Rebuild-Starts: der zweite bekommt
   ERR_REBUILD_RUNNING, statt den ersten zu ersetzen.

Lauf: nix-shell shell.nix --run "python testing/routes/test_rev1118_paket_b.py"
"""
import io, json, os, re, sys, tempfile, threading, zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

xdg = Path(tempfile.mkdtemp(prefix="nico-xdg-"))
os.environ["XDG_CONFIG_HOME"] = str(xdg)
cfg = Path(tempfile.mkdtemp(prefix="nico-cfg-"))
(cfg / "config.json").write_text(json.dumps({"hosts_dir": "hosts"}), encoding="utf-8")
(xdg / "nico").mkdir(parents=True)
(xdg / "nico" / "settings.json").write_text(
    json.dumps({"nixos_config_dir": str(cfg), "language": "de", "git_sync": False}),
    encoding="utf-8")

from nico import atomic, config_manager, core, importer, server  # noqa: E402
from nico.routes import rebuild as rebuild_mod  # noqa: E402

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

OLD = {"configuration.nix": b"{ ... }: { imports = [ ./mod.nix ]; }\n",
       "mod.nix": b"{ ... }: { networking.hostName = \"alt\"; }\n"}

def reset_old():
    for f in cfg.rglob("*.nix"):
        f.unlink()
    for rel, data in OLD.items():
        (cfg / rel).write_bytes(data)

def state():
    return {f.relative_to(cfg).as_posix(): f.read_bytes()
            for f in cfg.rglob("*.nix") if ".git" not in f.parts}

# ── 1a. ZIP mit beschädigtem Eintrag ────────────────────────────────────────
print("1a. ZIP-Import, zweiter Eintrag beschädigt")
reset_old()
buf = io.BytesIO()
with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as zf:
    zf.writestr("configuration.nix", "{ ... }: { imports = [ ./neu.nix ]; }\n")
    zf.writestr("neu.nix", "{ ... }: { networking.hostName = \"neuxxxxxxxxxxx\"; }\n")
raw = bytearray(buf.getvalue())
pos = raw.find(b"neuxxxxxxxxxxx")
raw[pos:pos + 3] = b"XYZ"          # content changed, CRC no longer matches
r = client.post("/api/import/zip/apply",headers={"X-CSRF-Token": csrf},
                data={"file": (io.BytesIO(bytes(raw)), "x.zip"), "confirmed": "true"},
                content_type="multipart/form-data")
body = r.get_json(silent=True) or {}
check("kein Erfolg, ERR_IMPORT_READ", body.get("error") == "ERR_IMPORT_READ"
      and not body.get("success"), (r.status_code, body))
check("alte Config vollständig unberührt", state() == OLD, sorted(state()))

# ── 1b. Schreibfehler mitten im Import → alter Stand zurück ────────────────
print("\n1b. Schreibfehler beim zweiten Datei-Schreiben")
reset_old()
real_write = atomic.write_bytes
calls = {"n": 0}
def failing_write(path, data, mode=None):
    calls["n"] += 1
    if calls["n"] == 2:
        raise OSError(28, "No space left on device")
    return real_write(path, data, mode=mode)
atomic.write_bytes = failing_write
try:
    s, r = post("/api/import/run", {"confirmed": True, "files": [
        {"path": "configuration.nix", "content": "{ ... }: { imports = [ ./neu.nix ]; }\n"},
        {"path": "neu.nix", "content": "{ ... }: { }\n"}]})
finally:
    atomic.write_bytes = real_write
check("ERR_IMPORT_WRITE", r.get("error") == "ERR_IMPORT_WRITE", (s, r))
check("alter Stand wiederhergestellt, keine neue Datei", state() == OLD, sorted(state()))

print("\n1c. Wiederherstellung scheitert auch")
reset_old()
def always_fail(path, data, mode=None):
    raise OSError(28, "No space left on device")
atomic.write_bytes = always_fail
try:
    s, r = post("/api/import/run", {"confirmed": True, "files": [
        {"path": "configuration.nix", "content": "{ ... }: { }\n"}]})
finally:
    atomic.write_bytes = real_write
check("ERR_IMPORT_PARTIAL statt Erfolg", r.get("error") == "ERR_IMPORT_PARTIAL", (s, r))

# ── 1d. /etc/nixos-Import mit unlesbarer Datei ─────────────────────────────
print("\n1d. Import aus /etc/nixos, eine Datei unlesbar")
reset_old()
etc = Path(tempfile.mkdtemp(prefix="nico-etc-"))
(etc / "configuration.nix").write_text("{ ... }: { imports = [ ./geheim.nix ]; }\n", encoding="utf-8")
(etc / "geheim.nix").write_text("{ ... }: { }\n", encoding="utf-8")
os.chmod(etc / "geheim.nix", 0)
real_etc = importer.ETC_NIXOS
importer.ETC_NIXOS = etc
try:
    s, r = post("/api/import/apply", {"confirmed": True})
finally:
    importer.ETC_NIXOS = real_etc
    os.chmod(etc / "geheim.nix", 0o644)
if os.geteuid() == 0:
    print("  (als root übersprungen: chmod 0 sperrt nicht)")
else:
    check("kein Erfolg mehr (früher: except pass)", not r.get("success")
          and r.get("error") == "ERR_IMPORT_PERMISSION", (s, r))
    check("alte Config unberührt", state() == OLD, sorted(state()))

print("\n1e. Import aus /etc/nixos, alles lesbar")
reset_old()
importer.ETC_NIXOS = etc
try:
    s, r = post("/api/import/apply", {"confirmed": True})
finally:
    importer.ETC_NIXOS = real_etc
check("Erfolg", s == 200 and r.get("success"), (s, r))
check("neue Dateien da, alte weg", "geheim.nix" in state() and "mod.nix" not in state(),
      sorted(state()))

# ── 4. Zwei Tabs ────────────────────────────────────────────────────────────
print("\n4. Zwei Tabs, Datei außen geändert")
for f in cfg.rglob("*.nix"):
    f.unlink()
CO = "configuration.nix"
s, r = post("/api/config", dict(config_manager.get_defaults(), hostname="alt",
                                _co_ready=True, _co_path=CO))
check("Ausgangsdatei geschrieben", s == 200 and r.get("success"), r)

def form():
    data = client.get(f"/api/parse/co?path={CO}", headers=H).get_json()
    data.update({"_co_ready": True, "_co_path": CO})
    return data

def change_outside(old, new):
    text = (cfg / CO).read_text(encoding="utf-8").replace(old, new)
    (cfg / CO).write_text(core.set_type_in_content(text, core.get_nico_type(text)),
                          encoding="utf-8")

tab_a = form()
check("Formular trägt eine Basis", bool(tab_a.get("_base")), tab_a.get("_base"))
change_outside('"alt"', '"aussen"')
tab_b = form()                       # second tab loads the newer file
outside_state = (cfg / CO).read_text(encoding="utf-8")
s, r = post("/api/config", tab_a)
check("Tab A: 409 trotz Neuladen in Tab B", s == 409
      and r.get("error") == "ERR_CHANGED_OUTSIDE", (s, r))
check("Datei unverändert", (cfg / CO).read_text(encoding="utf-8") == outside_state)
r = client.get(f"/api/file/outside-diff?path={CO}&base={tab_a['_base']}", headers=H).get_json()
hunks = (r.get("files") or [{}])[0].get("hunks", [])
check("Unterschiede gegen Tab As Stand", any(h["type"] == "removed" and '"alt"' in h["content"]
      for h in hunks) and any(h["type"] == "added" and '"aussen"' in h["content"] for h in hunks), hunks)
tab_b["timezone"] = "Europe/Berlin"
s, r = post("/api/config", tab_b)
check("Tab B speichert normal", s == 200 and r.get("success"), (s, r))
check("Antwort bringt neue Basis", bool(r.get("_base")) and r.get("_base") != tab_b["_base"], r)
check("_base landet nicht in der Datei", "_base" not in (cfg / CO).read_text(encoding="utf-8"))
tab_b["_base"] = r["_base"]
s, r = post("/api/config", tab_b)
check("Tab B danach erneut speicherbar", s == 200 and r.get("success"), (s, r))

print("\n4b. Geladene Datei außen gelöscht")
tab = form()
(cfg / CO).unlink()
s, r = post("/api/config", tab)
check("Löschen gilt als Konflikt", s == 409 and r.get("error") == "ERR_CHANGED_OUTSIDE", (s, r))
check("Datei nicht neu angelegt", not (cfg / CO).exists())

# ── 25. Zwei Rebuild-Starts ─────────────────────────────────────────────────
print("\n25. Zwei Rebuild-Starts fast gleichzeitig")
(cfg / CO).write_text(outside_state, encoding="utf-8")
entered, release = threading.Event(), threading.Event()
real_busy = rebuild_mod.switch_unit_busy
def slow_busy():
    entered.set()
    release.wait(5)
    return True                      # first start ends with ERR_SWITCH_RUNNING, no build
rebuild_mod.switch_unit_busy = slow_busy
first = {}
def start_first():
    first["r"] = client.post("/api/rebuild/start", headers=H, data=json.dumps({"mode": "test"}))
t = threading.Thread(target=start_first)
try:
    t.start()
    check("erster Start ist zwischen Prüfung und Reservierung", entered.wait(5))
    s, r = post("/api/rebuild/start", {"mode": "test"})
    check("zweiter Start: ERR_REBUILD_RUNNING", s == 409
          and r.get("error") == "ERR_REBUILD_RUNNING", (s, r))
finally:
    release.set()
    t.join(10)
    rebuild_mod.switch_unit_busy = real_busy
fr = first.get("r")
check("erster Start lief unbeeinflusst zu Ende",
      fr is not None and (fr.get_json() or {}).get("error") == "ERR_SWITCH_RUNNING",
      fr and fr.get_json())
check("Sperre danach wieder frei", rebuild_mod._start_lock.acquire(blocking=False))
rebuild_mod._start_lock.release()

print()
if fails:
    print(f"{len(fails)} FEHLER")
    sys.exit(1)
print("Alle Prüfungen bestanden.")
