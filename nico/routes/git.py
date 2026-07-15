"""Git integration routes.

Split from server.py. Route bodies are unchanged; shared request helpers
from create_app() are passed in via the ctx dict.
"""

import shutil

from flask import jsonify, request

from .. import config_manager, git_manager


def register(app, ctx):
    _check_csrf    = ctx["check_csrf"]
    _require_setup = ctx["require_setup"]

    # -------------------------------------------------------------------- git

    @app.route("/api/git/status")
    def git_status():
        import shutil
        git_installed = shutil.which("git") is not None
        nixos_dir, err = _require_setup()
        if err:
            return jsonify({"has_git": False, "git_installed": git_installed})
        return jsonify({
            "has_git":       git_manager.is_git_repo(nixos_dir),
            "git_installed": git_installed,
        })

    @app.route("/api/git/remote-status")
    def git_remote_status():
        nixos_dir, err = _require_setup()
        if err:
            return jsonify({"has_git": False, "has_remote": False, "remote_url": "", "behind": 0, "error": ""})
        return jsonify(git_manager.check_remote_status(nixos_dir))

    @app.route("/api/git/start-check")
    def git_start_check():
        nixos_dir, err = _require_setup()
        if err:
            return jsonify({
                "state": "error",
                "git_installed": True,
                "has_git": False,
                "has_remote": False,
                "dirty": False,
                "ahead": 0,
                "behind": 0,
                "remote_url": "",
                "detail": "not setup",
            })
        return jsonify(git_manager.check_start_guard(nixos_dir))

    @app.route("/api/git/init", methods=["POST"])
    def git_init():
        if err := _check_csrf(): return err
        nixos_dir, err = _require_setup()
        if err:
            return err
        ok, msg = git_manager.init_repo(nixos_dir)
        return jsonify({"success": ok, "message": msg})

    @app.route("/api/git/log")
    def git_log():
        nixos_dir, err = _require_setup()
        if err:
            return jsonify({"commits": []})
        return jsonify({"commits": git_manager.get_log(nixos_dir)})

    @app.route("/api/git/pull", methods=["POST"])
    def git_pull():
        if err := _check_csrf(): return err
        nixos_dir, err = _require_setup()
        if err:
            return err
        ok, msg = git_manager.git_pull(nixos_dir)
        return jsonify({"success": ok, "message": msg})

    @app.route("/api/git/fetch-remote", methods=["POST"])
    def git_fetch_remote():
        if err := _check_csrf(): return err
        nixos_dir, err = _require_setup()
        if err:
            return err
        ok, msg = git_manager.git_fetch_remote(nixos_dir)
        return jsonify({"success": ok, "message": msg})

    @app.route("/api/git/remote-branches")
    def git_remote_branches():
        nixos_dir, err = _require_setup()
        if err:
            return jsonify({"success": False, "branches": [], "message": "Setup fehlt."}), 400
        ok, branches, msg = git_manager.list_remote_branches(nixos_dir)
        return jsonify({"success": ok, "branches": branches, "message": msg})

    @app.route("/api/git/set-upstream", methods=["POST"])
    def git_set_upstream():
        if err := _check_csrf(): return err
        nixos_dir, err = _require_setup()
        if err:
            return err
        body = request.get_json(silent=True) or {}
        branch = (body.get("branch") or "").strip()
        if not branch:
            return jsonify({"success": False, "message": "Kein Remote-Branch angegeben."}), 400
        ok, msg = git_manager.set_upstream_branch(nixos_dir, branch)
        return jsonify({"success": ok, "message": msg})

    @app.route("/api/git/push", methods=["POST"])
    def git_push():
        if err := _check_csrf(): return err
        nixos_dir, err = _require_setup()
        if err:
            return err
        ok, msg, code = git_manager.git_push(nixos_dir)
        return jsonify({"success": ok, "message": msg, "error_code": code})

    @app.route("/api/git/set-remote", methods=["POST"])
    def git_set_remote():
        if err := _check_csrf(): return err
        nixos_dir, err = _require_setup()
        if err:
            return err
        body = request.get_json(silent=True) or {}
        url  = (body.get("url") or "").strip()
        if not url:
            return jsonify({"success": False, "message": "Keine URL angegeben."}), 400
        ok, msg = git_manager.set_remote(nixos_dir, url)
        if not ok:
            return jsonify({"success": False, "message": msg}), 500
        return jsonify({"success": True})

    @app.route("/api/git/check-write", methods=["POST"])
    def git_check_write():
        if err := _check_csrf(): return err
        nixos_dir, err = _require_setup()
        if err:
            return err
        ok, code, raw = git_manager.check_write_access(nixos_dir)
        return jsonify({"ok": ok, "error_code": code, "raw": raw})

    @app.route("/api/git/reset-hard", methods=["POST"])
    def git_reset_hard():
        if err := _check_csrf(): return err
        nixos_dir, err = _require_setup()
        if err:
            return err
        ok, msg = git_manager.git_reset_hard(nixos_dir)
        return jsonify({"success": ok, "message": msg})

    @app.route("/api/git/discard-local", methods=["POST"])
    def git_discard_local():
        if err := _check_csrf(): return err
        nixos_dir, err = _require_setup()
        if err:
            return err
        ok, msg = git_manager.git_discard_changes(nixos_dir)
        return jsonify({"success": ok, "message": msg})

    @app.route("/api/git/commit-push", methods=["POST"])
    def git_commit_push():
        if err := _check_csrf(): return err
        nixos_dir, err = _require_setup()
        if err:
            return err
        label = (request.get_json(silent=True) or {}).get("label", "")
        ok, msg = git_manager.git_commit_push(nixos_dir, label=label)
        return jsonify({"success": ok, "message": msg})

    @app.route("/api/git/push-force", methods=["POST"])
    def git_push_force():
        if err := _check_csrf(): return err
        nixos_dir, err = _require_setup()
        if err: return err
        ok, msg = git_manager.git_push_force(nixos_dir)
        return jsonify({"success": ok, "message": msg})

    @app.route("/api/git/commit-push-force", methods=["POST"])
    def git_commit_push_force():
        if err := _check_csrf(): return err
        nixos_dir, err = _require_setup()
        if err: return err
        ok, msg = git_manager.git_commit_push_force(nixos_dir)
        return jsonify({"success": ok, "message": msg})

    @app.route("/api/git/gitignore", methods=["GET"])
    def get_gitignore():
        nixos_dir, err = _require_setup()
        if err: return err
        exists, content = git_manager.read_gitignore(nixos_dir)
        return jsonify({"exists": exists, "content": content})

    @app.route("/api/git/create-gitignore", methods=["POST"])
    def create_gitignore():
        if err := _check_csrf(): return err
        nixos_dir, err = _require_setup()
        if err: return err
        body = request.get_json(silent=True) or {}
        content = body.get("content")
        if content is not None:
            ok, msg = git_manager.write_gitignore(nixos_dir, content)
        else:
            ok, msg = git_manager.create_gitignore(nixos_dir)
        return jsonify({"success": ok, "message": msg})

    # ── Fremddateien (foreign-file guard) ─────────────────────────────────

    @app.route("/api/git/foreign-files")
    def git_foreign_files():
        """List foreign files: pending (untracked, need a decision) and tracked
        (already in git, candidates for the cleanup dialog)."""
        nixos_dir, err = _require_setup()
        if err: return err
        settings = config_manager.load_config_settings(nixos_dir)
        enabled  = bool(settings.get("git_foreign_guard", True))
        keep     = settings.get("git_keep_files", []) or []
        if not git_manager.is_git_repo(nixos_dir):
            return jsonify({"enabled": enabled, "pending": [], "tracked": []})
        return jsonify({
            "enabled": enabled,
            "pending": git_manager.list_untracked_foreign(nixos_dir) if enabled else [],
            "tracked": git_manager.list_tracked_foreign(nixos_dir, keep=keep),
        })

    @app.route("/api/git/foreign-decide", methods=["POST"])
    def git_foreign_decide():
        """Apply the user's decision on pending foreign files:
        include → stage for the next commit, exclude → .gitignore entry."""
        if err := _check_csrf(): return err
        nixos_dir, err = _require_setup()
        if err: return err
        body    = request.get_json(silent=True) or {}
        include = [f for f in body.get("include", []) if isinstance(f, str)]
        exclude = [f for f in body.get("exclude", []) if isinstance(f, str)]
        ok1, msg1 = git_manager.include_foreign_files(nixos_dir, include)
        ok2, msg2 = git_manager.exclude_foreign_files(nixos_dir, exclude)
        if not (ok1 and ok2):
            return jsonify({"success": False, "error": msg1 or msg2}), 500
        return jsonify({"success": True})

    @app.route("/api/git/foreign-cleanup", methods=["POST"])
    def git_foreign_cleanup():
        """Clean up tracked foreign files: untrack → git rm --cached +
        .gitignore + commit, keep → remembered in config.json (git_keep_files)."""
        if err := _check_csrf(): return err
        nixos_dir, err = _require_setup()
        if err: return err
        body    = request.get_json(silent=True) or {}
        untrack = [f for f in body.get("untrack", []) if isinstance(f, str)]
        keep    = [f for f in body.get("keep", []) if isinstance(f, str)]
        if keep:
            tracked  = set(git_manager.list_tracked_foreign(nixos_dir))
            settings = config_manager.load_config_settings(nixos_dir)
            merged   = sorted(set(settings.get("git_keep_files", []) or [])
                              | {k for k in keep if k in tracked})
            config_manager.save_config_settings(nixos_dir, {"git_keep_files": merged})
        ok, msg = git_manager.untrack_foreign_files(nixos_dir, untrack)
        if not ok:
            return jsonify({"success": False, "error": msg}), 500
        return jsonify({"success": True})

    @app.route("/api/git/close-check")
    def git_close_check():
        nixos_dir, err = _require_setup()
        if err:
            return jsonify({"has_remote": False, "needs_push": False, "ahead": 0, "dirty": False})
        return jsonify(git_manager.check_close_state(nixos_dir))

    @app.route("/api/git/rollback", methods=["POST"])
    def git_rollback():
        if err := _check_csrf(): return err
        nixos_dir, err = _require_setup()
        if err:
            return err
        body = request.get_json(silent=True) or {}
        commit_hash = body.get("hash", "")
        ok, msg = git_manager.rollback(nixos_dir, commit_hash)
        return jsonify({"success": ok, "message": msg})

    @app.route("/api/git/diff")
    def git_diff():
        """Return structured diff between two commits."""
        nixos_dir, err = _require_setup()
        if err:
            return err
        from_hash = request.args.get("from", "").strip()
        to_hash   = request.args.get("to",   "HEAD").strip()
        if not from_hash:
            return jsonify({"error": "ERR_MISSING_FROM"}), 400
        result = git_manager.get_diff(nixos_dir, from_hash, to_hash)
        if "error" in result:
            return jsonify(result), 400
        return jsonify(result)

