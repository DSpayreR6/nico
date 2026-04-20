# NiCo – Changelog

## Unreleased

### Rebuild: path:-Prefix ohne Git-Repository
- Vor jedem Rebuild-Start wird geprüft ob das Config-Verzeichnis ein Git-Repository ist (`git_manager.is_git_repo`)
- Ohne Git: `--flake path:/abs/pfad#hostname` – Nix liest direkt vom Dateisystem statt über den Git-Index
- Mit Git: Verhalten unverändert (`--flake .#hostname`)
- Dryrun verwendet `path:` bereits immer – keine Änderung nötig

### Rebuild/Dryrun-Ausgabefenster (nix-output-monitor-Stil)
- Rebuild-Modal: oberer Scrollbereich für rohen Log-Stream (Warning gelb, Error rot hervorgehoben)
- Rebuild-Modal: unterer Monitor-Block mit Phasen-Label (Evaluating → Fetching → Building → Activating), animiertem Fortschrittsbalken (Shimmer, phasenfarbig), aktiven Paketnamen und Abschlussstatus (✅/❌)
- Dryrun-Output: `warning:` / `error:`-Zeilen werden ebenfalls farbig hervorgehoben
- Hilfsfunktion `_colorizedOutput()` (HTML-sicher, shared für Rebuild-Log und Dryrun)
- Backend/SSE-Logik unverändert

### NixOS-Aktionsmenü
- `dry-run`-Button aus der Header-Leiste entfernt
- Neues NixOS-Icon-Dropdown in der Header-Leiste mit drei farbkodierten Aktionen: Zwischenstand speichern (grün), Dry-Run (gelb), System-Neubau (rot)
- NixOS-SVG lokal unter `nico/static/nixos.svg` (MIT-Lizenz, NixOS-Projekt)
- Admin-Panel: „Aktionen"-Tab entfernt; Standard-Tab ist jetzt „Einstellungen"
- Neue i18n-Schlüssel: `header.nixosTitle`, `header.nixosSave`, `header.nixosDryRun`, `header.nixosRebuild`

### Git (ungetestet)
- Remote-Stand-Prüfung beim Start: falls das lokale Repo hinter dem Remote liegt, erscheint ein blaues Info-Banner mit Anzahl der fehlenden Commits
- Neuer `GET /api/git/remote-status`-Endpunkt (`git fetch` + `rev-list HEAD..@{u} --count`)
- Neuer Übersetzungsschlüssel `git.remoteBehind`

### Frontend & Lizenzen
- Prism.js wird nicht mehr per CDN geladen, sondern lokal aus `nico/static/vendor/prism/`
- Neue Root-Datei `THIRD_PARTY_LICENSES.md` dokumentiert Prism.js, Version, Quelle und MIT-Lizenztext
- „Über NiCo“ nennt Prism.js jetzt explizit als verwendete Drittkomponente für Syntax-Highlighting

## 0.0.2 (2026-04-08)

### Startschema & Kategorisierung
- Neuer `/api/categorize`-Endpunkt: schreibt `# nico-version: type#hash`-Header in alle `.nix`-Dateien beim Start, nach Import und nach manueller Kategorisierung
- Datei-Typen: `co` (configuration.nix / default.nix), `fl` (flake.nix), `hw` (hardware-configuration.nix), `hm` (home.nix), `nd` (sonstige .nix), `fx` (flake.lock, intern)
- Hash-Berechnung für `co`-Dateien korrigiert: kanonischer Inhalt = `_NICO_HEADER + content`

### Import
- Vor dem Import wird geprüft ob das Zielverzeichnis relevante Dateien enthält; wenn ja, Backup-Bestätigung mit ZIP-Sicherung
- `nico.json` und ZIP-Dateien lösen keine Backup-Nachfrage aus
- `backup_to_zip()`: erstellt `nixos-config-YYYY-MM-DD-HHmmss.zip`
- Backup-Bestätigungsflow für automatischen Import (/etc/nixos) und manuellen Import
- Admin-Import: `_doAdminImport()`-Helfer mit vollständigem Backup-Bestätigungsflow
- Fehlermeldung `ERR_IMPORT_PERMISSION` erklärt fehlende Root-Rechte auf /etc/nixos
- Importergebnisliste nach erfolgreichem Import entfernt

### Sidebar
- `flake.lock` aus dem Seitenbaum ausgeblendet (JSON, kein `#`-Kommentar möglich)
- Sidebar beim Start standardmäßig geöffnet

### Header & Navigation
- Sprachumschalter als Dropdown (statt zwei Buttons)
- Neuer 💾-Button (Speichern) in der Icon-Leiste
- Neuer `dry-run`-Button in der Icon-Leiste (Stil: rechteckig wie „Verzeichnis auswählen")
- Button-Reihenfolge: 💾 → dry-run → ⚙ Admin → ? Hilfe → ⏻ Ausschalten
- `dry-run` speichert zuerst, dann Syntaxprüfung (statt nur Syntaxprüfung)

### Panels & Admin-Bereich
- Speichern- und Admin-Bereich-Button aus dem linken Konfigurationspanel entfernt
- Speichern- und Konfiguration-testen-Button aus Admin „Aktionen" entfernt
- Admin-Bereich: Tabs und Inhalte horizontal zentriert

### Export
- ZIP-Export enthält jetzt alle sichtbaren Dateien aus dem nixos-Verzeichnis (rekursiv, nicht-versteckt), inkl. Import-Sicherungs-ZIPs; `.git` automatisch ausgeschlossen

---

## 0.0.1 (2026-04-07)

Erstes lauffähiges Grundgerüst: Flask-Backend, Konfigurationsgenerator, Panel-UI, Import, Brix-System, Git-Zeitmaschine, Home Manager, Mehrsprachigkeit (DE/EN).
