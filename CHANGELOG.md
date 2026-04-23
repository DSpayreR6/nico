# NiCo – Changelog

## Unreleased

---

## 0.9.2 (2026-04-23)

### Sichererer Datei- und Hostwechsel
Beim Wechsel zwischen `configuration.nix` und Host-Dateien schützt NiCo jetzt besser vor Datenverlust. Speichern und Auto-Save laufen nur noch, wenn das Formular vollständig zur aktuell geöffneten Datei geladen wurde. Außerdem wird die Filterauswahl der Sektionen als Programmeinstellung gespeichert und beim nächsten Start wiederhergestellt.

### Sektionen filtern
Im linken Panel gibt es jetzt ein Filter-Icon neben den Einklappen/Aufklappen-Buttons. Ein Klick öffnet drei Optionen: alle Sektionen anzeigen, nur Sektionen mit Inhalt anzeigen oder eine selbst konfigurierte Auswahl verwenden. Die gewählte Ansicht bleibt beim nächsten Start erhalten. Sektionen mit Inhalt werden unabhängig vom Filter immer angezeigt.

### Sektionen anpassen
Unter Admin → Einstellungen → NiCo-Einstellungen lässt sich per „Sektionen anpassen" festlegen, welche Sektionen beim Filter „Sektionen lt. Einstellungen" sichtbar sein sollen. Die Auswahl funktioniert wie die Validierungsregeln: Toggles pro Sektion, gespeichert in den Programmeinstellungen auf dem Rechner.

### Einstellungen-Tab neu gegliedert
Der Admin-Tab „Einstellungen" ist jetzt in zwei Sub-Tabs aufgeteilt: **NiCo-Einstellungen** (maschinenlokal) und **Config-Einstellungen** (wandern mit der Config). Das trennt klar, was wo gespeichert wird.

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

### Programmeinstellungen sichern und wiederherstellen
Im Admin-Bereich unter „Exportieren" lassen sich die NiCo-Programmeinstellungen (Sprache, Theme, Ansichtsoptionen) als JSON-Datei herunterladen und auf einem anderen Gerät wieder einspielen. Der Config-Pfad bleibt beim Import bewusst unangetastet – er ist maschinenspezifisch und wandert nicht mit.

### Interner Verbesserung: Parser
Der Nix-Import-Parser nutzt jetzt tree-sitter für eine genauere Erkennung von Konfigurationsoptionen und fällt nur bei fehlender Umgebung auf den bisherigen Regex-Parser zurück.

## 0.9.1 (2026-04-08)

Backup vor dem Import, Sidebar mit Dateibaum, überarbeitete Header-Navigation, ZIP-Export aller Config-Dateien und automatische Dateikategorisierung beim Start.

---

## 0.0.1 (2026-04-07)

Erstes lauffähiges Grundgerüst: Flask-Backend, Konfigurationsgenerator, Panel-UI, Import, Brix-System, Git-Zeitmaschine, Home Manager, Mehrsprachigkeit (DE/EN).
