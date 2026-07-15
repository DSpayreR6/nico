/** NiCo frontend — admin panel: settings tabs, validation, sections config, import tools. Split from app.js; classic script sharing the global scope. */
'use strict';

// ── Admin-Bereich ──────────────────────────────────────────────────────────
let _activeAdminTab = 'einstellungen';

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

    // Fremddatei-Guard-Toggle (config.json, auto-save on change)
    const fgToggle = document.getElementById('foreign-guard-toggle');
    if (fgToggle) {
      fgToggle.checked = data.git_foreign_guard !== false;
      if (!fgToggle.dataset.listenerAttached) {
        fgToggle.dataset.listenerAttached = '1';
        fgToggle.addEventListener('change', () => {
          csrfFetch('/api/config/settings', {
            method:  'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body:    JSON.stringify({ git_foreign_guard: fgToggle.checked }),
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
