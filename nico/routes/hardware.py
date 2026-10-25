"""Hardware-config matching routes.

Three different events can put a foreign hardware-configuration.nix into the
config directory - an import, a restore, or a plain `git clone` that NiCo never
sees - and the validator only runs on click and before a build.  So the check
cannot live in the validator alone: these routes let the import, the restore
paths and the startup check all ask the same question and act on the answer.
"""

from pathlib import Path

from flask import jsonify, request

from .. import hardware


def _file_json(f: hardware.HwFile, root: Path) -> dict:
    return {
        "path":       str(f.path.relative_to(root)),
        "host":       f.host,
        "verdict":    f.verdict,
        "missing":    f.missing,
        "unresolved": f.unresolved,
        "acked":      f.acked,
        "blocking":   f.blocking,
    }


def decision_json(nixos_dir: str) -> dict:
    """The current hardware situation of a config directory, for the frontend."""
    root = Path(nixos_dir)
    d = hardware.inspect(nixos_dir)
    return {
        "action":           d.action,
        "blocking":         d.blocking,
        "this_host":        hardware.running_host(),
        "matched_host":     d.matched_host,
        "host_name_hit":    d.host_name_hit,
        "source_available": d.source_available,
        "source_verdict":   d.source_verdict,
        "source_usable":    d.source_usable,
        "source_path":      str(hardware.ETC_NIXOS_HW) if d.source_available else None,
        "hosts":            d.hosts,
        "target":           _file_json(d.target, root) if d.target else None,
        "files":            [_file_json(f, root) for f in d.files],
    }


def register(app, ctx):
    _check_csrf    = ctx["check_csrf"]
    _require_setup = ctx["require_setup"]

    @app.route("/api/hardware/check")
    def hardware_check():
        """What has to be asked about hardware-configuration.nix right now."""
        nixos_dir, err = _require_setup()
        if err:
            return err
        return jsonify(decision_json(nixos_dir))

    @app.route("/api/hardware/apply", methods=["POST"])
    def hardware_apply():
        """
        Carry out the user's decision.

        "replace"  put this machine's file at the given host, keep the old one
                   as .import.bak
        "confirm"  record that the file there does belong to this machine
        """
        if err := _check_csrf():
            return err
        nixos_dir, err = _require_setup()
        if err:
            return err

        body   = request.get_json(silent=True) or {}
        action = (body.get("action") or "").strip()
        raw    = body.get("host")
        host   = raw.strip() if isinstance(raw, str) and raw.strip() else None

        root = Path(nixos_dir).resolve()

        # The host has to be one NiCo knows; otherwise the path below could be
        # pointed anywhere inside the config directory.
        if host is not None:
            from .. import config_manager
            if host not in config_manager.scan_hosts(nixos_dir):
                return jsonify({"error": "ERR_UNKNOWN_HOST"}), 400
            hosts_dir = (config_manager.load_config_settings(nixos_dir).get("hosts_dir")
                         or "hosts").strip() or "hosts"
            target = root / hosts_dir / host / hardware.HW_NAME
        else:
            target = root / hardware.HW_NAME

        try:
            target.resolve().relative_to(root)
        except ValueError:
            return jsonify({"error": "ERR_SYSTEM_PATH"}), 403

        if action == "replace":
            if not hardware.ETC_NIXOS_HW.is_file():
                return jsonify({"error": "ERR_NO_LOCAL_HARDWARE"}), 400
            try:
                hardware.replace_with_local(target)
            except ValueError:
                return jsonify({"error": "ERR_LOCAL_HARDWARE_MISMATCH"}), 409
            except PermissionError:
                return jsonify({"error": "ERR_IMPORT_PERMISSION"}), 403
            except OSError as exc:
                return jsonify({"error": "ERR_FILE_WRITE", "detail": str(exc)}), 500
            # The file that was just put in place is this machine's by
            # definition; confirming it keeps a label-only config out of a
            # red finding it could never clear.
            hardware.ack_hardware(nixos_dir, host, target)

        elif action == "confirm":
            if not target.is_file():
                return jsonify({"error": "ERR_FILE_NOT_FOUND"}), 404
            hardware.ack_hardware(nixos_dir, host, target)

        else:
            return jsonify({"error": "ERR_INVALID_ACTION"}), 400

        return jsonify({"success": True, "hardware": decision_json(nixos_dir)})
