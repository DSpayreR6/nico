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


