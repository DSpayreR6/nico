# NiCo Testing

This directory holds headless roundtrip tests for existing NixOS
configurations.

## Layout

```text
testing/
  configs/
    example/
      original/
        configuration.nix
        ...
      result/
```

Create one directory per test configuration under `testing/configs/`. Put the
complete original configuration into `original/`. The runner clears and rewrites
`result/`.

## Run

```bash
python3 testing/run.py
```

If `python3` is only available through the project shell:

```bash
nix-shell shell.nix --run "python3 testing/run.py"
```

Useful options:

```bash
python3 testing/run.py --config example
python3 testing/run.py --keep-work
python3 testing/run.py --no-syntax
python3 testing/run.py --rebuild-dry-run
python3 testing/run.py --old        # hand-built import of before 2026-11-21, comparison only
```

What the runner does goes through NiCo's own functions, nothing rebuilt:
the import of `configuration.nix` (`core.apply_import_result`, with the
guards of `save_config`), the take-over of `flake.nix`
(`core.reimport_flake`), and for every host and Home Manager file the
restructuring NiCo offers when it is opened (`core.restructure_file`), as if
the user confirmed each one.

The checks:

- `nix-instantiate --parse` for every resulting `.nix` file (Nix itself).
- Comments, attribute names and duplicate attributes compared with
  tree-sitter – independent of NiCo's own text walkers, so a blind spot of
  NiCo is not shared by the check (2026-11-21).
- Every attribute path and value per file through `nico/fidelity.py` – the
  same comparison NiCo's check before writing uses.
- Known NiCo fields and unmanaged content, measured with NiCo's importer.

Sensitive files and directories (`.env`, `*.key`, `*secret*`,
`*credential*`) are neither copied nor compared.

`--rebuild-dry-run` finds the hosts itself and calls `nixos-rebuild` directly –
not NiCo's own dry-run (host list, flake staging); a known difference.
It is optional because it depends on local NixOS tooling,
channels or flake inputs, host names, imports, hardware files, and sometimes
permissions.
