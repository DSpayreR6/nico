# NiCo – NixOS Configurator

> ⚠️ NiCo works — I use it myself to manage my machines.
> Still, I recommend making a backup of your config first, or letting NiCo work on a copy in a separate directory.
> That way you can verify that your config is processed correctly and a dry-run passes.

NiCo is a local, browser-based GUI editor for NixOS configurations. It reads and writes your real `.nix` files directly — no database, no intermediate format, no hidden state. Everything it generates stays a normal Nix file that works without NiCo.

Built by a non-developer using Claude Code and OpenAI Codex as an experiment in AI-assisted development.

Feedback and discussion: https://discourse.nixos.org/t/nico-nixos-configurator/77117

---

## The core idea: Nix-Brix

Most GUI config tools only allow what their forms cover. NiCo takes a different approach: anything it doesn't have a form field for, you write as a **Nix brick** — a free Nix section wrapped in marker comments that NiCo **never touches**. Form fields and hand-written Nix coexist in the same file. When importing an existing config, unrecognized sections are preserved as bricks automatically — nothing is ever discarded.

---

## Features

- **Import existing configs** – from a directory or ZIP; recognized options become form fields, everything else is preserved as Nix bricks, with automatic backups
- **Split-panel editor** – edit options on the left, see the resulting `.nix` file live on the right
- **Nix-Brix** – free Nix sections NiCo never modifies: create, rename, move, split them across hosts and modules
- **Dry-run & rebuild** – run `nixos-rebuild dry-build` / `switch` directly from NiCo with live streaming output
- **Validator** – configurable rule set that checks your config for common pitfalls (orphaned hosts, architecture mismatch, untracked flake references, Snapper mountpoints, …)
- **Git integration** – auto-commits, safety commit before every rebuild, rollback ("time machine"), remote push, and a guard that detects external changes before NiCo writes
- **Foreign-file guard** – files that don't belong to the config are detected; decide per file whether git tracks or ignores them
- **Flake & Home Manager support** – multi-host flakes with channel and architecture selection; per-user Home Manager files
- **Btrfs maintenance** – generate systemd timers for auto-scrub and auto-balance from the UI
- **Detach anytime** – one click removes all NiCo markers and leaves a plain Nix config (with a ZIP backup)
- **Help system** – built-in help (German/English), tooltips, and wiki links
- **Multi-language** – UI in EN, DE, ES, FR, JA, RU, ZH
- **Local only** – binds to `127.0.0.1`, no telemetry; the only network access is an optional read-only fetch of the public nixpkgs channel list

---

## Requirements

- NixOS
- Python 3 (via `shell.nix` / flake)
- A browser

---

## Getting Started

```bash
git clone https://github.com/DSpayreR6/nico.git
cd nico
./start.sh
```

Or with flakes:

```bash
nix run github:DSpayreR6/nico
```

NiCo picks a free port (default `8421`) and opens your browser automatically.

---

## Status

NiCo started as a proof of concept and has grown into a working tool — but it is still early. Expect rough edges. Contributions, bug reports, and feedback are welcome.

Please keep in mind I'm only one person, so responses may take some time.

---

## License

MIT
