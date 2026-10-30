"""Browser-Test: der Hardware-Dialog nach dem Umbau vom 2026-10-30.

Martins Befund beim VM-Test: Der erste Kasten stand voll mit UUIDs, und unten
sassen zwei Knoepfe, von denen einer die Quellenauswahl vollstaendig ignorierte
("Datei gehoert zu diesem Rechner" schrieb nichts, merkte sich nur die
Bestaetigung). Zwei Knoepfe fuer zwei verschiedene Fragen - unlesbar.

Jetzt: gruppierte Liste mit nur Pfaden, die Datentraeger-Kennungen hinter einem
Details-Dreieck, Host-Auswahl nur bei mehreren Hosts, und **ein** Aktionsknopf;
"Vorhandene Datei behalten" ist eine Zeile in derselben Liste.

Geprueft wird am echten Dialog im Browser, mit den Daten, die
/api/hardware/check liefert - der Dialog nimmt sie als Argument, es braucht
also keine praeparierte Config.

Lauf: nix-shell shell.nix --run "python testing/ui/test_hardware_dialog.py"
"""
import os, pathlib, sys, threading, time

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))
from nico.server import create_app
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.service import Service

PORT = 8797
app = create_app()
threading.Thread(target=lambda: app.run(port=PORT, threaded=True), daemon=True).start()
time.sleep(2)
opts = webdriver.ChromeOptions()
for f in ("--headless=new", "--no-sandbox", "--disable-dev-shm-usage", "--window-size=1600,1200"):
    opts.add_argument(f)
opts.binary_location = os.environ["CHROME_BIN"]
d = webdriver.Chrome(service=Service(os.environ["CHROMEDRIVER"]), options=opts)

fails = []
def check(label, cond, got=None):
    print(("  OK   " if cond else "  FAIL ") + label + ("" if cond or got is None else f"  (war: {got})"))
    if not cond: fails.append(label)


UUID_A = "11111111-2222-3333-4444-555555555555"
UUID_B = "99999999-8888-7777-6666-555555555555"

# Multi-Host: unser Host hat die fremde Datei, der Laptop die eigene.
MULTI = """{
  action: 'replace', blocking: true, this_host: 'nix-vm',
  matched_host: 'nix-vm', host_name_hit: true,
  hosts: ['nix-laptop', 'nix-vm'],
  target: {path: 'hosts/nix-vm/hardware-configuration.nix', host: 'nix-vm',
           verdict: 'mismatch', missing: ['%s'], unresolved: [], acked: false,
           blocking: true},
  files: [
    {path: 'hosts/nix-vm/hardware-configuration.nix', host: 'nix-vm',
     verdict: 'mismatch', missing: ['%s'], unresolved: [], acked: false, blocking: true},
    {path: 'hosts/nix-laptop/hardware-configuration.nix', host: 'nix-laptop',
     verdict: 'match', missing: [], unresolved: [], acked: false, blocking: false}
  ],
  sources: [
    {id: 'stash:nix-vm.nix', kind: 'stash', label: 'hosts/nix-vm/hardware-configuration.nix',
     host: 'nix-vm', at: '2026-10-30T12:00:00', verdict: 'match', usable: true},
    {id: 'etc', kind: 'etc', label: '/etc/nixos/hardware-configuration.nix',
     host: null, at: null, verdict: 'mismatch', usable: false}
  ],
  targets: [
    {host: 'nix-laptop', path: 'hosts/nix-laptop/hardware-configuration.nix',
     exists: true, verdict: 'match', is_this_host: false},
    {host: 'nix-vm', path: 'hosts/nix-vm/hardware-configuration.nix',
     exists: true, verdict: 'mismatch', is_this_host: true}
  ]
}""" % (UUID_B, UUID_B)

# Single-Host: keine Host-Auswahl, weil es nur eine Stelle gibt.
SINGLE = """{
  action: 'replace', blocking: true, this_host: 'nix-desktop',
  matched_host: null, host_name_hit: false, hosts: [],
  target: {path: 'hardware-configuration.nix', host: null, verdict: 'unknown',
           missing: [], unresolved: ['/dev/sda1'], acked: false, blocking: true},
  files: [{path: 'hardware-configuration.nix', host: null, verdict: 'unknown',
           missing: [], unresolved: ['/dev/sda1'], acked: false, blocking: true}],
  sources: [{id: 'stash:_root.nix', kind: 'stash', label: 'hardware-configuration.nix',
             host: null, at: null, verdict: 'unknown', usable: true}],
  targets: [{host: null, path: 'hardware-configuration.nix', exists: true,
             verdict: 'unknown', is_this_host: false}]
}"""

# Sackgasse: keine Datei da, keine brauchbare Quelle.
DEAD = """{
  action: 'add', blocking: true, this_host: 'nix-neu',
  matched_host: null, host_name_hit: false, hosts: [],
  target: null, files: [], sources: [],
  targets: [{host: null, path: 'hardware-configuration.nix', exists: false,
             verdict: null, is_this_host: false}]
}"""


def open_dialog(payload):
    d.execute_script(f"document.querySelectorAll('.overlay').forEach(o => o.remove());"
                     f"_showHardwareDialog({payload});")
    time.sleep(0.8)


def modal_text():
    return d.execute_script(
        "const m = [...document.querySelectorAll('.overlay .modal')].pop();"
        "return m ? m.textContent : '';")


try:
    d.get(f"http://127.0.0.1:{PORT}/")
    time.sleep(4)

    print("1. Keine Datentraeger-Kennungen im Lesefluss")
    open_dialog(MULTI)
    body = d.execute_script(
        "const m = [...document.querySelectorAll('.overlay .modal')].pop();"
        "const det = m.querySelector('details');"
        "const clone = m.cloneNode(true);"
        "clone.querySelector('details')?.remove();"
        "return clone.textContent;")
    check("UUID steht nicht im Hauptteil", UUID_B not in body, body[:160])
    check("UUID steckt im Details-Bereich",
          UUID_B in d.execute_script(
              "return document.querySelector('.overlay details').textContent;"))
    check("Details ist zugeklappt",
          d.execute_script("return !document.querySelector('.overlay details').open"))

    print("\n2. Gruppen mit Pfad, leere Gruppe mit Strich")
    groups = d.execute_script(
        "const m = [...document.querySelectorAll('.overlay .modal')].pop();"
        "return [...m.querySelectorAll('.gsi-block-body > div')]"
        "  .map(g => g.textContent.replace(/\\s+/g,' ').trim());")
    check("drei Gruppen", len(groups) == 3, groups)
    check("Treffer-Gruppe nennt den Laptop",
          "hosts/nix-laptop/hardware-configuration.nix" in groups[0], groups[0])
    check("Fehlt-Gruppe nennt die VM",
          "hosts/nix-vm/hardware-configuration.nix" in groups[1], groups[1])
    check("leere Gruppe zeigt –", groups[2].endswith("–"), groups[2])

    print("\n3. Ein Aktionsknopf statt zwei")
    btns = d.execute_script(
        "const m = [...document.querySelectorAll('.overlay .modal')].pop();"
        "return [...m.querySelectorAll('button')].map(b => b.id);")
    check("genau ein Knopf", btns == ["hw-apply"], btns)
    check("Knopf ist zunaechst gesperrt",
          d.find_element(By.ID, "hw-apply").get_attribute("disabled") == "true")

    print("\n4. Behalten ist eine Zeile in der Liste")
    opts_src = d.execute_script(
        "return [...document.getElementById('hw-source').options]"
        "  .map(o => [o.value, o.disabled]);")
    check("Platzhalter zuerst, gesperrt", opts_src[0][0] == "" and opts_src[0][1], opts_src)
    check("Behalten als zweiter Eintrag", opts_src[1][0] == "__keep__", opts_src)
    check("Zwischenlager waehlbar",
          ["stash:nix-vm.nix", False] in [list(o) for o in opts_src], opts_src)
    check("fremde Quelle gesperrt",
          ["etc", True] in [list(o) for o in opts_src], opts_src)

    print("\n5. Host-Auswahl nur bei mehreren Hosts")
    check("Multi-Host: Auswahl da", d.execute_script(
        "return !!document.getElementById('hw-host');"))
    check("nichts vorausgewaehlt", d.execute_script(
        "return document.getElementById('hw-host').selectedIndex === 0;"))
    check("eigener Host markiert", "nix-vm" in d.execute_script(
        "return [...document.getElementById('hw-host').options]"
        "  .filter(o => o.text.includes('nix-vm')).map(o => o.text).join();"))

    open_dialog(SINGLE)
    check("Single-Host: keine Auswahl", d.execute_script(
        "return !document.getElementById('hw-host');"))
    check("Single-Host: Quellenliste trotzdem da", d.execute_script(
        "return !!document.getElementById('hw-source');"))

    print("\n6. Knopf wird erst mit einer Auswahl frei")
    d.execute_script("const s = document.getElementById('hw-source');"
                     "s.selectedIndex = 1; s.dispatchEvent(new Event('change'));")
    time.sleep(0.3)
    check("nach Auswahl frei",
          d.find_element(By.ID, "hw-apply").get_attribute("disabled") is None)

    print("\n7. Sackgasse: Hinweis und ein Weg hinaus")
    open_dialog(DEAD)
    txt = modal_text()
    check("nennt nixos-generate-config", "nixos-generate-config" in txt, txt[-160:])
    check("kein Aktionsknopf", d.execute_script(
        "return !document.getElementById('hw-apply');"))
    btn = d.execute_script("return document.getElementById('hw-close');")
    check("Schliessen-Knopf da", btn is not None)
    d.find_element(By.ID, "hw-close").click()
    time.sleep(0.5)
    check("Dialog ist zu", d.execute_script(
        "return document.querySelectorAll('.overlay').length === 0;"))

    print("\n8. Alle Texte uebersetzt")
    for payload, name in ((MULTI, "multi"), (SINGLE, "single"), (DEAD, "dead")):
        open_dialog(payload)
        txt = modal_text()
        check(f"{name}: kein __TODO__", "__TODO__" not in txt)
        check(f"{name}: kein roher Schluessel", "hw." not in txt.replace("hw.", "", 0)
              or not any(k in txt for k in ("hw.gMatch", "hw.sourceLabel", "hw.keepFile",
                                            "hw.useLocal", "hw.details", "hw.close")), txt[:120])

finally:
    d.quit()

print()
if fails:
    print("FEHLGESCHLAGEN:", len(fails))
    for f in fails:
        print("  -", f)
    sys.exit(1)
print("Alle Prüfungen bestanden.")
