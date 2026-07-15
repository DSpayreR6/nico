/** NiCo frontend — code search, toast/diff/viewer helpers, UI event bindings. Split from app.js; classic script sharing the global scope. */
'use strict';

// ── Suche im Nix-Code ────────────────────────────────────────────────────────
//
// Architecture note: currentFiles is an array of file descriptors.
// Adding more files later (multi-host, hardware.nix, etc.) only requires
// pushing additional entries into currentFiles – no search logic changes.

/**
 * Walk all text nodes inside `root`, find regex matches in the full plain text
 * (ignoring Prism token boundaries), and wrap matched segments with
 * <mark class="search-match">.  Returns the number of logical matches found.
 *
 * Handles matches that span multiple Prism span elements (e.g. "services.printing"
 * where "." is a separate punctuation token) by marking each segment individually.
 */
function markTextInElement(root, re) {
  // Collect all text nodes and their positions in the concatenated plain text
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  const textNodes = [];
  let node;
  while ((node = walker.nextNode())) textNodes.push(node);
  if (textNodes.length === 0) return 0;

  let pos = 0;
  const nodeRanges = textNodes.map(tn => {
    const start = pos;
    pos += tn.textContent.length;
    return { node: tn, start, end: pos };
  });

  const fullText = textNodes.map(tn => tn.textContent).join('');

  // Find all match positions in the full plain text
  re.lastIndex = 0;
  const matches = [];
  let m;
  while ((m = re.exec(fullText)) !== null) {
    matches.push({ start: m.index, end: m.index + m[0].length });
  }
  if (matches.length === 0) return 0;

  // For each text node, collect local intervals to mark
  const nodeIntervals = textNodes.map(() => []);
  for (const { start: mStart, end: mEnd } of matches) {
    for (let ni = 0; ni < nodeRanges.length; ni++) {
      const nr = nodeRanges[ni];
      if (nr.end <= mStart || nr.start >= mEnd) continue;
      nodeIntervals[ni].push({
        localStart: Math.max(0, mStart - nr.start),
        localEnd:   Math.min(nr.end - nr.start, mEnd - nr.start),
      });
    }
  }

  // Apply marks to each text node in reverse DOM order (avoids position shifts)
  for (let ni = textNodes.length - 1; ni >= 0; ni--) {
    const intervals = nodeIntervals[ni];
    if (intervals.length === 0) continue;

    const tn   = textNodes[ni];
    const text = tn.textContent;

    // Sort intervals by start desc so we can process right-to-left
    intervals.sort((a, b) => b.localStart - a.localStart);

    const parts = [];
    let lastEnd = text.length;
    for (const { localStart, localEnd } of intervals) {
      if (localEnd < lastEnd) parts.unshift(document.createTextNode(text.slice(localEnd, lastEnd)));
      const mark = document.createElement('mark');
      mark.className   = 'search-match';
      mark.textContent = text.slice(localStart, localEnd);
      parts.unshift(mark);
      lastEnd = localStart;
    }
    if (lastEnd > 0) parts.unshift(document.createTextNode(text.slice(0, lastEnd)));

    const frag = document.createDocumentFragment();
    for (const p of parts) frag.appendChild(p);
    tn.parentNode.replaceChild(frag, tn);
  }

  return matches.length;
}

/** Remove all search marks from the DOM (restores plain text nodes). */
function clearSearchHighlights() {
  document.querySelectorAll('mark.search-match').forEach(m => {
    m.parentNode.replaceChild(document.createTextNode(m.textContent), m);
  });
  // Merge adjacent text nodes so future walker runs work correctly
  currentFiles.forEach(f => {
    const el = document.getElementById(f.containerId);
    if (el) el.normalize();
  });
  currentFiles.forEach(f => { f.matchCount = 0; });
  searchMatches  = [];
  searchMatchIdx = -1;
}

/** Apply search highlights to all file containers. */
function applySearchHighlights(query) {
  clearSearchHighlights();
  if (!query || query.length < 1) return;

  const escaped = query.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');

  currentFiles.forEach(f => {
    f.matchCount = (f.content.match(new RegExp(escaped, 'gi')) || []).length;
    const container = document.getElementById(f.containerId);
    if (container) markTextInElement(container, new RegExp(escaped, 'gi'));
  });

  // Expand any collapsed code sections in the preview that contain matches,
  // so scrollIntoView and navigation work. State is restored on closeSearch().
  document.querySelectorAll('.code-section.collapsed').forEach(sec => {
    if (sec.querySelector('mark.search-match')) sec.classList.remove('collapsed');
  });

  collectSearchMatches();
}

/** Collect all .search-match elements in the active tab for navigation. */
function collectSearchMatches() {
  const active = currentFiles.find(f => f.tabId === activeTab);
  const container = active ? document.getElementById(active.containerId) : null;
  searchMatches  = container ? [...container.querySelectorAll('mark.search-match')] : [];
  searchMatchIdx = searchMatches.length > 0 ? 0 : -1;
  if (searchMatchIdx === 0) highlightActiveMatch();
  updateSearchCount();
}

function highlightActiveMatch() {
  searchMatches.forEach((m, i) => m.classList.toggle('search-match-active', i === searchMatchIdx));
  searchMatches[searchMatchIdx]?.scrollIntoView({ behavior: 'smooth', block: 'center' });
}

function updateSearchCount() {
  const el = document.getElementById('search-count');
  if (!el) return;

  // Use raw-content match count for display (immune to Prism token splitting).
  // searchMatches.length can be inflated when a match spans multiple Prism tokens.
  const activeFile = currentFiles.find(f => f.tabId === activeTab);
  const totalInTab = activeFile?.matchCount ?? 0;
  if (!activeSearch) { el.textContent = ''; return; }
  if (totalInTab === 0) {
    const totalAll = currentFiles.reduce((s, f) => s + f.matchCount, 0);
    el.textContent = totalAll === 0 ? t('search.noResults') : t('search.noResultsHere');
  } else {
    // For navigation index, clamp to totalInTab so display never shows e.g. "4/2"
    const displayIdx = searchMatches.length > 0
      ? Math.min(searchMatchIdx + 1, totalInTab)
      : 1;
    el.textContent = `${displayIdx}/${totalInTab}`;
  }

  // Hint for matches in other files
  const otherMatches = currentFiles
    .filter(f => f.tabId !== activeTab && f.matchCount > 0)
    .map(f => `${f.matchCount} ${t('search.inFile')} ${f.name}`)
    .join(', ');
  if (otherMatches) el.title = t('search.also', otherMatches);
  else              el.title = '';
}

function openSearch() {
  document.getElementById('search-bar').classList.remove('hidden');
  document.getElementById('search-input').focus();
  document.getElementById('search-input').select();
}

function closeSearch() {
  document.getElementById('search-bar').classList.add('hidden');
  activeSearch = '';
  clearSearchHighlights();
  applySectionCollapse(); // restore sections that were expanded for search
  document.getElementById('search-input').value = '';
  document.getElementById('search-count').textContent = '';
}

function runSearch(query) {
  activeSearch = query;
  applySearchHighlights(query);
}

function searchStep(dir) {
  if (searchMatches.length === 0) return;
  searchMatchIdx = (searchMatchIdx + dir + searchMatches.length) % searchMatches.length;
  highlightActiveMatch();
  updateSearchCount();
}

function escHtml(str) {
  return String(str ?? '')
    .replace(/&/g, '&amp;').replace(/</g, '&lt;')
    .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

function showToast(message, type = 'success') {
  const el = document.createElement('div');
  el.className   = `toast toast-${type}`;
  el.textContent = message;
  document.body.appendChild(el);
  el.getBoundingClientRect();
  el.classList.add('visible');
  setTimeout(() => {
    el.classList.remove('visible');
    setTimeout(() => el.remove(), 300);
  }, 3000);
}

// ── Diff viewer ────────────────────────────────────────────────────────────
async function openDiff(fromHash, toHash, title) {
  const label = title || `${fromHash.slice(0, 7)} → ${toHash === 'HEAD' ? 'HEAD' : toHash.slice(0, 7)}`;
  try {
    const res  = await csrfFetch(`/api/git/diff?from=${encodeURIComponent(fromHash)}&to=${encodeURIComponent(toHash)}`);
    const data = await res.json();
    if (data.error) { showToast(data.error, 'error'); return; }
    openViewer(label, _renderDiff(data));
  } catch {
    showToast(t('toast.error'), 'error');
  }
}

function _renderDiff(data) {
  const files = data.files || [];
  if (!files.length) {
    return `<div class="diff-empty">${escHtml(t('diff.noChanges'))}</div>`;
  }
  const statusIcon  = { added: '+', modified: '~', deleted: '−' };
  const statusClass = { added: 'diff-status-added', modified: 'diff-status-modified', deleted: 'diff-status-deleted' };

  let html = '<div class="diff-viewer">';
  let anyFile = false;
  for (const f of files) {
    const icon = statusIcon[f.status]  || '~';
    const cls  = statusClass[f.status] || 'diff-status-modified';

    if (f.lock_updated) {
      anyFile = true;
      html += `
        <div class="diff-file">
          <div class="diff-file-header">
            <span class="${cls}">${icon}</span>
            <span class="diff-filename">${escHtml(f.filename)}</span>
          </div>
          <div class="diff-file-body open">
            <div class="diff-lock-note">${escHtml(t('diff.lockUpdated'))}</div>
          </div>
        </div>`;
      continue;
    }

    const relevant = (f.hunks || []).filter(h => h.type === 'added' || h.type === 'removed');
    if (!relevant.length) continue;
    anyFile = true;
    const lines = relevant.map(h => {
      const lineCls = `diff-line diff-line-${escHtml(h.type)}`;
      const prefix  = h.type === 'added' ? '+' : '−';
      return `<div class="${lineCls}">${escHtml(prefix + ' ' + h.content)}</div>`;
    }).join('');
    html += `
      <div class="diff-file">
        <div class="diff-file-header" onclick="this.nextElementSibling.classList.toggle('open')">
          <span class="${cls}">${icon}</span>
          <span class="diff-filename">${escHtml(f.filename)}</span>
        </div>
        <div class="diff-file-body open">${lines}</div>
      </div>`;
  }
  if (!anyFile) html += `<div class="diff-empty">${escHtml(t('diff.noChanges'))}</div>`;
  html += '</div>';
  return html;
}

// ── Generic content viewer ─────────────────────────────────────────────────
function openViewer(title, html) {
  document.getElementById('viewer-title').textContent = title;
  document.getElementById('viewer-content').innerHTML = html;
  document.getElementById('viewer-overlay').classList.remove('hidden');
}

function closeViewer() {
  document.getElementById('viewer-overlay').classList.add('hidden');
  document.getElementById('viewer-content').innerHTML = '';
}

// ── Event bindings ─────────────────────────────────────────────────────────
function on(id, event, handler) {
  document.getElementById(id)?.addEventListener(event, handler);
}

function bindUI() {
  // Plain-Code-View Toggle
  on('preview-mode-btn', 'click', togglePlainCodeView);

  // Language switcher dropdown (buttons built dynamically by initLangSwitcher)
  on('lang-current-btn', 'click', e => {
    e.stopPropagation();
    document.getElementById('lang-dropdown')?.classList.toggle('hidden');
  });
  document.addEventListener('click', () => {
    document.getElementById('lang-dropdown')?.classList.add('hidden');
    Sidebar.closeTreeContextMenu();
  });
  document.addEventListener('keydown', e => {
    if (e.key === 'Escape') Sidebar.closeTreeContextMenu();
  });

  // Setup
  on('setup-btn',       'click',   () => handleSetup());
  on('nixos-dir-input', 'keydown', e => { if (e.key === 'Enter') handleSetup(); });
  on('confirm-yes-btn',       'click', () => handleSetup(true));
  on('confirm-no-btn',        'click', hideConfirm);
  on('setup-symlink-yes-btn', 'click', () => doSetupSymlink(true));
  on('setup-symlink-no-btn',  'click', () => doSetupSymlink(false));
  on('setup-browse-btn', 'click', () => {
    const cur = document.getElementById('nixos-dir-input').value.trim();
    openDirBrowser(cur || '~', path => {
      document.getElementById('nixos-dir-input').value = path;
    });
  });

  // Dir-Browser
  on('dirbrowser-select-btn', 'click', () => {
    if (_dirBrowserCallback && _dirBrowserCurrent) _dirBrowserCallback(_dirBrowserCurrent);
    closeDirBrowser();
  });
  on('dirbrowser-cancel-btn', 'click', closeDirBrowser);

  // Erststart-Import
  on('import-apply-btn', 'click', () => applyImport(true));  // first-run: no backup needed
  on('import-backup-yes-btn', 'click', () => {
    document.getElementById('import-backup-confirm').classList.add('hidden');
    applyImport(true);
  });
  on('import-backup-cancel-btn', 'click', () => {
    document.getElementById('import-backup-confirm').classList.add('hidden');
    document.getElementById('import-main-buttons').classList.remove('hidden');
  });
  on('import-skip-btn', 'click', () => {
    document.getElementById('import-overlay').classList.add('hidden');
  });

  // Form → live preview + section visibility update
  on('config-form', 'input',  schedulePreviewUpdate);
  on('config-form', 'change', schedulePreviewUpdate);
  on('config-form', 'change', updateSectionVisibility);
  on('config-form', 'input',  updateSectionVisibility);

  // GC sub-options visibility
  on('nix_gc', 'change', e => toggleGcOptions(e.target.checked));

  // Btrfs balance sub-options visibility
  on('btrfs_balance', 'change', e => toggleBtrfsBalanceOptions(e.target.checked));

  // Hardware / Virtualisierung / Backup sub-option visibility
  on('opengl',       'change', e => toggleOpenglOptions(e.target.checked));
  on('boot_loader',      'change', e => { toggleBootEfiOptions(e.target.value !== 'none'); schedulePreviewUpdate(); });
  on('plymouth_enabled', 'change', e => {
    const checked = e.target.checked;
    togglePlymouthOptions(checked);
    if (checked) {
      const cb = document.getElementById('boot_initrd_systemd');
      if (cb && !cb.checked) cb.checked = true;
    }
    schedulePreviewUpdate();
  });
  on('pipewire',  'change', e => { togglePipewireOptions(e.target.checked); schedulePreviewUpdate(); });
  on('virtualbox_guest', 'change', e => { toggleVboxGuestOptions(e.target.checked); schedulePreviewUpdate(); });
  on('docker',       'change', e => toggleDockerOptions(e.target.checked));
  on('podman',       'change', e => togglePodmanOptions(e.target.checked));
  on('libvirtd',     'change', e => toggleLibvirtdOptions(e.target.checked));
  on('snapper_enable', 'change', e => {
    document.getElementById('snapper-area')?.classList.toggle('hidden', !e.target.checked);
    schedulePreviewUpdate();
  });
  document.getElementById('snapper-add-btn')?.addEventListener('click', () => {
    const current = getAllSnapperConfigs();
    current.push({ ...SN_DEFAULTS });
    renderAllSnapperConfigs(current);
    const list  = document.getElementById('snapper-list');
    const cards = list?.querySelectorAll('.extra-user-card');
    if (cards?.length) {
      const last = cards[cards.length - 1];
      last.querySelector('.extra-user-body')?.classList.add('open');
      last.querySelector('.eu-toggle')?.classList.add('open');
    }
    schedulePreviewUpdate();
  });

  on('flatpak_enable', 'change', e => {
    document.getElementById('flatpak-area')?.classList.toggle('hidden', !e.target.checked);
    schedulePreviewUpdate();
  });
  document.getElementById('flatpak-add-flathub-btn')?.addEventListener('click', () => {
    const cur = getAllFlatpakRemotes();
    if (!cur.some(r => r.name === 'flathub')) {
      cur.push({ name: 'flathub', url: 'https://flathub.org/repo/flathub.flatpakrepo' });
      renderFlatpakRemotes(cur);
      schedulePreviewUpdate();
    }
  });
  document.getElementById('flatpak-add-remote-btn')?.addEventListener('click', () => {
    const cur = getAllFlatpakRemotes();
    cur.push({ name: '', url: '' });
    renderFlatpakRemotes(cur);
    schedulePreviewUpdate();
  });
  document.getElementById('extra_locale_enable')?.addEventListener('change', function() {
    document.getElementById('extra-locale-detail')?.classList.toggle('hidden', !this.checked);
  });
  document.getElementById('firewall_tcp_enable')?.addEventListener('change', function() {
    document.getElementById('firewall-tcp-detail')?.classList.toggle('hidden', !this.checked);
  });
  document.getElementById('firewall_udp_enable')?.addEventListener('change', function() {
    document.getElementById('firewall-udp-detail')?.classList.toggle('hidden', !this.checked);
  });
  document.getElementById('state_version')?.addEventListener('input', updateStateVersionStyle);
  document.getElementById('firefox')?.addEventListener('change', function() {
    document.getElementById('firefox-detail')?.classList.toggle('hidden', !this.checked);
  });


  addSectionLineHints();

  // Ebene-2 Sektionen (aufklappbar innerhalb von Sektionen)
  initLvl2Sections();
  initNixosLinks();

  // Weiteren Benutzer hinzufügen
  on('add-user-btn', 'click', () => {
    const current = getAllUsers();
    current.push({ username: '', description: '', initial_password: '', uid: '',
                   groups: [...DEFAULT_EXTRA_USER_GROUPS], shell: 'bash', extra_nix: '' });
    renderAllUsers(current);
    schedulePreviewUpdate();
    // Zum neuen Eintrag scrollen und aufklappen
    const cards = document.querySelectorAll('.extra-user-card');
    const last  = cards[cards.length - 1];
    last?.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    last?.querySelector('.extra-user-body')?.classList.add('open');
    last?.querySelector('.eu-toggle')?.classList.add('open');
    last?.querySelector('.eu-username')?.focus();
  });

  // Sektionen-Steuerleiste
  on('collapse-all-btn', 'click', collapseAll);
  on('expand-all-btn',   'click', expandAll);

  // Section filter dropdown
  on('sec-filter-btn', 'click', _toggleFilterDropdown);
  document.addEventListener('click', _closeFilterDropdown);
  document.querySelectorAll('.sec-filter-option').forEach(opt => {
    opt.addEventListener('click', () => {
      sectionFilter = opt.dataset.filter;
      localStorage.setItem('nico-section-filter', sectionFilter);
      csrfFetch('/api/app/settings', {
        method:  'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body:    JSON.stringify({ section_filter: sectionFilter }),
      }).catch(() => {});
      document.getElementById('sec-filter-dropdown')?.classList.add('hidden');
      updateSectionVisibility();
    });
  });
  // Init dropdown active state
  updateSectionVisibility();

  // Section settings overlay
  on('sections-settings-btn',   'click', openSectionsSettings);
  on('sections-settings-close', 'click', closeSectionsSettings);
  on('sections-settings-overlay','click', e => {
    if (e.target === document.getElementById('sections-settings-overlay')) closeSectionsSettings();
  });
  on('sections-settings-save',  'click', saveSectionsSettings);

  // Admin settings sub-tabs
  document.querySelectorAll('.settings-subtab').forEach(btn => {
    btn.addEventListener('click', () => {
      document.querySelectorAll('.settings-subtab').forEach(b => b.classList.remove('active'));
      document.querySelectorAll('.settings-subtab-panel').forEach(p => p.classList.add('hidden'));
      btn.classList.add('active');
      document.querySelector(`.settings-subtab-panel[data-subtab-panel="${btn.dataset.subtab}"]`)
        ?.classList.remove('hidden');
    });
  });

  // Left panel section toggles (click on h3)
  document.querySelectorAll('.sec-toggle').forEach(h3 => {
    h3.addEventListener('click', () => {
      const section = h3.closest('section');
      if (section?.dataset.section) toggleSection(section.dataset.section);
    });
  });

  async function _saveAllSilent() {
    await Sidebar.flakeSave();
    await _autoSave();
    await _writeNix();
  }

  // NixOS action dropdown
  (function initNixosMenu() {
    const menu  = document.getElementById('nixos-menu');
    const btn   = document.getElementById('nixos-btn');
    const drop  = document.getElementById('nixos-dropdown');
    if (!menu || !btn || !drop) return;

    function toggle(e) {
      e.stopPropagation();
      const opening = drop.classList.contains('hidden');
      drop.classList.toggle('hidden');
      if (opening) _saveAllSilent();
    }
    function close() { drop.classList.add('hidden'); }

    btn.addEventListener('click', toggle);
    document.addEventListener('click', e => { if (!menu.contains(e.target)) close(); });

    on('nixos-save-btn',     'click', () => { close(); openWriteConfirm(); });
    on('nixos-validate-btn', 'click', () => { close(); runValidation(); });
    on('nixos-dryrun-btn',   'click', () => { close(); runSaveAndDryRun(); });
    on('nixos-rebuild-btn',  'click', () => { close(); openRebuild('switch'); });
    on('nixos-git-push-btn', 'click', () => { close(); gitPushManual(); });
    on('nixos-diff-btn',     'click', () => { close(); openDiff('HEAD~1', 'HEAD', t('diff.lastCommitTitle')); });
  })();

  async function gitPushManual() {
    try {
      const res  = await csrfFetch('/api/git/push', { method: 'POST' });
      const data = await res.json();
      if (data.success) {
        showToast(t('git.pushSuccess'), 'success');
      } else {
        _showGitPushErrorModal(data.message || t('toast.error'), data.error_code);
      }
    } catch (e) {
      _showGitPushErrorModal(String(e));
    }
  }

  const _PUSH_ERR_MSGS = {
    NO_KEY:           'git.pushErr.NO_KEY',
    NO_WRITE:         'git.pushErr.NO_WRITE',
    NOT_FOUND:        'git.pushErr.NOT_FOUND',
    NO_NETWORK:       'git.pushErr.NO_NETWORK',
    NOT_FAST_FORWARD: 'git.pushErr.NOT_FAST_FORWARD',
    AUTH_FAILED:      'git.pushErr.AUTH_FAILED',
    UNKNOWN:          'git.pushErr.UNKNOWN',
  };
  function _showGitPushErrorModal(msg, errorCode) {
    const friendly = errorCode && _PUSH_ERR_MSGS[errorCode]
      ? t(_PUSH_ERR_MSGS[errorCode])
      : '';
    const overlay = document.createElement('div');
    overlay.className = 'overlay';
    overlay.innerHTML = `
      <div class="modal">
        <div class="modal-logo">${escHtml(t('git.pushFailedTitle'))}</div>
        ${friendly ? `<p style="margin-bottom:8px">${escHtml(friendly)}</p>` : ''}
        <details style="margin-bottom:12px">
          <summary style="font-size:0.8em;color:var(--fg-muted);cursor:pointer">${escHtml(t('git.pushErrDetail'))}</summary>
          <pre style="white-space:pre-wrap;font-size:0.78em;color:var(--red);margin-top:6px;overflow-x:auto">${escHtml(msg)}</pre>
        </details>
        <div class="confirm-buttons">
          <button type="button" id="_push-err-ok" class="action-btn btn-red">OK</button>
        </div>
      </div>`;
    document.body.appendChild(overlay);
    overlay.querySelector('#_push-err-ok').addEventListener('click', () => overlay.remove());
    overlay.addEventListener('click', e => { if (e.target === overlay) overlay.remove(); });
  }

  // Admin-Bereich
  on('admin-btn',       'click', openAdmin);
  on('admin-close-btn', 'click', closeAdmin);
  on('admin-overlay',   'click', e => { if (e.target.id === 'admin-overlay') closeAdmin(); });
  on('admin-export-btn',   'click', exportZip);
  on('admin-symlink-btn',  'click', doAdminSymlink);

  // Validierungseinstellungen
  on('validation-settings-btn',   'click', openValidationSettings);
  on('validation-settings-close', 'click', closeValidationSettings);
  on('validation-settings-overlay','click', e => {
    if (e.target.id === 'validation-settings-overlay') closeValidationSettings();
  });
  on('validation-settings-save',  'click', saveValidationSettings);
  on('validation-results-close',  'click', closeValidationResults);
  on('validation-results-overlay','click', e => {
    if (e.target.id === 'validation-results-overlay') closeValidationResults();
  });

  on('admin-import-btn', 'click', runAdminImport);
  initImportBrowse();
  initImportManual();
  initZipImport();
  initSettingsImport();
  on('admin-detach-btn', 'click', detachConfig);
  initAdminTabs();
  initSettingsPanel();
  initAdminImportCollapse();

  // Rebuild output modal
  on('rebuild-close-btn', 'click', closeRebuild);
  on('rebuild-overlay',   'click', e => { if (e.target.id === 'rebuild-overlay') closeRebuild(); });

  // Sudo-Passwort-Modal: Hintergrundklick schließt (= Abbruch, handled in promptSudoPassword)
  document.getElementById('sudo-overlay')?.addEventListener('click', e => {
    if (e.target.id === 'sudo-overlay') document.getElementById('sudo-cancel-btn')?.click();
  });

  // Write-Confirm-Dialog (Sicherungspunkt)
  on('write-confirm-btn', 'click', writeFiles);
  on('write-cancel-btn',  'click', closeWriteConfirm);
  on('write-overlay',     'click', e => { if (e.target.id === 'write-overlay') closeWriteConfirm(); });
  on('write-label-input', 'keydown', e => {
    if (e.key === 'Enter')  writeFiles();
    if (e.key === 'Escape') closeWriteConfirm();
  });

  // Dry-run modal (used by admin)
  on('dryrun-close-btn', 'click', () => document.getElementById('dryrun-overlay').classList.add('hidden'));
  on('dryrun-overlay',   'click', e => { if (e.target.id === 'dryrun-overlay') document.getElementById('dryrun-overlay').classList.add('hidden'); });

  // Help dropdown
  on('help-btn', 'click', (e) => {
    e.stopPropagation();
    document.getElementById('help-dropdown').classList.toggle('hidden');
  });
  document.addEventListener('click', () => {
    document.getElementById('help-dropdown').classList.add('hidden');
  });
  on('about-btn', 'click', () => {
    document.getElementById('help-dropdown').classList.add('hidden');
    document.getElementById('about-overlay').classList.remove('hidden');
  });
  on('help-docs-btn', 'click', () => {
    document.getElementById('help-dropdown').classList.add('hidden');
    window.open('/help', '_blank');
  });
  on('about-close-btn', 'click', () => document.getElementById('about-overlay').classList.add('hidden'));
  on('about-overlay', 'click', e => { if (e.target.id === 'about-overlay') document.getElementById('about-overlay').classList.add('hidden'); });
  on('viewer-close-btn', 'click', closeViewer);
  on('viewer-overlay', 'click', e => { if (e.target.id === 'viewer-overlay') closeViewer(); });

  on('diff-show-btn', 'click', () => {
    const from = document.getElementById('diff-from-select')?.value;
    const to   = document.getElementById('diff-to-select')?.value;
    if (from) openDiff(from, to || 'HEAD');
  });

  // Quit
  on('quit-btn', 'click', async () => {
    if (!confirm(t('quit.confirm'))) return;
    await _autoSave();
    await Sidebar.flakeSave();

    // Close-check: prompt push if remote exists and local is ahead/dirty,
    // but only when NiCo loaded a config normally (not in aborted state).
    if (!window._nicoAborted) {
      try {
        const ccRes  = await csrfFetch('/api/git/close-check');
        const ccData = await ccRes.json();
        if (ccData.has_remote && ccData.needs_push) {
          const doPush = await confirmClosePushPrompt();
          if (doPush) {
            const pushRes  = await csrfFetch('/api/git/commit-push', { method: 'POST' });
            const pushData = await pushRes.json();
            if (!pushData.success) {
              alert(t('git.closePrompt.error', pushData.message || ''));
            }
          }
        }
      } catch (_) { /* network down – proceed with shutdown */ }
    }

    await csrfFetch('/api/shutdown', { method: 'POST' }).catch(() => {});
    window.close();
    document.body.innerHTML = `<p style="font-family:monospace;padding:2rem;color:#cdd6f4">${escHtml(t('quit.done'))}</p>`;
  });

  // Tabs
  document.querySelectorAll('.tab').forEach(btn =>
    btn.addEventListener('click', () => switchTab(btn.dataset.tab))
  );

  // Package modal
  on('add-package-btn',  'click',   openPkgModal);
  on('pkg-close-btn',    'click',   closePkgModal);
  on('pkg-overlay',      'click',   e => { if (e.target.id === 'pkg-overlay') closePkgModal(); });
  on('pkg-manual-add-btn', 'click', addManualPackage);
  document.getElementById('pkg-manual-input')?.addEventListener('keydown', e => {
    if (e.key === 'Enter') addManualPackage();
  });
  on('pkg-search-input', 'input',   e => {
    clearTimeout(pkgDebounce);
    pkgDebounce = setTimeout(() => runPkgSearch(e.target.value.trim()), 400);
  });
  on('pkg-search-input', 'keydown', e => { if (e.key === 'Escape') closePkgModal(); });

  // Suche im Nix-Code
  on('search-toggle-btn', 'click', () => {
    const bar = document.getElementById('search-bar');
    if (bar.classList.contains('hidden')) openSearch();
    else closeSearch();
  });
  on('search-close', 'click', closeSearch);
  on('search-prev',  'click', () => searchStep(-1));
  on('search-next',  'click', () => searchStep(+1));
  on('search-input', 'input', e => {
    clearTimeout(searchDebounce);
    searchDebounce = setTimeout(() => runSearch(e.target.value.trim()), 200);
  });
  on('search-input', 'keydown', e => {
    if (e.key === 'Escape')                   { closeSearch(); return; }
    if (e.key === 'Enter' && e.shiftKey)      { searchStep(-1); return; }
    if (e.key === 'Enter')                    { searchStep(+1); }
  });
  // Intercept Ctrl+F inside the app to open our search bar
  document.addEventListener('keydown', e => {
    if ((e.ctrlKey || e.metaKey) && e.key === 'f') {
      e.preventDefault();
      openSearch();
    }
  });

  // Brix dialogs
  on('brix-insert-btn',         'click', openBrixInsert);
  on('brix-insert-confirm-btn', 'click', insertBrix);
  on('brix-insert-cancel-btn',  'click', closeBrixInsert);
  on('brix-insert-overlay',     'click', e => { if (e.target.id === 'brix-insert-overlay') closeBrixInsert(); });
  on('brix-name-input',         'keydown', e => { if (e.key === 'Enter') insertBrix(); if (e.key === 'Escape') closeBrixInsert(); });

  on('brix-move-btn',           'click', openBrixMove);
  on('brix-move-confirm-btn',   'click', moveBrix);
  on('brix-move-cancel-btn',    'click', closeBrixMove);
  on('brix-move-overlay',       'click', e => { if (e.target.id === 'brix-move-overlay') closeBrixMove(); });

  on('brix-rename-confirm-btn', 'click', renameBrix);
  on('brix-rename-cancel-btn',  'click', closeBrixRename);
  on('brix-rename-overlay',     'click', e => { if (e.target.id === 'brix-rename-overlay') closeBrixRename(); });
  on('brix-rename-new',         'keydown', e => { if (e.key === 'Enter') renameBrix(); if (e.key === 'Escape') closeBrixRename(); });

  on('brix-split-confirm-btn',  'click', splitBrix);
  on('brix-split-cancel-btn',   'click', closeBrixSplit);
  on('brix-split-overlay',      'click', e => { if (e.target.id === 'brix-split-overlay') closeBrixSplit(); });
  on('brix-split-line',         'keydown', e => { if (e.key === 'Enter') splitBrix(); if (e.key === 'Escape') closeBrixSplit(); });

  Sidebar.init();
}
