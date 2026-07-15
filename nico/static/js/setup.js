/** NiCo frontend — setup flow: directory browser, first-run setup, symlink step, import overlay and manual import. Split from app.js; classic script sharing the global scope. */
'use strict';

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
        _gitSync       = data.git_sync !== false;
        _gitStatusOnly = !!data.git_status_only;
        if (_gitSync) {
          const ok = await ensureGitStartGuard(data.nixos_config_dir);
          if (!ok) return;
        }
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
    // Import anbieten (auch ohne /etc/nixos); Symlink-Frage erst nach Schließen des Dialogs
    await showImportOverlay(false);
    await _waitForOverlayHidden('import-overlay');
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
  // First-run import offer – only when the directory has no configuration.nix/flake.nix
  // yet (same needs_import rule as on restart); skipImport=true when the import was
  // already offered before the symlink step
  if (!skipImport) {
    try {
      const st = await (await csrfFetch('/api/status')).json();
      if (st.needs_import) await showImportOverlay(false);
    } catch (e) { console.warn('status check for import offer failed:', e); }
  }

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
 * requireEtcNixos=true  → Dialog nur wenn /etc/nixos/configuration.nix da ist (derzeit ungenutzt)
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

/** Resolves once the overlay has the 'hidden' class (immediately if already hidden). */
function _waitForOverlayHidden(id) {
  return new Promise(resolve => {
    const el = document.getElementById(id);
    if (!el || el.classList.contains('hidden')) { resolve(); return; }
    const obs = new MutationObserver(() => {
      if (el.classList.contains('hidden')) { obs.disconnect(); resolve(); }
    });
    obs.observe(el, { attributes: true, attributeFilter: ['class'] });
  });
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
      Sidebar.loadTree();
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
    Sidebar.loadTree();
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
    Sidebar.loadTree();
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


