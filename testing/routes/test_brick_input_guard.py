#!/usr/bin/env python3
"""Eigene Flake-Inputs lassen sich benutzen, und ein Brick macht die Datei nicht kaputt.

Beides aus Martins produktiver Config, 2026-10-30/31. Er wollte
`nixpkgs-unstable` ergänzen und hat getan, was die Oberfläche anbietet: einen
Brick in INPX mit `nixpkgs-unstable.url = …`. Benutzen ließ sich der Input
trotzdem nicht – der Generator schrieb die Argumentliste von `outputs` aus
seinen eigenen Feldern und hängte `...` an, das alles Übrige stillschweigend
schluckt. Jede Verwendung scheiterte mit `undefined variable`.

Sein Ausweg war ein zweiter Brick in OUTX, der nur den Namen enthielt. OUTX
liegt aber *innerhalb* des outputs-Attributsatzes, nicht in der Argumentliste:
daraus wurde ungültiges Nix. Gespeichert hat NiCo das ohne ein Wort – geprüft
wurde nur die Klammerbilanz, und ein nacktes Wort ist ausgeglichen. Die Config
war einen Tag lang nicht baubar, ohne dass irgendetwas es sagte.

Geprüft wird:
  1. Ein per Brick deklarierter Input steht in der Argumentliste von outputs.
  2. Mehrere Inputs, auch in der `name = { url = … }`-Form; keine Dubletten,
     keine Namen aus Kommentaren.
  3. Die erzeugte flake.nix ist danach lesbares Nix – der Fall von Martin,
     einmal ganz durchgespielt.
  4. Ein Brick, der die Datei unlesbar macht, wird abgelehnt und nennt die
     Nix-Meldung.
  5. Eine bereits kaputte Datei bleibt bearbeitbar – sonst sperrt der Schutz
     genau die Reparatur aus, für die man ihn braucht.
  6. Ein gültiger Brick geht weiterhin durch.

Lauf: nix-shell shell.nix --run "python testing/routes/test_brick_input_guard.py"
"""
import json
import os
import re
import shutil
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

from nico import config_manager, generator, server, validator  # noqa: E402
from nico.brix import format_brick, input_names_in_bricks  # noqa: E402

fails = []


def check(label, cond, got=None):
    print(("  OK   " if cond else "  FAIL ") + label
          + ("" if cond or got is None else f"  (war: {got})"))
    if not cond:
        fails.append(label)


def brick(section, order, name, body):
    return {"section": section, "order": order,
            "text": format_brick(section, order, name, body)}


def out_args(flake_text):
    m = re.search(r"outputs\s*=\s*\{([^}]*)\}", flake_text)
    return [a.strip() for a in m.group(1).split(",") if a.strip()] if m else []


# ── 1. Ein Input aus einem Brick ─────────────────────────────────────────────
print("\n1. Input aus einem INPX-Brick")
data = config_manager.get_defaults()
data.update({
    "flakes": True, "hostname": "nix-vm",
    "flake_hosts": [{"name": "nix-vm"}],
    "flake_brick_blocks": {
        "unstable-url": brick("INPX", 2, "unstable-url",
                              '  nixpkgs-unstable.url = "github:NixOS/nixpkgs/nixos-unstable";'),
    },
})
flake = generator.generate_flake_nix(data, nixos_dir=str(cfg))
args = out_args(flake)
check("nixpkgs-unstable steht in der Argumentliste", "nixpkgs-unstable" in args, args)
check("self und nixpkgs weiterhin da",
      "self" in args and "nixpkgs" in args, args)

# ── 2. Mehrere Formen, keine Dubletten ───────────────────────────────────────
print("\n2. Mehrere Inputs, verschiedene Schreibweisen")
blocks = {
    "a": brick("INPX", 1, "a", '  nixpkgs-unstable.url = "github:x/y";'),
    "b": brick("INPX", 2, "b",
               '  yandex-browser.url = "github:Teu5us/nix-yandex-browser";\n'
               '  yandex-browser.inputs.nixpkgs.follows = "nixpkgs";'),
    "c": brick("INPX", 3, "c", '  sops-nix = {\n    url = "github:Mic92/sops-nix";\n  };'),
    "d": brick("INPX", 4, "d", '  # auskommentiert.url = "github:nope/nope";'),
    "e": brick("OUTX", 1, "e", '  formatter.x86_64-linux = pkgs.nixfmt;'),
}
names = input_names_in_bricks(blocks)
check("alle drei echten Inputs erkannt",
      names == ["nixpkgs-unstable", "yandex-browser", "sops-nix"], names)
check("Kommentarzeile zählt nicht", "auskommentiert" not in names, names)
check("andere Abschnitte zählen nicht", "formatter" not in names, names)

# ── 3. Martins Fall, ganz durchgespielt ──────────────────────────────────────
print("\n3. Der Fall aus der Praxis: Input deklarieren und verwenden")
data = config_manager.get_defaults()
data.update({
    "flakes": True, "hostname": "nix-vm",
    "flake_hosts": [{"name": "nix-vm"}],
    "flake_brick_blocks": {
        "unstable-url": brick("INPX", 2, "unstable-url",
                              '  nixpkgs-unstable.url = "github:NixOS/nixpkgs/nixos-unstable";'),
        "host-nix-vm": brick("HOST:nix-vm", 1, "host-nix-vm",
                             '          system = "x86_64-linux";\n'
                             '          specialArgs = {\n'
                             '            pkgs-unstable = import nixpkgs-unstable '
                             '{ system = "x86_64-linux"; };\n'
                             '          };\n'
                             '          modules = [ ./configuration.nix ];'),
    },
})
flake = generator.generate_flake_nix(data, nixos_dir=str(cfg))
if shutil.which("nix-instantiate"):
    err = validator.nix_parse_error(flake)
    check("die erzeugte flake.nix ist lesbares Nix", err is None, err)
else:
    print("  --   nix-instantiate fehlt, Parse-Prüfung übersprungen")

# ── 4.–6. Der Schutz beim Speichern ──────────────────────────────────────────
print("\n4. Brick, der die Datei unlesbar macht")
if not shutil.which("nix-instantiate"):
    print("  --   nix-instantiate fehlt, Rest übersprungen")
else:
    (cfg / "configuration.nix").write_text(
        generator.generate_configuration_nix(config_manager.get_defaults()),
        encoding="utf-8")
    config_manager.save_config(str(cfg), config_manager.get_defaults())

    app = server.create_app()
    client = app.test_client()
    page = client.get("/").get_data(as_text=True)
    m = re.search(r'<meta name="csrf-token" content="([^"]+)"', page)
    hdr = {"X-CSRF-Token": m.group(1) if m else "", "Content-Type": "application/json"}

    r = client.post("/api/brick", headers=hdr, data=json.dumps(
        {"name": "kaputt", "section": "SERV", "file": "configuration.nix"}))
    check("Brick anlegen geht", r.status_code == 200, (r.status_code, r.get_json()))

    r = client.patch("/api/brick/kaputt", headers=hdr, data=json.dumps(
        {"content": "nixpkgs-unstable", "file": "configuration.nix"}))
    out = r.get_json()
    check("nacktes Wort wird abgelehnt", r.status_code == 400, (r.status_code, out))
    check("mit dem richtigen Fehlercode",
          out.get("error") == "ERR_BRICK_BREAKS_FILE", out)
    check("und der Nix-Meldung dazu", "error:" in (out.get("detail") or ""), out)
    on_disk = (cfg / "configuration.nix").read_text(encoding="utf-8")
    check("die Datei ist unverändert", "nixpkgs-unstable" not in on_disk)

    print("\n5. Eine bereits kaputte Datei bleibt bearbeitbar")
    broken = on_disk.replace("{\n", "{\n  kaputt = ;\n", 1)
    (cfg / "configuration.nix").write_text(broken, encoding="utf-8")
    r = client.patch("/api/brick/kaputt", headers=hdr, data=json.dumps(
        {"content": "  services.openssh.enable = true;", "file": "configuration.nix"}))
    check("Reparatur wird nicht blockiert", r.status_code == 200,
          (r.status_code, r.get_json()))
    (cfg / "configuration.nix").write_text(on_disk, encoding="utf-8")

    print("\n6. Gültiger Brick")
    r = client.patch("/api/brick/kaputt", headers=hdr, data=json.dumps(
        {"content": "  services.avahi.enable = true;", "file": "configuration.nix"}))
    check("geht durch", r.status_code == 200, (r.status_code, r.get_json()))
    check("steht in der Datei",
          "services.avahi.enable" in (cfg / "configuration.nix").read_text(encoding="utf-8"))

print()
if fails:
    print("FEHLGESCHLAGEN:", len(fails))
    for x in fails:
        print("  -", x)
    sys.exit(1)
print("Alle Prüfungen bestanden.")
