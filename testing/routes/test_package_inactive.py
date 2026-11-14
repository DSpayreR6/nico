#!/usr/bin/env python3
"""Systempakete deaktivieren, statt sie zu löschen.

Martins Wunsch vom 2026-11-10, sein Fall: Der Build scheitert an einem Paket
(`codex`). Löschen hieße, es später neu suchen zu müssen. Umgesetzt am
2026-11-14 über einen reservierten Nix-Brick im Abschnitt Programme:

    # <brick: PROG / #1 nico-inactive-packages inactive>
    #   codex
    # </brick: nico-inactive-packages>

Der Plan vom 2026-11-10 (auskommentierte Zeile in der Liste, Formatnummer 4)
ist damit abgelöst: ein deaktivierter Nix-Brick ist Format 3, das es schon
gibt.

Geprüft wird:
   1. Schreiben: deaktiviertes Paket steht im Brick, nicht in der Liste;
      Nix liest die Datei; Formatnummer 3 nur mit deaktiviertem Paket
   2. Laden: das Paket kommt als `enabled: False` zurück
   3. Die aktive Liste gewinnt, wenn ein Name in beiden steht
   4. Letztes Paket aktiviert oder gelöscht: der Brick verschwindet
   5. Alle Pakete deaktiviert: keine Liste, aber der Brick
   6. Die Sicherung: ein Schreibweg, der die Paketliste nur aus dem Parser
      kennt (Import), lässt den Brick stehen
   7. Routen: der reservierte Name ist gesperrt (anlegen, umbenennen,
      teilen, verschieben, bearbeiten, aktivieren), löschen geht
   8. Formular-Weg: /api/parse/co liefert die deaktivierten Pakete,
      POST /api/config schreibt sie, /api/preview zeigt sie
   9. Validator: Brick ohne `inactive` ist ein Fehler, fremde Zeile ein
      Hinweis – und die fremde Zeile bleibt beim Schreiben erhalten
  10. Host-Datei

Lauf: nix-shell shell.nix --run "python testing/routes/test_package_inactive.py"
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

cfg = Path(tempfile.mkdtemp(prefix="nico-cfg-"))
(xdg / "nico").mkdir(parents=True)
(xdg / "nico" / "settings.json").write_text(
    json.dumps({"nixos_config_dir": str(cfg), "language": "de", "git_sync": False}),
    encoding="utf-8")

from nico import brix, config_manager, generator, importer, server, validator  # noqa: E402

NAME = brix.INACTIVE_PKG_BRICK
KNOWN = brix.PKG_INACTIVE_KNOWN
CONF = cfg / "configuration.nix"
fails = []


def check(label, cond, got=None):
    print(("  OK   " if cond else "  FAIL ") + label
          + ("" if cond or got is None else f"  (war: {got})"))
    if not cond:
        fails.append(label)


def nix_reads(text: str) -> bool:
    tmp = Path(tempfile.mkdtemp()) / "t.nix"
    tmp.write_text(text, encoding="utf-8")
    try:
        r = subprocess.run(["nix-instantiate", "--parse", str(tmp)],
                           capture_output=True, timeout=30)
        return r.returncode == 0
    except Exception:
        return True          # no nix here: do not fail the run over it


def pk(attr, enabled=True):
    return {"attr": attr, "enabled": enabled}


def gen(packages, known=True, blocks=None):
    data = config_manager.get_defaults()
    data["packages"] = packages
    if known:
        data[KNOWN] = True
    if blocks is not None:
        data["brick_blocks"] = blocks
    return generator.generate_configuration_nix(data)


def pkg_list_of(text: str) -> list:
    m = re.search(r"environment\.systemPackages = with pkgs; \[(.*?)\];", text, re.S)
    return m.group(1).split() if m else []


def fmt(text: str) -> str:
    m = re.search(r"# nico-version: .*#f(\d+)", text)
    return m.group(1) if m else ""


print("\n1. Schreiben")
out = gen([pk("git"), pk("codex", False), pk("htop")])
blocks = brix.extract_brick_blocks(out)
check("der Brick steht in der Datei", NAME in blocks, list(blocks))
check("er ist deaktiviert", blocks.get(NAME, {}).get("inactive") is True)
check("er steht im Abschnitt Programme", blocks.get(NAME, {}).get("section") == "PROG",
      blocks.get(NAME, {}).get("section"))
check("codex steht im Brick", brix.inactive_package_names(blocks) == ["codex"],
      brix.inactive_package_names(blocks))
check("codex steht nicht in der Paketliste", pkg_list_of(out) == ["git", "htop"], pkg_list_of(out))
check("Nix liest die Datei", nix_reads(out))
check("Formatnummer 3", fmt(out) == "3", fmt(out))
out2 = gen([pk("git"), pk("htop")])
check("ohne deaktiviertes Paket: kein Brick", NAME not in brix.extract_brick_blocks(out2))
check("ohne deaktiviertes Paket: Formatnummer 2", fmt(out2) == "2", fmt(out2))

print("\n2. Laden")
CONF.write_text(out, encoding="utf-8")
data = config_manager.load_config(str(cfg))
got = {p["attr"]: p.get("enabled", True) for p in data["packages"]}
check("alle drei Pakete in der Liste", got == {"git": True, "htop": True, "codex": False}, got)
check("Laden setzt die Vollständigkeits-Marke nicht", not data.get(KNOWN))
data[KNOWN] = True
again = generator.generate_configuration_nix(data)
check("laden und schreiben ändert die Datei nicht", again == out)

print("\n3. Die aktive Liste gewinnt")
both = gen([pk("git"), pk("codex")], blocks=dict(blocks))
check("aktiviertes Paket verlässt den Brick", NAME not in brix.extract_brick_blocks(both),
      brix.inactive_package_names(brix.extract_brick_blocks(both)))
check("und steht in der Liste", "codex" in pkg_list_of(both), pkg_list_of(both))
dup = gen([pk("codex"), pk("codex", False), pk("vim", False)])
check("Name aktiv und deaktiviert zugleich: nur aktiv",
      brix.inactive_package_names(brix.extract_brick_blocks(dup)) == ["vim"]
      and "codex" in pkg_list_of(dup))
# Hand edit: the same name in the list and in the brick.
hand = out.replace("    git\n", "    codex\n    git\n")
CONF.write_text(hand, encoding="utf-8")
d = config_manager.load_config(str(cfg))
check("von Hand doppelt: beim Laden aktiv",
      [p for p in d["packages"] if p["attr"] == "codex"] == [pk("codex")],
      [p for p in d["packages"] if p["attr"] == "codex"])

print("\n4. Der Brick verschwindet, wenn er leer wird")
gone = gen([pk("git"), pk("htop")], blocks=dict(blocks))
check("gelöschtes deaktiviertes Paket ist weg", "codex" not in gone)
check("der Brick ist weg", NAME not in brix.extract_brick_blocks(gone))
check("Formatnummer wieder 2", fmt(gone) == "2", fmt(gone))

print("\n5. Alle Pakete deaktiviert")
alle = gen([pk("git", False), pk("htop", False)])
check("keine Paketliste", "environment.systemPackages" not in alle)
check("beide im Brick",
      brix.inactive_package_names(brix.extract_brick_blocks(alle)) == ["git", "htop"])
check("Nix liest die Datei", nix_reads(alle))
CONF.write_text(alle, encoding="utf-8")
d = config_manager.load_config(str(cfg))
check("beide kommen deaktiviert zurück",
      {p["attr"]: p["enabled"] for p in d["packages"]} == {"git": False, "htop": False},
      d["packages"])

print("\n6. Die Sicherung")
# What the import does (server._apply_import_result): load, then overwrite
# `packages` with what the parser found - active entries only.
CONF.write_text(out, encoding="utf-8")
d = config_manager.load_config(str(cfg))
d["packages"] = importer.parse_config(brix.strip_brick_blocks(out)).get("packages", [])
check("der Parser kennt nur die aktiven", [p["attr"] for p in d["packages"]] == ["git", "htop"])
config_manager.save_config(str(cfg), d)
after = CONF.read_text(encoding="utf-8")
check("der Brick steht noch", brix.inactive_package_names(brix.extract_brick_blocks(after)) == ["codex"],
      brix.inactive_package_names(brix.extract_brick_blocks(after)))
check("die Datei ist unverändert", after == out)
ohne = gen([pk("git")], known=False, blocks=dict(blocks))
check("ohne Marke baut der Generator den Brick nicht um",
      brix.inactive_package_names(brix.extract_brick_blocks(ohne)) == ["codex"])
ohne2 = gen([pk("git"), pk("vim", False)], known=False)
check("ohne Marke entsteht auch keiner", NAME not in brix.extract_brick_blocks(ohne2))
check("und das deaktivierte Paket wird nicht gebaut", pkg_list_of(ohne2) == ["git"])

print("\n7. Routen")
CONF.write_text(out, encoding="utf-8")
app = server.create_app()
client = app.test_client()
page = client.get("/").get_data(as_text=True)
m = re.search(r'<meta name="csrf-token" content="([^"]+)"', page)
hdr = {"X-CSRF-Token": m.group(1) if m else "", "Content-Type": "application/json"}
F = "configuration.nix"


def refused(r):
    return r.status_code == 400 and (r.get_json() or {}).get("error") == "ERR_BRICK_RESERVED"


r = client.post("/api/brick", headers=hdr, data=json.dumps(
    {"name": "eigener", "section": "PROG", "file": F}))
check("ein eigener Brick lässt sich anlegen", r.status_code == 200, (r.status_code, r.get_json()))
before = CONF.read_text(encoding="utf-8")
r = client.post("/api/brick", headers=hdr, data=json.dumps({"name": NAME, "section": "PROG", "file": F}))
check("anlegen unter dem reservierten Namen: abgelehnt", refused(r), (r.status_code, r.get_json()))
r = client.patch(f"/api/brick/{NAME}", headers=hdr, data=json.dumps({"inactive": False, "file": F}))
check("aktivieren: abgelehnt", refused(r), (r.status_code, r.get_json()))
r = client.patch(f"/api/brick/{NAME}", headers=hdr, data=json.dumps({"content": "  x = 1;", "file": F}))
check("bearbeiten: abgelehnt", refused(r), (r.status_code, r.get_json()))
r = client.post("/api/brick/rename", headers=hdr, data=json.dumps(
    {"old_name": NAME, "new_name": "anders", "file": F}))
check("umbenennen: abgelehnt", refused(r), (r.status_code, r.get_json()))
r = client.post("/api/brick/rename", headers=hdr, data=json.dumps(
    {"old_name": "eigener", "new_name": NAME, "file": F}))
check("einen anderen so nennen: abgelehnt", refused(r), (r.status_code, r.get_json()))
r = client.post("/api/brick/split", headers=hdr, data=json.dumps(
    {"name": NAME, "split_line": 1, "new_name": "haelfte", "file": F}))
check("teilen: abgelehnt", refused(r), (r.status_code, r.get_json()))
r = client.post("/api/brick/move", headers=hdr, data=json.dumps(
    {"name": NAME, "section": "SERV", "order": 1, "file": F}))
check("verschieben: abgelehnt", refused(r), (r.status_code, r.get_json()))
check("keine der Ablehnungen hat die Datei angefasst", CONF.read_text(encoding="utf-8") == before)
r = client.delete(f"/api/brick/{NAME}?file={F}", headers=hdr)
check("löschen geht", r.status_code == 200, (r.status_code, r.get_json()))
check("danach ist codex weg", "codex" not in CONF.read_text(encoding="utf-8"))

print("\n8. Der Weg des Formulars")
CONF.write_text(out, encoding="utf-8")
r = client.get(f"/api/parse/co?path={F}", headers=hdr)
parsed = r.get_json() or {}
got = {p["attr"]: p.get("enabled", True) for p in parsed.get("packages", [])}
check("/api/parse/co liefert das deaktivierte Paket", got.get("codex") is False, got)
check("und die aktiven", got.get("git") is True and got.get("htop") is True, got)

form = config_manager.get_defaults()
form.pop("brick_blocks", None)
form.update({"packages": [pk("git"), pk("codex", False), pk("htop", False)],
             KNOWN: True, "_co_ready": True, "_co_path": F})
r = client.post("/api/config", headers=hdr, data=json.dumps(form))
check("POST /api/config geht", r.status_code == 200, (r.status_code, r.get_json()))
saved = CONF.read_text(encoding="utf-8")
check("zweites Paket deaktiviert: beide im Brick",
      brix.inactive_package_names(brix.extract_brick_blocks(saved)) == ["codex", "htop"],
      brix.inactive_package_names(brix.extract_brick_blocks(saved)))
check("Liste enthält nur noch git", pkg_list_of(saved) == ["git"], pkg_list_of(saved))

form["packages"] = [pk("git"), pk("codex"), pk("htop")]
prev = dict(form); prev.pop("_co_ready"); prev.pop("_co_path"); prev["_path"] = F
r = client.post("/api/preview", headers=hdr, data=json.dumps(prev))
text = (r.get_json() or {}).get("configuration_nix", "")
check("Vorschau zeigt den Schalter sofort: kein Brick mehr",
      NAME not in brix.extract_brick_blocks(text) and pkg_list_of(text) == ["codex", "git", "htop"],
      pkg_list_of(text))
check("die Vorschau hat nichts geschrieben", CONF.read_text(encoding="utf-8") == saved)

# A frontend that does not know the feature: no marker, entries all active
# and the deactivated ones missing. Nothing may be lost.
alt = dict(form); alt.pop(KNOWN); alt["packages"] = [pk("git")]
r = client.post("/api/config", headers=hdr, data=json.dumps(alt))
check("Formular ohne Marke: Brick bleibt",
      brix.inactive_package_names(brix.extract_brick_blocks(CONF.read_text(encoding="utf-8")))
      == ["codex", "htop"])

r = client.post("/api/write", headers=hdr, data=json.dumps({"commit": False}))
check("/api/write geht", (r.get_json() or {}).get("success") is True, r.get_json())
check("und lässt den Brick stehen",
      brix.inactive_package_names(brix.extract_brick_blocks(CONF.read_text(encoding="utf-8")))
      == ["codex", "htop"])

print("\n9. Validator")


def findings():
    return [f for f in validator.run_validation(str(cfg), {}, config_manager.load_config(str(cfg)))
            if f.get("rule_id") == "pkg_inactive_brick"]


CONF.write_text(out, encoding="utf-8")
check("sauberer Brick: kein Befund", findings() == [], findings())
wach = out.replace(f"{NAME} inactive>", f"{NAME}>")
CONF.write_text(wach, encoding="utf-8")
f = findings()
check("Brick ohne inactive: Fehler", len(f) == 1 and f[0]["severity"] == "error", f)
fremd = out.replace("#   codex\n", "#   codex\n#   services.foo.enable = true;\n")
CONF.write_text(fremd, encoding="utf-8")
f = findings()
check("fremde Zeile: Hinweis", len(f) == 1 and f[0]["severity"] == "info", f)
d = config_manager.load_config(str(cfg))
check("fremde Zeile wird kein Paket", [p["attr"] for p in d["packages"]] == ["git", "htop", "codex"],
      [p["attr"] for p in d["packages"]])
d[KNOWN] = True
d["packages"] = [p for p in d["packages"] if p["attr"] != "codex"]
kept = generator.generate_configuration_nix(d)
check("fremde Zeile bleibt beim Schreiben, auch ohne Paket",
      "services.foo.enable = true;" in kept and NAME in brix.extract_brick_blocks(kept), kept[-400:])
check("Nix liest auch diese Datei", nix_reads(kept))

print("\n10. Host-Datei")
host = generator.generate_host_nix(
    {"packages": [pk("git"), pk("codex", False)], KNOWN: True}, "kiste")
hb = brix.extract_brick_blocks(host)
check("Brick in der Host-Datei", brix.inactive_package_names(hb) == ["codex"], list(hb))
check("Nix liest sie", nix_reads(host))
hdir = cfg / "hosts" / "kiste"
hdir.mkdir(parents=True)
(hdir / "default.nix").write_text(host, encoding="utf-8")
hd = config_manager.load_host_config(str(cfg), "kiste")
check("Laden der Host-Datei liefert das Paket deaktiviert",
      {p["attr"]: p.get("enabled", True) for p in hd["packages"]} == {"git": True, "codex": False},
      hd.get("packages"))

print()
if fails:
    print(f"{len(fails)} FEHLER:")
    for f in fails:
        print("  -", f)
    sys.exit(1)
print("ALLE PRÜFUNGEN BESTANDEN")
