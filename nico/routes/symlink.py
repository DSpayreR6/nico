"""/etc/nixos symlink routes.

Split from server.py. Route bodies are unchanged; shared request helpers
from create_app() are passed in via the ctx dict.
"""

import shlex
from pathlib import Path

from flask import jsonify, request

from .. import git_manager, importer


def register(app, ctx):
    _check_csrf    = ctx["check_csrf"]
    _require_setup = ctx["require_setup"]
    _nixos_dir     = ctx["nixos_dir"]
    _sudo_nonces   = ctx["sudo_nonces"]
    _time_mod      = ctx["time_mod"]

    # ── /etc/nixos Symlink ────────────────────────────────────────────────

    @app.route("/api/symlink/status")
    def symlink_status():
        """Gibt zurück ob /etc/nixos bereits ein Symlink auf das nico-Verzeichnis ist."""
        etc_nixos = Path("/etc/nixos")
        nixos_dir = _nixos_dir()
        if not nixos_dir:
            return jsonify({"status": "no_setup"})
        if etc_nixos.is_symlink():
            target = str(etc_nixos.resolve())
            is_nico = (Path(target).resolve() == Path(nixos_dir).resolve())
            return jsonify({"status": "symlink", "points_to_nico": is_nico, "target": target})
        if etc_nixos.exists():
            return jsonify({"status": "dir"})
        return jsonify({"status": "missing"})

    @app.route("/api/symlink/create", methods=["POST"])
    def symlink_create():
        """
        Kopiert optional .nix-Dateien aus /etc/nixos ins nixos_dir, verschiebt
        /etc/nixos nach /etc/nixos.bak und legt einen Symlink
        /etc/nixos → nixos_config_dir an.
        Erfordert sudo-Rechte (wird per subprocess mit sudo ausgeführt).

        Body (optional): { "copy_files": true }
        """
        if err := _check_csrf(): return err
        nixos_dir, err = _require_setup()
        if err:
            return err

        body = request.get_json(silent=True) or {}
        copy_files = bool(body.get("copy_files", False))

        # Sudo-Passwort: direkt im Body ODER via Nonce
        sudo_password = body.get("sudo_password", "")
        nonce = body.get("sudo_nonce", "")
        if not sudo_password and nonce and nonce in _sudo_nonces:
            pw, expiry = _sudo_nonces.pop(nonce)
            if expiry > _time_mod.time():
                sudo_password = pw

        import subprocess as _sp
        etc_nixos = Path("/etc/nixos")
        etc_bak   = Path("/etc/nixos.bak")

        if etc_nixos.is_symlink():
            target = str(etc_nixos.resolve())
            is_nico = (Path(target).resolve() == Path(nixos_dir).resolve())
            if is_nico:
                return jsonify({"ok": True, "already_done": True})
            return jsonify({"error": "ERR_SYMLINK_OTHER_TARGET", "target": target}), 400

        if not etc_nixos.exists():
            return jsonify({"error": "ERR_NIXOS_MISSING"}), 400

        if etc_bak.exists():
            return jsonify({"error": "ERR_BACKUP_EXISTS", "backup": str(etc_bak)}), 409

        # Schritt 1: .nix-Dateien kopieren (falls gewünscht)
        if copy_files:
            # copy_nix_tree deletes existing .nix/.lock first – secure them like
            # the import endpoints do (ZIP backup + auto-commit).
            if importer.dir_has_non_zip_files(nixos_dir):
                try:
                    importer.backup_to_zip(nixos_dir)
                except OSError as exc:
                    return jsonify({"error": "ERR_BACKUP_FAILED", "detail": str(exc)}), 500
            try:
                git_manager.auto_commit(nixos_dir, label="NiCo: Sicherung vor /etc/nixos-Kopie")
            except Exception:
                pass
            try:
                importer.copy_nix_tree(str(etc_nixos), nixos_dir)
            except Exception as e:
                return jsonify({"error": "ERR_COPY_FAILED", "detail": str(e)}), 500

        # Schritt 2: mv /etc/nixos /etc/nixos.bak && ln -s <nixos_dir> /etc/nixos
        cmd = ["sudo", "-S", "sh", "-c",
               f"mv /etc/nixos /etc/nixos.bak && ln -s {shlex.quote(str(Path(nixos_dir).resolve()))} /etc/nixos"]
        try:
            result = _sp.run(cmd, input=(sudo_password + "\n") if sudo_password else "",
                             capture_output=True, text=True, timeout=15)
            if result.returncode != 0:
                return jsonify({"error": "ERR_SYMLINK_FAILED",
                                "detail": (result.stderr or result.stdout).strip()}), 500
        except _sp.TimeoutExpired:
            return jsonify({"error": "ERR_SYMLINK_TIMEOUT"}), 500
        except FileNotFoundError:
            return jsonify({"error": "ERR_SUDO_MISSING"}), 500

        return jsonify({"ok": True, "backup": str(etc_bak), "link": str(etc_nixos)})

    @app.route("/api/symlink/remove", methods=["POST"])
    def symlink_remove():
        """
        Entfernt den Symlink /etc/nixos und stellt /etc/nixos.bak als Verzeichnis wieder her.
        """
        if err := _check_csrf(): return err

        body = request.get_json(silent=True) or {}
        sudo_password = body.get("sudo_password", "")
        nonce = body.get("sudo_nonce", "")
        if not sudo_password and nonce and nonce in _sudo_nonces:
            pw, expiry = _sudo_nonces.pop(nonce)
            if expiry > _time_mod.time():
                sudo_password = pw

        import subprocess as _sp
        etc_nixos = Path("/etc/nixos")
        etc_bak   = Path("/etc/nixos.bak")

        if not etc_nixos.is_symlink():
            return jsonify({"error": "ERR_NOT_A_SYMLINK"}), 400
        if not etc_bak.exists():
            return jsonify({"error": "ERR_NO_BACKUP"}), 400

        cmd = ["sudo", "-S", "sh", "-c", "rm /etc/nixos && mv /etc/nixos.bak /etc/nixos"]
        try:
            result = _sp.run(cmd, input=(sudo_password + "\n") if sudo_password else "",
                             capture_output=True, text=True, timeout=15)
            if result.returncode != 0:
                return jsonify({"error": "ERR_SYMLINK_REMOVE_FAILED",
                                "detail": (result.stderr or result.stdout).strip()}), 500
        except _sp.TimeoutExpired:
            return jsonify({"error": "ERR_SYMLINK_TIMEOUT"}), 500
        except FileNotFoundError:
            return jsonify({"error": "ERR_SUDO_MISSING"}), 500

        return jsonify({"ok": True})

