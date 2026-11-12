"""Browser-Test: eine nicht abbildbare Datei erscheint im Raw-Editor statt im Formular.

Hintergrund (Revision 2026-11-10, Paket 5a): Eine `configuration.nix` der Form
`{ … }: let … in { … }` kann NiCo nicht neu erzeugen, ohne die let-Bindungen
zu verlieren. Sie bleibt deshalb unangetastet. In der Oberfläche heißt das
(Entscheidung Martin 2026-11-12, vorläufig): kein Formular, an seiner Stelle
der Raw-Editor mit einem Hinweis darüber.

Die Wegwerf-Config legt dieser Test selbst an – er braucht eine, die es sonst
nirgends gibt.

Geprüft wird, was sich nur im Browser zeigt:

  * beim Start steht statt des Formulars der Raw-Editor da, mit dem Text der
    Datei und dem Hinweis links
  * der Panel-Schalter ist weg (es gibt kein Panel, in das man wechseln könnte)
  * Speichern im Raw-Editor schreibt genau den Text aus dem Editor
  * die flake.nix mit `@inputs` öffnet sich genauso
  * eine abbildbare Datei daneben zeigt weiter ihr Formular
  * nach all dem ist die let-Config bis auf die eigene Änderung unverändert
  * kein __TODO__ in der Oberfläche

Aufruf wie in testing/ui/README.md beschrieben; XDG_CONFIG_HOME wird hier
selbst gesetzt.
"""
import json, os, pathlib, sys, tempfile, threading, time
ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

xdg = pathlib.Path(tempfile.mkdtemp(prefix="nico-xdg-"))
cfg = pathlib.Path(tempfile.mkdtemp(prefix="nico-cfg-"))
os.environ["XDG_CONFIG_HOME"] = str(xdg)
os.environ["XDG_CACHE_HOME"] = str(xdg / "cache")
(xdg / "nico").mkdir(parents=True)
(xdg / "nico" / "settings.json").write_text(json.dumps(
    {"nixos_config_dir": str(cfg), "language": "de", "git_sync": False,
     "git_status_only": False}), encoding="utf-8")

LET = '''{ config, pkgs, ... }:
let
  unstable = import <nixos-unstable> { };
in
{
  networking.hostName = "let-pc";
  services.nginx.enable = true;
}
'''
FLAKE = '''{
  description = "test";
  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-25.05";
  };
  outputs = { self, nixpkgs, ... }@inputs: {
    nixosConfigurations = {
      alpha = nixpkgs.lib.nixosSystem {
        system = "x86_64-linux";
        modules = [ ./configuration.nix ];
      };
    };
  };
}
'''
from nico import generator
from nico.server import create_app

(cfg / "configuration.nix").write_text(LET, encoding="utf-8")
(cfg / "flake.nix").write_text(FLAKE, encoding="utf-8")
(cfg / "hosts" / "alpha").mkdir(parents=True)
# A host file as NiCo writes it – an unstamped one opens the type dialog,
# which is a different matter.
(cfg / "hosts" / "alpha" / "default.nix").write_text(
    generator.generate_host_nix({"hostname": "alpha"}, "alpha"), encoding="utf-8")

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.service import Service

PORT = 8799
app = create_app()
threading.Thread(target=lambda: app.run(port=PORT, threaded=True), daemon=True).start()
time.sleep(2)

opts = webdriver.ChromeOptions()
for f in ("--headless=new", "--no-sandbox", "--disable-dev-shm-usage", "--window-size=1600,1200"):
    opts.add_argument(f)
opts.binary_location = os.environ["CHROME_BIN"]
d = webdriver.Chrome(service=Service(os.environ["CHROMEDRIVER"]), options=opts)

fails = []
def check(label, cond):
    try:
        ok = bool(cond() if callable(cond) else cond)
        note = ""
    except Exception as exc:
        ok, note = False, f"  ({type(exc).__name__}: {exc})"
    print(("  OK   " if ok else "  FAIL ") + label + note)
    if not ok:
        fails.append(label)


def hidden(el_id):
    el = d.find_element(By.ID, el_id)
    return "hidden" in (el.get_attribute("class") or "") or not el.is_displayed()


def editor_text():
    return d.execute_script(
        "return document.getElementById('raw-file-editor')?.value ?? null")


def open_file(path):
    d.execute_script(
        "Sidebar.selectFile(arguments[0], arguments[0].split('/').pop(), {force: true})", path)
    time.sleep(2.5)


def dismiss_dialogs():
    """Close whatever start dialog a fresh throwaway config brings up."""
    d.execute_script("""
      document.querySelectorAll('.overlay:not(.hidden)').forEach(o => {
        if (o.id) o.classList.add('hidden'); else o.remove();
      });""")


try:
    d.get(f"http://127.0.0.1:{PORT}/")
    time.sleep(6)
    dismiss_dialogs()

    print("\n1. Start mit einer let-Config")
    check("das Formular ist nicht zu sehen", lambda: hidden("config-form"))
    check("der Raw-Editor zeigt den Text der Datei", lambda: editor_text() == LET)
    check("der Hinweis steht links daneben", lambda: not hidden("unmappable-hint"))
    check("… und sagt, dass die Datei unverändert bleibt",
          lambda: "unverändert" in d.find_element(By.ID, "unmappable-hint").text)
    check("der Hinweis „Panel deaktiviert“ steht nicht dabei",
          lambda: hidden("panel-off-hint"))
    check("der Panel-Schalter ist weg", lambda: hidden("panel-toggle"))
    check("die Datei auf der Platte ist unverändert",
          lambda: (cfg / "configuration.nix").read_text(encoding="utf-8") == LET)

    print("\n2. Speichern im Raw-Editor")
    edited = LET.replace("let-pc", "let-pc-2")
    d.execute_script("""
      const ta = document.getElementById('raw-file-editor');
      ta.value = arguments[0];
      ta.dispatchEvent(new Event('input', {bubbles: true}));
      document.getElementById('raw-save-tab-btn').click();""", edited)
    time.sleep(2.5)
    check("die Datei enthält genau den Text aus dem Editor",
          lambda: (cfg / "configuration.nix").read_text(encoding="utf-8") == edited)

    print("\n3. flake.nix mit @inputs")
    open_file("flake.nix")
    dismiss_dialogs()
    check("das Flake-Formular ist nicht zu sehen", lambda: hidden("flake-form"))
    check("der Raw-Editor zeigt die flake.nix", lambda: "@inputs" in (editor_text() or ""))
    check("der Hinweis steht daneben", lambda: not hidden("unmappable-hint"))
    check("die flake.nix auf der Platte ist unverändert",
          lambda: (cfg / "flake.nix").read_text(encoding="utf-8") == FLAKE)

    print("\n4. Eine abbildbare Datei daneben")
    open_file("hosts/alpha/default.nix")
    dismiss_dialogs()
    check("die Host-Datei zeigt ihr Formular", lambda: not hidden("config-form"))
    check("der Hinweis ist dort weg", lambda: hidden("unmappable-hint"))
    check("der Hostname steht im Feld",
          lambda: d.find_element(By.ID, "hostname").get_attribute("value") == "alpha")

    print("\n5. Zurück zur let-Config")
    open_file("configuration.nix")
    dismiss_dialogs()
    check("wieder der Raw-Editor", lambda: editor_text() == edited)
    check("die Datei ist bis auf die eigene Änderung unverändert",
          lambda: (cfg / "configuration.nix").read_text(encoding="utf-8") == edited)

    print("\n6. Texte")
    check("kein __TODO__ in der Oberfläche",
          lambda: "__TODO__" not in d.find_element(By.TAG_NAME, "body").get_attribute("innerHTML"))
    errs = [e for e in d.get_log("browser") if e.get("level") == "SEVERE"]
    check("keine Fehler in der Browser-Konsole", lambda: not errs)
    for e in errs[:5]:
        print("        ", e.get("message"))
finally:
    d.quit()

print()
if fails:
    print(f"{len(fails)} FEHLER:")
    for f in fails:
        print("  -", f)
    sys.exit(1)
print("Alle Prüfungen bestanden.")
