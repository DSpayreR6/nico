"""Nix-Brix block routes: add, delete, rename, split, move.

Split from server.py. Route bodies are unchanged; shared request helpers
from create_app() are passed in via the ctx dict.
"""

import re
from pathlib import Path

from flask import jsonify, request

from ..brix import extract_brick_blocks, format_brick, next_order_for_section, check_bracket_balance, deactivate_body, activate_body
from ..core import (
    brick_body as _brick_body,
    brick_blocks_for_file as _brick_blocks_for_file,
    modify_brick_in_file as _modify_brick_in_file,
)


def _nested_reply(err_str: str, nested: list[dict], fname: str):
    """Response for a brick write refused because it would nest bricks."""
    return jsonify({
        "error":  err_str,
        "nested": [{**f, "file": fname} for f in nested],
    }), 400


def _refused(err_str: str, details: list[dict], fname: str):
    """Response for a brick write the file would not survive.

    Two refusals share this path, both from modify_brick_in_file: a nesting it
    would introduce, and content that leaves the file unreadable for Nix. The
    second carries the Nix message, which names the line – without it the user
    only learns that something is wrong, not what.
    """
    if err_str == "ERR_BRICK_NESTED":
        return _nested_reply(err_str, details, fname)
    # An input still in use gets a sentence that says what to do; the name of
    # the input and of the brick travel behind the separator tErr() splits on.
    if details and details[0].get("input"):
        err_str = f"ERR_INPUT_STILL_USED\x1f{details[0]['input']}\x1f{details[0]['brick']}"
    return jsonify({
        "error":  err_str,
        "file":   fname,
        "detail": (details[0].get("detail") if details else "") or "",
    }), 400


def register(app, ctx):
    _check_csrf    = ctx["check_csrf"]
    _require_setup = ctx["require_setup"]

    # ------------------------------------------------------------------- brix

    @app.route("/api/brix/blocks", methods=["GET"])
    def brix_blocks():
        """Bricks of one file, read from disk – the single source for every
        file. Read-only, no side effects."""
        nixos_dir, err = _require_setup()
        if err:
            return err
        rel = request.args.get("file", "configuration.nix")
        try:
            blocks = _brick_blocks_for_file(nixos_dir, rel)
        except FileNotFoundError:
            return jsonify({"error": "ERR_NOT_FOUND"}), 404
        except ValueError as exc:
            code = str(exc)
            if code.startswith("ERR_"):
                return jsonify({"error": code}), 400
            return jsonify({"error": "ERR_PATH_OUTSIDE"}), 403
        except OSError:
            return jsonify({"error": "ERR_READ_FAILED"}), 500
        return jsonify({"file": rel, "blocks": blocks})

    @app.route("/api/brick", methods=["POST"])
    def brick_add():
        if err := _check_csrf(): return err
        """Create a new empty Brick block and write it directly into the .nix file."""
        nixos_dir, err = _require_setup()
        if err:
            return err
        body    = request.get_json(silent=True) or {}
        name    = body.get("name", "").strip()
        section = body.get("section", "TAIL").strip()
        fname   = body.get("file", "configuration.nix")
        if not name:
            return jsonify({"error": "ERR_NO_BRICK_NAME"}), 400
        if not re.fullmatch(r'[\w\-]+', name):
            return jsonify({"error": "ERR_INVALID_NAME"}), 400
        name_collision = [False]

        def _add(blocks):
            if name in blocks:
                name_collision[0] = True
                return blocks
            order     = next_order_for_section(blocks, section)
            # File text, so English - same rule as the section names
            # (vorgaben.txt: "Kommentare im Code Englisch"). Not translated:
            # the comment lands in the .nix and must not change with the UI
            # language, or two machines with different languages would write
            # alternating diffs on the same config.
            body_text = "  # Put your Nix code here"
            blocks[name] = {
                "section": section,
                "order":   order,
                "text":    format_brick(section, order, name, body_text),
            }
            return blocks

        ok, err_str, nested = _modify_brick_in_file(nixos_dir, fname, _add)
        if name_collision[0]:
            return jsonify({"error": "ERR_BRICK_NAME_EXISTS"}), 409
        if err_str in ("ERR_BRICK_NESTED", "ERR_BRICK_BREAKS_FILE"):
            return _refused(err_str, nested, fname)
        if not ok:
            return jsonify({"error": err_str}), 500
        return jsonify({"success": True, "brick_name": name,
                        "nested": [{**f, "file": fname} for f in nested]})

    @app.route("/api/brick/<path:name>", methods=["DELETE"])
    def brick_delete(name):
        if err := _check_csrf(): return err
        """Remove a Brick block by name directly from the .nix file."""
        nixos_dir, err = _require_setup()
        if err:
            return err
        fname = request.args.get("file", "configuration.nix")

        def _del(blocks):
            blocks.pop(name, None)
            return blocks

        ok, err_str, nested = _modify_brick_in_file(nixos_dir, fname, _del)
        if err_str in ("ERR_BRICK_NESTED", "ERR_BRICK_BREAKS_FILE"):
            return _refused(err_str, nested, fname)
        if not ok:
            return jsonify({"error": err_str}), 500
        return jsonify({"success": True,
                        "nested": [{**f, "file": fname} for f in nested]})

    @app.route("/api/brick/<path:name>", methods=["PATCH"])
    def brick_update(name):
        if err := _check_csrf(): return err
        """Update the body of an existing Brick block in the .nix file."""
        nixos_dir, err = _require_setup()
        if err:
            return err
        body      = request.get_json(silent=True) or {}
        content   = body.get("content", "")
        fname     = body.get("file", "configuration.nix")
        # None = leave the flag as it is. Deactivating goes through this route
        # instead of its own, so it passes the same guards as every other
        # brick write: nesting, bracket balance, and the refusal when the
        # result would stop Nix from reading the file.
        want_inactive = body.get("inactive")

        # Only for a real content edit. A deactivate request carries no body of
        # its own - it works on what is on disk, which also makes it idempotent.
        if want_inactive is None:
            balance_errors = check_bracket_balance(content)
            if balance_errors:
                return jsonify({"error": "ERR_UNBALANCED_BRACKETS", "details": balance_errors}), 400
        elif not want_inactive:
            # Activating: check before touching the file, the way renaming
            # does. A refused operation must leave the file alone - running
            # the check inside the modifier meant the writer rewrote the file
            # anyway, and the result differed in whitespace from what was
            # there before.
            fpath = Path(nixos_dir) / fname
            if fpath.exists():
                on_disk = extract_brick_blocks(fpath.read_text(encoding="utf-8"))
                b = on_disk.get(name)
                if b is not None and b.get("inactive", False):
                    errs = check_bracket_balance(activate_body(_brick_body(b["text"])))
                    if errs:
                        return jsonify({"error": "ERR_UNBALANCED_BRACKETS",
                                        "details": errs}), 400

        not_found  = [False]

        def _update(blocks):
            if name not in blocks:
                not_found[0] = True
                return blocks
            b    = blocks[name]
            was  = bool(b.get("inactive", False))
            flag = was if want_inactive is None else bool(want_inactive)
            if want_inactive is None:
                new_body = content
            elif flag == was:
                new_body = _brick_body(b["text"])      # nothing to do
            elif flag:
                new_body = deactivate_body(_brick_body(b["text"]))
            else:
                # Balance already checked above, before anything was written.
                new_body = activate_body(_brick_body(b["text"]))
            blocks[name] = {
                "section":  b["section"],
                "order":    b["order"],
                "text":     format_brick(b["section"], b["order"], name, new_body, flag),
                "inactive": flag,
            }
            return blocks

        ok, err_str, nested = _modify_brick_in_file(nixos_dir, fname, _update)
        if not_found[0]:
            return jsonify({"error": "ERR_BRICK_NOT_FOUND"}), 404
        if err_str in ("ERR_BRICK_NESTED", "ERR_BRICK_BREAKS_FILE"):
            return _refused(err_str, nested, fname)
        if not ok:
            return jsonify({"error": err_str}), 500
        return jsonify({"success": True,
                        "nested": [{**f, "file": fname} for f in nested]})

    @app.route("/api/brick/rename", methods=["POST"])
    def brick_rename():
        if err := _check_csrf(): return err
        """Rename a Brick block: updates the markers inside the .nix file."""
        nixos_dir, err = _require_setup()
        if err:
            return err
        body     = request.get_json(silent=True) or {}
        old_name = body.get("old_name", "").strip()
        new_name = body.get("new_name", "").strip()
        fname    = body.get("file", "configuration.nix")
        if not old_name or not new_name:
            return jsonify({"error": "ERR_NO_BRICK_NAME"}), 400
        if not re.fullmatch(r'[\w\-]+', new_name):
            return jsonify({"error": "ERR_INVALID_NAME"}), 400

        # Pre-check current state
        fpath = Path(nixos_dir) / fname
        if fpath.exists():
            existing = extract_brick_blocks(fpath.read_text(encoding="utf-8"))
            if old_name not in existing:
                return jsonify({"error": "ERR_BRICK_NOT_FOUND"}), 404
            if new_name != old_name and new_name in existing:
                return jsonify({"error": "ERR_BRICK_NAME_EXISTS"}), 409

        def _rename(blocks):
            if old_name not in blocks:
                return blocks
            b = blocks[old_name]
            new_block = {
                "section":  b["section"],
                "order":    b["order"],
                "text":     format_brick(b["section"], b["order"], new_name,
                                         _brick_body(b["text"]),
                                         b.get("inactive", False)),
                "inactive": b.get("inactive", False),
            }
            return {(new_name if k == old_name else k): (new_block if k == old_name else v)
                    for k, v in blocks.items()}

        ok, err_str, nested = _modify_brick_in_file(nixos_dir, fname, _rename)
        if err_str in ("ERR_BRICK_NESTED", "ERR_BRICK_BREAKS_FILE"):
            return _refused(err_str, nested, fname)
        if not ok:
            return jsonify({"error": err_str}), 500
        return jsonify({"success": True, "new_name": new_name,
                        "nested": [{**f, "file": fname} for f in nested]})

    @app.route("/api/brick/split", methods=["POST"])
    def brick_split():
        if err := _check_csrf(): return err
        """Split a Brick block into two at the given content line (1-based, relative to block body)."""
        nixos_dir, err = _require_setup()
        if err:
            return err
        body       = request.get_json(silent=True) or {}
        name       = body.get("name", "").strip()
        split_line = body.get("split_line")
        new_name   = body.get("new_name", "").strip()
        fname      = body.get("file", "configuration.nix")
        if not name or not new_name:
            return jsonify({"error": "ERR_NO_BRICK_NAME"}), 400
        if not re.fullmatch(r'[\w\-]+', new_name):
            return jsonify({"error": "ERR_INVALID_NAME"}), 400
        if not isinstance(split_line, int) or split_line < 1:
            return jsonify({"error": "ERR_INVALID_SPLIT_LINE"}), 400
        errors = [None]

        def _split(blocks):
            if name not in blocks:
                errors[0] = ("ERR_BRICK_NOT_FOUND", 404)
                return blocks
            b          = blocks[name]
            body_lines = _brick_body(b["text"]).splitlines(keepends=True)
            if split_line >= len(body_lines):
                errors[0] = ("ERR_SPLIT_OUT_OF_RANGE", 400)
                return blocks
            body1  = "".join(body_lines[:split_line])
            body2  = "".join(body_lines[split_line:])
            # Both halves keep the state of the original. Splitting does not
            # judge what the content does, so it must not switch anything on -
            # the user activates what they want (decision Martin 2026-11-01).
            inact  = b.get("inactive", False)
            block1 = {"section": b["section"], "order": b["order"], "inactive": inact,
                      "text": format_brick(b["section"], b["order"], name, body1, inact)}
            block2 = {"section": b["section"], "order": b["order"] + 1, "inactive": inact,
                      "text": format_brick(b["section"], b["order"] + 1, new_name,
                                           body2, inact)}
            items = list(blocks.items())
            idx   = next(i for i, (k, _) in enumerate(items) if k == name)
            items[idx] = (name, block1)
            items.insert(idx + 1, (new_name, block2))
            return dict(items)

        ok, err_str, nested = _modify_brick_in_file(nixos_dir, fname, _split)
        if errors[0]:
            return jsonify({"error": errors[0][0]}), errors[0][1]
        if err_str in ("ERR_BRICK_NESTED", "ERR_BRICK_BREAKS_FILE"):
            return _refused(err_str, nested, fname)
        if not ok:
            return jsonify({"error": err_str}), 500
        return jsonify({"success": True, "new_name": new_name,
                        "nested": [{**f, "file": fname} for f in nested]})

    @app.route("/api/brick/move", methods=["POST"])
    def brick_move():
        if err := _check_csrf(): return err
        """Move a Brick block to a different section / order position."""
        nixos_dir, err = _require_setup()
        if err:
            return err
        body    = request.get_json(silent=True) or {}
        name    = body.get("name", "").strip()
        section = body.get("section", "").strip()
        order   = body.get("order")
        fname   = body.get("file", "configuration.nix")
        if not name:
            return jsonify({"error": "ERR_NO_BRICK_NAME"}), 400
        if not section:
            return jsonify({"error": "ERR_NO_SECTION"}), 400
        if not isinstance(order, int) or order < 1:
            return jsonify({"error": "ERR_INVALID_ORDER"}), 400
        not_found = [False]

        def _move(blocks):
            if name not in blocks:
                not_found[0] = True
                return blocks
            b = blocks[name]
            blocks[name] = {
                "section":  section,
                "order":    order,
                "text":     format_brick(section, order, name, _brick_body(b["text"]),
                                         b.get("inactive", False)),
                "inactive": b.get("inactive", False),
            }
            return blocks

        ok, err_str, nested = _modify_brick_in_file(nixos_dir, fname, _move)
        if not_found[0]:
            return jsonify({"error": "ERR_BRICK_NOT_FOUND"}), 404
        if err_str in ("ERR_BRICK_NESTED", "ERR_BRICK_BREAKS_FILE"):
            return _refused(err_str, nested, fname)
        if not ok:
            return jsonify({"error": err_str}), 500
        return jsonify({"success": True,
                        "nested": [{**f, "file": fname} for f in nested]})

