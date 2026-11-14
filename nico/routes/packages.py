"""System package search and management routes.

Split from server.py. Route bodies are unchanged; shared request helpers
from create_app() are passed in via the ctx dict.
"""


from flask import jsonify, request

import re

from .. import config_manager
from ..brix import PKG_INACTIVE_KNOWN

_PKG_ATTR_RE = re.compile(r"[A-Za-z_][\w.'+-]*")


def register(app, ctx):
    _check_csrf    = ctx["check_csrf"]
    _require_setup = ctx["require_setup"]
    _nixos_dir     = ctx["nixos_dir"]

    # ---------------------------------------------------------------- packages

    @app.route("/api/packages/search")
    def packages_search():
        # A GET, but it starts `nix search` (network, up to 60 s) – any web
        # page can trigger a GET against 127.0.0.1 (revision 2026-11-10, S13).
        if err := _check_csrf(): return err
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
            # The message is a code with its data (packages._err); the
            # interface splits and translates it. The old `detail` field was
            # never read, so the search failed with a literal "{0}".
            return jsonify({"error": str(e)}), 502

    @app.route("/api/packages/add", methods=["POST"])
    def packages_add():
        if err := _check_csrf(): return err
        nixos_dir, err = _require_setup()
        if err:
            return err
        pkg = request.get_json(silent=True) or {}
        if not pkg.get("attr"):
            return jsonify({"error": "ERR_NO_PACKAGE"}), 400
        # The attribute goes into the package list as it is, so it has to be
        # a plain name – the same shape the importer reads back.
        if not isinstance(pkg["attr"], str) or not _PKG_ATTR_RE.fullmatch(pkg["attr"]):
            return jsonify({"error": "ERR_INVALID_NAME"}), 400

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
        data[PKG_INACTIVE_KNOWN] = True
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
        data[PKG_INACTIVE_KNOWN] = True
        config_manager.save_config(nixos_dir, data)
        return jsonify({"success": True})

    # /api/packages/<attr>/toggle stood here, called by nothing. A package
    # switched off through it fell out of the file and was gone on the next
    # load (revision 2026-11-10, T16).

