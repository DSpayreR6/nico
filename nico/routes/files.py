"""Config file tree and raw file routes.

Split from server.py. Route bodies are unchanged; shared request helpers
from create_app() are passed in via the ctx dict.
"""

import shutil
from pathlib import Path

from flask import jsonify, request

from .. import config_manager, git_manager, importer
from ..core import (
    _FILENAME_TYPE_HINTS,
    _SAFE_NAME_RE,
    _VALID_FILE_TYPES,
    classify_filename as _classify_filename,
    get_nico_type as _get_nico_type,
    list_config_tree as _list_config_tree,
    read_config_file as _read_config_file,
    normalize_hm_content as _normalize_hm_content,
    set_type_in_content as _set_type_in_content,
    check_brix_integrity as _check_brix_integrity,
    get_panel_mode as _get_panel_mode,
    set_panel_mode as _set_panel_mode,
)


def register(app, ctx):
    _check_csrf    = ctx["check_csrf"]
    _require_setup = ctx["require_setup"]
    _path_inside   = ctx["path_inside"]

    # ------------------------------------------------------------------ filesystem

    @app.route("/api/files")
    def list_files():
        """Return .nix files and folders under the nixos config dir as a tree."""
        nixos_dir, err = _require_setup()
        if err:
            return err
        return jsonify(_list_config_tree(nixos_dir))

    def _hardware_config_meta(candidate: Path, root: Path) -> dict:
        inside_config = False
        try:
            candidate.relative_to(root)
            inside_config = True
        except ValueError:
            inside_config = False
        stat = candidate.stat()
        return {
            "path": str(candidate),
            "label": str(candidate),
            "mtime": int(stat.st_mtime),
            "inside_config": inside_config,
        }

    def _resolve_importable_hardware_config(raw_path: str) -> Path:
        try:
            base = Path(raw_path).expanduser().resolve()
        except OSError as exc:
            raise ValueError("ERR_INVALID_PATH") from exc

        candidate = base / "hardware-configuration.nix" if base.is_dir() else base
        if candidate.name != "hardware-configuration.nix":
            raise ValueError("ERR_INVALID_PATH")
        if not candidate.exists() or not candidate.is_file():
            raise ValueError("ERR_FILE_NOT_FOUND")
        return candidate

    @app.route("/api/files/hardware-configs")
    def list_hardware_config_candidates():
        """Return importable hardware-configuration.nix candidates from known local paths."""
        nixos_dir, err = _require_setup()
        if err:
            return err

        root = Path(nixos_dir).resolve()
        configs = []
        for raw in importer.find_hardware_configs():
            try:
                path = _resolve_importable_hardware_config(raw)
                configs.append(_hardware_config_meta(path, root))
            except (OSError, ValueError):
                continue

        return jsonify({"configs": configs})

    @app.route("/api/files/hardware-configs/check", methods=["POST"])
    def check_manual_hardware_config_candidate():
        """Check a manual file or directory path for a direct hardware-configuration.nix candidate."""
        if err := _check_csrf():
            return err
        nixos_dir, err = _require_setup()
        if err:
            return err

        body = request.get_json(silent=True) or {}
        raw_path = (body.get("path") or "").strip()
        if not raw_path:
            return jsonify({"error": "ERR_NO_PATH"}), 400

        root = Path(nixos_dir).resolve()
        inside_config = False
        try:
            candidate = _resolve_importable_hardware_config(raw_path)
            candidate.relative_to(root)
            inside_config = True
        except ValueError as exc:
            code = str(exc)
            if code in {"ERR_INVALID_PATH", "ERR_FILE_NOT_FOUND"}:
                return jsonify({"error": code}), 400 if code == "ERR_INVALID_PATH" else 404
            inside_config = False
            try:
                candidate = _resolve_importable_hardware_config(raw_path)
            except ValueError as inner_exc:
                code = str(inner_exc)
                return jsonify({"error": code}), 400 if code == "ERR_INVALID_PATH" else 404
        except OSError as exc:
            return jsonify({"error": "ERR_FILE_READ", "detail": str(exc)}), 500

        try:
            return jsonify({"config": _hardware_config_meta(candidate, root)})
        except OSError as exc:
            return jsonify({"error": "ERR_FILE_READ", "detail": str(exc)}), 500

    @app.route("/api/files/import-hardware", methods=["POST"])
    def import_hardware_config_to_dir():
        """Copy a chosen hardware-configuration.nix into a target config directory."""
        if err := _check_csrf():
            return err
        nixos_dir, err = _require_setup()
        if err:
            return err

        body = request.get_json(silent=True) or {}
        if "target_dir" not in body:
            return jsonify({"error": "ERR_NO_DIR"}), 400
        rel_dir_raw = body.get("target_dir")
        rel_dir = rel_dir_raw.strip() if isinstance(rel_dir_raw, str) else ""
        src_path = (body.get("source_path") or "").strip()
        if not src_path:
            return jsonify({"error": "ERR_NO_PATH"}), 400

        root = Path(nixos_dir).resolve()
        target_dir = (root / rel_dir).resolve()
        try:
            target_dir.relative_to(root)
        except ValueError:
            return jsonify({"error": "ERR_SYSTEM_PATH"}), 403
        if not target_dir.exists() or not target_dir.is_dir():
            return jsonify({"error": "ERR_NOT_A_DIR"}), 400

        try:
            src = _resolve_importable_hardware_config(src_path)
        except ValueError as exc:
            code = str(exc)
            return jsonify({"error": code}), 400 if code == "ERR_INVALID_PATH" else 404

        dst = target_dir / "hardware-configuration.nix"
        backup = target_dir / "hardware-configuration.nix.bak"

        try:
            if dst.exists():
                if backup.exists():
                    backup.unlink()
                dst.replace(backup)
            shutil.copy2(src, dst)
        except PermissionError:
            return jsonify({"error": "ERR_IMPORT_PERMISSION"}), 403
        except OSError as exc:
            return jsonify({"error": "ERR_FILE_WRITE", "detail": str(exc)}), 500

        return jsonify({
            "success": True,
            "target_path": str(dst.relative_to(root)),
            "backup_created": backup.exists(),
            "source_path": str(src),
        })

    def _resolve_config_rel(root: Path, rel_path: str) -> Path:
        target = (root / rel_path).resolve()
        target.relative_to(root)
        return target

    def _validate_entry_name(name: str, *, file_name: bool = False) -> str | None:
        cleaned = (name or "").strip()
        if not cleaned or cleaned in {".", ".."}:
            return None
        if not _SAFE_NAME_RE.fullmatch(cleaned):
            return None
        if file_name and not cleaned.endswith((".nix", ".lock")):
            return None
        return cleaned

    @app.route("/api/files/create", methods=["POST"])
    def create_file_entry():
        if err := _check_csrf():
            return err
        nixos_dir, err = _require_setup()
        if err:
            return err

        body = request.get_json(silent=True) or {}
        parent_rel = (body.get("parent_path") or "").strip()
        entry_name = _validate_entry_name(body.get("name", ""), file_name=(body.get("type") == "file"))
        entry_type = (body.get("type") or "").strip()
        if entry_type not in {"file", "dir"}:
            return jsonify({"error": "ERR_INVALID_PATH"}), 400
        if not entry_name:
            return jsonify({"error": "ERR_INVALID_NAME"}), 400

        root = Path(nixos_dir).resolve()
        try:
            parent = _resolve_config_rel(root, parent_rel)
        except ValueError:
            return jsonify({"error": "ERR_SYSTEM_PATH"}), 403
        if not parent.exists() or not parent.is_dir():
            return jsonify({"error": "ERR_NOT_A_DIR"}), 400

        target = parent / entry_name
        if target.exists():
            return jsonify({"error": "ERR_FILE_EXISTS"}), 409

        try:
            if entry_type == "dir":
                target.mkdir(parents=False, exist_ok=False)
            else:
                target.write_text("", encoding="utf-8")
        except OSError as exc:
            return jsonify({"error": "ERR_FILE_WRITE", "detail": str(exc)}), 500

        return jsonify({
            "success": True,
            "path": str(target.relative_to(root)),
            "type": entry_type,
        })

    @app.route("/api/files/rename", methods=["POST"])
    def rename_file_entry():
        if err := _check_csrf():
            return err
        nixos_dir, err = _require_setup()
        if err:
            return err

        body = request.get_json(silent=True) or {}
        rel = (body.get("path") or "").strip()
        if not rel:
            return jsonify({"error": "ERR_NO_PATH"}), 400

        root = Path(nixos_dir).resolve()
        try:
            target = _resolve_config_rel(root, rel)
        except ValueError:
            return jsonify({"error": "ERR_SYSTEM_PATH"}), 403
        if not target.exists():
            return jsonify({"error": "ERR_FILE_NOT_FOUND"}), 404

        is_file = target.is_file()
        new_name = _validate_entry_name(body.get("new_name", ""), file_name=is_file)
        if not new_name:
            return jsonify({"error": "ERR_INVALID_NAME"}), 400

        dest = target.with_name(new_name)
        try:
            dest.relative_to(root)
        except ValueError:
            return jsonify({"error": "ERR_SYSTEM_PATH"}), 403
        if dest.exists():
            return jsonify({"error": "ERR_FILE_EXISTS"}), 409

        try:
            target.rename(dest)
        except OSError as exc:
            return jsonify({"error": "ERR_FILE_WRITE", "detail": str(exc)}), 500

        return jsonify({
            "success": True,
            "old_path": rel,
            "new_path": str(dest.relative_to(root)),
        })

    @app.route("/api/files/delete", methods=["POST"])
    def delete_file_entry():
        if err := _check_csrf():
            return err
        nixos_dir, err = _require_setup()
        if err:
            return err

        body = request.get_json(silent=True) or {}
        rel = (body.get("path") or "").strip()
        if not rel:
            return jsonify({"error": "ERR_NO_PATH"}), 400

        root = Path(nixos_dir).resolve()
        try:
            target = _resolve_config_rel(root, rel)
        except ValueError:
            return jsonify({"error": "ERR_SYSTEM_PATH"}), 403
        if target == root:
            return jsonify({"error": "ERR_SYSTEM_PATH"}), 403
        if not target.exists():
            return jsonify({"error": "ERR_FILE_NOT_FOUND"}), 404

        try:
            if target.is_dir():
                shutil.rmtree(target)
            else:
                target.unlink()
        except OSError as exc:
            return jsonify({"error": "ERR_FILE_WRITE", "detail": str(exc)}), 500

        return jsonify({"success": True, "deleted_path": rel})

    @app.route("/api/files/info")
    def files_info():
        """Return info (path, mtime, size) for all managed NiCo files."""
        import datetime
        nixos_dir, err = _require_setup()
        if err:
            return err

        cfg = config_manager.load_config(nixos_dir)
        root = Path(nixos_dir)

        candidates = [
            ("configuration.nix", root / "configuration.nix"),
        ]
        if cfg.get("flakes"):
            candidates.append(("flake.nix", root / "flake.nix"))
        hm_cfg = config_manager.load_config_settings(nixos_dir)
        hm_dir_name = hm_cfg.get("hm_dir", "home") or "home"
        hm_dir_path = (root / hm_dir_name).resolve()
        if _path_inside(hm_dir_path, root) and hm_dir_path.is_dir():
            for hm_file in sorted(hm_dir_path.glob("*.nix")):
                candidates.append((f"{hm_dir_name}/{hm_file.name}", hm_file))

        files = []
        for name, path in candidates:
            if path.exists():
                stat = path.stat()
                dt = datetime.datetime.fromtimestamp(stat.st_mtime)
                files.append({
                    "name": name,
                    "path": str(path),
                    "mtime": dt.strftime("%d.%m.%Y %H:%M"),
                    "size": stat.st_size,
                })
            else:
                files.append({
                    "name": name,
                    "path": str(path),
                    "mtime": None,
                    "size": None,
                })

        return jsonify({"files": files, "count": len(files)})

    @app.route("/api/parse/co")
    def parse_co_file():
        """Parst eine CO-Datei und gibt nur die darin gefundenen Felder zurück.
        Felder die nicht in der Datei stehen fehlen im Ergebnis → Frontend lässt
        diese Formularfelder leer statt sie mit nico.json-Werten zu füllen.
        """
        nixos_dir, err = _require_setup()
        if err:
            return err

        rel = request.args.get("path", "").strip()
        if not rel:
            return jsonify({"error": "ERR_NO_PATH"}), 400

        root   = Path(nixos_dir)
        target = (root / rel).resolve()
        try:
            target.relative_to(root.resolve())
        except ValueError:
            return jsonify({"error": "ERR_SYSTEM_PATH"}), 403

        if not target.exists():
            return jsonify({"error": "ERR_FILE_NOT_FOUND"}), 404

        try:
            content = target.read_text(encoding="utf-8")
        except OSError:
            return jsonify({"error": "ERR_FILE_READ"}), 500

        parsed = importer.parse_config(content)
        return jsonify(parsed)

    @app.route("/api/parse/flake", methods=["POST"])
    def parse_flake_file():
        """Parst den Inhalt einer flake.nix und gibt die gefundenen Felder zurück.
        Wird aufgerufen wenn der User flake.nix öffnet, damit das Formular mit
        den tatsächlichen Datei-Werten gefüllt wird – nicht mit nico.json-Defaults.
        """
        if err := _check_csrf(): return err
        nixos_dir, err = _require_setup()
        if err:
            return err

        body    = request.get_json(silent=True) or {}
        content = body.get("content", "")
        if not content:
            return jsonify({"error": "ERR_NO_CONTENT"}), 400

        parsed = importer.parse_flake_nix(content)
        return jsonify(parsed)

    @app.route("/api/file")
    def read_file_by_query():
        """Read a .nix or .lock file from the nixos config dir.
        Also detects/stamps the nico file-type code on first open (only for
        same-origin requests carrying the CSRF token).
        Returns: { content, path, file_type, writable }
          file_type: 'co'|'nd'|'fl'|'hw'|'mo'|'hm' or null (no nico-version)
        """
        nixos_dir, err = _require_setup()
        if err:
            return err

        rel = request.args.get("path", "").strip()
        try:
            result = _read_config_file(nixos_dir, rel,
                                       persist_stamp=_check_csrf() is None)
        except ValueError as exc:
            if str(exc) == "ERR_FILE_TYPE":
                return jsonify({"error": "ERR_NOT_NIX"}), 400
            if str(exc).startswith("ERR_"):
                return jsonify({"error": str(exc)}), 400
            return jsonify({"error": "ERR_SYSTEM_PATH"}), 403
        except FileNotFoundError:
            return jsonify({"error": "ERR_FILE_NOT_FOUND"}), 404
        except OSError:
            return jsonify({"error": "ERR_FILE_READ"}), 500
        return jsonify(result)


    @app.route("/api/file", methods=["POST"])
    def write_file():
        """Write content to a .nix file. Saves ALL open files at once."""
        if err := _check_csrf(): return err
        nixos_dir, err = _require_setup()
        if err:
            return err

        body = request.get_json(silent=True) or {}
        files = body.get("files", [])  # list of {path, content}

        if not files:
            return jsonify({"error": "ERR_NO_FILES"}), 400

        root = Path(nixos_dir).resolve()
        written = []

        for f in files:
            rel  = (f.get("path") or "").strip()
            content = f.get("content", "")
            if not rel:
                continue
            target = (root / rel).resolve()
            try:
                target.relative_to(root)
            except ValueError:
                return jsonify({"error": "ERR_SYSTEM_PATH"}), 403
            if target.suffix != '.nix':
                return jsonify({"error": "ERR_NOT_NIX"}), 400
            try:
                # Refuse to overwrite NiCo-managed files unless explicitly in raw mode
                if target.name in ('configuration.nix', 'flake.nix'):
                    try:
                        existing = target.read_text(encoding='utf-8')
                    except OSError:
                        existing = ''
                    if _get_panel_mode(existing) != 'r':
                        return jsonify({"error": "ERR_MANAGED_FILE"}), 400

                _cfg_s   = config_manager.load_config_settings(nixos_dir)
                _hm_dir  = _cfg_s.get("hm_dir", "home").strip() or "home"
                _rel_parts = Path(rel).parts
                is_hm_path = len(_rel_parts) >= 2 and _rel_parts[0] == _hm_dir
                if is_hm_path or _classify_filename(target.name) == 'hm':
                    content, _ = _normalize_hm_content(content)
                # Recompute nico-version hash for NiCo-managed files saved in raw mode
                ftype = _get_nico_type(content)
                if ftype:
                    content = _set_type_in_content(content, ftype)
                target.write_text(content, encoding='utf-8')
                written.append(rel)
            except OSError:
                return jsonify({"error": "ERR_FILE_WRITE"}), 500

        if written:
            git_manager.auto_commit(nixos_dir)

        return jsonify({"success": True, "written": written})

    @app.route("/api/file/set-type", methods=["POST"])
    def set_file_type():
        """Set the nico file-type code in a file's nico-version header."""
        try:
            if err := _check_csrf(): return err
            nixos_dir, err = _require_setup()
            if err:
                return err

            body  = request.get_json(silent=True) or {}
            rel   = (body.get("path") or "").strip()
            ftype = (body.get("file_type") or "").strip()

            if not rel:
                return jsonify({"error": "ERR_NO_PATH"}), 400
            if ftype not in _VALID_FILE_TYPES:
                return jsonify({"error": "ERR_INVALID_TYPE"}), 400

            root   = Path(nixos_dir).resolve()
            target = (root / rel).resolve()
            try:
                target.relative_to(root)
            except ValueError:
                return jsonify({"error": "ERR_SYSTEM_PATH"}), 403

            if target.suffix not in (".nix", ".lock"):
                return jsonify({"error": "ERR_NOT_NIX"}), 400

            # .lock-Dateien sind JSON – können keine Nix-Kommentare enthalten
            if target.suffix == ".lock":
                return jsonify({"ok": True, "file_type": _FILENAME_TYPE_HINTS.get(target.name) or ftype})

            try:
                content = target.read_text(encoding="utf-8")
            except OSError:
                return jsonify({"error": "ERR_FILE_READ"}), 500

            new_content = _set_type_in_content(content, ftype)

            try:
                target.write_text(new_content, encoding="utf-8")
            except OSError:
                # Schreibgeschützte Datei – Typ aus Dateinamen zurückgeben falls vorhanden
                hint = _FILENAME_TYPE_HINTS.get(target.name)
                if hint:
                    return jsonify({"ok": True, "file_type": hint})
                return jsonify({"error": "ERR_FILE_WRITE"}), 500

            git_manager.auto_commit(nixos_dir)
            return jsonify({"ok": True, "file_type": ftype})
        except Exception as exc:
            return jsonify({"error": f"ERR_INTERNAL: {exc}"}), 500

    @app.route("/api/file/panel-mode", methods=["POST"])
    def set_panel_mode_route():
        """Set the panel mode flag (#p or #r) in a file's nico-version header.
        Optionally saves new file content at the same time (when switching modes
        with unsaved changes). Checks brix integrity when switching to panel mode.
        """
        try:
            if err := _check_csrf(): return err
            nixos_dir, err = _require_setup()
            if err:
                return err

            body    = request.get_json(silent=True) or {}
            rel     = (body.get("path")    or "").strip()
            mode    = (body.get("mode")    or "").strip()
            content = body.get("content")  # optional – send when switching with unsaved edits

            if not rel:
                return jsonify({"error": "ERR_NO_PATH"}), 400
            if mode not in ("p", "r"):
                return jsonify({"error": "ERR_INVALID_MODE"}), 400

            root   = Path(nixos_dir).resolve()
            target = (root / rel).resolve()
            try:
                target.relative_to(root)
            except ValueError:
                return jsonify({"error": "ERR_SYSTEM_PATH"}), 403

            if target.suffix != '.nix':
                return jsonify({"error": "ERR_NOT_NIX"}), 400

            try:
                current = target.read_text(encoding="utf-8")
            except OSError:
                return jsonify({"error": "ERR_FILE_READ"}), 500

            # Use provided content if supplied (unsaved raw edits), else existing file
            base = content if content is not None else current

            # When switching to panel mode, verify brix markers are intact
            if mode == 'p' and not _check_brix_integrity(base):
                return jsonify({"error": "ERR_BRIX_INCOMPLETE"}), 400

            updated = _set_panel_mode(base, mode)

            # Recompute hash so change-detection stays consistent
            ftype = _get_nico_type(updated)
            if ftype:
                updated = _set_type_in_content(updated, ftype)

            try:
                target.write_text(updated, encoding="utf-8")
            except OSError:
                return jsonify({"error": "ERR_FILE_WRITE"}), 500

            git_manager.auto_commit(nixos_dir)
            return jsonify({"ok": True, "mode": mode})
        except Exception as exc:
            return jsonify({"error": f"ERR_INTERNAL: {exc}"}), 500

