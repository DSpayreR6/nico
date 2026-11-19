"""Does NiCo write back what a file says? A leaf-by-leaf comparison.

The importer ticks off what it reads by *name*, and the rescue of partly
understood blocks also works by name. A value that differs – `openFirewall =
false` against the generator's fixed `true` – passed both unnoticed
(revision 2026-11-18, finding 17). This module compares by full attribute
path *and* value, so the import can test its own result: read, write on
trial, compare (decision 2026-11-19, manifest „Gegenprobe").

Equal are: block and dot form (`a = { b = 1; }` and `a.b = 1`), whitespace
and comments outside strings, the indentation of `'' … ''` strings (Nix
strips it), `with pkgs; [ x ]` against `[ pkgs.x ]`, and the order of the
lists the generator sorts (ORDER_FREE). Everything else counts – also a
dropped `x = false`: whether NixOS then means false depends on other
modules (Plasma switches PipeWire on), so it is kept, not assumed.

`settle` is the trial itself: NiCo creates nothing the user did not write
(Martin, 2026-11-19). A form field whose output differs from the file, or
adds to it, is not taken; the file's own lines stay as Nix-Bricks.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from . import importer as _imp

_PLAIN_ITEM = re.compile(r"[A-Za-z_][\w'-]*(?:\.[A-Za-z_][\w'-]*)*")
# Lists the generator writes sorted; their order carries no meaning there.
ORDER_FREE = (re.compile(r'environment\.systemPackages'),
              re.compile(r'users\.users\.[^.]+\.extraGroups'))
_FUNCTION = re.compile(r'(?:\{[^{}]*\}\s*(?:@\s*[\w\'-]+\s*)?|[\w\'-]+\s*(?:@\s*\{[^{}]*\}\s*)?):(?!/)')
# _strip_nix_comments keeps brick markers; here they are noise.
_MARKER_LINE = re.compile(r'^[ \t]*#\s*</?bri(?:x|ck):.*$', re.MULTILINE)


def _code(text: str) -> str:
    """Text without comments and brick markers."""
    return _MARKER_LINE.sub('', _imp._strip_nix_comments(text))


# ── value normalisation ───────────────────────────────────────────────────────

def _dedent_indented(body: str) -> str:
    """The text of a `'' … ''` string as Nix sees it, indentation removed."""
    lines = body.split('\n')
    if lines and not lines[0].strip():
        lines = lines[1:]
    widths = [len(l) - len(l.lstrip(' ')) for l in lines if l.strip()]
    cut = min(widths) if widths else 0
    return '\n'.join(l[cut:].rstrip() for l in lines).strip('\n')


def _tokens(text: str) -> list[tuple[str, str]]:
    """Split a comment-free value into ('str', literal) and ('code', text) runs."""
    out: list[tuple[str, str]] = []
    i, n, start = 0, len(text), 0
    while i < n:
        if text[i] == '"':
            j = i + 1
            while j < n and text[j] != '"':
                j += 2 if text[j] == '\\' else 1
            out.append(('code', text[start:i]))
            out.append(('str', text[i:j + 1]))
            i = start = j + 1
        elif text.startswith("''", i):
            j = i + 2
            while j < n:
                if text.startswith("'''", j) or text.startswith("''$", j) \
                        or text.startswith("''\\", j):
                    j += 3
                elif text.startswith("''", j):
                    break
                else:
                    j += 1
            out.append(('code', text[start:i]))
            out.append(('str', "''" + _dedent_indented(text[i + 2:j]) + "''"))
            i = start = j + 2
        else:
            i += 1
    out.append(('code', text[start:]))
    return out


def _squash(text: str) -> str:
    """Whitespace outside strings collapsed, none around brackets and `;`."""
    parts = []
    for kind, run in _tokens(text):
        if kind == 'str':
            parts.append(run)
        else:
            run = re.sub(r'\s+', ' ', run)
            run = re.sub(r'\s*([\[\]{}();=:,])\s*', r'\1', run)
            parts.append(run)
    return ''.join(parts).strip()


def _mask_strings(text: str) -> str:
    """String bodies (quotes included) replaced by 'x', same length."""
    out, i, n = list(text), 0, len(text)
    while i < n:
        if text[i] == '"':
            j = i + 1
            while j < n and text[j] != '"':
                j += 2 if text[j] == '\\' else 1
            end = min(j + 1, n)
        elif text.startswith("''", i):
            j = i + 2
            while j < n:
                if text.startswith("'''", j) or text.startswith("''$", j) \
                        or text.startswith("''\\", j):
                    j += 3
                elif text.startswith("''", j):
                    break
                else:
                    j += 1
            end = min(j + 2, n)
        else:
            i += 1
            continue
        out[i:end] = 'x' * (end - i)
        i = end
    return ''.join(out)


def _list_items(inner: str) -> list[str]:
    """Top-level items of a comment-free list body; strings and brackets whole."""
    masked = _mask_strings(inner)
    items, depth, start = [], 0, None
    for i, ch in enumerate(masked):
        if ch in '([{':
            if depth == 0 and start is None:
                start = i
            depth += 1
        elif ch in ')]}':
            depth -= 1
        elif ch.isspace() and depth == 0:
            if start is not None:
                items.append(inner[start:i])
                start = None
        elif start is None:
            start = i
    if start is not None:
        items.append(inner[start:])
    return [it for it in items if it.strip()]


_GITHUB_REF = re.compile(r'^"github:([^/"]+)/([^/"]+)')


def normalize_value(value: str) -> str:
    """Canonical text of a Nix value for comparison."""
    v = _code(value).strip()
    # GitHub ignores the case of owner and repository: `github:nixos/nixpkgs`
    # is NiCo's `github:NixOS/nixpkgs` (revision 2026-11-10, B31).
    v = _GITHUB_REF.sub(lambda m: f'"github:{m.group(1).lower()}/{m.group(2).lower()}', v)
    m = re.fullmatch(r'with\s+pkgs\s*;\s*\[(.*)\]', v, re.DOTALL)
    if m or (v.startswith('[') and v.endswith(']')):
        inner = m.group(1) if m else v[1:-1]
        items = []
        for it in _list_items(inner):
            it = _squash(it)
            if m and _PLAIN_ITEM.fullmatch(it) and not it.startswith('pkgs.'):
                it = 'pkgs.' + it
            items.append(it)
        return '[' + ' '.join(items) + ']'
    return _squash(v)


# ── flattening ────────────────────────────────────────────────────────────────

def _walk(prefix: str, stmt: str, out: dict[str, list[str]]) -> None:
    path, value = _imp._split_stmt(stmt)
    if not path:
        # `inherit x;` has no path and still says something. Other text
        # without `=` is no statement at all – a stray `.` from the raw
        # editor; the panel drops it after asking (decision 2026-11-18).
        text = _squash(_code(stmt)).rstrip(';')
        if text.startswith('inherit'):
            out.setdefault(f'{prefix}.<{text}>' if prefix else f'<{text}>', []).append('')
        return
    full = f'{prefix}.{path}' if prefix else path
    # A function (`{ self, nixpkgs, ... }@inputs: { … }`, `pkgs: …`) is one
    # value: its body only means something together with its arguments.
    if _FUNCTION.match(_code(value).strip()):
        out.setdefault(full, []).append(normalize_value(value))
        return
    inner = _imp._attrset_inner(value.strip())
    subs = _imp._split_top_level_stmts(inner) if inner is not None else []
    if not subs:
        out.setdefault(full, []).append(normalize_value(value))
        return
    for sub in subs:
        _walk(full, sub, out)


def leaves(text: str) -> dict[str, list[str]]:
    """Every attribute path a module file defines → its normalised values.

    Comments are dropped first, so a deactivated Nix-Brick (its body is
    commented out) and NiCo's hint lines count as nothing; an active brick's
    body counts like any other statement – it is what the file says.
    """
    body = _imp._strip_module_head(_code(text))
    out: dict[str, list[str]] = {}
    for stmt in _imp._split_top_level_stmts(body):
        _walk('', stmt, out)
    return out


# ── comparison ────────────────────────────────────────────────────────────────

@dataclass
class Diff:
    """What a rewrite changes, by attribute path."""
    changed:   list[tuple[str, str, str]] = field(default_factory=list)  # path, before, after
    missing:   list[tuple[str, str]]      = field(default_factory=list)  # path, before
    added:     list[tuple[str, str]]      = field(default_factory=list)  # path, after
    reordered: list[tuple[str, str, str]] = field(default_factory=list)  # list, same items

    def __bool__(self) -> bool:
        return bool(self.changed or self.missing or self.added or self.reordered)


def _same_items(a: str, b: str) -> bool:
    if not (a.startswith('[') and b.startswith('[')):
        return False
    return sorted(_list_items(a[1:-1])) == sorted(_list_items(b[1:-1]))


def compare(original: str, written: str) -> Diff:
    """Leaf-by-leaf difference between a file and NiCo's version of it."""
    before, after = leaves(original), leaves(written)
    diff = Diff()
    for path, vals in before.items():
        new = after.get(path)
        if new is None:
            for v in vals:
                diff.missing.append((path, v))
            continue
        if sorted(vals) == sorted(new):
            continue
        if len(vals) == 1 and len(new) == 1 and _same_items(vals[0], new[0]):
            diff.reordered.append((path, vals[0], new[0]))
        else:
            diff.changed.append((path, ' | '.join(vals), ' | '.join(new)))
    for path, vals in after.items():
        if path not in before:
            for v in vals:
                diff.added.append((path, v))
    return diff


def _order_free(path: str) -> bool:
    return any(p.fullmatch(path) for p in ORDER_FREE)


def offending(diff: Diff) -> set[str]:
    """Paths where NiCo's version says something else or something more."""
    out = {p for p, _, _ in diff.changed} | {p for p, _ in diff.added}
    out |= {p for p, _, _ in diff.reordered if not _order_free(p)}
    return out


# ── the trial: settle what the form may take ──────────────────────────────────

HINT_LINE = '# nico: recommended by the panel, not set:'

# Keys that are not form fields, or that only steer how a file is written.
_NOT_FIELDS = {
    'brick_blocks', 'flake_brick_blocks', 'hm_brick_blocks', 'home_manager',
    'nix_args',
    # Home Manager: the file's existence and its header arguments; the head
    # is not compared (leaves() strips it), so they are never to blame.
    'enabled', 'args', 'args_explicit',
}


class NotFaithful(Exception):
    """NiCo cannot write this file back without changing what it says."""

    def __init__(self, paths):
        super().__init__(', '.join(sorted(paths)))
        self.paths = sorted(paths)


def _neutral(value):
    """The value a form field has when the file says nothing about it."""
    if isinstance(value, bool):
        return False
    if isinstance(value, str):
        return ''
    if isinstance(value, list):
        return []
    if isinstance(value, dict):
        return {}
    return None                      # numbers and the rest: drop the key


def _without(data: dict, key: str) -> dict:
    trial = dict(data)
    neutral = _neutral(trial[key])
    if neutral is None:
        trial.pop(key)
    else:
        trial[key] = neutral
    return trial


def _candidates(data: dict) -> list[str]:
    return [k for k, v in data.items()
            if not k.startswith('_') and k not in _NOT_FIELDS
            and _neutral(v) != v]


def _rescue_missing(original: str, data: dict, written: str,
                    bricks_key: str = 'brick_blocks', section: 'str | None' = None) -> list[str]:
    """Statements of `original` whose leaves NiCo does not write, as bricks.

    Like importer.rescue_lost_bindings, but also for statements of which
    nothing is written: a field the trial took back leaves its lines behind
    exactly like that, and nobody else picks them up.
    """
    from .brix import format_brick, next_order_for_section, section_for_attrpath

    have = set(leaves(written))
    body = _imp._strip_module_head(original)
    stmts = _imp._rescue_statements('', body, have)
    blocks = dict(data.get(bricks_key) or {})
    names = []
    fixed = section
    for text in stmts:
        if not _code(text).strip():
            continue
        section = fixed or section_for_attrpath(_imp._stmt_path(text))
        base = _imp._brix_name_from_stmt(text)
        name, n = base, 2
        while name in blocks:
            name, n = f'{base}-{n}', n + 1
        order = next_order_for_section(blocks, section)
        blocks[name] = {'section': section, 'order': order,
                        'text': format_brick(section, order, name, text)}
        names.append(name)
    data[bricks_key] = blocks
    return names


def _attach_hints(data: dict, hints: dict[str, str], new_bricks: list[str],
                  bricks_key: str = 'brick_blocks') -> None:
    """Write what the panel would have added into the brick it belongs to.

    Only values the file does not set at all (Martin, 2026-11-19): never a
    counter-proposal to something written down. Commented out, so nothing
    changes; uncommented, the brick is what the panel writes and the
    Validator says so (brix_redundant).
    """
    if not hints:
        return
    blocks = data.get(bricks_key) or {}
    by_brick: dict[str, list[str]] = {}
    for path, value in sorted(hints.items()):
        # The brick sharing the most leading path segments, at least two
        # (`services.avahi`); a bare `services` says nothing about belonging.
        best, best_len = None, 1
        segs = path.split('.')
        for name in new_bricks:
            stmt = _imp._stmt_path(_brick_body(blocks[name]['text'])).split('.')
            shared = 0
            while shared < min(len(segs), len(stmt)) and segs[shared] == stmt[shared]:
                shared += 1
            if shared > best_len:
                best, best_len = name, shared
        if best:
            by_brick.setdefault(best, []).append(f'# {path} = {value};')
    for name, lines in by_brick.items():
        text = blocks[name]['text']
        cut = text.rstrip().rfind('\n')
        blocks[name]['text'] = (text[:cut] + '\n' + HINT_LINE + '\n'
                                + '\n'.join(lines) + text[cut:])


def _brick_body(text: str) -> str:
    return _MARKER_LINE.sub('', text).strip()


def settle(original: str, data: dict, render, rounds: int = 40,
           bricks_key: str = 'brick_blocks', section: 'str | None' = None) -> dict:
    """The form data NiCo may write for `original` without changing it.

    `data` holds what the importer recognised plus the bricks so far;
    `render(data)` writes the file. Fields whose output differs from the
    file, or adds to it, are taken back – found by trying each field
    neutral – and the file's own lines become bricks. Raises NotFaithful
    when a difference has no field to blame; the caller then leaves the
    file as it is. `bricks_key` and `section`: where rescued lines go –
    Home Manager keeps its bricks under `hm_brick_blocks`, all in TAIL.
    """
    data = dict(data)
    hints: dict[str, str] = {}
    new_bricks: list[str] = []
    before = leaves(original)
    for _ in range(rounds):
        written = render(data)
        diff = compare(original, written)
        bad = offending(diff)
        if bad:
            # One field per round, the one whose withdrawal takes the least
            # out of the file: the user's groups, not the whole user.
            now = leaves(written)
            best, best_gone = None, None
            for key in _candidates(data):
                trial_text = render(_without(data, key))
                if offending(compare(original, trial_text)) < bad:
                    gone = set(now) - set(leaves(trial_text))
                    if best is None or len(gone) < len(best_gone):
                        best, best_gone = key, gone
            if best is None:
                raise NotFaithful(bad)
            for path in best_gone - set(before):
                hints[path] = now[path][0]
            data = _without(data, best)
            continue
        if diff.missing:
            names = _rescue_missing(original, data, written, bricks_key, section)
            if not names:
                raise NotFaithful(p for p, _ in diff.missing)
            new_bricks += names
            continue
        _attach_hints(data, hints, new_bricks, bricks_key)
        return data
    raise NotFaithful(offending(compare(original, render(data))) or {'?'})


# ── does the panel write exactly this brick? ──────────────────────────────────

def neutral_base() -> dict:
    """Form data with every field off: what the panel writes for 'nothing'."""
    from .config_manager import get_defaults
    base = {}
    for key, value in get_defaults().items():
        n = _neutral(value)
        if n is not None:
            base[key] = n
    base['_flake_mode'] = False
    return base


def _canon(found: dict[str, list[str]]) -> dict[str, list[str]]:
    out = {}
    for path, vals in found.items():
        if _order_free(path):
            vals = ['[' + ' '.join(sorted(_list_items(v[1:-1]))) + ']'
                    if v.startswith('[') else v for v in vals]
        out[path] = sorted(vals)
    return out


def panel_writes_exactly(body: str, render) -> bool:
    """True when the panel, given what this brick body says, writes exactly
    the brick's statements – no value different, nothing left out, nothing
    added. The Validator's brix_redundant asks this; before 2026-11-19 it
    only asked whether the importer recognised everything, by name, and
    called an Avahi brick without `nssmdns4` redundant."""
    wrapped = '{ config, pkgs, lib, ... }:\n{\n' + body + '\n}\n'
    recognized = _imp.parse_config(wrapped)
    if not recognized:
        return False
    base = neutral_base()
    before = _canon(leaves(render(base)))
    after = _canon(leaves(render({**base, **recognized})))
    gained = {p: v for p, v in after.items() if before.get(p) != v}
    lost = [p for p in before if p not in after]
    return not lost and gained == _canon(leaves(wrapped))


def require_faithful(original: str, written: str) -> None:
    """Raise NotFaithful unless `written` says exactly what `original` says.

    For flake.nix, where `settle` does not fit: its fields hang together
    (taking back the home-manager input changes the arguments of `outputs`),
    so there is no single field to withdraw. A flake NiCo would write
    differently stays as it is, like an unmappable one (2026-11-19). Found
    that way: NiCo turned `github:nix-community/home-manager` into
    `…/home-manager/master`.
    """
    diff = compare(original, written)
    bad = offending(diff) | {p for p, _ in diff.missing}
    if bad:
        raise NotFaithful(bad)
