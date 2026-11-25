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

Starten (ENV = der ausgegebene Pfad, BASE = ein leeres Verzeichnis, Grammatik
aus `nix-shell shell.nix --run 'echo $TREE_SITTER_NIX_GRAMMAR'`):

    export CHROMEDRIVER=$ENV/bin/chromedriver CHROME_BIN=$ENV/bin/chromium
    export TREE_SITTER_NIX_GRAMMAR=<grammar>/parser
    $ENV/bin/python3 testing/ui/make_test_config.py --for test_collapse $BASE
    export XDG_CONFIG_HOME=$BASE/xdg XDG_CACHE_HOME=$BASE/cache
    env -u LD_LIBRARY_PATH $ENV/bin/python3 testing/ui/test_collapse.py

Exit-Code 0 = alles grün.

## Wegwerf-Config: `make_test_config.py`

Die Wegwerf-Config legt `make_test_config.py` an, **vor jedem Test neu** – die
Tests verändern sie. `--for <test>` wählt das passende Profil, alle anderen
Tests bekommen `standard`. Welche Dateien und Bricks in welchem Profil
stehen, steht im Kopf des Skripts; es ist die einzige Quelle dafür.

Bis 2026-11-16 wurde die Config pro Sitzung von Hand im Temp-Verzeichnis
gebaut und nirgends aufbewahrt. Mit der Sitzung war sie weg, und
`test_collapse`, `test_nested_dialogs` und `test_state_version_hint` liefen
nicht mehr: der erste wurde vom Startdialog „Hardware-Datei prüfen“
blockiert (jedes Profil hat deshalb eine bestätigte Hardware-Datei), der
zweite braucht Bricks, die `test_brix_dialogs` stören würden, der dritte
einen Flake mit drei Hosts.

Tests, die ihre Config selbst anlegen und das Skript nicht brauchen:
`test_unmappable`, `test_flake_create`, `test_raw_panel_keep`,
`test_git_menu_remote`, `test_rebuild_preparing`, `test_package_inactive`,
`test_outside_change`, `test_outside_restructure`, `test_brick_adopt`.

## test_collapse.py

Auf- und Zuklappen von Sektionen und Nix-Brix, 14 Prüfungen. Deckt beide
Fehler von 2026-10-17 ab: "Alles einklappen" ließ die Brix offen, und ein
Klick auf einen Brick in einer zugeklappten Sektion öffnete nur die Sektion.

## test_brick_stay.py

Nix-Brick bleibt stehen (2026-11-25): Klick auf einen Brick-Kopf in
zugeklappter Sektion lässt den Kopf an seiner Stelle, ein Klick in einen
langen Brick setzt den Cursor in die angeklickte Zeile, ohne dass die
Ansicht springt; Farben im Brick-Editor schon beim Tippen, Abbrechen und
Speichern ohne Sprung. Profil `standard`, legt den langen Brick selbst an.

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

## test_package_inactive.py

Pakete deaktivieren, AppImage-Bibliotheken und die Firefox-Felder, 37
Prüfungen (2026-11-14). Der Schalter in der Paketliste wirkt sofort in der
Vorschau: das Paket wandert zwischen der Liste und dem Nix-Brick
`nico-inactive-packages`. Der Brick ist sichtbar, als verwaltet
gekennzeichnet, sein Menü bietet nur „Löschen". Dazu: Speichern und
Neuladen, kein doppelter Eintrag, das Feld „Zusätzliche Bibliotheken"
(geladen, nur bei aktivem AppImage-Support, ungültige Namen entfernt) und
dass die Firefox-Felder beim Laden gefüllt werden. Nicht gegen den Stand
davor gefahren.

Legt sich seine Wegwerf-Config selbst an und setzt `XDG_CONFIG_HOME` selbst –
es braucht nur `CHROME_BIN`, `CHROMEDRIVER` und `TREE_SITTER_NIX_GRAMMAR`.

## test_rebuild_verbose.py

Der Schalter „Alle Meldungen von Nix anzeigen" im Rebuild-Monitor: sichtbar,
ohne gespeicherten Wert aus, ein Klick merkt die Wahl in den App-Settings, und
der Optionen-Dialog zeigt denselben Stand. Öffnet den Monitor ohne Job, startet
also keinen Rebuild. Dass der Server die Detailmeldungen eines laufenden Jobs
verwirft, prüft `testing/routes/test_rebuild_verbose.py`. Braucht ein eigenes
XDG_CONFIG_HOME; eine leere `configuration.nix` genügt. Port 8812.

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

## test_rebuild_spinner.py

Drehender Strich mit Laufzeit (`| 1:23`) in Rebuild-Monitor und Dry-Run,
21 Prüfungen. Aus Martins Befund vom 2026-11-16: Strecken ohne neue Zeile
sehen aus, als stünde NiCo. Geprüft: Laufzeit ab dem Job-Start vom Server
(nicht ab dem Öffnen des Monitors), der Strich dreht sich, steht unter der
letzten Ausgabezeile und verschwindet am Ende; bei einem schon fertigen Job
erscheint er nicht; im Dry-Run bis zum Ergebnis. Dazu, dass der Schalter
„Alle Meldungen“ in einer Zeile mit seinem Text steht (`.modal label`
setzte `display: block`). Kein echter Lauf: `EventSource`, Status und
Dry-Run werden im Browser ersetzt. Port 8813.

## test_raw_panel_keep.py

Raw-Modus → Einstellung außerhalb eines Bricks → Panel → Speichern, 16
Prüfungen, für `configuration.nix` und eine Host-Datei. Aus dem Befund vom
2026-11-16: die Einstellung war danach weg (Raw-Speichern setzt den Hash
neu; Host-Dateien hatten gar keine Umwandlung). Fährt den Weg durch die
Oberfläche – Panel-Schalter, Raw-Editor, Raw-Speichern-Knopf, `saveConfig()`
– und prüft, dass die Einstellung als Nix-Brick genau einmal in der Datei
steht. Gegen den Stand vor dem Fix fallen 6 Prüfungen. Legt Wegwerf-Config
und XDG_CONFIG_HOME selbst an. Gegenstück ohne Browser:
`testing/routes/test_keep_unmodelled.py`. Port 8814.

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

## test_brick_adopt.py

„Ins Panel übernehmen" im Punkte-Menü eines Nix-Bricks (Gegenprobe Phase 4,
2026-11-19), 14 Prüfungen: Eintrag nur bei aktivem Brick in
configuration.nix bzw. Host-Datei; nach dem Klick Brick weg und Schalter im
Formular gesetzt; ein Brick, den das Panel anders schreiben würde
(`openFirewall = false`), bleibt, die Meldung ist übersetzt und nennt den
Pfad. Legt Config und XDG selbst an. Gegenstück ohne Browser:
`testing/routes/test_brick_adopt.py`.
