"""Flake host management routes.

Split from server.py. Route bodies are unchanged; shared request helpers
from create_app() are passed in via the ctx dict.
"""

import os
import re
from pathlib import Path

from flask import jsonify, request

from .. import atomic, config_manager, generator, git_manager, importer, validator
from ..brix import extract_brick_blocks
from ..core import flake_is_managed as _flake_is_managed
from ..core import get_flake_hosts as _get_flake_hosts


def register(app, ctx):
    _check_csrf     = ctx["check_csrf"]
    _require_setup  = ctx["require_setup"]
    _hosts_dir_name = ctx["hosts_dir_name"]

    @app.route("/api/flake/hosts")
    def flake_hosts():
        """Return panel-managed flake hosts as a simple host list."""
        nixos_dir, err = _require_setup()
        if err:
            return err
        # Flake mode is "a flake.nix exists", not the `flakes` form field
        # (revision 2026-11-10, B34).
        if not config_manager.flake_mode(nixos_dir):
            return jsonify({"flake_mode": False, "hosts": [], "build_hosts": []})
        data = config_manager.load_config(nixos_dir) or {}
        flake_path = Path(nixos_dir) / "flake.nix"
        mappable = False
        try:
            flake_content = flake_path.read_text(encoding="utf-8")
            mappable = importer.flake_unmappable(flake_content) is None
            if mappable:
                # Parse flake.nix for up-to-date host data
                data.update(importer.parse_flake_nix(flake_content))
                data["flake_brick_blocks"] = importer.ensure_flake_host_bricks(
                    flake_content, extract_brick_blocks(flake_content)
                )
        except OSError:
            pass
        # `hosts` are the ones the panel manages. `build_hosts` are the names
        # a rebuild or dry-run may be asked for: the same for a flake.nix
        # NiCo writes itself, found by text search for one it cannot
        # represent – there the panel has no hosts, and without this list
        # the rebuild stopped at "no hosts found".
        hosts = (data.get("flake_hosts") or []) if mappable else []
        build_hosts = ([h.get("name") for h in hosts if h.get("name")] if mappable
                       else _get_flake_hosts(nixos_dir, data))
        return jsonify({"flake_mode": True, "hosts": hosts, "build_hosts": build_hosts})

    @app.route("/api/flake/create", methods=["POST"])
    def flake_create():
        """Turn a classic config into a flake config, as one explicit step.

        Order: checkpoint of the state before, host directory, move the
        hardware config into it, write flake.nix and configuration.nix,
        checkpoint of the result. Everything that can refuse is asked before
        the first file is touched.

        Until revision 2026-11-10 switching the form field `flakes` on did
        this implicitly and only half: the flake.nix was written, the host
        directory it points to was not.
        """
        if err := _check_csrf(): return err
        nixos_dir, err = _require_setup()
        if err:
            return err

        root       = Path(nixos_dir)
        flake_path = root / "flake.nix"
        conf_path  = root / "configuration.nix"
        if flake_path.exists():
            return jsonify({"error": "ERR_FLAKE_EXISTS"}), 409
        if conf_path.is_file():
            try:
                if importer.config_unmappable(conf_path.read_text(encoding="utf-8")):
                    return jsonify({"error": "ERR_NOT_MAPPABLE",
                                    "file": "configuration.nix"}), 409
            except OSError:
                return jsonify({"error": "ERR_FILE_READ"}), 500

        data = config_manager.load_config(nixos_dir) or {}
        hostname = (data.get("hostname") or "nixos").strip()
        if not re.fullmatch(r'[a-zA-Z][a-zA-Z0-9_-]*', hostname):
            return jsonify({"error": "ERR_INVALID_NAME"}), 400

        host_dir = root / _hosts_dir_name(nixos_dir) / hostname
        host_default = host_dir / "default.nix"
        if host_default.is_file():
            try:
                if importer.config_unmappable(host_default.read_text(encoding="utf-8")):
                    return jsonify({"error": "ERR_NOT_MAPPABLE",
                                    "file": str(host_default.relative_to(root))}), 409
            except OSError:
                return jsonify({"error": "ERR_FILE_READ"}), 500

        # The flake commands have to be allowed for the next rebuild to work.
        data["flakes"]      = True
        data["_flake_mode"] = True
        data["flake_hosts"] = [{"name": hostname}]
        conf_text = generator.generate_configuration_nix(data)
        duplicates = importer.duplicate_leaf_paths(conf_text)
        if duplicates:
            return jsonify({"error": "ERR_DUPLICATE_ATTR",
                            "duplicates": duplicates[:10]}), 409
        flake_text = generator.generate_flake_nix(data, nixos_dir=nixos_dir)
        duplicates = validator.duplicate_attrs_in(flake_text)
        if duplicates:
            return jsonify({"error": "ERR_DUPLICATE_ATTR", "duplicates": duplicates}), 409

        # Checkpoint first. Without a repository there is none to make; the
        # dialog says so.
        git_manager.auto_commit(nixos_dir, label="NiCo: backup before flake.nix")

        hw_src = root / "hardware-configuration.nix"
        hw_dst = host_dir / "hardware-configuration.nix"
        moved = False
        try:
            if not host_default.exists():
                config_manager.save_host_config(nixos_dir, hostname, {})
            if hw_dst.exists():
                # Never replace a hardware config that is already in place.
                hardware = "kept" if hw_src.exists() else "present"
            elif hw_src.is_file():
                host_dir.mkdir(parents=True, exist_ok=True)
                os.replace(hw_src, hw_dst)
                moved, hardware = True, "moved"
            else:
                hardware = "missing"      # the Validator reports it
            atomic.write_text(conf_path, conf_text)
            atomic.write_text(flake_path, flake_text)
        except OSError as exc:
            # Do not leave the hardware config in a directory nothing points to.
            if moved and not flake_path.exists():
                try:
                    os.replace(hw_dst, hw_src)
                except OSError:
                    pass
            return jsonify({"error": str(exc)}), 500

        git_manager.auto_commit(nixos_dir, label="NiCo: flake.nix created")
        return jsonify({
            "success":  True,
            "hostname": hostname,
            "host_dir": str(host_dir.relative_to(root)),
            "hardware": hardware,
            "written":  ["configuration.nix", "flake.nix"],
        })

    @app.route("/api/flake/host/add", methods=["POST"])
    def flake_host_add():
        """Add a new host to flake.nix (panel-managed, no filesystem creation)."""
        if err := _check_csrf(): return err
        nixos_dir, err = _require_setup()
        if err:
            return err

        body = request.get_json(silent=True) or {}
        name = (body.get("name") or "").strip()

        if not re.fullmatch(r'[a-zA-Z][a-zA-Z0-9_-]*', name):
            return jsonify({"error": "ERR_INVALID_NAME"}), 400

        flake_path = Path(nixos_dir) / "flake.nix"
        if not flake_path.exists():
            return jsonify({"error": "ERR_NO_FLAKE"}), 400
        if not _flake_is_managed(flake_path.read_text(encoding="utf-8")):
            return jsonify({"error": "ERR_EXTERNAL_FLAKE"}), 400

        data = config_manager.load_config(nixos_dir) or {}
        flake_content = flake_path.read_text(encoding="utf-8")
        data.update(importer.parse_flake_nix(flake_content))
        data["flake_brick_blocks"] = importer.ensure_flake_host_bricks(
            flake_content, extract_brick_blocks(flake_content)
        )

        current_hosts: list[dict] = data.get("flake_hosts") or []
        if any(h.get("name") == name for h in current_hosts):
            return jsonify({"error": "ERR_HOST_EXISTS"}), 409

        current_hosts.append({"name": name})
        data["flake_hosts"] = current_hosts

        try:
            atomic.write_text(
                flake_path,
                generator.generate_flake_nix(data, nixos_dir=nixos_dir)
            )
        except OSError as exc:
            return jsonify({"error": str(exc)}), 500

        # The generated host entry references ./<hosts_dir>/<name> and the
        # hardware config inside it. Nix resolves a directory through its
        # default.nix, so without this file the flake stops evaluating for the
        # host that was just added – and nothing said so: flake_host_exists
        # finds the host in flake.nix, and imports_exist looked at `imports`
        # lists outside bricks only, while a flake host body *is* a brick.
        # The hardware config cannot be invented here; the Validator reports it
        # as missing instead, and the hardware dialog offers this host as a
        # destination. Leaving the import line out would be worse than a failed
        # build: the host body is generated once and never again, so the line
        # would never come back and the host would build without filesystems.
        host_default = Path(nixos_dir) / _hosts_dir_name(nixos_dir) / name / "default.nix"
        if not host_default.exists():
            try:
                config_manager.save_host_config(nixos_dir, name, {})
            except OSError:
                pass

        git_manager.auto_commit(nixos_dir)
        return jsonify({"success": True, "hosts": current_hosts})

    @app.route("/api/flake/host/delete", methods=["POST"])
    def flake_host_delete():
        """Remove a host from flake.nix (panel-managed hosts and exotic Brix)."""
        if err := _check_csrf(): return err
        nixos_dir, err = _require_setup()
        if err:
            return err

        body = request.get_json(silent=True) or {}
        name = (body.get("name") or "").strip()

        if not name:
            return jsonify({"error": "ERR_INVALID_NAME"}), 400

        flake_path = Path(nixos_dir) / "flake.nix"
        if not flake_path.exists():
            return jsonify({"error": "ERR_NO_FLAKE"}), 400
        if not _flake_is_managed(flake_path.read_text(encoding="utf-8")):
            return jsonify({"error": "ERR_EXTERNAL_FLAKE"}), 400

        data = config_manager.load_config(nixos_dir) or {}
        flake_content = flake_path.read_text(encoding="utf-8")
        data.update(importer.parse_flake_nix(flake_content))
        brix = importer.ensure_flake_host_bricks(flake_content, extract_brick_blocks(flake_content))
        data["flake_brick_blocks"] = brix

        # Remove from panel hosts
        current_hosts: list[dict] = [
            h for h in (data.get("flake_hosts") or []) if h.get("name") != name
        ]
        data["flake_hosts"] = current_hosts

        # Remove host-body bricks for this host
        host_section = importer.flake_host_section(name)
        for brick_name in [bn for bn, block in brix.items() if block.get("section") == host_section]:
            del brix[brick_name]
        data["flake_brick_blocks"] = brix

        try:
            atomic.write_text(
                flake_path,
                generator.generate_flake_nix(data, nixos_dir=nixos_dir)
            )
        except OSError as exc:
            return jsonify({"error": str(exc)}), 500

        git_manager.auto_commit(nixos_dir)
        return jsonify({"success": True, "hosts": current_hosts})

