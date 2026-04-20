"""
Brick block parser.
Brick blocks are manually written Nix sections that NiCo never modifies.
They are extracted before regenerating a file and re-inserted afterwards.

Syntax:
  # <brick: SectionName / #N brick-name>
  ...nix code...
  # </brick: brick-name>

Rules:
  - Unique name per block
  - No nesting
  - Multiple blocks per file allowed
  - SectionName: which NiCo section this brick belongs to
  - N: order within the section (1, 2, 3, ...)
"""

import re

_START     = re.compile(r"#\s*<brick:\s*([^/]+?)\s*/\s*#(\d+)\s+([\w\-]+)\s*>")
_END       = re.compile(r"#\s*</brick:\s*([\w\-]+)\s*>")
# Legacy format (automatically migrated to new format on first read/write)
_START_OLD = re.compile(r"#\s*<brix:\s*([\w\-]+)\s*>")
_END_OLD   = re.compile(r"#\s*</brix:\s*([\w\-]+)\s*>")

# Canonical section order for sorting bricks in the generated file
SECTION_ORDER: list[str] = [
    "Start",
    "Boot", "System", "Lokalisierung", "Netzwerk", "Services",
    "Desktop", "Audio", "Benutzer", "Programme",
    "Schriftarten", "Nix & System", "Hardware", "Virtualisierung",
    "Dateisystem & Backup", "Home Manager",
    "End",
]

# Zone sections: bricks are injected immediately when their section marker is
# encountered in the generated file, rather than being flushed at the next
# regular section boundary.  Used for injection points inside nested blocks
# (e.g. inside inputs = { } or outputs = { } in flake.nix).
ZONE_SECTIONS: frozenset[str] = frozenset({"Inputs-Extra", "Outputs-Extra", "Outputs-Hosts"})


def expand_nested_legacy(blocks: dict[str, dict]) -> dict[str, dict]:
    """
    Post-processing step: if a brick's body contains nested legacy # <brix:>
    sub-blocks (e.g. an old "imported-rest" wrapper), expand them into
    individual bricks with section "End".  The wrapper is dropped when its
    body consisted entirely of legacy sub-blocks; kept (cleaned) otherwise.
    Idempotent: new-format-only blocks are returned unchanged.
    """
    result: dict[str, dict] = {}
    for bname, bblock in blocks.items():
        text_lines = bblock["text"].splitlines(keepends=True)
        if len(text_lines) < 3:
            result[bname] = bblock
            continue
        body_lines = text_lines[1:-1]
        body = "".join(body_lines)
        if not _START_OLD.search(body):
            result[bname] = bblock
            continue

        # Expand: extract each legacy sub-block individually
        sub_order   = 1
        cur_name:   str | None  = None
        cur_lines:  list[str]   = []
        non_brix:   list[str]   = []

        for line in body_lines:
            if cur_name is None:
                m = _START_OLD.search(line)
                if m:
                    cur_name  = m.group(1)
                    cur_lines = []
                else:
                    non_brix.append(line)
            else:
                m_end = _END_OLD.search(line)
                if m_end and m_end.group(1) == cur_name:
                    sub_body = "".join(cur_lines).rstrip("\n")
                    result[cur_name] = {
                        "section": "End",
                        "order":   sub_order,
                        "text":    format_brick("End", sub_order, cur_name, sub_body),
                    }
                    sub_order += 1
                    cur_name  = None
                    cur_lines = []
                else:
                    cur_lines.append(line)

        remaining = "".join(non_brix).strip()
        if remaining:
            result[bname] = {
                "section": bblock["section"],
                "order":   bblock["order"],
                "text":    format_brick(bblock["section"], bblock["order"], bname, remaining),
            }
        # else: wrapper was only legacy sub-blocks → drop it entirely

    return result


def extract_brick_blocks(nix_content: str) -> dict[str, dict]:
    """
    Parse nix_content and return all Brick blocks as:
      {name: {"section": str, "order": int, "text": full_text_with_markers}}
    Supports both new format (# <brick: Section / #N name>) and legacy format
    (# <brix: name>). Legacy blocks are automatically converted to new format
    so they get migrated on the next write-back.
    New-format bricks whose body contains nested legacy sub-blocks are
    expanded into individual bricks (see expand_nested_legacy).
    Malformed or unclosed blocks are silently ignored.
    """
    blocks: dict[str, dict] = {}
    lines = nix_content.splitlines(keepends=True)

    current_name: str | None = None
    current_section: str = "End"
    current_order: int = 1
    current_lines: list[str] = []
    is_legacy: bool = False
    legacy_auto_order: int = 1  # used for migrating legacy blocks

    for line in lines:
        if current_name is None:
            m = _START.search(line)
            if m:
                current_section = m.group(1).strip()
                current_order   = int(m.group(2))
                current_name    = m.group(3)
                current_lines   = [line]
                is_legacy       = False
            else:
                m_old = _START_OLD.search(line)
                if m_old:
                    current_name    = m_old.group(1)
                    current_section = "End"
                    current_order   = legacy_auto_order
                    current_lines   = [line]
                    is_legacy       = True
        else:
            current_lines.append(line)
            m_end     = _END.search(line)
            m_end_old = _END_OLD.search(line)
            end_name  = (m_end and m_end.group(1)) or (m_end_old and m_end_old.group(1))

            if end_name == current_name:
                if is_legacy:
                    # Migrate: reconstruct body without old markers, store in new format
                    body = "".join(current_lines[1:-1]).rstrip("\n")
                    text = format_brick(current_section, current_order, current_name, body)
                    legacy_auto_order += 1
                else:
                    text = "".join(current_lines)

                blocks[current_name] = {
                    "section": current_section,
                    "order":   current_order,
                    "text":    text,
                }
                current_name  = None
                current_lines = []
                is_legacy     = False

    return expand_nested_legacy(blocks)


def strip_brick_blocks(nix_content: str) -> str:
    """Remove all brick blocks (including their marker lines) from nix_content.
    Handles both new format (# <brick:) and legacy format (# <brix:).
    Only exits a block when the matching end marker (same format + same name) is found.
    Orphaned end markers (no matching start) are also stripped."""
    lines = nix_content.splitlines(keepends=True)
    result: list[str] = []
    in_brick   = False
    brick_fmt  = None   # 'new' or 'old'
    brick_name = None

    for line in lines:
        if not in_brick:
            m = _START.search(line)
            if m:
                in_brick   = True
                brick_fmt  = 'new'
                brick_name = m.group(3)
                continue
            m_old = _START_OLD.search(line)
            if m_old:
                in_brick   = True
                brick_fmt  = 'old'
                brick_name = m_old.group(1)
                continue
            # Strip orphaned end markers that have no matching start
            if _END.search(line) or _END_OLD.search(line):
                continue
            result.append(line)
        else:
            # Inside a block: only exit on the matching format AND name
            if brick_fmt == 'new':
                m_end = _END.search(line)
                if m_end and m_end.group(1) == brick_name:
                    in_brick = brick_fmt = brick_name = None
            else:
                m_end = _END_OLD.search(line)
                if m_end and m_end.group(1) == brick_name:
                    in_brick = brick_fmt = brick_name = None
            # All lines inside a block are skipped (continue implied by else branch)
    return "".join(result)


def _section_rank(section: str) -> int:
    try:
        return SECTION_ORDER.index(section)
    except ValueError:
        return len(SECTION_ORDER) - 2  # unknown sections go just before End


_SECTION_HDR = re.compile(r"^\s*#\s*──+\s*(.+?)\s*──")


def inject_brick_blocks(nix_content: str, blocks: dict[str, dict]) -> str:
    """
    Insert Brick blocks inline into generated Nix content.
    Each brick is injected after its section's content, directly before the
    next section's header line.  "Start" bricks appear before the first
    section header.  "End" bricks and bricks for unrecognised sections are
    flushed just before the final closing brace.

    Zone sections (ZONE_SECTIONS): bricks are injected immediately when their
    section marker is encountered – used for injection inside nested Nix blocks
    like inputs = { } or outputs = { } in flake.nix.
    """
    if not blocks:
        return nix_content

    # Separate zone bricks (immediate injection) from regular bricks
    zone_bricks: dict[str, list[dict]] = {}
    regular_blocks: dict[str, dict] = {}
    for name, block in blocks.items():
        sec = block.get("section", "")
        if sec in ZONE_SECTIONS:
            zone_bricks.setdefault(sec, []).append(block)
        else:
            regular_blocks[name] = block

    for sec in zone_bricks:
        zone_bricks[sec].sort(key=lambda b: b["order"])

    sorted_blocks = sorted(
        regular_blocks.values(),
        key=lambda b: (_section_rank(b["section"]), b["order"])
    )
    ptr = 0  # next brick index to inject

    def pop_up_to(max_rank: int) -> list[dict]:
        nonlocal ptr
        out = []
        while ptr < len(sorted_blocks) and _section_rank(sorted_blocks[ptr]["section"]) <= max_rank:
            out.append(sorted_blocks[ptr])
            ptr += 1
        return out

    def bricks_text(bricks: list[dict]) -> str:
        return "\n".join(b["text"] for b in bricks) + "\n"

    lines = nix_content.splitlines(keepends=True)
    result: list[str] = []

    for line in lines:
        m = _SECTION_HDR.search(line)
        if m:
            section_name = m.group(1).strip()
            if section_name in ZONE_SECTIONS:
                # Zone section: inject its bricks immediately after this marker
                result.append(line)
                zone = zone_bricks.get(section_name, [])
                if zone:
                    result.append(bricks_text(zone))
            else:
                # Regular section: flush pending bricks ranked before this one
                pending = pop_up_to(_section_rank(section_name) - 1)
                if pending:
                    result.append(bricks_text(pending))
                result.append(line)
        elif line.rstrip() == "}":
            # Final closing brace: flush all remaining bricks
            pending = pop_up_to(len(SECTION_ORDER))
            if pending:
                result.append(bricks_text(pending))
            result.append(line)
        else:
            result.append(line)

    # Safety net: append anything still pending (e.g. no closing brace found)
    if ptr < len(sorted_blocks):
        result.append(bricks_text(sorted_blocks[ptr:]))

    return "".join(result)


def format_brick(section: str, order: int, name: str, body: str) -> str:
    """Format a complete brick block with markers."""
    return f"# <brick: {section} / #{order} {name}>\n{body}\n# </brick: {name}>\n"


def next_order_for_section(blocks: dict[str, dict], section: str) -> int:
    """Return the next free order number for a given section."""
    orders = [b["order"] for b in blocks.values() if b["section"] == section]
    return max(orders, default=0) + 1


def brix_content_to_bricks(
    brix_content: str,
    section: str = "Start",
    existing_blocks: dict[str, dict] | None = None,
) -> dict[str, dict]:
    """Convert build_rest_brix output (# <brix:> formatted content) into
    individual brick block dicts.  Order numbers start after the highest
    existing order in *section* within existing_blocks."""
    base = next_order_for_section(existing_blocks or {}, section)
    blocks: dict[str, dict] = {}
    lines = brix_content.splitlines(keepends=True)
    cur_name: str | None = None
    cur_lines: list[str] = []
    order = base

    for line in lines:
        if cur_name is None:
            m = _START_OLD.search(line)
            if m:
                cur_name  = m.group(1)
                cur_lines = []
        else:
            m_end = _END_OLD.search(line)
            if m_end and m_end.group(1) == cur_name:
                body = "".join(cur_lines).rstrip("\n")
                blocks[cur_name] = {
                    "section": section,
                    "order":   order,
                    "text":    format_brick(section, order, cur_name, body),
                }
                order    += 1
                cur_name  = None
                cur_lines = []
            else:
                cur_lines.append(line)

    return blocks
