"""Home-Manager file routes.

Split from server.py. Route bodies are unchanged; shared request helpers
from create_app() are passed in via the ctx dict.
"""

import re
from pathlib import Path

from flask import jsonify, request

from .. import config_manager, git_manager, hm_generator
from ..core import (
    get_nico_type as _get_nico_type,
    hm_patch_args as _hm_patch_args,
    hm_patch_bool as _hm_patch_bool,
    hm_patch_init_extra as _hm_patch_init_extra,
    hm_patch_packages as _hm_patch_packages,
    hm_patch_str as _hm_patch_str,
    hm_update_hash as _hm_update_hash,
)


def register(app, ctx):
    _check_csrf    = ctx["check_csrf"]
    _require_setup = ctx["require_setup"]
    _path_inside   = ctx["path_inside"]

    @app.route("/api/hm/files", methods=["GET"])
    def hm_files():
        nixos_dir, err = _require_setup()
        if err:
            return err
        cfg_s = config_manager.load_config_settings(nixos_dir)
        hm_dir_name = (cfg_s.get("hm_dir") or "home").strip() or "home"
        hm_dir = (Path(nixos_dir) / hm_dir_name).resolve()
        if not _path_inside(hm_dir, Path(nixos_dir)):
            return jsonify({"files": []})
        files = []
        if hm_dir.is_dir():
            for p in sorted(hm_dir.iterdir()):
                if p.suffix == ".nix" and p.is_file():
                    username = p.stem
                    files.append({
                        "filename": p.name,
                        "username": username,
                        "path":     str(p.relative_to(Path(nixos_dir))),
                    })
        return jsonify({"files": files})

    @app.route("/api/hm/create", methods=["POST"])
    def hm_create():
        if err := _check_csrf(): return err
        nixos_dir, err = _require_setup()
        if err:
            return err
        body = request.get_json(silent=True) or {}
        username      = (body.get("username") or "").strip()
        home_dir      = (body.get("home_dir") or "").strip()
        state_version = (body.get("state_version") or "").strip()
        if not username or not re.match(r"^[a-z_][a-z0-9_-]*$", username):
            return jsonify({"error": "ERR_INVALID_USERNAME"}), 400
        cfg_s = config_manager.load_config_settings(nixos_dir)
        hm_dir_name = (cfg_s.get("hm_dir") or "home").strip() or "home"
        hm_dir = Path(nixos_dir) / hm_dir_name
        if not _path_inside(hm_dir, Path(nixos_dir)):
            return jsonify({"error": "ERR_PATH"}), 400
        hm_dir.mkdir(parents=True, exist_ok=True)
        target = hm_dir / f"{username}.nix"
        if target.exists():
            return jsonify({"error": "ERR_FILE_EXISTS"}), 409
        content = hm_generator.create_hm_file(username, home_dir, state_version)
        target.write_text(content, encoding="utf-8")
        git_manager.auto_commit(nixos_dir)
        rel = str(target.relative_to(Path(nixos_dir)))
        return jsonify({"success": True, "path": rel, "content": content})

    @app.route("/api/hm/patch", methods=["POST"])
    def hm_patch():
        """Patch individual fields in a NiCo-managed HM .nix file in-place.
        Only touches the fields present in the request body; all other content
        (bashrcExtra, xdg.desktopEntries, …) is preserved unchanged."""
        if err := _check_csrf(): return err
        nixos_dir, err = _require_setup()
        if err:
            return err

        body = request.get_json(silent=True) or {}
        rel  = body.get("path", "").strip()
        if not rel:
            return jsonify({"error": "ERR_NO_PATH"}), 400

        nixos_path = Path(nixos_dir).resolve()
        target = (nixos_path / rel).resolve()
        try:
            rel_resolved = target.relative_to(nixos_path)
        except ValueError:
            return jsonify({"error": "ERR_PATH_OUTSIDE"}), 403

        # HM patching is only valid for .nix files below hm_dir; without this
        # check the endpoint could rewrite configuration.nix/flake.nix & Co.
        cfg_settings = config_manager.load_config_settings(nixos_dir)
        hm_dir = (cfg_settings.get("hm_dir") or "home").strip() or "home"
        if target.suffix != ".nix" or rel_resolved.parts[:1] != (hm_dir,):
            return jsonify({"error": "ERR_NOT_HM_FILE"}), 400

        try:
            content = target.read_text(encoding="utf-8")
        except OSError:
            return jsonify({"error": "ERR_FILE_READ"}), 500

        ftype = _get_nico_type(content)
        if ftype not in (None, "", "hm"):
            return jsonify({"error": "ERR_NOT_HM_FILE"}), 400

        if "username"      in body: content = _hm_patch_str( content, "home.username",               body["username"])
        if "home_dir"      in body: content = _hm_patch_str( content, "home.homeDirectory",          body["home_dir"])
        if "state_version" in body: content = _hm_patch_str( content, "home.stateVersion",           body["state_version"])
        if "hm_enable"     in body: content = _hm_patch_bool(content, "programs.home-manager.enable", body["hm_enable"])
        if "args"          in body: content = _hm_patch_args(content, body["args"])
        if "shell_init_extra" in body:
            content = _hm_patch_init_extra(content, body["shell_init_extra"])
        if "packages" in body:
            content = _hm_patch_packages(content, body["packages"])

        content = _hm_update_hash(content)

        try:
            target.write_text(content, encoding="utf-8")
        except OSError:
            return jsonify({"error": "ERR_FILE_WRITE"}), 500

        git_manager.auto_commit(nixos_dir)
        return jsonify({"success": True, "content": content})

