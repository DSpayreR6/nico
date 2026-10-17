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

## test_collapse.py

Auf- und Zuklappen von Sektionen und Nix-Brix, 14 Prüfungen. Deckt beide
Fehler von 2026-10-17 ab: "Alles einklappen" ließ die Brix offen, und ein
Klick auf einen Brick in einer zugeklappten Sektion öffnete nur die Sektion.
