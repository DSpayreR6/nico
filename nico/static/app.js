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

/** Drop-in fetch replacement that adds the CSRF token to every request.
 *  GET needs it too: /api/file only persists its type stamp for requests
 *  that prove same-origin via the token. */
function csrfFetch(url, options = {}) {
  const headers = { ...(options.headers || {}), 'X-CSRF-Token': CSRF_TOKEN };
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

/** Like t(), but falls back to a server-provided text (e.g. German validator
 *  messages) when the key is missing or still a __TODO__ placeholder. */
function tOr(key, fallback, ...args) {
  const raw = key ? _lang[key] : undefined;
  if (!raw || raw === '__TODO__') return fallback;
  let str = raw;
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
let _gitSync       = true;   // false = skip remote sync (pull on start, auto-push)
let _gitStatusOnly = false;  // true = show local git status even when _gitSync=false

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
let _hostsDir       = 'hosts';     // config.json hosts_dir – refreshed in loadConfig()

async function refreshHostsDir() {
  try {
    const res  = await csrfFetch('/api/config/settings');
    const data = await res.json();
    _hostsDir = (data.hosts_dir || 'hosts').trim() || 'hosts';
  } catch { /* keep current value */ }
}

function hostCoPath(hostName) {
  return `${_hostsDir}/${hostName}/default.nix`;
}

function matchHostCoPath(path) {
  const esc = _hostsDir.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  return path.match(new RegExp(`(?:^|/)${esc}/([^/]+)/default\\.nix$`));
}

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
        _gitSync       = data.git_sync !== false;
        _gitStatusOnly = !!data.git_status_only;
        if (_gitSync) {
          const ok = await ensureGitStartGuard(data.nixos_config_dir);
          if (!ok) return;
        }
        showApp(data.nixos_config_dir);
        await loadConfig().catch(e => console.error('loadConfig:', e));
        Sidebar.setActiveFile('configuration.nix', 'configuration.nix');
        if (_gitSync || _gitStatusOnly) checkGitStatus();
        if (_gitSync) checkGitRemoteStatus();
        if (_gitSync) csrfFetch('/api/git/log').then(r => r.json()).then(d => {
          if ((d.commits || []).length >= 2)
            document.getElementById('nixos-diff-btn')?.classList.remove('hidden');
        }).catch(() => {});
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

  if (_gitSync) {
    const ok = await ensureGitStartGuard(data.nixos_config_dir);
    if (!ok) return;
  }

  showApp(data.nixos_config_dir);
  await loadConfig();
  if (_gitSync || _gitStatusOnly) checkGitStatus();

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
  await refreshHostsDir();
  _activeHost = '';
  _brixTargetFile  = 'configuration.nix';
  _brixContextFile = 'configuration.nix';
  _brixTargetFtype = 'co';
  _brixContextFtype = 'co';
  hideIntegrityWarning();
  await _populateCoFormFromFile('configuration.nix');
  Sidebar.refreshPanelToggle('configuration.nix', 'co');

  // Collapse everything on (re)load
  document.querySelectorAll('section.collapsible[data-section]').forEach(s =>
    collapsedSections.add(s.dataset.section));
  // Also add code-only sections (Home Manager) that have no left-panel element
  BRICK_SECTIONS.forEach(s => collapsedSections.add(s));
  collapsedSections.add('__header__');
  applySectionCollapse();
  await updatePreview();
  loadHmFileList();
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
      // 'dirty' impliziert behind == 0 (sonst wäre state behind/diverged):
      // Remote-Reset würde hier ungepushte lokale Commits mitvernichten –
      // stattdessen nur unkommittierte Änderungen verwerfen (Commits bleiben).
      const hasAhead  = (check.ahead || 0) > 0;
      const fetchCard = check.state === 'dirty'
        ? `
        <div class="git-guard-card">
          <div class="git-guard-card-info">
            <div class="git-guard-card-title">${escHtml(t('git.guard.s2.discardTitle'))}</div>
            <div class="git-guard-card-desc">${escHtml(t('git.guard.s2.discardDesc'))}</div>
          </div>
          <button type="button" data-card="discard" class="action-btn btn-green">${escHtml(t('git.guard.s2.discardBtn'))}</button>
        </div>`
        : `
        <div class="git-guard-card${hasAhead ? ' git-guard-card--warn' : ''}">
          <div class="git-guard-card-info">
            <div class="git-guard-card-title">${escHtml(t('git.guard.s2.fetchTitle'))}</div>
            <div class="git-guard-card-desc">${escHtml(t('git.guard.s2.fetchDesc'))}${hasAhead ? ' ' + escHtml(t('git.guard.s2.fetchWarnAhead', check.ahead)) : ''}</div>
          </div>
          <button type="button" data-card="fetch" class="action-btn ${hasAhead ? 'btn-red' : 'btn-green'}">${escHtml(t('git.guard.s2.fetchBtn'))}</button>
        </div>`;
      cardsHtml = `${fetchCard}
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
            fetch:   'git.guard.s2.fetchError',
            send:    'git.guard.s2.sendError',
            discard: 'git.guard.s2.discardError',
          };
          // discard: only uncommitted changes are dropped, local commits survive
          if (cardId === 'discard') {
            const r = await csrfFetch('/api/git/discard-local', { method: 'POST' });
            const d = await r.json();
            if (!d.success) throw new Error(d.message || '');
            location.reload();
            return;
          }
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

function _updateGitStatusOnlyVisibility() {
  const syncOn  = document.getElementById('setting-git-sync')?.checked !== false;
  const row     = document.getElementById('setting-git-status-only-row');
  if (row) row.classList.toggle('hidden', syncOn);
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
    const cbPrefetchDryRun = document.getElementById('setting-prefetch-dry-run');
    if (cbPrefetchDryRun) cbPrefetchDryRun.checked = data.prefetch_dry_run !== false;
    const cbGitSync = document.getElementById('setting-git-sync');
    if (cbGitSync) cbGitSync.checked = data.git_sync !== false;
    const cbGitStatusOnly = document.getElementById('setting-git-status-only');
    if (cbGitStatusOnly) cbGitStatusOnly.checked = !!data.git_status_only;
    _updateGitStatusOnlyVisibility();

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

  // Auto-save Checkbox: prefetch_dry_run
  const cbPrefetchDryRun = document.getElementById('setting-prefetch-dry-run');
  if (cbPrefetchDryRun && !cbPrefetchDryRun.dataset.listenerAttached) {
    cbPrefetchDryRun.dataset.listenerAttached = '1';
    cbPrefetchDryRun.addEventListener('change', () => {
      csrfFetch('/api/app/settings', {
        method:  'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body:    JSON.stringify({ prefetch_dry_run: cbPrefetchDryRun.checked }),
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

  // Auto-save: git_sync
  const cbGitSyncEl = document.getElementById('setting-git-sync');
  if (cbGitSyncEl && !cbGitSyncEl.dataset.listenerAttached) {
    cbGitSyncEl.dataset.listenerAttached = '1';
    cbGitSyncEl.addEventListener('change', () => {
      _gitSync = cbGitSyncEl.checked;
      _updateGitStatusOnlyVisibility();
      csrfFetch('/api/app/settings', {
        method:  'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body:    JSON.stringify({ git_sync: cbGitSyncEl.checked }),
      }).then(() => showToast(t('admin.settings.saved'), 'success'))
        .catch(() => showToast(t('toast.error'), 'error'));
    });
  }

  // Auto-save: git_status_only
  const cbGitStatusOnlyEl = document.getElementById('setting-git-status-only');
  if (cbGitStatusOnlyEl && !cbGitStatusOnlyEl.dataset.listenerAttached) {
    cbGitStatusOnlyEl.dataset.listenerAttached = '1';
    cbGitStatusOnlyEl.addEventListener('change', () => {
      _gitStatusOnly = cbGitStatusOnlyEl.checked;
      csrfFetch('/api/app/settings', {
        method:  'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body:    JSON.stringify({ git_status_only: cbGitStatusOnlyEl.checked }),
      }).then(() => showToast(t('admin.settings.saved'), 'success'))
        .catch(() => showToast(t('toast.error'), 'error'));
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

    // Panel-Default
    _panelDefault = data.panel_default || 'p';
    const panelDefaultToggle = document.getElementById('panel-default-toggle');
    if (panelDefaultToggle) {
      panelDefaultToggle.checked = (_panelDefault === 'r');
      if (!panelDefaultToggle.dataset.listenerAttached) {
        panelDefaultToggle.dataset.listenerAttached = '1';
        panelDefaultToggle.addEventListener('change', () => {
          const newVal = panelDefaultToggle.checked ? 'r' : 'p';
          _panelDefault = newVal;
          csrfFetch('/api/config/settings', {
            method: 'PATCH', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ panel_default: newVal }),
          }).then(() => showToast(t('admin.settings.saved'), 'success'))
            .catch(() => showToast(t('toast.error'), 'error'));
        });
      }
    }
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
      }).then(() => {
        _hostsDir = hostsDir;
        showToast(t('admin.settings.saved'), 'success');
      })
        .catch(() => showToast(t('toast.error'), 'error'));
    });
  }
}

function _expectedCoPath() {
  return _activeHost ? hostCoPath(_activeHost) : 'configuration.nix';
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
  if (tab === 'einstellungen') { loadSettingsPath(); }
  if (tab === 'zeitmaschine') { checkGitStatus(); loadAdminGitLog(); loadGitignoreEditor(); }
  if (tab === 'administration') { loadAdminSymlinkStatus(); }
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
        <span style="display:flex;align-items:center">${dot}<strong>${_esc(tOr(`validator.rule.${rule.id}.label`, rule.label))}</strong>
          ${rule.flake_only ? '<span class="badge" style="margin-left:6px;font-size:10px;padding:1px 5px;background:var(--surface1);border-radius:4px">Flake</span>' : ''}
        </span>
        <span class="raw-panel-hint" style="margin:0 0 0 14px">${_esc(tOr(`validator.rule.${rule.id}.desc`, rule.description))}</span>
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
  await Sidebar.flakeSave();
  await _autoSave();
  await _writeNix();

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
    // Card look mirrors the git start guard dialog; "Hinweis N von M" header
    // makes findings referenceable and shows that more may follow below the fold.
    body.innerHTML = '<div class="git-guard-cards">' + findings.map((f, idx) => {
      const icon  = niIcon(iconNames[f.severity] || 'info');
      const color = colors[f.severity] || 'var(--text)';
      const counter = tOr('admin.validation.findingCounter',
                          `Hinweis ${idx + 1} von ${findings.length}`,
                          idx + 1, findings.length);
      const detail = f.detail
        ? `<div class="git-guard-card-desc" style="margin-top:4px;white-space:pre-line">${_esc(f.detail)}</div>`
        : '';
      const action = f.rule_id === 'git_missing_gitignore'
        ? `<button class="btn-surface btn-small" style="margin-top:6px;font-size:11px"
             onclick="closeValidationResults();openAdmin();_switchAdminTab('zeitmaschine')"
             data-i18n="admin.gitignore.goToSettings">${t('admin.gitignore.goToSettings')}</button>`
        : '';
      return `<div class="git-guard-card" style="align-items:flex-start">
        <span style="color:${color};font-size:16px;flex-shrink:0;margin-top:1px">${icon}</span>
        <div class="git-guard-card-info">
          <div class="git-guard-card-title">${_esc(counter)}</div>
          <div style="font-size:13px">${_esc(tOr(f.message_key, f.message, ...(f.params || [])))}</div>${detail}${action}
        </div>
      </div>`;
    }).join('') + '</div>';
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

    // Populate diff selects
    const fromSel = document.getElementById('diff-from-select');
    const toSel   = document.getElementById('diff-to-select');
    if (fromSel && toSel) {
      fromSel.innerHTML = '';
      toSel.innerHTML   = `<option value="HEAD">HEAD</option>`;
      for (const c of commits) {
        const short   = c.hash.slice(0, 7);
        const dateParts = (c.date || '').split(' ');
        const dateStr   = dateParts.length >= 2 ? `${dateParts[0]} ${dateParts[1].slice(0, 5)}` : c.date;
        const label   = `${short}  ${dateStr}  ${c.message.slice(0, 40)}`;
        fromSel.appendChild(Object.assign(document.createElement('option'), { value: c.hash, textContent: label }));
        toSel.appendChild(Object.assign(document.createElement('option'),   { value: c.hash, textContent: label }));
      }
      // Default: from = second commit, to = HEAD
      if (fromSel.options.length > 1) fromSel.selectedIndex = 1;
    }

    // Show diff button in NixOS menu when git repo has commits
    if (commits.length >= 2) {
      document.getElementById('nixos-diff-btn')?.classList.remove('hidden');
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

async function loadGitignoreEditor() {
  const missing = document.getElementById('git-gitignore-missing');
  const editor  = document.getElementById('git-gitignore-editor');
  const textarea = document.getElementById('git-gitignore-content');
  if (!missing || !editor || !textarea) return;

  try {
    const res  = await csrfFetch('/api/git/gitignore');
    const data = await res.json();
    if (data.exists) {
      textarea.value = data.content;
      missing.classList.add('hidden');
      editor.classList.remove('hidden');
    } else {
      missing.classList.remove('hidden');
      editor.classList.add('hidden');
    }
  } catch { /* git not set up yet */ }

  const createBtn = document.getElementById('git-gitignore-create-btn');
  if (createBtn && !createBtn.dataset.bound) {
    createBtn.dataset.bound = '1';
    createBtn.addEventListener('click', async () => {
      const res  = await csrfFetch('/api/git/create-gitignore', { method: 'POST' });
      const data = await res.json();
      if (data.success) { showToast(t('admin.gitignore.created'), 'success'); loadGitignoreEditor(); }
      else showToast(data.message || t('toast.error'), 'error');
    });
  }

  const saveBtn = document.getElementById('git-gitignore-save-btn');
  if (saveBtn && !saveBtn.dataset.bound) {
    saveBtn.dataset.bound = '1';
    saveBtn.addEventListener('click', async () => {
      const res  = await csrfFetch('/api/git/create-gitignore', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ content: textarea.value }),
      });
      const data = await res.json();
      if (data.success) showToast(t('admin.gitignore.saved'), 'success');
      else showToast(data.message || t('toast.error'), 'error');
    });
  }

  const addBtn = document.getElementById('git-gitignore-add-btn');
  if (addBtn && !addBtn.dataset.bound) {
    addBtn.dataset.bound = '1';
    addBtn.addEventListener('click', async () => {
      const res  = await csrfFetch('/api/git/create-gitignore', { method: 'POST' });
      const data = await res.json();
      if (data.success) { showToast(t('admin.gitignore.added'), 'success'); loadGitignoreEditor(); }
      else showToast(data.message || t('toast.error'), 'error');
    });
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
    return false;
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
  return data.success ?? false;
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
  if (!await saveConfig()) return;
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

