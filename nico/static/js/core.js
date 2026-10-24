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

// ── Sections ───────────────────────────────────────────────────────────────────
// Code -> i18n key, loaded once from /api/sections. The table lives in brix.py;
// copying it here would create a second truth that drifts.
let _sections       = {};
let _sectionsLegacy = {};
let SECTION_HOST_PREFIX = 'HOST:';
let SECTION_HDR_SEP     = '·';

async function loadSections() {
  try {
    const data = await fetch('/api/sections').then(r => r.json());
    _sections = {};
    (data.sections || []).forEach(s => { _sections[s.code] = s.i18n; });
    _sectionsLegacy     = data.legacy || {};
    SECTION_HOST_PREFIX = data.host_prefix || SECTION_HOST_PREFIX;
    SECTION_HDR_SEP     = data.header_sep  || SECTION_HDR_SEP;
  } catch (_) { /* labels fall back to the code itself */ }
}

// ── Files from a newer NiCo version ────────────────────────────────────────────
// Nothing can be destroyed – unknown content is kept as a brick – but blocks
// may get reordered. So this warns and lets the user decide instead of locking
// them out, and makes a safety commit before the first write.
async function checkFileFormat() {
  let data;
  try {
    data = await fetch('/api/format/check').then(r => r.json());
  } catch (_) { return; }
  const tooNew = data.too_new || [];
  if (!tooNew.length) return;

  const list = document.getElementById('format-files');
  if (list) {
    list.textContent = tooNew
      .map(f => t('format.fileLine', f.file, f.format, data.known))
      .join('\n');
  }
  document.getElementById('format-overlay')?.classList.remove('hidden');
}

function _closeFormatDialog() {
  document.getElementById('format-overlay')?.classList.add('hidden');
}

async function formatProceed() {
  const res  = await csrfFetch('/api/format/safety-commit', { method: 'POST' });
  const data = await res.json().catch(() => ({}));
  if (!data.success && data.message) showToast(t('format.commitFailed', tErr(data.message)), 'error');
  await csrfFetch('/api/format/proceed', { method: 'POST' }).catch(() => {});
  _closeFormatDialog();
  showToast(t('format.proceeding'), 'success');
}

const _SECTION_CODE_RE = /^[A-Z0-9]{4}(:[A-Za-z0-9_.-]+)?$/;

/** Section code for any spelling – mirrors brix.normalize_section(). */
function normalizeSection(name) {
  name = String(name || '').trim();
  if (_SECTION_CODE_RE.test(name)) return name;
  const host = name.match(/^Host:\s*(\S+)$/);
  if (host) return SECTION_HOST_PREFIX + host[1];
  return _sectionsLegacy[name] || name;
}

/** Section code from the text captured out of a `# ── … ──` header line. */
function sectionOfHeader(text) {
  const head = String(text || '');
  const cut  = head.includes(SECTION_HDR_SEP) ? head.split(SECTION_HDR_SEP)[0] : head;
  return normalizeSection(cut);
}

/** Section name in the user's language; the code itself when unknown. */
function secLabel(code) {
  code = String(code || '').trim();
  if (code.startsWith(SECTION_HOST_PREFIX)) {
    return t('sec.host', code.slice(SECTION_HOST_PREFIX.length));
  }
  const key = _sections[code];
  if (!key) return code;
  const text = _lang[key];
  return (!text || text === '__TODO__') ? code : text;
}

/** Replace section codes inside a validator parameter with their names. */
function secParams(params) {
  return (params || []).map(p => {
    const parts = String(p).split(', ');
    if (!parts.every(x => x in _sections || x.startsWith(SECTION_HOST_PREFIX))) return p;
    return parts.map(x => `"${secLabel(x)}"`).join(', ');
  });
}

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
  // CSS pseudo-elements cannot call t(), so hand the label over as a variable
  document.documentElement.style.setProperty(
    '--ni-collapsed-label', JSON.stringify('  ' + t('preview.collapsedHint')));
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
// ASCII spinner state for the package search (see startPkgSpinner in preview.js)
let pkgSpinTimer    = null;
let pkgSpinFrame    = 0;
let pkgSpinStart    = 0;
let pkgSpinQuery    = '';
let pkgSearchSeq    = 0;    // guards against a slow earlier search overwriting a newer one
let _treeContextMenu = null;

// Section names that are currently collapsed – always start fully collapsed
const collapsedSections = new Set();

// Brix block names that are individually collapsed (persisted in localStorage)
const collapsedBrix = (() => { try { return new Set(JSON.parse(localStorage.getItem('nico-collapsed-brix') || '[]')); } catch { return new Set(); } })();

// ── Init ───────────────────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', async () => {
  await loadLang(localStorage.getItem('nico_lang') || 'de');
  await loadSections();
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
  checkFileFormat();
  updateFsHint();
  restoreRebuildMonitor();
});

