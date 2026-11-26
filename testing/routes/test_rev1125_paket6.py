#!/usr/bin/env python3
"""Revision 2026-11-25, Paket 6: F7, F8, „Einmal erlauben“ (S3/W1 siehe
test_selfupdate.py).

F7 – Ohne Upstream galt die ganze Historie als lokal, die Bereinigung
großer Dateien schrieb auch Hochgeladenes um. Jetzt entscheiden die
Remote-Zweige (`rev-list HEAD --not --remotes`); ist die Grenze nicht
eindeutig, hält NiCo vor dem Umschreiben an.

F8 – Profil umgestellt, Aktivieren gescheitert: Die Liste zeigte den Stand
gelb als „nächster Start“, ohne Knopf. Jetzt meldet der Job beide Schritte
getrennt, die Liste bietet „Erneut aktivieren“ (nur Schritt 2).

Einmal erlauben – X-NiCo-Read-Once gibt eine Leseart für die Anfrage frei,
gespeichert wird nichts (Martin 2026-11-26).

Lauf: nix-shell shell.nix --run "python testing/routes/test_rev1125_paket6.py"
"""
import json, os, re, subprocess, sys, tempfile, time
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
(cfg / "configuration.nix").write_text("{ ... }: { }\n", encoding="utf-8")

from nico import generations, git_manager, hostread, server  # noqa: E402
from nico.routes import rebuild as rb  # noqa: E402

fails = []
def check(label, cond, got=None):
    print(("  OK   " if cond else "  FAIL ") + label + ("" if cond or got is None else f"  (war: {got})"))
    if not cond: fails.append(label)

def git(*args, cwd=cfg):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True).stdout.strip()

# ── F7 ──────────────────────────────────────────────────────────────────────
print("F7: Veröffentlichungsgrenze ohne Upstream")
git_manager.MAX_STAGE_BYTES = 1000
ok, _ = git_manager.init_repo(str(cfg))
check("Git eingerichtet", ok)
git_manager.auto_commit(str(cfg), label="eins")
remote = Path(tempfile.mkdtemp(prefix="nico-remote-")) / "r.git"
subprocess.run(["git", "init", "--bare", "-q", str(remote)], check=True)
git("remote", "add", "origin", str(remote))
branch = git("rev-parse", "--abbrev-ref", "HEAD")
git("push", "-q", "origin", branch)                     # by hand, without -u
pushed = git("rev-parse", "HEAD")
check("kein Upstream gesetzt", git("rev-parse", "--abbrev-ref", "@{u}") == "")
(cfg / "gross.bin").write_bytes(b"x" * 5000)
git("add", "-f", "gross.bin")
git("commit", "-q", "-m", "lokal mit großer Datei")
check("Grenze: der hochgeladene Commit", git_manager._published_base(str(cfg)) == pushed,
      git_manager._published_base(str(cfg)))
rep = git_manager.large_file_report(str(cfg))
check("große Datei gilt als lokal", [p for p, _ in rep["local"]] == ["gross.bin"], rep)
ok, msg, res = git_manager.clean_large_files(str(cfg), ["gross.bin"])
check("Bereinigung gelingt", ok, msg)
check("hochgeladener Commit bleibt Vorfahr von HEAD",
      subprocess.run(["git", "merge-base", "--is-ancestor", pushed, "HEAD"], cwd=cfg).returncode == 0)

print("\n   Lokale Commits mit Merge, alle über dem hochgeladenen: eindeutig")
git("push", "-q", "origin", branch)
pushed = git("rev-parse", "HEAD")
git("checkout", "-q", "-b", "lokal-seite")
(cfg / "a.nix").write_text("{ }\n"); git("add", "a.nix"); git("commit", "-q", "-m", "seite")
git("checkout", "-q", branch)
(cfg / "b.nix").write_text("{ }\n"); git("add", "b.nix"); git("commit", "-q", "-m", "haupt")
git("merge", "-q", "--no-edit", "lokal-seite")
check("Grenze trotz Merge der hochgeladene Commit", git_manager._published_base(str(cfg)) == pushed,
      git_manager._published_base(str(cfg)))

print("\n   Ein hochgeladener Seitenzweig ist eingemischt: nicht eindeutig")
git("checkout", "-q", pushed)
git("checkout", "-q", "-b", "veroeffentlicht")
(cfg / "c.nix").write_text("{ }\n"); git("add", "c.nix"); git("commit", "-q", "-m", "hochgeladene Seite")
git("push", "-q", "origin", "veroeffentlicht")
git("checkout", "-q", branch)
git("merge", "-q", "--no-edit", "veroeffentlicht")
(cfg / "gross2.bin").write_bytes(b"y" * 5000)
git("add", "-f", "gross2.bin"); git("commit", "-q", "-m", "groß")
check("Grenze unbekannt → None", git_manager._published_base(str(cfg)) is None,
      git_manager._published_base(str(cfg)))
head = git("rev-parse", "HEAD")
ok, msg, _ = git_manager.clean_large_files(str(cfg), ["gross2.bin"])
check("Bereinigung hält an: ERR_PUBLISH_UNKNOWN", not ok and msg == "ERR_PUBLISH_UNKNOWN", msg)
check("… nichts umgeschrieben", git("rev-parse", "HEAD") == head)

# ── Einmal erlauben ─────────────────────────────────────────────────────────
print("\nEinmal erlauben")
client = server.create_app().test_client()
page = client.get("/").get_data(as_text=True)
csrf = re.search(r'<meta name="csrf-token" content="([^"]+)"', page).group(1)
H = {"X-CSRF-Token": csrf, "Content-Type": "application/json"}
ONCE = {"X-NiCo-Read-Once": "read_host_generations"}

gens = [generations.Generation(number=5, date="2026-11-26 10:00", nixos_version="25.11",
                               kernel="6.18", running=True, booted=True, default=False),
        generations.Generation(number=6, date="2026-11-26 11:00", nixos_version="25.11",
                               kernel="6.18", running=False, booted=False, default=True)]
generations.list_generations = lambda *a, **k: gens
r = client.get("/api/generations").get_json()
check("ohne Freigabe: nicht gelesen", r.get("allowed") is False, r)
r = client.get("/api/generations", headers=ONCE).get_json()
check("einmal erlaubt (Kopfzeile): gelesen", r.get("allowed") is True and len(r["generations"]) == 2, r)
check("nichts gespeichert", hostread.state()["read_host_generations"] is False)
check("vier Schalter", set(hostread.state()) == {"read_host_validator", "read_host_hardware",
                                                  "read_host_generations", "read_host_profile"})
r = client.get("/api/generations", headers={"X-NiCo-Read-Once": "read_host_validator"}).get_json()
check("andere Art in der Kopfzeile gibt diese nicht frei", r.get("allowed") is False, r)

# ── F8 ──────────────────────────────────────────────────────────────────────
print("\nF8: Systemstand eingetragen, Aktivieren gescheitert")
def wait_done(job_id):
    for _ in range(200):
        with rb._job_cv:
            j = rb._job
            if j and j["id"] == job_id and not j["running"]:
                for ev in j["events"]:
                    if '"type": "done"' in ev:
                        return json.loads(ev.split("data: ", 1)[1])
                return {}
        time.sleep(0.05)
    return {}

# Harmless stand-ins; the third element marks the activation step as the
# job recognises it.
generations.switch_commands = lambda n, m, *a: [["sh", "-c", "exit 0"],
                                                ["sh", "-c", ": switch-to-configuration; exit 1"]]
generations.activate_command = lambda m, *a: ["sh", "-c", ": switch-to-configuration; exit 0"]
HO = {**H, **ONCE}
r = client.post("/api/generations/switch", headers=HO, data=json.dumps({"number": 6, "mode": "boot"}))
check("Wechsel gestartet", r.status_code == 200, (r.status_code, r.get_json()))
done = wait_done(r.get_json().get("job_id"))
check("Ergebnis: eingetragen, Aktivieren gescheitert",
      done.get("success") is False and done.get("entered") is True and done.get("failed_step") == "activate", done)
client.post("/api/rebuild/ack", headers=H)
r = client.get("/api/generations", headers=ONCE).get_json()
check("Liste bietet „Erneut aktivieren“ für Stand 6", r.get("retry") == {"number": 6, "mode": "boot"}, r)

r = client.post("/api/generations/switch", headers=HO,
                data=json.dumps({"number": 5, "mode": "boot", "activate_only": True}))
check("„Erneut aktivieren“ nur für den eingetragenen Stand", r.status_code == 400, (r.status_code, r.get_json()))
r = client.post("/api/generations/switch", headers=HO,
                data=json.dumps({"number": 6, "mode": "boot", "activate_only": True}))
done = wait_done(r.get_json().get("job_id"))
check("nur Schritt 2, erfolgreich", done.get("success") is True and done.get("failed_step") == "", done)
client.post("/api/rebuild/ack", headers=H)
r = client.get("/api/generations", headers=ONCE).get_json()
check("danach kein Angebot mehr", r.get("retry") is None, r)

generations.switch_commands = lambda n, m, *a: [["sh", "-c", "exit 1"],
                                                ["sh", "-c", ": switch-to-configuration; exit 0"]]
r = client.post("/api/generations/switch", headers=HO, data=json.dumps({"number": 6, "mode": "boot"}))
done = wait_done(r.get_json().get("job_id"))
check("Eintragen gescheitert: entered false, kein Angebot",
      done.get("entered") is False and done.get("failed_step") == "enter", done)
client.post("/api/rebuild/ack", headers=H)
check("… Liste ohne retry", client.get("/api/generations", headers=ONCE).get_json().get("retry") is None)

print()
if fails:
    print(f"{len(fails)} FEHLER")
    sys.exit(1)
print("Alles grün.")
