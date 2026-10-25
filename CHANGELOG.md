# NiCo – Changelog

## Unreleased

### New Features

- New validator rule: a user name NixOS would reject is reported before the rebuild, for the main user and every additional user
- New validator rule: hardware-configuration.nix sitting in the root directory while flake.nix expects it under `hosts/<name>/` – the state an existing config ends up in after switching to flakes
- A failed rebuild or dry-run now points at the check, with a button that runs it straight away: most of these failures are things the check names in plain words
- Package search animates its status line with the search term and, after three seconds, the elapsed time – the first `nix search` can take a minute, and until now nothing moved on screen at all
- The setup dialog says that a new directory is created by simply typing its path; the directory browser only ever offered existing ones, and the question "where is your config directory" implied one had to exist
- The same hint when changing the config path in the admin panel, and in the directory browser when it is opened from setup
- New validator rule: a line in a user's extra Nix field that sets an attribute NiCo already writes from the form – Nix refuses an attribute defined twice, and the build stops at evaluation
- Saving now refuses to write a file in which the same attribute is defined twice, naming it, instead of leaving a config behind that does not build
- After a rebuild, the monitor offers what git can actually do: nothing without a repository, "create restore point" for a local one, "Commit & Push" only with a remote configured
- The options dialog before a rebuild now appears for non-flake configs too. It used to be skipped entirely, which also took away "run in a terminal", "copy command" and the gentle build mode – none of which have anything to do with flakes. The flake update is greyed out there instead of hidden, so the option is visible without getting in the way
- The info dialog shows the commit behind the version. A version number alone stays the same for a whole development cycle, so two builds of it were indistinguishable; a trailing `+` marks a modified working tree
- One word per language for each concept. Every language used four or five terms for the same thing – English alone had commit, snapshot, checkpoint and restore point, Spanish five. Buttons now read "create checkpoint (commit)": the plain term for newcomers, the git term in brackets for everyone who knows it
- `Nix-Brick` and `Nix-Brix` are treated as the name they are and stay untranslated in every language; they used to appear as `bloque`, `bloc`, `brique`, `ブリック`, `блок`, `кирпич` and `砖块`, so nobody could look the term up or talk about it
- `snapshot` is reserved for Snapper and btrfs snapshots and no longer doubles as a word for commits
- `install.sh`, a menu around the five nix commands from the README: try, install, update, and the same two for the development branch. It adds `--extra-experimental-features` by itself where flakes are still disabled, and prints every command in full before running it, so the script teaches the command instead of hiding it
- The check is called `Validator` everywhere now, untranslated in every language – that is what the help has called it all along, while the interface said "validation". A button also carries the name of the function it starts: the one under a failed rebuild said "Run check" and led to a menu entry named "Validation"

### Bug Fixes

- "Collapse all" left things open in the flake view: individual bricks were never folded there, and the per-host sections could not be, because the button worked off a fixed list of section names that cannot know a host is called `nix-vm`. The three views – configuration.nix, flake.nix and Home Manager – each had their own copy of this, and each copy had forgotten something else; there is one implementation now, and it reads the sections that are actually on screen
- A flake input written in the flat form lost its `url` on import. `yandex-browser.url = …` and `yandex-browser.inputs.nixpkgs.follows = …` are two statements about one input; they were stored under the input's name, so the second replaced the first. What was left declared an input without a url, Nix looked the bare name up in the flake registries, and the rebuild stopped with `cannot find flake 'flake:yandex-browser'`. All statements of one input are kept together now, and the same grouping applies to extra output keys
- Blocks NiCo only partly understands lost their remaining fields on the first save. Measured on a realistic config: 15 of 18 neighbouring fields, among them `services.openssh.settings.PasswordAuthentication = false`, `ports`, `services.printing.drivers`, `services.xserver.xkb.options`, `nix.settings.substituters` and `boot.loader.systemd-boot.editor` – several of them security settings whose loss goes unnoticed. Whatever NiCo does not write back is kept as a Nix brick now, filed under the section it belongs to
- Statement boundaries were found by counting braces and semicolons without regard for strings and comments, so a `{` or `;` inside either tore statements apart – polybar configs in `'' … ''` blocks are full of both
- The rebuild monitor offered "Commit & Push" after every successful rebuild, even when the config directory was no git repository at all – the button could only fail. Found on a fresh NixOS installation, where git is not installed to begin with
- Git errors were German plain text in 20 places (`"Kein Git-Repository."`, `"Kein Remote konfiguriert."`): they never passed through the translation layer, so every non-German user read German. They are error codes now, translated in all seven languages
- Pushing without a configured remote returned git's own raw output – itself localised, so the message language followed the system instead of NiCo's setting
- The interface assumed git and a repository were present until proven otherwise, which offered actions that could only fail; it now assumes neither until the status answers

- Everything in a `users.users.<name>` block NiCo has no form field for was dropped on the first save, without a word: `packages` (every graphical install starts with `kdePackages.kate` there), `hashedPassword`, `openssh.authorizedKeys.keys`, `home`, `createHome`, `linger`. Such lines are kept in the user's extra Nix field now
- A user declared with `isSystemUser` is no longer pulled into the form at all: the generator always writes `isNormalUser`, which NixOS refuses alongside it. The block survives untouched as a Nix brick instead

- A non-flake `configuration.nix` never imported hardware-configuration.nix, so a newly created config could not build ("The 'fileSystems' option does not specify your root file system") – flake setups were never affected, because there the host body in flake.nix carries the import
- A quoted user name gained one escaping layer per save (`nico` → `"nico"` → `"\"nico\""`) until NixOS refused it: attribute paths are now read in their canonical form, and a name already damaged is repaired on the next read instead of growing further
- The check reported a missing hardware import for configs that keep their imports in a Nix brick, and for flake hosts whose import sits in flake.nix – it is therefore an error now instead of a warning
- Package search: a failed request left "Searching …" on screen forever with no message, and a slow earlier search could overwrite the results of a newer one

## 0.13.0 (2026-10-18)

### New Features

- The file system section is now "Maintenance & snapshots" and starts with options that hold for every file system, with the btrfs jobs as their own group below – adding another file system later means one more group, not a rebuild
- Disk monitoring (`services.smartd.enable`): reports drive errors before a disk fails, independent of the file system
- `/tmp` in RAM (`boot.tmp.useTmpfs`) with its size (`boot.tmp.tmpfsSize`, percentage or bytes), and clearing `/tmp` on boot (`boot.tmp.cleanOnBoot`) – the latter is greyed out while `/tmp` is in RAM, because a tmpfs starts empty anyway, and keeps its stored value
- The deprecated spellings `boot.tmpOnTmpfs`, `boot.tmpOnTmpfsSize` and `boot.cleanTmpDir` are read on import and written back under their current names
- New validator rule: btrfs auto-scrub or auto-balance enabled while no btrfs is mounted on this machine, plus a separate finding when the balance job's `/` is not btrfs – the config builds and boots, so the jobs used to fail silently forever
- A note above the btrfs group when this machine does not run btrfs; nothing is disabled, because the config may well be edited for another host
- Sections are anchored by a four-character code in the generated files (`# ── FSYS · Maintenance & snapshots ──`, `# <brick: FSYS / #2 name>`), per-host zones as `HOST:<name>`. The anchor is language-neutral, so a section can be renamed in the UI without touching a single file
- The text after the code is always English, matching the Nix option names around it; the section name is translated in the panel, the code view and the brick dialogs. Previously every file carried German section names regardless of the interface language
- German section names written by earlier versions are still read and are rewritten to codes on the next write, including the `Host: name` form – a brick keeps its section instead of drifting to the end of the file
- Sections now have a single source (`brix.py`): order, the name written into files and the translation key. Frontend, validator and documentation links read it from `/api/sections` instead of each keeping their own list
- Files carry a format version in the `nico-version` line (`#f2`). It counts layout changes, not releases, and is derived from the markers actually present, so a file that was only re-stamped is not mislabelled
- Opening a config written by a newer NiCo now warns and lets the user decide instead of failing silently: nothing can be lost (unknown content is kept as a brick) but blocks may be reordered, so NiCo makes a safety commit before the first write and refuses writes until the notice is confirmed

### Bug Fixes

- The gear icon closes the admin panel again when it is already open, instead of only ever opening it – closing does not trigger the autosave that opening does, and an open sub-dialog keeps the panel in place

## 0.12.0 (2026-10-17)

### New Features

- The rebuild log is written to `~/.cache/nico/nixos-rebuild.log` instead of into the config directory – it is a NiCo artefact, not part of the NixOS config, and can no longer end up in git
- The rebuild log is capped at 5 MB, keeping the end where the error is, with a header stating how many lines were dropped – a single failed rebuild used to produce a 2.1 GB file
- Staging refuses files above 50 MB in every path (write, rebuild, quit): git history is effectively permanent, and one oversized file blocks all further pushes
- Deliberately adding an oversized file through the foreign-file dialog is now refused with a reason instead of quietly doing nothing
- Push is checked against the remote's 100 MB file limit before any upload starts, so an oversized file in the history is reported immediately instead of after a long, doomed transfer
- New push error reasons: file too large for the remote, and timeout – a timeout now says that an aborted transfer is not necessarily a defect
- Git hygiene check on every start: missing `.gitignore` entries, a leftover rebuild log in the config directory and tracked foreign files are reported in one dialog, which fixes the harmless ones directly and hands tracked foreign files to the existing cleanup dialog
- Files the size guard kept out of a commit are reported in a dialog after saving, instead of being left out without a word
- Cleanup for oversized files (Zeitmaschine, start check, or straight from the push error): shows per file whether it only sits on disk, in unpushed commits, or already on the server, and removes the first two – unpushed commits are folded into one new commit without the file, with a backup branch left behind until the user drops it
- Files already pushed are reported but never rewritten: that would need a force push and would break the clones on the user's other machines
- Push and pull now report what they moved – snapshot, description, date and machine, plus the changed files after a pull. Previously both only said "done", so there was no way to tell what had been transferred or what had just changed on disk
- "Git Pull" as its own entry in the NixOS menu: pulling was only reachable through the start guard, which reloaded the page without a word about what it had fetched
- The rebuild monitor closes itself after a successful commit and push instead of asking for a second confirmation of a finished job
- Nix bricks are checked for nesting before every write: a brick pasted into another brick is refused, because the parser cannot see the inner one – it disappears from the brick list and is deleted together with the outer brick without a word
- A nesting that was already on disk never blocks a write, it is only reported: refusing would lock the user out of every brick action in that file, including the one needed to repair it
- The raw editor asks instead of refusing – the markers are visible there and keeping them can be deliberate
- New validator rule `brix_nested` (warning): reports nested bricks in every `.nix` file of the config, not just in `configuration.nix`
- NiCo installs a desktop entry and icon, so it appears in the application menu instead of being a terminal-only command
- The browser tab shows the NiCo icon instead of a blank page
- All seven languages are complete again: 57 strings per language were still placeholders, among them the git hygiene dialog that runs on every start and the permanent "Git Pull" menu entry, which read `__TODO__` in every language but German
- Validator findings are now fully translated: the explanation below each finding was hardcoded German in every language, 30 of them
- A second rebuild is refused while the previous activation is still running, with a plain message instead of a Python traceback from nixos-rebuild — the old guard only knew about rebuilds started in the same NiCo session and missed one that survived a restart
- The About dialog shows which version is actually running: it read a number kept in the language files, which had been stuck at 0.9.10 while two releases went past it
- The About dialog is no longer dated: the "last updated" line had been wrong since June

### Bug Fixes

- The first start no longer compiles six packages on the user's machine: the package asked for a pinned Python minor version, which is not part of the binary cache
- Moving a brick now offers the bricks of the file that is open: for host, module and HM files the dialog listed the bricks of the root `configuration.nix` instead, so it either preselected a foreign brick or claimed there were none
- Deleting a brick through the button now acts on the file that is open: it always listed and deleted in the root `configuration.nix`, no matter which file was in front of the user
- "Collapse all" now collapses the Nix bricks as well – it only ever collapsed the sections, while "expand all" did include the bricks
- Clicking a brick inside a collapsed section opens that brick instead of only the section: the brick body stays hidden while the section is collapsed, so the click appeared to do nothing
- The collapsed marker in the preview is translated instead of being German in all seven languages
- A nesting of Nix bricks that was already known is no longer reported again after renaming the outer brick: it was remembered under both brick names, so a rename made an old finding look new
- `start.sh` always runs the source tree it sits in: once NiCo was installed as a package, it silently started the frozen build from the Nix store instead, and no restart would pick up any change
- A click on the background no longer closes any dialog: 15 places still did, and a window vanishing after a click next to it leaves no way to tell what happened or whether it did anything. Every dialog closes through its own button
- Push and pull no longer abort after 30 seconds (now 600): a push with a lot of data ran into the timeout, which was then reported as "network error" and sent the search in the wrong direction
- Failed git pushes were swallowed without a word: the push error dialog was defined inside another function, so every call from the rebuild monitor, the write dialog and the settings raised a reference error – the user saw nothing and only noticed at shutdown that nothing had been pushed
- Push error dialog now names the actual reason for combined commit-and-push actions (no key, no write access, non-fast-forward, …); the error code was dropped between `git_commit_push` and the UI
- Rebuild monitor: the push button no longer stays stuck on "…" after a failed push, and a failed automatic push now offers a retry instead of vanishing without a trace
- Push error dialog closes via its button only – a background click no longer discards the one message that explains the failure

---

## 0.11.0 (2026-09-06)

### New Features

- Help page: untranslated languages (es, fr, ja, ru, zh) now fall back to English instead of German
- Documentation (README, help chapters 1.1, 3.2 and 7): form-managed files are regenerated completely on every write – comments and formatting outside of Nix bricks are not preserved, only brick content is safe
- New validator rule `hm_duplicate_args` (error): reports duplicate arguments in the function head of Home Manager files – a Nix syntax error ("duplicate formal function argument")
- "NiCo is already running" page: new green "Open running instance" button next to the now red "Restart" button – a closed tab can be reopened without restarting the server
- Rebuild monitor survives tab switches, reloads and closed tabs: the rebuild runs as a background job on the server and buffers its output, so the monitor reopens with the full log on the next page load and shows the result even if the tab was gone while it finished
- New validator rule `swap_in_snapshot` (warning): detects a btrfs swapfile sitting in a subvolume that snapper snapshots – btrfs then refuses to snapshot that subvolume permanently, which the config itself never reveals because it builds and boots fine
- New validator rule `host_checks_pending` (info): multi-host configs now list which hosts have never been checked by the machine-dependent rules, or were checked against an older config state; the per-host result is stored in `config.json` and therefore travels with the config to the other machines
- All language files are fully translated again (es, fr, ja, ru, zh, en): btrfs balance fields and the three new validator rules – no `__TODO__` placeholders left
- Validator rule `host_checks_pending` is now translatable end to end: three case-specific message keys (`.never`, `.stale`, `.both`) instead of one key fed with a pre-built German sentence, so the message no longer stays German inside the other six languages

### Bug Fixes

- Rebuild monitor: a click on the overlay background no longer closes the monitor and tears down the running rebuild stream; closing is only possible via the close button
- Rebuild monitor: a dropped connection no longer ends the view with "connection lost" – the stream reconnects and resumes at the last received line
- Rebuild log: output that arrives while the tab is in the background is now rendered when the tab becomes visible again
- HM files: deleted header arguments (e.g. `lib`) stay deleted – the read/save normalization no longer forces `config, pkgs, lib` back into an existing function head; core args are only added when a file is freshly generated
- HM panel: saving arguments now deduplicates by base name, so a duplicate entry can no longer produce an invalid function head
- Git start guard no longer reports a deviation when a page load reattaches to a running rebuild: the working tree is dirty on purpose there (flake.lock, written .nix files are committed once the build finishes), and the guard offered to discard exactly those changes; it stays out of the way until the rebuild result has been acknowledged
- German language file: six duplicate keys removed (`admin.gitTitle`, `admin.settings.hmDir/hmDirHint/save/saved`, `errors.ERR_IMPORT_PERMISSION`) – the dead first entries silently lost to the later ones

---

## 0.10.0 (2026-07-17)

### New Features

- Help page rewritten: task-oriented structure with Nix-Brix as the leading chapter, corrected against the current code base (setup order, brick markers, foreign-file dialogs, detach), reduced from 11 to 7 chapters; German and English, other languages fall back to German

- Validation results and the rule list are now translatable (all 7 languages; non-German strings pending as placeholders)
- Package search links point to the configured nixpkgs channel instead of a hardcoded one
- New validator rule `host_orphaned`: reports host directories that are not wired into the build (imports/flake.nix) in both flake and non-flake mode
- Flake panel: new "System architecture" selector (x86_64-linux/aarch64-linux); exotic architectures from imported flakes are preserved as extra options
- New validator rule `flake_arch_matches`: hints when flake.nix targets a different CPU architecture than this machine
- All language files are fully translated again (validator strings, HM strings, new keys) – no `__TODO__` placeholders left, including the help page; 33 keys missing in non-German languages (git guard cards, push errors) added, orphaned `nd.*` keys removed
- Validation results are shown as numbered cards ("Hinweis N von M") in the same style as the git start guard dialog
- Validator rule `git_foreign_files` reports one combined message (file list, config.json note, git-history hint) instead of two separate findings; rule `flake_hm_nix_assert` (optional Nix snippet hint) removed
- Rebuild options dialog: the copyable command now uses the full config path (works from any directory) and wraps instead of scrolling horizontally
- Internal restructuring: `server.py` routes split into a `nico/routes/` package (10 modules) and the monolithic `app.js` split into 12 scripts under `nico/static/js/` – no functional changes
- Foreign-file guard: before a checkpoint or rebuild NiCo asks whether new non-config files should be included in git or excluded via `.gitignore`; silent auto-saves leave undecided files untouched (toggle in the time machine tab, default on)
- Cleanup dialog for already tracked foreign files: untrack (`git rm --cached` + `.gitignore` entry + commit, file stays on disk) or keep (remembered in `config.json`)
- New validator rule `flake_untracked_reference` (flake only): warns when a `.nix` file references a file that is not tracked by git – the flake build would fail
- Help chapter 7.4 "Fremddateien in Git" explains include/exclude, the flake pitfall and the cleanup (German; other languages pending)
- New "Btrfs auto-balance" toggle (filesystem section): sets up a systemd timer/service running `btrfs balance` with configurable interval (weekly/monthly) and dusage/musage thresholds (0-100, clamped server-side); round-trips through the importer like other options

### Bug Fixes

- Admin panel: the git sync and "status only" checkboxes are now actually persisted; the settings endpoint silently dropped `git_sync`/`git_status_only`
- HM panel autosave race fixed: a pending autosave timer survived switching to another HM file and wrote the new file's form values onto the old file's path (corrupted guenther.nix with martin's values on 2026-07-12). Pending saves are now flushed with the old values before the panel is repopulated; the preview only updates if the panel still shows the same file; dead `_saveHmPanelNow` removed
- Git start guard no longer destroys unpushed local commits: with uncommitted changes only (not behind the remote) it now offers "discard local changes" (keeps commits) instead of a hard reset to the remote; when local commits would be lost, the fetch action is marked red with an explicit warning
- Switching files no longer silently discards unsaved raw-editor edits; they are auto-saved like form changes
- The plain/annotated code view toggle no longer saves files as a side effect; it only re-renders the preview
- File tree no longer follows directory symlinks (e.g. nixos-rebuild's `./result` into the Nix store); sidebar loads instantly instead of taking seconds
- Rebuild host suggestion: the machine's own hostname now always wins over a saved default host; a stale value (synced/migrated from another machine) can no longer suggest the wrong host
- App settings moved to `~/.config/nico/settings.json` (XDG); an existing `nico-settings.json` next to the program is migrated once automatically – fixes crashes when NiCo is installed as a Nix package (read-only store)
- Raw editor: saving `configuration.nix`/`flake.nix` in raw mode (`#r`) no longer fails with ERR_MANAGED_FILE
- Custom `hosts_dir` from config.json is now respected everywhere (host save/write, preview, validator, frontend paths)
- Validator: `flake_host_exists` rule no longer broken by dict-shaped host entries
- Validator: `flake_host_exists` no longer reports every host as missing when flake.nix uses the attrset form (`nixosConfigurations = { name = …; }`) that NiCo itself generates; previously only the dotted form was recognized
- Preview with flakes enabled no longer returns a server error before setup is complete
- Importer regex fallback (used when tree-sitter is unavailable) no longer splits statements at the `;` of `with pkgs;`/`assert …;` and now recognizes the same option set as the tree-sitter path
- ZIP import and directory import now reject file paths that would escape the config directory (zip-slip)
- All user-entered string values (hostname, descriptions, time zone, snapper names, flatpak remotes, home-manager fields, …) are now escaped when written into `.nix` files; quotes or `${` no longer produce broken or injectable Nix
- "/etc/nixos kopieren" during symlink setup now creates a ZIP backup and auto-commit before replacing existing `.nix` files in the config directory
- Setup no longer rewrites an existing `configuration.nix` (data loss for unparsed content, before git init) and no longer plants a default config into an empty directory, which suppressed the import offer
- Setup import offer now follows the same rule as on restart: only when the directory has no `configuration.nix`/`flake.nix` yet, but then also without `/etc/nixos` (manual/ZIP import)
- First-run with an empty existing directory now applies the git settings and runs the git start guard before showing the import dialog
- Setup with a newly created directory: the symlink question now waits until the import dialog is closed instead of stacking both overlays
- Non-numeric boot menu limit no longer causes a server error; falls back to 5
- Zeitmaschine rollback now also removes files that were added after the target commit
- Config diff no longer swallows real changes when identical lines appear multiple times
- Requests with a foreign Host header are rejected (DNS-rebinding guard); the restart helper page requires a token
- Opening a file via GET no longer writes a type stamp unless the request comes from the NiCo UI itself
- Non-flake dry-run no longer writes a temporary wrapper file into the config directory; it checks exactly what a non-flake rebuild would build (`configuration.nix`)
- Rebuild staging unified: `git add -A` only runs for flake configs in a git repo (needed so flake eval sees new files) and now happens in both the streamed and the terminal rebuild
- `/api/hm/patch` only accepts `.nix` files below the configured home-manager directory; it can no longer rewrite arbitrary config files
- Import policy unified: hidden paths (`.git`, `.direnv`, …) are never copied, deleted or written by any import; browser-based import only accepts `.nix`/`.lock` files
- Changing a file's type now rewrites an already-typed `nico-version` header (previously a silent no-op that let UI and disk disagree)
- Legacy root `home.nix` is treated read-only; loading a config no longer rewrites it
- Flake preview uses the same data basis as saving; an existing `system` architecture is no longer shown as the default in the preview
- Newly generated flake host bodies respect the configured `hosts_dir`
- Removed debug console logging of configuration snippets from the code preview

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
