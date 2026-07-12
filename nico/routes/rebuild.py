"""Rebuild, dry-run and sudo nonce routes.

Split from server.py. Route bodies are unchanged; shared request helpers
from create_app() are passed in via the ctx dict.
"""

import os
import re
import secrets
from pathlib import Path

from flask import Response, jsonify, request, stream_with_context

from .. import config_manager, git_manager
from ..core import (
    clean_nix_error as _clean_nix_error,
    get_flake_hosts as _get_flake_hosts,
)


def register(app, ctx):
    _check_csrf    = ctx["check_csrf"]
    _require_setup = ctx["require_setup"]
    _sudo_nonces   = ctx["sudo_nonces"]
    _time_mod      = ctx["time_mod"]
    _csrf_token    = ctx["csrf_token"]

    @app.route("/api/rebuild/default-host")
    def rebuild_default_host():
        """Return the default host for rebuild (hostname match, saved setting, or null)."""
        nixos_dir, err = _require_setup()
        if err:
            return err
        data = config_manager.load_config(nixos_dir) or {}
        if not data.get("flakes"):
            return jsonify({"default_host": None})

        hosts = _get_flake_hosts(nixos_dir, data)

        # Machine identity always wins: a stored default may have been synced
        # or migrated from another machine and must never override the host
        # NiCo is actually running on.
        import socket as _socket
        machine = _socket.gethostname()
        if machine in hosts:
            return jsonify({"default_host": machine})

        # Fallback for machines whose hostname matches no flake host.
        app_settings = config_manager.get_app_settings()
        saved = (app_settings.get("default_host") or "").strip()
        if saved and saved in hosts:
            return jsonify({"default_host": saved})
        if saved and saved not in hosts:
            config_manager.save_app_settings({"default_host": ""})

        return jsonify({"default_host": None})

    @app.route("/api/rebuild/open-terminal", methods=["POST"])
    def rebuild_open_terminal():
        """Build nixos-rebuild command and launch it in a terminal emulator."""
        if err := _check_csrf(): return err
        nixos_dir, err = _require_setup()
        if err:
            return err

        import shlex as _shlex
        import stat as _stat
        import subprocess as _sp
        import tempfile as _tempfile
        import shutil as _shutil2

        body = request.get_json(silent=True) or {}
        data = config_manager.load_config(nixos_dir) or {}
        use_flake = data.get("flakes", False)

        hostname          = (body.get("hostname") or "").strip()
        mode              = (body.get("mode") or "switch").strip()
        update_flake      = bool(body.get("update_flake", False)) and use_flake
        push_shutdown     = bool(body.get("push_shutdown_after", False))
        shutdown_after    = bool(body.get("shutdown_after", False)) and not push_shutdown
        safe_mode         = bool(body.get("safe_mode", False))

        if mode not in ('switch', 'boot', 'test'):
            return jsonify({"error": "ERR_INVALID_MODE"}), 400

        nixos_path = Path(nixos_dir).resolve()

        stage_before_rebuild = False
        if use_flake:
            if git_manager.is_git_repo(nixos_dir):
                flake_arg = f".#{hostname}"
                # Same staging as the SSE rebuild: flake eval in a git repo
                # only sees tracked files.
                stage_before_rebuild = True
            else:
                flake_arg = f"path:{nixos_path.as_posix()}#{hostname}"
            rebuild_cmd = f"sudo nixos-rebuild {mode} --flake {_shlex.quote(flake_arg)}"
        else:
            conf_path = nixos_path / "configuration.nix"
            rebuild_cmd = f"sudo nixos-rebuild {mode} -I nixos-config={_shlex.quote(str(conf_path))}"

        if safe_mode:
            rebuild_cmd += " --max-jobs 1 --cores 4"

        script_lines = [
            "#!/usr/bin/env bash",
            f"cd {_shlex.quote(str(nixos_path))}",
        ]
        if stage_before_rebuild:
            script_lines.append("git add -A")
        if shutdown_after or push_shutdown:
            script_lines.append("_rebuild_ok=0")
            if update_flake:
                script_lines += [
                    "if nix flake update; then",
                    f"    {rebuild_cmd} && _rebuild_ok=1",
                    "else",
                    "    echo 'nix flake update fehlgeschlagen'",
                    "fi",
                ]
            else:
                script_lines.append(f"{rebuild_cmd} && _rebuild_ok=1")
            if push_shutdown:
                script_lines += [
                    'if [ "$_rebuild_ok" -eq 1 ]; then',
                    '    git push',
                    '    sudo shutdown -h now',
                    'else',
                    '    echo',
                    '    read -rp "Rebuild fehlgeschlagen. Drücke Enter zum Schließen..."',
                    'fi',
                ]
            else:
                script_lines += [
                    'if [ "$_rebuild_ok" -eq 1 ]; then',
                    '    sudo shutdown -h now',
                    'else',
                    '    echo',
                    '    read -rp "Drücke Enter zum Schließen..."',
                    'fi',
                ]
        else:
            if update_flake:
                script_lines.append("nix flake update")
                script_lines.append(f"[ $? -eq 0 ] && {rebuild_cmd} || echo 'nix flake update fehlgeschlagen'")
            else:
                script_lines.append(rebuild_cmd)
            script_lines += [
                'echo',
                'read -rp "Drücke Enter zum Schließen..."',
            ]
        script_lines.append('rm -f "$0"')

        fd, script_path = _tempfile.mkstemp(suffix=".sh", prefix="nico-rebuild-")
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as f:
                f.write('\n'.join(script_lines) + '\n')
            os.chmod(script_path, _stat.S_IRWXU)
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

        TERMINALS = [
            ['konsole', '-e', 'bash', script_path],
            ['xterm', '-e', 'bash', script_path],
            ['alacritty', '-e', 'bash', script_path],
            ['kitty', 'bash', script_path],
            ['gnome-terminal', '--', 'bash', script_path],
            ['xfce4-terminal', '-e', f'bash {script_path}'],
        ]
        for term_cmd in TERMINALS:
            if _shutil2.which(term_cmd[0]):
                try:
                    _sp.Popen(term_cmd)
                    return jsonify({"success": True})
                except Exception:
                    continue

        try:
            os.unlink(script_path)
        except OSError:
            pass
        return jsonify({"error": "ERR_NO_TERMINAL"}), 500

    @app.route("/api/rebuild/stream")
    def rebuild_stream():
        """
        SSE endpoint: runs nixos-rebuild and streams output line-by-line.

        Uses a query-param token for CSRF because EventSource (browser API)
        only supports GET and cannot add custom headers.

        Query params:
          token  – CSRF token (required)
          mode   – "switch" | "boot" | "test"  (default: "switch")

        SSE event types:
          {"type": "output",   "line": "..."}
          {"type": "phase",    "phase": "evaluating|fetching|building|activating", "active": true|false, "pkg": "..."}
          {"type": "progress", "done": N, "total": M, "pkg": "..."}
          {"type": "done",     "success": true|false, "exit_code": N}
          {"type": "error",    "message": "..."}

        Architecture note: mode is already a parameter so switch/boot/test
        can be added to the UI later without touching this endpoint.
        """
        import json as _json
        import subprocess as _sp
        import re as _re

        # CSRF via query param (EventSource cannot set headers)
        token = request.args.get('token', '')
        if not secrets.compare_digest(token, _csrf_token):
            return jsonify({"error": "ERR_CSRF"}), 403

        mode = request.args.get('mode', 'switch')
        if mode not in ('switch', 'boot', 'test'):
            return jsonify({"error": "ERR_INVALID_MODE"}), 400

        nixos_dir, err = _require_setup()
        if err:
            return err

        data      = config_manager.load_config(nixos_dir) or {}
        conf_path = Path(nixos_dir) / "configuration.nix"
        use_flake = data.get("flakes", False)

        # hostname: query-param takes precedence; fall back to nico.json
        hostname_param = request.args.get('hostname', '').strip()
        if hostname_param and re.fullmatch(r'[\w.-]+', hostname_param):
            hostname = hostname_param
        else:
            hostname = (data.get("hostname") or "nixos").strip() or "nixos"

        # Sudo-Passwort via Nonce holen
        sudo_nonce = request.args.get('sudo_nonce', '')
        sudo_password = ''
        if sudo_nonce and sudo_nonce in _sudo_nonces:
            pw, expiry = _sudo_nonces.pop(sudo_nonce)
            if expiry > _time_mod.time():
                sudo_password = pw

        update_flake = request.args.get('update_flake', '0') == '1' and use_flake
        safe_mode    = request.args.get('safe_mode', '0') == '1'

        if use_flake:
            # Without git nix would try to copy via the git index and fail.
            # Use path: prefix so nix reads directly from the filesystem.
            if git_manager.is_git_repo(nixos_dir):
                flake_arg = f".#{hostname}"
            else:
                flake_arg = f"path:{Path(nixos_dir).resolve().as_posix()}#{hostname}"
            cmd = ["sudo", "-S", "nixos-rebuild", mode, "--flake", flake_arg,
                   "--log-format", "internal-json", "-v"]
        else:
            cmd = ["sudo", "-S", "nixos-rebuild", mode, "-I", f"nixos-config={conf_path}",
                   "--log-format", "internal-json", "-v"]

        if safe_mode:
            cmd += ["--max-jobs", "1", "--cores", "4"]

        app_settings        = config_manager.get_app_settings()
        rebuild_log_on      = bool(app_settings.get("rebuild_log", False))
        prefetch_dry_run_on = bool(app_settings.get("prefetch_dry_run", True))
        log_path       = Path(nixos_dir) / "nixos-rebuild.log"

        def _generate():
            # Tracks active nix activities: {id: {'phase', 'pkg', 'nix_type', 'dl_done', 'dl_expected'}}
            active_acts = {}
            # Aggregated download bytes across all substitute activities
            dl_state    = {'done': 0, 'expected': 0}
            # Build derivation counter; 'aggregate' switches to Builds-activity tracking
            build_state = {'done': 0, 'total': 0, 'aggregate': False}
            # Global CLI-style progress, preferred when present
            global_state = {
                'available': False,
                'built_done': 0,
                'built_total': 0,
                'copied_done': 0,
                'copied_total': 0,
                'copied_label': '',
                'copied_expected': 0,
                'dl_done': 0,
                'dl_expected': 0,
            }
            # Buffer for log output
            log_lines   = []

            # nix activity types
            ACT_BUILD         = 105  # individual derivation build
            ACT_SUBSTITUTE    = 108  # substituting path from binary cache (phase detection only)
            ACT_FILE_TRANSFER = 101  # HTTP file transfer (compressed download bytes)
            ACT_BUILDS        = 104  # aggregate builds tracker – resProgress gives total/done

            def _pkg_from_text(text):
                m = _re.search(r'/nix/store/[a-z0-9]+-([^/\s\'.]+)', text)
                return m.group(1) if m else ''

            def _emit_phase(phase, active, pkg=''):
                return f"data: {_json.dumps({'type': 'phase', 'phase': phase, 'active': active, 'pkg': pkg})}\n\n"

            def _emit_progress():
                building_pkgs = [v['pkg'] for v in active_acts.values()
                                 if v['phase'] == 'building' and v['pkg']]
                pkg = building_pkgs[0] if building_pkgs else ''
                return f"data: {_json.dumps({'type': 'progress', 'done': build_state['done'], 'total': build_state['total'], 'pkg': pkg})}\n\n"

            def _emit_dl():
                return f"data: {_json.dumps({'type': 'dl_progress', 'done': dl_state['done'], 'expected': dl_state['expected']})}\n\n"

            def _emit_global_progress():
                payload = {
                    'type': 'global_progress',
                    'built_done': global_state['built_done'],
                    'built_total': global_state['built_total'],
                    'copied_done': global_state['copied_done'],
                    'copied_total': global_state['copied_total'],
                    'copied_label': global_state['copied_label'],
                    'copied_expected': global_state['copied_expected'],
                    'dl_done': global_state['dl_done'],
                    'dl_expected': global_state['dl_expected'],
                }
                return f"data: {_json.dumps(payload)}\n\n"

            def _parse_size_to_bytes(value, unit):
                unit_map = {
                    'B': 1,
                    'KB': 1000, 'MB': 1000**2, 'GB': 1000**3, 'TB': 1000**4,
                    'KIB': 1024, 'MIB': 1024**2, 'GIB': 1024**3, 'TIB': 1024**4,
                }
                mult = unit_map.get((unit or 'B').upper())
                if mult is None:
                    return None
                try:
                    return int(float(value) * mult)
                except (TypeError, ValueError):
                    return None

            def _parse_bracket_progress(line):
                """
                Parse CLI-style aggregate progress, e.g.
                [53/312 built, 16/599/953 copied (6.7/15.9 GiB), 1.4/4.0 GiB DL]
                """
                m = _re.match(r'^\[([^\]]+)\]', line)
                if not m:
                    return None

                summary = m.group(1)
                result = {}

                built_match = _re.search(r'(\d+)\s*/\s*(\d+)\s+built\b', summary, _re.I)
                if built_match:
                    result['built_done'] = int(built_match.group(1))
                    result['built_total'] = int(built_match.group(2))

                copied_match = _re.search(
                    r'(\d+)\s*/\s*(\d+)(?:\s*/\s*(\d+))?\s+copied\b(?:\s*\(\s*([0-9.]+)\s*/\s*([0-9.]+)\s*([KMGT]?i?B)\s*\))?',
                    summary,
                    _re.I,
                )
                if copied_match:
                    result['copied_done'] = int(copied_match.group(1))
                    result['copied_total'] = int(copied_match.group(2))
                    copied_counts = [copied_match.group(1), copied_match.group(2)]
                    if copied_match.group(3):
                        copied_counts.append(copied_match.group(3))
                    result['copied_label'] = '/'.join(copied_counts)
                    if copied_match.group(4) and copied_match.group(5) and copied_match.group(6):
                        copied_done = _parse_size_to_bytes(copied_match.group(4), copied_match.group(6))
                        copied_expected = _parse_size_to_bytes(copied_match.group(5), copied_match.group(6))
                        if copied_done is not None:
                            result['copied_bytes_done'] = copied_done
                        if copied_expected is not None:
                            result['copied_expected'] = copied_expected

                dl_match = _re.search(r'([0-9.]+)\s*/\s*([0-9.]+)\s*([KMGT]?i?B)\s+DL\b', summary, _re.I)
                if dl_match:
                    dl_done = _parse_size_to_bytes(dl_match.group(1), dl_match.group(3))
                    dl_expected = _parse_size_to_bytes(dl_match.group(2), dl_match.group(3))
                    if dl_done is not None:
                        result['dl_done'] = dl_done
                    if dl_expected is not None:
                        result['dl_expected'] = dl_expected

                return result or None

            def _parse_nix_line(json_str):
                """Parse a @nix JSON line; yield SSE event strings."""
                try:
                    data = _json.loads(json_str)
                except Exception:
                    yield f"data: {_json.dumps({'type': 'output', 'line': json_str})}\n\n"
                    return

                action = data.get('action')

                if action == 'msg':
                    msg_text = data.get('msg', '')
                    if msg_text:
                        yield f"data: {_json.dumps({'type': 'output', 'line': msg_text})}\n\n"

                elif action == 'start':
                    act_id   = data.get('id')
                    nix_type = data.get('type', 0)
                    text     = data.get('text', '')

                    # nix_type takes priority for build/fetch; text handles evaluating + fallback
                    if nix_type == ACT_BUILD:
                        phase = 'building'
                        pkg   = _pkg_from_text(text)
                    elif nix_type == ACT_SUBSTITUTE:
                        phase, pkg = 'fetching', ''
                    elif _re.search(r'evaluating', text, _re.I):
                        phase, pkg = 'evaluating', ''
                    elif _re.search(r"building '?/nix/store/", text, _re.I):
                        phase = 'building'
                        pkg   = _pkg_from_text(text)
                    elif _re.search(r'fetch|download|copy', text, _re.I):
                        phase, pkg = 'fetching', ''
                    else:
                        phase, pkg = None, ''

                    if act_id is not None:
                        was_active = phase and any(v['phase'] == phase for v in active_acts.values())
                        active_acts[act_id] = {
                            'phase': phase, 'pkg': pkg, 'nix_type': nix_type,
                            'dl_done': 0, 'dl_expected': 0,
                        }
                        if phase and not was_active:
                            yield _emit_phase(phase, True, pkg)
                        if nix_type == ACT_BUILD:
                            if not build_state['aggregate']:
                                build_state['total'] += 1
                            if build_state['total'] > 0:
                                yield _emit_progress()

                elif action == 'stop':
                    act_id = data.get('id')
                    if act_id in active_acts:
                        act      = active_acts.pop(act_id)
                        nix_type = act['nix_type']

                        if nix_type == ACT_FILE_TRANSFER:
                            # Remove this transfer's byte contribution from download totals
                            dl_state['done']     = max(0, dl_state['done']     - act['dl_done'])
                            dl_state['expected'] = max(0, dl_state['expected'] - act['dl_expected'])

                        if nix_type == ACT_BUILD:
                            if not build_state['aggregate']:
                                build_state['done'] += 1
                            if build_state['total'] > 0:
                                yield _emit_progress()

                        stopped_phase = act['phase']
                        if stopped_phase:
                            still_active = any(v['phase'] == stopped_phase for v in active_acts.values())
                            if not still_active:
                                yield _emit_phase(stopped_phase, False)

                elif action == 'result':
                    result_type = data.get('type')
                    act_id      = data.get('id')
                    fields      = data.get('fields', [])

                    if result_type == 105:  # resProgress: [done, expected, running, failed]
                        act = active_acts.get(act_id)
                        if act is None or len(fields) < 2:
                            return
                        done, expected = fields[0], fields[1]
                        if act['nix_type'] == ACT_FILE_TRANSFER and isinstance(done, int) and isinstance(expected, int):
                            # Compressed download bytes from HTTP file transfer
                            dl_state['done']     += done     - act['dl_done']
                            dl_state['expected'] += expected - act['dl_expected']
                            dl_state['done']      = max(0, dl_state['done'])
                            dl_state['expected']  = max(0, dl_state['expected'])
                            act['dl_done']        = done
                            act['dl_expected']    = expected
                            if dl_state['expected'] > 0:
                                yield _emit_dl()
                        elif act['nix_type'] == ACT_BUILDS and isinstance(done, int) and isinstance(expected, int):
                            # Aggregate build counter from Builds activity – available before actBuild starts
                            build_state['aggregate'] = True
                            build_state['done']  = done
                            if expected > 0:
                                build_state['total'] = expected
                            if build_state['total'] > 0:
                                yield _emit_progress()

            try:
                # Flake eval in a git repo only sees tracked files – stage new
                # files so `.#host` matches the on-disk state. Non-flake builds
                # never need staging, so git is left untouched there.
                staged_with_git = False
                stage_msg = ""
                if use_flake:
                    try:
                        staged_with_git, stage_msg = git_manager.stage_all(nixos_dir)
                    except Exception:
                        staged_with_git, stage_msg = False, ""

                if staged_with_git:
                    yield f"data: {_json.dumps({'type': 'output', 'line': '── git add -A ──'})}\n\n"
                elif stage_msg:
                    yield f"data: {_json.dumps({'type': 'output', 'line': f'Git-Hinweis: {stage_msg}'})}\n\n"

                if update_flake:
                    yield f"data: {_json.dumps({'type': 'output', 'line': '── nix flake update ──'})}\n\n"
                    upd = _sp.Popen(
                        ["nix", "flake", "update"],
                        cwd=nixos_dir,
                        stdout=_sp.PIPE,
                        stderr=_sp.STDOUT,
                        text=True,
                        bufsize=1,
                    )
                    for raw_line in upd.stdout:
                        yield f"data: {_json.dumps({'type': 'output', 'line': raw_line.rstrip()})}\n\n"
                    upd.wait()
                    if upd.returncode != 0:
                        yield f"data: {_json.dumps({'type': 'error', 'message': f'nix flake update fehlgeschlagen (Exit {upd.returncode})'})}\n\n"
                        return
                    yield f"data: {_json.dumps({'type': 'output', 'line': '── nixos-rebuild ──'})}\n\n"

                # Pre-fetch dry-run: evaluate dependency graph to get stable total download size
                if prefetch_dry_run_on:
                    if use_flake:
                        dr_cmd = ["nixos-rebuild", "build", "--dry-run", "--flake", flake_arg]
                    else:
                        dr_cmd = ["nixos-rebuild", "build", "--dry-run",
                                  "-I", f"nixos-config={conf_path}"]
                    try:
                        yield _emit_phase('analysing', True)
                        dr = _sp.Popen(
                            dr_cmd,
                            cwd=nixos_dir,
                            stdout=_sp.PIPE,
                            stderr=_sp.STDOUT,
                            text=True,
                            bufsize=1,
                        )
                        total_mib = 0.0
                        for dr_line in dr.stdout:
                            m = _re.search(
                                r'these \d+ paths will be fetched \(([0-9.]+)\s+MiB download',
                                dr_line,
                            )
                            if m:
                                total_mib += float(m.group(1))
                        dr.wait()
                        yield _emit_phase('analysing', False)
                        if total_mib > 0:
                            yield f"data: {_json.dumps({'type': 'prefetch_total', 'mib': total_mib})}\n\n"
                    except Exception:
                        yield _emit_phase('analysing', False)

                proc = _sp.Popen(
                    cmd,
                    cwd=nixos_dir,       # flake.nix muss im cwd liegen
                    stdin=_sp.PIPE,
                    stdout=_sp.PIPE,
                    stderr=_sp.STDOUT,   # merge stderr into stdout
                    text=True,
                    bufsize=1,           # line-buffered
                )
                # Passwort an sudo -S senden und stdin schließen
                proc.stdin.write((sudo_password + "\n") if sudo_password else "")
                proc.stdin.flush()
                proc.stdin.close()
                for raw_line in proc.stdout:
                    line = raw_line.rstrip('\r\n')
                    log_lines.append(line)
                    if line.startswith('@nix '):
                        yield from _parse_nix_line(line[5:])
                    else:
                        progress = _parse_bracket_progress(line)
                        if progress:
                            global_state['available'] = True
                            if 'built_done' in progress:
                                global_state['built_done'] = progress['built_done']
                            if 'built_total' in progress:
                                global_state['built_total'] = progress['built_total']
                            if 'copied_done' in progress:
                                global_state['copied_done'] = progress['copied_done']
                            if 'copied_total' in progress:
                                global_state['copied_total'] = progress['copied_total']
                            if 'copied_label' in progress:
                                global_state['copied_label'] = progress['copied_label']
                            if 'copied_expected' in progress:
                                global_state['copied_expected'] = progress['copied_expected']
                            if 'dl_done' in progress:
                                global_state['dl_done'] = progress['dl_done']
                            if 'dl_expected' in progress:
                                global_state['dl_expected'] = progress['dl_expected']
                            yield _emit_global_progress()
                        yield f"data: {_json.dumps({'type': 'output', 'line': line})}\n\n"
                        # Activating phase detected from raw output (activation scripts
                        # run outside of nix's logger and write directly to stderr)
                        if _re.search(r'activating the configuration', line, _re.I):
                            yield _emit_phase('activating', True)
                proc.wait()
                success = proc.returncode == 0
                log_written = False
                if not success or rebuild_log_on:
                    try:
                        with open(log_path, 'w', encoding='utf-8') as _lf:
                            _lf.write('\n'.join(log_lines))
                        log_written = True
                    except Exception:
                        pass
                if not success:
                    yield f"data: {_json.dumps({'type': 'output', 'line': f'── Log gespeichert: {log_path} ──'})}\n\n"
                yield f"data: {_json.dumps({'type': 'done', 'success': success, 'exit_code': proc.returncode, 'log_written': log_written})}\n\n"
            except FileNotFoundError:
                msg = "nixos-rebuild not found. Is NiCo running on a NixOS system?"
                yield f"data: {_json.dumps({'type': 'error', 'message': msg})}\n\n"
            except Exception as exc:
                yield f"data: {_json.dumps({'type': 'error', 'message': str(exc)})}\n\n"

        return Response(
            stream_with_context(_generate()),
            mimetype='text/event-stream',
            headers={
                'Cache-Control':    'no-cache',
                'X-Accel-Buffering': 'no',  # disable nginx/proxy buffering
            },
        )

    @app.route("/api/rebuild/log")
    def rebuild_log():
        """Return the last nixos-rebuild log as plain text."""
        nixos_dir, err = _require_setup()
        if err:
            return err
        log_path = Path(nixos_dir) / "nixos-rebuild.log"
        if not log_path.exists():
            return jsonify({"error": "ERR_NO_LOG"}), 404
        try:
            content = log_path.read_text(encoding="utf-8", errors="replace")
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500
        return Response(content, mimetype="text/plain; charset=utf-8")


    @app.route("/api/dry-run", methods=["POST"])
    def dry_run():
        if err := _check_csrf(): return err
        """
        Validate configuration without writing anything.

        Flake mode  (flakes: true in nico.json):
          nix build .#nixosConfigurations.<host>.config.system.build.toplevel --dry-run
          Runs in nixos_dir.  Accepts body: {hostname, all_hosts}.
          all_hosts=true builds all known hosts in one call.

        Non-flake mode:
          1. nix-instantiate --eval -E "(import <nixpkgs/nixos> …)" – semantic
          2. Fallback: nix-instantiate --parse  – syntax only
        """
        import subprocess

        nixos_dir, err = _require_setup()
        if err:
            return err

        body   = request.get_json(silent=True) or {}
        stored = config_manager.load_config(nixos_dir) or {}
        use_flake    = stored.get("flakes", False)
        update_flake = bool(body.get("update_flake", False)) and use_flake

        # ── Flake mode ────────────────────────────────────────────────────
        if use_flake:
            # Optionales flake update vor dem Dry-Run
            if update_flake:
                try:
                    upd = subprocess.run(
                        ["nix", "flake", "update"],
                        cwd=nixos_dir,
                        capture_output=True,
                        text=True,
                        timeout=120,
                    )
                    if upd.returncode != 0:
                        combined = (upd.stdout + upd.stderr).strip()
                        return jsonify({"ok": False, "mode": "flake",
                                        "output": "nix flake update fehlgeschlagen:\n" + combined})
                except subprocess.TimeoutExpired:
                    return jsonify({"ok": False, "mode": "flake", "output": "ERR_UPDATE_TIMEOUT"})
                except FileNotFoundError:
                    return jsonify({"ok": False, "mode": "none",  "output": "ERR_DRY_NO_NIX"})

            all_hosts  = bool(body.get("all_hosts", False))
            hostname_p = (body.get("hostname") or "").strip()

            if all_hosts:
                hosts = _get_flake_hosts(nixos_dir, stored)
            elif hostname_p and re.fullmatch(r'[\w.-]+', hostname_p):
                hosts = [hostname_p]
            else:
                hosts = [(_get_flake_hosts(nixos_dir, stored) or ["nixos"])[0]]

            flake_ref = f"path:{Path(nixos_dir).resolve().as_posix()}"
            targets = [
                f"{flake_ref}#nixosConfigurations.{h}.config.system.build.toplevel"
                for h in hosts
            ]
            cmd = ["nix", "build"] + targets + [
                "--dry-run", "--no-link",
                "--extra-experimental-features", "nix-command flakes",
            ]

            try:
                result = subprocess.run(
                    cmd,
                    cwd=nixos_dir,
                    capture_output=True,
                    text=True,
                    timeout=120,
                )
                combined = (result.stdout + result.stderr).strip()
                if result.returncode == 0:
                    out = combined or "✓ Nichts zu bauen – Konfiguration ist aktuell."
                    return jsonify({"ok": True, "mode": "flake", "output": out})
                return jsonify({"ok": False, "mode": "flake", "output": combined or "Unbekannter Fehler."})
            except subprocess.TimeoutExpired:
                return jsonify({"ok": False, "mode": "flake", "output": "ERR_DRY_TIMEOUT_FLAKE"})
            except FileNotFoundError:
                return jsonify({"ok": False, "mode": "none",  "output": "ERR_DRY_NO_NIX"})

        # ── Non-flake mode ────────────────────────────────────────────────
        # Checks exactly what a non-flake rebuild would build: configuration.nix
        # plus whatever it imports itself. Host directories not imported anywhere
        # are reported by the validator (host_orphaned), not by dry-run.

        cfg_nix = Path(nixos_dir) / "configuration.nix"
        if not cfg_nix.exists():
            return jsonify({"ok": False, "mode": "semantic", "output": "ERR_NO_CONFIG"}), 400

        tmpfile = str(cfg_nix)

        try:
            # Attempt 1: full NixOS module evaluation.
            # Use the standard nixos-config lookup so dry-run matches
            # nixos-rebuild behavior for non-default config roots.
            try:
                result = subprocess.run(
                    [
                        "nix-instantiate",
                        "--eval",
                        "<nixpkgs/nixos>",
                        "-A", "config.system.build.toplevel.drvPath",
                        "-I", f"nixos-config={tmpfile}",
                    ],
                    cwd=nixos_dir,
                    capture_output=True, text=True, timeout=60
                )
                if result.returncode == 0:
                    return jsonify({
                        "ok": True,
                        "mode": "semantic",
                        "output": "✓ Semantische Prüfung erfolgreich – keine Fehler gefunden.",
                    })
                raw_err = (result.stderr or result.stdout or "").strip()
                nixpkgs_missing = (
                    "nixpkgs/nixos" in raw_err and
                    ("was not found" in raw_err or "cannot find" in raw_err.lower()
                     or "not found in" in raw_err)
                )
                if not nixpkgs_missing:
                    cleaned = _clean_nix_error(raw_err, tmpfile)
                    return jsonify({"ok": False, "mode": "semantic", "output": cleaned})
            except subprocess.TimeoutExpired:
                return jsonify({"ok": False, "mode": "semantic", "output": "ERR_DRY_TIMEOUT_SEM"})
            except FileNotFoundError:
                return jsonify({"ok": False, "mode": "none", "output": "ERR_DRY_NO_NIX"})
            except Exception:
                pass  # fall through to syntax-only

            # Attempt 2: syntax-only fallback
            result = subprocess.run(
                ["nix-instantiate", "--parse", tmpfile],
                capture_output=True, text=True, timeout=15
            )
            if result.returncode == 0:
                return jsonify({
                    "ok": True,
                    "mode": "syntax",
                    "output": "✓ Syntax korrekt – keine Fehler gefunden.\n"
                              "(Hinweis: Nur Syntax-Prüfung; <nixpkgs/nixos> nicht verfügbar.)",
                })
            raw_err = (result.stderr or result.stdout or "Unbekannter Fehler.").strip()
            return jsonify({"ok": False, "mode": "syntax", "output": _clean_nix_error(raw_err, tmpfile)})

        except FileNotFoundError:
            return jsonify({"ok": False, "mode": "none", "output": "ERR_DRY_NO_NIX"})
        except subprocess.TimeoutExpired:
            return jsonify({"ok": False, "mode": "syntax", "output": "ERR_DRY_TIMEOUT_SYN"})

    # -------------------------------------------------------------------- sudo

    @app.route("/api/sudo/acquire", methods=["POST"])
    def sudo_acquire():
        """Speichert ein Sudo-Passwort temporär (60 s) und gibt eine Einmal-Nonce zurück."""
        if err := _check_csrf(): return err
        body = request.get_json(silent=True) or {}
        password = body.get("password", "")
        nonce = secrets.token_hex(16)
        now = _time_mod.time()
        _sudo_nonces[nonce] = (password, now + 60)
        # abgelaufene aufräumen
        for k in list(_sudo_nonces):
            if _sudo_nonces[k][1] < now:
                del _sudo_nonces[k]
        return jsonify({"nonce": nonce})

