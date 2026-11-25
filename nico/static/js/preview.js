/** NiCo frontend — live code preview: section/brick parsing and rendering, collapse state, package tab, section doc links. Split from app.js; classic script sharing the global scope. */
'use strict';

// ── Live preview ───────────────────────────────────────────────────────────
async function updatePreview() {
  const payload = getFormData();
  if (_brixTargetFile && _brixTargetFile !== 'flake.nix') {
    payload._path = _brixTargetFile;
  }
  const res  = await csrfFetch('/api/preview', {
    method:  'POST',
    headers: { 'Content-Type': 'application/json' },
    body:    JSON.stringify(payload),
  });
  const data = await res.json();

  renderCodePreview(data.configuration_nix || '', 'preview-configuration', _brixTargetFile || 'configuration.nix');


  const flakeTab = document.getElementById('flake-tab');
  if (data.flake_nix) {
    renderCodePreview(data.flake_nix, 'preview-flake', 'flake.nix');
    flakeTab?.classList.remove('hidden');
  } else {
    flakeTab?.classList.add('hidden');
    if (activeTab === 'flake') switchTab('configuration');
  }

  // Legacy hm preview tab removed: no endpoint sends home_nix anymore
  // (root home.nix is legacy; HM files live in hm_dir/<username>.nix).
  document.getElementById('hm-tab')?.classList.add('hidden');
  if (activeTab === 'hm') switchTab('configuration');

  // Keep preview-tabs bar always hidden – tabs concept was discarded
  document.getElementById('preview-tabs')?.classList.add('hidden');

}

async function _refreshPreviewForFile(file = 'configuration.nix') {
  if (file === 'flake.nix') {
    await Sidebar.refreshFlakeView();
    return;
  }
  if (_brixTargetFtype === 'hm' && file !== 'configuration.nix') {
    const res = await csrfFetch(`/api/file?path=${encodeURIComponent(file)}`);
    const data = await res.json();
    if (!data.error) renderCodePreview(data.content, 'preview-hm', file);
    return;
  }
  await updatePreview();
}

function schedulePreviewUpdate() {
  clearTimeout(previewDebounce);
  previewDebounce = setTimeout(updatePreview, 450);
}

// ── Plain-Code-View Toggle ─────────────────────────────────────────────────

/** Entfernt Brix-Marker und Sektions-Kopfzeilen aus dem Code-Text. */
function stripAnnotations(code) {
  return code.split('\n').filter(line => {
    if (BRICK_START_RE.test(line)) return false;
    if (BRICK_END_RE.test(line))   return false;
    if (SECTION_RE.test(line))     return false;
    return true;
  }).join('\n');
}

/** Setzt Icon-Farbe und Tooltip des Toggle-Buttons passend zum aktuellen Zustand. */
function applyPlainCodeViewBtn() {
  const btn = document.getElementById('preview-mode-btn');
  if (!btn) return;
  // Nur anpassen wenn wir im Panel-Modus sind (nicht im Raw-Edit-Modus)
  if (btn.dataset.mode === 'edit') return;
  btn.innerHTML = niIcon('eye');
  btn.dataset.mode = 'view';
  if (plainCodeView) {
    btn.style.color  = 'var(--red, #e05252)';
    btn.title        = t('preview.plainActive');
    btn.style.cursor = 'pointer';
  } else {
    btn.style.color  = 'var(--green, #40a02b)';
    btn.title        = t('preview.plainInactive');
    btn.style.cursor = 'pointer';
  }
}

/** Toggelt zwischen annotierter und plain Ansicht und re-rendert. */
async function togglePlainCodeView() {
  await Sidebar.togglePlainCodeView();
}

// ── Code preview – section-aware & brick-aware rendering ──────────────────
const SECTION_RE    = /^\s*# ── (.+?) [─]+\s*$/;
// New brick format: # <brick: SectionName / #N brick-name>
// \s* handles legacy files where markers were indented (e.g. "  # <brick: ...>")
// The trailing "inactive" marks a brick whose body is commented out. It has
// to be in this regex too: without it the marker line does not match, the
// brick is rendered as ordinary text and disappears from the panel.
const BRICK_START_RE = /^\s*# <brick:\s*([^/]+?)\s*\/\s*#(\d+)\s+([\w\-]+)(\s+inactive)?\s*>/;
// NiCo's own brick for deactivated packages (brix.INACTIVE_PKG_BRICK). Shown
// like any brick - the file hides nothing - but only the package list writes
// it, so the menu offers nothing except deleting it.
const RESERVED_BRICK = 'nico-inactive-packages';
const BRICK_END_RE   = /^\s*# <\/brick:\s*([\w\-]+)(?:\s+inactive)?\s*>/;

// Canonical list of NiCo sections (mirrors brix.py SECTION_ORDER)
const BRICK_SECTIONS = [
  'HEAD',
  'BOOT', 'SYST', 'I18N', 'NETW', 'SERV',
  'DESK', 'AUDI', 'USER', 'PROG',
  'FONT', 'NIXS', 'HDWR', 'VIRT',
  'FSYS', 'HOME',
  'TAIL',
];

const HM_PREVIEW_SECTIONS = ['__header__', 'HEAD', 'HOME', 'SHEL', 'HPKG', 'TAIL'];

/**
 * Split raw code into typed segments:
 *   { type: 'preamble', lines }
 *   { type: 'section',  name, header, lines }
 *   { type: 'brick',    name, section, order, inactive, lines }
 */
function parseCodeSections(code) {
  const segments = [];
  let current = { type: 'preamble', lines: [], startLine: 1 };
  let lineNum  = 1;
  let inBrick  = null;

  for (const rawLine of code.split('\n')) {
    if (inBrick !== null) {
      current.lines.push(rawLine);
      if (BRICK_END_RE.test(rawLine)) {
        segments.push(current);
        current = { type: 'preamble', lines: [], startLine: lineNum + 1 };
        inBrick = null;
      }
      lineNum++;
      continue;
    }

    const sectionMatch = rawLine.match(SECTION_RE);
    const brickMatch   = rawLine.match(BRICK_START_RE);

    if (sectionMatch) {
      segments.push(current);
      current = { type: 'section', name: sectionOfHeader(sectionMatch[1]),
                  header: rawLine, lines: [], startLine: lineNum };
    } else if (brickMatch) {
      segments.push(current);
      inBrick  = brickMatch[3];
      current  = {
        type: 'brick', name: brickMatch[3],
        section: normalizeSection(brickMatch[1]), order: parseInt(brickMatch[2], 10),
        inactive: !!brickMatch[4],
        lines: [rawLine], startLine: lineNum,
      };
    } else {
      current.lines.push(rawLine);
    }
    lineNum++;
  }
  segments.push(current);
  return segments;
}

function renderLineNums(start, count) {
  const div = document.createElement('div');
  div.className = 'line-nums';
  if (count > 0) {
    const nums = [];
    for (let i = 0; i < count; i++) nums.push(start + i);
    div.textContent = nums.join('\n');
  }
  return div;
}

function highlightCode(el, text) {
  el.textContent = text;
  if (window.Prism) Prism.highlightElement(el);
}

function updateSectionLineRanges(ranges) {
  document.querySelectorAll('.sec-toggle').forEach(h3 => {
    const section = h3.closest('section');
    if (!section?.dataset.section) return;
    const hint = h3.querySelector('.sec-line-hint');
    if (!hint) return;
    const r = ranges[section.dataset.section];
    hint.textContent = r ? `(${r.start}–${r.end})` : '';
  });
}

/**
 * Render a plain Nix file into a container div with line numbers only.
 */
function renderPlainPreview(containerId, code) {
  const container = document.getElementById(containerId);
  if (!container) return;
  container.innerHTML = '';
  if (!code) return;

  const lines = code.split('\n');
  const wrap  = document.createElement('div');
  wrap.className = 'code-with-lines';

  wrap.appendChild(renderLineNums(1, lines.length));

  const pre  = document.createElement('pre');
  pre.className = 'code-view';
  const code_el = document.createElement('code');
  code_el.className = 'language-nix';
  highlightCode(code_el, code);
  pre.appendChild(code_el);
  wrap.appendChild(pre);
  container.appendChild(wrap);
}

function renderAnnotatedPreview(code, containerId = 'preview-configuration', file = 'configuration.nix') {
  const container = document.getElementById(containerId);
  if (!container) return;
  container.innerHTML = '';

  const segments      = parseCodeSections(code);
  const sectionRanges = {};
  let lastSectionDiv  = null;

  for (const seg of segments) {
    if (seg.type === 'preamble') {
      // Skip empty or whitespace-only preambles (e.g. blank lines after brick end markers)
      if (seg.lines.length === 0 || seg.lines.every(l => l.trim() === '')) continue;
      const PKEY = '__header__';
      const div = document.createElement('div');
      div.className = 'code-section';
      div.dataset.section = PKEY;
      if (collapsedSections.has(PKEY)) div.classList.add('collapsed');

      const headLine = seg.lines[0] ?? '';
      const bodyLines = seg.lines.slice(1);

      const headPre = document.createElement('pre');
      headPre.className = 'code-section-head code-view';
      const headCode = document.createElement('code');
      headCode.className = 'language-nix';
      highlightCode(headCode, headLine);
      headPre.appendChild(headCode);
      const headWrapper = document.createElement('div');
      headWrapper.className = 'code-with-lines';
      headWrapper.appendChild(renderLineNums(seg.startLine, 1));
      headWrapper.appendChild(headPre);
      headWrapper.addEventListener('click', () => toggleSection(PKEY));

      const bodyPre = document.createElement('pre');
      bodyPre.className = 'code-view';
      const bodyCode = document.createElement('code');
      bodyCode.className = 'language-nix';
      highlightCode(bodyCode, bodyLines.join('\n'));
      bodyPre.appendChild(bodyCode);
      const bodyWrapper = document.createElement('div');
      bodyWrapper.className = 'code-with-lines code-section-body';
      bodyWrapper.appendChild(renderLineNums(seg.startLine + 1, bodyLines.length));
      bodyWrapper.appendChild(bodyPre);

      div.appendChild(headWrapper);
      div.appendChild(bodyWrapper);
      container.appendChild(div);
      lastSectionDiv = div;

    } else if (seg.type === 'section') {
      const totalLines = 1 + seg.lines.length;
      sectionRanges[seg.name] = { start: seg.startLine, end: seg.startLine + totalLines - 1 };

      const div = document.createElement('div');
      div.className       = 'code-section';
      div.dataset.section = seg.name;
      if (collapsedSections.has(seg.name)) div.classList.add('collapsed');

      // Head (1 line) with line number
      const head = document.createElement('pre');
      head.className = 'code-section-head code-view';
      const headCode = document.createElement('code');
      headCode.className = 'language-nix';
      highlightCode(headCode, seg.header);
      head.appendChild(headCode);

      const headWrapper = document.createElement('div');
      headWrapper.className = 'code-with-lines';
      headWrapper.appendChild(renderLineNums(seg.startLine, 1));
      headWrapper.appendChild(head);
      headWrapper.addEventListener('click', () => toggleSection(seg.name));

      // Body (N lines) with line numbers
      const body = document.createElement('pre');
      body.className = 'code-view';
      const bodyCode = document.createElement('code');
      bodyCode.className = 'language-nix';
      highlightCode(bodyCode, seg.lines.join('\n'));
      body.appendChild(bodyCode);

      const bodyWrapper = document.createElement('div');
      bodyWrapper.className = 'code-with-lines code-section-body';
      bodyWrapper.appendChild(renderLineNums(seg.startLine + 1, seg.lines.length));
      bodyWrapper.appendChild(body);

      div.appendChild(headWrapper);
      div.appendChild(bodyWrapper);
      container.appendChild(div);
      lastSectionDiv = div;

    } else if (seg.type === 'brick') {
      // Render brick block with header bar and context menu (Option A: sibling to sections)
      const wrap = document.createElement('div');
      wrap.className = 'code-brix';
      wrap.dataset.brickName = seg.name;  // for post-insert scroll-to
      if (collapsedBrix.has(seg.name)) wrap.classList.add('collapsed');
      if (seg.inactive) wrap.classList.add('brix-inactive');
      const reserved = seg.name === RESERVED_BRICK;

      const headBar = document.createElement('div');
      headBar.className = 'code-brix-head';
      headBar.addEventListener('click', e => {
        if (e.target.closest('.brix-actions')) return;
        const parentSection = wrap.closest('.code-section');
        // The clicked head stays where it was clicked (Martin, 2026-11-25).
        // Opening the section grows everything above it, with a transition,
        // and the section scrolled itself into view - the brick jumped away.
        keepOnScreen(headBar, () => {
          if (parentSection?.classList.contains('collapsed')) {
            // A brick body stays hidden while its section is collapsed, so
            // open the section and show exactly this brick – other bricks
            // keep their state. Expanding only the brick would look like a
            // dead click.
            toggleSection(parentSection.dataset.section, { scroll: false });
            if (collapsedBrix.has(seg.name)) toggleBrix(seg.name, wrap);
          } else {
            toggleBrix(seg.name, wrap);
          }
        }, 400);
      });

      const label = document.createElement('span');
      label.className   = 'code-brix-label';
      label.textContent = `# Brick: ${secLabel(seg.section)} / #${seg.order} · ${seg.name}`
                        + (seg.inactive ? ` · ${t('brix.inactiveTag')}` : '')
                        + (reserved ? ` · ${t('brix.reservedTag')}` : '');
      if (reserved) label.title = t('brix.reservedHint');

      const actions = document.createElement('div');
      actions.className = 'brix-actions';

      // ⋮ context menu button
      const menuBtn = document.createElement('button');
      menuBtn.className = 'brix-menu-btn';
      menuBtn.innerHTML = niIcon('more-vertical');
      menuBtn.title = t('brix.menuTitle');

      const menu = document.createElement('div');
      menu.className = 'brix-context-menu hidden';

      const menuItems = reserved ? [
        { key: 'brix.menuDelete', action: () => confirmDeleteBrix(seg.name, file), cls: 'menu-item-danger' },
      ] : [
        { key: 'brix.menuRename', action: () => { _brixContextFile = file; _brixContextFtype = _brixTargetFtype; openBrixRename(seg.name); } },
        { key: 'brix.menuMove',   action: () => { _brixContextFile = file; _brixContextFtype = _brixTargetFtype; openBrixMoveFor(seg.name); } },
        { key: 'brix.menuSplit',  action: () => { _brixContextFile = file; _brixContextFtype = _brixTargetFtype; openBrixSplit(seg.name); } },
        // Only where a form regenerates the file: configuration.nix and host
        // files (Gegenprobe phase 4, 2026-11-19). The server decides whether
        // the panel writes exactly the same.
        ...(_brixTargetFtype === 'co' && !seg.inactive && (file === 'configuration.nix' || _activeHost)
          ? [{ key: 'brix.menuAdopt', action: () => adoptBrix(seg.name, file) }] : []),
        { key: seg.inactive ? 'brix.menuActivate' : 'brix.menuDeactivate',
          action: () => setBrixInactive(seg.name, !seg.inactive, file) },
        { key: 'brix.menuDelete', action: () => confirmDeleteBrix(seg.name, file), cls: 'menu-item-danger' },
      ];
      for (const item of menuItems) {
        const mi = document.createElement('button');
        mi.className = `brix-menu-item${item.cls ? ' ' + item.cls : ''}`;
        mi.textContent = t(item.key);
        mi.addEventListener('click', (e) => { e.stopPropagation(); closeAllBrixMenus(); item.action(); });
        menu.appendChild(mi);
      }

      menuBtn.addEventListener('click', (e) => {
        e.stopPropagation();
        const wasHidden = menu.classList.contains('hidden');
        closeAllBrixMenus();
        if (wasHidden) {
          menu.classList.remove('hidden');
          // The action bar is shown through .code-brix:hover, and the menu
          // hangs below it with top:100%. With the section collapsed the
          // brick is one line tall, so moving the mouse into the menu leaves
          // the brick, :hover stops matching and the menu vanishes mid-
          // motion - while the brick head underneath takes the hover and
          // shows its own buttons. Reported by Martin 2026-11-01. An open
          // menu therefore keeps its own bar visible, instead of relying on
          // where the pointer happens to be.
          wrap.classList.add('brix-menu-open');
        }
      });

      actions.appendChild(menuBtn);
      actions.appendChild(menu);
      headBar.appendChild(label);
      headBar.appendChild(actions);

      const bodyPre = document.createElement('pre');
      bodyPre.className = 'code-view';
      const bodyCode = document.createElement('code');
      bodyCode.className = 'language-nix';
      highlightCode(bodyCode, seg.lines.join('\n'));
      bodyPre.appendChild(bodyCode);

      const bodyWrapper = document.createElement('div');
      bodyWrapper.className = reserved ? 'code-with-lines' : 'code-with-lines brix-editable';
      bodyWrapper.title = t(reserved ? 'brix.reservedHint' : 'brix.clickToEdit');
      bodyWrapper.appendChild(renderLineNums(seg.startLine, seg.lines.length));
      bodyWrapper.appendChild(bodyPre);

      // Click on body → open inline editor (inner lines, without markers).
      // The clicked row is read from the line numbers: the caret goes there
      // and the row keeps its place on screen (Martin, 2026-11-25).
      bodyWrapper.addEventListener('click', e => {
        if (reserved) return;
        if (wrap.querySelector('.brix-inline-editor')) return;  // already open
        const inner = seg.lines.slice(1, -1);
        const nums  = bodyWrapper.querySelector('.line-nums');
        const top   = nums.getBoundingClientRect().top;
        const lh    = parseFloat(getComputedStyle(nums).lineHeight) || 21.45;
        const row   = Math.floor((e.clientY - top) / lh);
        // Row 0 and the last row are the marker lines, not editable.
        const line  = Math.max(0, Math.min(inner.length - 1, row - 1));
        openBrixInlineEdit(seg.name, inner.join('\n'), wrap, bodyWrapper, file,
                           { line, y: top + (line + 1) * lh });
      });

      wrap.appendChild(headBar);
      wrap.appendChild(bodyWrapper);
      // Append inside the last section so bricks collapse with their section
      (lastSectionDiv || container).appendChild(wrap);
    }
  }

  updateSectionLineRanges(sectionRanges);
}

function renderCodePreview(code, containerId = 'preview-configuration', file = 'configuration.nix') {
  const container = document.getElementById(containerId);
  if (!container) return;
  container.dataset.sourceCode = code;
  container.dataset.sourceFile = file;
  if (plainCodeView) {
    renderPlainPreview(containerId, stripAnnotations(code));
  } else {
    renderAnnotatedPreview(code, containerId, file);
  }
  // Every preview is drawn here: an open search follows the new content.
  Search.refresh({ keep: true });
}

// ── Section collapse (left ↔ right sync) ───────────────────────────────────
// Was needed while the flake panel section ("Hosts") and the generated marker
// ("Outputs-Hosts") had different names. Both are OUTH now, so the map is empty
// – kept as the hook for any future pair that differs again.
const _SECTION_SYNC_MAP = {};

// ── Keeping a spot on screen while the layout changes ─────────────────────

function _scrollParent(el) {
  for (let p = el.parentElement; p; p = p.parentElement) {
    const oy = getComputedStyle(p).overflowY;
    if (oy === 'auto' || oy === 'scroll') return p;
  }
  return document.scrollingElement;
}

/** Scrolls el's container so that getY() lands on screen position y0 again,
 *  and keeps doing so for `ms` – a section opens with a transition, so the
 *  content above the spot grows over several frames. A turn of the mouse
 *  wheel ends it early: the user's own scrolling wins. */
function holdAt(el, y0, getY, ms = 0) {
  const sc = _scrollParent(el);
  if (!sc) return;
  const fix = () => {
    const d = getY() - y0;
    if (Math.abs(d) >= 1) sc.scrollTop += d;
  };
  fix();
  if (!ms) return;
  let stop = false;
  const quit = () => { stop = true; };
  sc.addEventListener('wheel', quit, { once: true, passive: true });
  const end = performance.now() + ms;
  const step = () => {
    if (stop) return;
    fix();
    if (performance.now() < end) requestAnimationFrame(step);
    else sc.removeEventListener('wheel', quit);
  };
  requestAnimationFrame(step);
}

/** Runs `change` and keeps el at the screen position it had before. */
function keepOnScreen(el, change, ms = 0) {
  const y0 = el.getBoundingClientRect().top;
  change();
  holdAt(el, y0, () => el.getBoundingClientRect().top, ms);
}

function toggleSection(name, { scroll = true } = {}) {
  const wasCollapsed = collapsedSections.has(name);
  if (wasCollapsed) collapsedSections.delete(name);
  else              collapsedSections.add(name);

  const syncName = _SECTION_SYNC_MAP[name];
  if (syncName) {
    if (collapsedSections.has(name)) collapsedSections.add(syncName);
    else                              collapsedSections.delete(syncName);
  }

  // Sections deliberately start collapsed on every load (see core.js), so the
  // state is not persisted – it was written here but never read back.
  applySectionCollapse();

  // Scroll the code section into view when expanding – unless the caller
  // keeps its own spot on screen (brick head click).
  if (wasCollapsed && scroll) {
    const codeSection = document.querySelector(`.code-section[data-section="${name.replace(/"/g, '\\"')}"]`);
    if (codeSection) setTimeout(() => codeSection.scrollIntoView({ behavior: 'smooth', block: 'nearest' }), 50);
  }
}

function toggleBrix(name, wrapEl) {
  if (collapsedBrix.has(name)) {
    collapsedBrix.delete(name);
    wrapEl.classList.remove('collapsed');
  } else {
    collapsedBrix.add(name);
    wrapEl.classList.add('collapsed');
  }
  localStorage.setItem('nico-collapsed-brix', JSON.stringify([...collapsedBrix]));
}

function applySectionCollapse() {
  document.querySelectorAll('section[data-section]').forEach(el => {
    el.classList.toggle('collapsed', collapsedSections.has(el.dataset.section));
  });
  document.querySelectorAll('.code-section[data-section]').forEach(el => {
    el.classList.toggle('collapsed', collapsedSections.has(el.dataset.section));
  });
}

/** Collapse or expand everything the current view can fold: every rendered
 *  section plus every individual brick.
 *
 *  The sections are read from the DOM instead of a hard-coded list, because
 *  the flake view has one section per host (`HOST:nix-vm`) and no list can
 *  know their names – they stayed open however often you pressed "collapse
 *  all". BRICK_SECTIONS is merged in so that a section which is not rendered
 *  right now still gets the right state for when it appears.
 *
 *  One implementation for all three views. There used to be three copies –
 *  configuration.nix, flake.nix and Home Manager – and each had forgotten
 *  something else: the flake copy never touched the bricks at all, the Home
 *  Manager copy expanded them but never collapsed them.
 */
function setAllCollapsed(collapsed) {
  const sections = new Set(['__header__', ...BRICK_SECTIONS]);
  document.querySelectorAll('.code-section[data-section], section[data-section]')
    .forEach(el => sections.add(el.dataset.section));
  sections.forEach(s => collapsed ? collapsedSections.add(s)
                                  : collapsedSections.delete(s));
  applySectionCollapse();

  if (collapsed) {
    document.querySelectorAll('.code-brix[data-brick-name]').forEach(el => {
      collapsedBrix.add(el.dataset.brickName);
      el.classList.add('collapsed');
    });
  } else {
    collapsedBrix.clear();
    document.querySelectorAll('.code-brix.collapsed')
      .forEach(el => el.classList.remove('collapsed'));
  }
  localStorage.setItem('nico-collapsed-brix', JSON.stringify([...collapsedBrix]));
}

// ── Section filter & visibility ────────────────────────────────────────────

/** Returns true if a left-panel section has no user-configured values. */
function isSectionEmpty(sec) {
  // System is always considered non-empty (fundamental settings)
  if (sec.dataset.section === 'SYST') return false;

  const body = sec.querySelector('.sec-body');
  if (!body) return false;

  // Helper: is this element inside a .hidden container within the section body?
  function insideHidden(el) {
    let p = el.parentElement;
    while (p && p !== body) {
      if (p.classList.contains('hidden')) return true;
      p = p.parentElement;
    }
    return false;
  }

  for (const inp of body.querySelectorAll('input[type=checkbox]')) {
    if (!insideHidden(inp) && inp.checked) return false;
  }
  for (const inp of body.querySelectorAll('input[type=text], input[type=number], textarea')) {
    if (!insideHidden(inp) && inp.value.trim()) return false;
  }
  for (const sel of body.querySelectorAll('select')) {
    if (!insideHidden(sel) && sel.value && sel.value !== 'none') return false;
  }

  // Dynamic content areas
  const usersList = body.querySelector('#users-list');
  if (usersList && usersList.children.length > 0) return false;
  const pkgList = body.querySelector('#packages-list');
  if (pkgList && pkgList.children.length > 0) return false;

  return true;
}

/** Apply the current sectionFilter to all left-panel sections. */
function updateSectionVisibility() {
  document.querySelectorAll('section.collapsible[data-section]').forEach(sec => {
    let hide = false;
    if (sectionFilter === 'non-empty') {
      hide = isSectionEmpty(sec);
    } else if (sectionFilter === 'settings') {
      const name = sec.dataset.section;
      hide = hiddenSections.includes(name) && isSectionEmpty(sec);
    }
    sec.classList.toggle('sec-hidden', hide);
  });
  // Sync filter button active state
  const btn = document.getElementById('sec-filter-btn');
  if (btn) btn.classList.toggle('active', sectionFilter !== 'all');
  // Sync dropdown option highlights
  document.querySelectorAll('.sec-filter-option').forEach(opt => {
    opt.classList.toggle('active', opt.dataset.filter === sectionFilter);
  });
}

/** Toggle section filter dropdown. */
function _toggleFilterDropdown(e) {
  e.stopPropagation();
  const dd = document.getElementById('sec-filter-dropdown');
  if (dd) dd.classList.toggle('hidden');
}

/** Close filter dropdown when clicking outside. */
function _closeFilterDropdown(e) {
  const wrap = document.querySelector('.sec-filter-wrap');
  if (wrap && !wrap.contains(e.target)) {
    document.getElementById('sec-filter-dropdown')?.classList.add('hidden');
  }
}

// ── Tab switching ──────────────────────────────────────────────────────────
function switchTab(tab) {
  activeTab = tab;
  document.querySelectorAll('.tab').forEach(btn =>
    btn.classList.toggle('active', btn.dataset.tab === tab)
  );
  document.getElementById('preview-configuration')
    .classList.toggle('hidden', tab !== 'configuration');
  document.getElementById('preview-flake')
    .classList.toggle('hidden', tab !== 'flake');
  document.getElementById('preview-hm')
    ?.classList.toggle('hidden', tab !== 'hm');
  // An open search follows to the view now shown
  Search.refresh({ keep: true });
}

// ── Package list ───────────────────────────────────────────────────────────
function renderPackageList(packages) {
  const list = document.getElementById('packages-list');
  if (!list) return;
  list.innerHTML = '';

  const sorted = [...packages].sort((a, b) => a.attr.localeCompare(b.attr));

  sorted.forEach(pkg => {
    // Deactivated packages stay in the list, greyed, at their place: the
    // point is not having to search for them again. In the file they sit in
    // the reserved brick instead of the package list.
    const enabled = pkg.enabled !== false;
    const item = document.createElement('div');
    item.className    = enabled ? 'pkg-item' : 'pkg-item disabled';
    item.dataset.attr = pkg.attr;
    item.dataset.enabled = enabled ? '1' : '0';
    item.dataset.pname = pkg.pname || pkg.attr || '';
    item.dataset.description = pkg.description || pkg.attr || '';

    item.innerHTML = `
      <div class="pkg-item-info">
        <div class="pkg-item-name">${escHtml(pkg.pname || pkg.attr)}</div>
        <div class="pkg-item-desc">${escHtml(pkg.description || pkg.attr)}</div>
      </div>
      <button type="button" class="pkg-toggle${enabled ? ' active' : ''}"
              title="${escHtml(t(enabled ? 'pkg.toggleDeactivate' : 'pkg.toggleActivate'))}"></button>
      <button type="button" class="pkg-delete" title="${escHtml(t('pkg.removeTitle'))}">${niIcon('x')}</button>
    `;

    item.querySelector('.pkg-toggle').addEventListener('click', () => togglePackage(item));
    item.querySelector('.pkg-delete').addEventListener('click', () => removePackage(pkg.attr, item));
    list.appendChild(item);
  });
  updateSectionVisibility();
}

function getPackageListData() {
  return [...document.querySelectorAll('#packages-list .pkg-item')].map(item => ({
    attr: item.dataset.attr || '',
    enabled: item.dataset.enabled !== '0',
    pname: item.dataset.pname || item.dataset.attr || '',
    description: item.dataset.description || item.dataset.attr || '',
  })).filter(pkg => pkg.attr);
}

function togglePackage(itemEl) {
  const enabled = itemEl.dataset.enabled === '0';      // the new state
  itemEl.dataset.enabled = enabled ? '1' : '0';
  itemEl.classList.toggle('disabled', !enabled);
  const btn = itemEl.querySelector('.pkg-toggle');
  btn.classList.toggle('active', enabled);
  btn.title = t(enabled ? 'pkg.toggleDeactivate' : 'pkg.toggleActivate');
  markConfigDirty();
  schedulePreviewUpdate();
}

async function removePackage(attr, itemEl) {
  itemEl.remove();
  markConfigDirty();
  schedulePreviewUpdate();
}

async function addPackage(pkg) {
  const packages = getPackageListData();
  if (packages.some(p => p.attr === pkg.attr)) return false;
  packages.push({
    attr: pkg.attr,
    enabled: true,
    pname: pkg.pname || pkg.attr,
    description: pkg.description || pkg.attr,
  });
  renderPackageList(packages);
  markConfigDirty();
  schedulePreviewUpdate();
  return true;
}

// ── Package search spinner ──────────────────────────────────────────────────
// `nix search` can take a minute on the first call while nixpkgs metadata is
// fetched. A static status line looks like nothing is happening, so the line
// animates and shows the elapsed seconds once the wait becomes noticeable.
const PKG_SPIN_FRAMES = ['|', '/', '-', '\\'];
const PKG_SPIN_MS     = 120;
const PKG_SPIN_SECS_AFTER = 3;   // seconds before the elapsed counter appears

function renderPkgSpinner() {
  const statusEl = document.getElementById('pkg-search-status');
  if (!statusEl) return;
  const frame = PKG_SPIN_FRAMES[pkgSpinFrame % PKG_SPIN_FRAMES.length];
  const secs  = Math.floor((Date.now() - pkgSpinStart) / 1000);
  const parts = [
    `<span class="pkg-spin">${escHtml(frame)}</span>`,
    escHtml(t('pkg.searching', pkgSpinQuery)),
  ];
  if (secs >= PKG_SPIN_SECS_AFTER) parts.push(escHtml(t('pkg.searchElapsed', secs)));
  statusEl.innerHTML = parts.join(' ');
}

function startPkgSpinner(query) {
  stopPkgSpinner();
  pkgSpinQuery = query;
  pkgSpinFrame = 0;
  pkgSpinStart = Date.now();
  document.getElementById('pkg-search-status')?.classList.add('searching');
  renderPkgSpinner();
  pkgSpinTimer = setInterval(() => { pkgSpinFrame++; renderPkgSpinner(); }, PKG_SPIN_MS);
}

/** Stops the animation. Callers set their own status text afterwards; passing
 *  clear=true wipes the line (modal closed, query too short). */
function stopPkgSpinner(clear = false) {
  if (pkgSpinTimer) { clearInterval(pkgSpinTimer); pkgSpinTimer = null; }
  const statusEl = document.getElementById('pkg-search-status');
  if (!statusEl) return;
  statusEl.classList.remove('searching');
  if (clear) statusEl.textContent = '';
}

// ── Package search modal ────────────────────────────────────────────────────
function openPkgModal() {
  stopPkgSpinner(true);
  document.getElementById('pkg-overlay').classList.remove('hidden');
  document.getElementById('pkg-search-input').value = '';
  document.getElementById('pkg-search-results').innerHTML = '';
  document.getElementById('pkg-manual-input').value = '';
  document.getElementById('pkg-search-input').focus();
}

function closePkgModal() {
  clearTimeout(pkgDebounce);
  stopPkgSpinner(true);
  document.getElementById('pkg-overlay').classList.add('hidden');
}

async function addManualPackage() {
  const input = document.getElementById('pkg-manual-input');
  const attr  = input.value.trim();
  if (!attr) return;
  const ok = await addPackage({ attr, pname: attr, version: '', description: '' });
  if (ok) {
    input.value = '';
    showToast(t('pkg.manualAdded', attr), 'success');
  }
}

async function runPkgSearch(query) {
  const statusEl  = document.getElementById('pkg-search-status');
  const resultsEl = document.getElementById('pkg-search-results');
  if (query.length < 2) { stopPkgSpinner(true); resultsEl.innerHTML = ''; return; }

  const seq = ++pkgSearchSeq;
  startPkgSpinner(query);
  resultsEl.innerHTML = '';

  let data;
  try {
    const res = await csrfFetch(`/api/packages/search?q=${encodeURIComponent(query)}`);
    data = await res.json();
  } catch (e) {
    if (seq !== pkgSearchSeq) return;          // superseded, newer search owns the line
    stopPkgSpinner();
    statusEl.textContent = t('pkg.error', String(e.message || e));
    return;
  }
  if (seq !== pkgSearchSeq) return;            // a newer search is already running
  stopPkgSpinner();

  if (data.error) { statusEl.textContent = t('pkg.error', tErr(data.error)); return; }

  const results = data.results || [];
  // Versions come from the nixpkgs of this machine, the links go to the
  // channel of the config - said once, not hidden (finding 9).
  statusEl.textContent = results.length
    ? `${t('pkg.results', results.length)} · ${t('pkg.versionLocal', data.channel || 'unstable')}`
    : '';

  if (results.length === 0) {
    resultsEl.innerHTML = `<p class="pkg-empty">${escHtml(t('pkg.noResults'))}</p>`;
    return;
  }

  const existing = new Set(getPackageListData().map(p => p.attr));

  results.forEach(pkg => {
    const already = existing.has(pkg.attr);
    const row = document.createElement('div');
    row.className = 'pkg-result';
    row.innerHTML = `
      <div class="pkg-result-info">
        <span class="pkg-result-name">${escHtml(pkg.pname || pkg.attr)}</span>
        <span class="pkg-result-version">${escHtml(pkg.version)}</span>
        <div class="pkg-result-desc">${escHtml(pkg.description || '–')}</div>
      </div>
      <div class="pkg-result-actions">
        <a class="pkg-link" href="${escHtml(pkg.url)}" target="_blank" rel="noopener"
           title="${escHtml(t('pkg.detailsLink'))}">${niIcon('external-link')}</a>
        <button class="pkg-add-btn" ${already ? 'disabled' : ''}>
          ${already ? escHtml(t('pkg.alreadyAdded')) : escHtml(t('pkg.add'))}
        </button>
      </div>
    `;
    if (!already) {
      row.querySelector('.pkg-add-btn').addEventListener('click', async () => {
        const ok = await addPackage(pkg);
        if (ok) {
          existing.add(pkg.attr);
          const btn = row.querySelector('.pkg-add-btn');
          btn.disabled    = true;
          btn.textContent = t('pkg.alreadyAdded');
        }
      });
    }
    resultsEl.appendChild(row);
  });
}

function addSectionLineHints() {
  document.querySelectorAll('.sec-toggle').forEach(h3 => {
    const section = h3.closest('section');
    if (!section?.dataset.section) return;
    if (h3.querySelector('.sec-line-hint')) return;
    const hint = document.createElement('small');
    hint.className = 'sec-line-hint';
    h3.appendChild(hint);
  });
}

// ── Ebene-2 Sektionen (Benutzer: Erweitert / Freitext-Nix) ──────────────────
function initLvl2Sections() {
  document.querySelectorAll('.lvl2-section').forEach(el => {
    const key = `nico_lvl2_${el.dataset.lvl2}`;
    if (localStorage.getItem(key) === 'collapsed') el.classList.add('collapsed');
    el.querySelector('.lvl2-toggle').addEventListener('click', () => {
      el.classList.toggle('collapsed');
      localStorage.setItem(key, el.classList.contains('collapsed') ? 'collapsed' : 'open');
    });
  });
}

// ── NixOS-Optionen Hover-Tooltip ──────────────────────────────────────────────

// ── NixOS-Direktlinks pro Sektion ─────────────────────────────────────────────
const _SECTION_NIXOS_KEYS = {
  'SYST':               'networking.hostName',
  'I18N':        'time.timeZone',
  'NETW':             'networking.networkmanager',
  'SERV':             'services.printing',
  'DESK':              'services.xserver',
  'AUDI':                'services.pipewire',
  'USER':             'users.users',
  'PROG':            'environment.systemPackages',
  'FONT':         'fonts.packages',
  'NIXS':         'nix.settings',
  'HOME':         'home-manager',
  'HDWR':             'hardware',
  'VIRT':      'virtualisation.docker',
  'FSYS': 'services.snapper',
};

/** Active sec-docs popup element, used to close it when clicking outside. */
let _activeSecDocsPopup = null;

function _closeSecDocsPopup() {
  if (_activeSecDocsPopup) {
    _activeSecDocsPopup.remove();
    _activeSecDocsPopup = null;
  }
}

function initNixosLinks() {
  document.querySelectorAll('section.collapsible[data-section]').forEach(section => {
    const sectionName = section.dataset.section;
    const key = _SECTION_NIXOS_KEYS[sectionName];
    // Process if there's either a _SECTION_NIXOS_KEYS entry OR a _sectionLinks entry
    if (!key && !_sectionLinks[sectionName]) return;
    const h3 = section.querySelector('.sec-toggle');
    if (!h3 || h3.querySelector('.sec-nixos-link')) return; // already injected

    // Wrap button + popup in a positioned span
    const wrap = document.createElement('span');
    wrap.className = 'sec-docs-wrap';

    const btn = document.createElement('button');
    btn.type        = 'button';
    btn.className   = 'sec-nixos-link';
    btn.title       = t('section.nixosLink');
    btn.innerHTML = niIcon('copy');
    wrap.appendChild(btn);

    btn.addEventListener('click', e => {
      e.stopPropagation();

      // Toggle: close if already open for this section
      if (_activeSecDocsPopup && _activeSecDocsPopup.dataset.section === sectionName) {
        _closeSecDocsPopup();
        return;
      }
      _closeSecDocsPopup();

      // Determine URLs from _sectionLinks or fallback
      const langLinks = (_sectionLinks[sectionName] || {})[currentLang]
                     || (_sectionLinks[sectionName] || {})['en']
                     || {};
      const optionsUrl = langLinks.options
        || `https://search.nixos.org/options?query=${encodeURIComponent(key)}`;
      const wikiUrl    = langLinks.wiki
        || 'https://wiki.nixos.org/wiki/NixOS_Manual';

      const popup = document.createElement('div');
      popup.className       = 'sec-docs-popup';
      popup.dataset.section = sectionName;

      popup.innerHTML = `
        <a href="${escHtml(wikiUrl)}" target="_blank" rel="noopener noreferrer"
           class="sec-docs-link">${escHtml(t('docs.wiki'))}</a>
        <a href="${escHtml(optionsUrl)}" target="_blank" rel="noopener noreferrer"
           class="sec-docs-link">${escHtml(t('docs.options'))}</a>
      `;

      // Close popup when clicking a link
      popup.querySelectorAll('.sec-docs-link').forEach(a => {
        a.addEventListener('click', () => _closeSecDocsPopup());
      });

      document.body.appendChild(popup);
      const rect = btn.getBoundingClientRect();
      popup.style.top = `${rect.bottom + 4}px`;
      popup.style.left = `${rect.left}px`;
      _activeSecDocsPopup = popup;
    });

    h3.appendChild(wrap);
  });

  // Close popup when clicking anywhere outside
  document.addEventListener('click', e => {
    if (_activeSecDocsPopup && !e.target.closest('.sec-docs-popup') && !e.target.closest('.sec-nixos-link')) {
      _closeSecDocsPopup();
    }
  });
}


// ── Colored editor: text mode and brick editor ─────────────────────────────

/** Two layers on top of each other (Martin, 2026-11-20). Below, the text
 *  colored by Prism – the same highlightCode the preview uses, so colors and
 *  search marks look the same everywhere. On top, the textarea with
 *  transparent text: the caret and the selection are its own, what is seen is
 *  the layer below. Both use the same font, padding and no wrapping
 *  (wrap="off"), so every character sits in the same place; the view follows
 *  the textarea's scrolling, and is cut to the textarea's client area so the
 *  scrollbars do not shift it. No third-party editor on purpose.
 *
 *  Expects `ta` next to a `.text-editor-view > code` in a `.text-editor`.
 *  Used by the text mode (sidebar.js, with search and Nix's error spot as
 *  hooks) and by the brick editor (brix.js, with `grow`: as tall as its text,
 *  so only the preview scrolls – Martin, 2026-11-25). Returns the repaint. */
function attachColoredEditor(ta, { afterHighlight, afterBrackets, grow = false } = {}) {
  const view = ta.parentElement.querySelector('.text-editor-view');
  const code = view?.querySelector('code');
  if (!view || !code) return null;
  let pending = false;
  let brackets = [];
  const sync = () => { view.scrollTop = ta.scrollTop; view.scrollLeft = ta.scrollLeft; };
  const fitHeight = () => {
    if (!grow) return;
    const cs = getComputedStyle(ta);
    const lh = parseFloat(cs.lineHeight) || 21.45;
    const lines = ta.value.split('\n').length;
    // Border box: padding, border and a horizontal scrollbar on top.
    const h = lines * lh + parseFloat(cs.paddingTop) + parseFloat(cs.paddingBottom)
            + (ta.offsetHeight - ta.clientHeight);
    ta.style.height = `${Math.ceil(h)}px`;
  };
  const paint = () => {
    pending = false;
    // The trailing newline keeps an empty last line as tall as in the
    // textarea; positions before it are unchanged.
    highlightCode(code, ta.value + '\n');
    afterHighlight?.();
    brackets = pairBrackets(code);
    markBracketAtCaret(ta, brackets);
    afterBrackets?.(view, code);
    sync();
  };
  // Caret moves without input (arrows, click): only the pair mark follows.
  let caretPending = false;
  const caret = () => {
    if (caretPending) return;
    caretPending = true;
    requestAnimationFrame(() => { caretPending = false; markBracketAtCaret(ta, brackets); });
  };
  ['keydown', 'mouseup', 'focus', 'blur'].forEach(ev => ta.addEventListener(ev, caret));
  const fit = () => {
    view.style.right  = `${ta.offsetWidth  - ta.clientWidth}px`;
    view.style.bottom = `${ta.offsetHeight - ta.clientHeight}px`;
    sync();
  };
  ta.addEventListener('input', () => {
    // Height right away, not in the next frame: otherwise a new line first
    // scrolls the textarea by one line and the layers are off for a frame.
    fitHeight();
    if (pending) return;
    pending = true;
    requestAnimationFrame(paint);
  });
  ta.addEventListener('scroll', sync);
  if (window.ResizeObserver) new ResizeObserver(fit).observe(ta);
  fitHeight();
  paint();
  fit();
  return paint;
}

/** Bracket pairs in the editor (Martin, 2026-11-21, "like in Excel").
 *  Read from the layer Prism has just colored: only punctuation tokens
 *  count, so brackets inside strings and comments are left out, while
 *  those inside ${…} take part like code. A bracket without partner gets
 *  .bracket-bad; where a closing bracket meets an opener of another kind,
 *  the closer is the one marked - which bracket is really missing nobody
 *  can know. Returns [{ pos, el, partner }] in text order. */
function pairBrackets(code) {
  const OPEN = { '{': '}', '[': ']', '(': ')' };
  const list = [];
  const walker = document.createTreeWalker(code, NodeFilter.SHOW_TEXT);
  let pos = 0, n;
  while ((n = walker.nextNode())) {
    const s = n.textContent;
    if (s.length === 1 && '{}[]()'.includes(s)) {
      const el = n.parentElement?.closest('.token');
      if (el?.classList.contains('punctuation')) list.push({ ch: s, pos, el, partner: null });
    }
    pos += s.length;
  }
  const stack = [];
  for (const b of list) {
    if (OPEN[b.ch]) { stack.push(b); continue; }
    const top = stack[stack.length - 1];
    if (top && OPEN[top.ch] === b.ch) { stack.pop(); top.partner = b; b.partner = top; }
    else b.el.classList.add('bracket-bad');
  }
  stack.forEach(b => b.el.classList.add('bracket-bad'));
  return list;
}

/** The caret on or right after a bracket with a partner: both are shown.
 *  The character after the caret wins over the one before. */
function markBracketAtCaret(ta, list) {
  list.forEach(b => b.el.classList.remove('bracket-pair'));
  if (document.activeElement !== ta || ta.selectionStart !== ta.selectionEnd) return;
  const at = ta.selectionStart;
  const b = list.find(x => x.pos === at && x.partner)
         || list.find(x => x.pos === at - 1 && x.partner);
  if (!b) return;
  b.el.classList.add('bracket-pair');
  b.partner.el.classList.add('bracket-pair');
}
