"""ZIP import, categorize and /etc/nixos import routes.

Split from server.py. Route bodies are unchanged; shared request helpers
from create_app() are passed in via the ctx dict.
"""

import json
from pathlib import Path

from flask import jsonify, request

from .. import atomic, config_manager, git_manager, hardware, importer
from ..generator import check_version_hash as _check_version_hash
from ..core import (
    classify_filename as _classify_filename,
    effective_panel_mode as _effective_panel_mode,
    get_nico_type as _get_nico_type,
    set_type_in_content as _set_type_in_content,
)


def _store_edit_mode(nixos_dir: str, value):
    """Store the answer to "how do you want to edit this config?" BEFORE the
    import touches a file. Returns (ok, previous) – previous goes to
    _undo_edit_mode when the import fails afterwards.

    Text mode: NiCo works like an editor – the import only copies, no file
    is restructured or stamped, and files without a NiCo line open in text
    mode (`panel_default` "r" in config.json, the switch under Settings).
    Panel: as before – configuration.nix and flake.nix are rebuilt for the
    form. No answer (older callers, tests): the setting stays as it is and
    the import restructures as before (Martin, 2026-11-22).

    Until 2026-11-26 the answer was stored after the import and a failed
    save was swallowed: a text import reported success, and the files
    opened in the panel - which rebuilds them (revision 2026-11-25, W2).
    Now: not stored, not imported (Martin: either both are right, or
    nothing happened).
    """
    if value not in ("r", "p"):
        return True, None
    try:
        settings = config_manager.load_config_settings(nixos_dir)
        previous = ("panel_default" in settings, settings.get("panel_default"))
        settings["panel_default"] = value
        config_manager.save_config_settings(nixos_dir, settings)
    except OSError:
        return False, None
    return True, previous


def _undo_edit_mode(nixos_dir: str, previous) -> None:
    """The import failed after the answer was stored: put the old one back."""
    if previous is None:
        return
    try:
        settings = config_manager.load_config_settings(nixos_dir)
        if previous[0]:
            settings["panel_default"] = previous[1]
        else:
            settings.pop("panel_default", None)
        config_manager.save_config_settings(nixos_dir, settings)
    except OSError:
        pass


_EDIT_MODE_FAILED = ({"error": "ERR_EDIT_MODE_SAVE"}, 500)


def _stash_hardware(nixos_dir: str) -> None:
    """Park this machine's hardware configs before an import replaces them.

    Every import deletes the .nix files of the config directory and copies the
    incoming ones over them – the machine's own hardware-configuration.nix
    included. /etc/nixos is no fallback for it: with NiCo's own symlink feature
    /etc/nixos *is* the config directory, so after the import the only file on
    offer would be the imported foreign one. Failures are swallowed on purpose:
    the pre-import ZIP and the git checkpoint still exist, and no import may be
    blocked by a copy that is only there to make the repair offer better.
    """
    try:
        hardware.stash_hw_files(nixos_dir)
    except Exception:
        pass


def _import_failed(exc: OSError):
    """Response for an import that stopped (importer.replace_nix_tree)."""
    if isinstance(exc, importer.ImportNoMainError):
        return jsonify({"error": "ERR_IMPORT_NO_MAIN"}), 400
    if isinstance(exc, importer.ImportWriteError):
        code = "ERR_IMPORT_WRITE" if exc.restored else "ERR_IMPORT_PARTIAL"
    elif isinstance(exc, PermissionError) or isinstance(exc.__cause__, PermissionError):
        code = "ERR_IMPORT_PERMISSION"
    else:
        code = "ERR_IMPORT_READ"
    return jsonify({"error": code, "detail": str(exc)}), 500


def register(app, ctx):
    _check_csrf          = ctx["check_csrf"]
    _require_setup       = ctx["require_setup"]
    _apply_import_result = ctx["apply_import_result"]
    _reimport_flake      = ctx["reimport_flake"]
    _safe_import_dest    = ctx["safe_import_dest"]

    # ------------------------------------------------------------ zip-import

    @app.route("/api/import/zip/check", methods=["POST"])
    def import_zip_check():
        """
        Nimmt eine ZIP-Datei (multipart field 'file'), prüft ob auf dem
        Top-Level eine configuration.nix oder flake.nix vorhanden ist.
        Schreibt nichts auf Disk.
        """
        if err := _check_csrf(): return err
        zfile = request.files.get("file")
        if not zfile:
            return jsonify({"error": "ERR_NO_FILE"}), 400
        import io
        import zipfile
        try:
            data = zfile.read()
            with zipfile.ZipFile(io.BytesIO(data)) as zf:
                names = zf.namelist()
        except zipfile.BadZipFile:
            return jsonify({"error": "ERR_INVALID_ZIP"}), 400
        top_level = {n.split("/")[0] for n in names if n}
        # A file entry of that name at the top, not a directory called so
        # (revision 2026-11-25, F4: `configuration.nix/x` passed).
        found = None
        for candidate in ("configuration.nix", "flake.nix"):
            if candidate in names:
                found = candidate
                break
        return jsonify({
            "valid":       found is not None,
            "found_file":  found,
            "top_level":   sorted(top_level),
        })

    @app.route("/api/import/zip/apply", methods=["POST"])
    def import_zip_apply():
        """
        Importiert eine ZIP-Datei in das nixos_config_dir.
        1. Backup (falls confirmed=true und Ziel nicht leer)
        2. Bestehende .nix/.lock löschen
        3. ZIP extrahieren
        4. configuration.nix / flake.nix parsen + in nico.json mergen
        """
        if err := _check_csrf(): return err
        nixos_dir, err = _require_setup()
        if err:
            return err

        zfile     = request.files.get("file")
        confirmed = request.form.get("confirmed", "false").lower() == "true"
        edit_mode = request.form.get("edit_mode")

        if not zfile:
            return jsonify({"error": "ERR_NO_FILE"}), 400

        import io
        import zipfile

        try:
            zip_bytes = zfile.read()
            zf_test = zipfile.ZipFile(io.BytesIO(zip_bytes))
            zf_test.close()
        except zipfile.BadZipFile:
            return jsonify({"error": "ERR_INVALID_ZIP"}), 400

        dst = Path(nixos_dir).resolve()

        # Read every entry before the old config is touched: a damaged entry
        # used to fail only after the deletion (revision 2026-11-18, finding 1).
        incoming: dict[str, bytes] = {}
        try:
            with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
                for member in zf.infolist():
                    if member.is_dir():
                        continue
                    dest = _safe_import_dest(dst, member.filename)
                    if dest is None:  # zip-slip: entry would land outside config dir
                        continue
                    incoming[dest.relative_to(dst).as_posix()] = zf.read(member)
        except (zipfile.BadZipFile, OSError, EOFError) as exc:
            return jsonify({"error": "ERR_IMPORT_READ", "detail": str(exc)}), 400
        # What will really be taken over must hold a main file - checked
        # before the backup question, so an unusable ZIP leaves everything
        # as it is (revision 2026-11-25, F4; replace_nix_tree checks again).
        if not any(rel in ("configuration.nix", "flake.nix") for rel in incoming):
            return jsonify({"error": "ERR_IMPORT_NO_MAIN"}), 400

        # Backup-Bestätigung
        if not confirmed and importer.dir_has_non_zip_files(nixos_dir):
            from datetime import datetime as _dt
            ts = _dt.now().strftime("%Y-%m-%d-%H%M%S")
            return jsonify({
                "needs_backup_confirmation": True,
                "zip_name": f"nixos-config-{ts}.zip",
            })

        # A NiCo export brings its own config.json, and its panel_default
        # replaced the mode the user had just chosen - while the answer
        # reported a text import (Codex re-check 2026-11-26). The choice is
        # written into the incoming file instead, so files and setting are
        # one step that replace_nix_tree puts back as a whole.
        if edit_mode in ("r", "p") and "config.json" in incoming:
            try:
                arch_settings = json.loads(incoming["config.json"].decode("utf-8"))
                if not isinstance(arch_settings, dict):
                    raise ValueError("not an object")
            except (ValueError, UnicodeDecodeError) as exc:
                return jsonify({"error": "ERR_IMPORT_READ",
                                "detail": f"config.json: {exc}"}), 400
            arch_settings["panel_default"] = edit_mode
            incoming["config.json"] = (json.dumps(arch_settings, indent=2,
                                                  ensure_ascii=False)).encode("utf-8")

        # Backups first, before the first change: they hold the mode as it
        # was (Codex re-check 2026-11-26 - the choice used to be stored
        # before them).
        if confirmed:
            try:
                importer.backup_to_zip(nixos_dir)
            except OSError as exc:
                return jsonify({"error": "ERR_BACKUP_FAILED", "detail": str(exc)}), 500

        try:
            git_manager.auto_commit(nixos_dir, label="NiCo: backup before ZIP import")
        except Exception:
            pass
        _stash_hardware(nixos_dir)

        if "config.json" in incoming:
            prev_mode = None
        else:
            ok, prev_mode = _store_edit_mode(nixos_dir, edit_mode)
            if not ok:
                return jsonify(_EDIT_MODE_FAILED[0]), _EDIT_MODE_FAILED[1]

        try:
            files_copied = importer.replace_nix_tree(dst, incoming)
        except OSError as exc:
            _undo_edit_mode(nixos_dir, prev_mode)
            return _import_failed(exc)

        if edit_mode == "r":
            from .hardware import decision_json
            return jsonify({"success": True, "recognized": {}, "has_brix": False,
                            "files_copied": files_copied, "edit_mode": "r",
                            "hardware": decision_json(nixos_dir)})

        # configuration.nix parsen
        cfg_path = dst / "configuration.nix"
        recognized: dict = {}
        rest_brix = ""
        content = ""
        if cfg_path.is_file():
            try:
                content   = cfg_path.read_text(encoding="utf-8", errors="replace")
                recognized = importer.parse_config(content)
                rest_brix  = importer.build_rest_brix(content, recognized)
            except OSError:
                pass

        data = _apply_import_result(nixos_dir, recognized, rest_brix, content)
        _reimport_flake(nixos_dir, data)

        from .hardware import decision_json
        return jsonify({
            "success":      True,
            "recognized":   recognized,
            "has_brix":     bool(rest_brix.strip()),
            "files_copied": files_copied,
            "hardware":     decision_json(nixos_dir),
        })

    # ---------------------------------------------------------- categorize

    @app.route("/api/categorize", methods=["POST"])
    def categorize_files():
        """Add/update nico-version headers on all .nix files in the config dir.

        Classification rules (by filename):
          configuration.nix, default.nix  → co
          flake.nix                        → fl
          hardware-configuration.nix       → hw
          home.nix                         → hm
          everything else *.nix            → nd
          flake.lock / non-.nix files      → skipped (no comment header possible)
        """
        if err := _check_csrf(): return err
        nixos_dir, err = _require_setup()
        if err:
            return err

        root     = Path(nixos_dir)
        _cfg_s   = config_manager.load_config_settings(nixos_dir)
        _hm_dir  = _cfg_s.get("hm_dir", "home").strip() or "home"
        done: list[dict] = []
        errors: list[dict] = []

        from .. import configfiles
        # The config's own files (configfiles.py): a symlink out of it got
        # its type stamp written into the file at the other end (revision
        # 2026-11-25, S1). Hidden directories (.git) are not walked.
        for rel_s, nix_file in configfiles.scan(root).files:
            rel = Path(rel_s)

            # Pfad-basierte Klassifizierung: hm_dir/*.nix → 'hm' (höchste Priorität)
            rel_parts = rel.parts
            is_path_hm = (len(rel_parts) >= 2 and rel_parts[0] == _hm_dir)
            if is_path_hm:
                ftype = 'hm'
            else:
                ftype = _classify_filename(nix_file.name)
                if ftype is None:
                    continue

            try:
                content = nix_file.read_text(encoding="utf-8")
                # Text mode (flag #r, or no flag and the config default):
                # the file belongs to the editor and stays byte for byte –
                # a type stamp is a line NiCo adds (Martin, 2026-11-22).
                if _effective_panel_mode(nixos_dir, content) == "r":
                    done.append({"file": str(rel), "type": ftype, "changed": False})
                    continue
                # flake.nix und filename-basierte home.nix: nur stempeln wenn bereits
                # von NiCo verwaltet (nico-version Header). Externe Dateien werden nicht
                # angefasst – sonst würde write_files sie als NiCo-managed betrachten.
                # Pfad-basierte HM-Dateien (home/<hm_dir>/*.nix) werden immer gestempelt.
                if not is_path_hm and ftype in ("fl", "hm") and _get_nico_type(content) is None:
                    done.append({"file": str(rel), "type": ftype, "changed": False})
                    continue
                # A file a form writes back (configuration.nix, host files,
                # Home Manager) is stamped only when NiCo wrote it. The stamp
                # carries a valid hash, and a foreign file stamped here looked
                # like NiCo's own afterwards: Fall A did not ask, and the
                # first panel save dropped what the form does not hold –
                # `networking.useDHCP` from a ZIP import (VM test 2026-11-19).
                # Unstamped, opening it asks, and restructuring runs the
                # Gegenprobe.
                if ftype in ("co", "hm") and _check_version_hash(content) != "ok":
                    done.append({"file": str(rel), "type": ftype, "changed": False})
                    continue
                new_content = _set_type_in_content(content, ftype)
                changed = new_content != content
                if changed:
                    atomic.write_text(nix_file, new_content)
                done.append({"file": str(rel), "type": ftype, "changed": changed})
            except OSError as exc:
                errors.append({"file": str(rel), "error": str(exc)})

        return jsonify({"categorized": done, "errors": errors})

    # --------------------------------------------------------------- import

    @app.route("/api/import/run", methods=["POST"])
    def import_run():
        """
        Admin-Import: two modes.
        Path mode:    { config_path: "..." }  – server reads directory recursively.
        Content mode: { files: [{path, content}, ...] }  – browser sends file contents
                      (used when user picks a directory via the file browser).
        Both modes parse configuration.nix, merge known fields, save rest as brix.
        """
        if err := _check_csrf(): return err
        nixos_dir, err = _require_setup()
        if err:
            return err

        body       = request.get_json(silent=True) or {}
        files_data  = body.get("files")   # content mode
        config_path = (body.get("config_path") or "").strip()
        confirmed   = bool(body.get("confirmed", False))

        # ── Backup-Bestätigung: Zielverzeichnis nicht leer? ───────────────────
        if not confirmed and importer.dir_has_non_zip_files(nixos_dir):
            from datetime import datetime as _dt
            ts = _dt.now().strftime("%Y-%m-%d-%H%M%S")
            return jsonify({
                "needs_backup_confirmation": True,
                "zip_name": f"nixos-config-{ts}.zip",
            })
        if confirmed:
            try:
                importer.backup_to_zip(nixos_dir)
            except OSError as exc:
                return jsonify({"error": "ERR_BACKUP_FAILED", "detail": str(exc)}), 500

        git_manager.auto_commit(nixos_dir, label="NiCo: backup before import")
        _stash_hardware(nixos_dir)

        if files_data:
            # ── Content mode: browser sent file contents directly ─────────────
            config_entry = next(
                (f for f in files_data
                 if Path(f.get("path", "")).name == "configuration.nix"),
                None,
            )
            if not config_entry:
                return jsonify({"error": "ERR_NO_PATH"}), 400
            content = config_entry.get("content", "")

            recognized = importer.parse_config(content)
            rest_brix  = importer.build_rest_brix(content, recognized)

            # A clean 1:1 copy: the old .nix/.lock files go, the sent ones
            # come in - through replace_nix_tree, which puts the old state
            # back if a write fails (revision 2026-11-18, finding 1).
            dst = Path(nixos_dir).resolve()
            incoming: dict[str, bytes] = {}
            for file_item in files_data:
                rel   = file_item.get("path", "")
                fcont = file_item.get("content", "")
                if not rel:
                    continue
                # Same policy as copy_nix_tree: only .nix/.lock files are imported.
                if Path(rel).suffix not in (".nix", ".lock"):
                    continue
                dest = _safe_import_dest(dst, rel)
                if dest is None:  # traversal or hidden path segment
                    continue
                incoming[dest.relative_to(dst).as_posix()] = str(fcont).encode("utf-8")
            ok, prev_mode = _store_edit_mode(nixos_dir, body.get("edit_mode"))
            if not ok:
                return jsonify(_EDIT_MODE_FAILED[0]), _EDIT_MODE_FAILED[1]
            try:
                files_copied = importer.replace_nix_tree(dst, incoming)
            except OSError as exc:
                _undo_edit_mode(nixos_dir, prev_mode)
                return _import_failed(exc)

        elif config_path:
            # ── Path mode: server reads directory recursively ─────────────────
            cfg_file = Path(config_path).expanduser()
            if importer.main_file_leaves(cfg_file):
                return jsonify({"error": "ERR_IMPORT_NO_MAIN"}), 400
            try:
                content = cfg_file.read_text(encoding="utf-8", errors="replace")
            except FileNotFoundError:
                return jsonify({"error": "ERR_FILE_NOT_FOUND"}), 404
            except PermissionError:
                return jsonify({"error": "ERR_IMPORT_PERMISSION"}), 403
            except OSError as e:
                return jsonify({"error": "ERR_FILE_READ", "detail": str(e)}), 500

            recognized = importer.parse_config(content)
            rest_brix  = importer.build_rest_brix(content, recognized)

            ok, prev_mode = _store_edit_mode(nixos_dir, body.get("edit_mode"))
            if not ok:
                return jsonify(_EDIT_MODE_FAILED[0]), _EDIT_MODE_FAILED[1]
            try:
                files_copied = importer.copy_nix_tree(cfg_file.parent, nixos_dir)
            except OSError as exc:
                _undo_edit_mode(nixos_dir, prev_mode)
                return _import_failed(exc)

        else:
            return jsonify({"error": "ERR_NO_PATH"}), 400

        if body.get("edit_mode") == "r":
            from .hardware import decision_json
            return jsonify({"success": True, "recognized": {}, "has_brix": False,
                            "files_copied": files_copied, "edit_mode": "r",
                            "hardware": decision_json(nixos_dir)})

        # ── Common: merge into nico.json ─────────────────────────────────────
        data = _apply_import_result(nixos_dir, recognized, rest_brix, content)

        # Flake.nix: parse → panel-Felder + Brix, dann sauber regenerieren
        if any(Path(f).name == "flake.nix" for f in files_copied):
            _reimport_flake(nixos_dir, data)

        from .hardware import decision_json
        return jsonify({
            "success":      True,
            "recognized":   recognized,
            "has_brix":     bool(rest_brix.strip()),
            "files_copied": files_copied,
            "hardware":     decision_json(nixos_dir),
        })

    @app.route("/api/import/check")
    def import_check():
        """Check whether an importable /etc/nixos/configuration.nix exists."""
        available = importer.check_import_available()
        return jsonify({
            "available":       available,
            "has_hardware":    importer.has_hardware_config(),
            "config_path":     str(importer.ETC_NIXOS / "configuration.nix") if available else None,
        })

    @app.route("/api/import/exists")
    def import_exists():
        """Check whether configuration.nix exists in a given directory."""
        raw = request.args.get("path", "").strip()
        if not raw:
            return jsonify({"error": "ERR_NO_PATH"}), 400
        try:
            config_path = Path(raw).expanduser().resolve() / "configuration.nix"
        except Exception:
            return jsonify({"error": "ERR_INVALID_PATH"}), 400
        return jsonify({"exists": config_path.is_file(), "config_path": str(config_path)})

    @app.route("/api/import/source-left")
    def import_source_left():
        """Links out of an import source that the import leaves alone
        (Codex re-check 2026-11-26, decision Martin: not read there either).
        Shown before the import, like the list before every other action.
        Only the links themselves are looked at (readlink)."""
        raw = request.args.get("path", "").strip()
        if not raw:
            return jsonify({"error": "ERR_NO_PATH"}), 400
        try:
            src = Path(raw).expanduser()
            if src.name.endswith(".nix"):
                src = src.parent
            if not src.is_dir():
                # Nothing to list; the import itself says the path is wrong.
                return jsonify({"left": []})
            _files, left = importer.source_files(src)
        except OSError as exc:
            return jsonify({"error": "ERR_FILE_READ", "detail": str(exc)}), 500
        from dataclasses import asdict
        return jsonify({"left": [asdict(x) for x in left]})

    @app.route("/api/import/preview", methods=["POST"])
    def import_preview():
        """
        Parse /etc/nixos/configuration.nix and return what NiCo would extract.
        Nothing is written to disk.
        """
        if err := _check_csrf(): return err
        src = importer.ETC_NIXOS / "configuration.nix"
        if importer.main_file_leaves(src):
            return jsonify({"error": "ERR_IMPORT_NO_MAIN"}), 400
        if not src.is_file():
            return jsonify({"error": "ERR_FILE_NOT_FOUND"}), 404
        try:
            content = src.read_text(encoding="utf-8", errors="replace")
        except PermissionError:
            return jsonify({"error": "ERR_IMPORT_PERMISSION"}), 403
        except OSError as e:
            return jsonify({"error": "ERR_FILE_READ", "detail": str(e)}), 500
        recognized = importer.parse_config(content)
        rest = importer.build_rest_brix(content, recognized)
        return jsonify({
            "recognized":   recognized,
            "has_rest_brix": bool(rest.strip()),
            "has_hardware":  importer.has_hardware_config(),
        })

    @app.route("/api/import/apply", methods=["POST"])
    def import_apply():
        """
        Apply the import:
          1. Merge recognized fields into nico.json.
          2. Save unrecognized content as brix block "1-imported-config".
          3. Copy hardware-configuration.nix to the NiCo config dir.
        """
        if err := _check_csrf(): return err
        nixos_dir, err = _require_setup()
        if err:
            return err

        body      = request.get_json(silent=True) or {}
        confirmed = bool(body.get("confirmed", False))

        # ── Backup-Bestätigung: Zielverzeichnis nicht leer? ───────────────────
        if not confirmed and importer.dir_has_non_zip_files(nixos_dir):
            from datetime import datetime as _dt
            ts = _dt.now().strftime("%Y-%m-%d-%H%M%S")
            return jsonify({
                "needs_backup_confirmation": True,
                "zip_name": f"nixos-config-{ts}.zip",
            })
        if confirmed:
            try:
                importer.backup_to_zip(nixos_dir)
            except OSError as exc:
                return jsonify({"error": "ERR_BACKUP_FAILED", "detail": str(exc)}), 500

        try:
            git_manager.auto_commit(nixos_dir, label="NiCo: backup before import")
        except Exception:
            pass
        _stash_hardware(nixos_dir)

        src = importer.ETC_NIXOS / "configuration.nix"
        if importer.main_file_leaves(src):
            return jsonify({"error": "ERR_IMPORT_NO_MAIN"}), 400
        if not src.is_file():
            return jsonify({"error": "ERR_FILE_NOT_FOUND"}), 404

        try:
            content = src.read_text(encoding="utf-8", errors="replace")
        except PermissionError:
            return jsonify({"error": "ERR_IMPORT_PERMISSION"}), 403
        except OSError as e:
            return jsonify({"error": "ERR_FILE_READ", "detail": str(e)}), 500
        recognized = importer.parse_config(content)
        rest_brix  = importer.build_rest_brix(content, recognized)

        # Copy all .nix/.lock files from /etc/nixos into the config dir.
        # A failure used to be swallowed and the import reported success
        # with half a config in place (revision 2026-11-18, finding 1).
        ok, prev_mode = _store_edit_mode(nixos_dir, body.get("edit_mode"))
        if not ok:
            return jsonify(_EDIT_MODE_FAILED[0]), _EDIT_MODE_FAILED[1]
        try:
            files_copied = importer.copy_nix_tree(importer.ETC_NIXOS, nixos_dir)
        except OSError as exc:
            _undo_edit_mode(nixos_dir, prev_mode)
            return _import_failed(exc)

        if body.get("edit_mode") == "r":
            from .hardware import decision_json
            return jsonify({"success": True, "recognized": [], "has_brix": False,
                            "files_copied": files_copied, "edit_mode": "r",
                            "hardware": decision_json(nixos_dir)})

        _apply_import_result(nixos_dir, recognized, rest_brix, content)

        # An import copies whatever hardware-configuration.nix the source
        # carries. If that file describes a different machine, the config that
        # just arrived cannot boot here - the user has to decide before
        # anything is built, so the answer travels back with the import.
        from .hardware import decision_json
        return jsonify({
            "success":      True,
            "recognized":   list(recognized.keys()),
            "has_brix":     bool(rest_brix.strip()),
            "files_copied": files_copied,
            "hardware":     decision_json(nixos_dir),
        })

    # /api/import/from-path stood here, called by nothing. It would have
    # merged the fields of any file into the root configuration.nix
    # (revision 2026-11-10, T16).
