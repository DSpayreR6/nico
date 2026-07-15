/** NiCo frontend — config load/save/write, autosave, ZIP export/import, settings import, detach, integrity warning. Split from app.js; classic script sharing the global scope. */
'use strict';

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
  Sidebar.loadTree();
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

