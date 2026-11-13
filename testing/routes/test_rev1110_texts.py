#!/usr/bin/env python3
"""Revision 2026-11-10, Paket 4: Config in einem fremden Repository, und die
letzten deutschen Sätze aus dem Backend.

  B41  Liegt das Config-Verzeichnis als Unterverzeichnis in einem größeren
       Repository (Dotfiles), legte NiCo beim Einrichten darin ein eigenes
       `.git` an – ohne Rückfrage. Jetzt wird nicht initialisiert, und
       „Git initialisieren" antwortet mit ERR_INSIDE_OTHER_REPO.
  B48  Deutsche Sätze, die in jeder Sprache erschienen: Dry-Run-Ausgabe,
       Terminal-Skript, git-Timeout, set_remote, die „Läuft bereits"-Seite.

Lauf: nix-shell shell.nix --run "python testing/routes/test_rev1110_texts.py"
"""
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

xdg = Path(tempfile.mkdtemp(prefix="nico-xdg-"))
os.environ["XDG_CONFIG_HOME"] = str(xdg)
(xdg / "nico").mkdir(parents=True)
SETTINGS = xdg / "nico" / "settings.json"

from nico import config_manager, generator, git_manager, main, server  # noqa: E402

fails = []
def check(label, cond, got=None):
    print(("  OK   " if cond else "  FAIL ") + label + ("" if cond or got is None else f"  (war: {got})"))
    if not cond:
        fails.append(label)

def use(cfg: Path):
    SETTINGS.write_text(json.dumps({"nixos_config_dir": str(cfg), "language": "de",
                                    "git_sync": False}), encoding="utf-8")

def git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)

def write_conf(d: Path):
    data = config_manager.get_defaults()
    (d / "configuration.nix").write_text(generator.generate_configuration_nix(data), encoding="utf-8")

LANGS = {p.stem: json.loads(p.read_text(encoding="utf-8"))
         for p in sorted((ROOT / "nico" / "static" / "lang").glob("*.json"))}

print("B41 Config in einem fremden Repository")
outer = Path(tempfile.mkdtemp(prefix="nico-dotfiles-")).resolve()
git(outer, "init", "-q")
sub = outer / "nixos"
sub.mkdir()
write_conf(sub)
own = Path(tempfile.mkdtemp(prefix="nico-own-")).resolve()
write_conf(own)
git(own, "init", "-q")
plain = Path(tempfile.mkdtemp(prefix="nico-plain-")).resolve()
write_conf(plain)

fn = getattr(git_manager, "inside_other_repo", None)
check("es gibt git_manager.inside_other_repo", callable(fn))
if callable(fn):
    check("Unterverzeichnis eines Repos: ja, mit dessen Wurzel", fn(str(sub)) == str(outer), fn(str(sub)))
    check("eigenes Repository: nein", not fn(str(own)), fn(str(own)))
    check("gar kein Repository: nein", not fn(str(plain)), fn(str(plain)))

use(sub)
app = server.create_app()
app.config["TESTING"] = True
c = app.test_client()
m = re.search(r'name="csrf-token" content="([^"]+)"', c.get("/").get_data(as_text=True))
H = {"X-CSRF-Token": m.group(1) if m else "", "Content-Type": "application/json"}

r = c.post("/api/git/init", headers=H, data="{}")
j = r.get_json() or {}
check("Git initialisieren wird abgelehnt", j.get("success") is False, j)
check("mit dem Code und der Wurzel des fremden Repos",
      str(j.get("message", "")).startswith("ERR_INSIDE_OTHER_REPO") and str(outer) in str(j.get("message")), j)
check("kein .git im Unterverzeichnis entstanden", not (sub / ".git").exists())
ok, msg = git_manager.init_repo(str(sub))
check("auch init_repo selbst lehnt ab", ok is False and not (sub / ".git").exists(), (ok, msg))

r = c.post("/api/setup", headers=H, data=json.dumps({"nixos_config_dir": str(sub)}))
check("Einrichten gelingt", (r.get_json() or {}).get("success") is True, (r.status_code, r.get_json()))
check("und legt kein .git im Unterverzeichnis an", not (sub / ".git").exists())
r = c.post("/api/setup", headers=H, data=json.dumps({"nixos_config_dir": str(plain)}))
check("ohne fremdes Repository wird weiter initialisiert",
      (r.get_json() or {}).get("success") is True and (plain / ".git").is_dir(), (r.status_code, r.get_json()))
use(plain)
r = c.post("/api/git/init", headers=H, data="{}")
for lang, texts in LANGS.items():
    t = texts.get("errors.ERR_INSIDE_OTHER_REPO", "")
    check(f"{lang}: ERR_INSIDE_OTHER_REPO übersetzt, mit Platzhalter", bool(t) and t != "__TODO__" and "{0}" in t, t)

print("\nB48 deutsche Sätze im Backend")
GERMAN = re.compile(r"fehlgeschlagen|abgebrochen|Unbekannter Fehler|Fehler beim|wird beendet|"
                    r"Wird neu gestartet|Warte auf|bitte manuell|Laufende Instanz|Neu starten|"
                    r"l&auml;uft|&ouml;ffnen")
for rel in ("routes/rebuild.py", "git_manager.py", "main.py"):
    hits = []
    for no, line in enumerate((ROOT / "nico" / rel).read_text(encoding="utf-8").splitlines(), 1):
        code = line.split("#", 1)[0] if not line.strip().startswith(('"', "'")) else line
        if GERMAN.search(code) and '"""' not in line:
            hits.append(f"{no}: {line.strip()[:90]}")
    check(f"{rel}: keine deutschen Sätze mehr", not hits, hits)

rc, out = git_manager._run(["status"], cwd=str(plain), timeout=0.000001)
check("git-Timeout: Marker bleibt, Text ist englisch",
      "__TIMEOUT__" in out and "timed out" in out, out)
check("und wird weiter als TIMEOUT eingeordnet", git_manager.classify_push_error(out) == "TIMEOUT")

orig = git_manager._run
git_manager._run = lambda args, *a, **kw: (1, "") if args[:2] in (["remote", "add"], ["remote", "set-url"]) else orig(args, *a, **kw)
try:
    ok, msg = git_manager.set_remote(str(plain), "git@example.org:x/y.git")
finally:
    git_manager._run = orig
check("set_remote ohne git-Ausgabe: Code statt Satz", ok is False and msg.startswith("ERR_GIT_FAILED"), (ok, msg))

texts_fn = getattr(main, "_running_texts", None)
check("es gibt main._running_texts", callable(texts_fn))
KEYS = ("running.intro", "running.open", "running.restart", "running.restarting",
        "running.stopping", "running.waiting", "running.timeout")
if callable(texts_fn):
    de, en, zh = texts_fn("de"), texts_fn("en"), texts_fn("zh")
    check("alle Texte der Seite kommen aus der Sprachdatei",
          all(de.get(k) == LANGS["de"].get(k) and de.get(k) for k in KEYS), de)
    check("andere Sprache, andere Texte", all(en[k] != de[k] and zh[k] != en[k] for k in KEYS))
    check("unbekannte Sprache fällt auf Englisch zurück", texts_fn("xx") == en)
    page_fn = getattr(main, "_running_page", None)
    check("es gibt main._running_page", callable(page_fn))
    if callable(page_fn):
        page = page_fn("ru", theme_css="", note="", mini_port=1, preferred_port=2, restart_token="tok")
        check("die Seite trägt die Sprache", '<html lang="ru">' in page)
        check("und die russischen Texte", LANGS["ru"]["running.open"] in page
              and json.dumps(LANGS["ru"]["running.timeout"]) in page, page[-900:])
        check("keine offenen Platzhalter", not re.search(r"\bT_[A-Z]+\b|MINI_PORT|PREF_PORT|RESTART_TOKEN|THEME_CSS", page))
        evil = main._running_page.__wrapped__ if hasattr(main._running_page, "__wrapped__") else None
for lang, texts in LANGS.items():
    for k in KEYS + ("errors.ERR_DRY_UPDATE_FAILED",):
        check(f"{lang}: {k}", bool(texts.get(k)) and texts.get(k) != "__TODO__")

src = (ROOT / "nico" / "routes" / "rebuild.py").read_text(encoding="utf-8")
check("Dry-Run: gescheitertes flake update als Code", "ERR_DRY_UPDATE_FAILED" in src)
check("Dry-Run: unbekannter Fehler als Code", '"output": combined or "ERR_UNKNOWN"' in src)
js = (ROOT / "nico" / "static" / "js" / "rebuild.js").read_text(encoding="utf-8")
check("beide Dry-Run-Wege übersetzen die Ausgabe",
      js.count("tErr(data.output)") == 2, js.count("tErr(data.output)"))

print()
if fails:
    print(f"{len(fails)} FEHLER:", *fails, sep="\n  ")
    sys.exit(1)
print("Alle Prüfungen bestanden.")
