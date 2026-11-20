/** NiCo frontend — config load/save/write, autosave, ZIP export/import, settings import, detach, files changed outside NiCo. Split from app.js; classic script sharing the global scope. */
'use strict';

// ── Config load / save ─────────────────────────────────────────────────────
async function loadConfig() {
  await refreshHostsDir();
  _activeHost = '';
  _brixTargetFile  = 'configuration.nix';
  _brixContextFile = 'configuration.nix';
  _brixTargetFtype = 'co';
  _brixContextFtype = 'co';
  // Changed outside NiCo (Fall A): ask before the form takes the file. After
  // "open as raw" – or when restructuring failed – the file view takes over,
  // and asks again if it still has to.
  try {
    const f = await csrfFetch('/api/file?path=configuration.nix').then(r => r.json());
    if (f.outside && f.panel_mode === 'r') {
      await showOutsideNotice('configuration.nix');
    } else if (f.outside && await resolveOutside('configuration.nix') !== 'restructured') {
      await Sidebar.selectFile('configuration.nix', 'configuration.nix', { force: true });
      loadHmFileList();
      return;
    }
  } catch { /* the form load below reports its own errors */ }
  await _populateCoFormFromFile('configuration.nix');
  // A configuration.nix NiCo cannot represent: the form stays empty and
  // locked (_coFormReady is false after the call above), the raw editor
  // takes its place. No preview either – it would show a config generated
  // from defaults, which is not what is on disk.
  if (await Sidebar.showIfUnmappable('configuration.nix')) {
    loadHmFileList();
    return;
  }
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

// ── flake.nix anlegen ─────────────────────────────────────────────────────
// Flake mode is "a flake.nix exists" (revision 2026-11-10, B34). The switch
// above the button only allows the flake commands; turning a classic config
// into a flake config is this explicit step.

/** Show the button only for the root configuration.nix of a config without flake.nix. */
async function refreshFlakeCreateRow(path = _brixTargetFile) {
  const row = document.getElementById('flake-create-row');
  if (!row) return;
  let show = false;
  if (path === 'configuration.nix') {
    try {
      const info = await csrfFetch('/api/flake/hosts').then(r => r.json());
      show = info.flake_mode === false;
    } catch { /* unknown state: offer nothing */ }
  }
  row.classList.toggle('hidden', !show);
}

// The name of the host is asked for here. Until 2026-11-15 the dialog only
// showed it: it came from the Hostname field in the System section, and
// nothing said so (Martin, VM run: "nowhere to enter anything"). Host and
// hostname stay one name on purpose – nixos-rebuild looks up the flake host
// named like the machine – so what is entered here goes into that field.
// The pattern is the one /api/flake/create checks.
const _FLAKE_HOST_RE = /^[a-zA-Z][a-zA-Z0-9_-]*$/;

/** Resolves to the chosen host name, or null when cancelled. */
function _confirmFlakeCreate() {
  const start = (document.getElementById('hostname')?.value || '').trim() || 'nixos';
  return new Promise(resolve => {
    const overlay = document.createElement('div');
    overlay.className = 'overlay';
    overlay.innerHTML = `
      <div class="dialog-box" style="min-width:380px;max-width:560px">
        <h2 class="dialog-title">${escHtml(t('flake.createTitle'))}</h2>
        <label for="_fc-host" style="display:block;margin:10px 0 4px">${escHtml(t('flake.createHostLabel'))}</label>
        <input type="text" id="_fc-host" value="${escHtml(start)}" autocomplete="off" spellcheck="false" style="width:100%">
        <div class="setup-hint" style="margin:4px 0 0">${escHtml(t('flake.createHostHint'))}</div>
        <div id="_fc-error" class="hidden" style="margin:4px 0 0;color:var(--red)">${escHtml(tErr('ERR_INVALID_NAME'))}</div>
        <p id="_fc-body" style="margin:14px 0 16px;white-space:pre-line"></p>
        <div class="dialog-actions">
          <button id="_fc-ok"     class="btn-primary">${escHtml(t('flake.createOk'))}</button>
          <button id="_fc-cancel" class="btn-secondary">${escHtml(t('unsaved.cancel'))}</button>
        </div>
      </div>`;
    document.body.appendChild(overlay);
    const input = overlay.querySelector('#_fc-host');
    const okBtn = overlay.querySelector('#_fc-ok');
    const sync  = () => {
      const host  = input.value.trim();
      const valid = _FLAKE_HOST_RE.test(host);
      // The steps name directory and host, so they follow what is typed.
      overlay.querySelector('#_fc-body').textContent =
        t('flake.createBody', `${_hostsDir}/${host || '…'}`, host || '…');
      overlay.querySelector('#_fc-error').classList.toggle('hidden', valid || !host);
      okBtn.disabled = !valid;
    };
    input.addEventListener('input', sync);
    sync();
    input.focus();
    input.select();
    // Closes by button only (no background click).
    const done = val => { overlay.remove(); resolve(val); };
    okBtn.addEventListener('click', () => done(input.value.trim()));
    overlay.querySelector('#_fc-cancel').addEventListener('click', () => done(null));
  });
}

async function createFlake() {
  const host = await _confirmFlakeCreate();
  if (!host) return;
  // The route takes the host from the saved config, so the name goes into
  // the form first and is written with it below.
  const field = document.getElementById('hostname');
  if (field && field.value.trim() !== host) {
    field.value = host;
    field.dispatchEvent(new Event('input', { bubbles: true }));
  }
  // The route reads the files, so what is in the form has to be on disk
  // first – otherwise the reload afterwards would drop it.
  if (!await _autoSave()) return;
  if (!await _writeNix()) return;

  const res  = await csrfFetch('/api/flake/create', {
    method:  'POST',
    headers: { 'Content-Type': 'application/json' },
    body:    '{}',
  }).catch(() => null);
  const data = res ? await res.json().catch(() => null) : null;
  if (!data?.success) {
    showToast(tErr(data?.error) || t('toast.error'), 'error');
    return;
  }
  showToast(t('flake.createDone'), 'success');
  if (data.hardware === 'kept')    showToast(t('flake.createHwKept', data.host_dir), 'warning');
  if (data.hardware === 'missing') showToast(t('flake.createHwMissing'), 'warning');
  await loadConfig();
  Sidebar.loadTree();
}

document.addEventListener('DOMContentLoaded', () => {
  document.getElementById('flake-create-btn')?.addEventListener('click', createFlake);
});

/** Re-read everything an import changed on disk.
 *
 * Deliberately not location.reload(), although the settings import and detach
 * do that: an import writes its files *after* the safety commit, so they are
 * still uncommitted when this runs. A reload would start checkStatus() →
 * ensureGitStartGuard(), the start check would report `dirty`, and the guard
 * would offer to discard exactly the files just imported.
 *
 * One implementation for all five import paths (admin directory, admin ZIP,
 * /etc/nixos from the setup overlay, and the two manual ones) – each of them
 * used to forget something else.
 */
async function refreshAfterImport() {
  await categorizeFiles();
  await loadConfig();
  await Sidebar.loadTree();
  // The tree highlight may still point at a file the import removed.
  Sidebar.setActiveFile('configuration.nix', 'configuration.nix');
  updateFsHint();
  checkFileFormat();
  // The import commits before writing and may have created the repository,
  // so the git buttons are stale either way.
  if (_gitSync || _gitStatusOnly) checkGitStatus();
  if (_gitSync) checkGitRemoteStatus();
}

// ── Before the panel restructures a file (Fall A) ────────────────────────
// Two ways in (Martin, 2026-11-18):
//  * opening a file whose hash is wrong or missing (/api/file `outside`):
//    NiCo did not write what is on disk;
//  * switching from raw mode to the panel when the panel would write the
//    file differently (/api/file/panel-check) – raw save recomputes the
//    hash, so only the comparison sees edits from the raw editor.
// The panel would turn settings the form does not know into Nix-Bricks,
// comments into imported-notes, and drop what it cannot place at all.
// Nothing is written before the user answers. In raw mode a changed hash
// is only a notice: the raw editor shows the file as it is.

const _restructurePending = {};

/** Ask once per file, also when two places open it at the same time.
 *  opts.switching: called from the raw → panel switch ("stay in raw mode").
 *  opts.outside:   the hash says it was changed outside NiCo.
 *  Resolves 'restructured', 'raw', or null when the step failed. */
function resolveOutside(rel, opts = {}) {
  _restructurePending[rel] ??= _askRestructure(rel, opts)
    .finally(() => { delete _restructurePending[rel]; });
  return _restructurePending[rel];
}

async function _askRestructure(rel, { switching = false, outside = true } = {}) {
  const overlay = document.getElementById('restructure-overlay');
  const diffEl  = document.getElementById('restructure-diff');
  const rawBtn  = document.getElementById('restructure-raw-btn');
  document.getElementById('restructure-file').textContent = rel;
  document.getElementById('restructure-outside-note').classList.toggle('hidden', !outside);
  rawBtn.textContent = t(switching ? 'restructure.rawStayBtn' : 'restructure.rawBtn');
  diffEl.innerHTML = '';
  diffEl.classList.add('hidden');
  overlay.classList.remove('hidden');
  // No closing by a click on the background and no cancel: until there is
  // an answer, the panel must not write the file.
  const choice = await new Promise(resolve => {
    const okBtn   = document.getElementById('restructure-ok-btn');
    const diffBtn = document.getElementById('restructure-diff-btn');
    const done = c => {
      okBtn.onclick = rawBtn.onclick = diffBtn.onclick = null;
      overlay.classList.add('hidden');
      resolve(c);
    };
    okBtn.onclick  = () => done('restructure');
    rawBtn.onclick = () => done('raw');
    diffBtn.onclick = async () => {
      if (!diffEl.classList.contains('hidden')) { diffEl.classList.add('hidden'); return; }
      try {
        const data = await fetch(`/api/file/restructure-diff?path=${encodeURIComponent(rel)}`)
          .then(r => r.json());
        diffEl.innerHTML = data.error ? escHtml(tErr(data.error))
                                      : _renderDiff(data, t('diff.restructureNone'));
      } catch {
        diffEl.textContent = t('toast.error');
      }
      diffEl.classList.remove('hidden');
    };
    okBtn.focus();
  });
  const post = (url, body) => csrfFetch(url, {
    method:  'POST',
    headers: { 'Content-Type': 'application/json' },
    body:    JSON.stringify(body),
  }).then(r => r.json()).catch(() => null);
  const fail = d => { showToast(tErr(d?.error) || t('toast.error'), 'error'); return null; };

  if (choice === 'raw') {
    if (switching) return 'raw';            // already raw: nothing to write
    const d = await post('/api/file/panel-mode', { path: rel, mode: 'r' });
    return d?.ok ? 'raw' : fail(d);
  }
  if (switching) {
    // Into panel mode first: the route checks the brick markers and that a
    // panel fits the file at all, and its write is the checkpoint of the
    // raw state.
    const m = await post('/api/file/panel-mode', { path: rel, mode: 'p' });
    if (!m?.ok) {
      if (m?.error === 'ERR_BRIX_INCOMPLETE') { showToast(t('panelToggle.brixError'), 'error'); return null; }
      return fail(m);
    }
  }
  const d = await post('/api/file/restructure', { path: rel });
  if (!d?.success) return fail(d);
  if (d.backup === 'zip')      showToast(t('restructure.doneZip', d.zip), 'success');
  else if (d.backup === 'git') showToast(t('restructure.doneGit'), 'success');
  return 'restructured';
}

/** Raw mode, hash wrong: say so, nothing else to decide. Button only. */
function showOutsideNotice(rel) {
  return new Promise(resolve => {
    const overlay = document.createElement('div');
    overlay.className = 'overlay';
    overlay.id = 'outside-notice-overlay';
    overlay.innerHTML = `
      <div class="modal">
        <div class="modal-logo">${escHtml(t('outside.title'))}</div>
        <p class="raw-panel-hint">${escHtml(rel)}</p>
        <p>${escHtml(t('restructure.rawNotice'))}</p>
        <div class="confirm-buttons">
          <button type="button" id="outside-notice-ok" class="btn-primary">OK</button>
        </div>
      </div>`;
    document.body.appendChild(overlay);
    const ok = overlay.querySelector('#outside-notice-ok');
    ok.addEventListener('click', () => { overlay.remove(); resolve(); });
    ok.focus();
  });
}

// ── File changed outside NiCo (outside.py) ───────────────────────────────
// The form still holds the values from when it was loaded; saving would write
// them back over the newer file. The user decides (Martin, 2026-11-17).

/** Ask what to do. Resolves 'reload' or 'overwrite'; "show differences"
 *  opens the diff inside the dialog and keeps it open. No closing by a click
 *  on the background – only the buttons decide. */
function askChangedOutside(rel) {
  const overlay = document.getElementById('outside-overlay');
  const diffEl  = document.getElementById('outside-diff');
  document.getElementById('outside-file').textContent = rel;
  diffEl.innerHTML = '';
  diffEl.classList.add('hidden');
  overlay.classList.remove('hidden');
  return new Promise(resolve => {
    const reloadBtn = document.getElementById('outside-reload-btn');
    const overBtn   = document.getElementById('outside-overwrite-btn');
    const diffBtn   = document.getElementById('outside-diff-btn');
    const done = choice => {
      reloadBtn.onclick = overBtn.onclick = diffBtn.onclick = null;
      overlay.classList.add('hidden');
      resolve(choice);
    };
    reloadBtn.onclick = () => done('reload');
    overBtn.onclick   = () => done('overwrite');
    diffBtn.onclick   = async () => {
      if (!diffEl.classList.contains('hidden')) { diffEl.classList.add('hidden'); return; }
      try {
        const data = await fetch(`/api/file/outside-diff?path=${encodeURIComponent(rel)}&base=${encodeURIComponent(_coBase || '')}`)
          .then(r => r.json());
        diffEl.innerHTML = data.error ? escHtml(tErr(data.error))
                                      : _renderDiff(data, t('diff.outsideNone'));
      } catch {
        diffEl.textContent = t('toast.error');
      }
      diffEl.classList.remove('hidden');
    };
    reloadBtn.focus();
  });
}

/** POST the CO form; on ERR_CHANGED_OUTSIDE ask and act. Returns the
 *  response data, `{reloaded: true}` after "reload", or null on a network
 *  error. After "reload" the calling action stops: carrying on with values
 *  the user has not seen yet would surprise more than starting it again. */
async function _postCoForm(url, payload) {
  const send = async body => {
    const res = await csrfFetch(url, {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify({ ...body, _base: _coBase }),
    }).catch(() => null);
    const data = res ? await res.json().catch(() => null) : null;
    // The file as this save left it is the form's new base.
    if (data?.success && data._base) _coBase = data._base;
    return data;
  };
  const data = await send(payload);
  if (data?.error !== 'ERR_CHANGED_OUTSIDE') return data;
  const choice = await askChangedOutside(data.file);
  if (choice === 'overwrite') return await send({ ...payload, _overwrite: true });
  await _populateCoFormFromFile(data.file);
  document.dispatchEvent(new CustomEvent('nico:config-clean'));
  schedulePreviewUpdate();
  showToast(t('outside.reloaded'), 'success');
  return { reloaded: true };
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

  const data = await _postCoForm(url, payload);
  if (!data) { showToast(t('toast.error'), 'error'); return false; }
  if (data.reloaded) return false;
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

  const data = await _postCoForm(url, payload);
  if (!data || data.reloaded) return false;
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

  // Foreign-file guard: pending foreign files need a decision before the commit
  if (!await checkForeignFilesBeforeCommit()) return;

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
    if (data.skipped_large?.length) _showSkippedLargeModal(data.skipped_large);
    reportNestedBricks(data.nested);
  } else {
    showToast(tErr(data.error) || t('toast.error'), 'error');
  }
}

// ── Export ZIP ─────────────────────────────────────────────────────────────
async function exportZip() {
  // Fetched rather than navigated to: links out of the config directory are
  // left out of the archive (revision 2026-11-18, finding 6), and the
  // response header is the only place that says which.
  const res = await fetch('/api/export').catch(() => null);
  if (!res || !res.ok) { showToast(t('toast.error'), 'error'); return; }
  let skipped = [];
  try { skipped = JSON.parse(res.headers.get('X-NiCo-Skipped-Links') || '[]'); } catch { /* none */ }
  const name = (res.headers.get('Content-Disposition') || '')
    .match(/filename="?([^";]+)"?/)?.[1] || 'nico-export.zip';
  const url = URL.createObjectURL(await res.blob());
  const a = document.createElement('a');
  a.href = url;
  a.download = name;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
  if (skipped.length) showToast(t('export.skippedLinks', skipped.join(', ')), 'warning');
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
  await refreshAfterImport();
  // A ZIP carries whatever hardware config the machine it came from had. This
  // is the way a foreign config most often arrives, so it must be settled here
  // – the start check would only see it on the next page load.
  await checkHardwareMatch(data.hardware);
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
  await refreshAfterImport();
  await checkHardwareMatch(data.hardware);
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

