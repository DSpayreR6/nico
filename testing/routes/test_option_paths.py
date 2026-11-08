"""Test: NiCos Optionspfade gegen den echten NixOS-Optionssatz.

Hintergrund (Martin, 2026-11-05; manifest.md): NiCo bildet NixOS-Optionspfade von
Hand ab. Benennt NixOS eine um oder entfernt sie, merkt das sonst erst ein
Nutzer. Am 2026-11-08 ist genau das aufgefallen: `boot.initrd.systemd.enable`
schrieb NiCo weiter, nixpkgs lehnte die Option längst ab.

Die Pfade werden aus `importer.py` und `generator.py` gelesen, nicht von Hand
gelistet, sonst veraltet die Liste wie die Abbildung selbst. Zusammengesetzte
Pfade (`users.users.{name}`) und Optionen innerhalb eines Blocks sieht der Test
nicht.

Quelle ist das nixpkgs des laufenden Systems. Ohne dessen lokale Kopie wird der
Test übersprungen.

    python testing/routes/test_option_paths.py            prüfen
    python testing/routes/test_option_paths.py --update   Referenz neu schreiben

Läuft ohne Server und Browser, etwa zehn Sekunden.
"""
import json, pathlib, re, subprocess, sys

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from nico import validator
from nico.nix_parser import KNOWN_PREFIXES

BASELINE = pathlib.Path(__file__).with_name("option_paths.json")

_PATH = r"[a-zA-Z_][\w-]*(?:\.[a-zA-Z_][\w-]*)+"
_PREFIXES = KNOWN_PREFIXES - {"imports"}

# Names NiCo only reads, so that older configs still import. They may be gone
# from nixpkgs; what they must not do is come back into the generator.
LEGACY_READ_ONLY = {
    "boot.cleanTmpDir",
    "boot.initrd.systemd.enable",
    "boot.tmpOnTmpfs",
    "boot.tmpOnTmpfsSize",
    "hardware.opengl.driSupport32Bit",
    "hardware.opengl.enable",
    "services.xserver.desktopManager.gnome.enable",
    "services.xserver.desktopManager.plasma5.enable",
    "services.xserver.displayManager.autoLogin.enable",
    "services.xserver.displayManager.autoLogin.user",
    "services.xserver.displayManager.gdm.enable",
}

fails = []
def check(label, cond, got=None):
    print(("  OK   " if cond else "  FAIL ") + label + ("" if cond or got is None else f"  (war: {got})"))
    if not cond: fails.append(label)


def relevant(path: str) -> bool:
    # nixpkgs.url is a flake input, not an option.
    return (path.split(".")[0] in _PREFIXES and not path.startswith("users.users.")
            and path != "nixpkgs.url")


def collect() -> tuple:
    """(read by the importer, written by the generator)."""
    imp = (ROOT / "nico" / "importer.py").read_text(encoding="utf-8")
    gen = (ROOT / "nico" / "generator.py").read_text(encoding="utf-8")
    read = {p for p in re.findall(rf"'({_PATH})'", imp) if relevant(p)}
    written = {p for p in re.findall(rf"""(?:["']|^)\s{{2,}}({_PATH})\s*=""", gen, re.M) if relevant(p)}
    return read, written


def nix_eval(paths: list) -> "dict | None":
    try:
        info = json.loads(subprocess.run(["nixos-version", "--json"], capture_output=True,
                                         text=True, timeout=10).stdout)
        rev = info["nixpkgsRevision"]
    except (OSError, ValueError, KeyError, subprocess.TimeoutExpired):
        return None
    wanted = json.dumps(json.dumps(paths)).replace("$", "\\$")
    expr = f'''
      let
        f = builtins.getFlake "github:NixOS/nixpkgs/{rev}";
        lib = f.lib;
        options = (lib.nixosSystem {{
          system = "x86_64-linux";
          modules = [ {{ boot.loader.grub.enable = false;
                        fileSystems."/" = {{ device = "/dev/null"; fsType = "ext4"; }};
                        system.stateVersion = "26.05"; }} ];
        }}).options;
        safe = v: d: let r = builtins.tryEval v; in if r.success then r.value else d;
        describe = p:
          # A path may run on below an option: nix.settings is one option
          # whose type holds the rest. Nothing further is knowable from here.
          let o = builtins.foldl'
                (cur: seg:
                  if cur == null || cur._nicoInside or false then cur
                  else if lib.isOption cur then {{ _nicoInside = true; visible = safe (cur.visible or true) true; }}
                  else cur.${{seg}} or null)
                options (lib.splitString "." p);
          in
          if o == null then {{ kind = "missing"; }}
          else if o._nicoInside or false then {{ kind = "inside"; inherit (o) visible; }}
          else if lib.isOption o then {{
            kind = "option";
            type = safe (o.type.description or "") "";
            visible = safe (o.visible or true) true;
          }}
          else {{ kind = "namespace"; }};
      in {{
        source = f.outPath;
        paths = lib.genAttrs (builtins.fromJSON {wanted}) describe;
      }}'''
    try:
        r = subprocess.run(["nix", "--offline", "--extra-experimental-features", "nix-command flakes",
                            "eval", "--json", "--impure", "--expr", expr],
                           capture_output=True, text=True, timeout=180)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if r.returncode != 0:
        print(r.stderr[-600:])
        return None
    return json.loads(r.stdout)


read, written = collect()
print(f"{len(read)} gelesene, {len(written)} geschriebene Pfade")
check("Einsammeln findet Pfade", len(read) > 50 and len(written) > 30, (len(read), len(written)))
check("bekannter Pfad gefunden", "boot.plymouth.enable" in written)

print("Neue und alte Namen beim Einlesen")
from nico import config_manager, generator, importer

data = dict(config_manager.DEFAULT_NIXOS_CONFIG)
data.update(desktop_environment="gnome", opengl=True, opengl_32bit=True)
out = generator.generate_configuration_nix(data)
for name in ("services.desktopManager.gnome.enable", "services.displayManager.gdm.enable",
             "hardware.graphics.enable", "hardware.graphics.enable32Bit"):
    check(f"Generator schreibt {name}", f"{name} = true;" in out, out)
back = importer.parse_config(out)
check("Roundtrip: GNOME erkannt", back.get("desktop_environment") == "gnome", back.get("desktop_environment"))
check("Roundtrip: Grafik erkannt", back.get("opengl") is True and back.get("opengl_32bit") is True)
check("Roundtrip: nichts bleibt als Rest", "gdm" not in importer.build_rest_brix(out, {})
      and "hardware.graphics" not in importer.build_rest_brix(out, {}))

OLD = """{ config, pkgs, ... }:
{
  services.xserver.enable = true;
  services.xserver.displayManager.gdm.enable = true;
  services.xserver.desktopManager.gnome.enable = true;
  hardware.opengl.enable = true;
  hardware.opengl.driSupport32Bit = true;
}
"""
back = importer.parse_config(OLD)
check("alte Config: GNOME erkannt", back.get("desktop_environment") == "gnome", back.get("desktop_environment"))
check("alte Config: Grafik erkannt", back.get("opengl") is True and back.get("opengl_32bit") is True)
rest = importer.build_rest_brix(OLD, {})
check("alte Config: nichts bleibt als Rest", "gdm" not in rest and "opengl" not in rest, rest)

result = nix_eval(sorted(read | written))
if result is None:
    print("ÜBERSPRUNGEN: nixpkgs des laufenden Systems nicht auswertbar.")
    sys.exit(1 if fails else 0)

info = result["paths"]
removed = validator._removed_options(pathlib.Path(result["source"]))


def gone(path: str) -> bool:
    segments = tuple(path.split("."))
    return any(segments[:n] in removed for n in range(1, len(segments) + 1))


current = {p: i["type"] for p, i in sorted(info.items())
           if i["kind"] == "option" and not gone(p) and i.get("visible") is not False}

if "--update" in sys.argv:
    BASELINE.write_text(json.dumps(current, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Referenz geschrieben: {len(current)} Pfade")

print("Geschrieben von NiCo")
for p in sorted(written):
    i = info[p]
    check(f"{p} existiert", i["kind"] != "missing")
    check(f"{p} nicht entfernt", not gone(p))
    check(f"{p} nicht veraltet", i.get("visible") is not False)

print("Gelesen von NiCo")
for p in sorted(read - written):
    if p in LEGACY_READ_ONLY:
        continue
    i = info[p]
    check(f"{p} gibt es noch", i["kind"] != "missing" and not gone(p)
          and i.get("visible") is not False, i)

print("Altnamen")
for p in sorted(LEGACY_READ_ONLY):
    check(f"{p} wird nicht geschrieben", p not in written)
    check(f"{p} wird noch gelesen", p in read)

print("Typen gegen die Referenz")
baseline = json.loads(BASELINE.read_text(encoding="utf-8")) if BASELINE.exists() else {}
check("Referenz vorhanden", bool(baseline))
for p, t in sorted(current.items()):
    check(f"{p}: Typ unverändert", baseline.get(p) == t, (baseline.get(p), t))

print()
if fails:
    print(f"{len(fails)} FEHLER:", *fails, sep="\n  ")
    sys.exit(1)
print("Alle Prüfungen bestanden.")
