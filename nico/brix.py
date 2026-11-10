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

# The trailing "inactive" marks a brick whose body is commented out: the
# content stays in the file, Nix never sees it. Spelled as a word, not a
# symbol, because a person reading the .nix has to understand it without a
# legend - and because nix_parser.py already used this spelling for the
# legacy marker. The name group cannot swallow it: it is separated by
# whitespace, which [\w\-] does not match.
_START     = re.compile(r"#\s*<brick:\s*([^/]+?)\s*/\s*#(\d+)\s+([\w\-]+)(\s+inactive)?\s*>")
_END       = re.compile(r"#\s*</brick:\s*([\w\-]+)(?:\s+inactive)?\s*>")
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
#   3                         brick markers may carry the "inactive" flag
#
# A file only reaches 3 once it actually holds a deactivated brick: without
# one it stays 2 and an older NiCo keeps working on it normally. That is the
# point of deriving the number from the content instead of the release.
FILE_FORMAT = 3
SUPPORTED_FORMATS = frozenset({1, 2, 3})

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


def valid_section(name: str) -> str | None:
    """The section code for a name that came in from outside, None if unusable.

    A section travels from the browser into a marker line. Anything is
    accepted that normalises to a code; a line break or a slash would tear the
    marker apart, and the brick behind it would stop being one.
    """
    code = normalize_section(name if isinstance(name, str) else "")
    return code if _CODE_RE.fullmatch(code) else None


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
      3  at least one brick is flagged inactive
      FILE_FORMAT  no NiCo markers at all – nothing that could be stale
    """
    # Checked first and wins over the legacy test below: a number must never
    # be stamped down. A file carrying both a German section name and an
    # inactive flag is format 3; the German markers still get migrated,
    # because extract_brick_blocks decides that per brick, not per file.
    if any(m.group(4) for m in _START.finditer(content)):
        return 3
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
                        "section":  "TAIL",
                        "order":    sub_order,
                        "text":     format_brick("TAIL", sub_order, cur_name, sub_body),
                        "inactive": False,
                    }
                    sub_order += 1
                    cur_name  = None
                    cur_lines = []
                else:
                    cur_lines.append(line)

        remaining = "".join(non_brix).strip()
        if remaining:
            result[bname] = {
                "section":  bblock["section"],
                "order":    bblock["order"],
                "text":     format_brick(bblock["section"], bblock["order"], bname,
                                         remaining, bblock.get("inactive", False)),
                "inactive": bblock.get("inactive", False),
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
    start_idx = 0       # line index of the start marker of the open brick
    mark = 0            # len(findings) when that brick was opened

    lines = nix_content.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        if current_name is None:
            m = _START.search(line)
            m_old = None if m else _START_OLD.search(line)
            if m or m_old:
                current_name = m.group(3) if m else m_old.group(1)
                start_idx, mark = i, len(findings)
        else:
            m_inner = _START.search(line)
            if m_inner:
                findings.append({
                    "outer": current_name,
                    "inner": m_inner.group(3),
                    "line":  i + 1,
                })
            else:
                # Same closing condition as extract_brick_blocks: only the
                # matching name ends the block, regardless of marker format.
                m_end     = _END.search(line)
                m_end_old = _END_OLD.search(line)
                end_name  = (m_end and m_end.group(1)) or (m_end_old and m_end_old.group(1))
                if end_name == current_name:
                    current_name = None
        i += 1
        if i == len(lines) and current_name is not None:
            # Never closed: not a brick, so nothing sat "inside" it either.
            # Same rule as extract_brick_blocks.
            del findings[mark:]
            current_name = None
            i = start_idx + 1

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
      {name: {"section": str, "order": int, "text": full_text_with_markers,
              "inactive": bool}}
    Supports both new format (# <brick: Section / #N name>) and legacy format
    (# <brix: name>). Legacy blocks are automatically converted to new format
    so they get migrated on the next write-back.
    New-format bricks whose body contains nested legacy sub-blocks are
    expanded into individual bricks (see expand_nested_legacy).
    A start marker that is never closed is not a brick: it is skipped and the
    search goes on right behind it. Its content is plain file text then –
    strip_brick_blocks leaves it in place by the same rule. The parser used
    to stay "inside" such a block until the end of the file, so one typo in
    an end marker hid every brick behind it, and the stripping counterpart
    dropped everything behind it (revision 2026-11-10, B32).
    """
    blocks: dict[str, dict] = {}
    lines = nix_content.splitlines(keepends=True)

    current_name: str | None = None
    current_section: str = "TAIL"
    current_order: int = 1
    current_lines: list[str] = []
    current_inactive: bool = False
    is_legacy: bool = False
    legacy_auto_order: int = 1  # used for migrating legacy blocks
    start_idx = 0               # line index of the start marker of the open block

    i = 0
    while i < len(lines):
        line = lines[i]
        i += 1
        if current_name is None:
            m = _START.search(line)
            if m:
                raw_section     = m.group(1).strip()
                current_section = normalize_section(raw_section)
                current_order   = int(m.group(2))
                current_name    = m.group(3)
                current_inactive = bool(m.group(4))
                current_lines   = [line]
                start_idx       = i - 1
                # A German section name from an older NiCo counts as legacy too:
                # rebuilding the text rewrites the marker with the section code
                is_legacy       = current_section != raw_section
            else:
                m_old = _START_OLD.search(line)
                if m_old:
                    current_name    = m_old.group(1)
                    current_section = "TAIL"
                    current_order   = legacy_auto_order
                    current_inactive = False
                    current_lines   = [line]
                    start_idx       = i - 1
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
                    text = format_brick(current_section, current_order, current_name,
                                        body, current_inactive)
                    legacy_auto_order += 1
                else:
                    text = "".join(current_lines)

                blocks[current_name] = {
                    "section":  current_section,
                    "order":    current_order,
                    "text":     text,
                    "inactive": current_inactive,
                }
                current_name     = None
                current_lines    = []
                current_inactive = False
                is_legacy        = False

        if i == len(lines) and current_name is not None:
            # Reached the end inside a block: its start marker has no partner.
            # Forget it and look again from the line behind it.
            current_name     = None
            current_lines    = []
            current_inactive = False
            is_legacy        = False
            i = start_idx + 1

    return expand_nested_legacy(blocks)


def strip_brick_blocks(nix_content: str) -> str:
    """Remove all brick blocks (including their marker lines) from nix_content.
    Handles both new format (# <brick:) and legacy format (# <brix:).
    Only exits a block when the end marker with the same name is found.
    Orphaned end markers (no matching start) are also stripped.

    The blank line that follows a block goes with it. inject_brick_blocks
    writes one as a separator behind every brick, so leaving it here made
    each take-apart-and-reassemble cycle (rename, move, split, deactivate)
    add one more: Martins flake.nix collected 48 of them in blocks of 8 to 13
    on 2026-10-30 and it looked like NiCo scribbling in the file. Stripping
    and injecting have to be symmetrical, or the file never settles.
    Found 2026-11-01.

    A start marker that is never closed takes only itself away, never what
    follows it. Skipping "until the matching end marker" meant skipping to the
    end of the file when that marker was missing or misspelt, and every write
    path that regenerates a file from the stripped text then made the loss
    permanent: a typo in one end marker left a configuration.nix with nothing
    but its skeleton (revision 2026-11-10, B32). The marker line goes like any
    orphaned marker, the lines behind it are read again as ordinary text."""
    lines = nix_content.splitlines(keepends=True)
    result: list[str] = []
    in_brick   = False
    brick_name = None
    eat_blank  = False  # the separator right after a block we just removed
    start_idx  = 0      # line index of the start marker of the open block

    i = 0
    while True:
        while i < len(lines):
            line = lines[i]
            i += 1
            if not in_brick:
                if eat_blank:
                    eat_blank = False
                    if not line.strip():
                        continue
                m = _START.search(line)
                m_old = None if m else _START_OLD.search(line)
                if m or m_old:
                    in_brick   = True
                    brick_name = m.group(3) if m else m_old.group(1)
                    start_idx  = i - 1
                    continue
                # Strip orphaned end markers that have no matching start
                if _END.search(line) or _END_OLD.search(line):
                    continue
                result.append(line)
            else:
                # Inside a block: only the matching name ends it, in either
                # marker format - the same condition extract_brick_blocks
                # uses. The two must agree on where a block ends, or one
                # keeps as text what the other returns as a brick.
                m_end     = _END.search(line)
                m_end_old = _END_OLD.search(line)
                end_name  = (m_end and m_end.group(1)) or (m_end_old and m_end_old.group(1))
                if end_name == brick_name:
                    in_brick, brick_name = False, None
                    eat_blank = True
                # All other lines inside a block are skipped
        if not in_brick:
            break
        # End of file inside a block: its start marker has no partner. Nothing
        # was appended since that marker, so only the scan position moves back.
        in_brick, brick_name = False, None
        i = start_idx + 1
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

    # A zone brick is placed at its section header and nowhere else. Without
    # that header in the file it used to be left out of the result without a
    # word - a host brick whose host is not in the outputs, a brick moved to
    # OUTX in a file that has no such zone (revision 2026-11-10, B38). Such a
    # brick is treated like one with an unknown section instead: it goes in
    # front of the closing brace, marker and section unchanged, so it finds
    # its zone again as soon as the header is back.
    zones_present = set()
    for line in nix_content.splitlines():
        m = _SECTION_HDR.search(line)
        if m:
            zones_present.add(section_of_header(m.group(1)))

    # Separate zone bricks (immediate injection) from regular bricks
    zone_bricks: dict[str, list[dict]] = {}
    regular_blocks: dict[str, dict] = {}
    for name, block in blocks.items():
        sec = normalize_section(block.get("section", ""))
        block = {**block, "section": sec}
        if _is_zone_section(sec) and sec in zones_present:
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


def format_brick(section: str, order: int, name: str, body: str,
                 inactive: bool = False) -> str:
    """Assemble a brick with its marker lines.

    inactive defaults to False so the eleven call sites that create a fresh
    brick stay as they are - a new brick is always active. Only the seven
    that rebuild the marker of an existing brick have to pass the flag on,
    or deactivating a brick and then renaming it would switch it back on.
    """
    # rstrip is not cosmetic. core.brick_body() returns the body *with* its
    # trailing newline, and this function adds one of its own - so every
    # operation that takes a brick apart and puts it back together (rename,
    # move, split, deactivate) used to add one blank line before the end
    # marker. It compounds: Martins flake.nix picked up 48 blank lines in
    # blocks of 8 to 13 on 2026-10-30 (commit 1394f45 of his config), which
    # looked like NiCo scribbling in the file. Found 2026-11-01 while making
    # deactivating byte-exact; normalising here fixes it for every caller at
    # once, and no caller can tell the difference for well-formed input.
    flag = " inactive" if inactive else ""
    return (f"# <brick: {section} / #{order} {name}{flag}>\n"
            f"{body.rstrip(chr(10))}\n# </brick: {name}>\n")


# ── Deactivating a brick ────────────────────────────────────────────────────
# Commenting out beats deleting: the content stays in the file, which is where
# vorgaben.txt wants NixOS data - an archive in JSON would break that rule.
_INACTIVE_PREFIX = "# "


def deactivate_body(body: str) -> str:
    """Comment out every non-empty line of a brick body.

    The prefix goes at column 0, not behind the indentation. That is what
    makes the step reversible byte for byte: removing exactly one prefix
    restores the line whatever it looked like, indentation included, and a
    line that already was a comment becomes "# # foo" and comes back as
    "# foo". Blank lines stay blank - commenting them would add noise that
    activating could not tell from content.
    """
    return "\n".join(line if not line.strip() else _INACTIVE_PREFIX + line
                      for line in body.split("\n"))


def activate_body(body: str) -> str:
    """Undo deactivate_body: strip exactly one comment prefix per line.

    The second branch is for a body somebody edited by hand while it was
    deactivated ("#foo" without the space). Best effort - if the result does
    not parse, the write is refused by the same guard that catches a brick
    breaking the file, so a half-commented body cannot be saved.
    """
    out = []
    for line in body.split("\n"):
        if line.startswith(_INACTIVE_PREFIX):
            out.append(line[len(_INACTIVE_PREFIX):])
        elif line.startswith("#"):
            out.append(line[1:])
        else:
            out.append(line)
    return "\n".join(out)


_BRICK_MARKER_LINE = re.compile(r"^\s*#\s*</?bri[xc]k[: ].*>\s*$")

# An input declaration inside the inputs block: the name, then either a dotted
# path or an assignment. Covers `name.url = …`, `name = { … }` and
# `name.inputs.nixpkgs.follows = …`; a hyphen is a valid Nix identifier
# character, which is why nixpkgs-unstable works as a name at all.
_INPUT_NAME = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_'-]*)\s*(?:\.[A-Za-z_]|=)")


def brick_body(text: str) -> str:
    """The content of a brick without its two marker lines."""
    return "\n".join(l for l in (text or "").splitlines()
                     if not _BRICK_MARKER_LINE.match(l))


def input_names_in_bricks(blocks: dict[str, dict], section: str = "INPX") -> list[str]:
    """Names of the flake inputs declared in the bricks of one section.

    The generator writes the argument list of `outputs` from the inputs it
    manages itself and ends it with `...`, so an input added through a brick
    was accepted but never bound to a name: `...` swallows it silently and any
    use of it failed with `undefined variable`. There was no way through the
    interface to declare an input and then use it (measured on Martins config,
    2026-10-31). Reading the names back out of the bricks closes that.

    Order of first appearance, no duplicates.
    """
    out: list[str] = []
    for block in blocks.values():
        if block.get("section") != section:
            continue
        depth = 0
        for line in brick_body(block.get("text", "")).splitlines():
            # Comments and string contents must not take part: a name only
            # counts at the outermost level, otherwise the `url` of
            # `sops-nix = { url = …; }` is read as an input of its own.
            bare = re.sub(r'"(?:[^"\\]|\\.)*"', '""', re.sub(r"#.*$", "", line))
            if depth == 0:
                m = _INPUT_NAME.match(bare)
                if m and m.group(1) not in out:
                    out.append(m.group(1))
            depth += bare.count("{") + bare.count("[") + bare.count("(")
            depth -= bare.count("}") + bare.count("]") + bare.count(")")
            depth = max(0, depth)
    return out


# The argument set of `outputs` outside any brick: `outputs = { a, b, ... }`,
# optionally followed by `@inputs`. Defaults (`x ? …`) are not handled – such a
# line is left alone.
_OUTPUTS_ARGS = re.compile(r"(\boutputs\s*=\s*\{)([^{}]*)(\})")

# Arguments NiCo writes from its own panel fields; never removed here.
_MANAGED_OUTPUT_ARGS = frozenset(
    {"self", "nixpkgs", "home-manager", "nixos-hardware", "plasma-manager"})


_UNDEFINED_AT = re.compile(r"undefined variable '([^']+)'\s+at\s*\S*?:(\d+):\d+")


def input_still_used(message: str, final: str, old_blocks: dict[str, dict],
                     new_blocks: dict[str, dict]) -> tuple[str, str] | None:
    """(input, brick) if a write was refused because an input it drops is in use.

    Removing an INPX brick also removes the name from `outputs`, so a brick
    still using it fails as `undefined variable`. Nix's message is correct but
    does not tell the user what to do, and its line number counts in the file
    as it would be after the write, not the one on disk (yandex-browser,
    2026-11-08) - so the brick is named instead of the line.

    None when the message is about something else, or the use sits outside any
    brick: then there is no brick to send the user to.
    """
    m = _UNDEFINED_AT.search(message)
    if not m:
        return None
    name = m.group(1)
    if name not in set(input_names_in_bricks(old_blocks)) - set(input_names_in_bricks(new_blocks)):
        return None
    current = None
    for line in final.splitlines()[:int(m.group(2))]:
        if start := _START.search(line):
            current = start.group(3)
        elif _END.search(line):
            current = None
    return (name, current) if current else None


def sync_outputs_args(clean: str, old_blocks: dict[str, dict],
                      new_blocks: dict[str, dict]) -> str:
    """Bring the `outputs` argument list in line with the INPX bricks.

    `clean` is flake.nix with the bricks stripped. The generator derives the
    list from the INPX bricks, but it only runs on a panel or form change; a
    brick write only swaps brick texts. An input added through the brick
    dialog therefore stayed unbound until some unrelated panel field changed,
    and every use of it was refused as `undefined variable` (Martins config,
    solidcraft, 2026-11-07).

    Names of the new bricks that are missing get added before `...`; names
    whose brick is gone (deleted, renamed, deactivated) get removed, except
    the ones NiCo manages itself. Everything else in the list stays as it is.
    Returns `clean` unchanged when the line is not found exactly once, carries
    defaults, or needs no change.
    """
    matches = list(_OUTPUTS_ARGS.finditer(clean))
    if len(matches) != 1:
        return clean
    m = matches[0]
    raw = m.group(2)
    if "?" in raw:
        return clean
    args = [a.strip() for a in raw.split(",") if a.strip()]
    has_ellipsis = "..." in args
    names = [a for a in args if a != "..."]

    new_names = input_names_in_bricks(new_blocks)
    gone = set(input_names_in_bricks(old_blocks)) - set(new_names) - _MANAGED_OUTPUT_ARGS
    wanted = [n for n in names if n not in gone]
    wanted += [n for n in new_names if n not in wanted]
    if wanted == names:
        return clean

    inner = ", ".join(wanted + (["..."] if has_ellipsis else []))
    return clean[:m.start(2)] + f" {inner} " + clean[m.end(2):]


def check_bracket_balance(body: str) -> "list[dict]":
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
            errors.append({"kind": "unclosed", "n": n, "char": opener})
        elif n < 0:
            errors.append({"kind": "extra", "n": -n, "char": closer})
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
