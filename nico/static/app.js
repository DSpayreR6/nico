/**
 * NiCo frontend logic.
 *
 * Section markers in generated Nix:   `  # ── SectionName ──…`
 * Brick block markers in generated Nix: `# <brick: Section / #N name>` … `# </brick: name>`
 *
 * Both are detected and rendered specially in the right-panel preview.
 * Collapsible state is persisted in localStorage and stays in sync between
 * the left-panel sections and the right-panel code view.
 */

'use strict';

// ── Icon helper ───────────────────────────────────────────────────────────
/** Returns an HTML string for a Lucide icon span. Theme-swappable via icons.css. */
function niIcon(name) {
  return `<span class="ni-icon ni-icon-${name}" aria-hidden="true"></span>`;
}

// ── CSRF ──────────────────────────────────────────────────────────────────
const CSRF_TOKEN = document.querySelector('meta[name="csrf-token"]')?.content ?? '';

/** Drop-in fetch replacement that adds the CSRF token to all mutating requests. */
function csrfFetch(url, options = {}) {
  const method  = (options.method || 'GET').toUpperCase();
  const headers = { ...(options.headers || {}) };
  if (method !== 'GET' && method !== 'HEAD') {
    headers['X-CSRF-Token'] = CSRF_TOKEN;
  }
  return fetch(url, { ...options, headers });
}

// ── i18n ──────────────────────────────────────────────────────────────────────
let _lang        = {};
let currentLang  = 'de';

// Flag emoji per language code – extend when new lang files are added
const LANG_FLAGS = { de:'🇩🇪', en:'🇬🇧', es:'🇪🇸', fr:'🇫🇷', ja:'🇯🇵', ru:'🇷🇺', zh:'🇨🇳' };

// ── Section documentation links ────────────────────────────────────────────────
let _sectionLinks = {};

function t(key, ...args) {
  let str = _lang[key] ?? key;
  args.forEach((a, i) => { str = str.replaceAll(`{${i}}`, String(a)); });
  return str;
}

/**
 * Translate an API error code (e.g. "ERR_NO_DIR") to a localised string.
 * Falls back to the raw code if no translation is found.
 * Non-code strings (not starting with "ERR_") are returned as-is,
 * so callers don't need to distinguish between codes and free text.
 */
function tErr(codeOrText) {
  if (!codeOrText) return t('toast.error');
  if (!codeOrText.startsWith('ERR_')) return codeOrText;
  return t(`errors.${codeOrText}`, codeOrText);
}

async function loadLang(code) {
  try {
    const res = await fetch(`/static/lang/${code}.json`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    _lang       = await res.json();
    currentLang = code;
    localStorage.setItem('nico_lang', code);
  } catch (e) {
    console.error('loadLang:', e);
    if (code !== 'de') await loadLang('de');
    return;
  }
  applyTranslations();
  const btn = document.getElementById('lang-current-btn');
  if (btn) btn.textContent = LANG_FLAGS[code] || code.toUpperCase();
  document.getElementById('lang-dropdown')?.classList.add('hidden');
}

function initLangSwitcher(langs) {
  // EN first, then remaining sorted alphabetically
  const sorted = ['en', ...langs.filter(c => c !== 'en').sort()];

  // ── Header dropdown ───────────────────────────────────────────────────────
  const dropdown = document.getElementById('lang-dropdown');
  if (dropdown) {
    dropdown.innerHTML = '';
    sorted.forEach(code => {
      const b = document.createElement('button');
      b.className      = 'lang-btn';
      b.dataset.lang   = code;
      b.title          = code.toUpperCase();
      b.textContent    = (LANG_FLAGS[code] || '') + ' ' + code.toUpperCase();
      b.classList.toggle('active', code === currentLang);
      b.addEventListener('click', () => loadLang(code));
      dropdown.appendChild(b);
    });
  }

  // ── Setup-Overlay Flags ───────────────────────────────────────────────────
  const flagBar = document.getElementById('setup-lang-flags');
  if (flagBar) {
    flagBar.innerHTML = '';
    sorted.forEach(code => {
      const b = document.createElement('button');
      b.className      = 'setup-flag-btn lang-btn';
      b.dataset.lang   = code;
      b.title          = code.toUpperCase();
      b.textContent    = LANG_FLAGS[code] || code.toUpperCase();
      b.classList.toggle('active', code === currentLang);
      b.addEventListener('click', () => loadLang(code));
      flagBar.appendChild(b);
    });
  }
}

function applyTranslations() {
  document.querySelectorAll('[data-i18n]').forEach(el => {
    el.textContent = t(el.dataset.i18n);
  });
  document.querySelectorAll('[data-i18n-html]').forEach(el => {
    el.innerHTML = t(el.dataset.i18nHtml);
  });
  document.querySelectorAll('[data-i18n-placeholder]').forEach(el => {
    el.placeholder = t(el.dataset.i18nPlaceholder);
  });
  document.querySelectorAll('[data-i18n-title]').forEach(el => {
    el.title = t(el.dataset.i18nTitle);
  });
  document.querySelectorAll('[data-i18n-label]').forEach(el => {
    el.label = t(el.dataset.i18nLabel);
  });
  document.querySelector('html').lang = currentLang;
  document.querySelectorAll('.lang-btn').forEach(btn => {
    btn.classList.toggle('active', btn.dataset.lang === currentLang);
  });
}

// ── State ──────────────────────────────────────────────────────────────────
let activeTab       = 'configuration';
let previewDebounce = null;
let plainCodeView   = false;   // true = Brix-/Sektions-Marker aus Code ausgeblendet
let _startGuardApproved = '';

// Section filter: 'all' | 'non-empty' | 'settings'
let sectionFilter  = localStorage.getItem('nico-section-filter') || 'all';
let hiddenSections = [];   // section names to hide when filter = 'settings'
let _coFormReady   = false;
let _coLoadedPath  = 'configuration.nix';

// ── Brix target file – tracks which .nix file brix operations affect ────────
let _brixTargetFile  = 'configuration.nix';   // changes when flake panel opens
let _brixContextFile = 'configuration.nix';   // set when a dialog opens from context menu
let _brixTargetFtype = 'co';                  // ftype of current brix target ('co','fl','hm')
let _brixContextFtype = 'co';                 // ftype of context file

// ── Multi-Host State ───────────────────────────────────────────────────────
let _activeHost     = '';          // '' = defaults, 'nix-desktop' etc.

// Search state
// currentFiles is the source of truth for multi-file search.
// Each entry: { name, tabId, containerId, content, matchCount }
// Today: configuration.nix + flake.nix. Later: any number of files.
let currentFiles   = [];
let activeSearch   = '';
let searchMatches  = [];   // visible .search-match elements in active tab
let searchMatchIdx = -1;
let searchDebounce = null;
let pkgDebounce     = null;
let _treeContextMenu = null;

// Section names that are currently collapsed – always start fully collapsed
const collapsedSections = new Set();

// Brix block names that are individually collapsed (persisted in localStorage)
const collapsedBrix = (() => { try { return new Set(JSON.parse(localStorage.getItem('nico-collapsed-brix') || '[]')); } catch { return new Set(); } })();

// ── Init ───────────────────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', async () => {
  await loadLang(localStorage.getItem('nico_lang') || 'de');
  // Load available languages and build lang switchers dynamically
  fetch('/api/langs')
    .then(r => r.json())
    .then(langs => initLangSwitcher(langs))
    .catch(() => initLangSwitcher(['de', 'en']));
  // Load section documentation links in parallel with the rest of the startup
  fetch('/static/data/section_links.json')
    .then(r => r.ok ? r.json() : {})
    .then(data => { _sectionLinks = data; initNixosLinks(); })  // re-run for sections that need _sectionLinks (e.g. flake)
    .catch(() => { /* section links optional, fallback to options URL */ });
  // Load app settings (code view default, hidden sections)
  try {
    const appCfg = await fetch('/api/app/settings').then(r => r.json());
    plainCodeView = !!(appCfg.code_view_plain);
    if (Array.isArray(appCfg.hidden_sections)) hiddenSections = appCfg.hidden_sections;
    if (typeof appCfg.section_filter === 'string') {
      sectionFilter = appCfg.section_filter;
      localStorage.setItem('nico-section-filter', sectionFilter);
    }
  } catch { /* Fallback */ }
  applyPlainCodeViewBtn();
  updateSectionVisibility();
  bindUI();
  checkStatus();
});

// ── Directory browser ────────────────────────────────────────────────────────
let _dirBrowserCallback = null;
let _dirBrowserCurrent  = null;

function openDirBrowser(startPath, callback, quickLinks) {
  _dirBrowserCallback = callback;

  const ql = document.getElementById('dirbrowser-quicklinks');
  if (quickLinks && quickLinks.length) {
    ql.innerHTML = '';
    for (const {label, path} of quickLinks) {
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.textContent = label;
      btn.addEventListener('click', () => loadDirBrowser(path));
      ql.appendChild(btn);
    }
    ql.classList.remove('hidden');
  } else {
    ql.innerHTML = '';
    ql.classList.add('hidden');
  }

  document.getElementById('dirbrowser-overlay').classList.remove('hidden');
  loadDirBrowser(startPath);
}

async function loadDirBrowser(path) {
  try {
    const res  = await fetch(`/api/fs/ls?path=${encodeURIComponent(path)}`);
    const data = await res.json();
    if (data.error) return;

    _dirBrowserCurrent = data.path;
    document.getElementById('dirbrowser-path').textContent = data.path;

    const list = document.getElementById('dirbrowser-list');
    list.innerHTML = '';

    if (data.parent) {
      const li = document.createElement('li');
      li.className = 'dirbrowser-item dirbrowser-up';
      li.textContent = '..';
      li.addEventListener('click', () => loadDirBrowser(data.parent));
      list.appendChild(li);
    }
    for (const dir of data.dirs) {
      const li = document.createElement('li');
      li.className = 'dirbrowser-item';
      li.textContent = dir;
      li.addEventListener('click', () => loadDirBrowser(data.path + '/' + dir));
      list.appendChild(li);
    }
  } catch (e) {
    console.error('loadDirBrowser:', e);
  }
}

function closeDirBrowser() {
  document.getElementById('dirbrowser-overlay').classList.add('hidden');
  const ql = document.getElementById('dirbrowser-quicklinks');
  ql.innerHTML = '';
  ql.classList.add('hidden');
  _dirBrowserCallback = null;
  _dirBrowserCurrent  = null;
}

// ── Status / setup ─────────────────────────────────────────────────────────
async function checkStatus() {
  try {
    const res  = await csrfFetch('/api/status');
    const data = await res.json();
    if (data.setup_complete) {
      if (data.needs_import) {
        showApp(data.nixos_config_dir);
        // Verzeichnis vorhanden, aber kein configuration.nix / flake.nix
        await showImportOverlay(false);
      } else {
        const ok = await ensureGitStartGuard(data.nixos_config_dir);
        if (!ok) return;
        showApp(data.nixos_config_dir);
        await loadConfig().catch(e => console.error('loadConfig:', e));
        Sidebar.setActiveFile('configuration.nix', 'configuration.nix');
        checkGitStatus();
        checkGitRemoteStatus();
      }
    } else {
      showSetupOverlay();
    }
  } catch (e) {
    console.error('checkStatus:', e);
    showSetupOverlay();
  }
}

// State nach erfolgreichem Setup-API-Aufruf, für Fortsetzung nach Symlink-Schritt
let _pendingSetupData = null;

async function handleSetup(createIfMissing = false) {
  const dir     = document.getElementById('nixos-dir-input').value.trim();
  const errorEl = document.getElementById('setup-error');
  errorEl.classList.add('hidden');
  hideConfirm();

  if (!dir) {
    errorEl.textContent = t('setup.errNoDir');
    errorEl.classList.remove('hidden');
    return;
  }

  let res, data;
  try {
    res  = await csrfFetch('/api/setup', {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify({ nixos_config_dir: dir, create_if_missing: createIfMissing }),
    });
    data = await res.json();
  } catch (e) {
    errorEl.textContent = t('setup.errUnknown');
    errorEl.classList.remove('hidden');
    return;
  }

  if (data.needs_confirmation) { showConfirm(data.path); return; }

  if (!res.ok || data.error) {
    errorEl.textContent = tErr(data.error) || t('setup.errUnknown');
    errorEl.classList.remove('hidden');
    return;
  }

  // If a new directory was created (not /etc/nixos), offer import FIRST, then symlink
  const isEtcNixos = dir.replace(/\/+$/, '') === '/etc/nixos';
  if (data.dir_created && !isEtcNixos) {
    _pendingSetupData = data;
    // Import anbieten (auch ohne /etc/nixos) – Symlink-Schritt im Hintergrund
    await showImportOverlay(false);
    showSymlinkStep();
    return;
  }

  await _finishSetup(data);
}

function showSymlinkStep() {
  document.getElementById('setup-input-section').classList.add('hidden');
  document.getElementById('setup-symlink-step').classList.remove('hidden');
  document.getElementById('setup-symlink-error').classList.add('hidden');
}

async function doSetupSymlink(createSymlink) {
  if (createSymlink) {
    const errEl = document.getElementById('setup-symlink-error');
    errEl.classList.add('hidden');

    const nonce = await acquireSudoNonce();
    if (nonce === null) return;  // abgebrochen

    try {
      const res  = await csrfFetch('/api/symlink/create', {
        method:  'POST',
        headers: { 'Content-Type': 'application/json' },
        body:    JSON.stringify({ copy_files: true, sudo_nonce: nonce }),
      });
      const data = await res.json();
      if (!res.ok || data.error) {
        errEl.textContent = tErr(data.error) || t('setup.errUnknown');
        errEl.classList.remove('hidden');
        return;
      }
    } catch (e) {
      errEl.textContent = t('setup.errUnknown');
      errEl.classList.remove('hidden');
      return;
    }
  }
  document.getElementById('setup-symlink-step').classList.add('hidden');
  await _finishSetup(_pendingSetupData, true);  // Import war bereits vor Symlink-Schritt
  _pendingSetupData = null;
}

async function _finishSetup(data, skipImport = false) {
  // First-run import offer (only when /etc/nixos/configuration.nix exists)
  // skipImport=true wenn der Import bereits vor dem Symlink-Schritt angeboten wurde
  if (!skipImport) await maybeOfferImport();

  // Kategorisiere alle vorhandenen .nix-Dateien (no-op für leere Verzeichnisse)
  await categorizeFiles();

  const ok = await ensureGitStartGuard(data.nixos_config_dir);
  if (!ok) return;

  showApp(data.nixos_config_dir);
  await loadConfig();
  checkGitStatus();

  // Inform user if hardware-configuration.nix was found/copied
  if (data.hw_copied) {
    showToast(t('setup.hwCopied'), 'success');
  } else if (data.hw_present) {
    showToast(t('setup.hwPresent'), 'success');
  }
}

// ── Erststart-Import ───────────────────────────────────────────────────────
let _importPreview = null;

/**
 * Zeigt den Import-Dialog.
 * requireEtcNixos=true  → alter Pfad: Dialog nur wenn /etc/nixos/configuration.nix da ist
 * requireEtcNixos=false → immer zeigen; Apply-Button wird ausgeblendet wenn /etc/nixos fehlt
 */
async function showImportOverlay(requireEtcNixos = false) {
  // /etc/nixos verfügbar?
  let etcAvailable = false;
  let checkData    = null;
  try {
    const res = await csrfFetch('/api/import/check');
    checkData     = await res.json();
    etcAvailable  = checkData.available;
  } catch (e) { /* ignore */ }

  if (requireEtcNixos && !etcAvailable) return;

  // Dialog-Zustand zurücksetzen
  document.getElementById('import-recognized-wrap')?.classList.add('hidden');
  document.getElementById('import-brix-info')?.classList.add('hidden');
  document.getElementById('import-hw-info')?.classList.add('hidden');
  document.getElementById('import-details')?.classList.add('hidden');
  document.getElementById('import-backup-confirm')?.classList.add('hidden');
  document.getElementById('import-main-buttons')?.classList.remove('hidden');
  document.getElementById('import-manual-row')?.classList.add('hidden');
  document.getElementById('import-intro-text')?.classList.remove('hidden');
  document.getElementById('import-overlay-title').setAttribute('data-i18n', 'import.title');
  document.getElementById('import-overlay-title').textContent = t('import.title');

  // Apply-Button nur wenn /etc/nixos vorhanden
  const applyBtn = document.getElementById('import-apply-btn');
  if (applyBtn) applyBtn.classList.toggle('hidden', !etcAvailable);

  if (etcAvailable) {
    // Vorschau laden
    try {
      const res = await csrfFetch('/api/import/preview', { method: 'POST' });
      _importPreview = await res.json();
      if (!_importPreview.error) {
        const rec     = _importPreview.recognized || {};
        const recKeys = Object.keys(rec);
        const recWrap = document.getElementById('import-recognized-wrap');
        const recCount = document.getElementById('import-recognized-count');
        if (recKeys.length > 0 && recWrap) {
          if (recCount) recCount.textContent = recKeys.length;
          recWrap.classList.remove('hidden');
        }
        if (_importPreview.has_rest_brix) document.getElementById('import-brix-info')?.classList.remove('hidden');
        if (_importPreview.has_hardware)  document.getElementById('import-hw-info')?.classList.remove('hidden');
        document.getElementById('import-details')?.classList.remove('hidden');
      }
    } catch (e) { console.warn('import/preview failed:', e); }

    if (checkData?.config_path) {
      document.getElementById('import-intro-text').textContent =
        t('import.introWithPath', checkData.config_path);
    }
  } else {
    document.getElementById('import-intro-text').textContent = t('import.introNoSource');
  }

  document.getElementById('import-overlay').classList.remove('hidden');
}

async function maybeOfferImport() {
  await showImportOverlay(true);
}

async function applyImport(confirmed = false) {
  try {
    const body = confirmed ? { confirmed: true } : {};
    const res  = await csrfFetch('/api/import/apply', {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify(body),
    });
    const data = await res.json();
    if (data.needs_backup_confirmation) {
      document.getElementById('import-main-buttons').classList.add('hidden');
      document.getElementById('import-backup-confirm-text').textContent =
        t('import.backupConfirm', data.zip_name);
      document.getElementById('import-backup-confirm').classList.remove('hidden');
      return;
    }
    document.getElementById('import-overlay').classList.add('hidden');
    if (data.success) {
      await categorizeFiles();
      await loadConfig();
      loadTree();
      showToast(t('import.success'), 'success');
    } else {
      showToast(tErr(data.error), 'error');
    }
  } catch (e) {
    showToast(t('toast.error'), 'error');
  }
}

async function categorizeFiles() {
  try {
    await csrfFetch('/api/categorize', { method: 'POST' });
  } catch (e) {
    console.warn('categorize:', e);
  }
}

function initImportManual() {
  let _importCurrentPath = '/';
  let _checkedConfigPath = null;

  async function loadImportDirBrowser(path) {
    try {
      const res  = await fetch(`/api/fs/ls?path=${encodeURIComponent(path)}`);
      const data = await res.json();
      if (data.error) return;

      _importCurrentPath = data.path;
      document.getElementById('import-inline-path').textContent = data.path;

      const list = document.getElementById('import-inline-list');
      list.innerHTML = '';

      if (data.parent) {
        const li = document.createElement('li');
        li.className = 'dirbrowser-item dirbrowser-up';
        li.textContent = '..';
        li.addEventListener('click', () => loadImportDirBrowser(data.parent));
        list.appendChild(li);
      }
      for (const dir of data.dirs) {
        const li = document.createElement('li');
        li.className = 'dirbrowser-item';
        li.textContent = dir;
        li.addEventListener('click', () => loadImportDirBrowser(data.path + '/' + dir));
        list.appendChild(li);
      }
    } catch (e) {
      console.error('loadImportDirBrowser:', e);
    }
  }

  function showChooseState() {
    document.getElementById('import-manual-choose').classList.remove('hidden');
    document.getElementById('import-manual-found').classList.add('hidden');
    document.getElementById('import-manual-notfound').classList.add('hidden');
    document.getElementById('import-manual-result').classList.add('hidden');
    _checkedConfigPath = null;
  }

  on('import-manual-btn', 'click', () => {
    document.getElementById('import-intro-text')?.classList.add('hidden');
    document.getElementById('import-overlay-title').setAttribute('data-i18n', 'import.manualTitle');
    document.getElementById('import-overlay-title').textContent = t('import.manualTitle');
    document.getElementById('import-details')?.classList.add('hidden');
    document.getElementById('import-main-buttons')?.classList.add('hidden');
    document.getElementById('import-manual-row')?.classList.remove('hidden');
    showChooseState();
    loadImportDirBrowser('/');
  });

  on('import-manual-back-btn', 'click', () => {
    document.getElementById('import-manual-row')?.classList.add('hidden');
    document.getElementById('import-intro-text')?.classList.remove('hidden');
    document.getElementById('import-overlay-title').setAttribute('data-i18n', 'import.title');
    document.getElementById('import-overlay-title').textContent = t('import.title');
    document.getElementById('import-details')?.classList.remove('hidden');
    document.getElementById('import-main-buttons')?.classList.remove('hidden');
    showChooseState();
  });

  // Schritt 1: Prüfen ob configuration.nix im aktuellen Verzeichnis liegt
  on('import-manual-run-btn', 'click', async () => {
    const dir = _importCurrentPath;
    const res  = await fetch(`/api/import/exists?path=${encodeURIComponent(dir)}`);
    const data = await res.json();

    document.getElementById('import-manual-choose').classList.add('hidden');

    if (!data.exists) {
      document.getElementById('import-manual-notfound-text').textContent =
        t('import.notFound').replace('{dir}', dir);
      document.getElementById('import-manual-notfound').classList.remove('hidden');
      return;
    }

    _checkedConfigPath = data.config_path;
    document.getElementById('import-manual-found-text').textContent =
      t('import.foundConfirm').replace('{path}', data.config_path);
    document.getElementById('import-manual-found').classList.remove('hidden');
  });

  on('import-manual-found-back-btn', 'click', showChooseState);
  on('import-manual-notfound-back-btn', 'click', showChooseState);

  // Schritt 2: Tatsächlich importieren
  on('import-manual-confirm-btn', 'click', async () => {
    if (!_checkedConfigPath) return;
    const resultEl = document.getElementById('import-manual-result');
    document.getElementById('import-manual-found').classList.add('hidden');
    resultEl.textContent = '…';
    resultEl.classList.remove('hidden');

    const res  = await csrfFetch('/api/import/run', {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify({ config_path: _checkedConfigPath }),
    });
    const data = await res.json();

    if (data.needs_backup_confirmation) {
      resultEl.classList.add('hidden');
      document.getElementById('import-manual-backup-text').textContent =
        t('import.backupConfirm', data.zip_name);
      document.getElementById('import-manual-backup-confirm').classList.remove('hidden');
      return;
    }

    if (!res.ok || data.error) {
      resultEl.innerHTML = `<span class="import-err">${escHtml(tErr(data.error))}</span>`;
      return;
    }

    document.getElementById('import-overlay').classList.add('hidden');
    await categorizeFiles();
    await loadConfig();
    loadTree();
    showToast(t('import.success'), 'success');
  });

  // Backup-Bestätigung (manueller Import)
  on('import-manual-backup-yes-btn', 'click', async () => {
    document.getElementById('import-manual-backup-confirm').classList.add('hidden');
    const resultEl = document.getElementById('import-manual-result');
    resultEl.textContent = '…';
    resultEl.classList.remove('hidden');
    const res  = await csrfFetch('/api/import/run', {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify({ config_path: _checkedConfigPath, confirmed: true }),
    });
    const data = await res.json();
    if (!res.ok || data.error) {
      resultEl.innerHTML = `<span class="import-err">${escHtml(tErr(data.error))}</span>`;
      return;
    }
    document.getElementById('import-overlay').classList.add('hidden');
    await categorizeFiles();
    await loadConfig();
    loadTree();
    showToast(t('import.success'), 'success');
  });
  on('import-manual-backup-cancel-btn', 'click', () => {
    document.getElementById('import-manual-backup-confirm').classList.add('hidden');
    document.getElementById('import-manual-found').classList.remove('hidden');
  });
}

function showConfirm(path) {
  document.getElementById('confirm-path').textContent = path;
  document.getElementById('setup-confirm').classList.remove('hidden');
  document.getElementById('setup-input-section').classList.add('hidden');
}

function hideConfirm() {
  document.getElementById('setup-confirm').classList.add('hidden');
  document.getElementById('setup-input-section').classList.remove('hidden');
}

// ── Config load / save ─────────────────────────────────────────────────────
async function loadConfig() {
  _activeHost = '';
  _brixTargetFile  = 'configuration.nix';
  _brixContextFile = 'configuration.nix';
  _brixTargetFtype = 'co';
  _brixContextFtype = 'co';
  hideIntegrityWarning();
  await _populateCoFormFromFile('configuration.nix');

  // Collapse everything on (re)load
  document.querySelectorAll('section.collapsible[data-section]').forEach(s =>
    collapsedSections.add(s.dataset.section));
  // Also add code-only sections (Home Manager) that have no left-panel element
  BRICK_SECTIONS.forEach(s => collapsedSections.add(s));
  collapsedSections.add('__header__');
  applySectionCollapse();
  await updatePreview();
}

// ── Admin-Bereich ──────────────────────────────────────────────────────────
let _activeAdminTab = 'einstellungen';

async function fetchGitStartCheck() {
  const res = await csrfFetch('/api/git/start-check');
  return await res.json();
}

function _gitGuardNeedsDialog(check) {
  return ['dirty', 'behind', 'diverged', 'error', 'remote_no_upstream'].includes(check?.state);
}

function _gitGuardSeverity(check) {
  if (check.state === 'diverged') return 'diverged';
  if (check.state === 'behind')   return 'behind';
  if (check.state === 'dirty')    return 'dirty';
  return 'error';
}

function _gitGuardMessage(check) {
  if (check.state === 'behind') {
    return {
      title: t('git.startGuardBehindTitle'),
      body: t('git.startGuardBehindBody', check.behind),
      recommendation: t('git.startGuardSyncRecommendation'),
    };
  }
  if (check.state === 'diverged') {
    return {
      title: t('git.startGuardDivergedTitle'),
      body: t('git.startGuardDivergedBody', check.ahead, check.behind),
      recommendation: t('git.startGuardDivergedRecommendation'),
    };
  }
  if (check.state === 'ahead') {
    return {
      title: t('git.guard.aheadTitle'),
      body: t('git.guard.aheadBody', check.ahead),
      recommendation: '',
    };
  }
  if (check.state === 'dirty') {
    return {
      title: t('git.startGuardDirtyTitle'),
      body: t('git.startGuardDirtyBody'),
      recommendation: t('git.startGuardDirtyRecommendation'),
    };
  }
  if (check.state === 'remote_no_upstream') {
    return {
      title: t('git.startGuardNoUpstreamTitle'),
      body: t('git.startGuardNoUpstreamBody', check.local_branch || '?'),
      recommendation: '',
    };
  }
  return {
    title: t('git.startGuardErrorTitle'),
    body: t('git.startGuardErrorBody', check.detail || 'git fetch'),
    recommendation: t('git.startGuardErrorRecommendation'),
  };
}

function showRemoteBranchPicker(branches, localBranch) {
  return new Promise(resolve => {
    function _remoteBranchLabel(branch) {
      const slash = branch.indexOf('/');
      return slash >= 0 ? branch.slice(slash + 1) : branch;
    }

    const overlay = document.createElement('div');
    overlay.className = 'overlay';
    overlay.innerHTML = `
      <div class="dialog">
        <h2 class="dialog-title">${escHtml(t('git.branchPickTitle'))}</h2>
        <p class="dialog-info">${escHtml(t('git.branchPickBody', localBranch || '?'))}</p>
        <select id="_gs-branch-select" class="settings-path-input" style="width:100%;margin-top:12px">
          ${branches.map(branch => `<option value="${escHtml(branch)}">${escHtml(_remoteBranchLabel(branch))}</option>`).join('')}
        </select>
        <div class="dialog-actions">
          <button id="_gs-branch-cancel" class="btn-secondary">${escHtml(t('unsaved.cancel'))}</button>
          <button id="_gs-branch-ok" class="btn-primary">${escHtml(t('git.branchPickConfirm'))}</button>
        </div>
      </div>`;
    document.body.appendChild(overlay);
    overlay.querySelector('#_gs-branch-cancel')?.addEventListener('click', () => {
      overlay.remove();
      resolve(null);
    });
    overlay.querySelector('#_gs-branch-ok')?.addEventListener('click', () => {
      const value = overlay.querySelector('#_gs-branch-select')?.value || '';
      overlay.remove();
      resolve(value || null);
    });
  });
}

function showAbortedState(configDir) {
  window._nicoAborted = true;
  showApp(configDir || '');
  document.querySelector('aside.settings-panel')?.style.setProperty('display', 'none');
  document.getElementById('sidebar')?.style.setProperty('display', 'none');
  document.querySelector('.tab-bar')?.style.setProperty('display', 'none');
  const previewPanel = document.getElementById('preview-panel');
  if (previewPanel) {
    previewPanel.innerHTML = `<div style="display:flex;align-items:center;justify-content:center;height:100%;color:var(--subtext0);font-family:monospace;padding:2rem;text-align:center;white-space:pre-line">${escHtml(t('git.aborted.info'))}</div>`;
  }
}

function showGitStartGuard(check, configDir) {
  return new Promise(resolve => {
    const msg     = _gitGuardMessage(check);
    const isError = check.state === 'error';
    // Scenario 2: behind / dirty / diverged → two action cards + abort
    const isScenario2 = ['behind', 'dirty', 'diverged'].includes(check.state);
    const isNoUpstream = check.state === 'remote_no_upstream';

    const pathHtml = configDir
      ? `<p class="git-guard-path">${escHtml(configDir)}</p>`
      : '';

    let cardsHtml = '';
    let secondaryHtml = '';

    if (isScenario2) {
      cardsHtml = `
        <div class="git-guard-card">
          <div class="git-guard-card-info">
            <div class="git-guard-card-title">${escHtml(t('git.guard.s2.fetchTitle'))}</div>
            <div class="git-guard-card-desc">${escHtml(t('git.guard.s2.fetchDesc'))}</div>
          </div>
          <button type="button" data-card="fetch" class="action-btn btn-green">${escHtml(t('git.guard.s2.fetchBtn'))}</button>
        </div>
        <div class="git-guard-card git-guard-card--warn">
          <div class="git-guard-card-info">
            <div class="git-guard-card-title">${escHtml(t('git.guard.s2.sendTitle'))}</div>
            <div class="git-guard-card-desc">${escHtml(t('git.guard.s2.sendDesc'))}</div>
          </div>
          <button type="button" data-card="send" class="action-btn btn-red">${escHtml(t('git.guard.s2.sendBtn'))}</button>
        </div>`;
      secondaryHtml = `<button type="button" id="_gs-abort" class="btn-surface">${escHtml(t('git.guard.s2.abort'))}</button>`;
    } else if (isNoUpstream) {
      cardsHtml = `
        <div class="git-guard-card">
          <div class="git-guard-card-info">
            <div class="git-guard-card-title">${escHtml(t('git.guard.noUpstream.fetchTitle'))}</div>
            <div class="git-guard-card-desc">${escHtml(t('git.guard.noUpstream.fetchDesc'))}</div>
          </div>
          <button type="button" data-card="fetch-connect" class="action-btn btn-green">${escHtml(t('git.guard.noUpstream.fetchBtn'))}</button>
        </div>`;
      secondaryHtml = `
        <button type="button" id="_gs-abort" class="btn-surface">${escHtml(t('git.startGuardCancel'))}</button>
        <button type="button" id="_gs-open"  class="btn-surface">${escHtml(t('git.guard.noUpstream.keepLocal'))}</button>`;
    } else {
      // error state: offer local open or abort
      secondaryHtml = `
        <button type="button" id="_gs-abort"  class="btn-surface">${escHtml(t('git.startGuardCancel'))}</button>
        <button type="button" id="_gs-open"   class="btn-surface">${escHtml(t('git.startGuardProceed'))}</button>`;
    }

    const recHtml = isError
      ? `<p class="confirm-text" style="border-left-color:var(--red)">${escHtml(msg.recommendation)}</p>`
      : '';
    const bodyHtml = isScenario2 ? '' : `<p>${escHtml(msg.body)}</p>`;

    function _gsiCommitRow(c) {
      return `<div class="gsi-commit">
        <span class="gsi-hash">${escHtml(c.hash)}</span>
        <span class="gsi-date">${escHtml(c.date)}</span>
        <span class="gsi-author">${escHtml(c.author)}</span>
        <span class="gsi-msg">${escHtml(c.message)}</span>
      </div>`;
    }

    function _gsiLastRow(label, branch, info) {
      const infoHtml = info && info.hash
        ? `<span class="gsi-hash">${escHtml(info.hash)}</span>
           <span class="gsi-date">${escHtml(info.date)}</span>
           <span class="gsi-author">${escHtml(info.author)}</span>
           <span class="gsi-msg">${escHtml(info.message)}</span>`
        : `<span class="gsi-none">${escHtml(t('git.guard.status.noCommit'))}</span>`;
      return `<div class="gsi-last-row">
        <span class="gsi-last-label">${label}</span>
        <code class="gsi-branch">${escHtml(branch)}</code>
        ${infoHtml}
      </div>`;
    }

    let statusHtml = '';
    const parts = [];

    // Always: last state comparison
    if (check.last_local || check.last_remote) {
      const sameCommit = check.last_local && check.last_remote
        && check.last_local.hash === check.last_remote.hash;
      const summaryRows = sameCommit
        ? `<div class="gsi-last-row">
            <span class="gsi-last-label">${escHtml(t('git.guard.status.bothSame'))}</span>
            <code class="gsi-branch">${escHtml(check.local_branch || '?')}</code>
            <span class="gsi-hash">${escHtml(check.last_local.hash)}</span>
            <span class="gsi-date">${escHtml(check.last_local.date)}</span>
            <span class="gsi-author">${escHtml(check.last_local.author)}</span>
            <span class="gsi-msg">${escHtml(check.last_local.message)}</span>
           </div>`
        : `${_gsiLastRow(t('git.guard.status.remote'), check.remote_branch || '?', check.last_remote)}
           ${_gsiLastRow(t('git.guard.status.local'),  check.local_branch  || '?', check.last_local)}`;
      parts.push(`<div class="gsi-block gsi-block--summary">${summaryRows}</div>`);
    }

    // Delta: what remote has that local doesn't
    if (check.behind_commits && check.behind_commits.length) {
      parts.push(`<div class="gsi-block gsi-block--warn">
        <div class="gsi-block-head"><span class="gsi-block-label">${escHtml(t('git.guard.status.remoteNew', check.behind))}</span></div>
        <div class="gsi-block-body">${check.behind_commits.map(_gsiCommitRow).join('')}</div>
      </div>`);
    }

    // Delta: what local has that remote doesn't
    if (check.ahead_commits && check.ahead_commits.length) {
      parts.push(`<div class="gsi-block">
        <div class="gsi-block-head"><span class="gsi-block-label">${escHtml(t('git.guard.status.localNew', check.ahead))}</span></div>
        <div class="gsi-block-body">${check.ahead_commits.map(_gsiCommitRow).join('')}</div>
      </div>`);
    }

    // Dirty files
    if (check.dirty_files && check.dirty_files.length) {
      function _fileLabel(st) {
        if (st === 'D')                return ['gsi-del', t('git.guard.file.deleted')];
        if (st === 'A' || st === '??') return ['gsi-add', t('git.guard.file.new')];
        if (st.startsWith('R'))        return ['gsi-ren', t('git.guard.file.renamed')];
        return ['gsi-mod', t('git.guard.file.modified')];
      }
      const rows = check.dirty_files.map(f => {
        const [cls, label] = _fileLabel(f.status);
        return `<div class="gsi-file-row ${cls}">${escHtml(label)} ${escHtml(f.path)}</div>`;
      }).join('');
      parts.push(`<div class="gsi-block">
        <div class="gsi-block-head"><span class="gsi-block-label">${escHtml(t('git.guard.status.uncommitted'))}</span></div>
        <div class="gsi-block-body gsi-file-list">${rows}</div>
      </div>`);
    }

    if (parts.length) statusHtml = `<div class="gsi">${parts.join('')}</div>`;

    const overlay = document.createElement('div');
    overlay.className = 'overlay';
    overlay.innerHTML = `
      <div class="modal">
        <div class="modal-logo">${escHtml(msg.title)}</div>
        ${pathHtml}
        ${statusHtml}
        ${bodyHtml}
        ${recHtml}
        <p id="_gs-err" style="color:var(--red);display:none;margin-top:8px"></p>
        <div class="git-guard-cards">${cardsHtml}</div>
        <div class="git-guard-secondary">${secondaryHtml}</div>
      </div>`;
    document.body.appendChild(overlay);

    const errEl  = overlay.querySelector('#_gs-err');
    const finish = result => { overlay.remove(); resolve(result); };

    overlay.querySelector('#_gs-abort')?.addEventListener('click', () => finish('aborted'));
    overlay.querySelector('#_gs-open')?.addEventListener('click',  () => finish(true));

    overlay.querySelectorAll('[data-card]').forEach(btn => {
      btn.addEventListener('click', async () => {
        const cardId   = btn.dataset.card;
        const origText = btn.textContent;
        btn.disabled = true;
        btn.textContent = '…';
        errEl.style.display = 'none';
        try {
          const endpointMap = {
            fetch: '/api/git/reset-hard',   // reset to tracked remote branch then pull
            send:  '/api/git/commit-push-force',
          };
          const errKeyMap = {
            fetch: 'git.guard.s2.fetchError',
            send:  'git.guard.s2.sendError',
          };
          // For fetch: reset-hard brings us to the tracked remote branch, then pull gets latest
          if (cardId === 'fetch') {
            const r1 = await csrfFetch('/api/git/reset-hard', { method: 'POST' });
            const d1 = await r1.json();
            if (!d1.success) throw new Error(d1.message || '');
            await csrfFetch('/api/git/pull', { method: 'POST' }).catch(() => {});
            location.reload();
            return;
          }
          if (cardId === 'fetch-connect') {
            const fetchRes = await csrfFetch('/api/git/fetch-remote', { method: 'POST' });
            const fetchData = await fetchRes.json();
            if (!fetchData.success) throw new Error(fetchData.message || '');

            const branchRes = await csrfFetch('/api/git/remote-branches');
            const branchData = await branchRes.json();
            if (!branchData.success) throw new Error(branchData.message || '');
            const branches = Array.isArray(branchData.branches) ? branchData.branches : [];
            if (!branches.length) throw new Error(t('git.guard.noUpstream.noBranches'));

            let targetBranch = null;
            if (branches.length === 1) {
              targetBranch = branches[0];
            } else {
              targetBranch = await showRemoteBranchPicker(branches, check.local_branch || '');
            }
            if (!targetBranch) {
              btn.disabled = false;
              btn.textContent = origText;
              return;
            }

            const linkRes = await csrfFetch('/api/git/set-upstream', {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ branch: targetBranch }),
            });
            const linkData = await linkRes.json();
            if (!linkData.success) throw new Error(linkData.message || '');
            finish('retry-check');
            return;
          }
          const res  = await csrfFetch(endpointMap[cardId], { method: 'POST' });
          const data = await res.json();
          if (data.success) {
            location.reload();
          } else {
            errEl.textContent   = t(errKeyMap[cardId], data.message || '');
            errEl.style.display = '';
            btn.disabled        = false;
            btn.textContent     = origText;
          }
        } catch (e) {
          errEl.textContent   = String(e);
          errEl.style.display = '';
          btn.disabled        = false;
          btn.textContent     = origText;
        }
      });
    });
  });
}

async function ensureGitStartGuard(configDir) {
  if (_startGuardApproved === configDir) return true;

  while (true) {
    let check;
    try {
      check = await fetchGitStartCheck();
    } catch {
      check = {
        state: 'error',
        detail: 'start-check failed',
        ahead: 0,
        behind: 0,
        dirty: false,
      };
    }

    if (!_gitGuardNeedsDialog(check)) {
      _startGuardApproved = configDir;
      return true;
    }

    const result = await showGitStartGuard(check, configDir);
    if (result === 'retry-check') continue;
    if (result === true) {
      _startGuardApproved = configDir;
      return true;
    }
    if (result === 'aborted') {
      showAbortedState(configDir);
      return false;
    }
    // error state: user cancelled → back to setup
    showSetupOverlay();
    document.getElementById('nixos-dir-input').value = configDir || '';
    return false;
  }
}

function openAdmin() {
  _autoSave();
  Sidebar.flakeSave();
  document.getElementById('admin-overlay').classList.remove('hidden');
  _switchAdminTab(_activeAdminTab);
  _loadAdminSettings();
}

function _loadAdminSettings() {
  // Load app settings + theme picker
  Promise.all([
    fetch('/api/app/settings').then(r => r.json()),
    fetch('/api/themes').then(r => r.json()),
  ]).then(([data, themes]) => {
    const cb = document.getElementById('setting-code-view-plain');
    if (cb) cb.checked = !!data.code_view_plain;
    const cbLog = document.getElementById('setting-rebuild-log');
    if (cbLog) cbLog.checked = !!data.rebuild_log;
    const cbFlakeLock = document.getElementById('setting-show-flake-lock');
    if (cbFlakeLock) cbFlakeLock.checked = !!data.show_flake_lock;
    const cbRebuildTerminal = document.getElementById('setting-rebuild-terminal');
    if (cbRebuildTerminal) cbRebuildTerminal.checked = !!data.rebuild_terminal;

    // Standard-Host Dropdown (nur bei Flake-Modus)
    const defaultHostRow = document.getElementById('default-host-row');
    const defaultHostSel = document.getElementById('setting-default-host');
    if (defaultHostSel) {
      Promise.all([
        csrfFetch('/api/flake/hosts').then(r => r.json()),
        csrfFetch('/api/rebuild/default-host').then(r => r.json()),
      ]).then(([hostInfo, dhInfo]) => {
        if (!hostInfo.flake_mode || !hostInfo.hosts.length) return;
        defaultHostRow?.classList.remove('hidden');
        const saved = dhInfo.default_host || '';
        defaultHostSel.innerHTML = `<option value="">${t('admin.settings.defaultHostNone')}</option>` +
          hostInfo.hosts.map(h => {
            const n = typeof h === 'object' ? h.name : h;
            return `<option value="${escHtml(n)}"${n === saved ? ' selected' : ''}>${escHtml(n)}</option>`;
          }).join('');
        if (!defaultHostSel.dataset.listenerAttached) {
          defaultHostSel.dataset.listenerAttached = '1';
          defaultHostSel.addEventListener('change', () => {
            csrfFetch('/api/app/settings', {
              method:  'PATCH',
              headers: { 'Content-Type': 'application/json' },
              body:    JSON.stringify({ default_host: defaultHostSel.value }),
            }).then(() => showToast(t('admin.settings.saved'), 'success'))
              .catch(() => showToast(t('toast.error'), 'error'));
          });
        }
      }).catch(() => {});
    }
    if (Array.isArray(data.hidden_sections)) {
      hiddenSections = data.hidden_sections;
      updateSectionVisibility();
    }
    // Theme picker
    const sel = document.getElementById('setting-theme');
    if (sel && Array.isArray(themes)) {
      sel.innerHTML = '';
      themes.forEach(th => {
        const opt = document.createElement('option');
        opt.value = th.id;
        opt.textContent = th.name;
        if (th.id === (data.theme || 'catppuccin-mocha')) opt.selected = true;
        sel.appendChild(opt);
      });
      if (!sel.dataset.listenerAttached) {
        sel.dataset.listenerAttached = '1';
        sel.addEventListener('change', () => {
          csrfFetch('/api/app/settings', {
            method:  'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body:    JSON.stringify({ theme: sel.value }),
          }).then(() => {
            showToast(t('admin.settings.saved'), 'success');
            setTimeout(() => location.reload(), 600);
          }).catch(() => showToast(t('toast.error'), 'error'));
        });
      }
    }
  }).catch(() => {});

  // Auto-save Checkbox: code_view_plain
  const cbCodeView = document.getElementById('setting-code-view-plain');
  if (cbCodeView && !cbCodeView.dataset.listenerAttached) {
    cbCodeView.dataset.listenerAttached = '1';
    cbCodeView.addEventListener('change', () => {
      csrfFetch('/api/app/settings', {
        method:  'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body:    JSON.stringify({ code_view_plain: cbCodeView.checked }),
      }).then(() => showToast(t('admin.settings.saved'), 'success'))
        .catch(() => showToast(t('toast.error'), 'error'));
    });
  }

  // Auto-save Checkbox: rebuild_log
  const cbRebuildLog = document.getElementById('setting-rebuild-log');
  if (cbRebuildLog && !cbRebuildLog.dataset.listenerAttached) {
    cbRebuildLog.dataset.listenerAttached = '1';
    cbRebuildLog.addEventListener('change', () => {
      csrfFetch('/api/app/settings', {
        method:  'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body:    JSON.stringify({ rebuild_log: cbRebuildLog.checked }),
      }).then(() => showToast(t('admin.settings.saved'), 'success'))
        .catch(() => showToast(t('toast.error'), 'error'));
    });
  }

  // Auto-save Checkbox: rebuild_terminal
  const cbRebuildTerminal = document.getElementById('setting-rebuild-terminal');
  if (cbRebuildTerminal && !cbRebuildTerminal.dataset.listenerAttached) {
    cbRebuildTerminal.dataset.listenerAttached = '1';
    cbRebuildTerminal.addEventListener('change', () => {
      csrfFetch('/api/app/settings', {
        method:  'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body:    JSON.stringify({ rebuild_terminal: cbRebuildTerminal.checked }),
      }).then(() => showToast(t('admin.settings.saved'), 'success'))
        .catch(() => showToast(t('toast.error'), 'error'));
    });
  }

  // Auto-save Checkbox: show_flake_lock + Tree-Reload
  const cbShowFlakeLock = document.getElementById('setting-show-flake-lock');
  if (cbShowFlakeLock && !cbShowFlakeLock.dataset.listenerAttached) {
    cbShowFlakeLock.dataset.listenerAttached = '1';
    cbShowFlakeLock.addEventListener('change', () => {
      csrfFetch('/api/app/settings', {
        method:  'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body:    JSON.stringify({ show_flake_lock: cbShowFlakeLock.checked }),
      }).then(() => {
        showToast(t('admin.settings.saved'), 'success');
        if (!cbShowFlakeLock.checked) Sidebar.clearFlkIfActive();
        Sidebar.loadTree();
      }).catch(() => showToast(t('toast.error'), 'error'));
    });
  }

  // Load config settings from config.json (travels with the config)
  csrfFetch('/api/config/settings').then(r => r.json()).then(data => {
    // Flake-Update-Toggle (auto-save on change)
    const toggle = document.getElementById('flake-update-toggle');
    if (toggle) {
      toggle.checked = !!data.flake_update_on_rebuild;
      if (!toggle.dataset.listenerAttached) {
        toggle.dataset.listenerAttached = '1';
        toggle.addEventListener('change', () => {
          csrfFetch('/api/config/settings', {
            method:  'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body:    JSON.stringify({ flake_update_on_rebuild: toggle.checked }),
          }).then(() => showToast(t('admin.settings.saved'), 'success'))
            .catch(() => showToast(t('toast.error'), 'error'));
        });
      }
    }

    // Push-Toggles + NixOS-Push-Button – nur anzeigen wenn Remote vorhanden
    csrfFetch('/api/git/remote-status').then(r => r.json()).then(rs => {
      const setRemoteRow = document.getElementById('git-set-remote-row');
      if (setRemoteRow && rs.has_git) {
        setRemoteRow.classList.remove('hidden');

        function _buildRemoteUrl() {
          const platform = document.getElementById('git-remote-platform')?.value || 'github';
          if (platform === 'custom') {
            return document.getElementById('git-remote-url-input')?.value.trim() || '';
          }
          const user = document.getElementById('git-remote-user')?.value.trim() || '';
          const repo = document.getElementById('git-remote-repo')?.value.trim() || '';
          if (!user || !repo) return '';
          const hosts = { github: 'github.com', gitlab: 'gitlab.com', codeberg: 'codeberg.org' };
          return `git@${hosts[platform]}:${user}/${repo}.git`;
        }

        function _applyRemoteUrl(url) {
          const platformEl = document.getElementById('git-remote-platform');
          const userEl = document.getElementById('git-remote-user');
          const repoEl = document.getElementById('git-remote-repo');
          const customEl = document.getElementById('git-remote-url-input');
          if (!platformEl || !userEl || !repoEl || !customEl) return;

          const sshMatch = url.match(/^git@(github\.com|gitlab\.com|codeberg\.org):([^/]+)\/(.+?)(?:\.git)?$/);
          const httpsMatch = url.match(/^https?:\/\/(github\.com|gitlab\.com|codeberg\.org)\/([^/]+)\/(.+?)(?:\.git)?$/);
          const match = sshMatch || httpsMatch;
          const platformByHost = {
            'github.com': 'github',
            'gitlab.com': 'gitlab',
            'codeberg.org': 'codeberg',
          };
          if (!match) {
            platformEl.value = 'custom';
            customEl.value = url;
            userEl.value = '';
            repoEl.value = '';
            return;
          }
          platformEl.value = platformByHost[match[1]] || 'custom';
          userEl.value = match[2];
          repoEl.value = match[3];
          customEl.value = platformEl.value === 'custom' ? url : '';
        }

        function _updateRemotePreview() {
          const url = _buildRemoteUrl();
          const el = document.getElementById('git-remote-preview');
          if (el) el.textContent = url;
        }

        function _onPlatformChange() {
          const platform = document.getElementById('git-remote-platform')?.value;
          const fields = document.getElementById('git-remote-fields');
          const customField = document.getElementById('git-remote-custom-field');
          if (fields) fields.style.display = platform === 'custom' ? 'none' : 'flex';
          if (customField) customField.style.display = platform === 'custom' ? 'block' : 'none';
          _updateRemotePreview();
        }

        document.getElementById('git-remote-platform')?.addEventListener('change', _onPlatformChange);
        document.getElementById('git-remote-user')?.addEventListener('input', _updateRemotePreview);
        document.getElementById('git-remote-repo')?.addEventListener('input', _updateRemotePreview);
        document.getElementById('git-remote-url-input')?.addEventListener('input', _updateRemotePreview);

        const checkBtn  = document.getElementById('git-remote-check-btn');
        const statusEl  = document.getElementById('git-remote-status');
        if (rs.remote_url) _applyRemoteUrl(rs.remote_url);
        _onPlatformChange();
        if (checkBtn && !checkBtn.dataset.listenerAttached) {
          checkBtn.dataset.listenerAttached = '1';
          checkBtn.addEventListener('click', async () => {
            const url = _buildRemoteUrl();
            if (!url) return;
            checkBtn.disabled = true;
            statusEl.textContent = t('git.setRemoteSaving');
            statusEl.style.color = 'var(--fg-muted)';
            const setRes = await csrfFetch('/api/git/set-remote', {
              method: 'POST', headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ url }),
            }).catch(() => null);
            checkBtn.disabled = false;
            if (!setRes || !setRes.ok) {
              statusEl.textContent = t('git.setRemoteError');
              statusEl.style.color = 'var(--red)';
              showToast(t('git.setRemoteError'), 'error');
              return;
            }
            statusEl.textContent = t('git.setRemoteSavedRestart');
            statusEl.style.color = 'var(--green)';
            showToast(t('git.setRemoteSavedRestart'), 'success');
            document.getElementById('nixos-git-push-btn')?.classList.remove('hidden');
            document.getElementById('git-push-settings-row')?.classList.remove('hidden');
          });
        }
      }
      if (!rs.has_git || !rs.has_remote) return;
      // NixOS-Menü Push-Button einblenden
      document.getElementById('nixos-git-push-btn')?.classList.remove('hidden');
      // Push-Settings-Sektion einblenden
      const pushRow = document.getElementById('git-push-settings-row');
      if (pushRow) pushRow.classList.remove('hidden');
      const pas = document.getElementById('push-after-save-toggle');
      const par = document.getElementById('push-after-rebuild-toggle');
      if (pas) pas.checked = !!data.push_after_save;
      if (par) par.checked = !!data.push_after_rebuild;
      [['push-after-save-toggle', 'push_after_save'], ['push-after-rebuild-toggle', 'push_after_rebuild']].forEach(([id, key]) => {
        const el = document.getElementById(id);
        if (el && !el.dataset.listenerAttached) {
          el.dataset.listenerAttached = '1';
          el.addEventListener('change', async () => {
            if (el.checked) {
              // Schreibzugriff prüfen bevor Auto-Push aktiviert wird
              const wr = await csrfFetch('/api/git/check-write', { method: 'POST' })
                .then(r => r.json()).catch(() => null);
              if (!wr || !wr.ok) {
                el.checked = false;
                const code = wr?.error_code || 'UNKNOWN';
                _showGitPushErrorModal(wr?.raw || '', code);
                return;
              }
            }
            csrfFetch('/api/config/settings', {
              method: 'PATCH', headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ [key]: el.checked }),
            }).then(() => showToast(t('admin.settings.saved'), 'success'))
              .catch(() => showToast(t('toast.error'), 'error'));
          });
        }
      });
    }).catch(() => {});

    // Hosts-Verzeichnis
    const hostsDirInput = document.getElementById('settings-hosts-dir');
    if (hostsDirInput) hostsDirInput.value = data.hosts_dir || 'hosts';

    // Modules-Verzeichnis
    const modulesDirInput = document.getElementById('settings-modules-dir');
    if (modulesDirInput) modulesDirInput.value = data.modules_dir || 'modules';

    // HM-Verzeichnis
    const hmDirInput = document.getElementById('settings-hm-dir');
    if (hmDirInput) hmDirInput.value = data.hm_dir || 'home';
  }).catch(() => {});

  // Config-Einstellungen speichern
  const saveBtn = document.getElementById('settings-config-save');
  if (saveBtn && !saveBtn.dataset.listenerAttached) {
    saveBtn.dataset.listenerAttached = '1';
    saveBtn.addEventListener('click', () => {
      const hostsDir   = document.getElementById('settings-hosts-dir')?.value.trim() || 'hosts';
      const modulesDir = document.getElementById('settings-modules-dir')?.value.trim() || 'modules';
      const hmDir      = document.getElementById('settings-hm-dir')?.value.trim() || 'home';
      csrfFetch('/api/config/settings', {
        method:  'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body:    JSON.stringify({
          hosts_dir: hostsDir,
          modules_dir: modulesDir,
          hm_dir: hmDir,
        }),
      }).then(() => showToast(t('admin.settings.saved'), 'success'))
        .catch(() => showToast(t('toast.error'), 'error'));
    });
  }
}

function _expectedCoPath() {
  return _activeHost ? `hosts/${_activeHost}/default.nix` : 'configuration.nix';
}

function _canSaveCurrentCoForm() {
  return _coFormReady && _coLoadedPath === _expectedCoPath();
}

function closeAdmin() {
  document.getElementById('admin-overlay').classList.add('hidden');
}

function _switchAdminTab(tab) {
  _activeAdminTab = tab;
  document.querySelectorAll('.admin-tab').forEach(btn => {
    btn.classList.toggle('active', btn.dataset.adminTab === tab);
  });
  document.querySelectorAll('.admin-tab-panel').forEach(panel => {
    panel.classList.toggle('hidden', panel.dataset.adminPanel !== tab);
  });
  // Lazy-load per-tab data
  if (tab === 'zeitmaschine') { checkGitStatus(); loadAdminGitLog(); }
  if (tab === 'administration') { loadSettingsPath(); loadAdminSymlinkStatus(); }
}

function initAdminTabs() {
  document.querySelectorAll('.admin-tab').forEach(btn => {
    btn.addEventListener('click', () => _switchAdminTab(btn.dataset.adminTab));
  });
}

// ── Admin: Settings – Config path ─────────────────────────────────────────────
async function loadSettingsPath() {
  const input = document.getElementById('settings-current-path');
  if (!input) return;
  try {
    const res  = await csrfFetch('/api/settings/dir');
    const data = await res.json();
    input.value = data.dir || '';
  } catch (e) {
    console.error('loadSettingsPath:', e);
  }
}

async function loadAdminSymlinkStatus() {
  const label   = document.getElementById('admin-symlink-status-label');
  const hint    = document.getElementById('admin-symlink-hint');
  const btn     = document.getElementById('admin-symlink-btn');
  const errDiv  = document.getElementById('admin-symlink-error');
  if (!label) return;

  const baseHint = '/etc/nixos wird vorher nach /etc/nixos.bak gesichert.';
  errDiv.classList.add('hidden');
  btn.classList.add('hidden');
  label.textContent = '…';

  try {
    const res  = await csrfFetch('/api/symlink/status');
    const data = await res.json();
    if (data.status === 'symlink' && data.points_to_nico) {
      label.innerHTML = niIcon('check-circle') + ' Symlink aktiv → ' + escHtml(data.target);
      hint.textContent  = baseHint;
    } else if (data.status === 'symlink') {
      label.innerHTML = niIcon('alert-triangle') + ' Symlink zeigt auf ' + escHtml(data.target) + ' (nicht NiCo-Verzeichnis)';
      hint.textContent  = baseHint;
    } else if (data.status === 'dir') {
      label.textContent = '';
      hint.textContent  = baseHint + ' (aktuell noch kein Symlink angelegt)';
      btn.classList.remove('hidden');
    } else if (data.status === 'missing') {
      label.textContent = '/etc/nixos existiert nicht';
      hint.textContent  = baseHint;
    } else {
      label.textContent = '';
      hint.textContent  = baseHint;
    }
  } catch (e) {
    label.textContent = '';
    hint.textContent  = baseHint;
    console.error('loadAdminSymlinkStatus:', e);
  }
}

async function doAdminSymlink() {
  const errDiv = document.getElementById('admin-symlink-error');
  const btn    = document.getElementById('admin-symlink-btn');
  errDiv.classList.add('hidden');
  btn.disabled = true;

  const nonce = await acquireSudoNonce();
  if (nonce === null) { btn.disabled = false; return; }

  try {
    const res  = await csrfFetch('/api/symlink/create', {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify({ copy_files: false, sudo_nonce: nonce }),
    });
    const data = await res.json();
    if (!res.ok || data.error) {
      errDiv.textContent = tErr(data.error) || t('toast.error');
      errDiv.classList.remove('hidden');
      btn.disabled = false;
      return;
    }
    await loadAdminSymlinkStatus();
    showToast('Symlink angelegt ✅', 'success');
  } catch (e) {
    errDiv.textContent = t('toast.error');
    errDiv.classList.remove('hidden');
    btn.disabled = false;
  }
}

function initSettingsPanel() {
  const changeBtn  = document.getElementById('settings-change-btn');
  const cancelBtn  = document.getElementById('settings-cancel-btn');
  const applyBtn   = document.getElementById('settings-apply-btn');
  const changeRow  = document.getElementById('settings-change-row');
  const newInput   = document.getElementById('settings-new-path');
  const errorDiv   = document.getElementById('settings-error');

  if (!changeBtn) return;

  changeBtn.addEventListener('click', () => {
    const current = document.getElementById('settings-current-path')?.value || '';
    newInput.value = current;
    changeRow.classList.remove('hidden');
    errorDiv.classList.add('hidden');
    newInput.focus();
  });

  cancelBtn.addEventListener('click', () => {
    changeRow.classList.add('hidden');
    errorDiv.classList.add('hidden');
  });

  applyBtn.addEventListener('click', async () => {
    const newPath = newInput.value.trim();
    errorDiv.classList.add('hidden');
    if (!newPath) {
      errorDiv.textContent = t('setup.errNoDir');
      errorDiv.classList.remove('hidden');
      return;
    }
    try {
      const res  = await csrfFetch('/api/setup', {
        method:  'POST',
        headers: { 'Content-Type': 'application/json' },
        body:    JSON.stringify({ nixos_config_dir: newPath }),
      });
      const data = await res.json();
      if (data.error) {
        errorDiv.textContent = tErr(data.error);
        errorDiv.classList.remove('hidden');
        return;
      }
      if (data.needs_confirmation) {
        if (!confirm(`${t('setup.confirmPrefix')} ${data.path} ${t('setup.confirmSuffix')}`)) return;
        const res2  = await csrfFetch('/api/setup', {
          method:  'POST',
          headers: { 'Content-Type': 'application/json' },
          body:    JSON.stringify({ nixos_config_dir: newPath, create_if_missing: true }),
        });
        const data2 = await res2.json();
        if (data2.error) {
          errorDiv.textContent = tErr(data2.error);
          errorDiv.classList.remove('hidden');
          return;
        }
      }
      showToast(t('toast.saved'), 'success');
      setTimeout(() => location.reload(), 800);
    } catch (e) {
      errorDiv.textContent = t('toast.error');
      errorDiv.classList.remove('hidden');
    }
  });
}


// ── Validierung ────────────────────────────────────────────────────────────────

let _validationRules = [];     // Rule metadata from /api/validate/rules
let _isFlakeConfig   = false;  // Set on config load

/** Load rule metadata once (cached in _validationRules). */
async function _ensureValidationRules() {
  if (_validationRules.length) return;
  try {
    const res = await fetch('/api/validate/rules');
    if (res.ok) _validationRules = await res.json();
  } catch (e) { /* ignore */ }
}

/** Open the validation-settings overlay and render rule toggles. */
async function openValidationSettings() {
  await _ensureValidationRules();

  // Load current settings
  let enabled = {};
  try {
    const res = await fetch('/api/config/settings');
    if (res.ok) {
      const s = await res.json();
      enabled = s.validation_rules || {};
    }
  } catch (e) { /* ignore */ }

  const list = document.getElementById('validation-rules-list');
  if (!list) return;
  list.innerHTML = '';

  for (const rule of _validationRules) {
    // Hide flake-only rules when the config is not a flake
    const hidden = rule.flake_only && !_isFlakeConfig;
    const checked = enabled[rule.id] !== false;  // default: on

    const severityColors = { error: 'var(--red)', warning: 'var(--yellow)', info: 'var(--blue)' };
    const dot = `<span style="display:inline-block;width:8px;height:8px;border-radius:50%;
                 background:${severityColors[rule.severity] || 'var(--subtext0)'};
                 margin-right:6px;flex-shrink:0;margin-top:6px"></span>`;

    const row = document.createElement('label');
    row.className = 'toggle-row';
    row.style.cssText = 'margin-bottom:10px;align-items:flex-start;' + (hidden ? 'display:none' : '');
    row.dataset.ruleId = rule.id;
    row.innerHTML = `
      <span style="display:flex;flex-direction:column;gap:2px;flex:1;min-width:0">
        <span style="display:flex;align-items:center">${dot}<strong>${_esc(rule.label)}</strong>
          ${rule.flake_only ? '<span class="badge" style="margin-left:6px;font-size:10px;padding:1px 5px;background:var(--surface1);border-radius:4px">Flake</span>' : ''}
        </span>
        <span class="raw-panel-hint" style="margin:0 0 0 14px">${_esc(rule.description)}</span>
      </span>
      <span class="toggle-wrap" style="margin-left:12px;flex-shrink:0">
        <input type="checkbox" data-rule="${rule.id}" ${checked ? 'checked' : ''}>
        <span class="toggle-track"><span class="toggle-thumb"></span></span>
      </span>`;
    list.appendChild(row);
  }

  document.getElementById('validation-settings-overlay').classList.remove('hidden');
}

function closeValidationSettings() {
  document.getElementById('validation-settings-overlay').classList.add('hidden');
}

/** Save the current toggle state to config.json. */
async function saveValidationSettings() {
  const checks = document.querySelectorAll('#validation-rules-list input[data-rule]');
  const rules = {};
  checks.forEach(cb => { rules[cb.dataset.rule] = cb.checked; });
  try {
    await csrfFetch('/api/config/settings', {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ validation_rules: rules }),
    });
    showToast(t('admin.validation.saved'), 'success');
  } catch (e) {
    showToast(String(e), 'error');
  }
}

// ── Sections settings overlay ──────────────────────────────────────────────

// All hideable configuration sections (System is always visible)
const _ALL_SECTIONS = [
  'Boot', 'Lokalisierung', 'Netzwerk', 'Services', 'Desktop', 'Audio',
  'Benutzer', 'Programme', 'Schriftarten', 'Nix & System', 'Home Manager',
  'Hardware', 'Virtualisierung', 'Dateisystem & Backup',
];

function openSectionsSettings() {
  const list = document.getElementById('sections-settings-list');
  if (!list) return;
  list.innerHTML = '';

  for (const name of _ALL_SECTIONS) {
    const hidden = hiddenSections.includes(name);
    const row = document.createElement('label');
    row.className = 'toggle-row';
    row.style.cssText = 'margin-bottom:8px';
    row.innerHTML = `
      <span style="flex:1">${_esc(name)}</span>
      <span class="toggle-wrap" style="margin-left:12px;flex-shrink:0">
        <input type="checkbox" data-section-name="${_esc(name)}" ${hidden ? '' : 'checked'}>
        <span class="toggle-track"><span class="toggle-thumb"></span></span>
      </span>`;
    list.appendChild(row);
  }

  document.getElementById('sections-settings-overlay').classList.remove('hidden');
}

function closeSectionsSettings() {
  document.getElementById('sections-settings-overlay').classList.add('hidden');
}

async function saveSectionsSettings() {
  const checks = document.querySelectorAll('#sections-settings-list input[data-section-name]');
  const newHidden = [];
  checks.forEach(cb => { if (!cb.checked) newHidden.push(cb.dataset.sectionName); });
  try {
    await csrfFetch('/api/app/settings', {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ hidden_sections: newHidden }),
    });
    hiddenSections = newHidden;
    updateSectionVisibility();
    showToast(t('admin.sections.saved'), 'success');
    closeSectionsSettings();
  } catch (e) {
    showToast(String(e), 'error');
  }
}

/** POST /api/validate and show the results overlay. */
async function runValidation() {
  // For flake configs with multiple hosts, ask which host to validate
  let host = null;
  if (_isFlakeConfig) {
    const hostInfo = await _fetchFlakeHosts();
    if (hostInfo.hosts && hostInfo.hosts.length > 1) {
      host = await _showHostPicker(hostInfo.hosts, { allowAll: false });
      if (host === null) return; // cancelled
    } else if (hostInfo.hosts && hostInfo.hosts.length === 1) {
      host = typeof hostInfo.hosts[0] === 'object' ? hostInfo.hosts[0].name : hostInfo.hosts[0];
    }
  }

  let findings;
  try {
    const res  = await csrfFetch('/api/validate', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ host }),
    });
    const data = await res.json();
    findings   = data.findings || [];
  } catch (e) {
    showToast(String(e), 'error');
    return;
  }

  _showValidationResults(findings);
}

function _showValidationResults(findings) {
  const body = document.getElementById('validation-results-body');
  if (!body) return;

  if (!findings.length) {
    body.innerHTML = `<p style="color:var(--green);font-weight:600"
      data-i18n="admin.validation.noFindings">${t('admin.validation.noFindings')}</p>`;
  } else {
    const iconNames = { error: 'x-circle', warning: 'alert-triangle', info: 'info' };
    const colors = { error: 'var(--red)', warning: 'var(--yellow)', info: 'var(--blue)' };
    body.innerHTML = findings.map(f => {
      const icon  = niIcon(iconNames[f.severity] || 'info');
      const color = colors[f.severity] || 'var(--text)';
      const detail = f.detail
        ? `<div style="margin-top:4px;color:var(--subtext0);font-size:12px;white-space:pre-line">${_esc(f.detail)}</div>`
        : '';
      return `<div style="display:flex;gap:10px;margin-bottom:14px;align-items:flex-start">
        <span style="color:${color};font-size:16px;flex-shrink:0;margin-top:1px">${icon}</span>
        <div>
          <div style="font-size:13px">${_esc(f.message)}</div>${detail}
        </div>
      </div>`;
    }).join('');
  }

  document.getElementById('validation-results-overlay').classList.remove('hidden');
}

function closeValidationResults() {
  document.getElementById('validation-results-overlay').classList.add('hidden');
}

function _esc(s) {
  return String(s ?? '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

// ── Admin: Import section collapse ─────────────────────────────────────────────
function initAdminImportCollapse() {
  const toggle  = document.getElementById('admin-import-toggle');
  const body    = document.getElementById('admin-import-body');
  const section = document.getElementById('admin-import-section');
  if (!toggle || !body) return;

  const LS_KEY = 'nico_admin_import_collapsed';

  function applyState(collapsed) {
    if (collapsed) {
      body.classList.add('hidden');
      section.classList.add('import-collapsed');
    } else {
      body.classList.remove('hidden');
      section.classList.remove('import-collapsed');
    }
  }

  // Default: open (collapsed only when user explicitly collapsed it)
  const stored = localStorage.getItem(LS_KEY);
  applyState(stored === 'collapsed');

  toggle.addEventListener('click', () => {
    const isCollapsed = body.classList.contains('hidden');
    applyState(!isCollapsed);
    localStorage.setItem(LS_KEY, isCollapsed ? 'open' : 'collapsed');
  });
}



async function loadAdminGitLog() {
  const container = document.getElementById('admin-git-log');
  container.innerHTML = `<span class="admin-loading">${escHtml(t('admin.loading'))}</span>`;

  try {
    const res  = await csrfFetch('/api/git/log');
    const data = await res.json();
    const commits = data.commits || [];

    if (!commits.length) {
      container.innerHTML = `<span class="admin-empty">${escHtml(t('admin.noCommits'))}</span>`;
      return;
    }

    container.innerHTML = '';
    for (const c of commits) {
      const shortHash = c.hash.slice(0, 7);
      // Format date: "2026-04-03 14:23:11 +0200" → "2026-04-03 14:23"
      const dateParts = (c.date || '').split(' ');
      const dateStr   = dateParts.length >= 2
        ? `${dateParts[0]} ${dateParts[1].slice(0, 5)}`
        : c.date;

      const row = document.createElement('div');
      row.className = 'rollback-item';
      row.innerHTML = `
        <span class="rollback-hash">${escHtml(shortHash)}</span>
        <span class="rollback-msg" title="${escHtml(c.message)}">${escHtml(c.message)}</span>
        <span class="rollback-date">${escHtml(dateStr)}</span>
        <button class="rollback-btn"
                title="${escHtml(t('admin.rollbackBtnTitle'))}">
          ${escHtml(t('admin.rollbackBtn'))}
        </button>`;
      row.querySelector('.rollback-btn').addEventListener('click', () => {
        rollbackTo(c.hash, dateStr, c.message);
      });
      container.appendChild(row);
    }
  } catch (e) {
    container.innerHTML = `<span class="admin-empty">${escHtml(t('admin.gitError'))}</span>`;
    console.error('loadAdminGitLog:', e);
  }
}

async function rollbackTo(hash, dateStr, message) {
  if (!confirm(
    t('git.rollbackConfirm') + '\n\n' +
    `${hash.slice(0, 7)}  ${dateStr}\n${message}\n\n` +
    t('git.rollbackDetail')
  )) return;

  const res  = await csrfFetch('/api/git/rollback', {
    method:  'POST',
    headers: { 'Content-Type': 'application/json' },
    body:    JSON.stringify({ hash }),
  });
  const data = await res.json();

  if (data.success) {
    showToast(data.message || t('git.rollbackSuccess'), 'success');
    closeAdmin();
    await loadConfig();   // refresh form + preview to reflect restored files
  } else {
    showToast(data.message || t('git.rollbackError'), 'error');
  }
}

// ── Benutzer (einheitliche Karten) ────────────────────────────────────────

const DEFAULT_EXTRA_USER_GROUPS = ['wheel', 'networkmanager'];

function _userCard(idx, user, isOnly) {
  const uname  = escHtml(user.username      || '');
  const desc   = escHtml(user.description   || '');
  const pass   = escHtml(user.initial_password || '');
  const uid    = escHtml(String(user.uid    || ''));
  const groupsArr = Array.isArray(user.groups) ? user.groups : DEFAULT_EXTRA_USER_GROUPS;
  const groups = escHtml(groupsArr.join(' '));
  const shell  = user.shell || 'bash';
  const extra  = escHtml(user.extra_nix || '');
  const hasPw  = !!user.initial_password;
  const delTitle = isOnly ? escHtml(t('field.lastUser')) : escHtml(t('field.removeUser'));

  return `<div class="extra-user-card" data-eu-idx="${idx}">
    <div class="extra-user-header eu-toggle" data-eu-idx="${idx}">
      <span class="extra-user-label">${uname || t('field.newUser')}</span>
      <span class="eu-header-actions">
        ${niIcon('chevron-down').replace('class="', 'class="eu-chevron ')}
        <button type="button" class="eu-remove-btn" data-eu-idx="${idx}"
                ${isOnly ? 'disabled' : ''} title="${delTitle}">${niIcon('x')}</button>
      </span>
    </div>
    <div class="extra-user-body">
      <label>${escHtml(t('field.username'))}</label>
      <input type="text" class="eu-username" data-eu-idx="${idx}"
             value="${uname}" placeholder="benutzer"
             spellcheck="false" autocomplete="off">

      <label>${escHtml(t('field.userDesc'))}
        <span class="hint">${escHtml(t('field.userDescHint'))}</span></label>
      <input type="text" class="eu-description" data-eu-idx="${idx}"
             value="${desc}" placeholder="Max Mustermann">

      <label class="toggle-row" style="margin-top:4px">
        <span>${escHtml(t('field.userPass'))}
          <span class="hint">${escHtml(t('field.userPassHint'))}</span>
        </span>
        <span class="toggle-wrap">
          <input type="checkbox" class="eu-has-password" data-eu-idx="${idx}"${hasPw ? ' checked' : ''}>
          <span class="toggle-track"><span class="toggle-thumb"></span></span>
        </span>
      </label>
      <input type="password" class="eu-password${hasPw ? '' : ' hidden'}" data-eu-idx="${idx}"
             value="${pass}" autocomplete="new-password" style="margin-top:6px"
             placeholder="${escHtml(t('field.userPassPlaceholder') || '')}">

      <label>${escHtml(t('field.uid'))}
        <span class="hint">${escHtml(t('field.uidHint'))}</span></label>
      <input type="text" class="eu-uid" data-eu-idx="${idx}" value="${uid}" placeholder="1000">

      <label>${escHtml(t('field.groups'))}
        <span class="hint">${escHtml(t('field.groupsExtraHint'))}</span></label>
      <input type="text" class="eu-groups" data-eu-idx="${idx}" value="${groups}"
             placeholder="wheel networkmanager">

      <label>${escHtml(t('field.shell'))}</label>
      <select class="eu-shell" data-eu-idx="${idx}">
        ${['bash','zsh','fish','nushell'].map(s =>
          `<option value="${s}"${shell===s?' selected':''}>${s}</option>`).join('')}
      </select>

      <label>${escHtml(t('field.userExtraNix'))}</label>
      <textarea class="eu-extra-nix mono-input" data-eu-idx="${idx}" rows="3"
                placeholder='openssh.authorizedKeys.keys = [ "ssh-ed25519 …" ];'
                spellcheck="false">${extra}</textarea>
    </div>
  </div>`;
}

function renderAllUsers(users) {
  const list = document.getElementById('users-list');
  if (!list) return;
  const isOnly = users.length <= 1;
  list.innerHTML = users.map((u, i) => _userCard(i, u, isOnly)).join('');
  updateSectionVisibility();

  // First card starts expanded
  const firstCard = list.querySelector('.extra-user-card');
  if (firstCard) {
    firstCard.querySelector('.extra-user-body')?.classList.add('open');
    firstCard.querySelector('.eu-toggle')?.classList.add('open');
  }

  // Collapse/expand toggle
  list.querySelectorAll('.eu-toggle').forEach(header => {
    header.addEventListener('click', e => {
      if (e.target.closest('.eu-remove-btn')) return;
      const card = header.closest('.extra-user-card');
      const body = card?.querySelector('.extra-user-body');
      if (!body) return;
      const open = body.classList.toggle('open');
      header.classList.toggle('open', open);
    });
  });

  // Live-update of header label as user types
  list.querySelectorAll('.eu-username').forEach(inp => {
    inp.addEventListener('input', () => {
      const card = inp.closest('.extra-user-card');
      const label = card?.querySelector('.extra-user-label');
      if (label) label.textContent = inp.value || t('field.newUser');
      schedulePreviewUpdate();
    });
  });

  // Password toggle per Karte
  list.querySelectorAll('.eu-has-password').forEach(cb => {
    const idx   = cb.dataset.euIdx;
    const pwInp = list.querySelector(`.eu-password[data-eu-idx="${idx}"]`);
    cb.addEventListener('change', () => {
      pwInp?.classList.toggle('hidden', !cb.checked);
      schedulePreviewUpdate();
    });
  });

  // Remove buttons
  list.querySelectorAll('.eu-remove-btn:not([disabled])').forEach(btn => {
    btn.addEventListener('click', () => {
      const idx = parseInt(btn.dataset.euIdx, 10);
      const current = getAllUsers();
      current.splice(idx, 1);
      renderAllUsers(current);
      schedulePreviewUpdate();
    });
  });
  list.querySelectorAll('input, select, textarea').forEach(el => {
    if (!el.classList.contains('eu-username')) {
      el.addEventListener('input',  schedulePreviewUpdate);
      el.addEventListener('change', schedulePreviewUpdate);
    }
  });
}

function getAllUsers() {
  const list = document.getElementById('users-list');
  if (!list) return [];
  return [...list.querySelectorAll('.extra-user-card')].map(card => {
    const idx = card.dataset.euIdx;
    const hasPw = card.querySelector(`.eu-has-password[data-eu-idx="${idx}"]`)?.checked;
    const groupsRaw = card.querySelector(`.eu-groups[data-eu-idx="${idx}"]`)?.value?.trim() || '';
    return {
      username:         card.querySelector(`.eu-username[data-eu-idx="${idx}"]`)?.value?.trim()  || '',
      description:      card.querySelector(`.eu-description[data-eu-idx="${idx}"]`)?.value?.trim() || '',
      initial_password: hasPw ? (card.querySelector(`.eu-password[data-eu-idx="${idx}"]`)?.value || '') : '',
      uid:              card.querySelector(`.eu-uid[data-eu-idx="${idx}"]`)?.value?.trim()        || '',
      groups:           groupsRaw ? groupsRaw.split(/\s+/) : DEFAULT_EXTRA_USER_GROUPS,
      shell:            card.querySelector(`.eu-shell[data-eu-idx="${idx}"]`)?.value              || 'bash',
      extra_nix:        card.querySelector(`.eu-extra-nix[data-eu-idx="${idx}"]`)?.value?.trim()  || '',
    };
  });
}

// ── Benutzer-Detail aufklappen ─────────────────────────────────────────────
function initUserDetail() {
  const btn  = document.getElementById('user-detail-toggle');
  const card = document.getElementById('user-detail-card');
  if (!btn || !card) return;

  // Restore state from localStorage
  const stored = localStorage.getItem('nico_user_detail');
  if (stored === 'expanded') {
    card.classList.add('expanded');
    btn.classList.add('expanded');
  }

  btn.addEventListener('click', () => {
    const isExpanded = card.classList.toggle('expanded');
    btn.classList.toggle('expanded', isExpanded);
    localStorage.setItem('nico_user_detail', isExpanded ? 'expanded' : 'collapsed');
  });
}

// ── Git integration ────────────────────────────────────────────────────────
// Git-Status wird gecacht damit openWriteConfirm() darauf zugreifen kann
let _gitStatus = { git_installed: true, has_git: true };

async function checkGitStatus() {
  try {
    const res  = await csrfFetch('/api/git/status');
    const data = await res.json();
    _gitStatus = data;
    _updateGitStatusLabel(data);
    if (!data.git_installed) {
      showGitWarning(t('git.notInstalled'), false);
      return;
    }
    if (!data.has_git) {
      showGitWarning(t('git.noRepo'), true);
    } else {
      hideGitWarning();
    }
  } catch (e) {
    console.error('checkGitStatus:', e);
  }
}

async function checkGitRemoteStatus() {
  try {
    const res  = await csrfFetch('/api/git/remote-status');
    const data = await res.json();
    if (data.behind > 0) {
      showGitRemoteBanner(data.behind);
    }
  } catch (e) {
    console.error('checkGitRemoteStatus:', e);
  }
}

function showGitRemoteBanner(behind) {
  let el = document.getElementById('git-remote-banner');
  if (!el) {
    el = document.createElement('div');
    el.id        = 'git-remote-banner';
    el.className = 'git-remote-banner';
    const header = document.querySelector('#app header');
    header?.insertAdjacentElement('afterend', el);
  }
  el.innerHTML = '';
  const text = document.createElement('span');
  text.textContent = t('git.remoteBehind', behind);
  el.appendChild(text);
  const dismiss = document.createElement('button');
  dismiss.innerHTML   = niIcon('x');
  dismiss.className   = 'git-warning-dismiss';
  dismiss.onclick     = () => el.classList.add('hidden');
  el.appendChild(dismiss);
  el.classList.remove('hidden');
}

async function _doGitInit() {
  const r = await csrfFetch('/api/git/init', { method: 'POST' });
  const d = await r.json();
  if (d.success) {
    _gitStatus.has_git = true;
    hideGitWarning();
    _updateGitStatusLabel({ git_installed: true, has_git: true });
    showToast(t('git.initSuccess'), 'success');
  } else {
    showToast(d.message || t('git.initError'), 'error');
  }
}

function _updateGitStatusLabel(data) {
  const label   = document.getElementById('git-status-label');
  const initBtn = document.getElementById('git-init-btn');
  if (!label) return;
  if (!data.git_installed) {
    label.textContent = t('git.notInstalled');
    initBtn?.classList.add('hidden');
  } else if (!data.has_git) {
    label.textContent = t('git.noRepo');
    if (initBtn) {
      initBtn.classList.remove('hidden');
      initBtn.onclick = _doGitInit;
    }
  } else {
    label.textContent = t('git.active');
    initBtn?.classList.add('hidden');
  }
}

function showGitWarning(msg, showInitBtn) {
  let el = document.getElementById('git-warning-banner');
  if (!el) {
    el = document.createElement('div');
    el.id = 'git-warning-banner';
    el.className = 'git-warning-banner';
    // Insert after header, before main-layout
    const header = document.querySelector('#app header');
    header?.insertAdjacentElement('afterend', el);
  }
  el.innerHTML = '';
  const text = document.createElement('span');
  text.textContent = msg;
  el.appendChild(text);
  if (showInitBtn) {
    const btn = document.createElement('button');
    btn.textContent = t('git.initBtn');
    btn.className = 'btn-surface btn-small';
    btn.style.marginLeft = '10px';
    btn.onclick = _doGitInit;
    el.appendChild(btn);
  }
  const dismiss = document.createElement('button');
  dismiss.innerHTML = niIcon('x');
  dismiss.className = 'git-warning-dismiss';
  dismiss.onclick = hideGitWarning;
  el.appendChild(dismiss);
  el.classList.remove('hidden');
}

function hideGitWarning() {
  document.getElementById('git-warning-banner')?.classList.add('hidden');
}

// ── Sudo-Passwort-Modal ────────────────────────────────────────────────────

/**
 * Zeigt das Sudo-Passwort-Modal und gibt das Passwort zurück (oder null bei Abbruch).
 */
function promptSudoPassword() {
  return new Promise((resolve) => {
    const overlay  = document.getElementById('sudo-overlay');
    const input    = document.getElementById('sudo-password-input');
    const okBtn    = document.getElementById('sudo-ok-btn');
    const cancelBtn = document.getElementById('sudo-cancel-btn');
    const errEl    = document.getElementById('sudo-error');

    input.value = '';
    errEl.classList.add('hidden');
    overlay.classList.remove('hidden');
    setTimeout(() => input.focus(), 50);

    function cleanup() {
      overlay.classList.add('hidden');
      okBtn.removeEventListener('click', onOk);
      cancelBtn.removeEventListener('click', onCancel);
      input.removeEventListener('keydown', onKey);
    }
    function onOk() { cleanup(); resolve(input.value); }
    function onCancel() { cleanup(); resolve(null); }
    function onKey(e) { if (e.key === 'Enter') onOk(); if (e.key === 'Escape') onCancel(); }

    okBtn.addEventListener('click', onOk);
    cancelBtn.addEventListener('click', onCancel);
    input.addEventListener('keydown', onKey);
  });
}

/**
 * Holt eine Sudo-Nonce vom Server (Passwort wird 60 s serverseitig zwischengespeichert).
 * Gibt null zurück wenn der User abbricht.
 */
async function acquireSudoNonce() {
  const password = await promptSudoPassword();
  if (password === null) return null;
  const res  = await csrfFetch('/api/sudo/acquire', {
    method:  'POST',
    headers: { 'Content-Type': 'application/json' },
    body:    JSON.stringify({ password }),
  });
  const data = await res.json();
  return data.nonce || '';
}

// ── Änderungserkennung (nico-version Hash) ────────────────────────────────
function showIntegrityWarning(files) {
  let el = document.getElementById('integrity-warning-banner');
  if (!el) {
    el = document.createElement('div');
    el.id = 'integrity-warning-banner';
    el.className = 'integrity-warning-banner';
    // Dismiss button
    const btn = document.createElement('button');
    btn.className = 'integrity-dismiss-btn';
    btn.innerHTML = niIcon('x');
    btn.addEventListener('click', hideIntegrityWarning);
    el.appendChild(btn);
    const msg = document.createElement('span');
    msg.id = 'integrity-warning-msg';
    el.appendChild(msg);
    const header = document.querySelector('#app header');
    header?.insertAdjacentElement('afterend', el);
  }
  document.getElementById('integrity-warning-msg').textContent =
    t('integrity.modified').replace('{files}', files.length);
  el.classList.remove('hidden');
}

function hideIntegrityWarning() {
  document.getElementById('integrity-warning-banner')?.classList.add('hidden');
}

async function saveConfig() {
  if (!_canSaveCurrentCoForm()) {
    showToast(t('toast.error'), 'error');
    return;
  }
  const payload = getFormData();
  const isHostMode = !!_activeHost;
  if (isHostMode) delete payload._host;
  const url = isHostMode
    ? `/api/host/${encodeURIComponent(_activeHost)}/config`
    : '/api/config';

  const res  = await csrfFetch(url, {
    method:  'POST',
    headers: { 'Content-Type': 'application/json' },
    body:    JSON.stringify(payload),
  });
  const data = await res.json();
  showToast(data.success ? t('toast.saved') : (tErr(data.error) || t('toast.error')),
            data.success ? 'success' : 'error');
  if (data.success) {
    document.dispatchEvent(new CustomEvent('nico:config-saved'));
  }
}

// Silent auto-save: saves to JSON, no success toast, only error toast
async function _autoSave() {
  if (!_canSaveCurrentCoForm()) return false;
  const payload = getFormData();
  const isHostMode = !!_activeHost;
  if (isHostMode) delete payload._host;
  const url = isHostMode
    ? `/api/host/${encodeURIComponent(_activeHost)}/config`
    : '/api/config';

  const res  = await csrfFetch(url, {
    method:  'POST',
    headers: { 'Content-Type': 'application/json' },
    body:    JSON.stringify(payload),
  }).catch(() => null);
  if (!res) return false;
  const data = await res.json().catch(() => null);
  if (data?.success) {
    document.dispatchEvent(new CustomEvent('nico:config-saved'));
    return true;
  }
  if (data && !data.success) showToast(tErr(data.error) || t('toast.error'), 'error');
  return false;
}

// Silent write to .nix files; shows error toast on failure, returns success bool
async function _writeNix() {
  const url = _activeHost
    ? `/api/host/${encodeURIComponent(_activeHost)}/write`
    : '/api/write';

  const res = await csrfFetch(url, {
    method:  'POST',
    headers: { 'Content-Type': 'application/json' },
    body:    JSON.stringify({ commit: false }),
  }).catch(() => null);
  if (!res) { showToast(t('toast.error'), 'error'); return false; }
  const data = await res.json().catch(() => null);
  if (!data?.success) { showToast(tErr(data?.error) || t('toast.error'), 'error'); return false; }
  return true;
}

async function saveAndWrite() {
  if (!_canSaveCurrentCoForm()) {
    showToast(t('toast.error'), 'error');
    return false;
  }
  const payload = getFormData();
  const isHostMode = !!_activeHost;
  if (isHostMode) delete payload._host;
  const cfgUrl = isHostMode
    ? `/api/host/${encodeURIComponent(_activeHost)}/config`
    : '/api/config';
  const writeUrl = isHostMode
    ? `/api/host/${encodeURIComponent(_activeHost)}/write`
    : '/api/write';

  const cfgRes  = await csrfFetch(cfgUrl, {
    method:  'POST',
    headers: { 'Content-Type': 'application/json' },
    body:    JSON.stringify(payload),
  });
  const cfgData = await cfgRes.json();
  if (!cfgData.success) {
    showToast(tErr(cfgData.error) || t('toast.error'), 'error');
    return false;
  }
  const writeRes  = await csrfFetch(writeUrl, {
    method:  'POST',
    headers: { 'Content-Type': 'application/json' },
    body:    JSON.stringify({ commit: false }),
  });
  const writeData = await writeRes.json();
  showToast(writeData.success ? t('toast.saved') : (tErr(writeData.error) || t('toast.error')),
            writeData.success ? 'success' : 'error');
  if (writeData.success) {
    document.dispatchEvent(new CustomEvent('nico:config-saved'));
    return true;
  }
  return false;
}

// ── Write Nix files ────────────────────────────────────────────────────────
function openWriteConfirm() {
  document.getElementById('write-label-input').value = '';
  const dryResult = document.getElementById('write-dryrun-result');
  if (dryResult) { dryResult.textContent = ''; dryResult.classList.add('hidden'); }
  document.getElementById('write-overlay').classList.remove('hidden');
  setTimeout(() => document.getElementById('write-label-input').focus(), 50);
}

function closeWriteConfirm() {
  document.getElementById('write-overlay').classList.add('hidden');
}

async function writeFiles() {
  const label      = document.getElementById('write-label-input')?.value.trim() ?? '';
  const doDryrun   = document.getElementById('write-dryrun-check')?.checked;
  const dryResultEl = document.getElementById('write-dryrun-result');

  if (doDryrun) {
    if (!await Sidebar.flakeSave()) return;
    if (!await _autoSave()) return;
    if (!await _writeNix()) return;

    dryResultEl.textContent = '…';
    dryResultEl.style.color = '';
    dryResultEl.classList.remove('hidden');

    const dr = await csrfFetch('/api/dry-run', {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify({ _host: _activeHost }),
    }).catch(() => null);

    if (!dr) {
      dryResultEl.textContent = t('dryrun.networkError');
      dryResultEl.style.color = 'var(--red)';
      return;
    }
    const dd = await dr.json();
    dryResultEl.textContent = dd.output || (dd.ok ? '✓ OK' : t('dryrun.failed'));  // '✓' kept intentionally (text output)
    dryResultEl.style.color = dd.ok ? 'var(--green)' : 'var(--red)';
    if (!dd.ok) return;  // stop – show error, let user decide
  }

  closeWriteConfirm();
  await Sidebar.flakeSave();
  await saveConfig();
  const writeUrl = _activeHost
    ? `/api/host/${encodeURIComponent(_activeHost)}/write`
    : '/api/write';
  const res  = await csrfFetch(writeUrl, {
    method:  'POST',
    headers: { 'Content-Type': 'application/json' },
    body:    JSON.stringify({ label }),
  });
  const data = await res.json();
  if (data.success) {
    if (data.pushed) {
      showToast(t('git.pushAutoSuccess'), 'success');
    } else {
      showToast(t('write.success', data.written.join(', ')));
      if (data.push_error) _showGitPushErrorModal(data.push_error, data.push_error_code);
    }
  } else {
    showToast(tErr(data.error) || t('toast.error'), 'error');
  }
}

// ── Export ZIP ─────────────────────────────────────────────────────────────
function exportZip() {
  window.location.href = '/api/export';
}

// ── ZIP-Import ──────────────────────────────────────────────────────────────
let _pendingZipFile = null;

function _zipImportResetState() {
  _pendingZipFile = null;
  document.getElementById('admin-zip-import-state').classList.add('hidden');
  ['admin-zip-checking', 'admin-zip-confirm', 'admin-zip-backup-confirm', 'admin-zip-invalid']
    .forEach(id => document.getElementById(id)?.classList.add('hidden'));
}

function initZipImport() {
  on('admin-zip-import-btn', 'click', () => {
    _zipImportResetState();
    document.getElementById('admin-zip-input').value = '';
    document.getElementById('admin-zip-input').click();
  });

  document.getElementById('admin-zip-input')?.addEventListener('change', async (e) => {
    const file = e.target.files[0];
    if (!file) return;
    _pendingZipFile = file;

    const stateDiv   = document.getElementById('admin-zip-import-state');
    const checkingEl = document.getElementById('admin-zip-checking');
    stateDiv.classList.remove('hidden');
    checkingEl.classList.remove('hidden');

    const fd = new FormData();
    fd.append('file', file);
    let data;
    try {
      const res = await csrfFetch('/api/import/zip/check', { method: 'POST', body: fd });
      data = await res.json();
    } catch {
      checkingEl.classList.add('hidden');
      _showZipInvalid(t('toast.error'));
      return;
    }
    checkingEl.classList.add('hidden');

    if (data.error) {
      _showZipInvalid(tErr(data.error));
      return;
    }
    if (!data.valid) {
      _showZipInvalid(t('admin.zipInvalidHint'));
      return;
    }

    // Gültige ZIP – Bestätigung anzeigen
    document.getElementById('admin-zip-confirm-text').textContent =
      t('admin.zipConfirmText', data.found_file);
    document.getElementById('admin-zip-confirm').classList.remove('hidden');
  });

  on('admin-zip-confirm-yes',    'click', () => _doZipApply(false));
  on('admin-zip-confirm-cancel', 'click', _zipImportResetState);
  on('admin-zip-backup-yes',     'click', () => _doZipApply(true));
  on('admin-zip-backup-cancel',  'click', _zipImportResetState);
  on('admin-zip-invalid-cancel', 'click', _zipImportResetState);

  on('admin-zip-fallback-btn', 'click', () => {
    _zipImportResetState();
    // Manuellen Import-Browser öffnen (Import-Sektion aufklappen + Browser starten)
    const body = document.getElementById('admin-import-body');
    const section = document.getElementById('admin-import-section');
    if (body?.classList.contains('hidden') || section?.classList.contains('collapsed')) {
      document.getElementById('admin-import-toggle')?.click();
    }
    document.getElementById('admin-import-body')?.scrollIntoView({ behavior: 'smooth' });
  });
}

function _showZipInvalid(msg) {
  document.getElementById('admin-zip-import-state').classList.remove('hidden');
  document.getElementById('admin-zip-invalid-text').textContent = msg;
  document.getElementById('admin-zip-invalid').classList.remove('hidden');
}

async function _doZipApply(confirmed) {
  if (!_pendingZipFile) return;

  document.getElementById('admin-zip-confirm').classList.add('hidden');
  document.getElementById('admin-zip-backup-confirm').classList.add('hidden');
  const checkingEl = document.getElementById('admin-zip-checking');
  checkingEl.textContent = t('admin.zipImporting');
  checkingEl.classList.remove('hidden');

  const fd = new FormData();
  fd.append('file', _pendingZipFile);
  fd.append('confirmed', confirmed ? 'true' : 'false');

  let res, data;
  try {
    res  = await csrfFetch('/api/import/zip/apply', { method: 'POST', body: fd });
    data = await res.json();
  } catch {
    checkingEl.classList.add('hidden');
    _showZipInvalid(t('toast.error'));
    return;
  }
  checkingEl.classList.add('hidden');

  if (data.needs_backup_confirmation) {
    document.getElementById('admin-zip-backup-text').textContent =
      t('import.backupConfirm', data.zip_name);
    document.getElementById('admin-zip-backup-confirm').classList.remove('hidden');
    return;
  }

  if (!res.ok || data.error) {
    _showZipInvalid(tErr(data.error));
    return;
  }

  _zipImportResetState();
  await categorizeFiles();
  await loadConfig();
  loadTree();
  showToast(t('import.success'), 'success');
}

// ── Settings Export/Import ─────────────────────────────────────────────────

function exportAppSettings() {
  window.location.href = '/api/settings/app/export';
}

function _settingsImportReset() {
  document.getElementById('admin-settings-import-state').classList.add('hidden');
  ['admin-settings-import-confirm', 'admin-settings-import-error']
    .forEach(id => document.getElementById(id)?.classList.add('hidden'));
}

function initSettingsImport() {
  on('admin-settings-export-btn', 'click', exportAppSettings);

  on('admin-settings-import-btn', 'click', () => {
    _settingsImportReset();
    document.getElementById('admin-settings-import-input').value = '';
    document.getElementById('admin-settings-import-input').click();
  });

  document.getElementById('admin-settings-import-input')?.addEventListener('change', async (e) => {
    const file = e.target.files[0];
    if (!file) return;

    let data;
    try {
      data = JSON.parse(await file.text());
    } catch {
      document.getElementById('admin-settings-import-state').classList.remove('hidden');
      document.getElementById('admin-settings-import-error-text').textContent = t('admin.settingsImportErrJson');
      document.getElementById('admin-settings-import-error').classList.remove('hidden');
      return;
    }

    document.getElementById('admin-settings-import-confirm-text').textContent =
      t('admin.settingsImportConfirm');
    document.getElementById('admin-settings-import-state').classList.remove('hidden');
    document.getElementById('admin-settings-import-confirm').classList.remove('hidden');

    on('admin-settings-import-yes', 'click', async () => {
      const fd = new FormData();
      fd.append('file', file);
      let res, result;
      try {
        res    = await csrfFetch('/api/settings/app/import', { method: 'POST', body: fd });
        result = await res.json();
      } catch {
        showToast(t('toast.error'), 'error');
        _settingsImportReset();
        return;
      }
      _settingsImportReset();
      if (!res.ok || result.error) {
        showToast(tErr(result?.error) || t('toast.error'), 'error');
        return;
      }
      showToast(t('admin.settingsImportSuccess'), 'success');
      // Reload to apply language/theme changes
      setTimeout(() => location.reload(), 800);
    }, { once: true });

    on('admin-settings-import-cancel', 'click', _settingsImportReset, { once: true });
  });

  on('admin-settings-import-error-cancel', 'click', _settingsImportReset);
}

async function detachConfig() {
  if (!confirm(t('admin.detachConfirm'))) return;

  try {
    await _autoSave();
    await Sidebar.flakeSave();
  } catch {
    // Best effort only – detach should still be possible.
  }

  let res, data;
  try {
    res = await csrfFetch('/api/config/detach', { method: 'POST' });
    data = await res.json();
  } catch {
    showToast(t('toast.error'), 'error');
    return;
  }

  if (!res.ok || data.error) {
    showToast(tErr(data.error) || t('toast.error'), 'error');
    return;
  }

  showToast(t('admin.detachSuccess', data.backup), 'success');
  setTimeout(() => location.reload(), 900);
}

// ── Admin-Import ───────────────────────────────────────────────────────────

function initImportBrowse() {
  on('import-config-browse', 'click', () => {
    const pathEl = document.getElementById('import-config-path');
    const cur = pathEl?.value.trim() || '/etc/nixos';
    openDirBrowser(cur, path => {
      if (pathEl) pathEl.value = path;
    });
  });
}

async function runAdminImport() {
  const dir      = document.getElementById('import-config-path')?.value.trim() || '';
  const resultEl = document.getElementById('import-result');
  if (!dir) return;
  const configPath = dir.replace(/\/+$/, '') + '/configuration.nix';
  resultEl.textContent = '…';
  resultEl.className   = 'import-result';
  await _doAdminImport(configPath, false, resultEl);
}

async function _doAdminImport(configPath, confirmed, resultEl) {
  const body = confirmed
    ? { config_path: configPath, confirmed: true }
    : { config_path: configPath };

  const res  = await csrfFetch('/api/import/run', {
    method:  'POST',
    headers: { 'Content-Type': 'application/json' },
    body:    JSON.stringify(body),
  });
  const data = await res.json();

  if (data.needs_backup_confirmation) {
    resultEl.innerHTML =
      `${escHtml(t('import.backupConfirm', data.zip_name))}\n` +
      `<div class="confirm-buttons" style="margin-top:8px">` +
        `<button class="btn-primary" id="admin-backup-yes">${escHtml(t('import.backupYes'))}</button>` +
        `<button id="admin-backup-no">${escHtml(t('import.backupCancel'))}</button>` +
      `</div>`;
    document.getElementById('admin-backup-yes')?.addEventListener('click', async () => {
      resultEl.textContent = '…';
      await _doAdminImport(configPath, true, resultEl);
    });
    document.getElementById('admin-backup-no')?.addEventListener('click', () => {
      resultEl.className = 'import-result hidden';
    });
    return;
  }

  if (!res.ok || data.error) {
    resultEl.innerHTML = `<span class="import-err">${escHtml(tErr(data.error))}</span>`;
    resultEl.classList.remove('hidden');
    return;
  }

  resultEl.className = 'import-result hidden';
  await categorizeFiles();
  await loadConfig();
  showToast(t('import.success'), 'success');
}


function showImportResult(resultEl, data) {
  const rec   = data.recognized || {};
  const keys  = Object.keys(rec);
  const lines = [];
  if (keys.length > 0) {
    const labelMap = {
      hostname: t('field.hostname'), state_version: t('field.stateVersion'),
      timezone: t('field.timezone'), locale: t('field.locale'),
      keyboard_layout: t('field.keyboardLayout'), keyboard_console: t('field.keyboardConsole'),
      keyboard_variant: 'xkb variant',
      networkmanager: t('field.networkmanager'), ssh: t('field.ssh'),
      firewall_disable: t('field.firewallDisable'),
      firewall_tcp_enable: t('field.firewallTcpEnable'), firewall_tcp_ports: t('field.firewallTcpEnable'),
      firewall_udp_enable: t('field.firewallUdpEnable'), firewall_udp_ports: t('field.firewallUdpEnable'),
      printing: t('field.printing'), avahi: t('field.avahi'),
      bluetooth: t('field.bluetooth'), blueman: t('field.blueman'),
      pipewire: t('field.pipewire'), desktop_environment: t('field.desktopEnv'),
      autologin_user: t('field.autologin'), username: t('field.username'),
      allowUnfree: t('field.allowUnfree'), flakes: t('field.flakes'),
      nix_optimize_store: t('field.optimizeStore'),
    };
    lines.push(`<strong>${escHtml(t('import.recognized'))}</strong>`);
    for (const k of keys) {
      const label = labelMap[k] || k;
      const val   = rec[k];
      lines.push(`  ${escHtml(label)}: ${escHtml(typeof val === 'boolean' ? (val ? '✓' : '✗') : String(val))}`);
    }
  }
  if (data.has_brix) lines.push(`\n${escHtml(t('import.brixInfo'))}`);
  const files = data.files_copied || [];
  if (files.length > 0) {
    lines.push(`\n${escHtml(t('import.filesCopied'))}: ${files.length}`);
    for (const f of files) lines.push(`  ${escHtml(f)}`);
  }
  if (data.copy_error) lines.push(`<span class="import-err">${escHtml(tErr(data.copy_error))}</span>`);
  resultEl.innerHTML = lines.join('\n');
  resultEl.classList.remove('hidden');
}

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

  const hmTab = document.getElementById('hm-tab');
  if (data.home_nix) {
    renderCodePreview(data.home_nix, 'preview-hm', 'home.nix');
    hmTab?.classList.remove('hidden');
  } else {
    hmTab?.classList.add('hidden');
    if (activeTab === 'hm') switchTab('configuration');
  }

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
    if (activeFile?.path === 'flake.nix') {
      const res = await fetch(`/api/file?path=${encodeURIComponent(file)}`);
      const data = await res.json();
      if (!data.error) {
        await _renderFileIntoView(file, data, { skipTypeDialog: true });
        return;
      }
    }
    await _updateFlakePreview();
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
  console.log('[renderCodePreview] start', {
    containerId,
    file,
    plainCodeView,
    codeLength: code?.length ?? 0,
  });
  container.dataset.sourceCode = code;
  container.dataset.sourceFile = file;
  if (plainCodeView) {
    const stripped = stripAnnotations(code);
    console.log('[renderCodePreview] plain', {
      containerId,
      file,
      originalLength: code?.length ?? 0,
      strippedLength: stripped?.length ?? 0,
      changed: stripped !== code,
      preview: stripped.slice(0, 300),
    });
    renderPlainPreview(containerId, stripped);
    return;
  }
  console.log('[renderCodePreview] annotated', {
    containerId,
    file,
    preview: String(code).slice(0, 300),
  });
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

// ── Brix-Kontextmenü ────────────────────────────────────────────────────────
function closeAllBrixMenus() {
  document.querySelectorAll('.brix-context-menu').forEach(m => m.classList.add('hidden'));
}
document.addEventListener('click', closeAllBrixMenus);

// ── Brix-Block einfügen ─────────────────────────────────────────────────────
function openBrixInsert() {
  document.getElementById('brix-insert-overlay').classList.remove('hidden');
  document.getElementById('brix-name-input').value = '';
  document.getElementById('brix-insert-error').classList.add('hidden');
  const sel = document.getElementById('brix-section-select');
  if (sel) {
    // Repopulate sections based on target file
    sel.innerHTML = '';
    const sections = _brixTargetFtype === 'fl' ? _getFlakeBrickSections()
                   : _brixTargetFtype === 'hm' ? HM_BRICK_SECTIONS
                   : BRICK_SECTIONS;
    sections.forEach(s => {
      const opt = document.createElement('option');
      opt.value = s; opt.textContent = s;
      sel.appendChild(opt);
    });
    sel.value = 'End';
  }
  document.getElementById('brix-name-input').focus();
}

function closeBrixInsert() {
  document.getElementById('brix-insert-overlay').classList.add('hidden');
}

async function insertBrix() {
  const name    = document.getElementById('brix-name-input').value.trim();
  const section = document.getElementById('brix-section-select')?.value || 'End';
  const errorEl = document.getElementById('brix-insert-error');
  errorEl.classList.add('hidden');

  if (!name) {
    errorEl.textContent = t('brix.errNoName');
    errorEl.classList.remove('hidden');
    return;
  }
  if (!/^[\w\-]+$/.test(name)) {
    errorEl.textContent = t('brix.errInvalidName');
    errorEl.classList.remove('hidden');
    return;
  }

  const res  = await csrfFetch('/api/brick', {
    method:  'POST',
    headers: { 'Content-Type': 'application/json' },
    body:    JSON.stringify({ name, section, file: _brixTargetFile }),
  });
  const data = await res.json();
  if (data.error) {
    errorEl.textContent = tErr(data.error);
    errorEl.classList.remove('hidden');
    return;
  }

  closeBrixInsert();
  showToast(t('brix.inserted', data.brick_name));

  // Preview aktualisieren: flake.nix is reloaded from disk, other main files
  // keep using the generator preview, host files are reloaded directly.
  const isMainFile = _brixTargetFile === 'configuration.nix' || _brixTargetFile === 'flake.nix';
  if (isMainFile) {
    await _refreshPreviewForFile(_brixTargetFile);
  } else {
    // Host-Datei: Inhalt neu laden und Preview mit korrektem file-Parameter rendern
    try {
      const res = await fetch(`/api/file?path=${encodeURIComponent(_brixTargetFile)}`);
      const fileData = await res.json();
      if (!fileData.error) {
        renderCodePreview(fileData.content, 'preview-configuration', _brixTargetFile);
      }
    } catch { /* non-fatal */ }
  }

  const brickEl = document.querySelector(`[data-brick-name="${CSS.escape(data.brick_name)}"]`);
  brickEl?.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
}

// ── Nix-Brick löschen ───────────────────────────────────────────────────────
async function confirmDeleteBrix(name, file = 'configuration.nix') {
  if (!confirm(t('brix.confirmDelete', name))) {
    return;
  }
  await csrfFetch(`/api/brick/${encodeURIComponent(name)}?file=${encodeURIComponent(file)}`, { method: 'DELETE' });
  showToast(t('brix.deleted', name));
  await _refreshPreviewForFile(file);
}

// ── Nix-Brick löschen (Dialog) ───────────────────────────────────────────────
async function openBrixDelete() {
  const cfg   = await csrfFetch('/api/config').then(r => r.json());
  const names = Object.keys(cfg.brick_blocks || {});
  if (names.length === 0) {
    showToast(t('brix.noBlocks'), 'error');
    return;
  }
  const selected = await new Promise(resolve => {
    const overlay = document.createElement('div');
    overlay.className = 'overlay';
    overlay.innerHTML = `
      <div class="dialog">
        <h2 class="dialog-title" data-i18n="brix.deleteTitle">${t('brix.deleteTitle')}</h2>
        <select id="brix-delete-select" class="brix-select">
          ${names.map(n => `<option value="${escHtml(n)}">${escHtml(n)}</option>`).join('')}
        </select>
        <div class="dialog-actions">
          <button id="brix-delete-confirm" class="btn-danger">${t('brix.deleteConfirmBtn')}</button>
          <button id="brix-delete-cancel"  class="btn-secondary">${t('unsaved.cancel') || 'Abbrechen'}</button>
        </div>
      </div>`;
    document.body.appendChild(overlay);
    overlay.querySelector('#brix-delete-confirm').addEventListener('click', () => {
      const val = overlay.querySelector('#brix-delete-select').value;
      document.body.removeChild(overlay);
      resolve(val);
    });
    overlay.querySelector('#brix-delete-cancel').addEventListener('click', () => {
      document.body.removeChild(overlay);
      resolve(null);
    });
  });
  if (selected) confirmDeleteBrix(selected);
}

// ── Brix verschieben ────────────────────────────────────────────────────────
const FLAKE_BRICK_SECTIONS = ['Start', 'Inputs', 'Outputs', 'End'];
const HM_BRICK_SECTIONS    = ['Start', 'Home Manager', 'End'];
let _brixMoveTarget = null;

function _getFlakeBrickSections(cfg = null) {
  const sections = [...FLAKE_BRICK_SECTIONS];
  const hosts = cfg?.flake_hosts
    || Array.from(document.querySelectorAll('#flake-hosts-list .flake-host-card'))
      .map(card => ({ name: card.dataset.host || '' }));
  hosts.forEach(host => {
    const name = typeof host === 'string' ? host : (host?.name || '');
    if (!name) return;
    const section = `Host: ${name}`;
    if (!sections.includes(section)) sections.push(section);
  });
  return sections;
}

async function openBrixMove(targetName = null) {
  const cfg    = await csrfFetch('/api/config').then(r => r.json());
  // Bricks from the target file: flake.nix or configuration.nix
  const isFlake = _brixContextFile === 'flake.nix';
  const isHm    = _brixContextFtype === 'hm';
  const blocks  = isFlake ? (cfg.flake_brick_blocks || {})
                : isHm    ? (cfg.hm_brick_blocks || {})
                : (cfg.brick_blocks || {});
  const names   = Object.keys(blocks);

  if (names.length === 0) {
    showToast(t('brix.noBlocks'), 'error');
    return;
  }

  _brixMoveTarget = (targetName && blocks[targetName]) ? targetName : (names[0] || null);
  const block = _brixMoveTarget ? blocks[_brixMoveTarget] : null;
  const sourceName = document.getElementById('brix-move-source-name');
  const cancelBtn = document.getElementById('brix-move-cancel-btn');
  if (sourceName) {
    sourceName.textContent = _brixMoveTarget
      ? `${block?.section ?? '?'} #${block?.order ?? '?'} · ${_brixMoveTarget}`
      : '';
  }
  if (cancelBtn) {
    cancelBtn.setAttribute('data-i18n', 'brix.cancelBtn');
    cancelBtn.textContent = t('brix.cancelBtn');
  }

  // Populate section select – repopulate every time (context may have changed)
  const sectionSel = document.getElementById('brix-move-section');
  if (sectionSel) {
    sectionSel.innerHTML = '';
    (isFlake ? _getFlakeBrickSections(cfg) : _brixContextFtype === 'hm' ? HM_BRICK_SECTIONS : BRICK_SECTIONS).forEach(s => {
      const opt = document.createElement('option');
      opt.value = s; opt.textContent = s;
      sectionSel.appendChild(opt);
    });
  }

  // Pre-fill section/order from the selected brick
  if (sectionSel && block) sectionSel.value = block.section || 'End';
  document.getElementById('brix-move-order').value = block?.order ?? 1;
  document.getElementById('brix-move-warning').classList.add('hidden');
  document.getElementById('brix-move-overlay').classList.remove('hidden');
}

function closeBrixMove() {
  document.getElementById('brix-move-overlay').classList.add('hidden');
  _brixMoveTarget = null;
  // Reset cancel button back to "Abbrechen"
  const cancelBtn = document.getElementById('brix-move-cancel-btn');
  if (cancelBtn) {
    cancelBtn.setAttribute('data-i18n', 'brix.cancelBtn');
    cancelBtn.textContent = t('brix.cancelBtn');
  }
}

async function moveBrix() {
  const name     = _brixMoveTarget;
  const section  = document.getElementById('brix-move-section')?.value || '';
  const orderVal = document.getElementById('brix-move-order').value.trim();
  const order    = parseInt(orderVal, 10);

  if (!name) { showToast(t('brix.noBlocks'), 'error'); return; }
  if (!section || !orderVal || isNaN(order) || order < 1) {
    document.getElementById('brix-move-warning').classList.remove('hidden');
    return;
  }

  const res  = await csrfFetch('/api/brick/move', {
    method:  'POST',
    headers: { 'Content-Type': 'application/json' },
    body:    JSON.stringify({ name, section, order, file: _brixContextFile }),
  });
  const data = await res.json();
  if (data.error) {
    showToast(tErr(data.error), 'error');
    return;
  }

  showToast(t('brix.moved', name));
  closeBrixMove();

  // Refresh preview after closing the dialog
  try {
    await _refreshPreviewForFile(_brixContextFile);
  } catch (e) {
    console.warn('moveBrix refresh failed:', e);
  }
}

// ── Brix verschieben (aus Kontextmenü, mit vorausgewähltem Brick) ──────────
async function openBrixMoveFor(name) {
  await openBrixMove(name);
}

// ── Brix umbenennen ──────────────────────────────────────────────────────────
let _brixRenameTarget = null;

function openBrixRename(name) {
  _brixRenameTarget = name;
  document.getElementById('brix-rename-new').value = name;
  document.getElementById('brix-rename-error').classList.add('hidden');
  document.getElementById('brix-rename-overlay').classList.remove('hidden');
  document.getElementById('brix-rename-new').focus();
}

function closeBrixRename() {
  document.getElementById('brix-rename-overlay').classList.add('hidden');
  _brixRenameTarget = null;
}

async function renameBrix() {
  const newName = document.getElementById('brix-rename-new').value.trim();
  const errorEl = document.getElementById('brix-rename-error');
  errorEl.classList.add('hidden');

  if (!newName) {
    errorEl.textContent = t('brix.errNoName');
    errorEl.classList.remove('hidden');
    return;
  }
  if (!/^[\w\-]+$/.test(newName)) {
    errorEl.textContent = t('brix.errInvalidName');
    errorEl.classList.remove('hidden');
    return;
  }

  const res  = await csrfFetch('/api/brick/rename', {
    method:  'POST',
    headers: { 'Content-Type': 'application/json' },
    body:    JSON.stringify({ old_name: _brixRenameTarget, new_name: newName, file: _brixContextFile }),
  });
  const data = await res.json();
  if (data.error) {
    errorEl.textContent = tErr(data.error);
    errorEl.classList.remove('hidden');
    return;
  }
  closeBrixRename();
  showToast(t('brix.renamed', data.new_name));
  await _refreshPreviewForFile(_brixContextFile);
}

// ── Brix splitten ───────────────────────────────────────────────────────────
let _brixSplitTarget = null;

function openBrixSplit(name) {
  _brixSplitTarget = name;
  document.getElementById('brix-split-line').value = '1';
  document.getElementById('brix-split-error').classList.add('hidden');
  document.getElementById('brix-split-overlay').classList.remove('hidden');
  document.getElementById('brix-split-line').focus();
}

function closeBrixSplit() {
  document.getElementById('brix-split-overlay').classList.add('hidden');
  _brixSplitTarget = null;
}

async function splitBrix() {
  const splitLine = parseInt(document.getElementById('brix-split-line').value, 10);
  const errorEl   = document.getElementById('brix-split-error');
  errorEl.classList.add('hidden');

  if (isNaN(splitLine) || splitLine < 1) {
    errorEl.textContent = t('brix.errSplitLine');
    errorEl.classList.remove('hidden');
    return;
  }

  const newName = `${_brixSplitTarget}-2`;
  const res  = await csrfFetch('/api/brick/split', {
    method:  'POST',
    headers: { 'Content-Type': 'application/json' },
    body:    JSON.stringify({ name: _brixSplitTarget, split_line: splitLine, new_name: newName, file: _brixContextFile }),
  });
  const data = await res.json();
  if (data.error) {
    errorEl.textContent = tErr(data.error);
    errorEl.classList.remove('hidden');
    return;
  }
  closeBrixSplit();
  showToast(t('brix.split', data.new_name));
  await _refreshPreviewForFile(_brixContextFile);
}

// ── Brix inline editing ──────────────────────────────────────────────────────

function openBrixInlineEdit(name, currentContent, wrapEl, bodyWrapper, file = 'configuration.nix') {
  bodyWrapper.style.display = 'none';

  const editor = document.createElement('div');
  editor.className = 'brix-inline-editor';

  const textarea = document.createElement('textarea');
  textarea.className = 'brix-inline-textarea';
  textarea.value = currentContent;
  textarea.rows  = Math.max(5, currentContent.split('\n').length + 2);

  const actions = document.createElement('div');
  actions.className = 'brix-edit-actions';

  const saveBtn = document.createElement('button');
  saveBtn.className   = 'btn-primary';
  saveBtn.textContent = t('brix.editSave');
  saveBtn.addEventListener('click', async () => {
    saveBtn.disabled = true;
    const ok = await saveBrixInline(name, textarea.value, file);
    if (ok) {
      bodyWrapper.style.display = '';
      editor.remove();
      await _refreshPreviewForFile(file);
    } else {
      saveBtn.disabled = false;
    }
  });

  const cancelBtn = document.createElement('button');
  cancelBtn.className   = 'btn-secondary';
  cancelBtn.textContent = t('brix.editCancel');
  cancelBtn.addEventListener('click', () => {
    bodyWrapper.style.display = '';
    editor.remove();
  });

  actions.appendChild(saveBtn);
  actions.appendChild(cancelBtn);
  editor.appendChild(textarea);
  editor.appendChild(actions);
  wrapEl.appendChild(editor);
  textarea.focus();
}

async function saveBrixInline(name, content, file = 'configuration.nix') {
  const res  = await csrfFetch(`/api/brick/${encodeURIComponent(name)}`, {
    method:  'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body:    JSON.stringify({ content, file }),
  });
  const data = await res.json();
  if (data.error) {
    let msg = tErr(data.error);
    if (data.details?.length) msg += ': ' + data.details.join(', ');
    showToast(msg, 'error');
    return false;
  }
  showToast(t('brix.editSaved'));
  return true;
}

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

// ── Rebuild ───────────────────────────────────────────────────────────────────
//
// Architecture: mode ("switch"|"boot"|"test") is already a URL param so the UI
// can offer a mode selector later without touching server.py.
// EventSource requires GET → CSRF token passed as query param.
let _rebuildES = null;  // active EventSource, closed on modal close

// ── Flake host picker ────────────────────────────────────────────────────────

async function _fetchFlakeHosts() {
  try {
    const res = await csrfFetch('/api/flake/hosts');
    return await res.json();
  } catch { return { flake_mode: false, hosts: [] }; }
}

/**
 * Show a host-selection dialog.
 * allowAll: add an "Alle Hosts" option (for dry-run only).
 * Returns: hostname string, '__all__', or null (cancelled).
 */
function _showHostPicker(hosts, { allowAll = false, title = '', defaultHost = null } = {}) {
  return new Promise(resolve => {
    const overlay = document.createElement('div');
    overlay.className = 'overlay';

    const allOption = allowAll
      ? `<option value="__all__">${t('flake.allHosts')}</option>` : '';
    const hostOptions = hosts
      .map(h => {
        const n = escHtml(typeof h === 'object' ? h.name : h);
        const sel = (defaultHost && n === defaultHost) ? ' selected' : '';
        return `<option value="${n}"${sel}>${n}</option>`;
      })
      .join('');

    overlay.innerHTML = `
      <div class="dialog">
        <h2 class="dialog-title">${escHtml(title || t('flake.pickHost'))}</h2>
        <p class="dialog-info">${escHtml(t('flake.pickHostInfo'))}</p>
        <select id="_host-picker-sel" class="brix-select">
          ${allOption}${hostOptions}
        </select>
        <div class="dialog-actions">
          <button id="_host-picker-ok"     class="btn-primary">${t('flake.pickHostOk')}</button>
          <button id="_host-picker-cancel" class="btn-secondary">${t('unsaved.cancel')}</button>
        </div>
      </div>`;
    document.body.appendChild(overlay);

    const ok = overlay.querySelector('#_host-picker-ok');
    const cancel = overlay.querySelector('#_host-picker-cancel');
    const sel = overlay.querySelector('#_host-picker-sel');

    ok.addEventListener('click', () => {
      document.body.removeChild(overlay);
      resolve(sel.value || null);
    });
    cancel.addEventListener('click', () => {
      document.body.removeChild(overlay);
      resolve(null);
    });
    sel.focus();
  });
}

/**
 * Zeigt einen kleinen Optionen-Dialog vor Rebuild/Dry-Run.
 * Gibt { updateFlake: bool } zurück, oder null wenn abgebrochen.
 * Die Voreinstellung des Toggles kommt aus dem Admin-Panel.
 * Nur bei Flake-Modus angezeigt.
 */
async function _showRebuildOptions(hostInfo, { saveChoice = false, hostname = '', mode = 'switch' } = {}) {
  if (!hostInfo.flake_mode) return { updateFlake: false, useTerminal: false };

  let defaultFlakeUpdate = false;
  let defaultTerminal = false;
  try {
    const [cfg, app] = await Promise.all([
      csrfFetch('/api/config/settings').then(r => r.json()),
      csrfFetch('/api/app/settings').then(r => r.json()),
    ]);
    defaultFlakeUpdate = !!cfg.flake_update_on_rebuild;
    defaultTerminal    = !!app.rebuild_terminal;
  } catch {
    defaultFlakeUpdate = !!(document.getElementById('flake-update-toggle')?.checked);
  }

  const _buildCmd = (upd) => {
    const base = `sudo nixos-rebuild ${mode} --flake .#${hostname || 'hostname'}`;
    return upd ? `nix flake update && ${base}` : base;
  };

  return new Promise(resolve => {
    const overlay = document.createElement('div');
    overlay.className = 'overlay';
    overlay.innerHTML = `
      <div class="dialog-box" style="min-width:360px">
        <h2 class="dialog-title">${t('rebuild.optionsTitle')}</h2>
        <label class="toggle-row" style="margin:12px 0;cursor:pointer">
          <span>${t('rebuild.optFlakeUpdate')}</span>
          <span class="toggle-wrap">
            <input type="checkbox" id="_rbo-flake-update" ${defaultFlakeUpdate ? 'checked' : ''}>
            <span class="toggle-track"><span class="toggle-thumb"></span></span>
          </span>
        </label>
        <label class="toggle-row" style="margin:4px 0 4px;cursor:pointer">
          <span>${t('rebuild.optTerminal')}</span>
          <span class="toggle-wrap">
            <input type="checkbox" id="_rbo-terminal" ${defaultTerminal ? 'checked' : ''}>
            <span class="toggle-track"><span class="toggle-thumb"></span></span>
          </span>
        </label>
        <div id="_rbo-terminal-opts" style="display:${defaultTerminal ? 'flex' : 'none'};flex-direction:column;gap:4px;margin:4px 0 12px;padding-left:14px;border-left:2px solid var(--border,#444)">
          <label class="toggle-row" style="cursor:pointer">
            <span>${t('rebuild.optShutdown')}</span>
            <span class="toggle-wrap">
              <input type="checkbox" id="_rbo-shutdown">
              <span class="toggle-track"><span class="toggle-thumb"></span></span>
            </span>
          </label>
          <label class="toggle-row" style="cursor:pointer">
            <span>${t('rebuild.optPushShutdown')}</span>
            <span class="toggle-wrap">
              <input type="checkbox" id="_rbo-push-shutdown">
              <span class="toggle-track"><span class="toggle-thumb"></span></span>
            </span>
          </label>
        </div>
        <div style="margin-bottom:12px">
          <div style="font-size:0.78em;color:var(--fg-muted);margin-bottom:4px">${t('rebuild.cmdLabel')}</div>
          <div style="display:flex;align-items:center;gap:6px">
            <code id="_rbo-cmd" style="flex:1;font-size:0.8em;background:var(--bg-code,var(--bg2));padding:6px 8px;border-radius:4px;overflow-x:auto;white-space:nowrap"></code>
            <button id="_rbo-copy" class="btn-surface btn-small" title="${t('rebuild.cmdCopy')}">⎘</button>
          </div>
        </div>
        <div class="dialog-actions">
          <button id="_rbo-ok"     class="btn-primary">${t('rebuild.optStart')}</button>
          <button id="_rbo-cancel" class="btn-secondary">${t('unsaved.cancel')}</button>
        </div>
      </div>`;
    document.body.appendChild(overlay);

    const cmdEl         = overlay.querySelector('#_rbo-cmd');
    const flakeToggle   = overlay.querySelector('#_rbo-flake-update');
    const termToggle    = overlay.querySelector('#_rbo-terminal');
    const termOptsEl    = overlay.querySelector('#_rbo-terminal-opts');
    const shutdownCb    = overlay.querySelector('#_rbo-shutdown');
    const pushShutCb    = overlay.querySelector('#_rbo-push-shutdown');
    const updateCmd     = () => { cmdEl.textContent = _buildCmd(flakeToggle.checked); };
    updateCmd();
    flakeToggle.addEventListener('change', updateCmd);
    termToggle.addEventListener('change', () => {
      termOptsEl.style.display = termToggle.checked ? 'flex' : 'none';
      if (!termToggle.checked) { shutdownCb.checked = false; pushShutCb.checked = false; }
    });
    shutdownCb.addEventListener('change',  () => { if (shutdownCb.checked)  pushShutCb.checked = false; });
    pushShutCb.addEventListener('change',  () => { if (pushShutCb.checked)  shutdownCb.checked  = false; });

    overlay.querySelector('#_rbo-copy').addEventListener('click', () => {
      navigator.clipboard.writeText(cmdEl.textContent).then(
        () => showToast(t('rebuild.cmdCopied'), 'success'),
        () => showToast(t('toast.error'), 'error'),
      );
    });

    overlay.querySelector('#_rbo-ok').addEventListener('click', () => {
      const updateFlake     = !!flakeToggle.checked;
      const useTerminal     = !!termToggle.checked;
      const shutdownAfter   = !!shutdownCb.checked;
      const pushShutdownAfter = !!pushShutCb.checked;
      overlay.remove();
      if (saveChoice) {
        csrfFetch('/api/config/settings', {
          method:  'PATCH',
          headers: { 'Content-Type': 'application/json' },
          body:    JSON.stringify({ flake_update_on_rebuild: updateFlake }),
        }).then(() => {
          const adminToggle = document.getElementById('flake-update-toggle');
          if (adminToggle) adminToggle.checked = updateFlake;
        }).catch(() => {});
      }
      resolve({ updateFlake, useTerminal, shutdownAfter, pushShutdownAfter });
    });
    overlay.querySelector('#_rbo-cancel').addEventListener('click', () => {
      overlay.remove();
      resolve(null);
    });
  });
}

async function openRebuild(mode = 'switch') {
  // Flake mode: pick host first
  const hostInfo = await _fetchFlakeHosts();
  let hostname = null;
  if (hostInfo.flake_mode) {
    if (!hostInfo.hosts.length) {
      showToast(t('flake.noHosts'), 'error');
      return;
    }
    if (hostInfo.hosts.length === 1) {
      hostname = typeof hostInfo.hosts[0] === 'object' ? hostInfo.hosts[0].name : hostInfo.hosts[0];
    } else {
      let defaultHost = null;
      try {
        const dh = await csrfFetch('/api/rebuild/default-host').then(r => r.json());
        defaultHost = dh.default_host || null;
      } catch { /* kein Default */ }
      hostname = await _showHostPicker(hostInfo.hosts, {
        allowAll: false,
        title: t('rebuild.title'),
        defaultHost,
      });
      if (hostname === null) return;  // cancelled
    }
    // Gewählten Host als neuen Default speichern
    csrfFetch('/api/app/settings', {
      method:  'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify({ default_host: hostname }),
    }).catch(() => {});
  }

  // Rebuild-Optionen abfragen; Wahl wird zurückgespeichert (Voreinstellung für nächsten Rebuild)
  const opts = await _showRebuildOptions(hostInfo, { saveChoice: true, hostname, mode });
  if (opts === null) return;  // abgebrochen

  // Flake-Formular speichern falls dirty
  if (!await Sidebar.flakeSave()) return;

  // Terminal-Modus: Befehl in externem Terminal ausführen
  if (opts.useTerminal) {
    const res = await csrfFetch('/api/rebuild/open-terminal', {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify({ hostname, mode, update_flake: opts.updateFlake, shutdown_after: opts.shutdownAfter, push_shutdown_after: opts.pushShutdownAfter }),
    }).catch(() => null);
    if (!res || !res.ok) {
      const err = res ? (await res.json().catch(() => ({}))).error : null;
      showToast(err === 'ERR_NO_TERMINAL' ? t('rebuild.terminalError') : t('toast.error'), 'error');
    } else {
      showToast(t('rebuild.terminalLaunched'), 'success');
    }
    return;
  }

  // Sudo-Passwort VOR dem Overlay abfragen (damit das Dialog nicht dahinter liegt)
  const sudoNonce = await acquireSudoNonce();
  if (sudoNonce === null) return;  // abgebrochen

  const overlay      = document.getElementById('rebuild-overlay');
  const logEl        = document.getElementById('rebuild-log');
  const monitorEl    = document.getElementById('rebuild-monitor');
  const resultEl     = document.getElementById('rebuild-result');
  const closeBtn     = document.getElementById('rebuild-close-btn');
  const pushBtn      = document.getElementById('rebuild-push-btn');
  const pushInfoEl   = document.getElementById('rebuild-push-info');
  const counterEl    = document.getElementById('rph-build-counter');
  const buildPkgEl   = document.getElementById('rph-build-pkg');
  const fetchDoneEl  = document.getElementById('rph-fetch-done');
  const fetchRemainEl= document.getElementById('rph-fetch-remain');
  const fetchCopyEl  = document.getElementById('rph-fetch-copy');

  function _resetMonitor() {
    logEl.innerHTML        = '';
    resultEl.className     = 'rebuild-result hidden';
    resultEl.textContent   = '';
    counterEl.textContent  = '';
    buildPkgEl.textContent = '';
    fetchDoneEl.textContent   = '';
    fetchRemainEl.textContent = '';
    fetchCopyEl.textContent   = '';
    document.querySelectorAll('.rebuild-phase-col').forEach(el => el.classList.remove('active'));
    closeBtn.disabled = true;
    pushBtn.classList.add('hidden');
    pushBtn.disabled = false;
    pushInfoEl.classList.add('hidden');
    pushInfoEl.textContent = '';
  }

  // Reset state
  _resetMonitor();
  overlay.classList.remove('hidden');

  // Close any previous stream
  if (_rebuildES) { _rebuildES.close(); _rebuildES = null; }

  const updateFlake = opts.updateFlake ? '1' : '0';
  const hostParam   = hostname  ? `&hostname=${encodeURIComponent(hostname)}`   : '';
  const nonceParam  = sudoNonce ? `&sudo_nonce=${encodeURIComponent(sudoNonce)}` : '';
  const url = `/api/rebuild/stream?mode=${encodeURIComponent(mode)}&token=${encodeURIComponent(CSRF_TOKEN)}&update_flake=${updateFlake}${hostParam}${nonceParam}`;
  const es  = new EventSource(url);
  _rebuildES = es;

  let isRunning      = true;
  let firstErrorLine = '';
  let hasGlobalProgress = false;
  let maxBuildTotal = 0;
  let maxDlExpected = 0;
  let maxCopiedTotal = 0;
  let maxCopiedExpected = 0;

  function _phaseCol(phase) {
    return document.getElementById('rph-' + phase);
  }

  function _setPhaseActive(phase, active, pkg) {
    const col = _phaseCol(phase);
    if (!col) return;
    if (active) col.classList.add('active');
    else        col.classList.remove('active');
  }

  function _fmtBytes(b) {
    if (b < 1024)       return b + ' B';
    if (b < 1048576)    return (b / 1024).toFixed(1) + ' KB';
    if (b < 1073741824) return (b / 1048576).toFixed(1) + ' MB';
    return (b / 1073741824).toFixed(2) + ' GB';
  }

  function _setBuildProgress(done, total, pkg) {
    const stableTotal = Math.max(maxBuildTotal, total || 0);
    maxBuildTotal = stableTotal;
    const remain = Math.max(0, stableTotal - (done || 0));
    counterEl.textContent  = stableTotal > 0 ? `noch ${remain} von ${stableTotal}` : '';
    buildPkgEl.textContent = pkg || '';
  }

  function _setDlProgress(done, expected, copiedDone = 0, copiedTotal = 0, copiedExpected = 0, copiedLabel = '') {
    maxDlExpected = Math.max(maxDlExpected, expected || 0);
    maxCopiedTotal = Math.max(maxCopiedTotal, copiedTotal || 0);
    maxCopiedExpected = Math.max(maxCopiedExpected, copiedExpected || 0);

    const stableExpected = maxDlExpected;
    const stableCopiedTotal = maxCopiedTotal;
    const stableCopiedExpected = maxCopiedExpected;
    const remain = Math.max(0, stableExpected - (done || 0));
    const copiedRemain = Math.max(0, stableCopiedTotal - (copiedDone || 0));

    fetchDoneEl.textContent   = stableExpected > 0 ? 'geladen ' + _fmtBytes(done || 0) : '';
    fetchRemainEl.textContent = stableExpected > 0 ? 'noch ' + _fmtBytes(remain) : '';
    if (stableCopiedTotal > 0) {
      let copiedText = `noch ${copiedRemain} von ${stableCopiedTotal}`;
      if (stableCopiedExpected > 0) copiedText += ` (${_fmtBytes(Math.max(0, stableCopiedExpected - (done || 0)))})`;
      fetchCopyEl.textContent = copiedText;
    } else {
      fetchCopyEl.textContent = '';
    }
  }

  function _finishMonitor(success, message) {
    isRunning = false;
    counterEl.textContent     = '';
    buildPkgEl.textContent    = '';
    fetchDoneEl.textContent   = '';
    fetchRemainEl.textContent = '';
    fetchCopyEl.textContent   = '';
    document.querySelectorAll('.rebuild-phase-col').forEach(el => el.classList.remove('active'));
    resultEl.className  = 'rebuild-result ' + (success ? 'result-success' : 'result-failed');
    resultEl.innerHTML  = message;
    closeBtn.disabled    = false;
  }

  es.onmessage = (event) => {
    let msg;
    try { msg = JSON.parse(event.data); } catch { return; }

    if (msg.type === 'output') {
      const line = msg.line;

      // Append colorized line to log
      const span = document.createElement('span');
      if (/error:/i.test(line))        span.className = 'log-error';
      else if (/warning:/i.test(line)) span.className = 'log-warning';
      span.textContent = line + '\n';
      logEl.appendChild(span);
      logEl.scrollTop = logEl.scrollHeight;

      // Track first error line
      if (!firstErrorLine && /error:/i.test(line)) firstErrorLine = line.trim();

    } else if (msg.type === 'phase') {
      _setPhaseActive(msg.phase, msg.active, msg.pkg || '');

    } else if (msg.type === 'progress') {
      if (hasGlobalProgress) return;
      _setBuildProgress(msg.done, msg.total, msg.pkg || '');

    } else if (msg.type === 'dl_progress') {
      if (hasGlobalProgress) return;
      _setDlProgress(msg.done, msg.expected);

    } else if (msg.type === 'global_progress') {
      hasGlobalProgress = true;
      _setBuildProgress(msg.built_done || 0, msg.built_total || 0, buildPkgEl.textContent || '');
      _setDlProgress(
        msg.dl_done || 0,
        msg.dl_expected || 0,
        msg.copied_done || 0,
        msg.copied_total || 0,
        msg.copied_expected || 0,
        msg.copied_label || '',
      );

    } else if (msg.type === 'done') {
      const label = msg.success
        ? niIcon('check-circle') + ' ' + t('rebuild.success')
        : niIcon('x-circle') + ' ' + t('rebuild.failed') + (firstErrorLine ? ': ' + firstErrorLine.substring(0, 80) : '');
      _finishMonitor(msg.success, label);
      es.close();
      _rebuildES = null;
      if (msg.success) {
        csrfFetch('/api/config/settings').then(r => r.json()).then(cfg => {
          if (cfg.push_after_rebuild) {
            csrfFetch('/api/git/commit-push', {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ label: 'NiCo: Rebuild erfolgreich' }),
            }).then(r => r.json()).then(d => {
              if (d.success) {
                pushInfoEl.textContent = t('rebuild.pushDone');
                pushInfoEl.classList.remove('hidden');
              } else {
                _showGitPushErrorModal(d.message || t('toast.error'), d.error_code);
              }
            }).catch(() => {});
          } else {
            pushBtn.classList.remove('hidden');
            pushBtn.onclick = async () => {
              pushBtn.disabled = true;
              const origText = pushBtn.textContent;
              pushBtn.textContent = '…';
              try {
                const res  = await csrfFetch('/api/git/commit-push', {
                  method: 'POST',
                  headers: { 'Content-Type': 'application/json' },
                  body: JSON.stringify({ label: 'NiCo: Rebuild erfolgreich' }),
                });
                const data = await res.json();
                if (data.success) {
                  pushBtn.classList.add('hidden');
                  pushInfoEl.textContent = t('rebuild.pushDone');
                  pushInfoEl.classList.remove('hidden');
                } else {
                  _showGitPushErrorModal(data.message || t('toast.error'), data.error_code);
                  pushBtn.disabled = false;
                  pushBtn.textContent = origText;
                }
              } catch (e) {
                _showGitPushErrorModal(String(e));
                pushBtn.disabled = false;
                pushBtn.textContent = origText;
              }
            };
          }
        }).catch(() => {});
      }
    } else if (msg.type === 'error') {
      const errText = msg.message || t('rebuild.error');
      const span = document.createElement('span');
      span.className   = 'log-error';
      span.textContent = '\n[!] ' + errText + '\n';
      logEl.appendChild(span);
      logEl.scrollTop = logEl.scrollHeight;
      _finishMonitor(false, niIcon('x-circle') + ' ' + errText);
      es.close();
      _rebuildES = null;
    }
  };

  es.onerror = () => {
    if (isRunning) {
      _finishMonitor(false, niIcon('x-circle') + ' ' + t('rebuild.connectionError'));
    }
    es.close();
    _rebuildES = null;
  };
}

function closeRebuild() {
  if (_rebuildES) { _rebuildES.close(); _rebuildES = null; }
  document.getElementById('rebuild-overlay').classList.add('hidden');
}

// ── Shared output helpers ─────────────────────────────────────────────────────

// Render plain text into colorized HTML spans (safe: escapes HTML before inserting)
function _colorizedOutput(text) {
  return text.split('\n').map(line => {
    const esc = line.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
    if (/error:/i.test(line))   return `<span class="log-error">${esc}</span>`;
    if (/warning:/i.test(line)) return `<span class="log-warning">${esc}</span>`;
    return esc;
  }).join('\n');
}

// ── Dry-Run / Syntax-Prüfung ─────────────────────────────────────────────────

function _dryRunResetStatus() {
  const logo   = document.getElementById('dryrun-logo');
  const status = document.getElementById('dryrun-status');
  if (logo)   { logo.className = 'modal-logo'; }
  if (status) { status.className = 'dryrun-status hidden'; status.textContent = ''; }
}

function _dryRunShowStatus(ok) {
  const logo   = document.getElementById('dryrun-logo');
  const status = document.getElementById('dryrun-status');
  if (logo)   logo.classList.add(ok ? 'logo-ok' : 'logo-failed');
  if (status) {
    status.innerHTML = (ok ? niIcon('check') : niIcon('x-circle')) + ' ' + escHtml(ok ? t('dryrun.success') : t('dryrun.failed'));
    status.className   = 'dryrun-status ' + (ok ? 'status-ok' : 'status-failed');
  }
}

function _dryRunHostHeader(body) {
  if (body.all_hosts) return `Host: ${t('flake.allHosts')}\n\n`;
  if (body.hostname)  return `Host: ${body.hostname}\n\n`;
  return '';
}

async function _buildDryRunBody(hostInfo = null) {
  if (!hostInfo) hostInfo = await _fetchFlakeHosts();
  const base = getFormData();

  if (!hostInfo.flake_mode) return base;

  if (!hostInfo.hosts.length) {
    showToast(t('flake.noHosts'), 'error');
    return null;
  }
  const choice = await _showHostPicker(hostInfo.hosts, {
    allowAll: true,
    title: t('dryrun.title'),
  });
  if (choice === null) return null;

  return {
    ...base,
    all_hosts: choice === '__all__',
    hostname:  choice === '__all__' ? undefined : choice,
  };
}

async function runDryRun() {
  document.getElementById('dryrun-overlay')?.classList.add('hidden');
  const hostInfo = await _fetchFlakeHosts();
  const body     = await _buildDryRunBody(hostInfo);
  if (body === null) return;

  const overlay = document.getElementById('dryrun-overlay');
  const output  = document.getElementById('dryrun-output');
  output.textContent = '…';
  output.style.color = '';
  _dryRunResetStatus();
  overlay.classList.remove('hidden');

  if (!await Sidebar.flakeSave()) return;
  if (!await _autoSave()) return;
  if (!await _writeNix()) return;

  const res = await csrfFetch('/api/dry-run', {
    method:  'POST',
    headers: { 'Content-Type': 'application/json' },
    body:    JSON.stringify(body),
  }).catch(() => null);

  if (!res) { output.textContent = t('dryrun.networkError'); return; }
  const data = await res.json();
  output.style.color = '';
  output.innerHTML = _colorizedOutput(_dryRunHostHeader(body) + (tErr(data.output) || ''));
  _dryRunShowStatus(data.ok);
}

async function runSaveAndDryRun() {
  document.getElementById('dryrun-overlay')?.classList.add('hidden');
  const hostInfo = await _fetchFlakeHosts();
  const body     = await _buildDryRunBody(hostInfo);
  if (body === null) return;

  if (!await Sidebar.flakeSave()) return;
  if (!await _autoSave()) return;
  if (!await _writeNix()) return;

  const overlay = document.getElementById('dryrun-overlay');
  const output  = document.getElementById('dryrun-output');
  output.textContent = '…';
  output.style.color = '';
  _dryRunResetStatus();
  overlay.classList.remove('hidden');

  const res = await csrfFetch('/api/dry-run', {
    method:  'POST',
    headers: { 'Content-Type': 'application/json' },
    body:    JSON.stringify(body),
  }).catch(() => null);

  if (!res) { output.textContent = t('dryrun.networkError'); return; }
  const data = await res.json();
  output.style.color = '';
  output.innerHTML = _colorizedOutput(_dryRunHostHeader(body) + (data.output || ''));
  _dryRunShowStatus(data.ok);
}

// ── Nix GC options toggle ──────────────────────────────────────────────────
function toggleGcOptions(show) {
  document.getElementById('nix-gc-options')
    ?.classList.toggle('hidden', !show);
}

// ── Home Manager visibility toggles ───────────────────────────────────────
function toggleHmDetail(show) {
  document.getElementById('hm-detail')?.classList.toggle('hidden', !show);
}
function toggleHmGitDetail(show) {
  document.getElementById('hm-git-detail')?.classList.toggle('hidden', !show);
}
function toggleHmXdgDetail(show) {
  document.getElementById('hm-xdg-detail')?.classList.toggle('hidden', !show);
}

// ── Hardware / Virtualisierung / Backup visibility toggles ────────────────
function toggleOpenglOptions(show) {
  document.getElementById('opengl-options')?.classList.toggle('hidden', !show);
}
function toggleBootEfiOptions(show) {
  document.getElementById('boot-efi-options')?.classList.toggle('hidden', !show);
}
function updateStateVersionStyle() {
  const el = document.getElementById('state_version');
  if (!el) return;
  el.classList.toggle('field-warn', !el.value.trim());
}
function togglePipewireOptions(show) {
  document.getElementById('pipewire-options')?.classList.toggle('hidden', !show);
}
function toggleVboxGuestOptions(show) {
  document.getElementById('vbox-guest-options')?.classList.toggle('hidden', !show);
}
function toggleDockerOptions(show) {
  document.getElementById('docker-options')?.classList.toggle('hidden', !show);
}
function togglePodmanOptions(show) {
  document.getElementById('podman-options')?.classList.toggle('hidden', !show);
}
function toggleLibvirtdOptions(show) {
  document.getElementById('libvirtd-options')?.classList.toggle('hidden', !show);
}
// ── Snapper-Karten ─────────────────────────────────────────────────────────

const SN_DEFAULTS = { name: '', mountpoint: '', hourly: 5, daily: 7, weekly: 0, monthly: 1, yearly: 0 };

function _snapperCard(idx, cfg) {
  const name   = escHtml(cfg.name       || '');
  const mount  = escHtml(cfg.mountpoint || '');
  const title  = name || t('field.snapperNewConfig');
  return `<div class="extra-user-card" data-sn-idx="${idx}">
    <div class="extra-user-header eu-toggle" data-sn-idx="${idx}">
      <span class="extra-user-label">${title}</span>
      <span class="eu-header-actions">
        ${niIcon('chevron-down').replace('class="', 'class="eu-chevron ')}
        <button type="button" class="sn-remove-btn" data-sn-idx="${idx}"
                title="${escHtml(t('field.snapperRemove'))}">${niIcon('x')}</button>
      </span>
    </div>
    <div class="extra-user-body">
      <label data-i18n="field.snapperConfigName">${escHtml(t('field.snapperConfigName'))}</label>
      <input type="text" class="sn-name" data-sn-idx="${idx}" value="${name}"
             placeholder="root" spellcheck="false" autocomplete="off">

      <label data-i18n="field.snapperMountpoint">${escHtml(t('field.snapperMountpoint'))}</label>
      <input type="text" class="sn-mountpoint" data-sn-idx="${idx}" value="${mount}"
             placeholder="/" spellcheck="false" autocomplete="off">

      <div class="lvl2-section" data-lvl2="sn-timeline-${idx}">
        <div class="lvl2-toggle" data-i18n="field.snapperTimeline">${escHtml(t('field.snapperTimeline'))}</div>
        <div class="lvl2-body">
          <div class="hm-xdg-grid" style="grid-template-columns: repeat(3, 1fr)">
            ${['hourly','daily','weekly','monthly','yearly'].map(f => `
            <div>
              <label data-i18n="field.snapper${f[0].toUpperCase()+f.slice(1)}">${escHtml(t('field.snapper'+f[0].toUpperCase()+f.slice(1)))}</label>
              <input type="number" class="sn-${f}" data-sn-idx="${idx}"
                     min="0" max="99" value="${cfg[f] ?? SN_DEFAULTS[f]}" style="width:70px">
            </div>`).join('')}
          </div>
        </div>
      </div>
    </div>
  </div>`;
}

function renderAllSnapperConfigs(configs) {
  const list = document.getElementById('snapper-list');
  if (!list) return;
  list.innerHTML = configs.map((c, i) => _snapperCard(i, c)).join('');

  list.querySelectorAll('.eu-toggle').forEach(header => {
    header.addEventListener('click', e => {
      if (e.target.closest('.sn-remove-btn')) return;
      const card = header.closest('.extra-user-card');
      const body = card?.querySelector('.extra-user-body');
      if (!body) return;
      const open = body.classList.toggle('open');
      header.classList.toggle('open', open);
    });
  });

  list.querySelectorAll('.sn-name').forEach(inp => {
    inp.addEventListener('input', () => {
      const card  = inp.closest('.extra-user-card');
      const label = card?.querySelector('.extra-user-label');
      if (label) label.textContent = inp.value || t('field.snapperNewConfig');
      schedulePreviewUpdate();
    });
  });

  list.querySelectorAll('.sn-remove-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      const idx     = parseInt(btn.dataset.snIdx, 10);
      const current = getAllSnapperConfigs();
      current.splice(idx, 1);
      renderAllSnapperConfigs(current);
      schedulePreviewUpdate();
    });
  });

  list.querySelectorAll('input').forEach(el => {
    if (!el.classList.contains('sn-name')) {
      el.addEventListener('input',  schedulePreviewUpdate);
      el.addEventListener('change', schedulePreviewUpdate);
    }
  });

  initLvl2Sections(list);
}

function getAllSnapperConfigs() {
  const list = document.getElementById('snapper-list');
  if (!list) return [];
  return [...list.querySelectorAll('.extra-user-card[data-sn-idx]')].map(card => {
    const idx = card.dataset.snIdx;
    return {
      name:       card.querySelector(`.sn-name[data-sn-idx="${idx}"]`)?.value?.trim()       || '',
      mountpoint: card.querySelector(`.sn-mountpoint[data-sn-idx="${idx}"]`)?.value?.trim() || '',
      hourly:     parseInt(card.querySelector(`.sn-hourly[data-sn-idx="${idx}"]`)?.value    || '5', 10),
      daily:      parseInt(card.querySelector(`.sn-daily[data-sn-idx="${idx}"]`)?.value     || '7', 10),
      weekly:     parseInt(card.querySelector(`.sn-weekly[data-sn-idx="${idx}"]`)?.value    || '0', 10),
      monthly:    parseInt(card.querySelector(`.sn-monthly[data-sn-idx="${idx}"]`)?.value   || '1', 10),
      yearly:     parseInt(card.querySelector(`.sn-yearly[data-sn-idx="${idx}"]`)?.value    || '0', 10),
    };
  });
}

// ── Passwort-Toggle ────────────────────────────────────────────────────────
function initPasswordToggle() {
  const cb    = document.getElementById('user_has_password');
  const input = document.getElementById('user_initial_password');
  if (!cb || !input) return;
  cb.addEventListener('change', () => {
    input.classList.toggle('hidden', !cb.checked);
    if (!cb.checked) input.value = '';
    schedulePreviewUpdate();
  });
}

// ── Alles ein-/aufklappen ──────────────────────────────────────────────────
function collapseAll() {
  document.querySelectorAll('section.collapsible').forEach(s => {
    collapsedSections.add(s.dataset.section);
    s.classList.add('collapsed');
  });
  // Also add code-only sections (Home Manager) that have no left-panel element
  BRICK_SECTIONS.forEach(s => collapsedSections.add(s));
  collapsedSections.add('__header__');
  applySectionCollapse();
}

function expandAll() {
  document.querySelectorAll('section.collapsible').forEach(s => {
    collapsedSections.delete(s.dataset.section);
    s.classList.remove('collapsed');
  });
  // Also remove code-only sections
  BRICK_SECTIONS.forEach(s => collapsedSections.delete(s));
  collapsedSections.delete('__header__');
  applySectionCollapse();
  // Auch individuelle Brix aufklappen
  collapsedBrix.clear();
  localStorage.setItem('nico-collapsed-brix', '[]');
  document.querySelectorAll('.code-brix.collapsed').forEach(el => el.classList.remove('collapsed'));
}

// ── UI helpers ─────────────────────────────────────────────────────────────
function showSetupOverlay() {
  document.getElementById('setup-overlay').classList.remove('hidden');
  document.getElementById('app').classList.add('hidden');
  // Reset all setup steps
  document.getElementById('setup-input-section')?.classList.remove('hidden');
  document.getElementById('setup-confirm')?.classList.add('hidden');
  document.getElementById('setup-symlink-step')?.classList.add('hidden');
  _pendingSetupData = null;
  document.getElementById('nixos-dir-input')?.focus();
}

function showApp(configDir) {
  document.getElementById('setup-overlay').classList.add('hidden');
  document.getElementById('app').classList.remove('hidden');
  document.getElementById('config-dir-label').textContent = configDir;
}

function markConfigDirty() {
  document.dispatchEvent(new CustomEvent('nico:config-dirty'));
}

function setField(id, value) {
  const el = document.getElementById(id);
  if (el) el.value = value ?? '';
}

function setCheck(id, value) {
  const el = document.getElementById(id);
  if (el) el.checked = !!value;
}

function getFormData() {
  const v  = id => document.getElementById(id)?.value  ?? '';
  const ch = id => document.getElementById(id)?.checked ?? false;

  // Collect checked font packages from the Schriftarten section
  const fonts = [...document.querySelectorAll('.font-check:checked')]
    .map(cb => cb.value);

  // Home Manager packages: textarea (one per line) → array of non-empty strings
  const hmPackages = (v('hm_packages') || '')
    .split('\n').map(s => s.trim()).filter(Boolean);
  const packages = getPackageListData();

  return {
    hostname:            v('hostname'),
    state_version:       v('state_version'),
    nix_args:            v('nix_args') || 'config, pkgs, lib',
    timezone:            v('timezone'),
    locale:              v('locale'),
    extra_locale: document.getElementById('extra_locale_enable')?.checked
      ? (document.getElementById('extra_locale')?.value || '') : '',
    keyboard_layout:     v('keyboard_layout'),
    keyboard_variant:    '',        // no separate UI field yet
    keyboard_console:    v('keyboard_console'),
    networkmanager:       ch('networkmanager'),
    ssh:                  ch('ssh'),
    firewall_disable:     ch('firewall_disable'),
    firewall_tcp_enable:  ch('firewall_tcp_enable'),
    firewall_tcp_ports:   v('firewall_tcp_ports'),
    firewall_udp_enable:  ch('firewall_udp_enable'),
    firewall_udp_ports:   v('firewall_udp_ports'),
    boot_loader:         v('boot_loader') || 'none',
    boot_efi_can_touch:  ch('boot_efi_can_touch'),
    boot_efi_mount_point: v('boot_efi_mount_point') || '/boot',
    boot_config_limit:   parseInt(v('boot_config_limit') || '5', 10),
    printing:            ch('printing'),
    avahi:               ch('avahi'),
    bluetooth:           ch('bluetooth'),
    blueman:             ch('blueman'),
    libinput:            ch('libinput'),
    fprintd:             ch('fprintd'),
    pcscd:               ch('pcscd'),
    sunshine:            ch('sunshine'),
    pipewire_32bit:      ch('pipewire_32bit'),
    desktop_environment: v('desktop_environment'),
    autologin_user:      v('autologin_user'),
    pipewire:            ch('pipewire'),
    ...(() => {
      const allUsers = getAllUsers();
      const primary  = allUsers[0] || {};
      const groups   = Array.isArray(primary.groups) ? primary.groups : DEFAULT_EXTRA_USER_GROUPS;
      return {
        username:              primary.username         || '',
        user_description:      primary.description      || '',
        user_initial_password: primary.initial_password || '',
        user_uid:              primary.uid              || '',
        user_groups:           groups,
        user_groups_extra:     '',
        user_shell:            primary.shell            || 'bash',
        user_extra_nix:        primary.extra_nix        || '',
        extra_users:           allUsers.slice(1),
      };
    })(),
    guest_user:          ch('guest_user'),
    allowUnfree:         ch('allowUnfree'),
    steam:               ch('steam'),
    appimage:            ch('appimage'),
    firefox:             ch('firefox'),
    firefox_lang_packs:  ch('firefox') ? v('firefox_lang_packs') : '',
    firefox_prefs:       ch('firefox') ? v('firefox_prefs')      : '',
    packages,
    fonts,
    fonts_extra:         v('fonts_extra'),
    flakes:              ch('flakes'),
    nix_optimize_store:  ch('nix_optimize_store'),
    nix_gc:              ch('nix_gc'),
    nix_gc_frequency:    v('nix_gc_frequency'),
    nix_gc_age:          v('nix_gc_age'),

    home_manager: {
      enabled:            ch('hm_enabled'),
      git_enable:         ch('hm_git_enable'),
      git_name:           v('hm_git_name'),
      git_email:          v('hm_git_email'),
      git_default_branch: v('hm_git_default_branch') || 'main',
      shell:              v('hm_shell') || 'bash',
      shell_init_extra:   v('hm_shell_init_extra'),
      packages:           hmPackages,
      firefox:            ch('hm_firefox'),
      xdg_user_dirs:      ch('hm_xdg_user_dirs'),
      xdg_download:       v('hm_xdg_download')    || 'Downloads',
      xdg_documents:      v('hm_xdg_documents')   || 'Documents',
      xdg_pictures:       v('hm_xdg_pictures')    || 'Pictures',
      xdg_music:          v('hm_xdg_music')        || 'Music',
      xdg_videos:         v('hm_xdg_videos')       || 'Videos',
      xdg_desktop:        v('hm_xdg_desktop')      || 'Desktop',
      xdg_templates:      v('hm_xdg_templates')    || 'Templates',
      xdg_publicshare:    v('hm_xdg_publicshare')  || 'Public',
    },

    // Home Manager NixOS-Modul
    hm_use_global_pkgs:      ch('hm_use_global_pkgs'),
    hm_use_user_packages:    ch('hm_use_user_packages'),
    hm_plasma_manager:       ch('hm_shared_modules_enable') ? ch('hm_plasma_manager') : false,
    hm_shared_modules_extra: ch('hm_shared_modules_enable') ? v('hm_shared_modules_extra') : '',

    // Hardware
    enable_all_firmware: ch('enable_all_firmware'),
    cpu_microcode:       v('cpu_microcode') || 'none',
    opengl:              ch('opengl'),
    opengl_32bit:        ch('opengl_32bit'),
    zram_swap:           ch('zram_swap'),

    openrgb:             ch('openrgb'),
    ledger:              ch('ledger'),
    ratbagd:             ch('ratbagd'),

    // Virtualisierung
    docker:               ch('docker'),
    docker_rootless:      ch('docker_rootless'),
    podman:               ch('podman'),
    podman_docker_compat: ch('podman_docker_compat'),
    virtualbox_host:          ch('virtualbox_host'),
    virtualbox_guest:         ch('virtualbox_guest'),
    virtualbox_guest_drag_drop: ch('virtualbox_guest_drag_drop'),
    libvirtd:             ch('libvirtd'),
    virt_manager:         ch('virt_manager'),

    // Dateisystem & Backup
    btrfs_scrub:     ch('btrfs_scrub'),
    snapper_enable:  ch('snapper_enable'),
    snapper_configs: getAllSnapperConfigs(),

    // Multi-host context: tells the preview endpoint which host is active
    _host: _activeHost,
    _co_path: _coLoadedPath,
    _co_ready: _coFormReady,
  };
}

// ── Multi-Host Support ────────────────────────────────────────────────────────


async function switchHost(hostName) {
  if (hostName === _activeHost) return;

  if (_formDirty || _flakeFormDirty) {
    const ok = await confirmHostSwitch();
    if (!ok) return;
  }

  _activeHost = hostName;

  if (hostName === '') {
    _brixTargetFile  = 'configuration.nix';
    _brixContextFile = 'configuration.nix';
    await _populateCoFormFromFile('configuration.nix');
    await updatePreview();
  } else {
    await loadHostConfig(hostName);
  }
}

async function confirmHostSwitch() {
  return new Promise(resolve => {
    const overlay = document.createElement('div');
    overlay.className = 'overlay';
    overlay.innerHTML = `
      <div class="dialog">
        <h2 class="dialog-title">${t('host.switchTitle')}</h2>
        <p>${t('host.switchInfo')}</p>
        <div class="dialog-actions">
          <button id="host-switch-save"     class="btn-primary">${t('host.switchSave')}</button>
          <button id="host-switch-continue" class="btn-secondary">${t('host.switchContinue')}</button>
          <button id="host-switch-cancel"   class="btn-danger">${t('host.switchCancel')}</button>
        </div>
      </div>`;
    document.body.appendChild(overlay);

    overlay.querySelector('#host-switch-save').addEventListener('click', async () => {
      document.body.removeChild(overlay);
      const ok = await saveAndWrite();
      resolve(!!ok);
    });
    overlay.querySelector('#host-switch-continue').addEventListener('click', () => {
      document.body.removeChild(overlay);
      resolve(true);
    });
    overlay.querySelector('#host-switch-cancel').addEventListener('click', () => {
      document.body.removeChild(overlay);
      resolve(false);
    });
  });
}

async function confirmClosePushPrompt() {
  return new Promise(resolve => {
    const overlay = document.createElement('div');
    overlay.className = 'overlay';
    overlay.innerHTML = `
      <div class="dialog">
        <h2 class="dialog-title">${escHtml(t('git.closePrompt.title'))}</h2>
        <p>${escHtml(t('git.closePrompt.body'))}</p>
        <div class="dialog-actions">
          <button id="close-push-confirm" class="btn-primary">${escHtml(t('git.closePrompt.yes'))}</button>
          <button id="close-push-skip" class="btn-secondary">${escHtml(t('git.closePrompt.no'))}</button>
        </div>
      </div>`;
    document.body.appendChild(overlay);

    const cleanup = (result) => {
      if (overlay.parentNode) document.body.removeChild(overlay);
      resolve(result);
    };

    overlay.querySelector('#close-push-confirm')?.addEventListener('click', () => cleanup(true));
    overlay.querySelector('#close-push-skip')?.addEventListener('click', () => cleanup(false));
    overlay.addEventListener('click', e => {
      if (e.target === overlay) cleanup(false);
    });
  });
}

async function loadHostConfig(hostName) {
  await _populateCoFormFromFile(`hosts/${hostName}/default.nix`);
  await updatePreview();
}

function populateFormFromData(data) {
  const v  = (id, val) => { const el = document.getElementById(id); if (el) el.value = val ?? ''; };
  const ch = (id, val) => { const el = document.getElementById(id); if (el) el.checked = !!val; };

  if ('hostname'           in data) v('hostname',            data.hostname);
  if ('state_version'      in data) { v('state_version', data.state_version); updateStateVersionStyle(); }
  if ('nix_args'           in data) v('nix_args',            data.nix_args || 'config, pkgs, lib');
  if ('timezone'           in data) v('timezone',            data.timezone);
  if ('locale'             in data) v('locale',              data.locale);
  if ('keyboard_layout'    in data) v('keyboard_layout',     data.keyboard_layout);
  if ('keyboard_console'   in data) v('keyboard_console',    data.keyboard_console);
  if ('desktop_environment' in data) v('desktop_environment', data.desktop_environment);
  if ('autologin_user'     in data) v('autologin_user',      data.autologin_user);
  if ('username'           in data) v('username',            data.username);
  if ('user_description'   in data) v('user_description',    data.user_description);
  if ('user_uid'           in data) v('user_uid',            data.user_uid);
  if ('user_groups_extra'  in data) v('user_groups_extra',   data.user_groups_extra);
  if ('user_shell'         in data) v('user_shell',          data.user_shell || 'bash');
  if ('user_extra_nix'     in data) v('user_extra_nix',      data.user_extra_nix);
  if ('nix_gc_frequency'   in data) v('nix_gc_frequency',    data.nix_gc_frequency);
  if ('nix_gc_age'         in data) v('nix_gc_age',          data.nix_gc_age);
  if ('fonts_extra'        in data) v('fonts_extra',         data.fonts_extra);
  if ('firewall_tcp_ports' in data) v('firewall_tcp_ports',  data.firewall_tcp_ports);
  if ('firewall_udp_ports' in data) v('firewall_udp_ports',  data.firewall_udp_ports);
  if ('boot_loader'        in data) v('boot_loader',         data.boot_loader || 'none');
  if ('boot_efi_mount_point' in data) v('boot_efi_mount_point', data.boot_efi_mount_point || '/boot');
  if ('boot_config_limit'  in data) v('boot_config_limit',  data.boot_config_limit ?? 5);
  if ('cpu_microcode'      in data) v('cpu_microcode',       data.cpu_microcode || 'none');

  if ('guest_user'         in data) ch('guest_user',          data.guest_user);
  if ('networkmanager'      in data) ch('networkmanager',       data.networkmanager);
  if ('ssh'                 in data) ch('ssh',                  data.ssh);
  if ('firewall_disable'    in data) ch('firewall_disable',     data.firewall_disable);
  if ('firewall_tcp_enable' in data) {
    ch('firewall_tcp_enable', data.firewall_tcp_enable);
    document.getElementById('firewall-tcp-detail')?.classList.toggle('hidden', !data.firewall_tcp_enable);
  }
  if ('firewall_udp_enable' in data) {
    ch('firewall_udp_enable', data.firewall_udp_enable);
    document.getElementById('firewall-udp-detail')?.classList.toggle('hidden', !data.firewall_udp_enable);
  }
  if ('boot_efi_can_touch' in data) ch('boot_efi_can_touch',  data.boot_efi_can_touch);
  if ('printing'           in data) ch('printing',            data.printing);
  if ('avahi'              in data) ch('avahi',               data.avahi);
  if ('bluetooth'          in data) ch('bluetooth',           data.bluetooth);
  if ('blueman'            in data) ch('blueman',             data.blueman);
  if ('libinput'           in data) ch('libinput',            data.libinput);
  if ('fprintd'            in data) ch('fprintd',             data.fprintd);
  if ('pcscd'              in data) ch('pcscd',               data.pcscd);
  if ('sunshine'           in data) ch('sunshine',            data.sunshine);
  if ('pipewire'           in data) ch('pipewire',            data.pipewire);
  if ('pipewire_32bit'     in data) ch('pipewire_32bit',      data.pipewire_32bit);
  if ('allowUnfree'        in data) ch('allowUnfree',         data.allowUnfree);
  if ('steam'              in data) ch('steam',               data.steam);
  if ('appimage'           in data) ch('appimage',            data.appimage);
  if ('firefox'            in data) ch('firefox',             data.firefox);
  if ('flakes'             in data) ch('flakes',              data.flakes);
  if ('nix_optimize_store' in data) ch('nix_optimize_store',  data.nix_optimize_store);
  if ('nix_gc'             in data) ch('nix_gc',              data.nix_gc);
  if ('enable_all_firmware' in data) ch('enable_all_firmware', data.enable_all_firmware);
  if ('opengl'             in data) ch('opengl',              data.opengl);
  if ('opengl_32bit'       in data) ch('opengl_32bit',        data.opengl_32bit);
  if ('zram_swap'          in data) ch('zram_swap',           data.zram_swap);
  if ('openrgb'            in data) ch('openrgb',             data.openrgb);
  if ('ledger'             in data) ch('ledger',              data.ledger);
  if ('ratbagd'            in data) ch('ratbagd',             data.ratbagd);
  if ('docker'             in data) ch('docker',              data.docker);
  if ('docker_rootless'    in data) ch('docker_rootless',     data.docker_rootless);
  if ('podman'             in data) ch('podman',              data.podman);
  if ('podman_docker_compat' in data) ch('podman_docker_compat', data.podman_docker_compat);
  if ('virtualbox_host'    in data) ch('virtualbox_host',     data.virtualbox_host);
  if ('virtualbox_guest'   in data) ch('virtualbox_guest',    data.virtualbox_guest);
  if ('virtualbox_guest_drag_drop' in data) ch('virtualbox_guest_drag_drop', data.virtualbox_guest_drag_drop);
  if ('libvirtd'           in data) ch('libvirtd',            data.libvirtd);
  if ('virt_manager'       in data) ch('virt_manager',        data.virt_manager);
  if ('btrfs_scrub'     in data) ch('btrfs_scrub',    data.btrfs_scrub);
  if ('snapper_enable'  in data) {
    ch('snapper_enable', data.snapper_enable);
    document.getElementById('snapper-area')?.classList.toggle('hidden', !data.snapper_enable);
    renderAllSnapperConfigs(data.snapper_configs || []);
  }

  if ('user_groups' in data) {
    const selectedGroups = new Set(data.user_groups || []);
    document.querySelectorAll('.user-group-check').forEach(cb => {
      cb.checked = selectedGroups.has(cb.value);
    });
  }
  if ('username' in data || 'extra_users' in data) {
    const primary = {
      username:         data.username || '',
      description:      data.user_description || '',
      initial_password: data.user_initial_password || '',
      uid:              data.user_uid || '',
      groups:           data.user_groups || DEFAULT_EXTRA_USER_GROUPS,
      shell:            data.user_shell || 'bash',
      extra_nix:        data.user_extra_nix || '',
    };
    renderAllUsers([primary, ...(data.extra_users || [])]);
  }
  if ('fonts' in data) {
    const selectedFonts = new Set(data.fonts || []);
    document.querySelectorAll('.font-check').forEach(cb => {
      cb.checked = selectedFonts.has(cb.value);
    });
  }
  if ('packages' in data) renderPackageList(data.packages || []);

  document.getElementById('extra-locale-detail')?.classList.toggle('hidden', !document.getElementById('extra_locale_enable')?.checked);
  document.getElementById('firewall-tcp-detail')?.classList.toggle('hidden', !document.getElementById('firewall_tcp_enable')?.checked);
  document.getElementById('firewall-udp-detail')?.classList.toggle('hidden', !document.getElementById('firewall_udp_enable')?.checked);
  document.getElementById('firefox-detail')?.classList.toggle('hidden', !document.getElementById('firefox')?.checked);
  toggleBootEfiOptions((document.getElementById('boot_loader')?.value || 'none') !== 'none');
  togglePipewireOptions(!!document.getElementById('pipewire')?.checked);
  toggleGcOptions(!!document.getElementById('nix_gc')?.checked);
  toggleOpenglOptions(!!document.getElementById('opengl')?.checked);
  toggleDockerOptions(!!document.getElementById('docker')?.checked);
  togglePodmanOptions(!!document.getElementById('podman')?.checked);
  toggleLibvirtdOptions(!!document.getElementById('libvirtd')?.checked);
  toggleVboxGuestOptions(!!document.getElementById('virtualbox_guest')?.checked);
  updateSectionVisibility();
}

/** Setzt alle CO-Formularfelder auf leer/unchecked zurück. */
function clearCoForm() {
  const form = document.getElementById('config-form');
  if (!form) return;
  form.querySelectorAll('input[type="text"], textarea, select').forEach(el => { el.value = ''; });
  form.querySelectorAll('input[type="checkbox"]').forEach(el => { el.checked = false; });
  renderAllUsers([{
    username: '',
    description: '',
    initial_password: '',
    uid: '',
    groups: [...DEFAULT_EXTRA_USER_GROUPS],
    shell: 'bash',
    extra_nix: '',
  }]);
  renderPackageList([]);
  document.querySelectorAll('.font-check').forEach(cb => { cb.checked = false; });
  setField('boot_loader', 'none');
  setField('cpu_microcode', 'none');
  setField('boot_efi_mount_point', '/boot');
  setField('boot_config_limit', 5);
  setField('nix_gc_frequency', 'weekly');
  setField('nix_gc_age', '30d');
  renderAllSnapperConfigs([]);
  setField('hm_git_default_branch', 'main');
  setField('hm_shell', 'bash');
  setField('hm_xdg_download', 'Downloads');
  setField('hm_xdg_documents', 'Documents');
  setField('hm_xdg_pictures', 'Pictures');
  setField('hm_xdg_music', 'Music');
  setField('hm_xdg_videos', 'Videos');
  setField('hm_xdg_desktop', 'Desktop');
  setField('hm_xdg_templates', 'Templates');
  setField('hm_xdg_publicshare', 'Public');
  document.getElementById('extra-locale-detail')?.classList.add('hidden');
  document.getElementById('firewall-tcp-detail')?.classList.add('hidden');
  document.getElementById('firewall-udp-detail')?.classList.add('hidden');
  document.getElementById('firefox-detail')?.classList.add('hidden');
  document.getElementById('hm-shared-modules-detail')?.classList.add('hidden');
  toggleHmDetail(false);
  toggleHmGitDetail(false);
  toggleHmXdgDetail(false);
  toggleBootEfiOptions(false);
  togglePipewireOptions(false);
  toggleGcOptions(false);
  toggleOpenglOptions(false);
  toggleDockerOptions(false);
  togglePodmanOptions(false);
  toggleLibvirtdOptions(false);
  toggleVboxGuestOptions(false);
  document.getElementById('snapper-area')?.classList.add('hidden');
  updateSectionVisibility();
}

/**
 * Öffnet eine CO-Datei, parst ihren Inhalt und befüllt das Formular
 * ausschließlich mit den gefundenen Werten. Nicht vorhandene Felder
 * bleiben leer – nico.json-Werte werden nicht eingemischt.
 */
async function _populateCoFormFromFile(path, _content) {
  _coFormReady = false;
  _coLoadedPath = path;
  clearCoForm();
  let loaded = false;
  try {
    const res  = await fetch(`/api/parse/co?path=${encodeURIComponent(path)}`);
    const data = await res.json();
    if (!data.error) {
      populateFormFromData(data);
      loaded = true;
    }
  } catch { /* non-fatal: leeres Formular ist besser als falsche Werte */ }
  _coFormReady = loaded;
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

  // Home Manager visibility
  on('hm_enabled',       'change', e => { toggleHmDetail(e.target.checked); schedulePreviewUpdate(); });
  on('hm_git_enable',    'change', e => { toggleHmGitDetail(e.target.checked); schedulePreviewUpdate(); });
  on('hm_xdg_user_dirs', 'change', e => { toggleHmXdgDetail(e.target.checked); schedulePreviewUpdate(); });

  // Hardware / Virtualisierung / Backup sub-option visibility
  on('opengl',       'change', e => toggleOpenglOptions(e.target.checked));
  on('boot_loader', 'change', e => { toggleBootEfiOptions(e.target.value !== 'none'); schedulePreviewUpdate(); });
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
  document.getElementById('hm_shared_modules_enable')?.addEventListener('change', function() {
    document.getElementById('hm-shared-modules-detail')?.classList.toggle('hidden', !this.checked);
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

  // NixOS action dropdown
  (function initNixosMenu() {
    const menu  = document.getElementById('nixos-menu');
    const btn   = document.getElementById('nixos-btn');
    const drop  = document.getElementById('nixos-dropdown');
    if (!menu || !btn || !drop) return;

    function toggle(e) {
      e.stopPropagation();
      drop.classList.toggle('hidden');
    }
    function close() { drop.classList.add('hidden'); }

    btn.addEventListener('click', toggle);
    document.addEventListener('click', e => { if (!menu.contains(e.target)) close(); });

    on('nixos-save-btn',     'click', () => { close(); openWriteConfirm(); });
    on('nixos-validate-btn', 'click', () => { close(); runValidation(); });
    on('nixos-dryrun-btn',   'click', () => { close(); runSaveAndDryRun(); });
    on('nixos-rebuild-btn',  'click', () => { close(); openRebuild('switch'); });
    on('nixos-git-push-btn', 'click', () => { close(); gitPushManual(); });
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

// ══════════════════════════════════════════════════════════════════
// SIDEBAR & AKTIVE DATEI
// ══════════════════════════════════════════════════════════════════

const Sidebar = (() => {

  // State
  let activeFile  = null;   // {path, name} oder null
  let _formDirty      = false;  // config-form hat ungespeicherte Änderungen
  let _flakeFormDirty = false;  // flake-form hat ungespeicherte Änderungen

  // DOM refs (populated after DOMContentLoaded)
  let elSidebar, elToggleBtn, elTree, elPreviewPanel, elSaveAllBtn, elRefreshBtn;

  // ── Find-Bar state ─────────────────────────────────────────────
  let findMatches  = [];
  let findIndex    = 0;
  let elFindBar, elFindInput, elFindCount;

  function initFindBar() {
    elFindBar   = document.getElementById('find-bar');
    elFindInput = document.getElementById('find-input');
    elFindCount = document.getElementById('find-count');

    document.getElementById('find-close-btn').addEventListener('click', closeFindBar);
    document.getElementById('find-prev-btn').addEventListener('click', () => findStep(-1));
    document.getElementById('find-next-btn').addEventListener('click', () => findStep(1));
    elFindInput.addEventListener('input', runFind);
    elFindInput.addEventListener('keydown', e => {
      if (e.key === 'Enter') { e.preventDefault(); findStep(e.shiftKey ? -1 : 1); }
      if (e.key === 'Escape') closeFindBar();
    });
  }

  function openFindBar() {
    if (!activeFile) return;
    elFindBar.classList.remove('hidden');
    elFindInput.focus();
    elFindInput.select();
    runFind();
  }

  function closeFindBar() {
    elFindBar.classList.add('hidden');
    elFindInput.value = '';
    findMatches = [];
    elFindCount.textContent = '';
  }

  function runFind() {
    const needle = elFindInput.value;
    const ta = _activeTextarea();
    if (!ta || !needle) {
      findMatches = [];
      elFindCount.textContent = needle ? '0 / 0' : '';
      return;
    }
    const text  = ta.value;
    const lower = text.toLowerCase();
    const q     = needle.toLowerCase();
    findMatches = [];
    let pos = 0;
    while ((pos = lower.indexOf(q, pos)) !== -1) {
      findMatches.push(pos);
      pos += q.length;
    }
    findIndex = findMatches.length ? 0 : -1;
    elFindCount.textContent = findMatches.length
      ? `${findIndex + 1} / ${findMatches.length}`
      : '0 Treffer';
    if (findMatches.length) _highlightMatch(ta, findMatches[0], needle.length);
  }

  function findStep(dir) {
    if (!findMatches.length) return;
    findIndex = (findIndex + dir + findMatches.length) % findMatches.length;
    elFindCount.textContent = `${findIndex + 1} / ${findMatches.length}`;
    _highlightMatch(_activeTextarea(), findMatches[findIndex], elFindInput.value.length);
  }

  function _highlightMatch(ta, start, len) {
    if (!ta) return;
    ta.focus();
    ta.setSelectionRange(start, start + len);
    const linesBefore = ta.value.substring(0, start).split('\n').length - 1;
    const lineHeight  = parseInt(getComputedStyle(ta).lineHeight) || 22;
    ta.scrollTop = Math.max(0, linesBefore * lineHeight - ta.clientHeight / 2);
  }

  function _activeTextarea() {
    // Find-Bar only makes sense in the raw-view; return null if none active
    const rawView = document.getElementById('raw-file-view');
    if (!rawView) return null;
    return rawView.querySelector('textarea') || null;
  }

  function init() {
    elSidebar      = document.getElementById('sidebar');
    elToggleBtn    = document.getElementById('sidebar-toggle-btn');
    elTree         = document.getElementById('sidebar-tree');
    elPreviewPanel = document.getElementById('preview-panel');
    elSaveAllBtn   = document.getElementById('save-all-btn');
    elRefreshBtn   = document.getElementById('sidebar-refresh-btn');

    if (!elSidebar) return; // not on this page

    elToggleBtn.addEventListener('click', toggleSidebar);
    elRefreshBtn.addEventListener('click', () => loadTree());
    document.getElementById('sidebar-expand-all-btn')?.addEventListener('click', () => expandCollapseAll(true));
    document.getElementById('sidebar-collapse-all-btn')?.addEventListener('click', () => expandCollapseAll(false));

    // Sidebar standardmäßig geöffnet
    elToggleBtn.classList.add('active');
    loadTree();

    // Track form dirty state
    document.getElementById('config-form')?.addEventListener('input', () => { _formDirty = true; });
    document.addEventListener('nico:config-dirty', () => { _formDirty = true; });
    document.addEventListener('nico:config-saved', () => { _formDirty = false; });

    initFindBar();

    // Tools-Dropdown
    const toolsBtn      = document.getElementById('tools-btn');
    const toolsDropdown = document.getElementById('tools-dropdown');
    if (toolsBtn && toolsDropdown) {
      toolsBtn.addEventListener('click', e => {
        e.stopPropagation();
        toolsDropdown.classList.toggle('hidden');
      });
      document.addEventListener('click', () => toolsDropdown.classList.add('hidden'));
    }

    document.getElementById('tool-reload')?.addEventListener('click', () => {
      toolsDropdown?.classList.add('hidden');
      _reloadFile();
    });

    document.getElementById('tool-code-search')?.addEventListener('click', () => {
      toolsDropdown?.classList.add('hidden');
      openFindBar();
    });

    document.getElementById('tool-set-type')?.addEventListener('click', () => {
      toolsDropdown?.classList.add('hidden');
      openSetType();
    });

    document.getElementById('tool-brix-insert')?.addEventListener('click', () => {
      toolsDropdown?.classList.add('hidden');
      openBrixInsert();
    });
  }

  // ── Dateityp-Dialog ──────────────────────────────────────────────

  const _FILE_TYPES = [
    { code: 'co', label: 'co',  i18n: 'setType.co' },
    { code: 'nd', label: 'nd',  i18n: 'setType.nd' },
    { code: 'fl', label: 'fl',  i18n: 'setType.fl' },
    { code: 'mo', label: 'mo',  i18n: 'setType.mo' },
    { code: 'hm', label: 'hm',  i18n: 'setType.hm' },
    { code: 'hw', label: 'hw',  i18n: 'setType.hw' },
  ];

  function openSetType() {
    if (!activeFile) return;
    const overlay = document.getElementById('set-type-overlay');
    const grid    = document.getElementById('set-type-grid');
    if (!overlay || !grid) return;

    const currentType = document.querySelector(`.tree-file[data-path="${CSS.escape(activeFile.path)}"]`)
                          ?.dataset.fileType || null;

    grid.innerHTML = '';
    _FILE_TYPES.forEach(({ code, i18n }) => {
      const btn = document.createElement('button');
      btn.className = `set-type-btn ft-${code}${code === currentType ? ' active' : ''}`;
      btn.dataset.code = code;
      btn.innerHTML = `<span class="set-type-code">${code}</span><span class="set-type-label" data-i18n="${i18n}">${t(i18n)}</span>`;
      btn.addEventListener('click', () => _applyFileType(code, overlay));
      grid.appendChild(btn);
    });

    overlay.classList.remove('hidden');
    document.getElementById('set-type-cancel')?.addEventListener('click', () => {
      overlay.classList.add('hidden');
    }, { once: true });
  }

  async function _applyFileType(ftype, overlay) {
    if (!activeFile) return;

    // 1. Typ auf dem Server setzen
    let data;
    try {
      const res = await csrfFetch('/api/file/set-type', {
        method:  'POST',
        headers: { 'Content-Type': 'application/json' },
        body:    JSON.stringify({ path: activeFile.path, file_type: ftype }),
      });
      data = await res.json();
    } catch (e) {
      showToast(tErr('ERR_FILE_WRITE'), 'error');
      return;
    }
    if (data.error) { showToast(tErr(data.error), 'error'); return; }

    // 2. Erfolg: Dialog schließen, Toast zeigen, Sidebar aktualisieren
    overlay.classList.add('hidden');
    showToast(t('setType.saved'), 'success');
    const el = document.querySelector(`.tree-file[data-path="${CSS.escape(activeFile.path)}"]`);
    if (el) {
      el.dataset.fileType = ftype;
      el.className = `tree-file active ft-${ftype}`;
    }

    // 3. Datei neu laden → Panel umschalten + Inhalt zeigen.
    //    Für nicht-CO-Typen: Panel sofort umschalten (vor dem Netzwerk-Round-Trip).
    //    Für CO: _loadFileIntoView zeigt das CO-Formular nach dem Laden.
    if (ftype !== 'co') _showLeftPanel(ftype, activeFile.name);
    try {
      await _loadFileIntoView(activeFile.path, { skipTypeDialog: true });
    } catch (e) { /* non-fatal */ }
  }

  // ── Sidebar toggle ───────────────────────────────────────────────

  function toggleSidebar() {
    const isHidden = elSidebar.classList.toggle('hidden');
    elToggleBtn.classList.toggle('active', !isHidden);
    if (!isHidden) {
      loadTree();
    }
  }

  // ── Tree loading ─────────────────────────────────────────────────

  async function loadTree() {
    elTree.innerHTML = '';
    _closeTreeContextMenu();
    try {
      const res  = await fetch('/api/files');
      const data = await res.json();
      if (data.error) {
        elTree.innerHTML = `<div class="sidebar-empty">${tErr(data.error)}</div>`;
        return;
      }
      if (!data.tree || data.tree.length === 0) {
        elTree.innerHTML = `<div class="sidebar-empty">${t('sidebar.empty')}</div>`;
        return;
      }
      const rootName = (data.root || '').split('/').filter(Boolean).pop() || t('sidebar.rootLabel');
      renderTree([{
        name: rootName,
        type: 'dir',
        path: '',
        is_root: true,
        open: true,
        children: data.tree,
      }], elTree);
    } catch (e) {
      elTree.innerHTML = `<div class="sidebar-empty">${t('sidebar.loadError')}</div>`;
    }
  }

  function expandCollapseAll(open) {
    elTree.querySelectorAll('.tree-children').forEach(ch => {
      ch.style.display = open ? 'block' : 'none';
    });
    elTree.querySelectorAll('.tree-dir-toggle').forEach(t => {
      t.classList.toggle('open', open);
    });
  }

  function _closeTreeContextMenu() {
    if (_treeContextMenu) {
      if (_treeContextMenu._triggerBtn) {
        _treeContextMenu._triggerBtn.setAttribute('aria-expanded', 'false');
      }
      _treeContextMenu.remove();
      _treeContextMenu = null;
    }
  }

  function _openTreeContextMenu(triggerBtn, buildItems) {
    if (_treeContextMenu?._triggerBtn === triggerBtn) {
      _closeTreeContextMenu();
      return;
    }
    _closeTreeContextMenu();

    const menu = document.createElement('div');
    menu.className = 'tree-context-menu';
    menu.addEventListener('click', e => e.stopPropagation());
    buildItems(menu);
    const rect = triggerBtn.getBoundingClientRect();
    menu.style.top = `${Math.min(rect.bottom + 4, window.innerHeight - 12)}px`;
    menu.style.left = `${Math.max(12, rect.right - 220)}px`;
    menu._triggerBtn = triggerBtn;
    triggerBtn.setAttribute('aria-expanded', 'true');
    document.body.appendChild(menu);
    _treeContextMenu = menu;
  }

  function _appendTreeMenuItem(menu, label, onClick) {
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'tree-menu-item';
    btn.textContent = label;
    btn.addEventListener('click', async e => {
      e.stopPropagation();
      _closeTreeContextMenu();
      await onClick();
    });
    menu.appendChild(btn);
  }

  async function _pickHardwareConfigForDir(targetDir, targetLabel = '') {
    let data;
    try {
      const res = await fetch('/api/files/hardware-configs');
      data = await res.json();
    } catch (e) {
      showToast(t('sidebar.loadError'), 'error');
      return;
    }

    if (data.error) {
      showToast(tErr(data.error), 'error');
      return;
    }

    const configs = Array.isArray(data.configs)
      ? data.configs.filter(item => !item.inside_config)
      : [];

    const selected = await new Promise(resolve => {
      const overlay = document.createElement('div');
      overlay.className = 'overlay';
      const renderOptions = () => {
        const filtered = configs.filter(item => !item.inside_config);
        return filtered.length
          ? filtered.map(cfg => `<option value="${escHtml(cfg.path)}">${escHtml(cfg.label)}</option>`).join('')
          : `<option value="">${escHtml(t('sidebar.hwImportEmptyList'))}</option>`;
      };
      overlay.innerHTML = `
        <div class="dialog tree-import-dialog">
          <h2 class="dialog-title">${escHtml(t('sidebar.hwImportTitle'))}</h2>
          <p class="dialog-info">${escHtml(t('sidebar.hwImportInfo').replace('{dir}', targetLabel || t('sidebar.rootLabel')))}</p>
          <div class="tree-import-manual">
            <input id="tree-hw-import-path" type="text" value="" placeholder="${escHtml(t('sidebar.hwImportPathPlaceholder'))}" spellcheck="false">
            <button id="tree-hw-import-browse" type="button" class="btn-secondary">${t('sidebar.hwImportBrowse')}</button>
          </div>
          <div id="tree-hw-import-status" class="tree-import-status hidden"></div>
          <select id="tree-hw-import-select" class="brix-select">
            ${renderOptions()}
          </select>
          <div class="dialog-actions">
            <button id="tree-hw-import-ok" class="btn-primary">${t('sidebar.hwImportAction')}</button>
            <button id="tree-hw-import-cancel" class="btn-secondary">${t('unsaved.cancel')}</button>
          </div>
        </div>`;
      document.body.appendChild(overlay);
      const selectEl = overlay.querySelector('#tree-hw-import-select');
      const inputEl = overlay.querySelector('#tree-hw-import-path');
      const statusEl = overlay.querySelector('#tree-hw-import-status');
      const setStatus = (message, isError = false) => {
        if (!statusEl) return;
        statusEl.textContent = message;
        statusEl.classList.remove('hidden', 'error');
        if (isError) statusEl.classList.add('error');
      };
      const refreshOptions = () => {
        const filtered = configs.filter(item => !item.inside_config);
        if (!selectEl) return;
        selectEl.innerHTML = filtered.length
          ? filtered.map(cfg => `<option value="${escHtml(cfg.path)}">${escHtml(cfg.label)}</option>`).join('')
          : `<option value="">${escHtml(t('sidebar.hwImportEmptyList'))}</option>`;
        selectEl.disabled = !filtered.length;
      };

      overlay.querySelector('#tree-hw-import-browse')?.addEventListener('click', async () => {
        const raw = inputEl?.value?.trim() || '';
        if (!raw) {
          setStatus(tErr('ERR_NO_PATH'), true);
          return;
        }
        try {
          const res = await csrfFetch('/api/files/hardware-configs/check', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ path: raw }),
          });
          const result = await res.json();
          if (result.error) {
            setStatus(tErr(result.error), true);
            return;
          }
          const cfg = result.config;
          if (!cfg || cfg.inside_config) {
            setStatus(t('sidebar.hwImportAlreadyListed'), true);
            return;
          }
          if (!configs.some(item => item.path === cfg.path)) {
            configs.unshift(cfg);
          }
          refreshOptions();
          selectEl.value = cfg.path;
          setStatus(t('sidebar.hwImportAdded'));
        } catch (e) {
          setStatus(t('toast.error'), true);
        }
      });
      overlay.querySelector('#tree-hw-import-ok')?.addEventListener('click', () => {
        const value = selectEl?.value || '';
        document.body.removeChild(overlay);
        resolve(value || null);
      });
      overlay.querySelector('#tree-hw-import-cancel')?.addEventListener('click', () => {
        document.body.removeChild(overlay);
        resolve(null);
      });
    });

    if (!selected) return;

    try {
      const res = await csrfFetch('/api/files/import-hardware', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          target_dir: targetDir,
          source_path: selected,
        }),
      });
      const result = await res.json();
      if (result.error) {
        showToast(tErr(result.error), 'error');
        return;
      }
      showToast(
        result.backup_created ? t('sidebar.hwImportSuccessBackup') : t('sidebar.hwImportSuccess'),
        'success',
      );
      await loadTree();
      if (result.target_path) {
        const fileName = result.target_path.split('/').pop();
        await selectFile(result.target_path, fileName, { force: true });
      }
    } catch (e) {
      showToast(t('toast.error'), 'error');
    }
  }

  async function _createTreeEntry(parentPath, type) {
    const label = type === 'dir' ? t('sidebar.newDirPrompt') : t('sidebar.newFilePrompt');
    const value = window.prompt(label, type === 'dir' ? '' : 'default.nix');
    if (!value) return;

    try {
      const res = await csrfFetch('/api/files/create', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          parent_path: parentPath,
          name: value.trim(),
          type,
        }),
      });
      const data = await res.json();
      if (data.error) {
        showToast(tErr(data.error), 'error');
        return;
      }
      await loadTree();
      if (data.type === 'file' && data.path) {
        const fileName = data.path.split('/').pop();
        await selectFile(data.path, fileName, { force: true });
      }
    } catch (e) {
      showToast(t('toast.error'), 'error');
    }
  }

  async function _renameTreeEntry(entry) {
    const label = window.prompt(t('sidebar.renamePrompt'), entry.name);
    if (!label || label.trim() === entry.name) return;

    try {
      const res = await csrfFetch('/api/files/rename', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          path: entry.path,
          new_name: label.trim(),
        }),
      });
      const data = await res.json();
      if (data.error) {
        showToast(tErr(data.error), 'error');
        return;
      }
      await loadTree();
      if (activeFile?.path === entry.path && data.new_path) {
        const fileName = data.new_path.split('/').pop();
        await selectFile(data.new_path, fileName, { force: true });
      }
    } catch (e) {
      showToast(t('toast.error'), 'error');
    }
  }

  async function _deleteTreeEntry(entry) {
    if (!confirm(t('sidebar.deleteConfirm').replace('{name}', entry.name))) return;

    try {
      const res = await csrfFetch('/api/files/delete', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ path: entry.path }),
      });
      const data = await res.json();
      if (data.error) {
        showToast(tErr(data.error), 'error');
        return;
      }
      if (activeFile?.path === entry.path || activeFile?.path?.startsWith(`${entry.path}/`)) {
        activeFile = null;
        _updateActiveFileLabel();
      }
      await loadTree();
    } catch (e) {
      showToast(t('toast.error'), 'error');
    }
  }

  function _openEntryMenu(entry, triggerBtn) {
    _openTreeContextMenu(triggerBtn, menu => {
      if (entry.type === 'dir') {
        _appendTreeMenuItem(menu, t('sidebar.newDir'), async () => _createTreeEntry(entry.path, 'dir'));
        _appendTreeMenuItem(menu, t('sidebar.newFile'), async () => _createTreeEntry(entry.path, 'file'));
        _appendTreeMenuItem(menu, t('sidebar.hwImportMenu'), async () => _pickHardwareConfigForDir(entry.path, entry.name));
      }
      if (!entry.is_root) {
        _appendTreeMenuItem(menu, t('sidebar.rename'), async () => _renameTreeEntry(entry));
        _appendTreeMenuItem(menu, t('sidebar.delete'), async () => _deleteTreeEntry(entry));
      }
    });
  }

  function _buildTreeActions(entry, rowEl) {
    const actions = document.createElement('div');
    actions.className = 'tree-entry-actions';

    const menuBtn = document.createElement('button');
    menuBtn.type = 'button';
    menuBtn.className = 'tree-action-btn tree-menu-btn';
    menuBtn.title = entry.type === 'dir' ? t('sidebar.dirMenuTitle') : t('sidebar.fileMenuTitle');
    menuBtn.setAttribute('aria-expanded', 'false');
    menuBtn.innerHTML = niIcon('more-vertical');
    menuBtn.addEventListener('click', e => {
      e.stopPropagation();
      _openEntryMenu(entry, menuBtn);
    });

    rowEl.addEventListener('contextmenu', e => {
      e.preventDefault();
      e.stopPropagation();
      _openEntryMenu(entry, menuBtn);
    });

    actions.appendChild(menuBtn);
    return actions;
  }

  function _buildDirRow(entry, childEl) {
    const dirEl = document.createElement('div');
    dirEl.className = 'tree-dir';
    dirEl.dataset.path = entry.path;

    const toggle = document.createElement('button');
    toggle.type = 'button';
    toggle.className = 'tree-dir-toggle ni-icon ni-icon-chevron-right';
    toggle.setAttribute('aria-label', entry.name);

    const name = document.createElement('span');
    name.className = 'tree-entry-name';
    name.textContent = entry.name;
    if (entry.is_root) dirEl.classList.add('tree-root');

    dirEl.addEventListener('click', e => {
      e.stopPropagation();
      const open = childEl.style.display !== 'none';
      childEl.style.display = open ? 'none' : 'block';
      toggle.classList.toggle('open', !open);
      _closeTreeContextMenu();
    });

    dirEl.appendChild(toggle);
    dirEl.appendChild(name);
    dirEl.appendChild(_buildTreeActions(entry, dirEl));
    return dirEl;
  }

  function _buildFileRow(entry) {
    const fileEl = document.createElement('div');
    const ftype = entry.file_type || 'unknown';
    fileEl.className = `tree-file ft-${ftype}`;
    fileEl.dataset.path = entry.path;
    fileEl.dataset.fileType = ftype;

    const name = document.createElement('span');
    name.className = 'tree-entry-name';
    name.textContent = entry.name;
    fileEl.appendChild(name);
    fileEl.appendChild(_buildTreeActions(entry, fileEl));

    if (activeFile && activeFile.path === entry.path) {
      fileEl.classList.add('active');
    }

    fileEl.addEventListener('click', () => {
      _closeTreeContextMenu();
      selectFile(entry.path, entry.name);
    });
    return fileEl;
  }

  function renderTree(entries, container) {
    entries.forEach(entry => {
      if (entry.type === 'dir') {
        const childEl = document.createElement('div');
        childEl.className = 'tree-children';
        childEl.style.display = entry.open ? 'block' : 'none';
        renderTree(entry.children, childEl);
        const dirEl = _buildDirRow(entry, childEl);
        dirEl.querySelector('.tree-dir-toggle')?.classList.toggle('open', !!entry.open);

        container.appendChild(dirEl);
        container.appendChild(childEl);
        return;
      }
      container.appendChild(_buildFileRow(entry));
    });
  }

  function updateTreeHighlights() {
    document.querySelectorAll('.tree-file').forEach(el => {
      const isActive = !!activeFile && el.dataset.path === activeFile.path;
      const ftype = el.dataset.fileType || 'unknown';
      el.className = `tree-file ft-${ftype}${isActive ? ' active' : ''}`;
    });
  }

  // ── Aktive Datei ─────────────────────────────────────────────────

  async function selectFile(path, name, { force = false } = {}) {
    if (!force && activeFile && activeFile.path === path) return;

    if (_formDirty) {
      const ok = await _autoSave();
      if (!ok) return;
      _formDirty = false;
    }
    if (_flakeFormDirty) {
      const ok = await _flakeSave();
      if (!ok) return;
    }

    activeFile = { path, name };
    _brixTargetFile  = path;
    _brixContextFile = path;
    _updateActiveFileLabel();
    updateTreeHighlights();
    await _loadFileIntoView(path);
  }

  function _updateActiveFileLabel() {
    const el = document.getElementById('active-file-label');
    if (!el) return;
    if (activeFile) {
      el.textContent = activeFile.name;
      el.title = activeFile.path;
      el.classList.add('has-file');
    } else {
      el.textContent = '';
      el.classList.remove('has-file');
    }
  }

  // Aktiver Pfad der gerade im Raw-Editor angezeigten Datei
  let _rawEditPath = null;

  async function _renderFileIntoView(path, data, { skipTypeDialog = false } = {}) {
    const fileName = path.split('/').pop();
    const ftype = data.file_type || 'unknown';
    if (ftype === 'co') {
      _clearRawView();
      _showLeftPanel('form');
      switchTab('configuration');
      const hostMatch = path.match(/(?:^|\/)hosts\/([^/]+)\/default\.nix$/);
      if (fileName === 'configuration.nix' && !path.includes('/')) {
        _activeHost = '';
        await _populateCoFormFromFile(path, data.content);
        await updatePreview();
      } else if (hostMatch) {
        _activeHost = hostMatch[1];
        await _populateCoFormFromFile(path, data.content);
        await updatePreview();
      } else {
        _activeHost = '';
        await _populateCoFormFromFile(path, data.content);
        renderCodePreview(data.content, 'preview-configuration', path);
      }
    } else if (ftype === 'fl') {
      _clearRawView();
      _showLeftPanel('fl');
      switchTab('flake');
      await _populateFlakeFormFromFile(data.content);
    } else if (ftype === 'flk') {
      _showFlkView(data.content);
      _showLeftPanel('flk');
    } else if (ftype === 'hm') {
      _clearRawView();
      _showLeftPanel('hm', fileName);
      document.getElementById('hm-tab')?.classList.remove('hidden');
      _brixTargetFile  = path;
      _brixContextFile = path;
      _brixTargetFtype = 'hm';
      _brixContextFtype = 'hm';
      renderCodePreview(data.content, 'preview-hm', path);
      switchTab('hm');
      _populateHmPanel(data.content, path);
    } else {
      _showRawView(data.content, fileName, path);
      _showLeftPanel(ftype, fileName);
      if (data.file_type === null && !skipTypeDialog) openSetType();
    }
    document.querySelectorAll('.tree-file').forEach(el => {
      if (el.dataset.path === path) {
        el.dataset.fileType = ftype;
        el.className = `tree-file active ft-${ftype}`;
      }
    });
  }

  async function _loadFileIntoView(path, { skipTypeDialog = false } = {}) {
    try {
      const res  = await fetch(`/api/file?path=${encodeURIComponent(path)}`);
      const data = await res.json();
      // Abbruch falls der Nutzer zwischenzeitlich eine andere Datei gewählt hat
      if (activeFile?.path !== path) return;
      if (data.error) { showToast(tErr(data.error), 'error'); return; }
      await _renderFileIntoView(path, data, { skipTypeDialog });
    } catch (e) {
      showToast(t('sidebar.loadError'), 'error');
    }
  }

  function _showLeftPanel(mode, fileName) {
    // Hide all panels
    document.getElementById('config-form')?.classList.add('hidden');
    document.getElementById('flake-form')?.classList.add('hidden');
    document.getElementById('raw-panel')?.classList.add('hidden');
    document.querySelectorAll('.type-panel').forEach(p => p.classList.add('hidden'));

    if (mode === 'form') {
      document.getElementById('config-form')?.classList.remove('hidden');
    } else if (mode === 'fl') {
      document.getElementById('flake-form')?.classList.remove('hidden');
    } else if (mode === 'raw') {
      const rawPanel = document.getElementById('raw-panel');
      rawPanel?.classList.remove('hidden');
      const hwWarn = document.getElementById('raw-panel-hw-warn');
      hwWarn?.classList.toggle('hidden', fileName !== 'hardware-configuration.nix');
    } else {
      document.getElementById(`panel-${mode}`)?.classList.remove('hidden');
    }
  }

  // ── Panel-Listener-Bereinigung (AbortController) ─────────────────
  let _hmPanelAC  = null;
  let _hmCurrentContent = '';
  let _hmCurrentPath = '';

  // ── HM-Panel Parser ──────────────────────────────────────────────

  /** Liest einen String-Wert aus Nix-Inhalt (z.B. home.username = "martin").
   *  Gibt null zurück wenn der Schlüssel nicht vorhanden ist. */
  function _nixGetStr(content, key) {
    const ek = key.replace(/[.+*?^${}()|[\]\\-]/g, '\\$&');
    const m  = new RegExp(`${ek}\\s*=\\s*"([^"]*)"`, 'm').exec(content);
    return m ? m[1] : null;
  }

  /** Liest einen Bool-Wert aus Nix-Inhalt (z.B. programs.home-manager.enable = true).
   *  Gibt null zurück wenn der Schlüssel nicht vorhanden ist. */
  function _nixGetBool(content, key) {
    const ek = key.replace(/[.+*?^${}()|[\]\\-]/g, '\\$&');
    const m  = new RegExp(`${ek}\\s*=\\s*(true|false)`, 'm').exec(content);
    return m ? m[1] === 'true' : null;
  }

  /** Liest einen Integer-Wert aus Nix-Inhalt (z.B. configurationLimit = 5).
   *  Gibt null zurück wenn der Schlüssel nicht vorhanden ist. */
  function _nixGetInt(content, key) {
    const ek = key.replace(/[.+*?^${}()|[\]\\-]/g, '\\$&');
    const m  = new RegExp(`${ek}\\s*=\\s*(\\d+)`, 'm').exec(content);
    return m ? parseInt(m[1], 10) : null;
  }

  /** Wie _nixGetBool, prüft aber auch Block-Syntax: parentKey = { childKey = true; }.
   *  Nützlich für z.B. programs.steam = { enable = true; }. */
  function _nixGetBoolBlock(content, parentKey, childKey) {
    const direct = _nixGetBool(content, `${parentKey}.${childKey}`);
    if (direct !== null) return direct;
    const epk   = parentKey.replace(/[.+*?^${}()|[\]\\-]/g, '\\$&');
    const eck   = childKey.replace(/[.+*?^${}()|[\]\\-]/g, '\\$&');
    const block = new RegExp(`${epk}\\s*=\\s*\\{([^}]*)\\}`, 'm').exec(content);
    if (!block) return null;
    const inner = new RegExp(`\\b${eck}\\s*=\\s*(true|false)`, 'm').exec(block[1]);
    return inner ? inner[1] === 'true' : null;
  }

  /** Ersetzt einen Wert in Nix-Inhalt; unverändert wenn Schlüssel nicht gefunden */
  function _nixSetValue(content, key, nixValue) {
    const ek = key.replace(/[.+*?^${}()|[\]\\-]/g, '\\$&');
    const re = new RegExp(`(${ek}\\s*=\\s*)(?:"[^"]*"|true|false|\\d+)`, 'm');
    return re.test(content) ? content.replace(re, `$1${nixValue}`) : content;
  }

  /** Parst alle Argumente aus dem Nix-Funktionskopf (ohne '...'). */
  function _nixGetHmArgs(content) {
    const m = /^\s*\{([^}]*)\}\s*:/m.exec(content);
    if (!m) return [];
    return m[1].split(',')
      .map(s => s.trim()).filter(s => s && s !== '...');
  }

  /** Schreibt Argumente zurück in den Nix-Funktionskopf ('...' wird immer angehängt). */
  function _nixSetHmArgs(content, args) {
    const allArgs = [...args, '...'];
    return content.replace(/^\s*\{[^}]*\}\s*:/m, `{ ${allArgs.join(', ')} }:`);
  }

  function _hmGetShell(content) {
    for (const sh of ['bash', 'zsh', 'fish']) {
      if (new RegExp(`programs\\.${sh}\\s*=\\s*\\{`).test(content)) return sh;
    }
    return null;
  }

  function _hmGetInitExtra(content) {
    const m = /(?:initExtra|shellInit)\s*=\s*''([\s\S]*?)''/m.exec(content);
    if (!m) return '';
    const raw = m[1].replace(/^\n/, '').trimEnd();
    if (!raw) return '';
    const lines = raw.split('\n');
    const indent = Math.min(...lines.filter(l => l.trim()).map(l => l.match(/^(\s*)/)[1].length));
    return lines.map(l => l.slice(indent)).join('\n').trimEnd();
  }

  function _hmGetPackages(content) {
    const m = /home\.packages\s*=(?:\s*with\s+pkgs\s*;)?\s*\[([\s\S]*?)\]/m.exec(content);
    if (!m) return [];
    const inner = m[1];
    const fmt1 = [...inner.matchAll(/pkgs\.([\w_-]+)/g)].map(x => x[1]);
    if (fmt1.length > 0) return fmt1;
    return inner.trim().split(/\s+/).filter(s => s && !s.startsWith('#') && /^[\w_-]+$/.test(s));
  }

  /** Baut das HM-Benutzer-Formular und befüllt es mit geparsten Werten */
  function _populateHmPanel(content, filePath) {
    const container = document.getElementById('panel-hm-content');
    if (!container) return;

    const customArgs   = _nixGetHmArgs(content);
    const username     = _nixGetStr(content,  'home.username');
    const homeDir      = _nixGetStr(content,  'home.homeDirectory');
    const stateVersion = _nixGetStr(content,  'home.stateVersion');
    const hmEnable     = _nixGetBool(content, 'programs.home-manager.enable');
    const shell        = _hmGetShell(content);
    const initExtra    = shell ? _hmGetInitExtra(content) : '';
    const packages     = _hmGetPackages(content);
    _hmCurrentContent  = content;
    _hmCurrentPath     = filePath;

    const argsRowsHtml = customArgs.map(a => `
      <div class="hm-arg-row">
        <input type="text" class="hm-arg-name mono-input" value="${escHtml(a)}" placeholder="Argument (z.B. lib, osConfig)" spellcheck="false">
        <button type="button" class="fh-item-remove" title="Entfernen">${niIcon('x')}</button>
      </div>`).join('');

    container.innerHTML = `
      <div class="section-controls">
        <button type="button" id="hm-collapse-all-btn"
                data-i18n="sections.collapseAll"
                data-i18n-title="sections.collapseAllTitle"
                title="Alle Sektionen einklappen">${niIcon('chevron-up')} <span data-i18n="sections.collapseAll">Einklappen</span></button>
        <button type="button" id="hm-expand-all-btn"
                data-i18n-title="sections.expandAllTitle"
                title="Alle Sektionen aufklappen">${niIcon('chevron-down')} <span data-i18n="sections.expandAll">Aufklappen</span></button>
      </div>
      <section class="collapsible hm-section" data-section="Start">
        <h3 class="sec-toggle"><span data-i18n="hm.argsSection">Argumente</span></h3>
        <div class="sec-body">
          <div id="hm-args-list">${argsRowsHtml}</div>
          <button type="button" id="hm-add-arg-btn" class="fh-add-item-btn">+ ${escHtml(t('hm.addArg') || 'Argument hinzufügen')}</button>
        </div>
      </section>
      <section class="collapsible hm-section" data-section="Home Manager">
        <h3 class="sec-toggle"><span data-i18n="hm.userSection">Benutzer</span></h3>
        <div class="sec-body">
          <div class="hm-field">
            <label data-i18n="hm.username">Benutzername</label>
            <input id="hm-username" type="text" value="${escHtml(username)}" autocomplete="off" spellcheck="false" />
          </div>
          <div class="hm-field">
            <label data-i18n="hm.homeDir">Home-Verzeichnis</label>
            <input id="hm-homeDir" type="text" value="${escHtml(homeDir)}" autocomplete="off" spellcheck="false" />
          </div>
          <div class="hm-field">
            <label data-i18n="hm.stateVersion">State-Version</label>
            <input id="hm-stateVersion" type="text" value="${escHtml(stateVersion)}" autocomplete="off" spellcheck="false" />
          </div>
          <div class="hm-field hm-field-check">
            <input id="hm-hmEnable" type="checkbox" ${hmEnable ? 'checked' : ''} />
            <label for="hm-hmEnable" data-i18n="hm.hmEnable">programs.home-manager.enable</label>
          </div>
        </div>
      </section>
      ${shell ? `
      <section class="collapsible hm-section" data-section="Shell">
        <h3 class="sec-toggle"><span data-i18n="hm.shell">Shell</span> <span class="hint">(${escHtml(shell)})</span></h3>
        <div class="sec-body">
          <label for="hm-initExtra" data-i18n="hm.shellInitExtra">Shell-Init (extra)</label>
          <p class="hint" data-i18n="hm.shellInitExtraHint" style="margin-bottom:4px">Wird ans Ende der Shell-Init-Datei angehängt.</p>
          <textarea id="hm-initExtra" rows="4" spellcheck="false" class="mono-input"
                    placeholder='export PATH="$HOME/.local/bin:$PATH"'>${escHtml(initExtra)}</textarea>
        </div>
      </section>` : ''}
      <section class="collapsible hm-section" data-section="Pakete">
        <h3 class="sec-toggle"><span data-i18n="hm.packages">Pakete</span></h3>
        <div class="sec-body">
          <p class="hint" data-i18n="hm.packagesHint" style="margin-bottom:4px">(ein Nix-Attr pro Zeile)</p>
          <textarea id="hm-packages-ta" rows="4" spellcheck="false"
                    placeholder="ripgrep&#10;bat&#10;fastfetch">${escHtml(packages.join('\n'))}</textarea>
        </div>
      </section>
    `;

    HM_PREVIEW_SECTIONS.forEach(s => collapsedSections.add(s));
    applySectionCollapse();

    const argsList  = container.querySelector('#hm-args-list');
    const addArgBtn = container.querySelector('#hm-add-arg-btn');
    const collapseAllBtn = container.querySelector('#hm-collapse-all-btn');
    const expandAllBtn = container.querySelector('#hm-expand-all-btn');

    container.querySelectorAll('.sec-toggle').forEach(h3 => {
      h3.addEventListener('click', () => {
        const section = h3.closest('section');
        if (section?.dataset.section) toggleSection(section.dataset.section);
      });
    });

    collapseAllBtn?.addEventListener('click', () => {
      HM_PREVIEW_SECTIONS.forEach(s => collapsedSections.add(s));
      applySectionCollapse();
    });
    expandAllBtn?.addEventListener('click', () => {
      HM_PREVIEW_SECTIONS.forEach(s => collapsedSections.delete(s));
      applySectionCollapse();
      collapsedBrix.clear();
      localStorage.setItem('nico-collapsed-brix', '[]');
      document.querySelectorAll('.code-brix.collapsed').forEach(el => el.classList.remove('collapsed'));
    });

    // Remove-Buttons via Event-Delegation
    argsList.addEventListener('click', e => {
      if (e.target.classList.contains('fh-item-remove'))
        e.target.closest('.hm-arg-row').remove();
    });

    // Neue Zeile hinzufügen
    addArgBtn.addEventListener('click', () => {
      const row = document.createElement('div');
      row.className = 'hm-arg-row';
      row.innerHTML = `
        <input type="text" class="hm-arg-name mono-input" value="" placeholder="Argument (z.B. lib, osConfig)" spellcheck="false">
        <button type="button" class="fh-item-remove" title="Entfernen">${niIcon('x')}</button>
      `;
      argsList.appendChild(row);
      row.querySelector('.hm-arg-name').focus();
    });

    // Aktuellen Inhalt als Schreib-Basis festhalten
    let _hmContent = content;
    let _hmSaveTimer = null;

    function _buildHmPatchPayload(path) {
      const args = [...argsList.querySelectorAll('.hm-arg-row')]
        .map(row => row.querySelector('.hm-arg-name')?.value.trim() ?? '')
        .filter(Boolean);
      const pkgRaw = document.getElementById('hm-packages-ta')?.value ?? '';
      const payload = {
        path,
        args,
        username:      document.getElementById('hm-username')?.value      ?? '',
        home_dir:      document.getElementById('hm-homeDir')?.value       ?? '',
        state_version: document.getElementById('hm-stateVersion')?.value  ?? '',
        hm_enable:     document.getElementById('hm-hmEnable')?.checked    ?? false,
        packages:      pkgRaw.trim().split(/\s+/).filter(Boolean),
      };
      const initExtraEl = document.getElementById('hm-initExtra');
      if (initExtraEl) payload.shell_init_extra = initExtraEl.value;
      return payload;
    }

    async function _doHmSave() {
      const payload = _buildHmPatchPayload(filePath);
      try {
        const res  = await csrfFetch('/api/hm/patch', {
          method:  'POST',
          headers: { 'Content-Type': 'application/json' },
          body:    JSON.stringify(payload),
        });
        const data = await res.json();
        if (data.success) {
          _hmContent = data.content;
          _hmCurrentContent = data.content;
          renderCodePreview(data.content, 'preview-hm', filePath);
          showToast(t('hm.saved'), 'success');
          return true;
        } else {
          showToast(tErr(data.error) || t('toast.error'), 'error');
          return false;
        }
      } catch (e) {
        showToast(t('toast.error'), 'error');
        return false;
      }
    }

    if (_hmPanelAC) _hmPanelAC.abort();
    _hmPanelAC = new AbortController();
    const _hmSig = _hmPanelAC.signal;
    container.addEventListener('input',  () => { clearTimeout(_hmSaveTimer); _hmSaveTimer = setTimeout(_doHmSave, 800); }, { signal: _hmSig });
    container.addEventListener('change', () => { clearTimeout(_hmSaveTimer); _hmSaveTimer = setTimeout(_doHmSave, 800); }, { signal: _hmSig });
  }

  async function _saveHmPanelNow() {
    if (!_hmCurrentPath || activeTab !== 'hm') return true;
    const container = document.getElementById('panel-hm-content');
    if (!container || document.getElementById('panel-hm')?.classList.contains('hidden')) return true;

    const args = [...container.querySelectorAll('.hm-arg-row')]
      .map(row => row.querySelector('.hm-arg-name')?.value.trim() ?? '')
      .filter(Boolean);
    const pkgRaw = container.querySelector('#hm-packages-ta')?.value ?? '';
    const payload = {
      path:          _hmCurrentPath,
      args,
      username:      document.getElementById('hm-username')?.value     ?? '',
      home_dir:      document.getElementById('hm-homeDir')?.value      ?? '',
      state_version: document.getElementById('hm-stateVersion')?.value ?? '',
      hm_enable:     document.getElementById('hm-hmEnable')?.checked   ?? false,
      packages:      pkgRaw.trim().split(/\s+/).filter(Boolean),
    };
    const initExtraEl = document.getElementById('hm-initExtra');
    if (initExtraEl) payload.shell_init_extra = initExtraEl.value;

    try {
      const res  = await csrfFetch('/api/hm/patch', {
        method:  'POST',
        headers: { 'Content-Type': 'application/json' },
        body:    JSON.stringify(payload),
      });
      const data = await res.json();
      if (!data.success) {
        showToast(tErr(data.error) || t('toast.error'), 'error');
        return false;
      }
      _hmCurrentContent = data.content;
      renderCodePreview(data.content, 'preview-hm', _hmCurrentPath);
      return true;
    } catch (e) {
      showToast(t('toast.error'), 'error');
      return false;
    }
  }

  // ── Flake-Panel ──────────────────────────────────────────────────

  let _flakePreviewDebounce = null;

  function _scheduleFlakePreviewUpdate() {
    clearTimeout(_flakePreviewDebounce);
    _flakePreviewDebounce = setTimeout(_updateFlakePreview, 450);
  }

  async function _updateFlakePreview() {
    const formData = _getFlakeFormData();
    try {
      const res  = await csrfFetch('/api/preview/flake', {
        method:  'POST',
        headers: { 'Content-Type': 'application/json' },
        body:    JSON.stringify(formData),
      });
      const data = await res.json();
      if (data.flake_nix) renderCodePreview(data.flake_nix, 'preview-flake', 'flake.nix');
    } catch (e) { /* ignore */ }
  }

  function _getFlakeFormData() {
    const v = id => document.getElementById(id);

    const flake_hosts = [];
    document.querySelectorAll('#flake-hosts-list .flake-host-card').forEach(card => {
      const name = (card.dataset.host || '').trim();
      if (!name) return;
      flake_hosts.push({ name });
    });

    return {
      flake_description:     v('flake_description')?.value    ?? '',
      flake_nixpkgs_channel: v('flake_nixpkgs_channel')?.value ?? 'nixos-unstable',
      flake_hm_input:        v('flake_hm_input')?.checked     ?? false,
      flake_hm_follows:      v('flake_hm_follows')?.checked   ?? true,
      flake_hm_module:       v('flake_hm_module')?.checked    ?? true,
      flake_nixos_hardware:  v('flake_nixos_hardware')?.checked ?? false,
      flake_plasma_manager:  v('flake_plasma_manager')?.checked ?? false,
      flake_hosts,
    };
  }

  /**
   * Flake-Formular aus dem Dateiinhalt befüllen (nicht aus nico.json).
   * Wird aufgerufen wenn der User flake.nix öffnet – Datei ist die Wahrheit.
   */
  async function _populateFlakeFormFromFile(content) {
    try {
      renderCodePreview(content, 'preview-flake', 'flake.nix');

      // Datei parsen
      const res    = await csrfFetch('/api/parse/flake', {
        method:  'POST',
        headers: { 'Content-Type': 'application/json' },
        body:    JSON.stringify({ content }),
      });
      const parsed = await res.json();
      if (parsed.error) {
        console.warn('parse/flake error:', parsed.error);
        await _populateFlakeForm();   // fallback auf nico.json
        return;
      }

      // Felder mit Defaults, dann geparste Werte drüber
      const d = {
        flake_description:     '',
        flake_nixpkgs_channel: 'nixos-unstable',
        flake_hm_input:        false,
        flake_hm_follows:      true,
        flake_hm_module:       true,
        flake_nixos_hardware:  false,
        flake_plasma_manager:  false,
        ...parsed,
      };

      const set = (id, val) => { const el = document.getElementById(id); if (el) el.value = val ?? ''; };
      const chk = (id, val) => { const el = document.getElementById(id); if (el) el.checked = !!val; };
      const sel = (id, val) => { const el = document.getElementById(id); if (el) el.value = val ?? el.value; };

      set('flake_description',    d.flake_description);
      sel('flake_nixpkgs_channel', d.flake_nixpkgs_channel);
      chk('flake_hm_input',       d.flake_hm_input);
      chk('flake_hm_follows',     d.flake_hm_follows);
      chk('flake_hm_module',      d.flake_hm_module);
      chk('flake_nixos_hardware', d.flake_nixos_hardware);
      chk('flake_plasma_manager', d.flake_plasma_manager);

      _updateFlakeHmVisibility(!!d.flake_hm_input);
      await _renderFlakeHosts();

      _brixTargetFile  = 'flake.nix';
      _brixContextFile = 'flake.nix';

      ['__header__', 'Start', 'Inputs', 'Inputs-Extra', 'Outputs', 'Outputs-Hosts', 'Outputs-Extra', 'Hosts', 'End'].forEach(s => collapsedSections.add(s));
      applySectionCollapse();

      _flakeFormDirty = false;
      _initFlakeFormListeners();
    } catch (e) {
      console.error('_populateFlakeFormFromFile:', e);
      await _populateFlakeForm();   // fallback
    }
  }

  async function _populateFlakeForm() {
    try {
      const res  = await csrfFetch('/api/config');
      const data = await res.json();
      const set  = (id, val) => { const el = document.getElementById(id); if (el) el.value = val ?? el.value; };
      const chk  = (id, val) => { const el = document.getElementById(id); if (el) el.checked = !!val; };
      const sel  = (id, val) => { const el = document.getElementById(id); if (el && val !== undefined) el.value = val; };

      set('flake_description',    data.flake_description ?? '');
      sel('flake_nixpkgs_channel', data.flake_nixpkgs_channel ?? 'nixos-unstable');
      chk('flake_hm_input',       data.flake_hm_input);
      chk('flake_hm_follows',     data.flake_hm_follows !== false);
      chk('flake_hm_module',      data.flake_hm_module  !== false);
      chk('flake_nixos_hardware', data.flake_nixos_hardware);
      chk('flake_plasma_manager', data.flake_plasma_manager);

      // Sub-options visibility
      _updateFlakeHmVisibility(!!data.flake_hm_input);
      await _renderFlakeHosts();

      // Set brix target to flake.nix for the duration of this panel
      _brixTargetFile = 'flake.nix';
      _brixContextFile = 'flake.nix';

      // Collapse all sections by default
      ['__header__', 'Start', 'Inputs', 'Inputs-Extra', 'Outputs', 'Outputs-Hosts', 'Outputs-Extra', 'Hosts', 'End'].forEach(s => collapsedSections.add(s));
      applySectionCollapse();

      // Render preview
      await _updateFlakePreview();

      // Wire up change handlers (once per form load – idempotent via flag)
      _flakeFormDirty = false;
      _initFlakeFormListeners();
    } catch (e) {
      console.error('_populateFlakeForm:', e);
    }
  }

  function _updateFlakeHmVisibility(hmActive) {
    document.getElementById('flake-hm-follows-opt')?.classList.toggle('hidden', !hmActive);
    document.getElementById('flake-hm-module-opt')?.classList.toggle('hidden', !hmActive);
  }

  function _flakeHostCard(host) {
    const name = typeof host === 'string' ? host : (host.name || '');
    const h    = escHtml(name);

    return `<div class="flake-host-card" data-host="${h}">
      <div class="flake-host-header">
        <span class="flake-host-label">${h}</span>
        <span class="fh-header-actions">
          <button type="button" class="fh-remove-btn" data-host="${h}"
                  title="${escHtml(t('fl.hosts.deleteTitle'))}">${niIcon('x')}</button>
        </span>
      </div>
    </div>`;
  }

  function _addHostRow() {
    return `<div class="fh-add-row">
      <button type="button" class="btn-add-user" id="fh-add-btn">
        ${escHtml(t('fl.hosts.addTitle') || '+ Host hinzufügen')}
      </button>
      <div class="fh-add-inline hidden">
        <input type="text" id="fh-add-input" maxlength="40"
               placeholder="${escHtml(t('fl.hosts.addPlaceholder') || 'hostname')}"
               spellcheck="false" autocomplete="off">
        <button type="button" id="fh-add-confirm" class="fh-add-confirm">✓</button>
        <button type="button" id="fh-add-cancel"  class="fh-add-cancel">✕</button>
      </div>
    </div>`;
  }

  function _bindAddHost(container) {
    const addBtn    = container.querySelector('#fh-add-btn');
    const addInline = container.querySelector('.fh-add-inline');
    const input     = container.querySelector('#fh-add-input');
    const confirmBtn = container.querySelector('#fh-add-confirm');
    const cancelBtn  = container.querySelector('#fh-add-cancel');
    if (!addBtn) return;

    function open()  { addBtn.classList.add('hidden'); addInline.classList.remove('hidden'); input.focus(); }
    function close() { input.value = ''; addBtn.classList.remove('hidden'); addInline.classList.add('hidden'); }

    async function doAdd() {
      const name = input.value.trim();
      if (!name) return;
      if (!/^[a-zA-Z][a-zA-Z0-9_-]*$/.test(name)) {
        showToast(t('fl.hosts.errInvalidName'), 'error'); return;
      }
      const r = await csrfFetch('/api/flake/host/add', {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ name }),
      });
      const j = await r.json();
      if (j.error === 'ERR_HOST_EXISTS') { showToast(t('fl.hosts.errExists'), 'error'); return; }
      if (j.error) { showToast(j.error, 'error'); return; }
      await _renderFlakeHosts();
      _scheduleFlakePreviewUpdate();
    }

    addBtn.addEventListener('click', open);
    confirmBtn.addEventListener('click', doAdd);
    cancelBtn.addEventListener('click', close);
    input.addEventListener('keydown', e => {
      if (e.key === 'Enter')  doAdd();
      if (e.key === 'Escape') close();
    });
  }

  async function _renderFlakeHosts() {
    const container = document.getElementById('flake-hosts-list');
    if (!container) return;
    try {
      const res  = await csrfFetch('/api/flake/hosts');
      const data = await res.json();

      if (!data.flake_mode || !data.hosts?.length) {
        container.innerHTML =
          `<p class="flake-hosts-empty">${escHtml(t('fl.hosts.none'))}</p>` +
          _addHostRow();
        _bindAddHost(container);
        return;
      }

      container.innerHTML = data.hosts.map(_flakeHostCard).join('') + _addHostRow();

      // Delete buttons
      container.querySelectorAll('.fh-remove-btn').forEach(btn => {
        btn.addEventListener('click', async () => {
          const name = btn.dataset.host;
          if (!confirm(t('fl.hosts.deleteConfirm').replace('{name}', name))) return;
          const r = await csrfFetch('/api/flake/host/delete', {
            method: 'POST', headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ name }),
          });
          const j = await r.json();
          if (j.error) { showToast(j.error, 'error'); return; }
          await _renderFlakeHosts();
          _scheduleFlakePreviewUpdate();
        });
      });

      _bindAddHost(container);

    } catch {
      container.innerHTML = '';
    }
  }

  let _flakeListenersInitialized = false;

  function _initFlakeFormListeners() {
    if (_flakeListenersInitialized) return;
    _flakeListenersInitialized = true;

    const form = document.getElementById('flake-form');
    if (!form) return;

    // Dirty-Flag setzen + Preview aktualisieren bei Änderung (kein Auto-Save)
    form.addEventListener('change', () => {
      _updateFlakeHmVisibility(!!document.getElementById('flake_hm_input')?.checked);
      _flakeFormDirty = true;
      _scheduleFlakePreviewUpdate();
    });

    // Collapse/expand buttons (form sections + code preview via collapsedSections)
    const FLAKE_PREVIEW_SECTIONS = ['__header__', 'Start', 'Inputs', 'Inputs-Extra', 'Outputs', 'Outputs-Hosts', 'Outputs-Extra', 'Hosts', 'End'];
    document.getElementById('flake-collapse-all-btn')?.addEventListener('click', () => {
      FLAKE_PREVIEW_SECTIONS.forEach(s => collapsedSections.add(s));
      applySectionCollapse();
    });
    document.getElementById('flake-expand-all-btn')?.addEventListener('click', () => {
      FLAKE_PREVIEW_SECTIONS.forEach(s => collapsedSections.delete(s));
      applySectionCollapse();
    });

    // NOTE: sec-toggle click is handled globally by the main init (toggleSection).
    // No extra listener needed here – adding one would double-fire and cancel the toggle.
  }

  /** Flake-Formular auf den Server speichern (wie _autoSave für CO). Nur wenn dirty. */
  async function _flakeSave() {
    if (!_flakeFormDirty) return true;
    try {
      const res  = await csrfFetch('/api/config/flake', {
        method:  'POST',
        headers: { 'Content-Type': 'application/json' },
        body:    JSON.stringify(_getFlakeFormData()),
      });
      const data = await res.json().catch(() => null);
      if (data?.success) { _flakeFormDirty = false; return true; }
      showToast(tErr(data?.error) || t('toast.error'), 'error');
      return false;
    } catch (e) {
      showToast(t('toast.error'), 'error');
      return false;
    }
  }

  // ── Datei neu laden ──────────────────────────────────────────────

  async function _reloadFile() {
    if (!activeFile) return;
    const ftype = document.querySelector(`.tree-file[data-path="${CSS.escape(activeFile.path)}"]`)?.dataset.fileType;
    if (ftype === 'co') {
      await loadConfig();
      _formDirty = false;
    } else {
      await _loadFileIntoView(activeFile.path, { skipTypeDialog: true });
    }
    showToast(t('tools.reloaded'), 'success');
  }

  function _showRawView(content, fileName, filePath) {
    _clearRawView();
    _rawEditPath = filePath;
    const previewTabs = elPreviewPanel.querySelector('.preview-tabs');
    if (previewTabs) { previewTabs.dataset.hiddenByRaw = '1'; previewTabs.style.display = 'none'; }
    elPreviewPanel.querySelectorAll('.preview-code-wrap, .preview-content').forEach(el => {
      el.dataset.hiddenByRaw = '1'; el.style.display = 'none';
    });
    const rawDiv = document.createElement('div');
    rawDiv.id = 'raw-file-view';
    rawDiv.className = 'raw-file-view';

    const lineCount = content.split('\n').length;
    const lineNums  = Array.from({length: lineCount}, (_, i) => i + 1).join('\n');
    rawDiv.innerHTML = `
      <div class="raw-editor-wrap">
        <div class="raw-line-nums" aria-hidden="true">${lineNums}</div>
        <textarea id="raw-file-editor" class="raw-file-editor" spellcheck="false">${escHtml(content)}</textarea>
      </div>
    `;
    elPreviewPanel.appendChild(rawDiv);
    const _ta  = rawDiv.querySelector('#raw-file-editor');
    const _lns = rawDiv.querySelector('.raw-line-nums');
    _ta.addEventListener('scroll', () => { _lns.scrollTop = _ta.scrollTop; });
    _ta.addEventListener('input',  () => {
      const c = _ta.value.split('\n').length;
      _lns.textContent = Array.from({length: c}, (_, i) => i + 1).join('\n');
    });
    // Show raw-save button, switch mode indicator to pencil
    const rawSaveTabBtn = document.getElementById('raw-save-tab-btn');
    if (rawSaveTabBtn) { rawSaveTabBtn.classList.remove('hidden'); rawSaveTabBtn.onclick = _saveRawFile; }
    const modBtn = document.getElementById('preview-mode-btn');
    if (modBtn) { modBtn.innerHTML = niIcon('pencil'); modBtn.dataset.mode = 'edit'; modBtn.title = t('preview.modeRaw'); }
  }

  function _showFlkView(content) {
    _clearRawView();
    const previewTabs = elPreviewPanel.querySelector('.preview-tabs');
    if (previewTabs) { previewTabs.dataset.hiddenByRaw = '1'; previewTabs.style.display = 'none'; }
    elPreviewPanel.querySelectorAll('.preview-code-wrap, .preview-content').forEach(el => {
      el.dataset.hiddenByRaw = '1'; el.style.display = 'none';
    });

    const lines     = content.split('\n');
    const lineNums  = lines.map((_, i) => i + 1).join('\n');
    const highlighted = (typeof Prism !== 'undefined' && Prism.languages.nix)
      ? Prism.highlight(content, Prism.languages.nix, 'nix')
      : escHtml(content);

    const flkDiv = document.createElement('div');
    flkDiv.id = 'flk-file-view';
    flkDiv.className = 'raw-file-view';
    flkDiv.innerHTML = `
      <div class="raw-editor-wrap">
        <div class="raw-line-nums" aria-hidden="true">${lineNums}</div>
        <pre class="flk-file-pre language-nix"><code>${highlighted}</code></pre>
      </div>`;
    elPreviewPanel.appendChild(flkDiv);

    const lns = flkDiv.querySelector('.raw-line-nums');
    const pre = flkDiv.querySelector('.flk-file-pre');
    pre.addEventListener('scroll', () => { lns.scrollTop = pre.scrollTop; });
  }

  async function _saveRawFile() {
    const path    = _rawEditPath;
    const content = document.getElementById('raw-file-editor')?.value ?? '';
    if (!path) return;

    const fileName = path.split('/').pop();
    // hardware-configuration.nix: Bestätigungsdialog
    if (fileName === 'hardware-configuration.nix') {
      if (!confirm(t('sidebar.hwSaveConfirm'))) return;
    }

    try {
      const res  = await csrfFetch('/api/file', {
        method:  'POST',
        headers: { 'Content-Type': 'application/json' },
        body:    JSON.stringify({ files: [{ path, content }] }),
      });
      const data = await res.json();
      if (data.success) {
        showToast(t('sidebar.rawSaveOk'), 'success');
        return true;
      } else {
        showToast(tErr(data.error) || t('toast.error'), 'error');
        return false;
      }
    } catch (e) {
      showToast(t('toast.error'), 'error');
      return false;
    }
  }

  async function _togglePlainCodeViewInSidebar() {
    plainCodeView = !plainCodeView;
    console.log('[plain-toggle] toggled', {
      plainCodeView,
      activeTab,
      activeFile,
    });
    applyPlainCodeViewBtn();
    csrfFetch('/api/app/settings', {
      method:  'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify({ code_view_plain: plainCodeView }),
    }).catch(() => {});
    if (_rawEditPath) {
      const ok = await _saveRawFile();
      if (!ok) return;
    } else if (activeTab === 'hm') {
      const ok = await _saveHmPanelNow();
      if (!ok) return;
    } else if (activeTab === 'flake' && _flakeFormDirty) {
      const ok = await _flakeSave();
      if (!ok) return;
    } else if (_formDirty) {
      const ok = await _autoSave();
      if (!ok) return;
      _formDirty = false;
    }
    if (activeFile?.path) {
      try {
        const res = await fetch(`/api/file?path=${encodeURIComponent(activeFile.path)}`);
        const data = await res.json();
        if (data.error) {
          showToast(tErr(data.error) || t('toast.error'), 'error');
          return;
        }
        await _renderFileIntoView(activeFile.path, data, { skipTypeDialog: true });
      } catch (e) {
        showToast(t('sidebar.loadError'), 'error');
      }
      return;
    }
    await _reloadFile();
  }

  function _clearRawView() {
    document.getElementById('raw-file-view')?.remove();
    document.getElementById('flk-file-view')?.remove();
    _rawEditPath = null;
    document.querySelectorAll('[data-hidden-by-raw]').forEach(el => {
      el.style.display = ''; delete el.dataset.hiddenByRaw;
    });
    const rawSaveTabBtn = document.getElementById('raw-save-tab-btn');
    if (rawSaveTabBtn) { rawSaveTabBtn.classList.add('hidden'); rawSaveTabBtn.onclick = null; }
    const modBtn = document.getElementById('preview-mode-btn');
    if (modBtn) delete modBtn.dataset.mode;
    applyPlainCodeViewBtn();
  }

  async function openFileViewer(relPath, forceReadonly = false) {
    const encoded = relPath.split('/').map(encodeURIComponent).join('/');
    const res  = await csrfFetch(`/api/file/${encoded}`);
    const info = await res.json();
    if (info.error) { showToast(tErr(info.error), 'error'); return; }

    const readonly  = forceReadonly || !info.writable;
    const isHwConfig = relPath.includes('hardware-configuration');

    document.getElementById('file-viewer-title').textContent = relPath.split('/').pop();
    const textarea = document.getElementById('file-viewer-content');
    textarea.value    = info.content;
    textarea.readOnly = readonly;

    const warning = document.getElementById('file-viewer-warning');
    if (isHwConfig && !readonly) {
      warning.textContent = t('file.hwWarning');
      warning.classList.remove('hidden');
    } else {
      warning.classList.add('hidden');
    }

    document.getElementById('file-viewer-actions').classList.toggle('hidden', readonly);
    document.getElementById('file-viewer-overlay').classList.remove('hidden');

    document.getElementById('file-viewer-close').onclick = () =>
      document.getElementById('file-viewer-overlay').classList.add('hidden');

    document.getElementById('file-viewer-save').onclick = async () => {
      const r = await csrfFetch(`/api/file/${encoded}`, {
        method:  'POST',
        headers: { 'Content-Type': 'application/json' },
        body:    JSON.stringify({ content: textarea.value }),
      });
      const d = await r.json();
      if (d.error) { showToast(tErr(d.error), 'error'); return; }
      document.getElementById('file-viewer-overlay').classList.add('hidden');
      showToast(t('file.saved'));
    };
  }

  function setActiveFile(path, name) {
    activeFile = { path, name };
    _updateActiveFileLabel();
    updateTreeHighlights();
  }

  function clearFlkIfActive() {
    if (activeFile?.name !== 'flake.lock') return;
    activeFile = null;
    _updateActiveFileLabel();
    _clearRawView();
    _showLeftPanel('none');
  }

  return {
    init,
    openFileViewer,
    flakeSave: _flakeSave,
    setActiveFile,
    closeTreeContextMenu: _closeTreeContextMenu,
    updateFlakePreview: _updateFlakePreview,
    togglePlainCodeView: _togglePlainCodeViewInSidebar,
    loadTree,
    clearFlkIfActive,
  };
})();
