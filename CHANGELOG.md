# NiCo – Changelog

## Unreleased

### Lucide icon set
All Unicode/emoji icons throughout the UI have been replaced with a consistent set of Lucide SVG icons (v1.9.0, ISC license). Icons are rendered via CSS `mask-image` with `background-color: currentColor`, so they inherit the surrounding text color and are fully theme-swappable. Icon sizes can be overridden per context via the `--ni-icon-size` CSS custom property. The 24 used SVGs are vendored locally under `nico/static/vendor/lucide/`; the theme stylesheet lives at `nico/static/themes/default/icons.css`. The NixOS logo and language flag emojis are intentionally unchanged.

### Security audit & CI
Initial security audit covering bandit, pip-audit, and a manual code review. No CVEs found in dependencies. One path-traversal bug in the brick file editor was fixed: `_modify_brick_in_file` now validates that the target path stays inside the config directory. GitHub Actions workflows for automated scanning (bandit + pip-audit on every push) and CodeQL analysis added. Dependabot enabled for Python dependencies and Actions versions.

---

## 0.9.2 (2026-04-23)

### Safer file and host switching
When switching between `configuration.nix` and host files, NiCo now protects more reliably against data loss. Save and auto-save only run when the form has been fully loaded for the currently open file. The section filter selection is also stored as a program setting and restored on the next start.

### Filter sections
The left panel now has a filter icon next to the collapse/expand buttons. Clicking it opens three options: show all sections, show only sections with content, or use a custom selection. The chosen view is preserved for the next start. Sections with content are always shown regardless of the filter.

### Adjust sections
Under Admin → Settings → NiCo Settings, “Adjust sections” can now be used to define which sections should be visible for the “According to settings” filter. The selection works like the validation rules: toggles per section, stored in the machine-local program settings.

### Settings tab reorganized
The admin tab “Settings” is now split into two sub-tabs: **NiCo Settings** (machine-local) and **Config Settings** (travel with the config). This clearly separates what is stored where.

### Validation before rebuild
Before a rebuild, the configuration can now be checked for common problems via a new “Validation” button in the NixOS menu. NiCo checks, for example, whether the current user exists in the config, whether all import paths exist on disk, whether the hardware configuration is imported, and whether disk UUIDs match the current system. For flake configs with multiple hosts, NiCo first asks which host should be validated, and hardware checks then target that host specifically. Which checks are active can be configured individually under “Adjust validation” in the admin settings, and that selection travels with the config.

### NixOS actions in the header
The single dry-run button has been replaced with a clearer NixOS menu. Clicking the NixOS logo opens three color-coded actions: save snapshot (green), dry run (yellow), and system rebuild (red).

### Better rebuild output
The rebuild window now shows both the raw output stream and a compact status monitor with progress bars and the current build process, similar to `nix-output-monitor`. Warnings and errors are highlighted in color.

### Rebuild log on failure
If a rebuild fails, NiCo now automatically writes a complete log file (`nixos-rebuild.log`) into the config directory. This can also be enabled for successful rebuilds in the settings.

### Rebuild without a Git repository
Flake rebuilds now also work when the config directory is not a Git repository. NiCo detects this automatically and passes the absolute path directly to Nix.

### Create `/etc/nixos` symlink
In the admin area, a symlink from `/etc/nixos` to the NiCo directory can now be created. This allows NixOS tools such as `nixos-rebuild` to work without an explicit path. The original is backed up as `/etc/nixos.bak`.

### Remote status in the Git banner (experimental)
If the local Git repository is behind the remote, a blue info banner appears on startup showing the number of missing commits.

### Prism.js is now local
The Prism.js syntax highlighter is no longer loaded from an external CDN. It is now served directly by NiCo. License and attribution are documented in `THIRD_PARTY_LICENSES.md`.

### Back up and restore program settings
In the admin area under “Export”, NiCo program settings (language, theme, view options) can now be downloaded as a JSON file and imported again on another device. The config path is intentionally left untouched during import because it is machine-specific and should not travel.

### Internal improvement: parser
The Nix import parser now uses tree-sitter for more accurate detection of configuration options and only falls back to the previous regex parser when the environment does not support it.

## 0.9.1 (2026-04-08)

Backup before import, sidebar with file tree, revised header navigation, ZIP export of all config files, and automatic file categorization on startup.

---

## 0.0.1 (2026-04-07)

First working foundation: Flask backend, configuration generator, panel UI, import, Brix system, Git time machine, Home Manager, and multilingual support (DE/EN).
