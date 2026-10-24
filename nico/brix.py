"""
Brick block parser.
Brick blocks are manually written Nix sections that NiCo never modifies.
They are extracted before regenerating a file and re-inserted afterwards.

Syntax:
  # <brick: CODE / #N brick-name>
  ...nix code...
  # </brick: brick-name>

Rules:
  - Unique name per block
  - No nesting
  - Multiple blocks per file allowed
  - CODE: four-character section code (see SECTIONS), language-neutral so a
    section can be renamed in the UI without touching a single file.  Per-host
    sections carry the host name as payload: HOST:<name>
  - N: order within the section (1, 2, 3, ...)
"""

import re

_START     = re.compile(r"#\s*<brick:\s*([^/]+?)\s*/\s*#(\d+)\s+([\w\-]+)\s*>")
_END       = re.compile(r"#\s*</brick:\s*([\w\-]+)\s*>")
# Legacy format (automatically migrated to new format on first read/write)
_START_OLD = re.compile(r"#\s*<brix:\s*([\w\-]+)\s*>")
_END_OLD   = re.compile(r"#\s*</brix:\s*([\w\-]+)\s*>")

# ── File format version ───────────────────────────────────────────────────────
# Counts only changes to the file layout NiCo parses, not NiCo releases: a new
# release must not make older files look unreadable.  Rides in the nico-version
# line as a trailing "#f<N>" – metadata for NiCo, not for the user to read.
#
#   1  (implicit, no marker)  German section names in markers and headers
#   2                         four-character section codes, "CODE · Name" headers
FILE_FORMAT = 2
SUPPORTED_FORMATS = frozenset({1, 2})

# ── Sections ──────────────────────────────────────────────────────────────────
# The single source for sections: code -> (name written into the .nix, i18n key
# for the UI).  Order of this table is the order bricks are sorted in.
#
# The name in the file is always English and lives here, not in a language file:
# the generator must be able to write it even when a translation is missing, and
# a file whose text depended on the UI language would be rewritten on every
# language switch.  Translation happens only where NiCo leads – in the UI.
SECTIONS: dict[str, tuple[str, str]] = {
    "HEAD": ("Start",                   "sec.start"),
    "BOOT": ("Boot",                    "sec.boot"),
    "SYST": ("System",                  "sec.system"),
    "I18N": ("Localisation",            "sec.localization"),
    "NETW": ("Networking",              "sec.network"),
    "SERV": ("Services",                "sec.services"),
    "DESK": ("Desktop",                 "sec.desktop"),
    "AUDI": ("Audio",                   "sec.audio"),
    "USER": ("Users",                   "sec.user"),
    "PROG": ("Programs",                "sec.packages"),
    "FONT": ("Fonts",                   "sec.fonts"),
    "NIXS": ("Nix & system",            "sec.nixSystem"),
    "HDWR": ("Hardware",                "sec.hardware"),
    "VIRT": ("Virtualisation",          "sec.virtualization"),
    "FSYS": ("Maintenance & snapshots", "sec.filesystem"),
    "HOME": ("Home Manager",            "sec.homeManager"),
    "TAIL": ("End",                     "sec.end"),
    # flake.nix: headers of the real inputs/outputs blocks …
    "INPU": ("Inputs",                  "sec.inputs"),
    "OUTP": ("Outputs",                 "sec.outputs"),
    # … and the injection zones inside them
    "INPX": ("Inputs extra",            "sec.inputsExtra"),
    "OUTX": ("Outputs extra",           "sec.outputsExtra"),
    "OUTH": ("Outputs hosts",           "sec.outputsHosts"),
}

# Canonical section order for sorting bricks in the generated file
SECTION_ORDER: list[str] = ["HEAD",
                            "BOOT", "SYST", "I18N", "NETW", "SERV",
                            "DESK", "AUDI", "USER", "PROG",
                            "FONT", "NIXS", "HDWR", "VIRT",
                            "FSYS", "HOME",
                            "TAIL"]

# Which section a NixOS attribute path belongs to, most specific prefix first.
# Used to file rescued bricks where a user would look for them; anything not
# listed here lands in HEAD.
SECTION_BY_ATTRPATH: tuple[tuple[str, str], ...] = (
    ("services.xserver.xkb",        "I18N"),
    ("services.pipewire",           "AUDI"),
    ("services.pulseaudio",         "AUDI"),
    ("security.rtkit",              "AUDI"),
    ("services.displayManager",     "DESK"),
    ("services.desktopManager",     "DESK"),
    ("services.xserver",            "DESK"),
    ("services.snapper",            "FSYS"),
    ("services.btrfs",              "FSYS"),
    ("services.smartd",             "FSYS"),
    ("services.flatpak",            "PROG"),
    ("services.openssh",            "NETW"),
    ("boot.tmp",                    "FSYS"),
    ("boot",                        "BOOT"),
    ("i18n",                        "I18N"),
    ("console",                     "I18N"),
    ("time",                        "I18N"),
    ("networking",                  "NETW"),
    ("users",                       "USER"),
    ("fonts",                       "FONT"),
    ("programs",                    "PROG"),
    ("environment",                 "PROG"),
    ("nixpkgs",                     "NIXS"),
    ("nix",                         "NIXS"),
    ("hardware",                    "HDWR"),
    ("virtualisation",              "VIRT"),
    ("fileSystems",                 "FSYS"),
    ("swapDevices",                 "FSYS"),
    ("system",                      "SYST"),
    ("services",                    "SERV"),
)


def section_for_attrpath(path: str) -> str:
    """Section a NixOS attribute path belongs to, HEAD when nothing matches."""
    clean = path.strip().strip('"')
    for prefix, section in SECTION_BY_ATTRPATH:
        if clean == prefix or clean.startswith(prefix + "."):
            return section
    return "HEAD"


# Zone sections: bricks are injected immediately when their section marker is
# encountered in the generated file, rather than being flushed at the next
# regular section boundary.  Used for injection points inside nested blocks
# (e.g. inside inputs = { } or outputs = { } in flake.nix).
ZONE_SECTIONS: frozenset[str] = frozenset({"INPX", "OUTX", "OUTH"})

# German section names written by NiCo 0.12.0 and earlier.  Kept permanently:
# another machine with an older NiCo, a restored backup or a hand-copied marker
# still carries them, and without this map such a brick would count as an
# unknown section and be flushed to the end of the file.
LEGACY_SECTIONS: dict[str, str] = {
    "Start": "HEAD",
    "Boot": "BOOT",
    "System": "SYST",
    "Lokalisierung": "I18N",
    "Netzwerk": "NETW",
    "Services": "SERV",
    "Desktop": "DESK",
    "Audio": "AUDI",
    "Benutzer": "USER",
    "Programme": "PROG",
    "Schriftarten": "FONT",
    "Nix & System": "NIXS",
    "Hardware": "HDWR",
    "Virtualisierung": "VIRT",
    "Dateisystem & Backup": "FSYS",
    "Home Manager": "HOME",
    "End": "TAIL",
    "Inputs-Extra": "INPX",
    "Outputs-Extra": "OUTX",
    "Outputs-Hosts": "OUTH",
    # UI aliases of the move dialog
    "Inputs": "INPX",
    "Outputs": "OUTX",
    "Hosts": "OUTH",
}

_HOST_PREFIX     = "HOST:"
_CODE_RE         = re.compile(r"^[A-Z0-9]{4}(?::[A-Za-z0-9_.-]+)?$")
_LEGACY_HOST_RE  = re.compile(r"^Host:\s*(\S+)$")


def normalize_section(name: str) -> str:
    """
    Map any section spelling to its code.

    Accepts codes (``FSYS``, ``HOST:martin``), the German names written by
    older NiCo versions, and the legacy ``Host: martin`` form.  An unknown
    name is returned unchanged – it then sorts before TAIL like before, and
    nothing is lost.
    """
    name = (name or "").strip()
    if _CODE_RE.match(name):
        return name
    m = _LEGACY_HOST_RE.match(name)
    if m:
        return _HOST_PREFIX + m.group(1)
    return LEGACY_SECTIONS.get(name, name)


def section_file_label(code: str) -> str:
    """English text written after the code into the .nix section header."""
    code = normalize_section(code)
    if code.startswith(_HOST_PREFIX):
        return f"Host {code[len(_HOST_PREFIX):]}"
    entry = SECTIONS.get(code)
    return entry[0] if entry else code


def section_i18n_key(code: str) -> str:
    """i18n key the UI uses to show this section in the user's language."""
    code = normalize_section(code)
    if code.startswith(_HOST_PREFIX):
        return "sec.host"
    entry = SECTIONS.get(code)
    return entry[1] if entry else ""


def detect_content_format(content: str) -> int:
    """
    Format the markers in *content* actually use.

    Read from the file instead of trusting its header: a body NiCo has just
    regenerated is format 2 even when the old header said 1, and a file that
    was only re-stamped keeps the format its markers still have.

      1  at least one German section name or `Host: name`
      2  every marker carries a section code
      FILE_FORMAT  no NiCo markers at all – nothing that could be stale
    """
    raw = [m.group(1).strip() for m in _START.finditer(content)]
    for line in content.splitlines():
        m = _SECTION_HDR.search(line)
        if not m:
            continue
        text = m.group(1)
        raw.append(text.split(SECTION_HDR_SEP, 1)[0].strip()
                   if SECTION_HDR_SEP in text else text.strip())
    if not raw:
        return FILE_FORMAT
    return 1 if any(not _CODE_RE.match(name) for name in raw) else 2


def sections_for_frontend() -> dict:
    """Section tables for /api/sections – keeps the frontend from copying them."""
    return {
        "sections": [{"code": code, "file_label": label, "i18n": key}
                     for code, (label, key) in SECTIONS.items()],
        "legacy":   dict(LEGACY_SECTIONS),
        "order":    list(SECTION_ORDER),
        "zones":    sorted(ZONE_SECTIONS),
        "host_prefix": _HOST_PREFIX,
        "header_sep":  SECTION_HDR_SEP,
    }


def _is_zone_section(section: str) -> bool:
    return section in ZONE_SECTIONS or section.startswith(_HOST_PREFIX)


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
                        "section": "TAIL",
                        "order":   sub_order,
                        "text":    format_brick("TAIL", sub_order, cur_name, sub_body),
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


def find_nested_bricks(nix_content: str) -> list[dict]:
    """
    Report new-format start markers that sit inside an already open brick.

    While inside a block the parser below looks for end markers only, so such a
    marker is swallowed as plain body text: the inner brick vanishes from NiCo
    and dies together with the outer one when that is moved or deleted.  Nix
    itself is unaffected – the markers are comments.

    Legacy `# <brix: name>` markers inside a new-format brick are *not*
    reported: expand_nested_legacy() resolves those on purpose.

    Returns [{"outer": str, "inner": str, "line": int}, ...], lines 1-based.
    """
    findings: list[dict] = []
    current_name: str | None = None

    for lineno, line in enumerate(nix_content.splitlines(), start=1):
        if current_name is None:
            m = _START.search(line)
            if m:
                current_name = m.group(3)
                continue
            m_old = _START_OLD.search(line)
            if m_old:
                current_name = m_old.group(1)
            continue

        m_inner = _START.search(line)
        if m_inner:
            findings.append({
                "outer": current_name,
                "inner": m_inner.group(3),
                "line":  lineno,
            })
            continue

        # Same closing condition as extract_brick_blocks: only the matching
        # name ends the block, regardless of marker format.
        m_end     = _END.search(line)
        m_end_old = _END_OLD.search(line)
        end_name  = (m_end and m_end.group(1)) or (m_end_old and m_end_old.group(1))
        if end_name == current_name:
            current_name = None

    return findings


def new_nested_bricks(before: str, after: str) -> list[dict]:
    """Nestings in `after` that were not already present in `before`.

    Identified by the *inner* brick name alone – neither by line (a pre-existing
    nesting shifts lines on every write) nor by the outer name: renaming or
    moving the outer brick would otherwise make a long-standing nesting look
    newly introduced and get the write refused, which is exactly the lock-out
    this distinction exists to prevent.  Brick names are unique per file, so the
    inner name is a stable identity.

    Used by the write paths to tell "the user just pasted this in" (refuse)
    apart from "it was already on disk" (write, but say so).
    """
    known = {f["inner"] for f in find_nested_bricks(before)}
    return [f for f in find_nested_bricks(after) if f["inner"] not in known]


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
    current_section: str = "TAIL"
    current_order: int = 1
    current_lines: list[str] = []
    is_legacy: bool = False
    legacy_auto_order: int = 1  # used for migrating legacy blocks

    for line in lines:
        if current_name is None:
            m = _START.search(line)
            if m:
                raw_section     = m.group(1).strip()
                current_section = normalize_section(raw_section)
                current_order   = int(m.group(2))
                current_name    = m.group(3)
                current_lines   = [line]
                # A German section name from an older NiCo counts as legacy too:
                # rebuilding the text rewrites the marker with the section code
                is_legacy       = current_section != raw_section
            else:
                m_old = _START_OLD.search(line)
                if m_old:
                    current_name    = m_old.group(1)
                    current_section = "TAIL"
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
# Header text is "CODE · English name"; files from older NiCo carry the German
# name alone, which normalize_section() maps to its code.
SECTION_HDR_SEP = "·"


def section_of_header(text: str) -> str:
    """Section code from the captured text of a section header line."""
    head = text.split(SECTION_HDR_SEP, 1)[0].strip() if SECTION_HDR_SEP in text else text.strip()
    return normalize_section(head)


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
        sec = normalize_section(block.get("section", ""))
        block = {**block, "section": sec}
        if _is_zone_section(sec):
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
            section_name = section_of_header(m.group(1))
            if _is_zone_section(section_name):
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


def check_bracket_balance(body: str) -> list[str]:
    """Check if {}, [], () are balanced in Nix code, ignoring strings and comments.
    Returns a list of error strings; empty list means balanced."""
    counts = {'{': 0, '[': 0, '(': 0}
    closers = {'}': '{', ']': '[', ')': '('}
    in_string = False
    in_indent = False
    in_comment = False
    i = 0
    while i < len(body):
        ch = body[i]
        if in_comment:
            if ch == '\n':
                in_comment = False
            i += 1
            continue
        if in_indent:
            if body.startswith("''", i):
                in_indent = False
                i += 2
            else:
                i += 1
            continue
        if in_string:
            if ch == '\\' and i + 1 < len(body):
                i += 2
            elif ch == '"':
                in_string = False
                i += 1
            else:
                i += 1
            continue
        if body.startswith("''", i):
            in_indent = True
            i += 2
            continue
        if ch == '#':
            in_comment = True
            i += 1
            continue
        if ch == '"':
            in_string = True
            i += 1
            continue
        if ch in counts:
            counts[ch] += 1
        elif ch in closers:
            counts[closers[ch]] -= 1
        i += 1

    errors = []
    for opener, closer in [('{', '}'), ('[', ']'), ('(', ')')]:
        n = counts[opener]
        if n > 0:
            errors.append(f"{n}× nicht geschlossene `{opener}`")
        elif n < 0:
            errors.append(f"{-n}× überschüssige `{closer}`")
    return errors


def next_order_for_section(blocks: dict[str, dict], section: str) -> int:
    """Return the next free order number for a given section."""
    orders = [b["order"] for b in blocks.values() if b["section"] == section]
    return max(orders, default=0) + 1


def brix_content_to_bricks(
    brix_content: str,
    section: str = "HEAD",
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
