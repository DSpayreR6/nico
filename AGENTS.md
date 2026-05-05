# Nico – Claude Rules

**Immer zuerst ausführen:**

**Zugriffs- und Recherche-Rechte:**
- Darf alle Dateien innerhalb des Projektverzeichnisses lesen, analysieren, bearbeiten und schreiben.
- Web-Recherche ist erlaubt, wenn sie für die Aufgabe notwendig ist (z. B. Dokumentation, APIs, Best Practices).
- Vor **jeder Code-Änderung oder Datei-Modifikation** zuerst einen klaren Plan präsentieren und explizite User-Freigabe einholen.
- Niemals Dateien außerhalb des Projektverzeichnisses anfassen.
- Sensible Dateien (.env, *.key, credentials, secrets) niemals lesen, schreiben oder in Prompts einbeziehen.

**Kernregeln (streng befolgen):**
- Jede Antwort beginnt mit /statusline
- User-Anweisungen sofort auf Fehler, Widersprüche oder Unklarheiten prüfen und direkt darauf hinweisen
- Widersprüche zwischen `AGENTS.md`, `CLAUDE.md`, `vorgaben.txt`, `hinweisliste.txt`, Projektdateien und User-Anweisungen immer sofort benennen und vor weiterer Arbeit klären
- Keine Annahmen treffen. Bei Unklarheit immer nachfragen
- Vor jeder Code-Änderung oder Implementierung explizit User-Freigabe einholen
- Nichts ändern ohne ≥95 % Sicherheit über die korrekte Lösung
- Bei unklarem Bug: Ursache mitteilen, Debugging-Strategie mit User erarbeiten oder weitere Infos anfordern
- Keine Lösung gefunden → ehrlich mitteilen und nicht weitermachen
- Workarounds, wiederholte Erklärungen oder neue Erkenntnisse in `hinweisliste.txt` im Projektroot dokumentieren
- Antworten maximal präzise, ohne Fluff, Begrüßungen, Höflichkeitsfloskeln oder unnötige Erklärungen

**Nico-Projektregeln:**
- Drei-Ebenen-Einstellungsarchitektur:
  1. **App-Settings** (`nico-settings.json` im Projektroot): Gehören zu NiCo selbst und speichern nur Einstellungen, die auf diesem Rechner relevant sind
     - `nixos_config_dir`: Pfad zum Config-Verzeichnis
     - `language`: UI-Sprache (de, en, ...)
     - `theme`: UI-Theme (Platzhalter für später)
  2. **Config-Settings** (`{config-dir}/config.json`): Gehören zur konkreten Config und speichern alles, was für diese Config relevant ist und mit ihr mitwandern soll
     - `hosts_dir`: Verzeichnis für Hosts (Standard: "hosts")
     - `modules_dir`: Verzeichnis für Module (Standard: "modules")
     - `hm_dir`: Verzeichnis für Home-Manager (Standard: "home")
     - `flake_update_on_rebuild`: Flake vor Rebuild aktualisieren
  3. **NixOS-Config**: Ausschließlich in `.nix`-Dateien
- Keine NixOS-Daten in JSON-Dateien speichern
- Temporäre Dateien nur für Parser-Zwischendaten erlaubt → Inhalt sofort verwenden und Datei löschen
- Bei Umfangsfragen: Pareto 80/20 anwenden, bei Zweifel User nachfragen
- Immer zuerst einen kurzen Plan machen, bevor Code geschrieben oder geändert wird (außer bei winzigen Fixes)

**Mehrsprachigkeit:**
- Geplante Sprachen: de, en, es, fr, ja, ru, zh
- Neue Sprache = neue `.json` in `nico/static/lang/` → erscheint automatisch (`/api/langs` scannt das Verzeichnis)
- Flag-Emoji-Map in `app.js` (`LANG_FLAGS`) bei neuer Sprache ergänzen
- `section_links.json`: de → de-Links, alle anderen → en-Fallback (später sprachspezifisch erweiterbar)
- Übersetzungsstrings während Entwicklung als `__TODO__` – keine ausformulierten Strings

**Datei-Limit:**
- Diese Datei ≤ 500 Zeilen. Bei >400 Zeilen sofort warnen

beim starten einer sitzung prüfen ob eine datei vorgaben.txt existiert. wenn ja nachfragen, ob diese angewendet werden muss.

nach erfolgreicher implementierung oder relevanten änderungen immer fragen ob ein commit gemacht werden soll.

wenn funktionen geändert, hinzugefügt oder gelöscht wurden, nachfragen ob changelog angepasst werden soll.

bei neuen texten deutsch immer schon einfügen, alle anderen sprachen nur platzhalter
