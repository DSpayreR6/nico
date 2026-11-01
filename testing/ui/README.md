# Browser-Tests (Selenium + Chromium)

Headless-Tests gegen die echte Oberfläche. Laufen gegen eine Wegwerf-Config,
die echten Einstellungen werden nicht angefasst (eigenes XDG_CONFIG_HOME).

Umgebung bauen:

    nix build --impure --no-link --print-out-paths --expr '
      with (builtins.getFlake "git+file://$PWD").inputs.nixpkgs.legacyPackages.x86_64-linux;
      buildEnv { name = "ui-test-env"; paths = [
        (python3.withPackages (ps: [ ps.flask ps.tree-sitter ps.selenium ]))
        chromium chromedriver
      ]; }'

Starten (ENV = der ausgegebene Pfad, CFG = Verzeichnis mit Wegwerf-Config):

    export XDG_CONFIG_HOME=$CFG/xdg XDG_CACHE_HOME=$CFG/cache
    export CHROMEDRIVER=$ENV/bin/chromedriver CHROME_BIN=$ENV/bin/chromium
    export TREE_SITTER_NIX_GRAMMAR=<grammar>/parser
    $ENV/bin/python3 testing/ui/test_collapse.py

$CFG/xdg/nico/settings.json muss auf das Config-Verzeichnis zeigen.
Exit-Code 0 = alles grün.

## Wegwerf-Config, die alle Tests erfüllt

Mehrere Tests prüfen Brick-Listen und brechen ohne passende Dateien ab oder
melden Fehler, die keine sind. Diese Struktur deckt alle ab:

    configuration.nix              Brick "basis-brick", **kein** imports-Attribut
                                   (ein imports = [ … ] wird zu einem Brick
                                   namens "imports" und verfälscht die Listen)
    hosts/testhost/default.nix     Bricks "host-brick-eins", "host-brick-zwei"
    home/martin.nix                Brick "hm-brick"
    bestand.nix                    Brick "huelle" mit einem Brick "innen" darin
                                   (vorhandene Verschachtelung)

Deutsche Abschnittsnamen in den Markern sind dabei nützlich: dann läuft der
Test gleich über den Weg, auf dem NiCo Dateien älterer Versionen einliest.

## test_collapse.py

Auf- und Zuklappen von Sektionen und Nix-Brix, 14 Prüfungen. Deckt beide
Fehler von 2026-10-17 ab: "Alles einklappen" ließ die Brix offen, und ein
Klick auf einen Brick in einer zugeklappten Sektion öffnete nur die Sektion.

## test_sections.py (ohne Browser, in testing/routes/)

Abschnitts-Kürzel, alte deutsche Namen und der Formatmarker, 38 Prüfungen.
Läuft mit `nix-shell shell.nix --run "python testing/routes/test_sections.py"`.

## test_fs_section.py

Abschnitt "Wartung & Snapshots" und das Zahnrad, 21 Prüfungen: die zwei
Gruppen mit ihren Feldern, die /tmp-Größe nur bei aktivem tmpfs, das
Ausgrauen von "/tmp beim Booten leeren" ohne Wertverlust, und das Zahnrad
als Umschalter – inklusive der Regel, dass ein offener Unterdialog Vorrang
hat.

## test_validator_gate.py

Der Validator vor dem Bauen, 27 Prüfungen: rote Funde öffnen die Ausgabe,
Warnungen und Hinweise lassen durch. Geprüft wird auch die Reihenfolge im
Dialog – Hinweis zuerst, Karten in der Mitte (rot vor gelb vor blau), die
Knöpfe als letztes Element –, dass derselbe Fund aus mehreren Hosts nur
einmal erscheint, dass der Erzwingen-Knopf den Namen der Funktion trägt, die
er startet, und dass im Menü aufgerufen keine Knöpfe erscheinen.

## test_state_version_hint.py

Hinweiszeile und Platzhalter am Feld System-State-Version, 23 Prüfungen.
Aus Martins Frage vom 2026-11-01, was ein Eintrag in der `configuration.nix`
bei mehreren Hosts bewirkt: Bei zwei Definitionen bricht Nix ab, ein
Host-Wert gewinnt nicht von selbst. Geprüft wird, dass die Zeile die Hosts
mit und ohne eigenen Wert nennt, dass sie genau diese Warnung ausspricht,
dass der Platzhalter auf die Host-Dateien verweist, und dass die Zeile bei
geöffneter Host-Datei auf den einen Host umschaltet. Gegen den Stand vor dem
Fix fallen 14 davon.

Zwei Dinge, die beim Bau des Tests aufgefallen sind und die ein Nachfolger
nicht neu herleiten sollte:

* Sektionen starten zugeklappt (Entscheidung 2026-10-17), und Selenium gibt
  für ein Element darin einen leeren `.text` zurück. Ohne Aufklappen prüft
  so ein Test nichts und ist trotzdem grün.
* `switchHost()` in `form.js` ist **nicht** benutzbar: die Funktion hat
  keinen Aufrufer und läuft in einen `ReferenceError`, weil `_formDirty` und
  `_flakeFormDirty` im Closure von `Sidebar` liegen und nicht global sind.
  Der Weg des Nutzers ist `Sidebar.selectFile(...)`, und so fährt der Test.
