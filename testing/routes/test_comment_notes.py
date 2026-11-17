#!/usr/bin/env python3
"""Kommentare außerhalb von Nix-Brix überleben das Speichern im Panel.

Plan 2026-11-16, Bau 2026-11-17 (Martin, manifest „Neuer Plan“ unter
„Ideen-Durchgang 2026-11-05“): Jeder Kommentar außerhalb eines Bricks, den
NiCo nicht selbst geschrieben hat, wird gerettet – ohne Dialog.

  * Direkt über einem Befehl, der ohnehin Nix-Brick wird, und hinter ihm in
    seiner letzten Zeile: mit in diesen Brick.
  * Alle anderen: in den Sammel-Brick `imported-notes` (Abschnitt TAIL),
    ein Block pro Gruppe, mit dem Ort, an dem er stand.
  * Was der Generator ohnehin zurückschreibt (eine wörtlich übernommene
    Paketliste), steht nicht doppelt.
  * Zweimal speichern ändert nichts; später von außen dazugekommene
    Kommentare werden angehängt; deaktivierte Nix-Brix bleiben unberührt;
    NiCos eigene Dateien ergeben nichts.

Lauf: nix-shell shell.nix --run "python testing/routes/test_comment_notes.py"
"""
import json, os, re, sys, tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

xdg = Path(tempfile.mkdtemp(prefix="nico-xdg-"))
os.environ["XDG_CONFIG_HOME"] = str(xdg)
cfg = Path(tempfile.mkdtemp(prefix="nico-cfg-"))
(cfg / "hosts" / "h1").mkdir(parents=True)
(cfg / "config.json").write_text(json.dumps({"hosts_dir": "hosts"}), encoding="utf-8")
(xdg / "nico").mkdir(parents=True)
(xdg / "nico" / "settings.json").write_text(
    json.dumps({"nixos_config_dir": str(cfg), "language": "de", "git_sync": False}),
    encoding="utf-8")

from nico import config_manager, generator, importer, nix_parser, server  # noqa: E402
from nico.brix import extract_brick_blocks, _END, _START  # noqa: E402

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

def read(rel):
    return (cfg / rel).read_text(encoding="utf-8")

def save(rel):
    form = client.get(f"/api/parse/co?path={rel}", headers=H).get_json()
    form.update({"_co_ready": True, "_co_path": rel})
    url = "/api/config" if rel == "configuration.nix" else \
        f"/api/host/{rel.split('/')[1]}/config"
    return post(url, form)

def notes(text):
    b = extract_brick_blocks(text).get("imported-notes")
    return b["text"] if b else ""

CO = "configuration.nix"
FOREIGN = '''# Mein Laptop, Stand März
{ config, pkgs, ... }:
{
  imports = [ ./hardware-configuration.nix ];

  # von Hand gesetzt 2024
  # nicht zurückstellen!
  networking.hostName = "nixe";

  time.timeZone = "Europe/Berlin"; # Sommerzeit macht Ärger

  # gehört zum Rest-Brick
  services.restdienst.enable = true; # hinten dran

  users.users.anna = {
    isNormalUser = true;
    packages = with pkgs; [
      kate
    #  thunderbird
    ];
  };
  # am Ende
}
'''

# ── 1. Fremde Datei, erstes Speichern ───────────────────────────────────────
print("1. Fremde configuration.nix, erstes Speichern im Panel")
(cfg / CO).write_text(FOREIGN, encoding="utf-8")
(cfg / "hardware-configuration.nix").write_text("{ ... }: { }\n", encoding="utf-8")
s, r = save(CO)
check("Speichern ok", s == 200 and r.get("success"), r)
text = read(CO)
n = notes(text)
check("Sammel-Brick imported-notes vorhanden", bool(n))
check("… im Abschnitt TAIL", "<brick: TAIL /" in n, n.splitlines()[:1])
check("Dateianfang mit Ort", "# at the top of the file:\n#   Mein Laptop, Stand März" in n, n)
check("Block über Formularfeld mit Ort, beide Zeilen",
      "# before networking.hostName:\n#   von Hand gesetzt 2024\n#   nicht zurückstellen!" in n, n)
check("Kommentar hinter Formularfeld", "# after time.timeZone:\n#   Sommerzeit macht Ärger" in n, n)
check("Dateiende", "# at the end of the file:\n#   am Ende" in n, n)
check("Blöcke durch Trennlinie getrennt", importer.NOTES_SEPARATOR in n)
rest = [b["text"] for b in extract_brick_blocks(text).values()
        if "services.restdienst.enable" in b["text"]]
check("Rest-Brick trägt den Kommentar darüber", rest and "# gehört zum Rest-Brick" in rest[0], rest)
check("… und den dahinter", rest and "# hinten dran" in rest[0], rest)
check("… nicht zusätzlich in imported-notes", "gehört zum Rest-Brick" not in n and "hinten dran" not in n)
check("Kommentar in wörtlich übernommener Paketliste steht genau einmal",
      text.count("thunderbird") == 1, text.count("thunderbird"))
check("Formularwert richtig übernommen", 'networking.hostName = "nixe"' in text)

# ── 2. Idempotenz ───────────────────────────────────────────────────────────
print("\n2. Zweimal speichern")
before = read(CO)
save(CO); save(CO)
check("Datei bleibt gleich", read(CO) == before)
fresh = generator.generate_configuration_nix(dict(config_manager.get_defaults(), hostname="x"))
check("frisch erzeugte NiCo-Datei ergibt nichts", importer.collect_notes(fresh) == [],
      importer.collect_notes(fresh))

# ── 3. Später von außen ergänzt ─────────────────────────────────────────────
print("\n3. Kommentar später von außen ergänzt")
t = read(CO)
t = t.replace("  networking.hostName", "  # neu von außen\n  networking.hostName", 1)
(cfg / CO).write_text(t, encoding="utf-8")
s, r = save(CO)
check("Speichern ok", s == 200 and r.get("success"), r)
n2 = notes(read(CO))
check("angehängt", "# before networking.hostName:\n#   neu von außen" in n2, n2)
check("alte Blöcke bleiben", "Mein Laptop, Stand März" in n2 and "Sommerzeit" in n2)
check("weiterhin ein einziger Sammel-Brick", read(CO).count("<brick: TAIL") >= 1
      and read(CO).count("imported-notes>") == 2)

# ── 4. Deaktivierte Nix-Brix ────────────────────────────────────────────────
print("\n4. Deaktivierter Nix-Brick bleibt unberührt")
t = read(CO)
dead = "# <brick: SERV / #9 aus inactive>\n# services.aus.enable = true;\n# </brick: aus>\n"
i = t.rstrip().rfind("}")
(cfg / CO).write_text(t[:i] + dead + t[i:], encoding="utf-8")
before_notes = notes(read(CO))
save(CO)
after = read(CO)
check("deaktivierter Brick steht noch da", "# services.aus.enable = true;" in after
      and "aus inactive>" in after)
check("… und nicht in imported-notes", "services.aus" not in notes(after))
check("imported-notes unverändert", notes(after) == before_notes)

# ── 5. Host-Datei ───────────────────────────────────────────────────────────
print("\n5. Host-Datei")
HOST = "hosts/h1/default.nix"
s, r = post("/api/host/h1/config", {"_co_ready": True, "_co_path": HOST, "hostname": "h1"})
check("Host-Datei erzeugt", s == 200 and r.get("success"), r)
check("NiCos eigene Host-Datei ergibt nichts", importer.collect_notes(read(HOST)) == [],
      importer.collect_notes(read(HOST)))
t = read(HOST)
t = t.replace('  networking.hostName', '  # Rechner im Keller\n  networking.hostName', 1)
(cfg / HOST).write_text(t, encoding="utf-8")
s, r = save(HOST)
check("Speichern ok", s == 200 and r.get("success"), r)
check("Kommentar gerettet", "#   Rechner im Keller" in notes(read(HOST)), notes(read(HOST)))

# ── 6. Marker im Kommentar ──────────────────────────────────────────────────
print("\n6. Brick-Marker im Kommentartext")
out = importer.render_notes([importer.Note("before x", ["</brick: x>", "<brick: A / #1 y>"])])
check("Endmarker entschärft", not _END.search(out), out)
check("Startmarker entschärft", not _START.search(out), out)

# ── 6a. Hinter der letzten Einstellung ──────────────────────────────────────
print("\n6a. Kommentar hinter der letzten Einstellung (Martins VM-Datei)")
last = '{ config, ... }:\n{\n  time.timeZone = "Europe/Berlin";\n  services.pipewire.alsa.support32Bit = true; #test hm2\n}\n'
got = [(nt.where, nt.lines) for nt in importer.collect_notes(last)]
check("Ort ist die letzte Einstellung, nicht das Dateiende",
      got == [("after services.pipewire.alsa.support32Bit", ["test hm2"])], got)
last_rest = '{ config, ... }:\n{\n  time.timeZone = "Europe/Berlin";\n  services.restdienst.enable = true; # hinten\n}\n'
check("hinter dem letzten Rest-Befehl: mit in dessen Brick",
      "services.restdienst.enable = true; # hinten" in importer.build_rest_brix(last_rest, {})
      and importer.collect_notes(last_rest) == [], importer.collect_notes(last_rest))

# ── 6b. Home-Manager-Datei ──────────────────────────────────────────────────
print("\n6b. Home-Manager-Datei (Raw-Speichern normalisiert)")
s, r = post("/api/hm/create", {"username": "anna", "home_dir": "/home/anna",
                               "state_version": "25.05"})
check("HM-Datei erzeugt", s == 200 and r.get("success"), r)
HM = r.get("path", "home/anna.nix")
check("NiCos eigene HM-Datei ergibt nichts",
      importer.collect_notes(read(HM), rest_comments=False) == [],
      importer.collect_notes(read(HM), rest_comments=False))
t = read(HM)
t = t.replace("  home.username", "  # mein Hauptkonto\n  home.username", 1)
i = t.rstrip().rfind("}")
t = t[:i] + "  # eigener Dienst\n  services.hmeigen.enable = true; # hinten\n" + t[i:]
s, r = post("/api/file", {"files": [{"path": HM, "content": t}]})
check("Raw speichern ok", s == 200 and r.get("success"), r)
hm_text = read(HM)
hn = notes(hm_text)
check("Kommentar vor Formularfeld mit Ort", "# before home.username:\n#   mein Hauptkonto" in hn, hn)
check("Kommentar zum Rest-Befehl gerettet", "#   eigener Dienst" in hn, hn)
check("Kommentar hinter Rest-Befehl gerettet", "#   hinten" in hn, hn)
check("Rest-Befehl als Nix-Brick", any("services.hmeigen.enable" in b["text"]
      for b in extract_brick_blocks(hm_text).values()))
s, r = post("/api/file", {"files": [{"path": HM, "content": hm_text}]})
check("zweites Raw-Speichern ändert nichts", read(HM) == hm_text)

# ── 7. Ohne tree-sitter ─────────────────────────────────────────────────────
print("\n7. Ersatzweg ohne tree-sitter")
orig = nix_parser._load_language
nix_parser._load_language = lambda: None
try:
    rx = importer.collect_notes(FOREIGN)
finally:
    nix_parser._load_language = orig
texts = [l for nt in rx for l in nt.lines]
check("ohne Ortsangabe", all(nt.where == "" for nt in rx), [nt.where for nt in rx])
for needle in ("Mein Laptop, Stand März", "von Hand gesetzt 2024", "Sommerzeit macht Ärger",
               "gehört zum Rest-Brick", "hinten dran", " thunderbird", "am Ende"):
    check(f"findet „{needle.strip()}“", any(needle.strip() in t for t in texts))
check("von NiCo erzeugte Datei ergibt nichts",
      importer._rx_collect_notes(generator.generate_configuration_nix(
          config_manager.get_defaults()).strip()) == [])

print()
print("ALLE PRÜFUNGEN BESTANDEN" if not fails else f"FEHLGESCHLAGEN: {len(fails)}")
sys.exit(0 if not fails else 1)
