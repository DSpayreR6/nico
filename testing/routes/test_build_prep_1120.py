#!/usr/bin/env python3
"""Codex-Aufgaben vom 2026-11-20: Downloadzählung und Build-Vorbereitung.

A  Downloadgröße (rebuild.DownloadTally) – Codex' Rechenbeispiel: 100 MiB
   geplant, zwei Downloads mit 40 und 60 MiB. Sind 40 und 10 MiB da, stehen
   vor und nach dem Ende des ersten 50 geladen, 50 verbleibend; am Schluss
   100 / 0. Früher zog das Ende eines Downloads seine Bytes wieder ab.
B  Dieselbe Dateiauswahl für Monitor-Weg (stage_all), Terminal und Dry-Run:
   aus gleichen Repos mit neuer .nix, Fremddatei und übergroßer Datei
   entsteht derselbe Index. Scheitert git, melden alle Wege den Fehler und
   rufen nix nicht auf.

Lauf: nix-shell shell.nix --run "python testing/routes/test_build_prep_1120.py"
"""
import json, os, re, shutil, subprocess, sys, tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

xdg = Path(tempfile.mkdtemp(prefix="nico-xdg-"))
os.environ["XDG_CONFIG_HOME"] = str(xdg)
(xdg / "nico").mkdir(parents=True)
settings = xdg / "nico" / "settings.json"

from nico import config_manager, generator, git_manager, server  # noqa: E402
from nico.routes import rebuild as rebuild_mod  # noqa: E402

fails = []
def check(label, cond, got=None):
    print(("  OK   " if cond else "  FAIL ") + label + ("" if cond or got is None else f"  (war: {got})"))
    if not cond: fails.append(label)

MiB = 1024 * 1024

# ── A ───────────────────────────────────────────────────────────────────────
print("A. Downloadzählung")
plan = 100 * MiB
tally = rebuild_mod.DownloadTally()
def remaining():
    return max(tally.expected, plan) - tally.done
tally.progress("a", 0, 40 * MiB)
tally.progress("b", 0, 60 * MiB)
tally.progress("a", 40 * MiB, 40 * MiB)
tally.progress("b", 10 * MiB, 60 * MiB)
check("vor dem Ende von A: 50 geladen, 50 verbleibend",
      tally.done == 50 * MiB and remaining() == 50 * MiB, (tally.done, remaining()))
tally.stop("a")
check("nach dem Ende von A: unverändert 50 / 50",
      tally.done == 50 * MiB and remaining() == 50 * MiB, (tally.done, remaining()))
tally.progress("b", 60 * MiB, 60 * MiB)
tally.stop("b")
check("am Schluss 100 geladen, 0 verbleibend", tally.done == 100 * MiB and remaining() == 0,
      (tally.done, remaining()))
t2 = rebuild_mod.DownloadTally()
t2.progress("x", 5, 10)
t2.stop("x")   # broken off: what arrived counts, its plan becomes that
check("abgebrochener Download: Plan = Angekommenes", t2.done == 5 and t2.expected == 5, (t2.done, t2.expected))

# ── B ───────────────────────────────────────────────────────────────────────
print("\nB. Dieselbe Dateiauswahl auf allen Wegen")
git_manager.MAX_STAGE_BYTES = 1000

def make_repo():
    cfg = Path(tempfile.mkdtemp(prefix="nico-cfg-"))
    (cfg / "config.json").write_text(json.dumps({"hosts_dir": "hosts"}), encoding="utf-8")
    data = config_manager.get_defaults()
    data.update({"flakes": True, "hostname": "h1", "flake_hosts": [{"name": "h1"}]})
    config_manager.save_config(str(cfg), data)
    (cfg / "configuration.nix").write_text(generator.generate_configuration_nix(data), encoding="utf-8")
    (cfg / "flake.nix").write_text(generator.generate_flake_nix(data, nixos_dir=str(cfg)), encoding="utf-8")
    config_manager.save_host_config(str(cfg), "h1", {})
    git_manager.init_repo(str(cfg))
    git_manager.auto_commit(str(cfg), label="start")
    (cfg / "modules").mkdir()
    (cfg / "modules" / "neu.nix").write_text("{ ... }: { }\n", encoding="utf-8")
    (cfg / "notizen.txt").write_text("fremd\n", encoding="utf-8")
    (cfg / "modules" / "riesig.nix").write_text("{ ... }: { }\n" + "#" * 2000 + "\n", encoding="utf-8")
    return cfg

def index(cfg):
    return sorted(subprocess.run(["git", "diff", "--cached", "--name-only"], cwd=cfg,
                                 capture_output=True, text=True).stdout.split())

def client_for(cfg):
    settings.write_text(json.dumps({"nixos_config_dir": str(cfg), "language": "de",
                                    "git_sync": False}), encoding="utf-8")
    c = server.create_app().test_client()
    page = c.get("/").get_data(as_text=True)
    csrf = re.search(r'<meta name="csrf-token" content="([^"]+)"', page).group(1)
    return c, {"X-CSRF-Token": csrf, "Content-Type": "application/json"}

nix_calls = []
real_run, real_popen, real_which = subprocess.run, subprocess.Popen, shutil.which
def fake_run(cmd, *a, **kw):
    if cmd and cmd[0] in ("nix", "nixos-rebuild", "nix-instantiate"):
        nix_calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, "", "")
    return real_run(cmd, *a, **kw)
launched = []
def fake_popen(cmd, *a, **kw):
    if isinstance(cmd, list) and cmd and cmd[0] == "konsole":
        launched.append(cmd)
        class P: pid = 0
        return P()
    return real_popen(cmd, *a, **kw)
def fake_which(name, *a, **kw):
    return "/usr/bin/konsole" if name == "konsole" else real_which(name, *a, **kw)
subprocess.run, subprocess.Popen, shutil.which = fake_run, fake_popen, fake_which

try:
    expected = ["modules/neu.nix"]

    cfg = make_repo()
    ok, msg = git_manager.stage_all(str(cfg))
    check("Monitor-Weg (stage_all): Erfolg", ok, msg)
    check("… Index: neue .nix, keine Fremd-, keine Riesendatei", index(cfg) == expected, index(cfg))

    cfg = make_repo()
    c, H = client_for(cfg)
    r = c.post("/api/dry-run", headers=H, data=json.dumps({"hostname": "h1"}))
    check("Dry-Run: derselbe Index", index(cfg) == expected, (index(cfg), r.get_json()))

    cfg = make_repo()
    c, H = client_for(cfg)
    r = c.post("/api/rebuild/open-terminal", headers=H, data=json.dumps({"hostname": "h1", "mode": "test"}))
    check("Terminal: gestartet", r.status_code == 200 and launched, (r.status_code, r.get_json()))
    check("Terminal: derselbe Index", index(cfg) == expected, index(cfg))
    script = " ".join(" ".join(x) for x in launched)
    check("Terminal-Skript ohne eigenes 'git add -A'", "git add -A" not in script and
          not any("git add -A" in Path(p).read_text() for p in re.findall(r"(/\S+\.sh)", script)
                  if Path(p).exists()), script[:200])

    print("\n   git scheitert (Index gesperrt)")
    cfg = make_repo()
    (cfg / ".git" / "index.lock").write_text("", encoding="utf-8")
    ok, msg = git_manager.stage_all(str(cfg))
    check("stage_all meldet den Fehler", not ok and msg.startswith("ERR_GIT_FAILED_OUT"), (ok, msg))
    c, H = client_for(cfg)
    nix_calls.clear()
    r = c.post("/api/dry-run", headers=H, data=json.dumps({"hostname": "h1"}))
    body = r.get_json() or {}
    check("Dry-Run: Fehler, nix nicht aufgerufen", not body.get("ok")
          and str(body.get("output", "")).startswith("ERR_GIT_FAILED_OUT") and not nix_calls,
          (body, nix_calls))
    launched.clear()
    r = c.post("/api/rebuild/open-terminal", headers=H, data=json.dumps({"hostname": "h1", "mode": "test"}))
    check("Terminal: Fehler, kein Terminal gestartet", r.status_code == 500 and not launched,
          (r.status_code, r.get_json()))
finally:
    subprocess.run, subprocess.Popen, shutil.which = real_run, real_popen, real_which

print()
if fails:
    print(f"{len(fails)} FEHLER")
    sys.exit(1)
print("Alle Prüfungen bestanden.")
