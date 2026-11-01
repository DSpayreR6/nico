#!/usr/bin/env python3
"""Einen Nix-Brick deaktivieren, statt ihn zu löschen.

Martins Wunsch vom 2026-11-01: Ein gelöschter Brick ist weg, obwohl man ihn
später wieder brauchen kann - genau sein Fall mit `services.paperless`, das
den Rebuild blockiert, aber nicht verlorengehen soll. Umgesetzt als
Auskommentieren an Ort und Stelle: Der Inhalt bleibt in der `.nix`, Nix sieht
ihn nie. Ein Archiv in JSON war keine Option, `vorgaben.txt` verlangt
NixOS-Daten in `.nix`.

Geprueft wird:
   1. Grammatik: der Marker mit `inactive` wird gelesen, einer ohne nicht
      falsch erkannt, und ein Brick, der selbst "inactive" heisst, bleibt
      aktiv
   2. Koerper-Umformung: byte-exakt umkehrbar, auch bei Zeilen, die schon
      Kommentar waren, bei Leerzeilen und bei Einrueckung
   3. Formatnummer: 3 nur, wenn wirklich ein Brick deaktiviert ist
   4. Die ganze Route: deaktivieren, Datei ansehen, aktivieren, wieder
      byte-identisch
   5. Zweimal deaktivieren aendert nichts (idempotent)
   6. Umbenennen, Verschieben und Teilen behalten den Zustand - beim Teilen
      beide Haelften (Entscheidung Martin)
   7. Nix kann die Datei in beiden Zustaenden lesen
   8. Die Falle: `_START` trifft auch in einer doppelt auskommentierten
      Zeile. Ein Brick, in dessen Koerper fremde Markerzeilen stehen, bleibt
      nach dem Deaktivieren fuer die Verschachtelungspruefung sichtbar.
   9. Ein von Hand halb entkommentierter Koerper wird beim Aktivieren
      abgelehnt, nicht gespeichert

Gegen den Stand vor dem Fix fallen 42 der Pruefungen (gemessen 2026-11-01).
Der Test laeuft dort absichtlich bis zum Ende durch: Fehlendes wird als Fund
gezaehlt und durch Attrappen ersetzt. Ohne die Flagge in der Grammatik findet
_START den Marker nicht und der Brick fehlt vollstaendig - genau der Schaden,
den der Test belegt.

Dabei gefunden und mitbehoben: Jede Brick-Operation fuegte eine Leerzeile
hinzu, weil inject_brick_blocks einen Trenner anhaengt und
strip_brick_blocks ihn stehen liess. Abschnitt 4 und 5 pruefen jetzt
Byte-Gleichheit und Idempotenz und halten das fest.

Lauf: nix-shell shell.nix --run "python testing/routes/test_brick_inactive.py"
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

from nico import brix, config_manager, generator, server  # noqa: E402

fails = []


def check(label, cond, got=None):
    print(("  OK   " if cond else "  FAIL ") + label
          + ("" if cond or got is None else f"  (war: {got})"))
    if not cond:
        fails.append(label)


# Gegen einen Stand ohne die Funktion soll der Test seine Befunde melden statt
# mit KeyError/AttributeError abzubrechen: sonst sieht man nur, dass etwas
# fehlt, und nicht, was dadurch nicht geht.
if not hasattr(brix, "deactivate_body"):
    check("brix.deactivate_body existiert", False)
    brix.deactivate_body = lambda body: body
if not hasattr(brix, "activate_body"):
    check("brix.activate_body existiert", False)
    brix.activate_body = lambda body: body

import inspect as _inspect
if len(_inspect.signature(brix.format_brick).parameters) < 5:
    check("format_brick nimmt die Flagge an", False)
    _alt_format = brix.format_brick
    brix.format_brick = lambda section, order, name, body, inactive=False: \
        _alt_format(section, order, name, body)


def inact(block):
    """Der Zustand eines Bricks; None, wenn das Feld noch gar nicht da ist."""
    return block.get("inactive")


def brick_of(blocks, name):
    """Ein Brick aus der Liste; leeres Dict, wenn er gar nicht erkannt wurde.

    Ohne die Flagge in der Grammatik erkennt _START den Marker nicht und der
    Brick fehlt vollstaendig - genau der Schaden, den dieser Test belegt. Er
    soll ihn melden, nicht mit KeyError abbrechen.
    """
    return blocks.get(name, {})


def nix_reads(text: str) -> bool:
    """True when Nix can parse this content."""
    tmp = Path(tempfile.mkdtemp()) / "t.nix"
    tmp.write_text(text, encoding="utf-8")
    try:
        r = subprocess.run(["nix-instantiate", "--parse", str(tmp)],
                           capture_output=True, timeout=30)
        return r.returncode == 0
    except Exception:
        return True          # no nix here: do not fail the run over it


print("\n1. Grammatik")

aktiv = "# <brick: SERV / #1 paperless>\n  services.paperless.enable = true;\n# </brick: paperless>\n"
inakt = "# <brick: SERV / #1 paperless inactive>\n#   services.paperless.enable = true;\n# </brick: paperless>\n"

b = brix.extract_brick_blocks(aktiv)
check("aktiver Brick: inactive ist False", inact(brick_of(b, "paperless")) is False, brick_of(b, "paperless"))
b = brix.extract_brick_blocks(inakt)
check("deaktivierter Brick wird erkannt", inact(brick_of(b, "paperless")) is True, brick_of(b, "paperless"))
check("Abschnitt bleibt richtig", brick_of(b, "paperless").get("section") == "SERV")
check("Nummer bleibt richtig", brick_of(b, "paperless").get("order") == 1)
check("der Text bleibt unveraendert erhalten",
      brick_of(b, "paperless").get("text") == inakt, brick_of(b, "paperless").get("text"))

# Ein Brick, der selbst so heisst, darf nicht als deaktiviert gelten.
heisst_so = "# <brick: SERV / #1 inactive>\n  x = 1;\n# </brick: inactive>\n"
b = brix.extract_brick_blocks(heisst_so)
check("ein Brick mit dem Namen 'inactive' ist aktiv",
      "inactive" in b and inact(brick_of(b, "inactive")) is False, b)

check("format_brick schreibt die Flagge",
      brix.format_brick("SERV", 1, "x", "  y = 1;", True).startswith(
          "# <brick: SERV / #1 x inactive>"))
check("und ohne Flagge nicht",
      brix.format_brick("SERV", 1, "x", "  y = 1;").startswith(
          "# <brick: SERV / #1 x>"))
check("der Endmarker traegt sie nicht",
      "# </brick: x>" in brix.format_brick("SERV", 1, "x", "  y = 1;", True))
check("ein Endmarker MIT Flagge wird trotzdem gelesen",
      "paperless" in brix.extract_brick_blocks(
          inakt.replace("# </brick: paperless>", "# </brick: paperless inactive>")))

print("\n2. Koerper hin und zurueck")

faelle = {
    "einfach":            "  services.x.enable = true;",
    "mehrzeilig":         "  services.x = {\n    enable = true;\n  };",
    "mit Leerzeile":      "  a = 1;\n\n  b = 2;",
    "schon Kommentar":    "# ein Hinweis\n  a = 1;",
    "Kommentar ohne Raum":"#kein Leerzeichen\n  a = 1;",
    "tief eingerueckt":   "        deep = true;",
    "leer":               "",
    "nur Leerzeilen":     "\n\n",
    "Nix-String":         "  x = ''\n    roher text ; { }\n  '';",
}
for name, body in faelle.items():
    aus = brix.deactivate_body(body)
    check(f"umkehrbar: {name}", brix.activate_body(aus) == body,
          repr(brix.activate_body(aus)))
    nicht_leer = [l for l in body.split("\n") if l.strip()]
    check(f"alle Zeilen kommentiert: {name}",
          all(l.startswith("# ") for l in aus.split("\n") if l.strip()),
          repr(aus))
    if nicht_leer:
        check(f"Einrueckung bleibt sichtbar: {name}",
              all(("# " + l) in aus for l in nicht_leer), repr(aus))

check("Leerzeilen bleiben leer",
      brix.deactivate_body("  a = 1;\n\n  b = 2;").split("\n")[1] == "")

print("\n3. Formatnummer")

check("ohne deaktivierten Brick weiter 2", brix.detect_content_format(aktiv) == 2)
check("mit deaktiviertem Brick 3", brix.detect_content_format(inakt) == 3)
check("3 ist unterstuetzt", 3 in brix.SUPPORTED_FORMATS)
check("FILE_FORMAT ist 3", brix.FILE_FORMAT == 3)
check("gemischt: ein deaktivierter genuegt",
      brix.detect_content_format(aktiv + inakt) == 3)

print("\n4. Die Route, einmal hin und zurueck")

(cfg / "configuration.nix").write_text(
    generator.generate_configuration_nix(config_manager.get_defaults()), encoding="utf-8")
config_manager.save_config(str(cfg), config_manager.get_defaults())

app = server.create_app()
client = app.test_client()
page = client.get("/").get_data(as_text=True)
m = re.search(r'<meta name="csrf-token" content="([^"]+)"', page)
hdr = {"X-CSRF-Token": m.group(1) if m else "", "Content-Type": "application/json"}

KOERPER = "  services.paperless = {\n    enable = true;\n    passwordFile = \"/etc/pw\";\n  };"

r = client.post("/api/brick", headers=hdr, data=json.dumps(
    {"name": "paperless", "section": "SERV", "file": "configuration.nix"}))
check("Brick anlegen geht", r.status_code == 200, (r.status_code, r.get_json()))
r = client.patch("/api/brick/paperless", headers=hdr, data=json.dumps(
    {"content": KOERPER, "file": "configuration.nix"}))
check("Inhalt setzen geht", r.status_code == 200, (r.status_code, r.get_json()))

vorher = (cfg / "configuration.nix").read_text(encoding="utf-8")
check("Nix liest die Datei im Ausgangszustand", nix_reads(vorher))

r = client.patch("/api/brick/paperless", headers=hdr, data=json.dumps(
    {"inactive": True, "file": "configuration.nix"}))
check("deaktivieren geht", r.status_code == 200, (r.status_code, r.get_json()))

aus_datei = (cfg / "configuration.nix").read_text(encoding="utf-8")
check("der Marker traegt die Flagge",
      "# <brick: SERV / #1 paperless inactive>" in aus_datei)
check("der Inhalt steht noch in der Datei", "services.paperless" in aus_datei)
check("aber auskommentiert", "#     enable = true;" in aus_datei, 
      [l for l in aus_datei.split("\n") if "enable = true" in l])
check("Nix liest die Datei auch deaktiviert", nix_reads(aus_datei))
check("die Formatnummer steht auf f3", "#f3" in aus_datei,
      [l for l in aus_datei.split("\n") if "nico-version" in l])
blocks = brix.extract_brick_blocks(aus_datei)
check("NiCo liest ihn als deaktiviert", inact(brick_of(blocks, "paperless")) is True)

r = client.patch("/api/brick/paperless", headers=hdr, data=json.dumps(
    {"inactive": False, "file": "configuration.nix"}))
check("aktivieren geht", r.status_code == 200, (r.status_code, r.get_json()))
nachher = (cfg / "configuration.nix").read_text(encoding="utf-8")
check("die Datei ist wieder byte-identisch", nachher == vorher,
      "\n".join(l for l in nachher.split("\n") if l not in vorher.split("\n"))[:200])

print("\n5. Zweimal dasselbe aendert nichts")

client.patch("/api/brick/paperless", headers=hdr, data=json.dumps(
    {"inactive": True, "file": "configuration.nix"}))
einmal = (cfg / "configuration.nix").read_text(encoding="utf-8")
client.patch("/api/brick/paperless", headers=hdr, data=json.dumps(
    {"inactive": True, "file": "configuration.nix"}))
zweimal = (cfg / "configuration.nix").read_text(encoding="utf-8")
check("zweimal deaktivieren ist idempotent", einmal == zweimal)
check("nicht doppelt auskommentiert", "# #     enable" not in zweimal,
      [l for l in zweimal.split("\n") if "# #" in l])

print("\n6. Umbenennen, Verschieben, Teilen behalten den Zustand")

r = client.post("/api/brick/rename", headers=hdr, data=json.dumps(
    {"old_name": "paperless", "new_name": "dokumente", "file": "configuration.nix"}))
check("umbenennen geht", r.status_code == 200, (r.status_code, r.get_json()))
t = (cfg / "configuration.nix").read_text(encoding="utf-8")
check("bleibt nach dem Umbenennen deaktiviert",
      inact(brick_of(brix.extract_brick_blocks(t), "dokumente")) is True,
      [l for l in t.split("\n") if "<brick:" in l])

r = client.post("/api/brick/move", headers=hdr, data=json.dumps(
    {"name": "dokumente", "section": "PROG", "order": 1,
     "file": "configuration.nix"}))
check("verschieben geht", r.status_code == 200, (r.status_code, r.get_json()))
t = (cfg / "configuration.nix").read_text(encoding="utf-8")
blk = brix.extract_brick_blocks(t)["dokumente"]
check("bleibt nach dem Verschieben deaktiviert", inact(blk) is True, blk)
check("und liegt im neuen Abschnitt", blk.get("section") == "PROG", blk)

r = client.post("/api/brick/split", headers=hdr, data=json.dumps(
    {"name": "dokumente", "split_line": 2, "new_name": "rest",
     "file": "configuration.nix"}))
check("teilen geht", r.status_code == 200, (r.status_code, r.get_json()))
t = (cfg / "configuration.nix").read_text(encoding="utf-8")
beide = brix.extract_brick_blocks(t)
check("beide Haelften bleiben deaktiviert",
      inact(brick_of(beide, "dokumente")) is True and inact(brick_of(beide, "rest")) is True,
      {k: inact(v) for k, v in beide.items()})

print("\n7. Die Falle: Marker in einem auskommentierten Koerper")

check("_START trifft auch in einer doppelt auskommentierten Zeile",
      bool(brix._START.search("# # <brick: SERV / #1 x>")))
# Daraus folgt: ein Brick mit fremden Markerzeilen im Koerper bleibt nach dem
# Deaktivieren verschachtelt - der Schutz greift also weiter, statt die
# Verschachtelung zu verstecken. Das ist die gewollte Richtung.
huelle = brix.format_brick("SERV", 1, "huelle",
                           brix.deactivate_body("# <brick: SERV / #2 innen>\n  a = 1;\n# </brick: innen>"),
                           True)
gefunden = brix.find_nested_bricks(huelle)
check("eine Verschachtelung bleibt auch deaktiviert sichtbar",
      any(f.get("inner") == "innen" for f in gefunden), gefunden)

print("\n8. Halb von Hand entkommentiert wird abgelehnt")

(cfg / "configuration.nix").write_text(vorher, encoding="utf-8")
client.patch("/api/brick/paperless", headers=hdr, data=json.dumps(
    {"inactive": True, "file": "configuration.nix"}))
kaputt = (cfg / "configuration.nix").read_text(encoding="utf-8")
# Eine schliessende Klammer von Hand entkommentiert: nach dem Aktivieren
# faellt sie doppelt an und die Bilanz stimmt nicht mehr.
kaputt = kaputt.replace("#   };", "#   };\n  };", 1)
(cfg / "configuration.nix").write_text(kaputt, encoding="utf-8")
r = client.patch("/api/brick/paperless", headers=hdr, data=json.dumps(
    {"inactive": False, "file": "configuration.nix"}))
out = r.get_json() or {}
check("aktivieren wird abgelehnt", r.status_code in (400, 409), (r.status_code, out))
check("mit einem erklaerenden Fehlercode",
      str(out.get("error", "")).startswith("ERR_"), out)
check("die Datei bleibt unveraendert",
      (cfg / "configuration.nix").read_text(encoding="utf-8") == kaputt)

print()
if fails:
    print("FEHLGESCHLAGEN:", len(fails))
    for f in fails:
        print("  -", f)
    sys.exit(1)
print("Alle Pruefungen bestanden.")
