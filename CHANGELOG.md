# NiCo – Changelog

## Unreleased

### New Features

- Before export, detach, copying from /etc/nixos and the backup of an import, NiCo lists the files the action does not cover: symlinks leading out of the config, hard links, and paths in the Nix code pointing outside it (e.g. `/etc/nixos/hardware-configuration.nix`)
- Dry run runs in the background like the rebuild: its window comes back with the result after a reload
- Dry run and rebuild say when the config was changed while they ran – the result is about the previous state
- Move config: afterwards a window asks which config NiCo works with – the new place, another path, or none (setup starts again); before the move it lists what will not come along or will point nowhere
- Reading this machine: besides *Always allow* there is *Allow once* – valid until the page is reloaded, nothing is stored; a fourth switch *Installed programs* covers reading `nix profile list` for *Update NiCo*
- System state that was entered but could not be activated: the time machine offers *Activate again*, which repeats only the activation

### Bug Fixes

- Detach and categorize no longer write into files outside the config that are linked in by symlink; format check and Validator no longer read them
- Rebuild, dry run, system state switch, update, moving the config and restarting NiCo can no longer run at the same time – also not while a rebuild is still being prepared; the message says what is running
- Moving the config to another drive could lose it when deleting the old place failed half-way; now the copy is written to disk, read back and compared before anything at the old place is deleted, and a failed delete leaves the copy alone
- After moving the config, a failed save of the new path no longer leaves NiCo pointing to a directory that is gone
- Nothing can be saved while the config is being moved
- Importing a ZIP without a configuration.nix or flake.nix file at its top level is refused before anything is changed; before, a folder of that name passed and the old config was deleted
- A failed import puts linked files back as links and keeps file permissions (a file only the owner could read stayed readable for others); linked files outside the config are no longer read
- The choice panel/text of an import is saved first: if that fails, nothing is imported; if the import fails, the choice is reset
- Text mode no longer overwrites a file changed outside NiCo (another editor, Syncthing, git pull) while it was open: saving asks first – *Cancel*, *Show differences* or *Overwrite* (with a checkpoint of the outside state)
- Time machine: restoring stops before touching any file when the checkpoint taken first had to leave out a file above the size limit – its current state would otherwise have been lost
- NiCo no longer reads the list of installed programs (`nix profile list`) on every start; *Update NiCo* shows when NiCo runs from the Nix store and asks on the click
- *Update NiCo* updates the installation that is running – not a different profile entry (started from a checkout, or two entries for main and dev)
- Switching to a system state: when entering it worked but updating the boot menu failed, the monitor says so instead of showing the state as set for the next start
- Removing oversized files no longer rewrites already uploaded checkpoints when the branch was pushed by hand without a linked remote branch; if the boundary cannot be told, it stops

## 0.24.0 (2026-11-25)

### New Features

- Nix-Brick editor: syntax colors and bracket pairs while typing, like the text mode; the editor grows with its text
- Restart NiCo: the quit dialog offers *Cancel*, *Restart* and *Quit*; after an update the monitor offers *Restart NiCo* or *Later*. The open file is saved first, the tab reloads by itself
- Admin panel: the imports (ZIP, directory, program settings) and changing the config path run in one window, step by step, instead of rows opening below the buttons
- Admin area redesigned: one line per option with an ⓘ for the explanation, groups that fold (closed on opening), filled buttons – red where something is replaced or removed –, and windows that scroll on small screens; tabs in the order Settings | Time machine | Administration
- Version control (git) settings and upkeep (remote, .gitignore, foreign files, oversized files) moved from the time machine to the config settings; the clean-ups show what they found before anything is done
- Texts say *version control (git)* instead of plain *git*; the menu entries read *Upload (push)* and *Download (pull)*
- Move the config to another directory (Administration), including version control; offers to switch the /etc/nixos symlink
- Symlink /etc/nixos now sits below the config path in the settings and can also be removed (puts /etc/nixos.bak back)
- New Validator rule: warns when /etc/nixos is a symlink to another directory (with the release “read this machine: Validator”)
- Validator rules: one line per rule with an ⓘ and a legend saying what error, warning and note do
- Time machine: the lists fill the window; checkpoints can be loaded beyond the latest 30 (*Load more*)

### Bug Fixes

- The Validator settings did not recognise a flake config: flake rules were hidden and *Run Validator* did not ask which host to check
- The checkpoint list was loaded twice on opening, so the compare selection listed every checkpoint twice
- Quit no longer loses unsaved edits in text mode: the open file is saved first, as with the disk button; if that fails, NiCo stays open
- Clicking a Nix-Brick head no longer makes the view jump: the brick stays where it was clicked, also when its section opens
- Editing a Nix-Brick no longer makes the view jump: the caret goes to the clicked line and that line keeps its place, also after Save or Cancel
- The interface language is stored in NiCo's settings instead of only in the browser, so it survives another browser or cleared site data; a choice made before is taken over once

## 0.23.0 (2026-11-24)

### New Features

- Help menu: links to report a bug (Issue) and ask a question (Discussions); the issue form opens with the NiCo version filled in
- Help menu: *Email the developer* – no GitHub account needed; shows the address with *Copy address* and *Open in mail app* (subject and version filled in)
- The interface starts in the browser's language (English if it is not available); a language you chose stays as it is

## 0.22.0 (2026-11-22)

### New Features

- *Update NiCo* in the help menu: for a NiCo installed with `nix profile`, runs `nix profile upgrade --refresh` in the rebuild monitor (same branch it was installed from); the config is not touched, the new version is used after a restart. Hidden when NiCo was started with `nix run` or from a git checkout
- Every import first asks how you want to edit the config: *In text mode* – NiCo works like an editor, the import only copies and changes no file, not even a header line – or *With the panel* as before; the answer becomes the setting *Open files in text mode by default*

### Bug Fixes

- Import: `configuration.nix` and `flake.nix` were rebuilt for the panel even when you then chose *Open in text mode* – the import did it before asking
- With *Open files in text mode by default* on, a file switched to the panel fell back to text mode after its first panel save, and a background save could still rewrite a file shown in text mode; panel files now carry their mode, and files without a mark follow the setting everywhere
- Switching a file without a NiCo header line to the panel (or to text mode against the setting) silently did nothing
- Text mode after an import: NiCo still opened `configuration.nix` in the panel, asked whether to rebuild it, and showed no panel switch – the setting *Open files in text mode by default* never reached the file view, and a file NiCo never wrote counted as changed outside NiCo
- Settings → *Open files in text mode by default* had no effect in the file view
- Settings and first-run setup: when `/etc/nixos` is a symlink to another directory, NiCo offers to switch it to this config directory (it used to fail with "symlink points to another directory"); `/etc/nixos.bak` stays as it is
- Import: the boot loader of a freshly installed NixOS (systemd-boot or GRUB) stayed out of the panel's *Boot loader* field – the panel would have added `configurationLimit = 5` and `canTouchEfiVariables = false`, which the installer does not write. The panel now writes `canTouchEfiVariables` only when ticked, and *Max. configurations* has its own switch (off after importing a config without a limit, so an imported limit can also be removed)
- Validator: new check *Boot loader set* – a host without one makes NixOS turn on GRUB, and the rebuild stops without a target device

## 0.21.0 (2026-11-21)

### New Features

- NiCo reads only the config directory unless you allow more: three switches under Settings → NiCo → *Read this machine* (Validator checks of users, groups and file systems; hardware check and search; system generations), all off by default, each with an *Allow* button where it is needed. Without them the Validator says once which checks it left out, the hardware check stays silent, and NiCo looks into `/etc/nixos` only after a click on *Look in /etc/nixos*. The safe rebuild reads the free memory only once it is chosen
- Time machine with two sides, *System | Config*: the System side lists this machine's system states (generations); at the end of each row one box says what it is – green *This system state is running*, yellow *Used at the next start*, or the button *Use this system state*, which switches now or from the next start, in the rebuild monitor. With another state chosen for the next start, the green box is a button that takes the choice back. After the switch NiCo returns to the list. The config files are not touched
- First-run import: *Import from ZIP* next to *Look in /etc/nixos*
- Text mode: a bracket without partner is shown inverted, the caret on or next to a bracket shows its partner; after opening or saving a file Nix cannot read, the error line is tinted and the character Nix stopped at is marked

### Bug Fixes

- Validator: user, group and stateVersion checks read only active Nix code – a commented-out password or `wheel` no longer counts as set, `users = { mutableUsers = false; }` and `lib.mkForce false` are recognized, `uid = 1234 + 1` is no longer read as 1234, and modules written as functions (e.g. in a flake's module list) are checked too
- Switching to a flake and adding a flake host put every file back when the last write fails (e.g. disk full) instead of leaving a half-changed config; the message says whether everything was restored
- Restructuring a file no longer cuts a line at a `#` inside a nested string such as `"rgb(${lib.removePrefix "#" color})"` – the result was invalid Nix; NiCo's check before writing now also refuses any result Nix cannot read
- Validator: a Nix-Brick holding only comments (such as `imported-notes` after an import) is no longer reported as something the panel could take over
- The safe rebuild is off by default again (a saved choice stays as it is): its suggestion reads the free memory, which NiCo does only once you choose it
- Questions and messages (restore a checkpoint, create a directory, delete a file or host, quit, …) are NiCo dialogs instead of the browser's own
- The Validator result uses the full height of the window
- Time machine: bringing back a checkpoint is called *Restore* in every language, no longer *Reset* or *Rollback* – in Nix, rollback means returning to an earlier system generation, which is a different thing

## 0.20.0 (2026-11-20)

### New Features

- A file changed outside NiCo (an editor, Syncthing, `git pull`) or never saved by NiCo is no longer restructured silently. NiCo checks the hash of every file a form writes – `configuration.nix`, `flake.nix`, host and Home Manager files – and asks before the form takes it over: restructure (a checkpoint of the state from outside first, a ZIP without Git), show what restructuring would change, or open it in text mode, which leaves the file as it is
- Settings the panel would add on its own are written into the Nix-Brick as a commented-out suggestion; uncommented, the Validator reports that the panel can take them over
- Nix-Brick menu: *Move into panel* takes a brick into the form when the panel writes exactly the same; otherwise it names what would change
- Save button for the panel as well as text mode, and Ctrl+S in both; a red dot on the button and a star before the file name show unsaved changes, and closing the tab with unsaved changes asks first
- A file that opens in text mode only gets a warning on the left with the reason – for a syntax error with the line as the editor counts it; after saving NiCo checks again, so a fixed file gets its panel switch back (set to text) and a newly broken one loses it
- Text mode (formerly "raw mode"): the editor now colors the Nix code like the panel preview, search hits are marked in it in every browser, and long lines scroll sideways instead of wrapping, so the line numbers stay right; the panel switch reads "Panel | Text"
- Notices stack instead of covering each other, warnings stay 8 and errors 10 seconds, and a click closes them
- The header shows the path of the open file relative to the config directory instead of only its name (shortened at the front when space is short)

### Bug Fixes

- Importing or restructuring a file no longer changes what it says: a setting the form would write differently, or would add on its own (e.g. `openFirewall = true`, `gdm.enable = true`, NetworkManager), stays in the file as a Nix-Brick instead
- Overwriting or restructuring a file changed outside NiCo goes ahead only when its previous state is really secured – the checkpoint is checked against the file, an ignored or never committed file goes into a ZIP, and without git a ZIP is made at all
- After a ZIP import, foreign host and Home Manager files are no longer marked as written by NiCo; opening them asks before the panel takes them over
- The Validator no longer reports a missing password for a user whose settings stand partly in a Nix-Brick
- Text after the last closing brace of a file – also a single dot – now makes it a text-mode-only file, as a letter there already did, instead of offering a restructure that could not be done
- Saving the rest of the config no longer rewrites a flake.nix that was changed outside NiCo; it stays as it is, and opening it asks
- An imported flake.nix is left as it is when NiCo would write it differently (e.g. a home-manager URL without branch)
- Restructuring a host file no longer drops settings of partly understood blocks (`networking.useDHCP`, static addresses)
- NetworkManager switched off in the form no longer comes back on with the next save or package change
- Home Manager: a disabled shell no longer comes back enabled, a commented-out Firefox line no longer turns it on, and spaces in names and paths are kept
- Switching from text mode to the panel asks first when the panel would write the file differently – settings it does not know become Nix-Bricks; before, the switch could drop them without a word. The same dialog: restructure, show differences, or stay in text mode. In text mode a file changed outside NiCo only gets a notice
- Restructuring a `flake.nix` generated by NiCo no longer doubles its host Nix-Brick
- Opening `configuration.nix` or `flake.nix` no longer rewrites them when they were changed outside NiCo; host and Home Manager files had their hash not checked at all and were restructured on the next save without a checkpoint
- Saving the form no longer writes a `configuration.nix` that defines an attribute twice (a form value next to a Nix-Brick setting the same one) – it is refused with the names, as for host files
- A read error right before a form save no longer writes the file anew without its Nix-Bricks; the save stops with an error
- Adding a flake host reports when its `default.nix` cannot be written, instead of success with a flake pointing at a missing file
- Restoring a checkpoint no longer reports success when git could not remove a file added after it
- The permission to edit a config from a newer NiCo no longer carries over to another config directory in the same run
- The Home Manager file dialog and two labels showed translation keys instead of text
- Changing a Home Manager value whose old string contained an escaped quote no longer leaves part of the old value behind
- An empty `system.stateVersion` no longer leaves a `# TODO: set NixOS state version` line in the file; the Validator and the hint at the field say where the value belongs, and an existing line disappears with the next save
- A commented-out `system.stateVersion` no longer counts as set for the Validator, also behind a statement or inside `/* … */`
- The rebuild monitor no longer lets the remaining download size grow again: finished downloads stay counted, and the planned total is shown at once ("determining download size" until it is known)
- A refused Home Manager save no longer shows as saved: the red dot stays, the save button reports the error, and switching files, dry run and rebuild wait until the file is saved
- Rebuild in a terminal, dry run and the monitor stage the same files (foreign and oversized files stay out), and a failing git step stops them before Nix runs
- Switching from text mode to the panel with edited text secures the previous state first; if that is not possible, the file is left as it was
- A stored host check now goes stale when a shared module changes, not only files of the host itself
- Two ZIP backups in the same second no longer overwrite each other
- Importing a config no longer makes users admins: an empty `extraGroups` list, or none at all, came back as `networkmanager` and `wheel`, and an empty selection in the form was written as `wheel`. NiCo never adds `wheel` by itself now, and new users in the form start without it; a new red Validator finding stops the rebuild when no user is in `wheel` (it can be forced, e.g. for a server run as root)
- Importing a config no longer turns on auto-login when `autoLogin.enable = false` stands next to a user name; such lines stay as they are, as a Nix-Brick
- An import that cannot read its source completely no longer deletes the old config first; a write error puts the previous files back, and neither case reports success with half a config any more
- A file changed outside NiCo is asked about also when a second tab has loaded the newer version in the meantime, and a deleted file counts as changed instead of being recreated from the old form
- Two rebuilds started almost at the same time (two tabs, a double click) no longer both run; the second gets "rebuild already running"
- "Show differences" in the restructure and changed-outside dialogs no longer says "no changes between these commits" when only NiCo's header line differs
- A new `.nix` file created in the file tree starts as a valid empty module instead of an empty file Nix cannot read
- Search (Ctrl+F or the tools menu) now works in every file NiCo shows – panel previews, text mode and the flake.lock view; before, in the editor and for flake.nix or Home Manager files it searched an outdated preview and counted hits that could not be seen
- A file Nix cannot read (e.g. a stray `};.` in a Nix-Brick) no longer opens in the panel, only to fail on the panel switch, "show differences" or restructuring; it opens in text mode with the reason, and restructuring no longer drops unreadable text
- Rebuilding a different host than this machine's own (after deleting it from the flake, or a wrong pick) is stopped by the Validator when that host's hardware config names disks this machine does not have; before, the check only looked at this machine's own host
- Two hardware configs outside the host directories no longer overwrite each other in the copy NiCo keeps before an import
- ZIP export and ZIP backup no longer pack the content of links that point outside the config directory; the export names what it left out
- Deleting the last flake host is refused instead of reporting success and writing the host back
- In a git repository the dry run now reads the same flake as the rebuild, so it no longer passes with files the rebuild does not see
- Package search says that the versions shown come from this machine's nixpkgs and can differ from the config's channel

## 0.19.0 (2026-11-18)

### New Features

- Comments outside Nix-Bricks are no longer lost when NiCo regenerates `configuration.nix`, a host file or a Home Manager file – on import, after editing in raw mode, or when they were added from outside. They are collected in one Nix-Brick per file, `imported-notes`, each block headed by where it stood (`before networking.hostName`, `inside services.pipewire`, …); a comment directly above a statement that becomes a Nix-Brick anyway moves into that brick with it. Blank lines and formatting outside bricks are still not kept

### Bug Fixes

- A file changed outside NiCo while its form was open (Syncthing, `git pull`, an editor, a second NiCo) is no longer overwritten with the old form values on the next save. NiCo asks instead: reload the form from the file, show the differences, or overwrite – then the state from outside is kept in a checkpoint first
- Switching from the panel to raw mode saves `configuration.nix` and host files even without changes, so the raw editor shows the same file the panel preview showed – rescued comments and new Nix-Bricks included. Before, raw still showed the old file until the next save
- Rebuild output: the "Show all Nix messages" line has the same space above and below
- The eye, floppy and wrench icons above the file view are larger (18 px) and easier to spot

## 0.18.0 (2026-11-17)

### New Features

- Rebuild and dry-run show a turning `| / - \` with the elapsed time while waiting, so a long stretch without output no longer looks stuck; the rebuild counts from the job's real start, also after a reload

### Bug Fixes

- Settings written outside a Nix-Brick are no longer lost on the next save in the panel: in `configuration.nix` after editing in raw mode and switching back (raw save re-stamped the file, so it no longer counted as changed), and in host files after any edit outside NiCo, which had no rescue at all. Whatever the form cannot hold now becomes a Nix-Brick on every panel save; comments outside bricks are still not kept
- Raw mode: a background save of the form (opening the admin area, Validator, dry-run, rebuild, switching files) no longer rewrites a file that is open in raw mode. It took the file out of raw mode, replaced raw edits of settings the form knows with the old form values, and the next raw save failed with "This file is managed by NiCo". Switching from the panel to raw mode now saves the panel first, so its unsaved changes are in the file the raw editor shows
- Rebuild output: the "Show all Nix messages" switch sits on the same line as its label again, right-aligned, instead of below it

## 0.17.0 (2026-11-15)

### New Features

- Safe rebuild: the limits are no longer fixed at `--max-jobs 1 --cores 4`. NiCo suggests values for this machine from the free memory (2 GB per compiler thread, after a reserve for the desktop), and both can be set by hand; an empty field means automatic
- Safe rebuild is switched on by default; a choice that was already saved stays as it is
- A rebuild that built and activated the new system but could not restart every service is no longer shown as failed: the result is yellow, names the services, and the checkpoint is offered as after a successful rebuild. The case is also recognised where `nixos-rebuild` reports status 4 only in the last line of its traceback (seen on NixOS 26.05). The result and the boxes with services and error lines now close the output window instead of standing below the phase bar; phases and buttons keep their place
- A system package can be deactivated instead of deleted: a switch in the package list takes it out of the build, the entry stays in the list greyed out. In the file the names are kept in a Nix-Brick of their own (`nico-inactive-packages`), managed by NiCo. Deleting that brick drops the deactivated packages for good: they leave the package list as well
- Rebuild monitor: the flood of Nix detail messages (evaluated files, store links) is off by default, so warnings and notes stay visible; the monitor now shows what a rebuild in the terminal shows, and the log file no longer fills up with them. A switch "Show all Nix messages" in the rebuild options and in the monitor brings them back, also while a rebuild is running; the choice is remembered per machine. The debug lines of `nixos-rebuild` and the frames of its Python traceback are hidden in the monitor the same way, with the last line of the traceback kept; these always go into the log file
- AppImage support: a field for extra libraries (`programs.appimage.package` with `extraPkgs`), for AppImages that stop with "cannot open shared object file"
- "Create flake.nix" in Nix & System turns a classic config into a flake config in one explicit step: checkpoint of the state before, host directory, hardware config moved into it, `flake.nix` written. The dialog asks for the name of the host; it is entered as the hostname of the machine as well

### Bug Fixes

- Firefox language packs and preferences were not shown in the form after loading; the next save removed both from the file
- `programs.appimage.binfmt = false` is no longer switched to `true` by the form; such a block stays a Nix-Brick. A `programs.appimage.binfmt = true;` written on its own line no longer comes back as a Nix-Brick next to the generated line
- A classic config that allows the flake commands (`experimental-features`, needed for `nix run github:…`) no longer gets a `flake.nix` pointing at directories that do not exist, and keeps its hardware import. Flake mode now means "a `flake.nix` exists"; the switch only controls the `experimental-features` line and is labelled that way
- A flake config without that line is built with `--flake` instead of the classic way
- Rebuild and dry-run find the hosts of a `flake.nix` NiCo cannot show as a form, instead of stopping at "no hosts found"
- Importing a flake config no longer adds the `experimental-features` line on its own and no longer creates a `configuration.nix` next to a `flake.nix` that has none
- Import no longer changes or drops values it cannot hold in the form; they stay in the file as a Nix-Brick instead: groups with a hyphen (`systemd-journal`), firewall ports given as an expression, a third `experimental-features` entry, `${…}` in the host name, `boot.kernelParams` with `++`, package and font lists with expressions (`python3.withPackages (…)`), a real user named `gast`, `home-manager.sharedModules` with expressions
- Desktops NiCo has no form entry for (MATE, LXQt, Plasma 5) and snapper configs with further fields are kept instead of disappearing
- Home Manager files: a `programs.git` block with `aliases` or `signing`, own `xdg.userDirs` paths, packages with a dot in the name (`pkgs.kdePackages.kate`) and `${…}` in the home directory survive saving; a `#` inside a string (`"#1e1e2e"`) no longer cuts the line and leaves invalid Nix
- Files are stamped with format `#f3` only when they really contain a deactivated Nix-Brick; before, every file saved through the form got `#f3` and locked out a NiCo 0.14.0 on a second machine
- The rebuild output is limited while it is collected, not only when the log is written; a very long build no longer piles up in NiCo's memory
- Importing a hardware config no longer overwrites an existing `.bak`; the next free backup name is used
- "Initialise git" leaves oversized files out of the first commit, like every later commit does
- Deleting or renaming a symlink in the file tree affects the link, not the file it points to; hidden entries such as `.git` are refused
- Snapper limits that are empty or not a number fall back to the default instead of producing invalid Nix; a package name with anything but a plain attribute name is refused
- Error text coming from nix is escaped before it is shown in the rebuild monitor
- The validator now reports a config that has no `hardware-configuration.nix` at all and no line importing one; before, only the dry-run failed. Configs that get their file systems another way (written by hand, disko, WSL, a container) are not reported
- Rebuild monitor, dry-run and syntax check: a line counts as an error only where `error:` stands as a word of its own. The headline of a failed rebuild no longer quotes a `debug:` line of `nixos-rebuild`, and the last line of its traceback (`CalledProcessError:`) is no longer painted red
- Directory names in `config.json` (hosts, modules, Home Manager) must be relative names inside the config directory; a `config.json` that came along with a cloned config can no longer point writes elsewhere
- Requests that fetch from the git remote, search packages or store the default host require the session token; with "sync with remote" off NiCo no longer contacts the remote for the status display
- A config directory that sits inside a larger git repository (dotfiles) no longer gets a nested repository of its own during setup; "Initialise git" explains why instead
- The page shown when NiCo is already running, a failed `nix flake update` in the dry-run and a few git messages appeared in German in every language; they are translated now
- Removing a flake input that is still in use now names the Nix-Brick using it
- A Nix-Brick whose end marker is missing or misspelt no longer takes the rest of the file with it. Its content stays in the file as ordinary text and the Nix-Brix behind it are found again; before, the next load reduced the file to its skeleton
- A Nix-Brick in a zone the file does not have (a host that is gone, outputs extra in a file without outputs) is placed in front of the closing brace instead of disappearing
- Loading no longer creates a `configuration.nix` next to a `flake.nix` that has none, and an externally changed file is written once instead of twice
- The time machine takes a checkpoint before it goes back, so a state that was not saved yet is no longer lost
- Cleaning up oversized files only deletes files the report itself lists; a path from outside the config directory or a small file is refused
- A config file NiCo cannot show as a form is no longer rewritten. Files built as `let … in`, `lib.mkIf`, `with lib;`, `{ … }@args:` or `rec { … }` lost that part on the first load; they now stay byte for byte as they are and open in the raw editor, with a note saying why. The same holds for host files and Home Manager files
- Importing a foreign `flake.nix` no longer replaces its `outputs` block with a single default host. A flake NiCo cannot represent – `outputs` with `@inputs` or `let`, flat `inputs.x.url`, `nixConfig`, hosts from another nixpkgs, a pinned or forked home-manager – is left untouched and edited in the raw editor
- The nixpkgs channel is read from `github:nixos/nixpkgs/…` in lower case too; before, such a flake silently moved to `nixos-unstable`
- With both home-manager and plasma-manager as inputs, switching off "follows" for home-manager now stays off

## 0.16.0 (2026-11-08)

### New Features

- New Validator rule: options that nixpkgs has removed are reported before the build, with file, line and the reason nixpkgs gives. The list comes from the nixpkgs this config is locked to, so it needs no upkeep and follows future releases; nothing is downloaded, and without a local copy of that nixpkgs the rule stays silent
- Before a rebuild starts, NiCo says which packages will be compiled on this machine and offers the safe rebuild. Packages without a binary-cache copy are built from source — anything with an unfree part, for instance, because Hydra never builds those. bambu-studio is such a package, and compiling it on every nixpkgs update ran a machine out of memory. The forecast runs after writing and the Validator, on the written state; the glue every rebuild builds (unit files, /etc, the system path) is not reported, only real packages. The dialog offers building safely (one job at a time), building normally anyway, or cancelling — a running rebuild cannot be stopped, so the question has to come first. With "update flake" switched on, the forecast resolves the inputs afresh in memory and leaves flake.lock alone. Controlled by the existing pre-rebuild analysis switch in the admin area, on by default

### Bug Fixes

- When NiCo is already running, the start page says so if a different version is running than the one you started, and restarting switches to the new one
- The NixOS menu says when no remote is set up, and shows Git push and pull right after start instead of only after the admin area was opened
- Choosing install in the menu now replaces an existing installation; before, Nix kept the old version and reported success. The menu shows the installed version and commit
- A rebuild now reacts at once: a window names the current step until the forecast appears, and a second click no longer starts a second run
- The refusal to write a duplicate attribute now also covers `flake.nix` and host files, as it was announced to. When a save is refused, no file is touched
- The hardware stash keeps files from earlier imports selectable, and a file NiCo could not judge no longer replaces one confirmed as this machine's
- Replacing a hardware configuration no longer overwrites an earlier backup; each replaced file is kept under its own name
- Loading the configuration now requires the CSRF token. The request can rewrite externally changed files, and any web page open in the browser could trigger it
- NiCo writes the current option names for GNOME, GDM and graphics. The old names still built, but only through aliases that nixpkgs drops sooner or later; older configs using them are still recognised on import
- NiCo no longer writes `boot.initrd.systemd.enable`. On nixos-unstable and from 26.11 the option stops the build, and enabling Plymouth used to switch it on without asking. The switch is gone: systemd in the initrd has been the default since 26.05, and Plymouth does not need the option. An imported `= true` is dropped, an imported `= false` now stays in the file as a brick instead of being lost
- A flake input added through the brick dialog is now usable right away. The argument list of `outputs` is derived from the input bricks, but only the generator wrote it, and the generator runs on a panel or form change, not on a brick write — so the new input stayed unbound until some unrelated field was touched, and a host brick using it (`{ environment.systemPackages = [ solidcraft.packages.x86_64-linux.default ]; }`) was refused as unreadable. Every brick write in `flake.nix` now brings the list in line: new input names are added, names whose brick was deleted, renamed or deactivated are removed, the ones NiCo manages and any added by hand stay
- A brick refused as unreadable now says why. The server sent the Nix message as `detail`, the interface looked for `details`, so the reason never arrived and only the generic sentence was shown. The first line of the Nix message and its position are appended now, for example `undefined variable 'solidcraft' (4:38)`
- A failed package search showed a literal `{0}`. The cause was sent in a field the interface never read, and the texts behind it were German for everyone. The search now reports its cause in the interface language, including the hint that the first call may have to download nixpkgs
- Messages from the backend reach every user in their own language. Twenty-one places sent fixed German or English sentences: package search, the rebuild monitor (failed `nix flake update`, missing `nixos-rebuild`), four checks in the git routes, the start-up git check, and the details of four Validator rules (duplicate attributes, home-manager without a branch, foreign files in git, untracked referenced files). They travel as codes with data now, translated in all seven languages. The two status lines in the rebuild log are English like the tool output around them (`── log saved: … ──`), and a parse check without a Nix message reports the exit code instead of a German sentence. A new test keeps fixed sentences out of exceptions, responses and Validator details
- The time machine section carries its own name in every language. Below the tab "Time machine", five languages titled the section "Git history & rollback" — the other concept: the time machine is the tool for going back, the history is git's data structure. The section is "Git / Time machine" everywhere now, the German text no longer mixes in the English "History", and an error that spoke of the "Git log" now says the checkpoints could not be loaded
- The download size before a rebuild is shown again. The analysis called `nixos-rebuild build --dry-run`, which the Python rewrite of nixos-rebuild rejects, so it failed without a word and the progress bar never had a total. It also only understood sizes in MiB, while nix writes KiB or GiB depending on the amount. Now `dry-build`, all units, and the result is reused for the build instead of evaluating the config a second time

## 0.15.0 (2026-11-01)

### New Features

- The options dialog before a rebuild now lets you pick the mode: apply now and boot into it (`switch`), only from the next start (`boot`), or only now with the boot order untouched (`test`). The server has understood all three for a long time; the interface always sent `switch`. `test` is what makes trying NiCo on a real machine reasonable — it activates the new system but leaves the bootloader alone, so a reboot brings the old one back. The README now describes that route in full, including the three things a rebuild of the old config does not undo
- A Nix-Brick can be deactivated instead of deleted: its body is commented out in place, so the content stays in the file and Nix never sees it. Deleting was the only option, and a brick one might want back next month had to be kept somewhere else — NiCo has nowhere else, because config data belongs in the .nix file. The entry sits in the brick's own menu, the row is greyed out and marked, and activating it again restores the body byte for byte. The marker carries the state (`# <brick: SERV / #1 name inactive>`), which is also what makes it survive a rename, a move or a split; splitting leaves both halves deactivated, because splitting does not judge what the content does. A file holding at least one deactivated brick is format 3 — without one it stays format 2, so an older NiCo keeps working on it unchanged
- A failed rebuild now shows the lines that say why, above the log, and keeps them in the saved log whatever else is dropped. Until now nix had the last word with `Cannot build … 1 dependency failed`, which names nothing: the line that does name something is written by the builder, and those were thrown away unread. The log was no help either – it is capped at 5 MB and keeps the tail, and with `--log-format internal-json -v` the tail is store-optimisation chatter. One failed run on 2026-11-01 dropped 2,540,770 lines and with them the only mention of the package whose test suite had failed, which made a working flake.nix look corrupted for days. The error lines are now collected apart from the log, so the cap cannot reach them, and the package that failed is named in front of its own output
- The state version field says where the value belongs. In a flake with two hosts or more it is the host file, and the form gave no hint of it – worse, the obvious assumption is wrong: a host value does not win over the one in the base config, Nix refuses an option defined twice and stops the evaluation. The field now names which hosts carry their own value, which do not, and what happens if both places have one; with a host file open it names that host instead
- New validator rule: a `.nix` file Nix cannot read. It was the one thing the rule set did not check — 38 rules looked at state versions, uids and mountpoints, and none of them at whether the files parse. So the Validator passed a config whose `flake.nix` had a stray word in a Nix brick, the build started, and `nixos-rebuild` answered with a Python traceback: it runs nix with `--log-format internal-json`, and the readable message is gone by the time the exception is raised. The rule names the file and passes the Nix message through, with its line, before anything is built. It also catches a name nothing binds — an input used but never declared — because resolving names is part of reading the file

### Bug Fixes

- A failed rebuild now reports what went wrong during activation, not only during the build. The activation phase writes plain text, not the JSON stream the rest of the output uses, and only the JSON was searched for error lines — so a rebuild that built fine and then could not restart one service showed a Python traceback and nothing else. Measured on a real run: the box held two copies of `warning: Git tree is dirty` while the line naming the service was missing. Warnings no longer qualify at all; one that appears on every single flake rebuild crowds out the cause it is supposed to sit next to
- The brick context menu is no longer see-through. A brick inside a collapsed section is dimmed to show it is collapsed, and the dimming was applied to the brick — but `opacity` opens its own compositing context, so everything inside went with it, including the menu, which hangs below the head and outside the brick's box. With the sections collapsed that is the normal state, so the menu sat washed out over whatever was behind it. The hint now sits on the label instead, where it belongs; the same correction applies to a deactivated brick. The menu also carries a solid fallback colour, in case a theme ever forgets the variable
- The brick menu no longer disappears while the mouse is on its way to an entry. The action bar was shown only while the pointer was over that brick, and the menu opens below it — with the section collapsed the brick is one line tall, so moving down left the brick, took the bar away mid-motion and handed the hover to the brick underneath. An open menu now keeps its own bar visible
- Every git error was reported as "unknown". NiCo reads git's output to tell one failure from another — no SSH key, file too large, not fast-forward, authentication failed, nine cases in all — and every one of those comparisons is written in English, while git speaks the system language. On a German system none of them ever matched, so nine distinct problems arrived as one unhelpful message. Measured, not guessed: git answers "Fehler: Fehler beim Versenden einiger Referenzen" there. NiCo now runs git with a fixed language, which also makes the output it passes on the same for everyone — and more useful in a bug report. The one translated phrase that had been bolted onto a single comparison, the only one that worked on a German machine, is gone with it
- Commit messages NiCo writes are English. They are not interface text: they land permanently in the user's repository, visible in `git log` and on a hosting site. Translating them to the interface language would make two machines with different languages write a mixed-language history into one repository, and break searching it across them — the same reason the section names in the .nix files are English. Eleven labels, one of which was also missing the `NiCo:` prefix
- The remaining git messages are translated too. Twenty-three sentences had survived the October round because they do not look like translatable text — "git push failed", "no upstream branch configured", "no file was restored". Eighteen of them said the same thing with a different command, so one code carrying the command covers them all; the rest got a code each, eleven in total, in all seven languages. Where git's own output is appended it stays English, which is also what belongs in a bug report. The interface composes the sentence, so a file list or a size limit still reaches the reader
- Five error messages from the git layer were German for everyone: an invalid remote branch, a file already published, a branch that could not be deleted, an invalid commit id, and a checkpoint that holds no files. They did not look like translatable text, which is why they survived the round that converted the twenty others. The same for the dry-run success messages, the build and download counters in the rebuild monitor, and the /etc/nixos symlink status — the seven translations for that one had been sitting unused in the language files while the code wrote German. Unbalanced-bracket reports now travel as data and are composed in the interface
- Removed `switchHost()` and `confirmHostSwitch()`: dead code that could not have run anyway — it read two variables that live in another file's closure, so the first access raised a ReferenceError. Host switching goes through the file tree, which never used them. `saveAndWrite()` went with them, its only caller
- Every brick operation used to add a blank line. Injecting a brick writes a separator behind it, stripping one left the separator behind, so renaming, moving or splitting a brick made the file grow by one line each time and it never settled. It compounds and it looks like NiCo scribbling: one flake.nix collected 48 of them in blocks of 8 to 13 in a single session. Stripping and injecting are symmetrical now, and a brick body is normalised before the end marker is written
- Saving no longer writes `# system.stateVersion = "";  # TODO: set NixOS state version` into the base config of a flake with several hosts. Empty is the correct state there, so the comment claimed an unfinished task where there was nothing to do, and invited exactly the entry that makes Nix refuse the evaluation. The threshold matches the validator rule, so the two cannot contradict each other
- A flake input added through a Nix brick could be declared but never used. The argument list of `outputs` was written from the inputs NiCo manages itself and ended in `...`, which silently swallows everything else — so the input existed, no name pointed at it, and every use of it failed with `undefined variable`. The interface offered no way to fix that: the only place to put the name was another brick, and the section it lands in sits inside the outputs set rather than in its argument list, which turns the file into something Nix cannot read. The names are now read back out of the bricks, so declaring an input and using it is one step
- A Nix brick whose content makes the file unreadable is refused instead of saved. Only bracket balance was checked, and a bare word passes that: a brick holding nothing but `nixpkgs-unstable` left a `flake.nix` that does not parse, was saved without a word, and the config stayed unbuildable for a day. Refused the same way as a nested brick — only when that write is what breaks it, so a file that is already damaged can still be repaired through the same dialogs. The Nix message, which names the line, comes along with the refusal
- Searching in the raw editor typed into the file. The search ran on every keystroke and moved the focus into the text to show the match, so the second letter replaced the match it had just selected. The selection is shown without taking the focus now, and the focus only follows when the search bar is closed — at which point the user does want to carry on editing there

## 0.14.0 (2026-10-31)

### New Features

- The Validator now runs by itself before a rebuild and before the syntax check, on the state just written to disk. Warnings and hints never interrupt. A critical error brings its output up instead of starting the build, and the button that forces the build anyway sits at the very bottom, behind everything that stopped it – deliberately placed so it cannot be reached without scrolling past the findings. Until now the check was only offered after a failed rebuild: a rebuild that locks the user out ran through, and the hint arrived once they were already shut out. Opened from the menu, the same output carries no buttons – there is nothing to force there
- The two state version fields are named apart: "System state version (system.stateVersion)" and "Home state version (home.stateVersion)". Both used to read like one value – the NixOS field said "NixOS stateVersion", the Home Manager field just "state version" – which is how two numbers that count entirely different things came to be kept equal
- New validator rule: a `system.stateVersion` newer than the release the machine runs. Nobody can have installed with a release that did not exist yet, and that is exactly the state a fresh VM lands in when it imports a config from a machine further along
- New validator rule: no `system.stateVersion` at all. NixOS then takes the release currently being built, which is right only for a machine installed with exactly that release
- New validator rule: one `system.stateVersion` in the shared base config of a flake with several hosts. Each machine was installed at its own time, so the value belongs in the host file – the shared one claims a single installation date for every machine. Only hosts that actually inherit it are named; a host carrying its own value is already right. And a value in the base plus one in a host file is reported as an error, because Nix refuses two definitions of one option and stops at evaluation – unless the base one steps back with `lib.mkDefault`, which is read and accepted
- New validator rule: a user the rebuild would create without a password or an SSH key. NixOS creates such an account locked, no password is ever accepted, and `/etc/passwd` and `/etc/shadow` are mutable system state – booting an older generation does not bring the old login back. Silent for users that already exist on the machine, unless `users.mutableUsers = false` makes the config the only source
- New validator rule: a uid that does not match the machine. Either the uid of an existing user changes – the home directory keeps its old owner, so the login succeeds and the session dies immediately – or the uid already belongs to somebody else there
- New validator rule: the same uid handed to more than one user in the config
- The same two rules for groups: a `users.groups.<name>.gid` that does not match the machine, and one gid pinned for two groups. A gid is the same kind of mutable state as a uid – it lives in `/etc/group`, not in a generation. Changing it leaves the group's files behind with a number no group reaches any more (shared directories, service sockets), and a gid that already belongs to another group there widens who may read what, silently and without a trace
- Both machine-dependent user rules stay silent about other hosts and say so instead of guessing, like the existing btrfs and snapper checks
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
- NiCo now asks whether the hardware-configuration.nix in a config belongs to this machine. The file names the disks of exactly one computer, and a config that arrives from somewhere else brings the disks of the machine it came from – the build then looks for devices that do not exist and the result does not boot. Everything except a confirmed match stops and the user decides; nothing is preselected and there is no "later" that quietly continues. A file that cannot be checked at all – one built on labels or a shared folder – stops just as hard, because not knowing is not the same as being fine, and can be confirmed once per host so it does not block forever
- The question is asked at the three moments a foreign hardware config can arrive: after an import, after a restore from a checkpoint, and at start – the last one because a config brought in with `git clone` passes no dialog at all, and until now the only check ran on click or before a build
- Where a config has several hosts, only the file of the host that is this machine is ever touched. The hostname decides; the disks only decide whether the file is sound. The file of another host is *supposed* not to match here, and replacing it would destroy that machine's config. What gets replaced is kept as `.import.bak`, because a file that arrived by mistake may be the only copy of another machine's hardware
- The replacement itself is checked before it is written. The usual way a foreign config reaches a machine is that somebody copies it to `/etc/nixos` and imports from there – at which point the "local" file is the foreign one, and putting it in place would look like a repair while changing nothing
- The hardware dialog asks which file should be put in place, and with several hosts also which host this machine is – NiCo cannot answer that reliably, the hostname is a hint and not proof, and it is a decision about the user's own machine. Nothing is preselected, and a file known to name another machine's disks is listed but cannot be chosen. One action button: "keep the file that is there" is an entry in the same list rather than a second button answering a different question
- The dialog lists the hardware configs by what was found – devices present here, devices missing here, cannot be checked – with the path alone. The device identifiers behind a verdict sit behind a details toggle now: three UUIDs per line is evidence, not information, and the decision does not depend on which identifier is missing
- The wording no longer claims more than the check knows. "Devices present" replaces "matches this machine": what is verified is that the devices a file names exist here, not that it is the right file – a hardware config describing a USB stick passes that test and is still wrong
- Hosts that carry no hardware config at all are offered as a destination too: a config arriving from another machine may have no host for this one, and then the answer is a new file rather than a replacement
- This machine's hardware config is now kept aside before every import and every restore, in `~/.config/nico/hw-stash/` next to the app settings. An import replaces every `.nix` in the config directory, this machine's hardware config included, and afterwards it is reachable only through git – so the one file the repair actually needs was gone by the time the question was asked
- After a rebuild the monitor names the services that failed, each with the last lines of its journal. `nixos-rebuild` answers with an exit code and the reason sits somewhere in a log that has long scrolled past – finding it meant running `systemctl status` and `journalctl` by hand. Only services that were still healthy before this build are listed, so the ones a desktop carries around anyway stay out of it, and the list appears after a successful build too: a service that fails quietly there is the one nobody looks for

### Bug Fixes

- Config files are written atomically. `write_text` truncates a file to zero bytes the moment it opens it and only then writes the new content, and once it returns the data may still sit in the page cache rather than on the disk. A machine that stops in either window – a hard reset, a power cut – left a truncated `configuration.nix` or `flake.nix` behind, and such a file does not merely lose the last edit: it no longer parses, so nothing builds at all, not even a step back. All 28 write sites now go through one helper that writes to a temporary file beside the target, flushes it to disk, and renames it – a rename is indivisible, so what remains after an abrupt end is either the complete old file or the complete new one. The same pattern had been guarding the JSON settings for months; the `.nix` files were never part of it
- Two things the switch to atomic writing had to keep: a file's permissions, which a plain rename would have replaced with those of the temporary file (0600 – a config readable only by its owner, and for a tracked file a change git reports), and symlinks, which a rename replaces instead of writing through. A `configuration.nix` that is a link into a repository is now still a link afterwards, with the repository updated
- The rule that compares a hardware config against this machine reported every correct config as foreign. It read the machine's disks through `blkid`, and `blkid` answers an unprivileged process with an empty list – NiCo runs as the user, so no UUID was ever found and all of them counted as missing. The disks are read from `/dev/disk/by-*` now, which needs no privileges, and `PARTUUID`, `LABEL` and `PARTLABEL` are recognised alongside the long UUID that was the only form matched before
- The same rule reported another host's hardware config as broken whenever that host was validated. It compared whichever host was selected against the disks of the machine NiCo happens to run on – and a laptop's hardware config is not supposed to match the desktop
- Two validator rules gave advice NixOS itself contradicts, and dragged a real config along with it. `stateVersion must match nixpkgs` asked for the value to be raised with every release, while `system.stateVersion` is precisely the release a machine was *installed* with and is never raised – that is what keeps the defaults of stateful options at the values the data on disk was written for. The companion rule demanded `home.stateVersion` be equal to it, although that one marks when a user's Home Manager state began, which can be years later. Measured on the config both rules ran against: two values that stood at 25.11 were pulled to 26.05 on the same day. Only "newer than" is reported now – older is the normal state of every machine that has seen an upgrade – and the Home Manager value is compared with the Home Manager branch in use instead of with the system
- A state version typed into a host disappeared on save. The field is in the host form and the parser reads the value back out of the host file, but the generator deliberately left it out of host files – so the entry was swallowed without a word. It is written per host now, which is where a release a machine was installed with belongs. Whether the base config keeps a value of its own stays the user's decision; two definitions of one option are reported as an error, since Nix refuses them
- An import through the admin panel left the old config on screen: the file tree was never re-read, so clicking a file showed content that was no longer on disk. Five import paths each refreshed a different subset of the interface – they share one implementation now, which also re-reads the format marker, the git state the import's own checkpoint changed, and the tree selection that may point at a file the import removed. Deliberately not a page reload: an import writes its files after the safety checkpoint, so a reload would meet a dirty working tree and the start guard would offer to discard exactly those files
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

- Putting this machine's hardware config in place could delete it instead. `/etc/nixos/hardware-configuration.nix` was the only source on offer, and with `/etc/nixos` symlinked to the config directory – which NiCo's own symlink feature sets up – source and destination were the same file without looking like it: the file was renamed to `.import.bak`, reading the source then failed, and the config was left with no `hardware-configuration.nix` while the interface only reported that a file could not be written. The two are compared through their resolved paths now, and the source is read before anything is renamed, so a failure can no longer leave the destination gone
- A foreign hardware config could reach the rebuild button without a question ever being asked: only the import from `/etc/nixos` raised it. The manual import, the import from the admin panel and the ZIP import did not, and the check that runs at start only sees the config on the next page load – which an import deliberately does not trigger. The ZIP import is the way a foreign config most often arrives
- The rule that warns about btrfs maintenance on a machine without btrfs asked NiCo's own model, so it was blind to everything that arrived inside a Nix-Brick – a brick has no entry there. It reads the written `.nix` files now, where a brick is ordinary Nix like anything else, and it looks for anything btrfs-only rather than just the two switches the form writes. Found on a VM that has no btrfs: a `btrfs-dynamic-reclaim` service inside an imported brick failed to start and took the rebuild down with it, with nothing warning beforehand
- The check reported a missing hardware import for configs that keep their imports in a Nix brick, and for flake hosts whose import sits in flake.nix – it is therefore an error now instead of a warning
- Package search: a failed request left "Searching …" on screen forever with no message, and a slow earlier search could overwrite the results of a newer one
- The list of failed services from the previous rebuild stayed below the fresh log for the whole of the next run and only vanished when that one finished – a red report about services that had nothing to do with the build in progress. It is cleared when the monitor starts, like the log and the result line
- Files whose names leave ASCII were invisible to every git operation. Git escapes such a name in its output – `"drucker-b\303\274ro.nix"` – and read literally it matches no file at all, so git did nothing and NiCo reported success for a file it never touched. Three things went wrong at once: a new config file with an umlaut landed in no checkpoint, with an empty skip list and a success message; the 50 MB guard did not see an oversized file and let a 50 MB blob into the commit, the very thing that once blocked every push for good; and a restore left exactly those files on their newer content while everything around them went back, reporting a successful restore for a config directory that was now a mix of two states. Paths are read NUL-separated now, the one form git never escapes, and a restore that could not complete says so instead of counting as done
- A `config.json` that could not be parsed – conflict markers from a `git pull` are enough, the file is tracked and travels with the config – silently cost every config-level setting. Reading fell back to the defaults, and the next settings change wrote those defaults down: a config directory with its own `hosts_dir` and `hm_dir` was left looking for hosts in directories that hold nothing, with the remembered host checks and validator selection gone. Such a file is moved aside as `config.json.kaputt-<date>` now and keeps its content, where it can be read by hand and the foreign-file dialog asks about it by itself. The same for the app settings, where the loss took `nixos_config_dir` with it and dropped NiCo back into setup
- Saving from the form uploaded even with "Git-Sync" switched off; only `push_after_save` was checked. The two switches sit on different levels on purpose – one belongs to the machine, the other travels with the config – so a config carrying push-after-save reaches a machine that wants to be left alone. One implementation for all three save paths now, after the copies had drifted apart
- Adding a host to a flake left a config that cannot be built: the generated entry points at `hosts/<name>/` and the hardware config inside it, and neither was created. Nothing said so either – the host is in flake.nix, so that rule passed, and the rule for missing imports looked at `imports` lists only, outside bricks, while a flake host body is a brick. The host directory is created with its module now, and the Validator reads `modules` lists and brick content as well, so the hardware config it cannot invent is named as missing before the build rather than by Nix during it

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
