# NiCo – NixOS Configurator

> ⚠️ NiCo should now work. I use it myself for managing my machines.
> Despite this, I recommand to make a backup of your config or let nico work with a copy in a separate directory.
> So you can tryout if your config is processed correct an if a dry-run works.

NiCo is a browser-based GUI editor for NixOS configurations. It reads and writes directly to your `.nix` files — no database, no intermediate format, no hidden state.

Built by a non-developer using Claude Code and OpenAI Codex as an experiment in AI-assisted development.

---

## Features

- **Import existing configs** – import your current NixOS configuration with extras like Git integration and symlink setup
- **Split-panel editor** – edit options on the left, see the resulting config file live on the right
- **Nix-Brix** – insert free-text blocks at the end of any section for anything the editor doesn't cover
- **Clean view** – toggle a comment-free view of the config for better readability
- **Dry-run** – run a NixOS dry-run directly from NiCo
- **Git integration** – commit changes and run `nixos-rebuild switch` from within the editor
- **Time machine** – restore previous Git states via the menu
- **Flake & Home Manager support** – rudimentary but functional
- **Import / Export** – save and load your NiCo configuration
- **Subdirectory support** – works with split config structures
- **Help system** – tooltips, help texts, and wiki links built in
- **Multi-language** – UI available in EN, DE, ES, FR, JA, RU, ZH (translations partially complete)

---

## Requirements

- NixOS (Flake-based config recommended)
- Python 3
- A browser

---

## Getting Started

```bash
git clone https://github.com/DSpayreR6/nico.git
cd nico
./start.sh
```

Then open your browser at `http://localhost:5000`.

---

## Status

NiCo is a proof of concept that has grown into something more — but it is still early. Expect rough edges. Contributions, bug reports, and feedback are welcome.

Please keep in mind, im only one person and response may take some time.

---

## License

MIT
