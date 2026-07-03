# NiCo – Changelog

## Unreleased

### Bug Fixes

- App settings moved to `~/.config/nico/settings.json` (XDG); an existing `nico-settings.json` next to the program is migrated once automatically – fixes crashes when NiCo is installed as a Nix package (read-only store)
- Raw editor: saving `configuration.nix`/`flake.nix` in raw mode (`#r`) no longer fails with ERR_MANAGED_FILE
- Custom `hosts_dir` from config.json is now respected everywhere (host save/write, preview, validator, frontend paths)
- Validator: `flake_host_exists` rule no longer broken by dict-shaped host entries
- Preview with flakes enabled no longer returns a server error before setup is complete
- Importer regex fallback (used when tree-sitter is unavailable) no longer splits statements at the `;` of `with pkgs;`/`assert …;` and now recognizes the same option set as the tree-sitter path

---

## 0.9.11 (2026-07-02)

### New Features

- Pre-fetch dry-run: before each rebuild NiCo runs a short `nixos-rebuild build --dry-run` to determine the total download size; enables a stable, accurate fetch progress bar in the rebuild monitor; can be disabled in admin settings
- Rebuild log viewer: after a completed rebuild a "Log öffnen" button appears in the rebuild overlay; opens the full `nixos-rebuild.log` in a scrollable overlay
- Config diff viewer: compare any two commits in the Zeitmaschine tab; also accessible via the NixOS menu ("Diff – letzter Commit"); shows only `.nix` files; `flake.lock` is listed as an info note instead of a raw diff

### Bug Fixes

- Diff viewer overlay was hidden behind the admin overlay (z-index fix: `#viewer-overlay` raised to 60)
- Diff file bodies now start expanded instead of collapsed
- NixOS menu "Diff" button is now visible immediately on app start (not only after opening the Zeitmaschine tab)
- Config diff now only shows lines that were truly added or removed; lines that merely changed position within the file (reordered by NiCo on write) are excluded
- `flake.lock` diff is suppressed to avoid thousands of hash lines; a short note is shown instead
- Write dialog (`writeFiles`) now aborts if saving the form config fails; previously the write step ran regardless, which could produce a host `.nix` file without brick blocks
- Redundant double-write of host `.nix` file in `/api/host/<name>/write` removed; brick data is now merged once and written in a single pass
- Time machine `rollback()` now restores **all** files tracked by git at the target commit (via `git ls-tree -r`), including `hosts/*/default.nix`, `home/`, `modules/`, and any subdirectory; previously only `configuration.nix` and `flake.nix` were restored
- Time machine rollback no longer shows a spurious error when all files were already at the target state
- All UI strings translated into all 7 supported languages (de, en, es, fr, ja, ru, zh)

---

## 0.9.10 (2026-06-27)

### New Features

- `.gitignore` management: automatically created with standard entries (logs, backups, zips) on `git init`; editor in the Zeitmaschine settings tab allows viewing, saving, and adding missing default entries
- Validator: three new rules — missing `.gitignore` (warning, with direct link to settings), log file larger than 100 MB tracked in git (warning), non-`.nix` files tracked in git (info)

### Bug Fixes

- Brick section "Inputs" / "Outputs" in flake.nix now correctly maps to the injection point inside the respective block (`inputs = {}` / `outputs = {}`); previously placed the brick after the final closing brace
- Security: command injection in symlink-create fixed — path in `sh -c` string now wrapped with `shlex.quote()`
- Security: path traversal in file API fixed — `str.startswith()` replaced by `Path.relative_to()` (startswith allowed `/nixos-evil` to bypass `/nixos` check)
- Security: theme name validation added — `..` and `/` rejected before path construction
- Security: `initialPassword` in generated Nix now correctly escapes backslashes and double quotes
- All new UI strings translated into all 7 supported languages (de, en, es, fr, ja, ru, zh)

---

## 0.9.9 (2026-06-03)

### New Features

- Safe rebuild mode: "Safe rebuild" toggle in the rebuild options dialog limits the build to 1 parallel job (`--max-jobs 1 --cores 4`); prevents system crashes during RAM-intensive compilations; state persists between sessions
- Rebuild monitor redesigned from 4 horizontal phase columns to 4 stacked rows; dot, label, and status columns align across all phases
- All UI strings fully translated into all 7 supported languages (de, en, es, fr, ja, ru, zh)
- Flake panel now loads NixOS channels dynamically from the official NixOS channel listing, with a local fallback
- Panel toggle redesigned as a card with a real toggle switch in the tab bar; save button replaces the eye icon in raw mode for a stable layout
- Snapper individually configurable with free subvolumes and per-entry snapshot schedule
- Git remote setup directly in the Config Settings tab
- Git remote sync on startup (branch assignment, ahead/behind display)
- Rebuild optionally runs in an external terminal window
- Default host for flake configs with multiple hosts is now persistent
- Rebuild progress shows stable totals instead of jumping per-activity values
- Theme system via TOML; included: Catppuccin Mocha, Breeze Dark/Light, Adwaita Dark, Neon Dark
- flake.lock optionally visible in the file tree
- HM panel: shell, initExtra and packages directly editable
- Validator: brix redundancy hint now shows the affected panel section
- Plymouth (bootsplash) support: enable/disable Plymouth per host, optional theme selection, automatic `boot.initrd.systemd.enable` dependency; full import/export roundtrip
- Kernel parameters (`boot.kernelParams`) directly configurable in the Boot section; full import/export roundtrip
- Flatpak support in the panel: enable Flatpak and manage remotes (name + URL) per host; Flathub quick-add button
- Config integrity check: `testing/verify_config.py` compares current config semantically against a reference ZIP and reports moved, changed, or lost data
- Git-Sync toggle in settings: disable remote sync (start guard, auto-push) while keeping optional local status display
- Settings panel: interface improvements and layout polish

### Bug Fixes

- Double-start protection: second NiCo instance shows info page instead of silent restart
- Home Manager flake input now follows the matching branch for the selected nixpkgs channel and validates matching `flake.lock` refs
- Home Manager config files now live in `hm_dir/<username>.nix` instead of a single root `home.nix`; the configuration form shows a live file list and a "Create HM file" button; validation detects missing flake references, missing files, and orphaned root `home.nix`
- Settings config path was only loaded when opening the Administration tab, not the Settings tab
- Panel toggle: race condition on rapid clicking no longer causes inconsistent state
- Brick move broken after switching to an HM file
- Firefox freeze during rebuild in the web UI fixed (DOM batching, max 500 log lines)
- Flake host brix: `specialArgs` and host-specific content was lost on roundtrip
- Duplicate attribute validation now checks all `.nix` files in the config
- NixOS data no longer incorrectly persisted in config.json
- bashrcExtra and other unknown shell fields no longer deleted on regeneration
- HM initExtra block no longer duplicated on every plain-text view toggle
- Home Manager enable toggle in CO form now correctly saves and generates home.nix (home_manager dict was accidentally dropped before save)
- `/api/files/info` now correctly detects Home Manager enabled state (wrong config key fixed)

---

## 0.9.4 (2026-05-01)

### Bug Fixes

- Flake-Update-Einstellung wird jetzt sofort beim Ändern gespeichert (kein Speichern-Button mehr nötig)
- `push_after_save` und `push_after_rebuild` wurden nicht persistiert (fehlten in `_CONFIG_KEYS`)

---

## 0.9.3 (2026-04-26)

### New Features

- Git startup guard shows a compact status panel before action cards: last commit on remote and local side, commits ahead/behind with message preview, uncommitted files with color-coded labels
- NiCo now sets `user.email = USER@hostname` and `user.name = USER` per config directory before every commit so git log shows which machine made each change
- Config can be detached from NiCo: creates a ZIP backup, removes NiCo marker lines from all `.nix` files, deletes the metadata JSON, and clears the stored config path
- Sidebar file tree: hover and context menus for files and directories; rename, delete, create, and `hardware-configuration.nix` import with `.bak` backup
- All Unicode/emoji icons replaced with Lucide SVG icons (v1.9.0, ISC license); CSS `mask-image` approach; theme-swappable; 24 SVGs vendored locally
- Initial security audit (bandit, pip-audit, manual review); GitHub Actions added for automated scanning and CodeQL; Dependabot enabled

### Bug Fixes

- Path-traversal bug fixed in brick file editor: `_modify_brick_in_file` now validates that the target path stays inside the config directory
- Many minor bugfixes

---

## 0.9.2 (2026-04-23)

### New Features

- Safer file and host switching: save and auto-save only run when the form is fully loaded for the currently active file
- Section filter: filter icon in the left panel shows all sections, only sections with content, or a custom selection; persists on restart
- "Adjust sections" under Admin → Settings → NiCo Settings to define which sections are visible for the custom filter; travels with the config
- Settings tab split into "NiCo Settings" (machine-local) and "Config Settings" (travel with the config)
- Validation before rebuild via new "Validation" button in the NixOS menu; per-host for flake configs; configurable rules travel with the config
- NixOS actions in the header: NixOS logo opens save snapshot (green), dry run (yellow), and rebuild (red)
- Rebuild window shows raw output stream and a compact status monitor with progress and current build process; warnings and errors highlighted
- Rebuild log written automatically on failure; can also be enabled for successful rebuilds in settings
- Flake rebuilds now work without a Git repository; NiCo detects this and passes the absolute path to Nix
- Symlink from `/etc/nixos` to the NiCo directory can be created in the admin area; original backed up as `/etc/nixos.bak`
- Remote status banner on startup when the local repository is behind the remote
- Prism.js syntax highlighter now served locally; no external CDN; license documented in `THIRD_PARTY_LICENSES.md`
- Program settings (language, theme, view options) can be exported as JSON and imported on another device; config path is intentionally excluded
- Nix import parser now uses tree-sitter for more accurate detection; falls back to regex when tree-sitter is unavailable

---

## 0.9.1 (2026-04-08)

### New Features

- Backup created automatically before import
- Sidebar with file tree
- Revised header navigation
- ZIP export of all config files
- Automatic file categorization on startup

---

## 0.0.1 (2026-04-07)

### New Features

- First working foundation: Flask backend, configuration generator, panel UI, import, Brix system, Git time machine, Home Manager, and multilingual support (DE/EN)
