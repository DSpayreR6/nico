#!/usr/bin/env python3
"""„Nix-Brick ins Panel übernehmen" (Gegenprobe Phase 4, Martin 2026-11-19).

Die Route übernimmt einen Nix-Brick nur, wenn das Panel genau dasselbe
schreibt: jede Zeile des Bricks mit gleichem Wert, der Rest der Datei
unverändert. Alles oder nichts, vorher ein Sicherungspunkt. Abgelehnt mit
den Pfaden, die sich ändern würden; Kommentare im Brick → abgelehnt;
deaktivierte und reservierte Bricks nicht.

Lauf: nix-shell shell.nix --run "python testing/routes/test_brick_adopt.py"
"""
import json, os, re, subprocess, sys, tempfile
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
(cfg / "config.json").write_text(json.dumps({"hosts_dir": "hosts"}), encoding="utf-8")
env = {**os.environ, "GIT_CEILING_DIRECTORIES": str(cfg.parent)}
subprocess.run(["git", "init", "-q"], cwd=cfg, env=env, check=True)
subprocess.run(["git", "config", "user.email", "t@t"], cwd=cfg, env=env, check=True)
subprocess.run(["git", "config", "user.name", "t"], cwd=cfg, env=env, check=True)
os.environ["GIT_CEILING_DIRECTORIES"] = str(cfg.parent)

from nico import config_manager, fidelity, git_manager, importer, server  # noqa: E402
from nico.brix import extract_brick_blocks, format_brick, strip_brick_blocks  # noqa: E402

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

CO = cfg / "configuration.nix"

def write_co(bricks, **fields):
    """A configuration.nix as NiCo writes it, with the given bricks."""
    d = config_manager.get_defaults()
    d.update(hostname="box", _flake_mode=False, **fields)
    d["brick_blocks"] = {n: {"section": "SERV", "order": i + 1,
                             "text": format_brick("SERV", i + 1, n, body)}
                         for i, (n, body) in enumerate(bricks.items())}
    config_manager.save_config(str(cfg), d)
    git_manager.auto_commit(str(cfg), label="test")
    return CO.read_text(encoding="utf-8")

def adopt(name, file="configuration.nix"):
    return post("/api/brick/adopt", {"name": name, "file": file})

def form():
    return importer.parse_config(strip_brick_blocks(CO.read_text(encoding="utf-8")))

print("1. Abbildbarer Brick wird übernommen")
before = write_co({"druck": "services.printing.enable = true;"})
s, r = adopt("druck")
after = CO.read_text(encoding="utf-8")
check("Übernahme ok", s == 200 and r.get("success"), r)
check("Brick ist weg", "druck" not in extract_brick_blocks(after), list(extract_brick_blocks(after)))
check("Drucken steht im Formular", form().get("printing") is True, form())
d = fidelity.compare(before, after)
check("Datei sagt dasselbe", not fidelity.offending(d) and not d.missing, d)
head = subprocess.run(["git", "show", "HEAD:configuration.nix"], cwd=cfg, env=env,
                      capture_output=True, text=True).stdout
check("Stand davor liegt in einem Sicherungspunkt", head == before)

print("\n2. Vorschlag auskommentiert: abgelehnt; einkommentiert: übernommen")
hint_body = ("services.avahi.enable = true;\n" + fidelity.HINT_LINE + "\n"
             "# services.avahi.nssmdns4 = true;\n# services.avahi.openFirewall = true;")
before = write_co({"avahi": hint_body})
s, r = adopt("avahi")
check("abgelehnt, weil das Panel mehr schreibt", s == 409 and r.get("error", "").startswith("ERR_ADOPT_NOT_EXACT"), r)
check("… nennt nssmdns4", "services.avahi.nssmdns4" in r.get("paths", []), r.get("paths"))
check("… Datei unverändert", CO.read_text(encoding="utf-8") == before)
before = write_co({"avahi": hint_body.replace("# services", "services")})
s, r = adopt("avahi")
check("einkommentiert: übernommen", s == 200 and r.get("success"), r)
check("Avahi im Formular", form().get("avahi") is True, form())

print("\n3. Ausdrücklich anderer Wert: nie übernommen")
before = write_co({"avahi": "services.avahi = { enable = true; nssmdns4 = true; openFirewall = false; };"})
s, r = adopt("avahi")
check("openFirewall = false: abgelehnt", s == 409 and "services.avahi.openFirewall" in r.get("paths", []), r)
check("… Datei unverändert", CO.read_text(encoding="utf-8") == before)

# A panel value next to a brick saying the opposite cannot be saved at all
# (revision 2026-11-18, finding 30: DuplicateAttrError), so there is no such
# file to take a brick from.

print("\n4. Kommentare, deaktiviert, reserviert")
before = write_co({"druck": "# mein Drucker\nservices.printing.enable = true;"})
s, r = adopt("druck")
check("Kommentar im Brick: abgelehnt", s == 400 and r.get("error") == "ERR_ADOPT_COMMENTS", r)
check("… Datei unverändert", CO.read_text(encoding="utf-8") == before)
d = config_manager.get_defaults(); d.update(hostname="box", _flake_mode=False)
d["brick_blocks"] = {"druck": {"section": "SERV", "order": 1, "inactive": True,
                               "text": format_brick("SERV", 1, "druck",
                                                    "# services.printing.enable = true;", True)}}
config_manager.save_config(str(cfg), d)
s, r = adopt("druck")
check("deaktivierter Brick: abgelehnt", s == 400 and r.get("error") == "ERR_BRICK_RESERVED", r)
s, r = adopt("gibtsnicht")
check("unbekannter Brick: abgelehnt", s == 400 and r.get("error") == "ERR_BRICK_NOT_FOUND", r)

print("\n5. Host-Datei")
hd = cfg / "hosts" / "h1"
hd.mkdir(parents=True, exist_ok=True)
hdata = {"hostname": "h1", "brick_blocks": {"druck": {"section": "SERV", "order": 1,
         "text": format_brick("SERV", 1, "druck", "services.printing.enable = true;")}}}
config_manager.save_host_config(str(cfg), "h1", hdata)
before = (hd / "default.nix").read_text(encoding="utf-8")
s, r = adopt("druck", "hosts/h1/default.nix")
after = (hd / "default.nix").read_text(encoding="utf-8")
check("Host: übernommen", s == 200 and r.get("success"), r)
check("Host: Brick weg", "druck" not in extract_brick_blocks(after))
d = fidelity.compare(before, after)
check("Host: Datei sagt dasselbe", not fidelity.offending(d) and not d.missing, d)

print("\n6. Andere Dateien")
(cfg / "modules").mkdir(exist_ok=True)
(cfg / "modules" / "x.nix").write_text("{ ... }:\n{\n}\n", encoding="utf-8")
s, r = adopt("druck", "modules/x.nix")
check("Moduldatei: abgelehnt", s == 400 and r.get("error") in ("ERR_ADOPT_FILE", "ERR_BRICK_NOT_FOUND"), r)

print()
if fails:
    print(f"{len(fails)} FEHLER:", *fails, sep="\n  ")
    sys.exit(1)
print("Alle Prüfungen bestanden.")
