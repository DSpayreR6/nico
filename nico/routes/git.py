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
        ok, msg, info = git_manager.git_pull(nixos_dir)
        return jsonify({"success": ok, "message": msg, "info": info})

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
        # Collected before pushing: afterwards nothing is "ahead" any more and
        # there would be nothing left to report.
        info = git_manager.push_info(nixos_dir)
        ok, msg, code = git_manager.git_push(nixos_dir)
        return jsonify({"success": ok, "message": msg, "error_code": code,
                        "info": info if ok else {}})

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

    @app.route("/api/git/commit", methods=["POST"])
    def git_commit_only():
        """Commit without pushing – for a repository that has no remote.

        Pushing needs a remote, committing does not. Without this route the
        rebuild monitor could only offer "Commit & Push", which fails on a
        local-only repository.
        """
        if err := _check_csrf(): return err
        nixos_dir, err = _require_setup()
        if err:
            return err
        label = (request.get_json(silent=True) or {}).get("label", "")
        ok, msg, skipped = git_manager.auto_commit(nixos_dir, label=label)
        if not ok and "nothing to commit" in msg:
            ok, msg = True, ""
        return jsonify({"success": ok, "message": msg, "skipped_large": skipped})

    @app.route("/api/git/commit-push", methods=["POST"])
    def git_commit_push():
        if err := _check_csrf(): return err
        nixos_dir, err = _require_setup()
        if err:
            return err
        label = (request.get_json(silent=True) or {}).get("label", "")
        ok, msg, code, info = git_manager.git_commit_push(nixos_dir, label=label)
        return jsonify({"success": ok, "message": msg, "error_code": code, "info": info})

    @app.route("/api/git/push-force", methods=["POST"])
    def git_push_force():
        if err := _check_csrf(): return err
        nixos_dir, err = _require_setup()
        if err: return err
        info = git_manager.push_info(nixos_dir)
        ok, msg, code = git_manager.git_push_force(nixos_dir)
        return jsonify({"success": ok, "message": msg, "error_code": code,
                        "info": info if ok else {}})

    @app.route("/api/git/commit-push-force", methods=["POST"])
    def git_commit_push_force():
        if err := _check_csrf(): return err
        nixos_dir, err = _require_setup()
        if err: return err
        ok, msg, code, info = git_manager.git_commit_push_force(nixos_dir)
        return jsonify({"success": ok, "message": msg, "error_code": code, "info": info})

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

    # ── Git-Hygiene (start check) ─────────────────────────────────────────
    # These three problems used to be reported by validator rules only, which
    # run when the user clicks "Prüfen" – so nobody ever saw them. They are
    # cheap enough to check on every start.

    @app.route("/api/git/hygiene")
    def git_hygiene():
        """Report repo problems that let NiCo artefacts end up in git."""
        nixos_dir, err = _require_setup()
        if err:
            return jsonify({"items": []})
        if not git_manager.is_git_repo(nixos_dir):
            return jsonify({"items": []})

        from pathlib import Path
        items = []

        exists, content = git_manager.read_gitignore(nixos_dir)
        present = {l.strip() for l in content.splitlines() if l.strip() and not l.startswith("#")}
        missing = [e for e in git_manager.GITIGNORE_ENTRIES if e not in present]
        if missing:
            items.append({"id": "gitignore", "exists": exists, "missing": missing})

        old_log = Path(nixos_dir) / "nixos-rebuild.log"
        if old_log.exists():
            # NiCo writes to the cache dir since 2026-09-12; anything here is a
            # leftover that git can still pick up.
            items.append({
                "id":      "old_log",
                "size_mb": round(old_log.stat().st_size / (1024 * 1024), 1),
                "tracked": "nixos-rebuild.log" in git_manager.list_tracked_files(nixos_dir),
            })

        settings = config_manager.load_config_settings(nixos_dir)
        keep     = settings.get("git_keep_files", []) or []
        foreign  = git_manager.list_tracked_foreign(nixos_dir, keep=keep)
        if foreign:
            items.append({"id": "foreign", "files": foreign})

        # Oversized files are the one item that can get the repo into a state
        # that is no longer fully repairable, so they are reported here too.
        report = git_manager.large_file_report(nixos_dir)
        if report["worktree"] or report["local"] or report["published"]:
            items.append({
                "id":        "large",
                "worktree":  len(report["worktree"]),
                "local":     len(report["local"]),
                "published": len(report["published"]),
            })

        return jsonify({"items": items})

    @app.route("/api/git/hygiene/fix", methods=["POST"])
    def git_hygiene_fix():
        """Apply the fixes the user accepted in the hygiene dialog.

        Only the two harmless ones live here: completing .gitignore and
        deleting the leftover log. Tracked foreign files stay with the existing
        cleanup dialog, because untracking them is a commit and needs the
        per-file decision.
        """
        if err := _check_csrf(): return err
        nixos_dir, err = _require_setup()
        if err:
            return err
        from pathlib import Path
        actions = (request.get_json(silent=True) or {}).get("actions", [])
        done, failed = [], []

        if "gitignore" in actions:
            ok, msg = git_manager.create_gitignore(nixos_dir)
            (done if ok else failed).append("gitignore" if ok else f"gitignore: {msg}")

        if "old_log" in actions:
            try:
                log_path = Path(nixos_dir) / "nixos-rebuild.log"
                if "nixos-rebuild.log" in git_manager.list_tracked_files(nixos_dir):
                    git_manager.untrack_foreign_files(nixos_dir, ["nixos-rebuild.log"])
                log_path.unlink(missing_ok=True)
                done.append("old_log")
            except Exception as exc:
                failed.append(f"old_log: {exc}")

        return jsonify({"success": not failed, "done": done, "failed": failed})

    # ── Zu große Dateien (cleanup) ────────────────────────────────────────

    @app.route("/api/git/large-files")
    def git_large_files():
        """Where oversized files sit: working tree, local commits, or already
        published (see git_manager.large_file_report)."""
        nixos_dir, err = _require_setup()
        if err:
            return jsonify({"worktree": [], "local": [], "published": [], "base": "", "commits": 0})
        report = git_manager.large_file_report(nixos_dir)
        report["limit_mb"] = git_manager.MAX_STAGE_BYTES // (1024 * 1024)
        return jsonify(report)

    @app.route("/api/git/large-files/clean", methods=["POST"])
    def git_large_files_clean():
        """Remove the chosen oversized files from the working tree and from
        local (unpublished) commits. Published history is refused."""
        if err := _check_csrf(): return err
        nixos_dir, err = _require_setup()
        if err:
            return err
        body   = request.get_json(silent=True) or {}
        paths  = [p for p in body.get("paths", []) if isinstance(p, str)]
        delete = bool(body.get("delete", True))
        if not paths:
            return jsonify({"success": False, "message": "Keine Datei angegeben."}), 400
        ok, msg, result = git_manager.clean_large_files(nixos_dir, paths, delete_from_disk=delete)
        return jsonify({"success": ok, "message": msg, **result})

    @app.route("/api/git/backup-branch/drop", methods=["POST"])
    def git_drop_backup_branch():
        """Delete the backup branch left by a cleanup and run gc, which is what
        actually frees the disk space the removed files were holding."""
        if err := _check_csrf(): return err
        nixos_dir, err = _require_setup()
        if err:
            return err
        branch = ((request.get_json(silent=True) or {}).get("branch") or "").strip()
        ok, msg = git_manager.drop_backup_branch(nixos_dir, branch)
        return jsonify({"success": ok, "message": msg})

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

