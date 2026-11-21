"""
nix_parser.py – AST-based parser for NixOS configuration files.

Requires tree-sitter + tree-sitter-nix grammar from nixpkgs:
  python3Packages.tree-sitter + tree-sitter-grammars.tree-sitter-nix

The grammar .so path is read from TREE_SITTER_NIX_GRAMMAR (set automatically
in shell.nix via shellHook).  Falls back gracefully when unavailable.
"""
from __future__ import annotations

import os
import re
import warnings
from dataclasses import dataclass, field

# ── Constants ──────────────────────────────────────────────────────────────────

GRAMMAR_ENV = "TREE_SITTER_NIX_GRAMMAR"

#: Top-level attrpath prefixes that NiCo knows about.
KNOWN_PREFIXES: frozenset[str] = frozenset({
    "networking", "services", "users", "boot", "i18n", "hardware",
    "fonts", "nix", "time", "console", "environment", "nixpkgs",
    "system", "programs", "virtualisation", "imports",
})

_RE_BRIX_START = re.compile(r"#\s*<brix:\s*([\w.-]+)(?:\s+(inactive))?\s*>")
_RE_BRIX_END   = re.compile(r"#\s*</brix:\s*([\w.-]+)(?:\s+inactive)?\s*>")

#: A plain Nix identifier, i.e. an attrpath component that needs no quoting.
_RE_ATTR_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_'-]*$")


def _split_attrpath(text: str) -> list[str]:
    """Split a dotted attrpath, ignoring dots inside "..." and ${...}."""
    parts: list[str] = []
    buf: list[str] = []
    depth  = 0
    in_str = False
    esc    = False
    for ch in text:
        if in_str:
            buf.append(ch)
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
        elif ch == "." and depth == 0:
            parts.append("".join(buf).strip())
            buf = []
            continue
        buf.append(ch)
    parts.append("".join(buf).strip())
    return parts


def normalize_attrpath(text: str) -> str:
    """Canonical dotted form of an attrpath: drop quotes a component does not need.

    `users.users."nico"` and `users.users.nico` are the same attribute in Nix,
    but the raw source text differs. Everything downstream compares keys as
    strings, so the quoted spelling used to be carried into the data – and the
    generator then quoted it again on write, one layer per save.

    Only quotes around a plain identifier are removed. `a."b.c"` keeps them,
    because `a.b.c` would mean something else (nested attributes instead of one
    attribute named "b.c").
    """
    out: list[str] = []
    for part in _split_attrpath(text):
        if len(part) >= 2 and part.startswith('"') and part.endswith('"'):
            inner = re.sub(r"\\(.)", r"\1", part[1:-1])
            if _RE_ATTR_IDENT.match(inner):
                out.append(inner)
                continue
        out.append(part)
    return ".".join(out)

# ── Data classes ───────────────────────────────────────────────────────────────

@dataclass
class Binding:
    """One top-level attribute assignment in the NixOS config."""
    key: str          # attrpath as dotted string, e.g. "networking.hostName"
    value_text: str   # raw Nix source of the value (right-hand side only)
    full_text: str    # entire binding source "key = value;"
    start_line: int   # 1-based
    end_line: int

    @property
    def prefix(self) -> str:
        return self.key.split(".")[0]

    @property
    def is_known(self) -> bool:
        return self.prefix in KNOWN_PREFIXES


@dataclass
class BrixBlock:
    """A Nix-Brix block delimited by # <brix: name> … # </brix: name>."""
    name: str
    content_lines: list[str]   # raw source lines between the markers
    start_line: int            # line of the opening marker (1-based)
    end_line: int              # line of the closing marker (1-based)
    inactive: bool = False     # True when marked as inactive


@dataclass
class ParseResult:
    known: list[Binding] = field(default_factory=list)
    unknown: list[Binding] = field(default_factory=list)
    brix_blocks: list[BrixBlock] = field(default_factory=list)
    has_syntax_error: bool = False
    errors: list[str] = field(default_factory=list)
    available: bool = True   # False when tree-sitter/grammar are missing

# ── Grammar loading (lazy, cached) ────────────────────────────────────────────

_lang = None


def _load_language():
    global _lang
    if _lang is not None:
        return _lang
    grammar_path = os.environ.get(GRAMMAR_ENV)
    if not grammar_path or not os.path.exists(grammar_path):
        return None
    try:
        import ctypes
        import tree_sitter
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            lib = ctypes.cdll.LoadLibrary(grammar_path)
            lib.tree_sitter_nix.restype = ctypes.c_void_p
            _lang = tree_sitter.Language(lib.tree_sitter_nix())
        return _lang
    except Exception:
        return None

# ── Public API ─────────────────────────────────────────────────────────────────

def is_available() -> bool:
    """Return True if tree-sitter + grammar are usable."""
    return _load_language() is not None


def parse(content: str) -> ParseResult:
    """
    Parse a NixOS configuration file into known/unknown bindings and brix blocks.

    Returns a ParseResult.  When tree-sitter is unavailable, returns a stub
    result with available=False so callers can fall back to the regex importer.
    """
    lang = _load_language()
    if lang is None:
        return ParseResult(available=False,
                           errors=["tree-sitter-nix grammar not available – "
                                   f"set {GRAMMAR_ENV} env var (see shell.nix)"])
    try:
        import tree_sitter
        parser = tree_sitter.Parser(lang)
    except Exception as exc:
        return ParseResult(available=False, errors=[str(exc)])

    src_bytes = content.encode("utf-8")
    tree = parser.parse(src_bytes)
    result = ParseResult(has_syntax_error=tree.root_node.has_error)

    if tree.root_node.has_error:
        result.errors.append("Nix syntax error detected in source")

    binding_set = _find_first(tree.root_node, "binding_set")
    if binding_set is None:
        result.errors.append("No top-level attribute set found")
        return result

    _walk_binding_set(binding_set, src_bytes, result)
    return result

def parse_tree(content: str):
    """The raw tree-sitter tree of `content`, or None without tree-sitter.

    For callers that need more than the top-level bindings parse() collects –
    the comment rescue (importer.collect_notes) walks every comment node.
    """
    lang = _load_language()
    if lang is None:
        return None
    try:
        import tree_sitter
        return tree_sitter.Parser(lang).parse(content.encode("utf-8"))
    except Exception:
        return None


# ── Active assignments (whole file, nested) ───────────────────────────────────

@dataclass(frozen=True)
class Assignment:
    """One active assignment, with its full attribute path.

    `users.users.anna = { uid = 1000; }` and `users.users.anna.uid = 1000;`
    both give path ("users", "users", "anna", "uid"). The block itself is an
    assignment too, so a rule can find where a user starts.
    """
    path: tuple        # components in normalized form (quotes only where needed)
    value_text: str    # raw Nix source of the right-hand side
    line: int          # 1-based line of the binding

    @property
    def key(self) -> str:
        return ".".join(self.path)


# Values whose binding sets are attributes of the same path. Descended so
# that what NixOS merges is seen: mkIf/mkMerge/mkForce, `//`, if/then/else,
# with, let … in. Not descended: strings and a let's own bindings; a
# function's body starts a new path (_collect_assignments).
_ASSIGN_SKIP = frozenset({"string_expression", "indented_string_expression",
                          "formals", "comment"})

_assign_cache: dict = {}
_ASSIGN_CACHE_MAX = 256


def assignments(content: str) -> tuple:
    """Every active assignment of a .nix file, nested blocks flattened.

    Comments are not code to tree-sitter, so a commented-out line and a
    deactivated Nix-Brick (its body commented out) are never part of it;
    active Nix-Brix are ordinary bindings and are. The values stay text:
    `uid = 1234 + 1` is an expression, not 1234 - extract_int says None.

    Without tree-sitter a simpler fallback runs on the text with comments
    blanked (plain `{ … }` blocks only). Remembered per content: the
    Validator reads the same file once per rule.
    """
    hit = _assign_cache.get(content)
    if hit is not None:
        return hit
    tree = parse_tree(content)
    if tree is None:
        result = tuple(_rx_assignments(content))
    else:
        out: list = []
        src = content.encode("utf-8")
        root = tree.root_node
        # The file itself may be a function ({ pkgs, ... }: { … }) - that body
        # is the module, unlike a function standing as a value.
        while root is not None and root.type in ("source_code", "function_expression"):
            body = None
            for ch in root.children:
                if root.type == "source_code" and ch.type != "comment":
                    body = ch
                elif root.type == "function_expression" and ch.type not in (
                        "formals", ":", "identifier", "@", "comment"):
                    body = ch
            root = body
        if root is not None:
            _collect_assignments(root, (), src, out)
        result = tuple(out)
    if len(_assign_cache) >= _ASSIGN_CACHE_MAX:
        _assign_cache.pop(next(iter(_assign_cache)))
    _assign_cache[content] = result
    return result


def _collect_assignments(node, prefix: tuple, src: bytes, out: list) -> None:
    t = node.type
    if t in _ASSIGN_SKIP:
        return
    if t == "binding_set":
        for b in node.children:
            if b.type != "binding":
                continue
            attr = value = None
            for ch in b.children:
                if ch.type == "attrpath":
                    attr = ch
                elif ch.type not in ("=", ";", "comment") and attr is not None and value is None:
                    value = ch
            if attr is None:
                continue
            path = prefix + tuple(_split_attrpath(normalize_attrpath(
                _node_source(attr, src).strip())))
            out.append(Assignment(path, _node_source(value, src), b.start_point[0] + 1))
            if value is not None:
                _collect_assignments(value, path, src, out)
        return
    if t == "function_expression":
        # A function standing as a value is a module ({ config, ... }: { … }
        # in a flake's modules list, flake-parts) or a template (genAttrs
        # names (name: { … })). Its body is read from the top, like a file:
        # a module's options are found, and a template's attributes do not
        # turn into users or options under the outer path.
        kids = [c for c in node.children
                if c.type not in ("formals", ":", "identifier", "@", "comment")]
        if kids:
            _collect_assignments(kids[-1], (), src, out)
        return
    if t == "let_expression":
        # Only the body: the let's own bindings are local names, not options.
        kids = [c for c in node.children if c.type not in ("let", "in", "comment")]
        if kids and kids[-1].type != "binding_set":
            _collect_assignments(kids[-1], prefix, src, out)
        return
    for ch in node.children:
        _collect_assignments(ch, prefix, src, out)


def _rx_assignments(content: str) -> list:
    """Fallback for assignments() without tree-sitter: comments blanked,
    statements split at depth 0, plain `{ … }` values descended."""
    from .importer import _blank_comments, _split_top_level_stmts
    text = _blank_comments(content)
    body = re.sub(r'^\s*\{[^}]*\}\s*:\s*', '', text, flags=re.DOTALL).strip()
    if body.startswith("{") and body.endswith("}"):
        body = body[1:-1]
    out: list = []

    def walk(part: str, prefix: tuple) -> None:
        for stmt in _split_top_level_stmts(part):
            m = re.match(r'\s*([\w."\-]+)\s*=\s*(.*?)\s*;?\s*$', stmt, re.DOTALL)
            if not m:
                continue
            path = prefix + tuple(_split_attrpath(normalize_attrpath(m.group(1))))
            value = m.group(2)
            pos = text.find(stmt.strip())
            out.append(Assignment(path, value, text.count("\n", 0, pos) + 1 if pos >= 0 else 0))
            inner = extract_inner_block(value)
            if inner is not None:
                walk(inner, path)

    walk(body, ())
    return out


_RE_PRIO_WRAP = re.compile(
    r'^\s*(?:lib\s*\.\s*)?(mkDefault|mkForce|mkOverride\s+\d+)\s+(.*?)\s*$',
    re.DOTALL)


def unwrap_priority(value_text: str) -> tuple:
    """`lib.mkForce false` → ("false", "mkForce"); a plain value → (value, "").
    For mkOverride the name alone, as the Validator reports it."""
    m = _RE_PRIO_WRAP.match(value_text or "")
    if not m:
        return (value_text or "").strip(), ""
    return m.group(2).strip(), m.group(1).split()[0]


def body_shape(content: str) -> str | None:
    """How a module file is built: 'plain', 'other', or None without tree-sitter.

    'plain' is the one form NiCo can write back: an attribute set, optionally
    behind a `{ formals }:` head. Everything else changes its meaning when the
    file is regenerated, because the generator always writes `{ args }: {`:

      { … }: let x = 1; in { … }   the let bindings are gone, `x` is undefined
      { … }: lib.mkIf c { … }      the condition is gone
      { … }: with lib; { … }       `mkDefault` and friends are undefined
      { … }@args: { … }            `args` is undefined
      { … }: rec { … }             self references are undefined

    parse() took the first binding set anywhere in the tree, which for `let`
    is the helper bindings and not the config (revision 2026-11-10, B33).

    Only the form is judged. A syntax error inside an otherwise plain body
    still gives 'plain'; a file broken so badly that no form is visible gives
    'other', because regenerating from a parse that failed loses whatever the
    parser could not place.
    """
    lang = _load_language()
    if lang is None:
        return None
    try:
        import tree_sitter
        tree = tree_sitter.Parser(lang).parse(content.encode("utf-8"))
    except Exception:
        return None

    node = tree.root_node.child_by_field_name("expression")
    if node is None:
        return "plain"
    if node.type == "function_expression":
        if node.child_by_field_name("universal") is not None:
            return "other"
        node = node.child_by_field_name("body")
        if node is None:
            return "plain"
    return "plain" if node.type == "attrset_expression" else "other"


# ── Tree walking helpers ───────────────────────────────────────────────────────

def _find_first(node, node_type: str):
    """DFS search for the first node of a given type."""
    if node.type == node_type:
        return node
    for child in node.children:
        found = _find_first(child, node_type)
        if found:
            return found
    return None


def _walk_binding_set(bs_node, src_bytes: bytes, result: ParseResult) -> None:
    """
    Walk binding_set children in order.
    Comments that contain brix markers delimit BrixBlock groups.
    All other bindings are classified as known or unknown.
    """
    children = bs_node.children
    i = 0
    while i < len(children):
        child = children[i]

        if child.type == "comment":
            text = child.text.decode("utf-8")
            m_start = _RE_BRIX_START.search(text)
            if m_start:
                brix_name = m_start.group(1)
                inactive  = bool(m_start.group(2))
                start_line = child.start_point[0] + 1
                content_lines: list[str] = []
                i += 1
                while i < len(children):
                    inner = children[i]
                    inner_text = inner.text.decode("utf-8")
                    if inner.type == "comment" and _RE_BRIX_END.search(inner_text):
                        result.brix_blocks.append(BrixBlock(
                            name=brix_name,
                            content_lines=content_lines,
                            start_line=start_line,
                            end_line=inner.start_point[0] + 1,
                            inactive=inactive,
                        ))
                        break
                    content_lines.append(_node_source(inner, src_bytes))
                    i += 1
                # fall through; i points at end marker, outer i += 1 skips it

        elif child.type == "binding":
            b = _parse_binding(child, src_bytes)
            if b:
                (result.known if b.is_known else result.unknown).append(b)

        elif child.type == "inherit":
            full = _node_source(child, src_bytes)
            result.unknown.append(Binding(
                key="inherit",
                value_text=full,
                full_text=full,
                start_line=child.start_point[0] + 1,
                end_line=child.end_point[0] + 1,
            ))

        i += 1


def _parse_binding(node, src_bytes: bytes) -> Binding | None:
    """Extract key and value from a tree-sitter binding node."""
    attrpath = None
    value_node = None
    for child in node.children:
        if child.type == "attrpath":
            attrpath = child.text.decode("utf-8").strip()
        elif child.type not in ("=", ";") and attrpath is not None and value_node is None:
            value_node = child
    if attrpath is None:
        return None
    return Binding(
        key=normalize_attrpath(attrpath),
        value_text=_node_source(value_node, src_bytes),
        full_text=_node_source(node, src_bytes),
        start_line=node.start_point[0] + 1,
        end_line=node.end_point[0] + 1,
    )


def _node_source(node, src_bytes: bytes) -> str:
    if node is None:
        return ""
    return src_bytes[node.start_byte:node.end_byte].decode("utf-8")


# ── Value extraction helpers (operate on raw value_text strings) ───────────────

# A string the generator can write back unchanged: no escapes and no `${…}`.
# An interpolation used to pass, and generator.nix_esc then escaped it –
# `"${prefix}-pc"` came back as `"\${prefix}-pc"`, a different value
# (revision 2026-11-10, B36).
PLAIN_STRING = r'"((?:[^"\\$]|\$(?!\{))*)"'
_PLAIN_STRING_RE = re.compile(r'^\s*' + PLAIN_STRING + r'\s*$')
_SIMPLE_NAME_RE = re.compile(r"[A-Za-z_][\w.'+-]*")


def extract_string(value_text: str) -> str | None:
    """'\"foo\"' → 'foo', None if not a plain string (escapes, `${…}`)."""
    m = _PLAIN_STRING_RE.match(value_text.strip())
    return m.group(1) if m else None


def extract_string_list(value_text: str) -> list[str] | None:
    """'[ "a" "b" ]' → ['a', 'b']; None unless it is exactly a list of plain
    strings. `[ "a" ] ++ more`, comments and interpolations all yield None."""
    v = value_text.strip()
    if not re.fullmatch(r'\[\s*(?:' + PLAIN_STRING + r'\s*)*\]', v):
        return None
    return [m.group(1) for m in re.finditer(PLAIN_STRING, v)]


def extract_simple_list(value_text: str, with_name: str | None = "pkgs") -> list[str] | None:
    """Entries of '[ a b.c ]' or 'with pkgs; [ a b ]' when every entry is a
    simple name; None otherwise.

    The predecessor (extract_identifier_list) took every word it found:
    `(python3.withPackages (ps: with ps; [ requests ]))` became the packages
    `python3.withPackages`, `ps`, `with`, `requests`, and the list written
    back was not even valid Nix. A list that holds anything but simple names
    is not this function's business – the caller leaves the binding alone and
    it survives as a Nix-Brick (revision 2026-11-10, B36).

    with_name: the only `with X;` prefix accepted (the generator writes
    `with pkgs;`); None accepts no prefix at all.
    """
    v = value_text.strip()
    m = re.match(r'with\s+([\w.]+)\s*;\s*(\[.*\])\s*$', v, re.DOTALL)
    if m:
        if with_name is None or m.group(1) != with_name:
            return None
        v = m.group(2)
    if not (v.startswith('[') and v.endswith(']')):
        return None
    inner = re.sub(r'/\*.*?\*/', ' ', v[1:-1], flags=re.DOTALL)
    inner = re.sub(r'#[^\n]*', ' ', inner)
    tokens = inner.split()
    if any(not _SIMPLE_NAME_RE.fullmatch(t) for t in tokens):
        return None
    return tokens


def split_list_elements(value_text: str) -> list[str] | None:
    """Top-level elements of '[ a (b c) ./d.nix ]', each verbatim.

    None when the value is not one plain list (`++`, a `with` prefix), holds
    a comment, or its brackets do not balance.
    """
    v = value_text.strip()
    if not (v.startswith('[') and v.endswith(']')):
        return None
    inner = v[1:-1]
    elems, depth, start, i, n = [], 0, None, 0, len(inner)
    while i < n:
        ch = inner[i]
        if ch == '#' or inner.startswith('/*', i):
            return None
        if ch == '"':
            if start is None:
                start = i
            i += 1
            while i < n and inner[i] != '"':
                i += 2 if inner[i] == '\\' else 1
            if i >= n:
                return None
        elif inner.startswith("''", i):
            if start is None:
                start = i
            end = inner.find("''", i + 2)
            if end < 0:
                return None
            i = end + 1
        elif ch in '([{':
            if start is None:
                start = i
            depth += 1
        elif ch in ')]}':
            depth -= 1
            if depth < 0:
                return None
        elif ch.isspace():
            if depth == 0 and start is not None:
                elems.append(inner[start:i])
                start = None
        elif start is None:
            start = i
        i += 1
    if depth != 0:
        return None
    if start is not None:
        elems.append(inner[start:])
    # `a ++ b` inside the brackets is one expression, not two elements.
    if any(e in ('++', '//') for e in elems):
        return None
    return elems


def extract_bool(value_text: str) -> bool | None:
    """'true'/'false' → True/False, None otherwise."""
    v = value_text.strip()
    if v == 'true':  return True
    if v == 'false': return False
    return None


def extract_int(value_text: str) -> int | None:
    """'42' → 42, None otherwise."""
    m = re.match(r'^\s*(\d+)\s*$', value_text.strip())
    return int(m.group(1)) if m else None


def extract_inner_block(value_text: str) -> str | None:
    """'{ ... }' → '...' (content without outer braces), None if not a block."""
    v = value_text.strip()
    if v.startswith('{') and v.endswith('}'):
        return v[1:-1]
    return None


def extract_identifier_list(value_text: str) -> list[str] | None:
    """
    Extract identifiers from 'with pkgs; [ a b c ]' or '[ "x" "y" ]'.
    Returns list of names, or None if value_text doesn't look like a list.
    """
    v = value_text.strip()
    m = re.match(r'with\s+\w+\s*;\s*\[(.*)\]\s*$', v, re.DOTALL)
    inner = m.group(1) if m else (v[1:-1] if v.startswith('[') else None)
    if inner is None:
        return None
    cleaned = re.sub(r'#[^\n]*', '', inner)
    return re.findall(r'\b([a-zA-Z][a-zA-Z0-9_.\\-]*)\b', cleaned)


def make_kv(result: 'ParseResult') -> dict[str, str]:
    """Build a flat key→value_text dict from all bindings in a ParseResult."""
    return {b.key: b.value_text for b in result.known + result.unknown}
