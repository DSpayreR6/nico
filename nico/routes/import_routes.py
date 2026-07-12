"""ZIP import, categorize and /etc/nixos import routes.

Split from server.py. Route bodies are unchanged; shared request helpers
from create_app() are passed in via the ctx dict.
"""

from pathlib import Path

from flask import jsonify, request

from .. import config_manager, git_manager, importer
from ..brix import format_brick, next_order_for_section
from ..core import (
    classify_filename as _classify_filename,
    get_nico_type as _get_nico_type,
    set_type_in_content as _set_type_in_content,
)


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
        found = None
        for candidate in ("configuration.nix", "flake.nix"):
            if candidate in top_level:
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

        # Backup-Bestätigung
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
            git_manager.auto_commit(nixos_dir, label="NiCo: Sicherung vor ZIP-Import")
        except Exception:
            pass

        dst = Path(nixos_dir)

        # Bestehende .nix/.lock entfernen (nur sichtbare Pfade – nie .git & Co.)
        for existing in dst.rglob("*"):
            if (existing.is_file() and existing.suffix in (".nix", ".lock")
                    and not any(p.startswith('.') for p in existing.relative_to(dst).parts)):
                try:
                    existing.unlink()
                except OSError:
                    pass

        # ZIP extrahieren
        files_copied = []
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
            for member in zf.infolist():
                if member.is_dir():
                    continue
                name = member.filename
                dest = _safe_import_dest(dst, name)
                if dest is None:  # zip-slip: entry would land outside config dir
                    continue
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(zf.read(member))
                files_copied.append(name)

        # configuration.nix parsen
        cfg_path = dst / "configuration.nix"
        recognized: dict = {}
        rest_brix = ""
        if cfg_path.is_file():
            try:
                content   = cfg_path.read_text(encoding="utf-8", errors="replace")
                recognized = importer.parse_config(content)
                rest_brix  = importer.build_rest_brix(content, recognized)
            except OSError:
                pass

        data = _apply_import_result(nixos_dir, recognized, rest_brix)
        _reimport_flake(nixos_dir, data)

        return jsonify({
            "success":      True,
            "recognized":   recognized,
            "has_brix":     bool(rest_brix.strip()),
            "files_copied": files_copied,
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

        for nix_file in sorted(root.rglob("*.nix")):
            rel = nix_file.relative_to(root)
            # Skip hidden directories (e.g. .git)
            if any(part.startswith('.') for part in rel.parts[:-1]):
                continue
            if nix_file.suffix != ".nix":
                continue

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
                # flake.nix und filename-basierte home.nix: nur stempeln wenn bereits
                # von NiCo verwaltet (nico-version Header). Externe Dateien werden nicht
                # angefasst – sonst würde write_files sie als NiCo-managed betrachten.
                # Pfad-basierte HM-Dateien (home/<hm_dir>/*.nix) werden immer gestempelt.
                if not is_path_hm and ftype in ("fl", "hm") and _get_nico_type(content) is None:
                    done.append({"file": str(rel), "type": ftype, "changed": False})
                    continue
                new_content = _set_type_in_content(content, ftype)
                changed = new_content != content
                if changed:
                    nix_file.write_text(new_content, encoding="utf-8")
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

        git_manager.auto_commit(nixos_dir, label="NiCo: Sicherung vor Import")

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

            # Clear existing .nix/.lock files for a clean 1:1 copy
            # (visible paths only – never touch .git/.direnv & Co.)
            dst = Path(nixos_dir)
            for existing in dst.rglob("*"):
                if (existing.is_file() and existing.suffix in (".nix", ".lock")
                        and not any(p.startswith('.') for p in existing.relative_to(dst).parts)):
                    existing.unlink()

            files_copied = []
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
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_text(fcont, encoding="utf-8")
                files_copied.append(rel)

        elif config_path:
            # ── Path mode: server reads directory recursively ─────────────────
            cfg_file = Path(config_path).expanduser()
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

            files_copied = []
            try:
                files_copied = importer.copy_nix_tree(cfg_file.parent, nixos_dir)
            except PermissionError:
                return jsonify({"error": "ERR_IMPORT_PERMISSION"}), 403
            except OSError:
                return jsonify({"error": "ERR_FILE_READ"}), 500

        else:
            return jsonify({"error": "ERR_NO_PATH"}), 400

        # ── Common: merge into nico.json ─────────────────────────────────────
        data = _apply_import_result(nixos_dir, recognized, rest_brix)

        # Flake.nix: parse → panel-Felder + Brix, dann sauber regenerieren
        if any(Path(f).name == "flake.nix" for f in files_copied):
            _reimport_flake(nixos_dir, data)

        return jsonify({
            "success":      True,
            "recognized":   recognized,
            "has_brix":     bool(rest_brix.strip()),
            "files_copied": files_copied,
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

    @app.route("/api/import/preview", methods=["POST"])
    def import_preview():
        """
        Parse /etc/nixos/configuration.nix and return what NiCo would extract.
        Nothing is written to disk.
        """
        if err := _check_csrf(): return err
        src = importer.ETC_NIXOS / "configuration.nix"
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
            git_manager.auto_commit(nixos_dir, label="NiCo: Sicherung vor Import")
        except Exception:
            pass

        src = importer.ETC_NIXOS / "configuration.nix"
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

        # Copy all .nix/.lock files from /etc/nixos into the config dir
        files_copied = []
        try:
            files_copied = importer.copy_nix_tree(importer.ETC_NIXOS, nixos_dir)
        except Exception:
            pass  # source might not exist or be readable

        _apply_import_result(nixos_dir, recognized, rest_brix)

        return jsonify({
            "success":      True,
            "recognized":   list(recognized.keys()),
            "has_brix":     bool(rest_brix.strip()),
            "files_copied": files_copied,
        })

    @app.route("/api/import/from-path", methods=["POST"])
    def import_from_path():
        """
        Liest eine CO-Datei aus dem nixos-Verzeichnis, parst bekannte Felder
        und speichert den Rest als Brix.  Wird aufgerufen wenn der User einer
        Datei erstmals den Typ CO zuweist.
        """
        if err := _check_csrf(): return err
        nixos_dir, err = _require_setup()
        if err:
            return err

        body = request.get_json(silent=True) or {}
        rel  = (body.get("path") or "").strip()
        if not rel:
            return jsonify({"error": "ERR_NO_PATH"}), 400

        root   = Path(nixos_dir).resolve()
        target = (root / rel).resolve()
        try:
            target.relative_to(root)
        except ValueError:
            return jsonify({"error": "ERR_SYSTEM_PATH"}), 403

        try:
            content = target.read_text(encoding="utf-8")
        except OSError:
            return jsonify({"error": "ERR_FILE_READ"}), 500

        recognized = importer.parse_config(content)
        rest_brix  = importer.build_rest_brix(content, recognized)

        git_manager.auto_commit(nixos_dir, label="NiCo: Sicherung vor Import")

        data = config_manager.load_config(nixos_dir) or {}
        data.update(recognized)            # Datei gewinnt für erkannte Felder
        if rest_brix.strip():
            blocks     = data.get("brick_blocks", {})
            brick_name = f"imported-{target.stem}"
            order      = next_order_for_section(blocks, "Start")
            blocks[brick_name] = {
                "section": "Start",
                "order":   order,
                "text":    format_brick("Start", order, brick_name, rest_brix.strip()),
            }
            data["brick_blocks"] = blocks
        config_manager.save_config(nixos_dir, data)

        return jsonify({
            "ok":        True,
            "recognized": list(recognized.keys()),
            "brix_added": bool(rest_brix.strip()),
        })

