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
        key=attrpath,
        value_text=_node_source(value_node, src_bytes),
        full_text=_node_source(node, src_bytes),
        start_line=node.start_point[0] + 1,
        end_line=node.end_point[0] + 1,
    )


def _node_source(node, src_bytes: bytes) -> str:
    if node is None:
        return ""
    return src_bytes[node.start_byte:node.end_byte].decode("utf-8")
