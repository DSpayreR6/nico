"""Flake host management routes.

Split from server.py. Route bodies are unchanged; shared request helpers
from create_app() are passed in via the ctx dict.
"""

import re
from pathlib import Path

from flask import jsonify, request

from .. import atomic, config_manager, generator, git_manager, importer
from ..brix import extract_brick_blocks
from ..core import get_nico_type as _get_nico_type


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
        data = config_manager.load_config(nixos_dir) or {}
        if not data.get("flakes"):
            return jsonify({"flake_mode": False, "hosts": []})
        # Parse flake.nix for up-to-date host data
        flake_path = Path(nixos_dir) / "flake.nix"
        if flake_path.exists():
            try:
                flake_content = flake_path.read_text(encoding="utf-8")
                data.update(importer.parse_flake_nix(flake_content))
                data["flake_brick_blocks"] = importer.ensure_flake_host_bricks(
                    flake_content, extract_brick_blocks(flake_content)
                )
            except OSError:
                pass
        hosts = data.get("flake_hosts") or []
        return jsonify({"flake_mode": True, "hosts": hosts})

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
        if _get_nico_type(flake_path.read_text(encoding="utf-8")) is None:
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
        if _get_nico_type(flake_path.read_text(encoding="utf-8")) is None:
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

