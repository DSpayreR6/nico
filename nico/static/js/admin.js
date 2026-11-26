/** NiCo frontend — admin panel: settings tabs, validation, sections config, import tools. Split from app.js; classic script sharing the global scope. */
'use strict';

// ── Admin-Bereich ──────────────────────────────────────────────────────────
let _activeAdminTab = 'einstellungen';

function openAdmin() {
  _autoSave();
  Sidebar.flakeSave();
  // Groups in the settings start closed on every opening (Martin, 2026-11-25).
  document.querySelectorAll('.settings-group').forEach(g => g.classList.add('collapsed'));
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
    // Reading this machine (hostread.py): three releases, each saved at once.
    document.querySelectorAll('input[data-host-read-setting]').forEach(cb => {
      const key = cb.dataset.hostReadSetting;
      cb.checked = data[key] === true;
      if (cb.dataset.listenerAttached) return;
      cb.dataset.listenerAttached = '1';
      cb.addEventListener('change', () => {
        csrfFetch('/api/app/settings', {
          method:  'PATCH',
          headers: { 'Content-Type': 'application/json' },
          body:    JSON.stringify({ [key]: cb.checked }),
        }).then(r => { if (!r.ok) throw new Error(); showToast(t('admin.settings.saved'), 'success'); })
          .catch(() => { cb.checked = !cb.checked; showToast(t('toast.error'), 'error'); });
      });
    });

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

    // Remote: a read-only line with "Set up"; the form is a window
    // (_remoteFlow, 2026-11-25). Push toggles only with a remote.
    csrfFetch('/api/git/remote-status').then(r => r.json()).then(rs => {
      _remoteState = rs;
      _showGitSettingsRows(!!rs.has_git);
      const cur = document.getElementById('git-remote-current');
      if (cur) cur.value = rs.remote_url || t('git.remoteNone');
      if (!rs.has_git || !rs.has_remote) return;
      // NixOS-Menü Push- und Pull-Eintrag einblenden
      document.getElementById('nixos-git-push-btn')?.classList.remove('hidden');
      document.getElementById('nixos-git-pull-btn')?.classList.remove('hidden');
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
    Sidebar.setPanelDefault(data.panel_default);
    const panelDefaultToggle = document.getElementById('panel-default-toggle');
    if (panelDefaultToggle) {
      panelDefaultToggle.checked = (data.panel_default === 'r');
      if (!panelDefaultToggle.dataset.listenerAttached) {
        panelDefaultToggle.dataset.listenerAttached = '1';
        panelDefaultToggle.addEventListener('change', () => {
          const newVal = panelDefaultToggle.checked ? 'r' : 'p';
          Sidebar.setPanelDefault(newVal);
          csrfFetch('/api/config/settings', {
            method: 'PATCH', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ panel_default: newVal }),
          }).then(() => showToast(t('admin.settings.saved'), 'success'))
            .catch(() => showToast(t('toast.error'), 'error'));
        });
      }
    }
  }).catch(() => {});
}

// ── Config settings: windows for directories and remote (2026-11-25) ───────

/** Versioning rows of the config settings: remote and upkeep only with a
 *  repository, otherwise one line with "Set up" (2026-11-25). */
function _showGitSettingsRows(hasGit) {
  document.getElementById('git-set-remote-row')?.classList.toggle('hidden', !hasGit);
  document.getElementById('git-maint-rows')?.classList.toggle('hidden', !hasGit);
  document.getElementById('git-settings-nogit')?.classList.toggle('hidden', hasGit);
}

let _remoteState = null;   // last /api/git/remote-status answer

const _DIR_FIELDS = [
  // key in config.json, read-only box in the tab, label key, default
  ['hosts_dir',   'settings-hosts-dir',   'admin.settings.hostsDir',   'hosts'],
  ['modules_dir', 'settings-modules-dir', 'admin.settings.modulesDir', 'modules'],
  ['hm_dir',      'settings-hm-dir',      'admin.settings.hmDir',      'home'],
];

/** "Change" below the directories: the three names in a window, each with
 *  a browser that starts in the config directory. They are stored as names
 *  relative to the config, so a choice outside it is refused. */
async function _dirsFlow() {
  const flow = openFlowDialog(t('admin.settings.dirsTitle'));
  const base = (document.getElementById('settings-current-path')?.value || '').replace(/\/+$/, '');
  const node = document.createElement('div');
  const msg  = document.createElement('p');
  msg.className = 'dialog-info import-err flow-error hidden';
  const inputs = {};
  for (const [key, boxId, labelKey, def] of _DIR_FIELDS) {
    const row = document.createElement('div');
    row.className = 'flow-field';
    row.innerHTML = `
      <div class="flow-field-label">${escHtml(t(labelKey))}</div>
      <div class="import-path-row">
        <input type="text" spellcheck="false" autocomplete="off">
        <button type="button" class="btn-browse" title="${escHtml(t('admin.importBrowse'))}">…</button>
      </div>`;
    const input = row.querySelector('input');
    input.value = document.getElementById(boxId)?.value || def;
    input.placeholder = def;
    row.querySelector('.btn-browse').addEventListener('click', () => {
      openDirBrowser(`${base}/${input.value.trim() || def}`, picked => {
        const p = String(picked || '').replace(/\/+$/, '');
        msg.classList.add('hidden');
        if (base && p.startsWith(base + '/')) {
          input.value = p.slice(base.length + 1);
        } else {
          msg.textContent = t('admin.settings.dirOutside');
          msg.classList.remove('hidden');
        }
      });
    });
    inputs[key] = input;
    node.appendChild(row);
  }
  node.appendChild(msg);

  let error = '';
  for (;;) {
    const go = await flow.step({ node, error, buttons: [
      { label: t('admin.settingsCancelBtn'), value: 'cancel' },
      { label: t('admin.settings.save'), value: 'ok', primary: true }] });
    if (go !== 'ok') return flow.close();
    const patch = {};
    for (const [key, , , def] of _DIR_FIELDS) patch[key] = inputs[key].value.trim() || def;
    let res, data;
    try {
      res  = await csrfFetch('/api/config/settings', {
        method:  'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body:    JSON.stringify(patch),
      });
      data = await res.json();
    } catch {
      error = t('toast.error');
      continue;
    }
    if (!res.ok || data.error) { error = tErr(data.error); continue; }
    flow.close();
    for (const [key, boxId] of _DIR_FIELDS) {
      const box = document.getElementById(boxId);
      if (box) box.value = patch[key];
    }
    _hostsDir = patch.hosts_dir;
    showToast(t('admin.settings.saved'), 'success');
    return;
  }
}

/** "Set up" next to the remote: platform, user and repository – or an own
 *  URL – in a window, with the address it will use shown below. */
async function _remoteFlow() {
  const rs   = _remoteState || {};
  const flow = openFlowDialog(t('git.setRemoteTitle'));
  const node = document.createElement('div');
  node.innerHTML = `
    <p class="dialog-info">${rs.remote_url ? '' : escHtml(t('git.setRemoteHint'))}
      <a href="/help#k7-3" target="_blank" class="flow-help-link">${escHtml(t('git.setRemoteHelpLink'))}</a></p>
    <select class="settings-path-input flow-remote-platform">
      <option value="github">GitHub</option>
      <option value="gitlab">GitLab</option>
      <option value="codeberg">Codeberg</option>
      <option value="custom">${escHtml(t('git.setRemoteCustomOption'))}</option>
    </select>
    <div class="flow-remote-fields">
      <input type="text" class="settings-path-input flow-remote-user" placeholder="benutzername" spellcheck="false" autocomplete="off">
      <span>/</span>
      <input type="text" class="settings-path-input flow-remote-repo" placeholder="nixos-config" spellcheck="false" autocomplete="off">
    </div>
    <input type="text" class="settings-path-input flow-remote-url" placeholder="git@github.com:user/nixos-config.git" spellcheck="false" autocomplete="off">
    <div class="flow-remote-preview"></div>
    <p class="admin-hint">${escHtml(t('git.setRemoteRestartHint'))}</p>`;
  const $ = sel => node.querySelector(sel);
  const HOSTS = { github: 'github.com', gitlab: 'gitlab.com', codeberg: 'codeberg.org' };
  const build = () => {
    const platform = $('.flow-remote-platform').value;
    if (platform === 'custom') return $('.flow-remote-url').value.trim();
    const user = $('.flow-remote-user').value.trim();
    const repo = $('.flow-remote-repo').value.trim();
    return user && repo ? `git@${HOSTS[platform]}:${user}/${repo}.git` : '';
  };
  const refresh = () => {
    const custom = $('.flow-remote-platform').value === 'custom';
    $('.flow-remote-fields').classList.toggle('hidden', custom);
    $('.flow-remote-url').classList.toggle('hidden', !custom);
    $('.flow-remote-preview').textContent = build();
  };
  // Prefill from the current remote: a known host is split into user and
  // repository, anything else goes into the own-URL field.
  const url = rs.remote_url || '';
  const m = url.match(/^git@(github\.com|gitlab\.com|codeberg\.org):([^/]+)\/(.+?)(?:\.git)?$/)
         || url.match(/^https?:\/\/(github\.com|gitlab\.com|codeberg\.org)\/([^/]+)\/(.+?)(?:\.git)?$/);
  if (m) {
    $('.flow-remote-platform').value = Object.keys(HOSTS).find(k => HOSTS[k] === m[1]);
    $('.flow-remote-user').value = m[2];
    $('.flow-remote-repo').value = m[3];
  } else if (url) {
    $('.flow-remote-platform').value = 'custom';
    $('.flow-remote-url').value = url;
  }
  node.addEventListener('input', refresh);
  node.addEventListener('change', refresh);
  refresh();

  let error = '';
  for (;;) {
    const go = await flow.step({ node, error, buttons: [
      { label: t('admin.settingsCancelBtn'), value: 'cancel' },
      { label: t('git.setRemoteSave'), value: 'ok', primary: true }] });
    if (go !== 'ok') return flow.close();
    const newUrl = build();
    if (!newUrl) { error = t('git.remoteMissing'); continue; }
    flow.busy(t('git.setRemoteSaving'));
    const res = await csrfFetch('/api/git/set-remote', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ url: newUrl }),
    }).catch(() => null);
    if (!res || !res.ok) { error = t('git.setRemoteError'); continue; }
    flow.close();
    showToast(t('git.setRemoteSavedRestart'), 'success');
    const cur = document.getElementById('git-remote-current');
    if (cur) cur.value = newUrl;
    _remoteState = { ...rs, remote_url: newUrl, has_remote: true };
    // Only with a repository: a remote URL can be entered without one,
    // and push/pull would then fail.
    if (rs.has_git) {
      document.getElementById('nixos-git-push-btn')?.classList.remove('hidden');
      document.getElementById('nixos-git-pull-btn')?.classList.remove('hidden');
      document.getElementById('nixos-git-noremote')?.classList.add('hidden');
    }
    document.getElementById('git-push-settings-row')?.classList.remove('hidden');
    return;
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

/** True while any other overlay (validation settings, file viewer, …) is open
 *  on top of the admin panel – pulling the panel away underneath it would
 *  leave that dialog floating over the form. */
function _adminSubdialogOpen() {
  return [...document.querySelectorAll('.overlay')]
    .some(el => el.id !== 'admin-overlay' && !el.classList.contains('hidden'));
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
  if (tab === 'einstellungen') { loadSettingsPath(); loadGitignoreEditor(); loadAdminSymlinkStatus(); }
  if (tab === 'zeitmaschine') {
    checkGitStatus(); loadAdminGitLog();
    // System is the first side (Martin, 2026-11-21) – load it when it shows.
    if (!document.getElementById('tm-system')?.classList.contains('hidden')) loadGenerations();
  }
  // (administration: nothing to load since the symlink moved to the settings)
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
  const btn     = document.getElementById('admin-symlink-btn');
  const errDiv  = document.getElementById('admin-symlink-error');
  if (!label) return;

  // The explanation (incl. the backup to /etc/nixos.bak) is behind the ⓘ of
  // the row since 2026-11-25; here only the state and the button.
  errDiv.classList.add('hidden');
  btn.classList.add('hidden');
  const removeBtn = document.getElementById('admin-symlink-remove-btn');
  removeBtn?.classList.add('hidden');
  label.textContent = '…';

  try {
    const res  = await csrfFetch('/api/symlink/status');
    const data = await res.json();
    // Remove: for any symlink with a backup to put back (2026-11-25).
    if (data.status === 'symlink' && data.has_backup) removeBtn?.classList.remove('hidden');
    if (data.status === 'symlink' && data.points_to_nico) {
      label.innerHTML = niIcon('check-circle') + ' '
                      + escHtml(t('admin.symlinkActive', data.target || ''));
    } else if (data.status === 'symlink') {
      label.innerHTML = niIcon('alert-triangle') + ' '
                      + escHtml(t('admin.symlinkOther', data.target || ''));
      // Pointing elsewhere: offer to switch it (2026-11-22).
      btn.textContent = t('admin.symlinkRetarget');
      btn.dataset.action = 'retarget';
      btn.dataset.oldTarget = data.target || '';
      btn.dataset.nicoDir = data.nico_dir || '';
      btn.classList.remove('hidden');
    } else if (data.status === 'dir') {
      label.textContent = t('admin.symlinkIsDir');
      btn.textContent = t('admin.symlinkCreate');
      btn.dataset.action = 'create';
      btn.classList.remove('hidden');
    } else if (data.status === 'missing') {
      label.textContent = t('admin.symlinkMissing');
    } else {
      label.textContent = '';
    }
  } catch (e) {
    label.textContent = '';
    console.error('loadAdminSymlinkStatus:', e);
  }
}

/** /etc/nixos points at another directory: ask, then point it at this
 *  config directory. Resolves true when switched, false on cancel or error
 *  (the error is shown in errEl). /etc/nixos.bak stays as it is. */
/** "Remove": /etc/nixos becomes its own directory again – the symlink goes,
 *  /etc/nixos.bak is put back (Martin, 2026-11-25). Needs sudo. */
async function doAdminSymlinkRemove() {
  const errDiv = document.getElementById('admin-symlink-error');
  errDiv.classList.add('hidden');
  if (!(await nicoConfirm(t('admin.adm.symlinkRemoveConfirm'),
                          { okLabel: t('admin.adm.symlinkRemoveBtn'), danger: true }))) return;
  const nonce = await acquireSudoNonce();
  if (nonce === null) return;
  try {
    const res  = await csrfFetch('/api/symlink/remove', {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify({ sudo_nonce: nonce }),
    });
    const data = await res.json();
    if (!res.ok || data.error) {
      errDiv.textContent = tErr(data.error) || t('toast.error');
      errDiv.classList.remove('hidden');
      return;
    }
    showToast(t('admin.symlinkRemoved'), 'success');
  } catch {
    errDiv.textContent = t('toast.error');
    errDiv.classList.remove('hidden');
  }
  await loadAdminSymlinkStatus();
}

async function retargetSymlink(oldTarget, nicoDir, errEl) {
  if (!(await nicoConfirm(t('symlink.retargetConfirm', oldTarget, nicoDir),
                          { okLabel: t('admin.symlinkRetarget') }))) return false;
  const nonce = await acquireSudoNonce();
  if (nonce === null) return false;
  try {
    const res  = await csrfFetch('/api/symlink/retarget', {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify({ sudo_nonce: nonce }),
    });
    const data = await res.json();
    if (!res.ok || data.error) {
      errEl.textContent = tErr(data.error) || t('toast.error');
      errEl.classList.remove('hidden');
      return false;
    }
    showToast(t('symlink.retargeted', data.target || nicoDir), 'success');
    return true;
  } catch {
    errEl.textContent = t('toast.error');
    errEl.classList.remove('hidden');
    return false;
  }
}

async function doAdminSymlink() {
  const errDiv = document.getElementById('admin-symlink-error');
  const btn    = document.getElementById('admin-symlink-btn');
  errDiv.classList.add('hidden');
  if (btn.dataset.action === 'retarget') {
    btn.disabled = true;
    await retargetSymlink(btn.dataset.oldTarget, btn.dataset.nicoDir, errDiv);
    btn.disabled = false;
    await loadAdminSymlinkStatus();
    return;
  }
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
    showToast(t('admin.symlinkCreated', '/etc/nixos.bak'), 'success');
  } catch (e) {
    errDiv.textContent = t('toast.error');
    errDiv.classList.remove('hidden');
    btn.disabled = false;
  }
}

/** "Change" next to the config path: a window with the new path, its hint
 *  and any error (Martin, 2026-11-25 – until then a row opened below). */
function initSettingsPanel() {
  on('settings-change-btn', 'click', _changeConfigPathFlow);
  on('settings-dirs-change-btn', 'click', _dirsFlow);
  on('git-remote-setup-btn', 'click', _remoteFlow);
  on('git-settings-init-btn', 'click', () => _doGitInit());
  on('git-gitignore-edit-btn', 'click', _gitignoreFlow);
  on('git-foreign-cleanup-btn', 'click', openForeignCleanup);
  on('git-large-cleanup-btn', 'click', openLargeFileCleanup);
  // Fold headers of the settings groups: click, Enter or Space.
  document.querySelectorAll('.settings-group-toggle').forEach(h => {
    const flip = () => h.closest('.settings-group').classList.toggle('collapsed');
    h.addEventListener('click', flip);
    h.addEventListener('keydown', e => {
      if (e.target === h && (e.key === 'Enter' || e.key === ' ')) { e.preventDefault(); flip(); }
    });
  });
}

async function _changeConfigPathFlow() {
  const flow = openFlowDialog(t('admin.settingsChangeTitle'));
  if (!(await _configPathSteps(flow, document.getElementById('settings-current-path')?.value || ''))) {
    flow.close();
  }
}

/** The steps of "change config path" in `flow`: path, create question,
 *  save. True when saved (window closed, page reloads), false on Cancel –
 *  the caller decides what follows (the window after a move comes back to
 *  its three choices). */
async function _configPathSteps(flow, path) {
  let error = '';
  const post = body => csrfFetch('/api/setup', {
    method:  'POST',
    headers: { 'Content-Type': 'application/json' },
    body:    JSON.stringify(body),
  }).then(r => r.json());

  for (;;) {
    const node = document.createElement('div');
    node.innerHTML = `
      <input type="text" class="settings-path-input flow-path-input" spellcheck="false" autocomplete="off">
      <p class="setup-hint">${escHtml(t('admin.settingsPathHint'))}</p>`;
    const input = node.querySelector('input');
    input.value = path;
    input.placeholder = t('admin.settingsNewPathPlaceholder');
    const go = await flow.step({ node, error, buttons: [
      { label: t('admin.settingsCancelBtn'), value: 'cancel' },
      { label: t('admin.settingsApplyBtn'), value: 'ok', danger: true }] });
    if (go !== 'ok') return false;
    path = input.value.trim();
    if (!path) { error = t('setup.errNoDir'); continue; }

    let data;
    try {
      data = await post({ nixos_config_dir: path });
      if (data.needs_confirmation) {
        const create = await flow.step({
          text: `${t('setup.confirmPrefix')} ${data.path} ${t('setup.confirmSuffix')}`,
          buttons: [{ label: t('admin.settingsCancelBtn'), value: 'cancel' },
                    { label: t('dialog.ok'), value: 'ok', primary: true }] });
        if (create !== 'ok') { error = ''; continue; }
        data = await post({ nixos_config_dir: path, create_if_missing: true });
      }
    } catch {
      error = t('toast.error');
      continue;
    }
    if (data.error) { error = tErr(data.error); continue; }
    flow.close();
    showToast(t('toast.saved'), 'success');
    setTimeout(() => location.reload(), 800);
    return true;
  }
}


// ── Validierung ────────────────────────────────────────────────────────────────

let _validationRules = [];     // Rule metadata from /api/validate/rules
let _isFlakeConfig   = false;  // from /api/flake/hosts (flake_mode) when needed

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
  // Flake rules only for a flake config – asked from the server, see runValidation.
  _isFlakeConfig = !!(await _fetchFlakeHosts()).flake_mode;

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

    // One line per rule, the description behind an ⓘ (Martin, 2026-11-25).
    // `for` on the label: without it the ⓘ button would be the labelled
    // control and a click on the line would open the text, not the switch.
    const cbId = `vrule-${rule.id}`;
    const row = document.createElement('label');
    row.className = 'toggle-row';
    row.htmlFor = cbId;
    row.style.cssText = 'margin-bottom:8px;' + (hidden ? 'display:none' : '');
    row.dataset.ruleId = rule.id;
    const descKey = `validator.rule.${rule.id}.desc`;
    const tip = t(descKey) !== descKey
      ? `<button type="button" class="info-tip" data-tip="${_esc(descKey)}" aria-label="${_esc(t('info.label'))}"><span class="ni-icon ni-icon-info" aria-hidden="true"></span></button>`
      : '';
    row.innerHTML = `
      <span class="settings-label" style="flex:1;min-width:0">
        ${dot.replace('margin-top:6px', 'margin-top:0')}<span>${_esc(tOr(`validator.rule.${rule.id}.label`, rule.label))}</span>
        ${rule.flake_only ? '<span class="rule-badge">Flake</span>' : ''}
        ${tip}
      </span>
      <span class="toggle-wrap" style="margin-left:12px;flex-shrink:0">
        <input type="checkbox" id="${cbId}" data-rule="${rule.id}" ${checked ? 'checked' : ''}>
        <span class="toggle-track"><span class="toggle-thumb"></span></span>
      </span>`;
    list.appendChild(row);
  }

  // The Flake line of the legend only where a rule carries the badge – a
  // config without flake shows no flake rules (Martin, 2026-11-25).
  document.getElementById('validator-legend-flake')?.classList.toggle('hidden',
    ![...list.querySelectorAll('.toggle-row')].some(r => r.style.display !== 'none' && r.querySelector('.rule-badge')));
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
  'BOOT', 'I18N', 'NETW', 'SERV', 'DESK', 'AUDI',
  'USER', 'PROG', 'FONT', 'NIXS', 'HOME',
  'HDWR', 'VIRT', 'FSYS',
];

function openSectionsSettings() {
  const list = document.getElementById('sections-settings-list');
  if (!list) return;
  list.innerHTML = '';

  for (const name of _ALL_SECTIONS) {
    const hidden = hiddenSections.includes(name);
    // Code plus its name in the interface language (Martin, 2026-11-25).
    // No per-section "has content" mark: the panel decides that anew for
    // every file it shows, host files included – the note below the list
    // says so once.
    const row = document.createElement('label');
    row.className = 'toggle-row';
    row.style.cssText = 'margin-bottom:8px';
    row.innerHTML = `
      <span style="flex:1">${_esc(name)} – ${_esc(secLabel(name))}</span>
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

  // For flake configs with multiple hosts, ask which host to validate.
  // Flake or not comes from the server (2026-11-25): the old flag was set
  // from a preview field the server stopped sending, so it was always false
  // and the host was never asked.
  let host = null;
  const hostInfo = await _fetchFlakeHosts();
  _isFlakeConfig = !!hostInfo.flake_mode;
  if (_isFlakeConfig) {
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

/** Validator output as a gate before a build.
 *
 * Shown only when something red came out of it. The findings come first and
 * the force button last, inside the scrolling body: whoever wants to build
 * anyway has to scroll past everything the run was stopped for. Resolves true
 * for "build anyway", false for cancel and for closing the dialog.
 *
 * `mode` names the function the force button starts, as every button here does
 * ('switch'/'boot' → the rebuild, 'dryrun' → the syntax check).
 */
function showValidationGate(findings, mode = 'switch') {
  return new Promise(resolve => {
    _showValidationResults(findings, { gate: true, mode });

    let settled = false;
    const done = (value) => {
      if (settled) return;
      settled = true;
      closeValidationResults();
      resolve(value);
    };
    document.getElementById('validation-gate-force')
      ?.addEventListener('click', () => done(true), { once: true });
    document.getElementById('validation-gate-cancel')
      ?.addEventListener('click', () => done(false), { once: true });
    // The header's x is a cancel like any other – the overlay deliberately
    // cannot be dismissed by clicking the background.
    document.getElementById('validation-results-close')
      ?.addEventListener('click', () => done(false), { once: true });
  });
}

function _showValidationResults(findings, opts = {}) {
  const body = document.getElementById('validation-results-body');
  if (!body) return;

  if (opts.gate) {
    // Red first: the reason the build was stopped should be read before the
    // rest, and the force button stays below all of it.
    const rank = { error: 0, warning: 1, info: 2 };
    findings = [...findings].sort(
      (a, b) => (rank[a.severity] ?? 3) - (rank[b.severity] ?? 3));
  }

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
      // detail_key is absent where the detail is pure data (paths, file lists,
      // error texts) – tOr then falls back to that data unchanged
      const detailText = tOr(f.detail_key, f.detail, ...secParams(f.detail_params));
      const detail = detailText
        ? `<div class="git-guard-card-desc" style="margin-top:4px;white-space:pre-line">${_esc(detailText)}</div>`
        : '';
      // Checks that need to read this machine and were not released
      // (hostread.py): the release right here; the next run checks them.
      const action = f.rule_id === 'git_missing_gitignore'
        ? `<button class="btn-surface btn-small" style="margin-top:6px;font-size:11px"
             onclick="closeValidationResults();openAdmin();_switchAdminTab('zeitmaschine')"
             data-i18n="admin.gitignore.goToSettings">${t('admin.gitignore.goToSettings')}</button>`
        : f.rule_id === 'host_read_off'
          ? hostReadButton(f.message_key === 'validator.f.host_read_off_hardware'
                           ? 'read_host_hardware' : 'read_host_validator')
          : '';
      return `<div class="git-guard-card" style="align-items:flex-start">
        <span style="color:${color};font-size:16px;flex-shrink:0;margin-top:1px">${icon}</span>
        <div class="git-guard-card-info">
          <div class="git-guard-card-title">${_esc(counter)}</div>
          <div style="font-size:13px">${_esc(tOr(f.message_key, f.message, ...secParams(f.params)))}</div>${detail}${action}
        </div>
      </div>`;
    }).join('') + '</div>';
  }

  if (opts.gate) {
    const errors = findings.filter(f => f.severity === 'error').length;
    const isDry  = opts.mode === 'dryrun';
    const notice = isDry ? t('validator.gate.noticeDryRun', errors)
                         : t('validator.gate.noticeBuild', errors);
    const force  = isDry ? t('validator.gate.forceDryRun')
                         : t('validator.gate.forceBuild');
    body.innerHTML =
      `<div class="validate-gate-notice">${_esc(notice)}</div>` +
      body.innerHTML +
      `<div class="validate-gate-actions">
         <button id="validation-gate-cancel" class="btn-surface">${_esc(t('validator.gate.cancel'))}</button>
         <button id="validation-gate-force" class="btn-surface btn-force">${_esc(force)}</button>
       </div>`;
  }

  document.getElementById('validation-results-overlay').classList.remove('hidden');
}

function closeValidationResults() {
  document.getElementById('validation-results-overlay').classList.add('hidden');
}

function _esc(s) {
  return String(s ?? '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

// ── Moving the config (Martin, 2026-11-25) ──────────────────────────────────

/** "Move": target, question, move (configmove.py), then – if /etc/nixos
 *  pointed to the old place – the offer to switch it; at the end a reload,
 *  like after changing the config path. Everything in one window. */
async function _moveConfigFlow() {
  const flow = openFlowDialog(t('admin.adm.moveTitle'));
  let current = '';
  try { current = (await (await fetch('/api/app/settings')).json()).nixos_config_dir || ''; } catch { /* shown empty */ }

  const node = document.createElement('div');
  node.innerHTML = `
    <p class="dialog-info"></p>
    <div class="import-path-row">
      <input type="text" spellcheck="false" autocomplete="off">
      <button type="button" class="btn-browse" title="${escHtml(t('admin.importBrowse'))}">…</button>
    </div>
    <p class="admin-hint">${escHtml(t('admin.adm.moveHint'))}</p>`;
  node.querySelector('.dialog-info').textContent = t('admin.adm.moveFrom', current);
  const input = node.querySelector('input');
  node.querySelector('.btn-browse').addEventListener('click', () =>
    openDirBrowser(input.value.trim() || '~', p => { input.value = p; }, null, { newHint: true }));

  let error = '';
  for (;;) {
    const go = await flow.step({ node, error, buttons: [
      { label: t('dialog.cancel'), value: 'cancel' },
      { label: t('admin.adm.moveBtn'), value: 'ok', primary: true }] });
    if (go !== 'ok') return flow.close();
    const target = input.value.trim();
    if (!target) { error = t('setup.errNoDir'); continue; }
    const sure = await flow.step({ text: t('admin.adm.moveConfirm', current, target), buttons: [
      { label: t('dialog.cancel'), value: 'cancel' },
      { label: t('admin.adm.moveBtn'), value: 'ok', primary: true }] });
    if (sure !== 'ok') { error = ''; continue; }
    // What the move does not take along or breaks (configfiles.py).
    if (!(await confirmLeftOut(flow, { all: true, move: true }))) { error = ''; continue; }
    if (!(await saveBeforeLeave())) return flow.close();

    flow.busy(t('admin.adm.moving'));
    let res, data;
    try {
      res  = await csrfFetch('/api/config/move', {
        method:  'POST',
        headers: { 'Content-Type': 'application/json' },
        body:    JSON.stringify({ target }),
      });
      data = await res.json();
    } catch {
      error = t('toast.error');
      continue;
    }
    if (!res.ok || data.error) { error = tErr(data.error); continue; }
    return _afterMove(flow, data);
  }
}

/** The window after the move (F5, Martin 2026-11-26). No cancel: the old
 *  place is gone, so one of three answers is needed – the new place, another
 *  path, or none (setup starts again). The server has already pointed NiCo
 *  to the new place, so nothing writes into the old one meanwhile; a failed
 *  save keeps the window open with the message, and "lies now in …" stays
 *  visible, so the user always knows where the config is. */
async function _afterMove(flow, data) {
  const setPath = path => csrfFetch('/api/config/path', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ path }),
  }).then(r => r.json()).catch(() => null);
  let error = data.path_saved ? '' : tErr('ERR_PATH_SAVE');
  for (;;) {
    let text = t('admin.adm.moveDone', data.target);
    if (data.source_left) text += '\n\n' + t('admin.adm.moveSourceLeft', data.old);
    const choice = await flow.step({ text, error, buttons: [
      { label: t('admin.adm.moveFresh'), value: 'fresh' },
      { label: t('admin.adm.moveOther'), value: 'other' },
      { label: t('admin.adm.moveUseNew'), value: 'new', primary: true }] });
    error = '';
    if (choice === 'new') {
      const r = await setPath(data.target);
      if (!r || r.error) { error = tErr(r?.error || 'ERR_PATH_SAVE'); continue; }
      // /etc/nixos only for the config NiCo works with from now on.
      if (data.symlink_old) await _offerSymlinkRetarget(flow);
      flow.close();
      showToast(t('admin.adm.moved', data.target), 'success');
      setTimeout(() => location.reload(), 900);
      return;
    }
    if (choice === 'other') {
      // The existing "change config path" steps; Cancel comes back here.
      if (await _configPathSteps(flow, data.target)) return;
      continue;
    }
    if (choice === 'fresh') {
      const ok = await flow.step({ text: t('admin.adm.moveFreshInfo', data.target), buttons: [
        { label: t('admin.adm.moveBack'), value: 'back' },
        { label: t('admin.adm.moveFreshStart'), value: 'ok', primary: true }] });
      if (ok !== 'ok') continue;
      const r = await setPath('');
      if (!r || r.error) { error = tErr(r?.error || 'ERR_PATH_SAVE'); continue; }
      flow.close();
      location.reload();
      return;
    }
  }
}

/** /etc/nixos pointed to the old place: offer to switch it (sudo). */
async function _offerSymlinkRetarget(flow) {
  const sw = await flow.step({ text: t('admin.adm.moveSymlink'), buttons: [
    { label: t('admin.adm.moveLater'), value: 'cancel' },
    { label: t('admin.symlinkRetarget'), value: 'ok', primary: true }] });
  if (sw !== 'ok') return;
  const nonce = await acquireSudoNonce();
  if (nonce === null) return;
  const r = await csrfFetch('/api/symlink/retarget', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ sudo_nonce: nonce }),
  }).then(x => x.json()).catch(() => null);
  if (!r || r.error) {
    await flow.step({ error: tErr(r?.error) || t('toast.error'),
                      buttons: [{ label: t('dialog.close'), value: 'cancel', primary: true }] });
  }
}
