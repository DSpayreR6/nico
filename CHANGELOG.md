# NiCo – Changelog

## Unreleased

### Validierung vor Rebuild
Vor dem Rebuild lässt sich die Konfiguration jetzt auf häufige Probleme prüfen – per neuem „Validierung"-Button im NixOS-Menü. NiCo schaut dabei zum Beispiel, ob der aktuelle Benutzer in der Config angelegt ist, ob alle Import-Pfade auf der Platte existieren, ob die Hardware-Konfiguration eingebunden ist und ob Disk-UUIDs zum aktuellen System passen. Bei Flake-Configs mit mehreren Hosts fragt NiCo vorher, welcher Host geprüft werden soll – Hardware-Checks beziehen sich dann gezielt auf diesen Host. Welche Prüfungen aktiv sind, lässt sich in den Admin-Einstellungen unter „Validierung anpassen" individuell ein- oder ausschalten – die Auswahl wandert mit der Config.

### NixOS-Aktionen im Header
Der einzelne Dry-Run-Button wurde durch ein übersichtlicheres NixOS-Menü ersetzt. Ein Klick auf das NixOS-Logo öffnet drei farbkodierte Aktionen: Zwischenstand speichern (grün), Dry-Run (gelb) und System-Neubau (rot).

### Bessere Rebuild-Ausgabe
Das Rebuild-Fenster zeigt jetzt sowohl den rohen Ausgabe-Stream als auch einen kompakten Status-Monitor mit Fortschrittsbalken und aktuellem Bauprozess – ähnlich wie nix-output-monitor. Warnungen und Fehler werden farbig hervorgehoben.

### Rebuild-Log bei Fehler
Schlägt ein Rebuild fehl, schreibt NiCo automatisch eine vollständige Log-Datei (`nixos-rebuild.log`) in das Config-Verzeichnis. In den Einstellungen lässt sich das auch für erfolgreiche Rebuilds aktivieren.

### Rebuild ohne Git-Repository
Flake-Rebuilds funktionieren jetzt auch wenn das Config-Verzeichnis kein Git-Repository ist. NiCo erkennt das automatisch und übergibt den absoluten Pfad direkt an Nix.

### Symlink /etc/nixos anlegen
Im Admin-Bereich lässt sich ein Symlink von `/etc/nixos` auf das NiCo-Verzeichnis einrichten. Dann funktionieren NixOS-Tools wie `nixos-rebuild` ohne Pfadangabe. Das Original wird als `/etc/nixos.bak` gesichert.

### Remote-Stand im Git-Banner (experimentell)
Liegt das lokale Git-Repository hinter dem Remote zurück, erscheint beim Start ein blaues Info-Banner mit der Anzahl fehlender Commits.

### Prism.js jetzt lokal
Der Syntax-Highlighter Prism.js wird nicht mehr von einem externen CDN geladen, sondern direkt aus NiCo heraus ausgeliefert. Lizenz und Herkunft sind in `THIRD_PARTY_LICENSES.md` dokumentiert.

### Interner Verbesserung: Parser
Der Nix-Import-Parser nutzt jetzt tree-sitter für eine genauere Erkennung von Konfigurationsoptionen und fällt nur bei fehlender Umgebung auf den bisherigen Regex-Parser zurück.

---

## 0.9.1 (2026-04-08)

Backup vor dem Import, Sidebar mit Dateibaum, überarbeitete Header-Navigation, ZIP-Export aller Config-Dateien und automatische Dateikategorisierung beim Start.

---

## 0.0.1 (2026-04-07)

Erstes lauffähiges Grundgerüst: Flask-Backend, Konfigurationsgenerator, Panel-UI, Import, Brix-System, Git-Zeitmaschine, Home Manager, Mehrsprachigkeit (DE/EN).
