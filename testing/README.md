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
```

The default checks are intentionally simple:

- NiCo parses and writes the configuration without the web UI.
- Known NiCo fields parsed from the original are still present after writing.
- Unsupported/unmanaged Nix content is expected to survive as Brick content.
- `nix-instantiate --parse` is run for generated `.nix` files when available.

`--rebuild-dry-run` is optional because it depends on local NixOS tooling,
channels or flake inputs, host names, imports, hardware files, and sometimes
permissions.
