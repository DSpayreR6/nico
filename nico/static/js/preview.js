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

  _isFlakeConfig = !!data.flake_nix;

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

  // Update file corpus for search – only configuration.nix is in the visible preview
  currentFiles = [
    { name: 'configuration.nix', tabId: 'configuration',
      containerId: 'preview-configuration', content: data.configuration_nix || '', matchCount: 0 },
  ];

  // Re-apply active search after re-render
  if (activeSearch) applySearchHighlights(activeSearch);
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
const BRICK_START_RE = /^\s*# <brick:\s*([^/]+?)\s*\/\s*#(\d+)\s+([\w\-]+)\s*>/;
const BRICK_END_RE   = /^\s*# <\/brick:\s*([\w\-]+)\s*>/;

// Canonical list of NiCo sections (mirrors brix.py SECTION_ORDER)
const BRICK_SECTIONS = [
  'Start',
  'Boot', 'System', 'Lokalisierung', 'Netzwerk', 'Services',
  'Desktop', 'Audio', 'Benutzer', 'Programme',
  'Schriftarten', 'Nix & System', 'Hardware', 'Virtualisierung',
  'Dateisystem & Backup', 'Home Manager',
  'End',
];

const HM_PREVIEW_SECTIONS = ['__header__', 'Start', 'Home Manager', 'Shell', 'Pakete', 'End'];

/**
 * Split raw code into typed segments:
 *   { type: 'preamble', lines }
 *   { type: 'section',  name, header, lines }
 *   { type: 'brick',    name, section, order, lines }
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
      current = { type: 'section', name: sectionMatch[1], header: rawLine, lines: [], startLine: lineNum };
    } else if (brickMatch) {
      segments.push(current);
      inBrick  = brickMatch[3];
      current  = {
        type: 'brick', name: brickMatch[3],
        section: brickMatch[1].trim(), order: parseInt(brickMatch[2], 10),
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

      const headBar = document.createElement('div');
      headBar.className = 'code-brix-head';
      headBar.addEventListener('click', e => {
        if (e.target.closest('.brix-actions')) return;
        const parentSection = wrap.closest('.code-section');
        if (parentSection?.classList.contains('collapsed')) {
          // Expand the parent section so the brick becomes visible
          toggleSection(parentSection.dataset.section);
        } else {
          toggleBrix(seg.name, wrap);
        }
      });

      const label = document.createElement('span');
      label.className   = 'code-brix-label';
      label.textContent = `# Brick: ${seg.section} / #${seg.order} · ${seg.name}`;

      const actions = document.createElement('div');
      actions.className = 'brix-actions';

      // ⋮ context menu button
      const menuBtn = document.createElement('button');
      menuBtn.className = 'brix-menu-btn';
      menuBtn.innerHTML = niIcon('more-vertical');
      menuBtn.title = t('brix.menuTitle');

      const menu = document.createElement('div');
      menu.className = 'brix-context-menu hidden';

      const menuItems = [
        { key: 'brix.menuRename', action: () => { _brixContextFile = file; _brixContextFtype = _brixTargetFtype; openBrixRename(seg.name); } },
        { key: 'brix.menuMove',   action: () => { _brixContextFile = file; _brixContextFtype = _brixTargetFtype; openBrixMoveFor(seg.name); } },
        { key: 'brix.menuSplit',  action: () => { _brixContextFile = file; _brixContextFtype = _brixTargetFtype; openBrixSplit(seg.name); } },
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
        if (wasHidden) menu.classList.remove('hidden');
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
      bodyWrapper.className = 'code-with-lines brix-editable';
      bodyWrapper.title = t('brix.clickToEdit');
      bodyWrapper.appendChild(renderLineNums(seg.startLine, seg.lines.length));
      bodyWrapper.appendChild(bodyPre);

      // Click on body → open inline editor (inner lines, without markers)
      bodyWrapper.addEventListener('click', () => {
        if (wrap.querySelector('.brix-inline-editor')) return;  // already open
        const innerContent = seg.lines.slice(1, -1).join('\n');
        openBrixInlineEdit(seg.name, innerContent, wrap, bodyWrapper, file);
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
    return;
  }
  renderAnnotatedPreview(code, containerId, file);
}

// ── Section collapse (left ↔ right sync) ───────────────────────────────────
const _SECTION_SYNC_MAP = {
  'Hosts': 'Outputs-Hosts',
  'Outputs-Hosts': 'Hosts',
};

function toggleSection(name) {
  const wasCollapsed = collapsedSections.has(name);
  if (wasCollapsed) collapsedSections.delete(name);
  else              collapsedSections.add(name);

  const syncName = _SECTION_SYNC_MAP[name];
  if (syncName) {
    if (collapsedSections.has(name)) collapsedSections.add(syncName);
    else                              collapsedSections.delete(syncName);
  }

  localStorage.setItem('nico-collapsed', JSON.stringify([...collapsedSections]));
  applySectionCollapse();

  // Scroll the code section into view when expanding
  if (wasCollapsed) {
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

// ── Section filter & visibility ────────────────────────────────────────────

/** Returns true if a left-panel section has no user-configured values. */
function isSectionEmpty(sec) {
  // System is always considered non-empty (fundamental settings)
  if (sec.dataset.section === 'System') return false;

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
  // Refresh nav to show matches in the newly active tab
  if (activeSearch) collectSearchMatches();
}

// ── Package list ───────────────────────────────────────────────────────────
function renderPackageList(packages) {
  const list = document.getElementById('packages-list');
  if (!list) return;
  list.innerHTML = '';

  const sorted = [...packages].sort((a, b) => a.attr.localeCompare(b.attr));

  sorted.forEach(pkg => {
    const item = document.createElement('div');
    item.className    = 'pkg-item';
    item.dataset.attr = pkg.attr;
    item.dataset.pname = pkg.pname || pkg.attr || '';
    item.dataset.description = pkg.description || pkg.attr || '';

    item.innerHTML = `
      <div class="pkg-item-info">
        <div class="pkg-item-name">${escHtml(pkg.pname || pkg.attr)}</div>
        <div class="pkg-item-desc">${escHtml(pkg.description || pkg.attr)}</div>
      </div>
      <button type="button" class="pkg-delete" title="${escHtml(t('pkg.removeTitle'))}">${niIcon('x')}</button>
    `;

    item.querySelector('.pkg-delete').addEventListener('click', () => removePackage(pkg.attr, item));
    list.appendChild(item);
  });
  updateSectionVisibility();
}

function getPackageListData() {
  return [...document.querySelectorAll('#packages-list .pkg-item')].map(item => ({
    attr: item.dataset.attr || '',
    enabled: true,
    pname: item.dataset.pname || item.dataset.attr || '',
    description: item.dataset.description || item.dataset.attr || '',
  })).filter(pkg => pkg.attr);
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

// ── Package search modal ────────────────────────────────────────────────────
function openPkgModal() {
  document.getElementById('pkg-overlay').classList.remove('hidden');
  document.getElementById('pkg-search-input').value = '';
  document.getElementById('pkg-search-results').innerHTML = '';
  document.getElementById('pkg-search-status').textContent = '';
  document.getElementById('pkg-manual-input').value = '';
  document.getElementById('pkg-search-input').focus();
}

function closePkgModal() {
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
  if (query.length < 2) { statusEl.textContent = ''; resultsEl.innerHTML = ''; return; }

  statusEl.textContent = t('pkg.searching');
  resultsEl.innerHTML  = '';

  const res  = await csrfFetch(`/api/packages/search?q=${encodeURIComponent(query)}`);
  const data = await res.json();

  if (data.error) { statusEl.textContent = t('pkg.error', tErr(data.error)); return; }

  const results = data.results || [];
  statusEl.textContent = results.length ? t('pkg.results', results.length) : '';

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
           title="Details auf search.nixos.org">${niIcon('external-link')}</a>
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
  'System':               'networking.hostName',
  'Lokalisierung':        'time.timeZone',
  'Netzwerk':             'networking.networkmanager',
  'Services':             'services.printing',
  'Desktop':              'services.xserver',
  'Audio':                'services.pipewire',
  'Benutzer':             'users.users',
  'Programme':            'environment.systemPackages',
  'Schriftarten':         'fonts.packages',
  'Nix & System':         'nix.settings',
  'Home Manager':         'home-manager',
  'Hardware':             'hardware',
  'Virtualisierung':      'virtualisation.docker',
  'Dateisystem & Backup': 'services.snapper',
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

