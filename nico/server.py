"""
Flask application factory and core routes (index, setup, status, themes).
Domain routes live in nico/routes/ and are registered via routes.register_all;
all state lives in config_manager – this package only handles HTTP concerns.
"""

import os
import re
import secrets
import threading
import tomllib
import urllib.error
import urllib.request
from pathlib import Path

from flask import Flask, jsonify, render_template, request

from . import config_manager, generator, git_manager, importer, routes
from .brix import brix_content_to_bricks
from .core import validate_user_path as _validate_user_path


_THEMES_DIR = Path(__file__).parent / "static" / "themes"
_DEFAULT_THEME = "catppuccin-mocha"
_NIXOS_CHANNELS_URL = "https://nix-channels.s3.amazonaws.com?delimiter=/"
_FALLBACK_NIXOS_CHANNELS = [
    "nixos-unstable",
    "nixos-26.05",
    "nixos-25.11",
    "nixos-25.05",
    "nixos-24.11",
]
_NIXOS_CHANNEL_RE = re.compile(r"\bnixos-(?:unstable|\d{2}\.\d{2})\b")


def _sort_nixos_channels(channels: set[str], stable_limit: int = 4) -> list[str]:
    stable = sorted(
        (c for c in channels if re.fullmatch(r"nixos-\d{2}\.\d{2}", c)),
        key=lambda c: tuple(int(part) for part in c.removeprefix("nixos-").split(".")),
        reverse=True,
    )
    result = ["nixos-unstable"]
    result.extend(stable[:stable_limit])
    return result


def _fetch_nixos_channels() -> tuple[list[str], str]:
    try:
        req = urllib.request.Request(
            _NIXOS_CHANNELS_URL,
            headers={"User-Agent": "NiCo channel updater"},
        )
        with urllib.request.urlopen(req, timeout=4) as resp:
            html = resp.read(200_000).decode("utf-8", errors="replace")
        channels = set(_NIXOS_CHANNEL_RE.findall(html))
        if "nixos-unstable" not in channels:
            channels.add("nixos-unstable")
        result = _sort_nixos_channels(channels)
        if len(result) > 1:
            return result, "remote"
    except (OSError, urllib.error.URLError, TimeoutError, UnicodeError):
        pass
    return list(_FALLBACK_NIXOS_CHANNELS), "fallback"

def _load_theme_css(theme_name: str) -> str:
    if not theme_name or "/" in theme_name or ".." in theme_name:
        theme_name = _DEFAULT_THEME
    toml_path = _THEMES_DIR / theme_name / "theme.toml"
    if not toml_path.exists():
        toml_path = _THEMES_DIR / _DEFAULT_THEME / "theme.toml"
    try:
        with open(toml_path, "rb") as f:
            data = tomllib.load(f)
        css_vars = "\n".join(
            f"  --{k}: {v};" for k, v in data.get("vars", {}).items()
        )
        return f":root {{\n{css_vars}\n}}"
    except Exception:
        return ""


def create_app() -> Flask:
    app = Flask(__name__)
    # Preserve dict insertion order in JSON responses (needed for brick_blocks ordering)
    app.json.sort_keys = False

    # One CSRF token per server process – regenerated on each start
    _csrf_token = secrets.token_hex(32)

    # DNS-rebinding guard: NiCo binds to 127.0.0.1, but a hostile domain can
    # resolve to 127.0.0.1 and read API responses from a browser. Reject any
    # request whose Host header is not a local one.
    _ALLOWED_HOST_NAMES = {"127.0.0.1", "localhost", "[::1]", "::1"}

    @app.before_request
    def _check_host_header():
        host = request.host or ""
        if host.startswith("["):                      # IPv6: [::1]:5000
            hostname = host.split("]", 1)[0] + "]"
        else:
            hostname = host.rsplit(":", 1)[0]
        if hostname not in _ALLOWED_HOST_NAMES:
            return jsonify({"error": "ERR_HOST_HEADER"}), 403

    # Sudo-Passwort-Nonces: nonce → (password, expiry_timestamp)
    # POST /api/sudo/acquire speichert, rebuild_stream/symlink lesen einmalig.
    import time as _time_mod
    _sudo_nonces: dict[str, tuple[str, float]] = {}

    # ------------------------------------------------------------------ helpers

    def _nixos_dir() -> str | None:
        return config_manager.get_nixos_config_dir()

    def _require_setup():
        """Return (nixos_dir, None) or (None, error_response)."""
        d = _nixos_dir()
        if not d:
            return None, (jsonify({"error": "ERR_NOT_SETUP"}), 400)
        return d, None

    def _check_csrf():
        """Return None if the CSRF token is valid, or an error response tuple."""
        token = request.headers.get('X-CSRF-Token', '')
        if not secrets.compare_digest(token, _csrf_token):
            return jsonify({"error": "ERR_CSRF"}), 403
        return None

    def _hosts_dir_name(nixos_dir: str) -> str:
        """Configured hosts directory name (config.json hosts_dir, default 'hosts')."""
        cfg = config_manager.load_config_settings(nixos_dir)
        return (cfg.get("hosts_dir") or "hosts").strip() or "hosts"

    def _path_inside(child: Path, root: Path) -> bool:
        """True when child (resolved) lies inside root (resolved)."""
        try:
            child.resolve().relative_to(root.resolve())
            return True
        except ValueError:
            return False

    def _apply_import_result(nixos_dir: str, recognized: dict, rest_brix: str) -> dict:
        """Merge parsed import data into nico.json: recognized fields, rest
        content as bricks, hardware_config flag. Saves and returns data."""
        data = config_manager.load_config(nixos_dir) or {}
        for key, val in recognized.items():
            data[key] = val
        if rest_brix.strip():
            blocks = data.get("brick_blocks", {})
            rest_blocks = brix_content_to_bricks(rest_brix, section="Start", existing_blocks=blocks)
            blocks.update(rest_blocks)
            data["brick_blocks"] = blocks
        if (Path(nixos_dir) / "hardware-configuration.nix").exists():
            data["hardware_config"] = True
        config_manager.save_config(nixos_dir, data)
        return data

    def _reimport_flake(nixos_dir: str, data: dict) -> None:
        """Parse an imported flake.nix, merge its fields, regenerate it cleanly."""
        flake_path = Path(nixos_dir) / "flake.nix"
        if not flake_path.exists():
            return
        data["flakes"] = True
        try:
            flake_content = flake_path.read_text(encoding="utf-8")
            flake_fields  = importer.parse_flake_nix(flake_content)
            flake_brix    = importer.build_flake_brix(flake_content)
            flake_brix    = importer.ensure_flake_host_bricks(flake_content, flake_brix)
            data.update(flake_fields)
            config_manager.save_config(nixos_dir, data)
            data["flake_brick_blocks"] = flake_brix
            flake_path.write_text(
                generator.generate_flake_nix(data, nixos_dir=nixos_dir),
                encoding="utf-8",
            )
        except OSError:
            pass

    def _safe_import_dest(root: Path, rel_name: str) -> "Path | None":
        """Resolve rel_name below root; None when it would escape (zip-slip)
        or contains hidden path segments (.git, .direnv, …)."""
        if not rel_name or Path(rel_name).is_absolute():
            return None
        if any(part.startswith('.') for part in Path(rel_name).parts):
            return None
        dest = (root / rel_name).resolve()
        try:
            dest.relative_to(root.resolve())
        except ValueError:
            return None
        return dest

    # ------------------------------------------------------------------ routes

    # Cache-buster: changes every server restart so browsers always reload static files
    _static_v = secrets.token_hex(6)

    @app.route("/")
    def index():
        from flask import make_response
        _theme = config_manager.get_app_settings().get("theme", _DEFAULT_THEME)
        r = make_response(render_template(
            "index.html",
            csrf_token=_csrf_token,
            home_dir=str(Path.home()),
            static_v=_static_v,
            theme_style=_load_theme_css(_theme),
        ))
        r.headers["Cache-Control"] = "no-store"
        return r

    @app.route("/api/themes")
    def available_themes():
        """Return list of available themes (scanned from static/themes/)."""
        themes = []
        for toml_path in sorted(_THEMES_DIR.glob("*/theme.toml")):
            try:
                with open(toml_path, "rb") as f:
                    data = tomllib.load(f)
                themes.append({"id": toml_path.parent.name, "name": data.get("name", toml_path.parent.name)})
            except Exception:
                pass
        return jsonify(themes)

    @app.route("/api/langs")
    def available_langs():
        """Return sorted list of available language codes (scanned from static/lang/)."""
        lang_dir = Path(__file__).parent / "static" / "lang"
        codes = sorted(p.stem for p in lang_dir.glob("*.json"))
        return jsonify(codes)

    @app.route("/api/nixos/channels")
    def nixos_channels():
        """Return current NixOS flake channels with a local fallback."""
        channels, source = _fetch_nixos_channels()
        return jsonify({"channels": channels, "source": source})

    @app.route("/api/status")
    def status():
        nixos_dir = _nixos_dir()
        if nixos_dir and Path(nixos_dir).is_dir():
            p = Path(nixos_dir)
            has_config = (p / "configuration.nix").exists() or (p / "flake.nix").exists()
            app_s = config_manager.get_app_settings()
            return jsonify({
                "setup_complete":    True,
                "needs_import":      not has_config,
                "nixos_config_dir":  nixos_dir,
                "git_sync":          app_s.get("git_sync", True),
                "git_status_only":   app_s.get("git_status_only", False),
            })
        return jsonify({"setup_complete": False})

    @app.route("/api/settings/dir")
    def settings_dir():
        """Return the current NixOS config directory."""
        return jsonify({"dir": _nixos_dir() or ""})

    @app.route("/api/setup", methods=["POST"])
    def setup():
        if err := _check_csrf(): return err

        data = request.get_json(silent=True) or {}
        raw = data.get("nixos_config_dir", "").strip()
        create_if_missing = bool(data.get("create_if_missing", False))

        path, path_err = _validate_user_path(raw)
        if path_err:
            return jsonify({"error": path_err}), 400

        dir_created = False
        if not path.exists():
            if not create_if_missing:
                return jsonify({"needs_confirmation": True, "path": str(path)})
            try:
                path.mkdir(parents=True)
                dir_created = True
            except OSError:
                return jsonify({"error": "ERR_DIR_CREATE"}), 400
        elif not path.is_dir():
            return jsonify({"error": "ERR_NOT_A_DIR"}), 400

        config_manager.init_nico_dir(str(path))
        config_manager.migrate_nico_json(str(path))  # Migrate old nico.json → config.json
        config_manager.save_app_settings({"nixos_config_dir": str(path)})

        # Auto-copy hardware-configuration.nix from /etc/nixos/ if available
        hw_copied = False
        hw_src    = Path("/etc/nixos/hardware-configuration.nix")
        hw_dst    = path / "hardware-configuration.nix"
        # Copy only if source exists and destination doesn't (never overwrite)
        if hw_src.is_file() and not hw_dst.exists():
            try:
                import shutil as _shutil
                _shutil.copy2(hw_src, hw_dst)
                hw_copied = True
            except OSError:
                pass  # permission denied or other OS error – silently skip

        # Ensure git repo exists – init if missing (best-effort, silently skip if git unavailable)
        try:
            if not git_manager.is_git_repo(str(path)):
                git_manager.init_repo(str(path))
        except Exception:
            pass

        return jsonify({
            "success":          True,
            "nixos_config_dir": str(path),
            "hw_copied":        hw_copied,
            "hw_present":       hw_dst.exists(),
            "dir_created":      dir_created,
        })

    @app.route("/api/fs/ls")
    def fs_ls():
        """Return subdirectories of a given path for the directory browser."""
        raw = request.args.get("path", str(Path.home()))
        try:
            p = Path(raw).expanduser().resolve()
        except Exception:
            return jsonify({"error": "ERR_INVALID_PATH"}), 400
        if not p.is_dir():
            # Try parent so the browser can still navigate
            p = p.parent
        try:
            dirs = sorted(
                entry.name for entry in p.iterdir() if entry.is_dir()
            )
        except PermissionError:
            dirs = []
        parent = str(p.parent) if p != p.parent else None
        return jsonify({"path": str(p), "parent": parent, "dirs": dirs})

    # ------------------------------------------------------------------- help

    @app.route("/help")
    def help_page():
        return render_template("help.html")

    # ----------------------------------------------------- domain route modules

    routes.register_all(app, {
        "check_csrf":          _check_csrf,
        "require_setup":       _require_setup,
        "nixos_dir":           _nixos_dir,
        "hosts_dir_name":      _hosts_dir_name,
        "path_inside":         _path_inside,
        "apply_import_result": _apply_import_result,
        "reimport_flake":      _reimport_flake,
        "safe_import_dest":    _safe_import_dest,
        "sudo_nonces":         _sudo_nonces,
        "time_mod":            _time_mod,
        "csrf_token":          _csrf_token,
    })

    @app.route("/api/shutdown", methods=["POST"])
    def shutdown():
        if err := _check_csrf(): return err
        # Delay exit slightly so the HTTP response can be sent back first
        threading.Thread(target=lambda: (threading.Event().wait(0.3), os._exit(0)), daemon=True).start()
        return jsonify({"ok": True})

    return app
