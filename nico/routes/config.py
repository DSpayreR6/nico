"""Config, settings, validation, preview and write routes.

Split from server.py. Route bodies are unchanged; shared request helpers
from create_app() are passed in via the ctx dict.
"""

import re
from pathlib import Path

from flask import jsonify, request

from .. import config_manager, generator, git_manager, importer
from ..brix import extract_brick_blocks, brix_content_to_bricks
from ..core import (
    get_nico_type as _get_nico_type,
    read_config_file as _read_config_file,
    write_raw_config_file as _write_raw_config_file,
    load_and_normalize_config as _load_and_normalize_config,
    run_enabled_validation as _run_enabled_validation,
    write_config_files as _write_config_files,
)


def register(app, ctx):
    _check_csrf     = ctx["check_csrf"]
    _require_setup  = ctx["require_setup"]
    _nixos_dir      = ctx["nixos_dir"]
    _hosts_dir_name = ctx["hosts_dir_name"]

    @app.route("/api/config", methods=["GET"])
    def get_config():
        nixos_dir, err = _require_setup()
        if err:
            return err
        return jsonify(_load_and_normalize_config(nixos_dir))

    @app.route("/api/config", methods=["POST"])
    def save_config():
        if err := _check_csrf(): return err
        nixos_dir, err = _require_setup()
        if err:
            return err

        incoming = request.get_json(silent=True) or {}
        if not incoming.get("_co_ready") or incoming.get("_co_path") != "configuration.nix":
            return jsonify({"error": "ERR_STALE_FORM"}), 409
        existing = config_manager.load_config(nixos_dir) or {}

        # These fields are managed by dedicated endpoints – never overwrite from the form
        # hardware_config is set by the setup/import endpoints, not the form
        incoming["hardware_config"] = existing.get("hardware_config", False)
        # flake_* fields are managed by /api/config/flake
        for key in existing:
            if key.startswith("flake_"):
                incoming[key] = existing[key]

        incoming.pop("_co_path", None)
        incoming.pop("_co_ready", None)
        config_manager.save_config(nixos_dir, incoming)
        return jsonify({"success": True})

    @app.route("/api/config/settings", methods=["GET"])
    def get_config_settings():
        """Return current config.json settings."""
        nixos_dir, err = _require_setup()
        if err:
            return err
        return jsonify(config_manager.load_config_settings(nixos_dir))

    @app.route("/api/config/settings", methods=["PATCH"])
    def patch_config_settings():
        """Merge settings into config.json (config-level settings that travel with the config)."""
        if err := _check_csrf(): return err
        nixos_dir, err = _require_setup()
        if err:
            return err

        _CONFIG_KEYS = frozenset({
            "hosts_dir", "modules_dir", "hm_dir",
            "flake_update_on_rebuild", "validation_rules",
            "push_after_save", "push_after_rebuild",
            "panel_default",
        })
        incoming = request.get_json(silent=True) or {}
        patch    = {k: v for k, v in incoming.items() if k in _CONFIG_KEYS}
        if not patch:
            return jsonify({"success": True})

        config_manager.save_config_settings(nixos_dir, patch)
        return jsonify({"success": True})

    @app.route("/api/validate/rules", methods=["GET"])
    def get_validate_rules():
        """Return metadata for all validation rules (label, description, severity, flake_only)."""
        from .. import validator as _val
        return jsonify(_val.rules_as_dicts())

    @app.route("/api/validate", methods=["POST"])
    def run_validate():
        """Run enabled validation rules and return a list of findings."""
        if err := _check_csrf(): return err
        nixos_dir, err = _require_setup()
        if err:
            return err
        config        = config_manager.load_config(nixos_dir)
        body          = request.get_json(silent=True) or {}
        host          = body.get("host") or None
        findings      = _run_enabled_validation(nixos_dir, config, host=host)
        return jsonify({"findings": findings})

    @app.route("/api/app/settings", methods=["GET"])
    def get_app_settings():
        """Return current app settings (language, theme, config path)."""
        return jsonify(config_manager.get_app_settings())

    @app.route("/api/app/settings", methods=["PATCH"])
    def patch_app_settings():
        """Merge settings into app settings (~/.config/nico/settings.json)."""
        if err := _check_csrf(): return err

        _APP_KEYS = frozenset({"language", "theme", "code_view_plain", "rebuild_log", "hidden_sections", "section_filter", "show_flake_lock", "default_host", "rebuild_terminal", "rebuild_safe", "prefetch_dry_run"})
        incoming = request.get_json(silent=True) or {}
        patch    = {k: v for k, v in incoming.items() if k in _APP_KEYS}
        if not patch:
            return jsonify({"success": True})

        config_manager.save_app_settings(patch)
        return jsonify({"success": True})

    @app.route("/api/config/detach", methods=["POST"])
    def detach_config():
        """Back up the current config and detach it from NiCo management."""
        if err := _check_csrf(): return err
        nixos_dir, err = _require_setup()
        if err:
            return err

        root = Path(nixos_dir)
        try:
            backup_name = importer.backup_to_zip(root)
            cleaned_files = importer.scrub_nico_comments_in_tree(root)

            removed_json: list[str] = []
            for rel_name in ("config.json", "nico.json"):
                target = root / rel_name
                if target.exists():
                    target.unlink()
                    removed_json.append(rel_name)

            config_manager.save_app_settings({"nixos_config_dir": ""})
        except OSError:
            return jsonify({"error": "ERR_DETACH_FAILED"}), 500

        return jsonify({
            "ok": True,
            "backup": backup_name,
            "cleaned_files": cleaned_files,
            "removed_json": removed_json,
            "restart_setup": True,
        })

    @app.route("/api/config/flake", methods=["POST"])
    def save_flake_config():
        """Apply incoming flake_* fields and write flake.nix to disk immediately.
        flake.nix is the source of truth for flake_* – never written via /api/write alone."""
        if err := _check_csrf(): return err
        nixos_dir, err = _require_setup()
        if err:
            return err

        incoming    = request.get_json(silent=True) or {}
        nixos_path  = Path(nixos_dir)
        flake_file  = nixos_path / "flake.nix"

        # Only touch nico-managed flake.nix files
        _flake_content = flake_file.read_text(encoding="utf-8") if flake_file.exists() else None
        if _flake_content is not None and _get_nico_type(_flake_content) is None:
            return jsonify({"success": True})  # external flake.nix – never overwrite

        # Build data: base from configuration.nix, then overlay flake.nix, then incoming
        data = config_manager.load_config(nixos_dir) or {}
        if _flake_content:
            data.update(importer.parse_flake_nix(_flake_content))
            flake_brix = extract_brick_blocks(_flake_content)
            flake_brix = importer.ensure_flake_host_bricks(_flake_content, flake_brix)
            if flake_brix:
                data["flake_brick_blocks"] = flake_brix
        for key, val in incoming.items():
            if key.startswith("flake_"):
                data[key] = val

        try:
            flake_file.write_text(
                generator.generate_flake_nix(data, nixos_dir=str(nixos_path)),
                encoding="utf-8",
            )
        except OSError as exc:
            return jsonify({"error": str(exc)}), 500

        return jsonify({"success": True})

    @app.route("/api/preview/flake", methods=["POST"])
    def preview_flake():
        """Return generated flake.nix for the posted flake_* fields (nothing written to disk)."""
        if err := _check_csrf(): return err
        nixos_dir, err = _require_setup()
        if err:
            return err
        incoming = request.get_json(silent=True) or {}
        data = config_manager.load_config(nixos_dir) or {}
        # Same data basis as save_flake_config: overlay the existing flake.nix
        # (preserves fields without panel UI, e.g. flake_arch), then incoming.
        flake_nix_path = Path(nixos_dir) / "flake.nix"
        if flake_nix_path.exists():
            try:
                flake_content = flake_nix_path.read_text(encoding="utf-8")
                data.update(importer.parse_flake_nix(flake_content))
                flake_brix = extract_brick_blocks(flake_content)
                flake_brix = importer.ensure_flake_host_bricks(flake_content, flake_brix)
                if flake_brix:
                    data["flake_brick_blocks"] = flake_brix
            except OSError:
                pass
        data.update(incoming)
        return jsonify({"flake_nix": generator.generate_flake_nix(data, nixos_dir=nixos_dir)})

    @app.route("/api/preview", methods=["POST"])
    def preview():
        if err := _check_csrf(): return err
        """
        Generate a live preview from the data sent in the request body.
        Nothing is written to disk – purely for display.
        Fields not tracked in the form (packages, brick_blocks) are merged
        from the stored config so the preview is always accurate.
        """
        data = request.get_json(silent=True) or {}

        host_name = data.pop("_host", None)  # optional host context
        rel_path = data.pop("_path", None)
        nixos_dir = _nixos_dir()

        # Exact file mode for CO files:
        # use only the current file plus current form state, without merging
        # root/host defaults from other files into the preview.
        if nixos_dir and rel_path:
            nixos_path = Path(nixos_dir).resolve()
            target = (nixos_path / rel_path).resolve()
            try:
                target.relative_to(nixos_path)
            except ValueError:
                return jsonify({"error": "ERR_PATH_OUTSIDE"}), 403

            if target.exists():
                try:
                    nix_content = target.read_text(encoding="utf-8")
                    on_disk = extract_brick_blocks(nix_content)
                    if on_disk:
                        data["brick_blocks"] = on_disk
                except OSError:
                    pass

            hosts_dir_name = _hosts_dir_name(nixos_dir)
            host_match = re.search(
                rf"(?:^|/){re.escape(hosts_dir_name)}/([^/]+)/default\.nix$", rel_path
            )
            if rel_path == "configuration.nix":
                if (nixos_path / "hardware-configuration.nix").exists():
                    data["hardware_config"] = True
                return jsonify({
                    "configuration_nix": generator.generate_configuration_nix(data)
                })
            if host_match:
                host_dir = nixos_path / hosts_dir_name / host_match.group(1)
                hw_config = (host_dir / "hardware-configuration.nix").exists()
                return jsonify({
                    "configuration_nix": generator.generate_host_nix(
                        data, host_match.group(1), hw_config
                    )
                })
            return jsonify({
                "configuration_nix": generator.generate_configuration_nix(data)
            })

        if nixos_dir:
            stored = config_manager.load_config(nixos_dir) or {}

            if host_name:
                # Merge defaults + host overrides for preview
                host_stored = config_manager.load_host_config(nixos_dir, host_name) or {}
                merged = dict(stored)
                merged.update(data)  # host form data (non-additive fields)
                # Merge packages additively
                def_pkgs  = stored.get("packages", [])
                host_pkgs = data.get("packages", host_stored.get("packages", []))
                merged["packages"] = def_pkgs + [p for p in host_pkgs if p not in def_pkgs]
                # Merge fonts additively
                def_fonts  = stored.get("fonts", [])
                host_fonts = data.get("fonts", host_stored.get("fonts", []))
                merged["fonts"] = sorted(set(def_fonts + host_fonts))
                data = merged

            # packages and brick_blocks are not part of the form payload
            brick_blocks = stored.get("brick_blocks", {})
            # nico.json kann veraltet sein – Brix immer aus .nix nachladen
            cfg_nix = Path(nixos_dir) / "configuration.nix"
            if cfg_nix.exists():
                try:
                    nix_content = cfg_nix.read_text(encoding="utf-8")
                    on_disk = extract_brick_blocks(nix_content)
                    if on_disk:
                        brick_blocks = on_disk
                    elif not brick_blocks:
                        recognized = importer.parse_config(nix_content)
                        rest = importer.build_rest_brix(nix_content, recognized)
                        if rest.strip():
                            brick_blocks = brix_content_to_bricks(rest, section="Start")
                except OSError:
                    pass
            data["brick_blocks"] = brick_blocks
            data["packages"]    = stored.get("packages", []) if not host_name else data.get("packages", [])
            # Flake-spezifische Felder aus stored config übernehmen (kommen nicht vom Formular)
            for _k, _v in stored.items():
                if _k.startswith("flake_") and _k not in data:
                    data[_k] = _v

        result: dict = {
            "configuration_nix": generator.generate_configuration_nix(data)
        }
        if data.get("flakes"):
            if nixos_dir:
                flake_nix_path = Path(nixos_dir) / "flake.nix"
                if flake_nix_path.exists():
                    try:
                        flake_content = flake_nix_path.read_text(encoding="utf-8")
                        flake_brix = extract_brick_blocks(flake_content)
                        flake_brix = importer.ensure_flake_host_bricks(flake_content, flake_brix)
                        if flake_brix:
                            data["flake_brick_blocks"] = flake_brix
                    except OSError:
                        pass
            result["flake_nix"] = generator.generate_flake_nix(data, nixos_dir=nixos_dir)

        return jsonify(result)

    @app.route("/api/write", methods=["POST"])
    def write_files():
        if err := _check_csrf(): return err
        nixos_dir, err = _require_setup()
        if err:
            return err

        data = config_manager.load_config(nixos_dir)
        if data is None:
            return jsonify({"error": "ERR_NO_CONFIG"}), 400

        body = request.get_json(silent=True) or {}
        return jsonify(_write_config_files(
            nixos_dir, data,
            commit=body.get("commit", True),
            label=body.get("label", ""),
        ))

    # ── Multi-Host Support ────────────────────────────────────────────────

    @app.route("/api/hosts")
    def list_hosts():
        nixos_dir = _nixos_dir()
        if not nixos_dir:
            return jsonify([])
        return jsonify(config_manager.scan_hosts(nixos_dir))

    @app.route("/api/host/<host_name>/config", methods=["POST"])
    def save_host_config_route(host_name):
        if err := _check_csrf():
            return err
        nixos_dir, err = _require_setup()
        if err:
            return err
        data = request.get_json(silent=True) or {}
        expected = f"{_hosts_dir_name(nixos_dir)}/{host_name}/default.nix"
        if not data.get("_co_ready") or data.get("_co_path") != expected:
            return jsonify({"error": "ERR_STALE_FORM"}), 409
        data.pop("_co_path", None)
        data.pop("_co_ready", None)
        config_manager.save_host_config(nixos_dir, host_name, data)
        return jsonify({"success": True})

    @app.route("/api/host/<host_name>/write", methods=["POST"])
    def write_host_file(host_name):
        if err := _check_csrf():
            return err
        nixos_dir, err = _require_setup()
        if err:
            return err

        body = request.get_json(silent=True) or {}
        do_commit = body.get("commit", True)

        host_data = config_manager.load_host_config(nixos_dir, host_name)
        if host_data is None:
            return jsonify({"error": "ERR_NO_CONFIG"}), 400

        hosts_dir_name = _hosts_dir_name(nixos_dir)
        host_dir = Path(nixos_dir) / hosts_dir_name / host_name
        host_nix = host_dir / "default.nix"

        # Merge brix from existing file into host_data before the single write below
        if host_nix.exists():
            on_disk_brix = extract_brick_blocks(host_nix.read_text(encoding="utf-8"))
            stored_brix  = host_data.get("brick_blocks", {})
            stored_brix.update(on_disk_brix)
            host_data["brick_blocks"] = stored_brix

        hw_config = (host_dir / "hardware-configuration.nix").exists()
        host_dir.mkdir(parents=True, exist_ok=True)
        host_nix.write_text(generator.generate_host_nix(host_data, host_name, hw_config), encoding="utf-8")

        if do_commit:
            try:
                git_manager.auto_commit(nixos_dir, label=body.get("label", ""))
            except Exception:
                pass

        return jsonify({"success": True, "written": [f"{hosts_dir_name}/{host_name}/default.nix"], **_maybe_auto_push(nixos_dir)})

    # ── Datei-Viewer / Roh-Editor ─────────────────────────────────────────────
    # Suffix-Regeln leben in core (_READABLE_SUFFIXES/_WRITABLE_SUFFIXES)

    def _core_file_error(exc: ValueError, *, suffix_code: str, outside_code: str):
        """Map ValueError from core file helpers to an API error response."""
        code = str(exc)
        if code == "ERR_FILE_TYPE":
            return jsonify({"error": suffix_code}), 400
        if code.startswith("ERR_"):
            return jsonify({"error": code}), 400
        return jsonify({"error": outside_code}), 403  # relative_to: path escapes root

    @app.route("/api/file/<path:rel_path>", methods=["GET"])
    def read_file(rel_path):
        nixos_dir, err = _require_setup()
        if err:
            return err
        try:
            result = _read_config_file(nixos_dir, rel_path,
                                       persist_stamp=_check_csrf() is None)
        except ValueError as exc:
            return _core_file_error(exc, suffix_code="ERR_FILE_TYPE",
                                    outside_code="ERR_PATH_OUTSIDE")
        except FileNotFoundError:
            return jsonify({"error": "ERR_NOT_FOUND"}), 404
        except OSError:
            return jsonify({"error": "ERR_FILE_READ"}), 500
        return jsonify(result)

    @app.route("/api/file/<path:rel_path>", methods=["POST"])
    def write_file_raw(rel_path):
        if err := _check_csrf():
            return err
        nixos_dir, err = _require_setup()
        if err:
            return err
        body = request.get_json(silent=True) or {}
        try:
            result = _write_raw_config_file(nixos_dir, rel_path,
                                            body.get("content", ""),
                                            label=body.get("label", ""))
        except ValueError as exc:
            return _core_file_error(exc, suffix_code="ERR_FILE_TYPE",
                                    outside_code="ERR_PATH_OUTSIDE")
        except OSError:
            return jsonify({"error": "ERR_WRITE"}), 500
        return jsonify(result)

    # ------------------------------------------------------------------ export

    @app.route("/api/export")
    def export_zip():
        """Download a ZIP containing all visible files from the nixos directory."""
        import io
        import zipfile
        from datetime import date
        from flask import send_file
        from pathlib import Path

        nixos_dir, err = _require_setup()
        if err:
            return err

        root = Path(nixos_dir)
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for f in sorted(root.rglob("*")):
                if not f.is_file():
                    continue
                rel = f.relative_to(root)
                if any(part.startswith('.') for part in rel.parts):
                    continue
                zf.write(f, rel)
        buf.seek(0)

        filename = f"nico-export-{date.today()}.zip"
        return send_file(buf, mimetype="application/zip",
                         as_attachment=True, download_name=filename)

    # ------------------------------------------------- app-settings export/import

    @app.route("/api/settings/app/export")
    def export_app_settings():
        """Download app preferences (excluding nixos_config_dir) as JSON."""
        import json as _json
        from datetime import date
        from flask import send_file
        import io

        _EXPORTABLE = {"language", "theme", "code_view_plain", "rebuild_log", "hidden_sections", "section_filter"}
        all_settings = config_manager.get_app_settings()
        exported = {k: v for k, v in all_settings.items() if k in _EXPORTABLE}

        buf = io.BytesIO(_json.dumps(exported, indent=2, ensure_ascii=False).encode("utf-8"))
        buf.seek(0)
        filename = f"nico-settings-{date.today()}.json"
        return send_file(buf, mimetype="application/json",
                         as_attachment=True, download_name=filename)

    @app.route("/api/settings/app/import", methods=["POST"])
    def import_app_settings():
        """
        Accept a JSON file with exported app preferences.
        Only known exportable keys are accepted; nixos_config_dir is never touched.
        """
        import json as _json

        _IMPORTABLE = {"language", "theme", "code_view_plain", "rebuild_log", "hidden_sections", "section_filter"}

        if err := _check_csrf(): return err

        file = request.files.get("file")
        if not file:
            return jsonify({"error": "ERR_NO_FILE"}), 400

        try:
            data = _json.loads(file.read().decode("utf-8"))
        except Exception:
            return jsonify({"error": "ERR_INVALID_JSON"}), 400

        if not isinstance(data, dict):
            return jsonify({"error": "ERR_INVALID_JSON"}), 400

        patch = {k: v for k, v in data.items() if k in _IMPORTABLE}
        if not patch:
            return jsonify({"error": "ERR_NO_VALID_KEYS"}), 400

        config_manager.save_app_settings(patch)
        return jsonify({"ok": True, "imported": list(patch.keys())})

