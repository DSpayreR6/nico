"""Nix-Brix block routes: add, delete, rename, split, move.

Split from server.py. Route bodies are unchanged; shared request helpers
from create_app() are passed in via the ctx dict.
"""

import re
from pathlib import Path

from flask import jsonify, request

from ..brix import extract_brick_blocks, format_brick, next_order_for_section, check_bracket_balance
from ..core import (
    brick_body as _brick_body,
    modify_brick_in_file as _modify_brick_in_file,
)


def register(app, ctx):
    _check_csrf    = ctx["check_csrf"]
    _require_setup = ctx["require_setup"]

    # ------------------------------------------------------------------- brix

    @app.route("/api/brick", methods=["POST"])
    def brick_add():
        if err := _check_csrf(): return err
        """Create a new empty Brick block and write it directly into the .nix file."""
        nixos_dir, err = _require_setup()
        if err:
            return err
        body    = request.get_json(silent=True) or {}
        name    = body.get("name", "").strip()
        section = body.get("section", "End").strip()
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
            body_text = "  # Nix-Code hier einfügen"
            blocks[name] = {
                "section": section,
                "order":   order,
                "text":    format_brick(section, order, name, body_text),
            }
            return blocks

        ok, err_str = _modify_brick_in_file(nixos_dir, fname, _add)
        if name_collision[0]:
            return jsonify({"error": "ERR_BRICK_NAME_EXISTS"}), 409
        if not ok:
            return jsonify({"error": err_str}), 500
        return jsonify({"success": True, "brick_name": name})

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

        ok, err_str = _modify_brick_in_file(nixos_dir, fname, _del)
        if not ok:
            return jsonify({"error": err_str}), 500
        return jsonify({"success": True})

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

        balance_errors = check_bracket_balance(content)
        if balance_errors:
            return jsonify({"error": "ERR_UNBALANCED_BRACKETS", "details": balance_errors}), 400

        not_found = [False]

        def _update(blocks):
            if name not in blocks:
                not_found[0] = True
                return blocks
            b = blocks[name]
            blocks[name] = {
                "section": b["section"],
                "order":   b["order"],
                "text":    format_brick(b["section"], b["order"], name, content),
            }
            return blocks

        ok, err_str = _modify_brick_in_file(nixos_dir, fname, _update)
        if not_found[0]:
            return jsonify({"error": "ERR_BRICK_NOT_FOUND"}), 404
        if not ok:
            return jsonify({"error": err_str}), 500
        return jsonify({"success": True})

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
                "section": b["section"],
                "order":   b["order"],
                "text":    format_brick(b["section"], b["order"], new_name,
                                        _brick_body(b["text"])),
            }
            return {(new_name if k == old_name else k): (new_block if k == old_name else v)
                    for k, v in blocks.items()}

        ok, err_str = _modify_brick_in_file(nixos_dir, fname, _rename)
        if not ok:
            return jsonify({"error": err_str}), 500
        return jsonify({"success": True, "new_name": new_name})

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
            block1 = {"section": b["section"], "order": b["order"],
                      "text": format_brick(b["section"], b["order"], name, body1)}
            block2 = {"section": b["section"], "order": b["order"] + 1,
                      "text": format_brick(b["section"], b["order"] + 1, new_name, body2)}
            items = list(blocks.items())
            idx   = next(i for i, (k, _) in enumerate(items) if k == name)
            items[idx] = (name, block1)
            items.insert(idx + 1, (new_name, block2))
            return dict(items)

        ok, err_str = _modify_brick_in_file(nixos_dir, fname, _split)
        if errors[0]:
            return jsonify({"error": errors[0][0]}), errors[0][1]
        if not ok:
            return jsonify({"error": err_str}), 500
        return jsonify({"success": True, "new_name": new_name})

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
                "section": section,
                "order":   order,
                "text":    format_brick(section, order, name, _brick_body(b["text"])),
            }
            return blocks

        ok, err_str = _modify_brick_in_file(nixos_dir, fname, _move)
        if not_found[0]:
            return jsonify({"error": "ERR_BRICK_NOT_FOUND"}), 404
        if not ok:
            return jsonify({"error": err_str}), 500
        return jsonify({"success": True})

