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
