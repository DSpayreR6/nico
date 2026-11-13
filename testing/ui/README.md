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

## test_unmappable.py

Nicht abbildbare Dateien, 19 Prüfungen: eine `configuration.nix` mit
`let … in` und eine `flake.nix` mit `@inputs` erscheinen im Raw-Editor statt
im Formular, mit Hinweis und ohne Panel-Schalter; Speichern schreibt genau
den Editor-Text; eine abbildbare Host-Datei daneben zeigt weiter ihr
Formular. Legt Wegwerf-Config und XDG_CONFIG_HOME selbst an. Das Gegenstück
ohne Browser ist `testing/routes/test_rev1110_guard.py` (84 Prüfungen).

## test_flake_create.py

„flake.nix anlegen" als eigener Schritt, 20 Prüfungen: die Schaltfläche
erscheint nur ohne `flake.nix`, der Schalter darüber legt keine an, der
Dialog nennt Verzeichnis und Host und schließt nur per Schaltfläche, danach
liegen `flake.nix`, Host-Verzeichnis und die verschobene Hardware-Datei da.
Legt Wegwerf-Config und XDG_CONFIG_HOME selbst an. Das Gegenstück ohne
Browser ist `testing/routes/test_rev1110_flake_mode.py`.

## test_rebuild_activation_warning.py

Der Rebuild-Monitor bei „gebaut und aktiv, aber ein Dienst startete nicht
neu" (gelb statt rot, Dienst beim Namen, Sicherungspunkt angeboten) und die
Maskierung von Fehlertexten aus nix. Es läuft kein Rebuild: `EventSource`
wird durch eine Attrappe ersetzt. Braucht ein eigenes XDG_CONFIG_HOME; ein
git-Repository in der Wegwerf-Config legt der Test selbst an.

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

## test_brick_inactive.py

Nix-Brick deaktivieren und wieder aktivieren, 22 Prüfungen. Aus Martins
Wunsch vom 2026-11-01: Ein gelöschter Brick ist weg, obwohl man ihn später
wieder braucht. Geprüft wird, dass der Menüeintrag zwischen „Deaktivieren"
und „Aktivieren" wechselt, dass die Zeile ausgegraut wird und den Zusatz
„deaktiviert" trägt, dass der Brick **in der Liste bleibt** – würde der
Marker mit der Flagge nicht erkannt, wäre er verschwunden – und dass nach
dem Aktivieren alles wieder wie vorher ist. Gegen den Stand vor dem Fix
fallen 8 davon.

Braucht in der Wegwerf-Config den Brick `basis-brick` in
`configuration.nix`, wie oben beschrieben.

## Die Wegwerf-Config ist pro Test verschieden (gemessen 2026-11-01)

Die Struktur oben deckt **nicht** alle Tests gleichzeitig ab. Drei Konflikte,
die beim Bau von `test_brick_inactive.py` aufgefallen sind:

* `test_brix_dialogs.py` erwartet in `configuration.nix` **genau einen** Brick
  (`basis-brick`) und zählt Listen ab. `test_nested_dialogs.py` braucht dort
  zusätzlich einen Brick namens `aussen`. Beide zusammen gehen nicht.
* `test_nested_dialogs.py` **verändert seine Fixture**: es benennt `huelle`
  nach `huelle2` und `huelle3` um. Ein zweiter Lauf auf derselben Config
  scheitert mit `ERR_BRICK_NOT_FOUND` – die Datei muss vor jedem Lauf neu
  geschrieben werden.
* Liegt `bestand.nix` mit seiner Verschachtelung im Verzeichnis, öffnet NiCo
  beim Seitenladen den Verschachtelungs-Hinweis. Dessen Overlay fängt Klicks
  ab und `test_collapse.py` bricht mit `ElementClickInterceptedException` ab.
  Für `test_collapse.py` also ohne `bestand.nix` fahren.

Vor jedem Test die Fixture neu schreiben, nicht einmal für die ganze Suite.

## Ports nicht doppelt belegen

Jeder Test startet seinen eigenen Server. Zwei Tests auf demselben Port laufen
einzeln, aber beim Nachstellen eines Fehlers greift man leicht zum falschen
Prozess – am 2026-11-01 eine halbe Stunde Suche gekostet. Stand heute:

    8792  test_brick_inactive      8796  test_validator_gate
    8793  test_nested_dialogs      8797  test_brix_dialogs, test_state_version_hint,
    8794  test_about_version             test_hardware_dialog
    8795  test_rebuild_options     8798  test_i18n, test_fs_section
                                   8799  test_collapse

Die dreifache 8797 und die doppelte 8798 sind Altbestand; ein neuer Test nimmt
einen freien Port.
