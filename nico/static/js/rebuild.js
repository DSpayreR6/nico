/** NiCo frontend — rebuild modal, dry-run, sudo password handling. Split from app.js; classic script sharing the global scope. */
'use strict';


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
async function _showRebuildOptions(hostInfo, { hostname = '', mode = 'switch' } = {}) {
  let defaultFlakeUpdate = false;
  let defaultTerminal = false;
  let defaultSafeMode = false;
  let configDir = '';
  try {
    const [cfg, app] = await Promise.all([
      csrfFetch('/api/config/settings').then(r => r.json()),
      csrfFetch('/api/app/settings').then(r => r.json()),
    ]);
    defaultFlakeUpdate = !!cfg.flake_update_on_rebuild;
    defaultTerminal    = !!app.rebuild_terminal;
    defaultSafeMode    = !!app.rebuild_safe;
    configDir          = (app.nixos_config_dir || '').trim();
  } catch {
    defaultFlakeUpdate = !!(document.getElementById('flake-update-toggle')?.checked);
  }

  if (!hostInfo.flake_mode) return { updateFlake: false, useTerminal: false, safeMode: defaultSafeMode, shutdownAfter: false, pushShutdownAfter: false };

  // Vollständiger Pfad statt '.#host': der kopierte Befehl muss aus jedem
  // Verzeichnis heraus funktionieren (manifest.md "nächste Ziele").
  const _buildCmd = (upd, safe) => {
    const safeArgs = safe ? ' --max-jobs 1 --cores 4' : '';
    const flakeDir = configDir || '.';
    const base = `sudo nixos-rebuild ${mode} --flake ${flakeDir}#${hostname || 'hostname'}${safeArgs}`;
    return upd ? `nix flake update --flake ${flakeDir} && ${base}` : base;
  };

  return new Promise(resolve => {
    const overlay = document.createElement('div');
    overlay.className = 'overlay';
    overlay.innerHTML = `
      <div class="dialog-box" style="min-width:360px">
        <h2 class="dialog-title">${t('rebuild.optionsTitle')}</h2>
        <label class="toggle-row" style="margin:12px 0 10px;cursor:pointer">
          <span>${t('rebuild.optFlakeUpdate')}</span>
          <span class="toggle-wrap">
            <input type="checkbox" id="_rbo-flake-update" ${defaultFlakeUpdate ? 'checked' : ''}>
            <span class="toggle-track"><span class="toggle-thumb"></span></span>
          </span>
        </label>
        <label class="toggle-row" style="margin:0 0 10px;cursor:pointer">
          <span>${t('rebuild.optSafeMode')}</span>
          <span class="toggle-wrap">
            <input type="checkbox" id="_rbo-safe-mode" ${defaultSafeMode ? 'checked' : ''}>
            <span class="toggle-track"><span class="toggle-thumb"></span></span>
          </span>
        </label>
        <label class="toggle-row" style="margin:0 0 10px;cursor:pointer">
          <span>${t('rebuild.optTerminal')}</span>
          <span class="toggle-wrap">
            <input type="checkbox" id="_rbo-terminal" ${defaultTerminal ? 'checked' : ''}>
            <span class="toggle-track"><span class="toggle-thumb"></span></span>
          </span>
        </label>
        <div id="_rbo-terminal-opts" style="display:${defaultTerminal ? 'flex' : 'none'};flex-direction:column;gap:2px;margin:0 0 12px;padding-left:14px;border-left:2px solid var(--border,#444)">
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
            <code id="_rbo-cmd" style="flex:1;font-size:0.8em;background:var(--bg-code,var(--bg2));padding:6px 8px;border-radius:4px;white-space:pre-wrap;word-break:break-all"></code>
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
    const safeToggle    = overlay.querySelector('#_rbo-safe-mode');
    const termToggle    = overlay.querySelector('#_rbo-terminal');
    const termOptsEl    = overlay.querySelector('#_rbo-terminal-opts');
    const shutdownCb    = overlay.querySelector('#_rbo-shutdown');
    const pushShutCb    = overlay.querySelector('#_rbo-push-shutdown');
    const updateCmd     = () => { cmdEl.textContent = _buildCmd(flakeToggle.checked, safeToggle.checked); };
    updateCmd();
    flakeToggle.addEventListener('change', updateCmd);
    safeToggle.addEventListener('change', updateCmd);
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
      const safeMode        = !!safeToggle.checked;
      const useTerminal     = !!termToggle.checked;
      const shutdownAfter   = !!shutdownCb.checked;
      const pushShutdownAfter = !!pushShutCb.checked;
      csrfFetch('/api/app/settings', {
        method:  'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body:    JSON.stringify({ rebuild_safe: safeMode }),
      }).catch(() => {});
      overlay.remove();
      resolve({ updateFlake, safeMode, useTerminal, shutdownAfter, pushShutdownAfter });
    });
    overlay.querySelector('#_rbo-cancel').addEventListener('click', () => {
      overlay.remove();
      resolve(null);
    });
  });
}

async function openRebuild(mode = 'switch') {
  // Foreign-file guard: pending foreign files need a decision before the
  // rebuild stages/commits anything
  if (!await checkForeignFilesBeforeCommit()) return;

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

  const opts = await _showRebuildOptions(hostInfo, { hostname, mode });
  if (opts === null) return;  // abgebrochen

  // Flake-Formular speichern falls dirty, dann alle Änderungen schreiben
  if (!await Sidebar.flakeSave()) return;
  if (!await _autoSave()) return;
  if (!await _writeNix()) return;

  // Terminal-Modus: Befehl in externem Terminal ausführen
  if (opts.useTerminal) {
    const res = await csrfFetch('/api/rebuild/open-terminal', {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify({ hostname, mode, update_flake: opts.updateFlake, safe_mode: opts.safeMode, shutdown_after: opts.shutdownAfter, push_shutdown_after: opts.pushShutdownAfter }),
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
  const logBtn       = document.getElementById('rebuild-log-btn');
  const pushBtn      = document.getElementById('rebuild-push-btn');
  const pushInfoEl   = document.getElementById('rebuild-push-info');
  const counterEl    = document.getElementById('rph-build-counter');
  const buildPkgEl   = document.getElementById('rph-build-pkg');
  const fetchDoneEl  = document.getElementById('rph-fetch-done');
  const fetchRemainEl= document.getElementById('rph-fetch-remain');
  const fetchCopyEl  = document.getElementById('rph-fetch-copy');

  // Batched log rendering – prevents per-line reflow in Firefox
  const MAX_LOG_LINES = 500;
  let _logBuffer = [];
  let _logRafId  = null;

  function _resetMonitor() {
    if (_logRafId) { cancelAnimationFrame(_logRafId); _logRafId = null; }
    _logBuffer = [];
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
    logBtn.classList.add('hidden');
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
  const url = `/api/rebuild/stream?mode=${encodeURIComponent(mode)}&token=${encodeURIComponent(CSRF_TOKEN)}&update_flake=${updateFlake}&safe_mode=${opts.safeMode ? 1 : 0}${hostParam}${nonceParam}`;
  const es  = new EventSource(url);
  _rebuildES = es;

  let isRunning         = true;
  let firstErrorLine    = '';
  let hasGlobalProgress = false;
  let maxBuildTotal     = 0;
  let maxDlExpected     = 0;
  let maxCopiedTotal    = 0;
  let maxCopiedExpected = 0;
  let prefetchTotalBytes = 0;

  function _flushLog() {
    _logRafId = null;
    if (!_logBuffer.length) return;
    const frag = document.createDocumentFragment();
    for (const { text, cls } of _logBuffer) {
      const span = document.createElement('span');
      if (cls) span.className = cls;
      span.textContent = text;
      frag.appendChild(span);
    }
    _logBuffer = [];
    logEl.appendChild(frag);
    // Trim oldest lines to keep DOM lean
    while (logEl.childElementCount > MAX_LOG_LINES) {
      logEl.removeChild(logEl.firstChild);
    }
    logEl.scrollTop = logEl.scrollHeight;
  }

  function _appendLog(line) {
    let cls = '';
    if (/error:/i.test(line))        cls = 'log-error';
    else if (/warning:/i.test(line)) cls = 'log-warning';
    _logBuffer.push({ text: line + '\n', cls });
    if (!_logRafId) _logRafId = requestAnimationFrame(_flushLog);
  }

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
    counterEl.textContent  = stableTotal > 0 ? `${done || 0} von ${stableTotal} gebaut` : '';
    buildPkgEl.textContent = pkg || '';
  }

  function _setDlProgress(done, expected, copiedDone = 0, copiedTotal = 0, copiedExpected = 0, copiedLabel = '') {
    maxDlExpected = Math.max(maxDlExpected, expected || 0);
    maxCopiedTotal = Math.max(maxCopiedTotal, copiedTotal || 0);
    maxCopiedExpected = Math.max(maxCopiedExpected, copiedExpected || 0);

    const stableExpected = prefetchTotalBytes > 0
      ? Math.max(maxDlExpected, prefetchTotalBytes)
      : maxDlExpected;
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
      if (!firstErrorLine && /error:/i.test(line)) firstErrorLine = line.trim();
      _appendLog(line);

    } else if (msg.type === 'phase') {
      _setPhaseActive(msg.phase, msg.active, msg.pkg || '');

    } else if (msg.type === 'progress') {
      if (hasGlobalProgress) return;
      _setBuildProgress(msg.done, msg.total, msg.pkg || '');

    } else if (msg.type === 'dl_progress') {
      if (hasGlobalProgress) return;
      _setDlProgress(msg.done, msg.expected);

    } else if (msg.type === 'prefetch_total') {
      prefetchTotalBytes = (msg.mib || 0) * 1024 * 1024;

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
      _flushLog();
      const label = msg.success
        ? niIcon('check-circle') + ' ' + t('rebuild.success')
        : niIcon('x-circle') + ' ' + t('rebuild.failed') + (firstErrorLine ? ': ' + firstErrorLine.substring(0, 80) : '');
      _finishMonitor(msg.success, label);
      if (msg.log_written) {
        logBtn.classList.remove('hidden');
        logBtn.onclick = async () => {
          try {
            const res  = await csrfFetch('/api/rebuild/log');
            const text = await res.text();
            const pre  = document.createElement('pre');
            pre.style.margin = '0';
            pre.textContent  = text;
            openViewer(t('rebuild.openLog'), pre.outerHTML);
          } catch {
            showToast(t('toast.error'), 'error');
          }
        };
      }
      es.close();
      _rebuildES = null;
      if (msg.success) {
        csrfFetch('/api/config/settings').then(r => r.json()).then(cfg => {
          if (_gitSync && cfg.push_after_rebuild) {
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

