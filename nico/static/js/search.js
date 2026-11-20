/** NiCo frontend — one search for every file NiCo shows. Classic script sharing the global scope.
 *
 * Until 2026-11-20 there were two searches with their own code: Ctrl+F
 * searched a fixed list that only the configuration/host preview filled, and
 * the raw editor had a second one behind the tools menu. In raw mode and for
 * flake.nix or Home Manager files Ctrl+F therefore searched an outdated
 * preview – it counted hits nobody could see (VM test 2026-11-19, A).
 *
 * Martin: a function like this exists once and works on every file NiCo can
 * open, as "copy whole file" does. So: one bar, one entry (Ctrl+F and the
 * tools menu), one matcher over the text of whatever is shown right now. Only
 * the last step differs by display, because a textarea cannot hold marks:
 *   - text mode: marked in the colored layer below the textarea, and also
 *     selected and scrolled to – without taking the focus from the search
 *     field (a focused textarea turned the next typed letter into an edit of
 *     the config). The marks are what is seen: Firefox does not draw the
 *     selection of an unfocused textarea (Martin's test, 2026-11-20);
 *   - code views (previews, flake.lock): the hit is marked.
 */
'use strict';

const Search = (() => {
  let query    = '';
  let matches  = [];   // [{start, end}] in the target's text
  let idx      = -1;
  let marks    = [];   // code view: per match, the <mark> elements it spans
  let target   = null; // {kind: 'text'|'code', el}
  let debounce = null;

  const $ = id => document.getElementById(id);

  function isOpen() {
    return !$('search-bar')?.classList.contains('hidden');
  }

  /** What is shown on the right right now. In text mode the marks go into
   *  the colored layer below the textarea (sidebar._attachTextEditor). */
  function _currentTarget() {
    const ta = document.querySelector('#raw-file-view textarea');
    if (ta) return { kind: 'text', el: ta,
                     view: ta.parentElement.querySelector('.text-editor-view code') };
    const flk = document.querySelector('#flk-file-view pre');
    if (flk) return { kind: 'code', el: flk };
    const view = [...document.querySelectorAll('#preview-panel .code-view-container')]
      .find(c => !c.classList.contains('hidden'));
    return view ? { kind: 'code', el: view } : null;
  }

  function _textOf(t) {
    return t.kind === 'text' ? t.el.value : t.el.textContent;
  }

  function _find(text, q) {
    const out = [];
    if (!q) return out;
    const lower = text.toLowerCase(), needle = q.toLowerCase();
    let pos = 0;
    while ((pos = lower.indexOf(needle, pos)) !== -1) {
      out.push({ start: pos, end: pos + needle.length });
      pos += needle.length;
    }
    return out;
  }

  // ── Code views: marks ───────────────────────────────────────────────────

  /** Wrap every match in <mark>, segment by segment: Prism splits text into
   *  tokens, so one match can span several text nodes. */
  function _mark(root, list) {
    marks = list.map(() => []);
    if (!list.length) return;
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
    const nodes = [];
    let pos = 0, n;
    while ((n = walker.nextNode())) {
      nodes.push({ node: n, start: pos, end: pos + n.textContent.length });
      pos += n.textContent.length;
    }
    for (let ni = nodes.length - 1; ni >= 0; ni--) {
      const { node, start, end } = nodes[ni];
      const parts = [];
      list.forEach((m, mi) => {
        if (m.end <= start || m.start >= end) return;
        parts.push({ a: Math.max(0, m.start - start), b: Math.min(end, m.end) - start, mi });
      });
      if (!parts.length) continue;
      const text = node.textContent;
      const frag = document.createDocumentFragment();
      let last = 0;
      parts.sort((x, y) => x.a - y.a).forEach(({ a, b, mi }) => {
        if (a > last) frag.appendChild(document.createTextNode(text.slice(last, a)));
        const mk = document.createElement('mark');
        mk.className = 'search-match';
        mk.textContent = text.slice(a, b);
        frag.appendChild(mk);
        marks[mi].unshift(mk);
        last = b;
      });
      if (last < text.length) frag.appendChild(document.createTextNode(text.slice(last)));
      node.parentNode.replaceChild(frag, node);
    }
    // Collapsed preview sections holding a hit open up; close() restores them.
    root.querySelectorAll('.code-section.collapsed').forEach(sec => {
      if (sec.querySelector('mark.search-match')) sec.classList.remove('collapsed');
    });
  }

  function _unmark() {
    document.querySelectorAll('mark.search-match').forEach(m => {
      const parent = m.parentNode;
      parent.replaceChild(document.createTextNode(m.textContent), m);
      parent.normalize();
    });
    marks = [];
  }

  // ── Showing the current hit ─────────────────────────────────────────────

  function _show({ scroll = true } = {}) {
    if (!target || idx < 0 || !matches[idx]) return;
    marks.forEach((group, i) => group.forEach(mk =>
      mk.classList.toggle('search-match-active', i === idx)));
    if (target.kind === 'text') {
      const ta = target.el, m = matches[idx];
      // Works without focus; style.css makes the inactive selection visible.
      ta.setSelectionRange(m.start, m.end);
      if (!scroll) return;
      // Where the hit really is: its mark in the layer below, which shares
      // the textarea's coordinates. Long lines need the sideways scroll too.
      const mk = marks[idx]?.[0];
      if (mk) {
        ta.scrollTop = Math.max(0, mk.offsetTop - ta.clientHeight / 2);
        const left = mk.offsetLeft, right = left + mk.offsetWidth;
        if (left < ta.scrollLeft || right > ta.scrollLeft + ta.clientWidth) {
          ta.scrollLeft = Math.max(0, left - ta.clientWidth / 2);
        }
      } else {
        const linesBefore = ta.value.substring(0, m.start).split('\n').length - 1;
        const lineHeight  = parseFloat(getComputedStyle(ta).lineHeight) || 22;
        ta.scrollTop = Math.max(0, linesBefore * lineHeight - ta.clientHeight / 2);
      }
    } else if (scroll) {
      marks[idx]?.[0]?.scrollIntoView({ behavior: 'smooth', block: 'center' });
    }
  }

  function _count() {
    const el = $('search-count');
    if (!el) return;
    if (!query) el.textContent = '';
    else if (!matches.length) el.textContent = t('search.noResults');
    else el.textContent = `${idx + 1}/${matches.length}`;
  }

  // ── Public ──────────────────────────────────────────────────────────────

  /** Search again in whatever is shown now. A new query starts at the first
   *  hit; a re-render of the view (`keep`: preview updated while typing in
   *  the form, file switch) keeps the position and does not scroll. */
  function refresh({ keep = false } = {}) {
    if (!isOpen()) return;
    const prev = idx;
    _unmark();
    target  = _currentTarget();
    matches = target ? _find(_textOf(target), query) : [];
    idx     = !matches.length ? -1 : keep ? Math.min(Math.max(prev, 0), matches.length - 1) : 0;
    _markTarget();
    _count();
    _show({ scroll: !keep });
  }

  function _markTarget() {
    if (target?.kind === 'code') _mark(target.el, matches);
    else if (target?.kind === 'text' && target.view) _mark(target.view, matches);
  }

  /** The text editor re-colored its layer (typing): the marks are set again
   *  for the current text. The selection and the caret are left alone – the
   *  user is typing there. */
  function remark(ta) {
    if (!isOpen() || target?.kind !== 'text' || target.el !== ta) return;
    const prev = idx;
    _unmark();
    target.view = ta.parentElement.querySelector('.text-editor-view code');
    matches = _find(ta.value, query);
    idx = matches.length ? Math.min(Math.max(prev, 0), matches.length - 1) : -1;
    _markTarget();
    marks.forEach((group, i) => group.forEach(mk =>
      mk.classList.toggle('search-match-active', i === idx)));
    _count();
  }

  /** Next/previous hit. In the raw editor the text may have changed since the
   *  last search, so the hits are recomputed and the step goes from the
   *  current selection. */
  function step(dir) {
    if (target?.kind === 'text') {
      const from = target.el.selectionStart;
      _unmark();
      matches = _find(target.el.value, query);
      _markTarget();
      if (!matches.length) { idx = -1; _count(); return; }
      if (dir > 0) {
        const next = matches.findIndex(m => m.start > from);
        idx = next === -1 ? 0 : next;
      } else {
        let prev = -1;
        matches.forEach((m, i) => { if (m.start < from) prev = i; });
        idx = prev === -1 ? matches.length - 1 : prev;
      }
    } else {
      if (!matches.length) return;
      idx = (idx + dir + matches.length) % matches.length;
    }
    _count();
    _show();
  }

  function open() {
    $('search-bar').classList.remove('hidden');
    const input = $('search-input');
    input.focus();
    input.select();
    refresh();
  }

  /** Closing is the moment the focus may move into the raw text: the user is
   *  done searching and wants to edit at the hit. */
  function close() {
    const ta = target?.kind === 'text' ? target.el : null;
    $('search-bar').classList.add('hidden');
    $('search-input').value = '';
    query = '';
    _unmark();
    matches = []; idx = -1; target = null;
    _count();
    if (typeof applySectionCollapse === 'function') applySectionCollapse();
    ta?.focus();
  }

  function init() {
    const input = $('search-input');
    input.addEventListener('input', e => {
      clearTimeout(debounce);
      debounce = setTimeout(() => { query = e.target.value.trim(); refresh(); }, 150);
    });
    input.addEventListener('keydown', e => {
      if (e.key === 'Escape') { close(); return; }
      if (e.key === 'Enter')  { e.preventDefault(); step(e.shiftKey ? -1 : 1); }
    });
    $('search-close').addEventListener('click', close);
    $('search-prev').addEventListener('click', () => step(-1));
    $('search-next').addEventListener('click', () => step(1));
  }

  return { init, open, close, refresh, remark, step, isOpen };
})();
