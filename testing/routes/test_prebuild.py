#!/usr/bin/env python3
"""Vorschau vor dem Rebuild: was wird geladen, was wird kompiliert.

Anlass (Martin, 2026-11-07): Sein Rechner stürzte beim Bau von bambu-studio
ab, der Arbeitsspeicher reichte nicht. bambu-studio hat einen unfreien Teil,
Hydra baut so etwas nie, also kompiliert jedes nixpkgs-Update es lokal.

Nebenbei gefunden: der Vorab-Dry-Run rief `nixos-rebuild build --dry-run`,
das die neue Python-Fassung von nixos-rebuild ablehnt – die Download-Größe
wurde nie angezeigt. Und erkannt wurde nur „MiB", nix schreibt aber auch KiB
und GiB.

Geprüft wird:
  1. Der Befehl ist `dry-build`, mit Flake-Update nur im Speicher.
  2. Die Ausgabe wird gelesen: Ableitungsliste (auch Einzahl), Download in
     allen Einheiten. Grundlage ist ein echter Lauf auf nix-desktop.
  3. Die Erkennung „echtes Paket": src + pname + version. Klebe-Ableitungen
     und Hilfsskripte zählen nicht, strukturierte Attribute werden gelesen.
  4. Die Route: abgeschaltet → skipped; sonst das Ergebnis von precheck,
     mit Host und Flake-Update durchgereicht.

Lauf: nix-shell shell.nix --run "python testing/routes/test_prebuild.py"
"""
import json
import os
import re
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

from nico import config_manager, generator, prebuild, server  # noqa: E402

fails = []


def check(label, cond, got=None):
    print(("  OK   " if cond else "  FAIL ") + label
          + ("" if cond or got is None else f"  (war: {got})"))
    if not cond:
        fails.append(label)


# ── 1. Befehl ────────────────────────────────────────────────────────────────
print("\n1. Befehl")
c = prebuild.dry_build_cmd(str(cfg), True, ".#nix-desktop")
check("Flake: dry-build, kein --dry-run",
      c[:2] == ["nixos-rebuild", "dry-build"] and "--dry-run" not in c, c)
c = prebuild.dry_build_cmd(str(cfg), True, ".#h", update_flake=True)
check("Flake-Update nur im Speicher",
      "--recreate-lock-file" in c and "--no-write-lock-file" in c, c)
c = prebuild.dry_build_cmd(str(cfg), False)
check("ohne Flake: -I nixos-config=…/configuration.nix",
      c[:2] == ["nixos-rebuild", "dry-build"]
      and any(a.endswith("/configuration.nix") for a in c), c)
check("Flake-Ref mit git", prebuild.flake_ref(str(cfg), "h", True) == ".#h")
check("Flake-Ref ohne git: path:",
      prebuild.flake_ref(str(cfg), "h", False).startswith("path:"))

# ── 2. Ausgabe lesen ─────────────────────────────────────────────────────────
print("\n2. Ausgabe lesen")
# Ausschnitt aus dem echten Lauf vom 2026-11-07 (nix-desktop, nixpkgs c56a87d).
REAL = """building the system configuration...
evaluation warning: stdenv.isLinux is deprecated, use stdenv.hostPlatform.isLinux instead
these 3 derivations will be built:
  /nix/store/drfzy2qn46r8gp6rxn2d3h74n1c65rjr-options.json.drv
  /nix/store/q8gnqd152h430ppyrbllb5qbvqmaz85s-system-path.drv
  /nix/store/pjjabhg7flrs20i1yr5abljj9vxp7xby-nixos-system-nix-desktop-26.05.20261105.c56a87d.drv
these 2 paths will be fetched (16.1 KiB download, 51.2 KiB unpacked):
  /nix/store/aaaa-x
  /nix/store/bbbb-y
"""
drvs, mib = prebuild.parse_dry_build(REAL)
check("drei Ableitungen", len(drvs) == 3, drvs)
check("Pfade nach dem Abschnitt zählen nicht",
      all(d.endswith(".drv") for d in drvs), drvs)
check("KiB wird umgerechnet", abs(mib - 16.1 / 1024) < 1e-6, mib)
drvs, mib = prebuild.parse_dry_build(
    "this derivation will be built:\n  /nix/store/zz-bambu-studio-02.08.00.50.drv\n"
    "this path will be fetched (2.5 GiB download, 9 GiB unpacked):\n  /nix/store/q\n")
check("Einzahl: this derivation", drvs == ["/nix/store/zz-bambu-studio-02.08.00.50.drv"], drvs)
check("Einzahl und GiB", abs(mib - 2560) < 1e-6, mib)
check("nichts zu tun", prebuild.parse_dry_build("building the system configuration...\n")
      == ([], 0.0))

# ── 3. Erkennung ─────────────────────────────────────────────────────────────
print("\n3. Echtes Paket oder Klebe-Ableitung")
shown = {"derivations": {
    "a.drv": {"env": {"name": "etc"}},
    "b.drv": {"env": {"name": "check-link-targets.sh",
                      "src": "/nix/store/x-check-link-targets.sh"}},
    "c.drv": {"env": {"pname": "bambu-studio", "version": "02.08.00.50",
                      "src": "/nix/store/bvz-source"}},
    "d.drv": {"env": {"__json": json.dumps({"pname": "orca-slicer", "version": "2.3",
                                             "srcs": ["/nix/store/s"]})}},
    "e.drv": {"env": {}, "structuredAttrs": {"pname": "freecad", "version": "1.1",
                                              "src": "/nix/store/f"}},
}}
names = [h["name"] for h in prebuild.heavy_from_show(shown)]
check("bambu-studio erkannt", "bambu-studio" in names, names)
check("strukturierte Attribute (__json) gelesen", "orca-slicer" in names, names)
check("strukturierte Attribute (structuredAttrs) gelesen", "freecad" in names, names)
check("Klebe-Ableitung zählt nicht", "etc" not in names, names)
check("Hilfsskript mit src, ohne pname zählt nicht",
      "check-link-targets.sh" not in names, names)
check("alte Ausgabeform ohne 'derivations'-Hülle",
      [h["name"] for h in prebuild.heavy_from_show(
          {"c.drv": shown["derivations"]["c.drv"]})] == ["bambu-studio"])

# ── 4. Route ─────────────────────────────────────────────────────────────────
print("\n4. Route /api/rebuild/precheck")
(cfg / "configuration.nix").write_text(
    generator.generate_configuration_nix(config_manager.get_defaults()), encoding="utf-8")
config_manager.save_config(str(cfg), config_manager.get_defaults())
app = server.create_app()
client = app.test_client()
page = client.get("/").get_data(as_text=True)
m = re.search(r'<meta name="csrf-token" content="([^"]+)"', page)
hdr = {"X-CSRF-Token": m.group(1) if m else "", "Content-Type": "application/json"}

r = client.post("/api/rebuild/precheck", data="{}",
                headers={"Content-Type": "application/json"})
check("ohne CSRF abgelehnt", r.status_code in (400, 403), r.status_code)

calls = []
orig = prebuild.precheck
prebuild.precheck = lambda *a, **k: (calls.append((a, k)) or
                                     {"ok": True, "heavy": [{"name": "bambu-studio", "version": "1"}],
                                      "build_count": 1, "fetch_mib": 3.0})
try:
    r = client.post("/api/rebuild/precheck", headers=hdr,
                    data=json.dumps({"hostname": "nix-desktop", "update_flake": True}))
    out = r.get_json() or {}
    check("liefert das Ergebnis", r.status_code == 200
          and out.get("heavy", [{}])[0].get("name") == "bambu-studio", (r.status_code, out))
    check("ohne Flake kein Flake-Update durchgereicht",
          calls and calls[-1][1].get("update_flake") is False, calls[-1:])

    config_manager.save_app_settings({**config_manager.get_app_settings(),
                                      "prefetch_dry_run": False})
    calls.clear()
    r = client.post("/api/rebuild/precheck", headers=hdr, data="{}")
    check("abgeschaltet → skipped", (r.get_json() or {}).get("skipped") is True, r.get_json())
    check("abgeschaltet → kein Probelauf", not calls, calls)
finally:
    prebuild.precheck = orig

print()
if fails:
    print("FEHLGESCHLAGEN:", len(fails))
    for x in fails:
        print("  -", x)
    sys.exit(1)
print("Alle Prüfungen bestanden.")
