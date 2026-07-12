"""System package search and management routes.

Split from server.py. Route bodies are unchanged; shared request helpers
from create_app() are passed in via the ctx dict.
"""


from flask import jsonify, request

from .. import config_manager


def register(app, ctx):
    _check_csrf    = ctx["check_csrf"]
    _require_setup = ctx["require_setup"]
    _nixos_dir     = ctx["nixos_dir"]

    # ---------------------------------------------------------------- packages

    @app.route("/api/packages/search")
    def packages_search():
        from .. import packages as pkg_mod
        query = request.args.get("q", "").strip()
        if len(query) < 2:
            return jsonify({"results": []})
        channel = "unstable"
        d = _nixos_dir()
        if d:
            try:
                cfg_ch = (config_manager.load_config(d) or {}).get("flake_nixpkgs_channel") or ""
                if cfg_ch.strip():
                    channel = cfg_ch.strip().removeprefix("nixos-")
            except Exception:
                pass
        try:
            results = pkg_mod.search_nixpkgs(query, channel=channel)
            return jsonify({"results": results})
        except RuntimeError as e:
            return jsonify({"error": "ERR_PKG_SEARCH", "detail": str(e)}), 502

    @app.route("/api/packages/add", methods=["POST"])
    def packages_add():
        if err := _check_csrf(): return err
        nixos_dir, err = _require_setup()
        if err:
            return err
        pkg = request.get_json(silent=True) or {}
        if not pkg.get("attr"):
            return jsonify({"error": "ERR_NO_PACKAGE"}), 400

        data     = config_manager.load_config(nixos_dir) or {}
        pkglist  = data.get("packages", [])

        if any(p["attr"] == pkg["attr"] for p in pkglist):
            return jsonify({"error": "ERR_PKG_DUPLICATE"}), 409

        pkglist.append({
            "attr":        pkg["attr"],
            "pname":       pkg.get("pname", pkg["attr"]),
            "version":     pkg.get("version", ""),
            "description": pkg.get("description", ""),
            "enabled":     True,
        })
        data["packages"] = pkglist
        config_manager.save_config(nixos_dir, data)
        return jsonify({"success": True})

    @app.route("/api/packages/<path:attr>", methods=["DELETE"])
    def packages_remove(attr):
        if err := _check_csrf(): return err
        nixos_dir, err = _require_setup()
        if err:
            return err
        data = config_manager.load_config(nixos_dir) or {}
        data["packages"] = [p for p in data.get("packages", []) if p["attr"] != attr]
        config_manager.save_config(nixos_dir, data)
        return jsonify({"success": True})

    @app.route("/api/packages/<path:attr>/toggle", methods=["POST"])
    def packages_toggle(attr):
        if err := _check_csrf(): return err
        nixos_dir, err = _require_setup()
        if err:
            return err
        data = config_manager.load_config(nixos_dir) or {}
        for p in data.get("packages", []):
            if p["attr"] == attr:
                p["enabled"] = not p.get("enabled", True)
                break
        config_manager.save_config(nixos_dir, data)
        return jsonify({"success": True})

