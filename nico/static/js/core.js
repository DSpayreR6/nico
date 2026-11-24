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

// ── Elapsed-time spinner ──────────────────────────────────────────────────
// Rebuild and dry-run can sit for minutes without a new output line. A
// turning `| / - \` with the elapsed time shows that NiCo is still waiting,
// not stuck. Characters and digits only, so nothing to translate.
const ELAPSED_SPIN_FRAMES = ['|', '/', '-', '\\'];
const ELAPSED_SPIN_MS     = 120;

/** `m:ss` for a duration in milliseconds. */
function fmtElapsed(ms) {
  const s = Math.max(0, Math.floor(ms / 1000));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;
}

/**
 * Animate `el` as `| 1:23` until stop() is called.
 *
 * startMs is when the wait began (epoch ms), so a monitor reopened after a
 * reload can count from the job's real start; setStart() moves it later.
 * stop() leaves the element alone if the caller has written into it
 * meanwhile, and clears it otherwise.
 */
function startElapsedSpinner(el, startMs = Date.now()) {
  let frame = 0;
  let start = startMs;
  let shown = '';
  const render = () => {
    shown = `${ELAPSED_SPIN_FRAMES[frame % ELAPSED_SPIN_FRAMES.length]} ${fmtElapsed(Date.now() - start)}`;
    el.textContent = shown;
  };
  render();
  let timer = setInterval(() => { frame++; render(); }, ELAPSED_SPIN_MS);
  return {
    setStart(ms) { start = ms; if (timer) render(); },
    stop() {
      if (!timer) return;
      clearInterval(timer);
      timer = null;
      if (el.textContent === shown) el.textContent = '';
    },
  };
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
  // A code may carry its data behind the unit separator:
  // "ERR_GIT_FAILED_OUT\x1fgit push\x1f<output>" fills {0} and {1} of the
  // translation. That separator appears in neither a file name nor git's
  // output, so nothing can be mistaken for one. Without data the split
  // yields a single element and this behaves exactly as before.
  const [code, ...params] = codeOrText.split('\x1f');
  return t(`errors.${code}`, ...params);
}

// ── Reading this machine: release (hostread.py) ─────────────────────────────
// Three releases, all off until the user switches them on (Martin,
// 2026-11-21): read_host_validator, read_host_hardware,
// read_host_generations. One way for every place that offers a release: a
// button with data-host-read="<kind>". The click switches the app setting
// on, replaces the button by a short confirmation and sends the event
// "host-read-released" (detail = kind) up the page, so the place can reload.

/** Switch one release on. Resolves true when stored. */
async function releaseHostRead(kind) {
  try {
    const r = await csrfFetch('/api/app/settings', {
      method:  'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify({ [kind]: true }),
    });
    return r.ok;
  } catch { return false; }
}

/** HTML of a release button for `kind`. */
function hostReadButton(kind) {
  return `<button type="button" class="btn-surface btn-small" data-host-read="${escHtml(kind)}"
            style="margin-top:6px">${escHtml(t('hostRead.release'))}</button>`;
}

document.addEventListener('click', async e => {
  const btn = e.target.closest?.('[data-host-read]');
  if (!btn) return;
  const kind = btn.dataset.hostRead;
  btn.disabled = true;
  if (!await releaseHostRead(kind)) {
    btn.disabled = false;
    showToast(t('toast.error'), 'error');
    return;
  }
  const note = document.createElement('div');
  note.className = 'hint';
  note.style.marginTop = '6px';
  note.textContent = t('hostRead.released');
  btn.replaceWith(note);
  note.dispatchEvent(new CustomEvent('host-read-released', { bubbles: true, detail: kind }));
  document.querySelectorAll(`input[data-host-read-setting="${kind}"]`)
    .forEach(cb => { cb.checked = true; });
});

// ── NiCo dialogs instead of the browser's own ─────────────────────────────
// confirm(), alert() and prompt() look like the browser, not like NiCo, and
// differ per browser (VM test 2026-11-21). One small dialog for all of them,
// built like every other dynamic overlay (.overlay > .dialog). Closed by its
// buttons only, never by a click beside it; Enter confirms, Escape cancels.

/** Text with \n\n as paragraphs and \n as line breaks, escaped. */
function _dialogText(text) {
  return String(text ?? '').split(/\n{2,}/)
    .map(p => `<p class="dialog-info">${escHtml(p).replace(/\n/g, '<br>')}</p>`).join('');
}

/**
 * The one dialog behind nicoConfirm/nicoAlert/nicoPrompt. Resolves with
 * true/false (confirm, alert), the entered text or null (prompt).
 */
function _nicoDialog({ title = '', text = '', okLabel, cancel = true, danger = false,
                       input = null }) {
  return new Promise(resolve => {
    const overlay = document.createElement('div');
    overlay.className = 'overlay';
    overlay.innerHTML = `
      <div class="dialog">
        ${title ? `<h2 class="dialog-title">${escHtml(title)}</h2>` : ''}
        ${_dialogText(text)}
        ${input !== null ? `<input type="text" class="nico-dialog-input">` : ''}
        <div class="dialog-actions">
          ${cancel ? `<button type="button" class="btn-secondary" data-nd="cancel">${escHtml(t('dialog.cancel'))}</button>` : ''}
          <button type="button" class="${danger ? 'action-btn btn-red' : 'btn-primary'}" data-nd="ok">${escHtml(okLabel || t('dialog.ok'))}</button>
        </div>
      </div>`;
    document.body.appendChild(overlay);
    const field = overlay.querySelector('.nico-dialog-input');
    if (field) field.value = input;
    const done = ok => {
      document.removeEventListener('keydown', onKey, true);
      overlay.remove();
      resolve(input !== null ? (ok ? field.value : null) : ok);
    };
    const onKey = e => {
      if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); done(false); }
      else if (e.key === 'Enter') { e.preventDefault(); e.stopPropagation(); done(true); }
    };
    document.addEventListener('keydown', onKey, true);
    overlay.querySelectorAll('[data-nd]').forEach(b =>
      b.addEventListener('click', () => done(b.dataset.nd === 'ok')));
    (field || overlay.querySelector('[data-nd="ok"]')).focus();
    field?.select();
  });
}

/** Yes/no question. Resolves true on OK. `danger` paints OK red. */
function nicoConfirm(text, { title = '', okLabel = '', danger = false } = {}) {
  return _nicoDialog({ title, text, okLabel, danger });
}

/** A message with one OK button. */
function nicoAlert(text, { title = '' } = {}) {
  return _nicoDialog({ title, text, cancel: false });
}

/** One line of text. Resolves with it, or null when cancelled. */
function nicoPrompt(text, value = '', { title = '' } = {}) {
  return _nicoDialog({ title, text, input: String(value ?? '') });
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
    if (code !== 'en') await loadLang('en');
    return;
  }
  applyTranslations();
  const btn = document.getElementById('lang-current-btn');
  if (btn) btn.textContent = LANG_FLAGS[code] || code.toUpperCase();
  document.getElementById('lang-dropdown')?.classList.add('hidden');
}

/** Start language without a stored choice: the first browser language we
 *  ship (de-AT → de), otherwise English. */
function browserLang(langs) {
  for (const tag of (navigator.languages || [navigator.language || ''])) {
    const code = String(tag).toLowerCase().split('-')[0];
    if (langs.includes(code)) return code;
  }
  return 'en';
}

/** A language the user picked: shown at once and kept in the app settings
 *  (settings.json), so it survives another browser or cleared site data. */
async function chooseLang(code) {
  await loadLang(code);
  await csrfFetch('/api/app/settings', {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ language: currentLang }),
  }).catch(() => {});
}

/** Start language. The app settings decide; empty there means nothing was
 *  chosen, then the browser language applies. Up to 0.23.0 the choice lived
 *  only in localStorage, while settings.json held the old default "de" – so
 *  a choice found in this browser wins once and moves into the settings.
 *  The marker keeps a stale value from overriding a later choice made in
 *  another browser. */
async function startLang(langs) {
  const app = await fetch('/api/app/settings').then(r => r.json()).catch(() => ({}));
  const saved = langs.includes(app.language) ? app.language : '';
  let stored = null, moved = false;
  try {
    stored = localStorage.getItem('nico_lang');
    moved  = localStorage.getItem('nico_lang_moved') === '1';
  } catch {}
  if (stored && !moved && langs.includes(stored)) {
    await chooseLang(stored);
  } else {
    await loadLang(saved || browserLang(langs));
  }
  try { localStorage.setItem('nico_lang_moved', '1'); } catch {}
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
      b.addEventListener('click', () => chooseLang(code));
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
      b.addEventListener('click', () => chooseLang(code));
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
// Digest of the content the CO form was filled from (outside.py). Sent back
// with every save: a change on disk since then is asked about, also when
// another tab has loaded the newer file in the meantime.
let _coBase        = null;

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
  // Available languages first: they decide the start language and build the
  // lang switcher
  const langs = await fetch('/api/langs').then(r => r.json()).catch(() => ['de', 'en']);
  await startLang(langs);
  await loadSections();
  initLangSwitcher(langs);
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

