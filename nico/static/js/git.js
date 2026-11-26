/** NiCo frontend — git integration: start guard, status, remote banner, gitignore, log and rollback. Split from app.js; classic script sharing the global scope. */
'use strict';

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
    body: t('git.startGuardErrorBody', check.detail ? tErr(check.detail) : 'git fetch'),
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
            errEl.textContent   = t(errKeyMap[cardId], data.message ? tErr(data.message) : '');
            errEl.style.display = '';
            btn.disabled        = false;
            btn.textContent     = origText;
          }
        } catch (e) {
          // The routes answer with codes (ERR_…); a network error has its own text.
          errEl.textContent   = e.message ? tErr(e.message) : String(e);
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

// Commits already shown in the time machine list – the offset for "Load
// more" (Martin, 2026-11-25). The server sends pages of 30.
let _gitLogShown = 0;
// Opening the admin area can start two loads at once; only the newest one
// may write the list, or the offset and the diff selects count twice.
let _gitLogGen = 0;

/** "2026-04-03 14:23:11 +0200" → "2026-04-03 14:23" */
function _gitLogDate(date) {
  const p = (date || '').split(' ');
  return p.length >= 2 ? `${p[0]} ${p[1].slice(0, 5)}` : date;
}

async function loadAdminGitLog() {
  const container = document.getElementById('admin-git-log');
  container.innerHTML = `<span class="admin-loading">${escHtml(t('admin.loading'))}</span>`;
  _gitLogShown = 0;
  await _loadGitLogPage(container, true, ++_gitLogGen);
}

async function _loadGitLogPage(container, first, gen = _gitLogGen) {
  try {
    const res  = await csrfFetch(`/api/git/log?skip=${_gitLogShown}`);
    const data = await res.json();
    if (gen !== _gitLogGen) return;   // a newer load took over
    const commits = data.commits || [];

    if (first && !commits.length) {
      container.innerHTML = `<span class="admin-empty">${escHtml(t('admin.noCommits'))}</span>`;
      return;
    }

    const fromSel = document.getElementById('diff-from-select');
    const toSel   = document.getElementById('diff-to-select');
    if (fromSel && toSel) {
      // Diff selects start fresh with the first page; later pages add.
      if (first) {
        fromSel.innerHTML = '';
        toSel.innerHTML   = `<option value="HEAD">HEAD</option>`;
      }
      for (const c of commits) {
        const label = `${c.hash.slice(0, 7)}  ${_gitLogDate(c.date)}  ${c.message.slice(0, 40)}`;
        fromSel.appendChild(Object.assign(document.createElement('option'), { value: c.hash, textContent: label }));
        toSel.appendChild(Object.assign(document.createElement('option'),   { value: c.hash, textContent: label }));
      }
      // Default: from = second commit, to = HEAD
      if (first && fromSel.options.length > 1) fromSel.selectedIndex = 1;
    }

    // Show diff button in NixOS menu when git repo has commits
    if (first && commits.length >= 2) {
      document.getElementById('nixos-diff-btn')?.classList.remove('hidden');
    }

    if (first) container.innerHTML = '';
    container.querySelector('.git-log-more')?.remove();
    for (const c of commits) {
      const shortHash = c.hash.slice(0, 7);
      const dateStr   = _gitLogDate(c.date);
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
    _gitLogShown += commits.length;

    // Older checkpoints exist: one button at the end of the list.
    if (data.more) {
      const more = document.createElement('button');
      more.type = 'button';
      more.className = 'btn-primary btn-small git-log-more';
      more.textContent = t('admin.gitLogMore');
      more.addEventListener('click', () => {
        more.disabled = true;
        _loadGitLogPage(container, false, _gitLogGen);
      });
      container.appendChild(more);
    }
  } catch (e) {
    if (first) container.innerHTML = `<span class="admin-empty">${escHtml(t('admin.gitError'))}</span>`;
    else showToast(t('admin.gitError'), 'error');
    console.error('loadAdminGitLog:', e);
  }
}

async function rollbackTo(hash, dateStr, message) {
  if (!(await nicoConfirm(
    `${hash.slice(0, 7)}  ${dateStr}\n${message}\n\n` + t('git.rollbackDetail'),
    { title: t('git.rollbackConfirm'), okLabel: t('admin.rollbackBtn'), danger: true },
  ))) return;

  const res  = await csrfFetch('/api/git/rollback', {
    method:  'POST',
    headers: { 'Content-Type': 'application/json' },
    body:    JSON.stringify({ hash }),
  });
  const data = await res.json();

  if (data.success) {
    showToast(tErr(data.message) || t('git.rollbackSuccess'), 'success');
    closeAdmin();
    await loadConfig();   // refresh form + preview to reflect restored files
    // A restored commit can carry a hardware config from before a disk change.
    await checkHardwareMatch(data.hardware);
  } else {
    showToast(tErr(data.message) || t('git.rollbackError'), 'error');
  }
}

// ── Git integration ────────────────────────────────────────────────────────
// Git-Status wird gecacht damit openWriteConfirm() darauf zugreifen kann
// Pessimistic until checkGitStatus() has answered: assuming git is there
// offers actions that can only fail – on a fresh NixOS install git is not
// even installed, and a new config directory is no repository yet.
let _gitStatus = { git_installed: false, has_git: false };

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
    // Set here, at start-up. Until now only the admin panel revealed push and
    // pull, and without a remote they were simply missing with no word why.
    const hasRemote = !!(data.has_git && data.has_remote);
    document.getElementById('nixos-git-push-btn')?.classList.toggle('hidden', !hasRemote);
    document.getElementById('nixos-git-pull-btn')?.classList.toggle('hidden', !hasRemote);
    document.getElementById('nixos-git-noremote')?.classList.toggle('hidden', !(data.has_git && !data.has_remote));
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
    // Both places that offer "Set up" follow (time machine, config settings).
    if (typeof _showGitSettingsRows === 'function') _showGitSettingsRows(true);
    loadAdminGitLog();
    showToast(t('git.initSuccess'), 'success');
  } else {
    showToast(tErr(d.message) || t('git.initError'), 'error');
  }
}

/** The .gitignore button in the config settings says "Create" or "Edit"
 *  (2026-11-25). The buttons themselves are bound once at start
 *  (initSettingsPanel) – bound here they only worked after the time machine
 *  tab had been opened, where they used to live. */
async function loadGitignoreEditor() {
  const editBtn = document.getElementById('git-gitignore-edit-btn');
  if (!editBtn) return;
  try {
    const data = await (await csrfFetch('/api/git/gitignore')).json();
    editBtn.textContent = t(data.exists ? 'admin.gitignore.editBtn' : 'admin.gitignore.createShortBtn');
    editBtn.dataset.exists = data.exists ? '1' : '';
  } catch { /* versioning not set up yet */ }

}

/** No .gitignore yet: create it with the standard entries. Otherwise edit it
 *  in a window – add the standard entries, save, or cancel. */
async function _gitignoreFlow() {
  const btn = document.getElementById('git-gitignore-edit-btn');
  const post = body => csrfFetch('/api/git/create-gitignore', body
    ? { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }
    : { method: 'POST' }).then(r => r.json());

  if (!btn?.dataset.exists) {
    const d = await post().catch(() => null);
    if (d?.success) { showToast(t('admin.gitignore.created'), 'success'); loadGitignoreEditor(); }
    else showToast(tErr(d?.message) || t('toast.error'), 'error');
    return;
  }

  const flow = openFlowDialog(t('admin.gitignore.title'));
  const node = document.createElement('div');
  node.innerHTML = `<textarea class="flow-textarea" spellcheck="false"></textarea>`;
  const ta = node.querySelector('textarea');
  const load = async () => {
    const data = await (await csrfFetch('/api/git/gitignore')).json();
    ta.value = data.content || '';
  };
  try { await load(); } catch { return _flowFailed(flow, t('toast.error')); }

  let error = '';
  for (;;) {
    const go = await flow.step({ node, error, buttons: [
      { label: t('dialog.cancel'), value: 'cancel' },
      { label: t('admin.gitignore.addBtn'), value: 'add' },
      { label: t('admin.gitignore.saveBtn'), value: 'ok', primary: true }] });
    if (go === 'cancel') return flow.close();
    error = '';
    if (go === 'add') {
      // The server merges the standard entries into the file on disk; what
      // was typed here and not saved yet is saved first, so nothing is lost.
      const saved = await post({ content: ta.value }).catch(() => null);
      const d = saved?.success ? await post().catch(() => null) : saved;
      if (!d?.success) { error = tErr(d?.message) || t('toast.error'); continue; }
      await load().catch(() => {});
      showToast(t('admin.gitignore.added'), 'success');
      continue;
    }
    const d = await post({ content: ta.value }).catch(() => null);
    if (!d?.success) { error = tErr(d?.message) || t('toast.error'); continue; }
    flow.close();
    showToast(t('admin.gitignore.saved'), 'success');
    return;
  }
}

// ── Fremddatei-Guard (foreign-file guard) ──────────────────────────────────

/** Pre-commit hook for interactive actions (Sicherungspunkt, Rebuild):
 *  if untracked foreign files are pending, ask the user first.
 *  Resolves true = continue the action, false = user aborted. */
async function checkForeignFilesBeforeCommit() {
  let data;
  try {
    const res = await csrfFetch('/api/git/foreign-files');
    data = await res.json();
  } catch { return true; }  // never block saving/rebuilding on a guard error
  if (!data.enabled || !data.pending || !data.pending.length) return true;
  return showForeignFilesDialog(data.pending, 'pending');
}

/** List dialog for foreign files. mode 'pending': include/exclude untracked
 *  files; mode 'cleanup': keep/untrack already tracked files. */
function showForeignFilesDialog(files, mode) {
  return new Promise(resolve => {
    const isCleanup = mode === 'cleanup';
    const inLabel   = t(isCleanup ? 'git.foreign.keep'    : 'git.foreign.include');
    const outLabel  = t(isCleanup ? 'git.foreign.untrack' : 'git.foreign.exclude');
    const rows = files.map((f, i) => `
      <div class="gsi-file-row" style="display:flex;gap:14px;align-items:center">
        <span style="flex:1;overflow-wrap:anywhere">${escHtml(f)}</span>
        <label style="white-space:nowrap;cursor:pointer;display:inline-flex;align-items:center;gap:6px;margin:0">
          <input type="radio" name="ff-${i}" value="in" style="margin:0">${escHtml(inLabel)}</label>
        <label style="white-space:nowrap;cursor:pointer;display:inline-flex;align-items:center;gap:6px;margin:0">
          <input type="radio" name="ff-${i}" value="out" style="margin:0" checked>${escHtml(outLabel)}</label>
      </div>`).join('');

    const overlay = document.createElement('div');
    overlay.className = 'overlay';
    overlay.innerHTML = `
      <div class="modal">
        <div class="modal-logo">${escHtml(t(isCleanup ? 'git.foreign.cleanupTitle' : 'git.foreign.title'))}</div>
        <p>${escHtml(t(isCleanup ? 'git.foreign.cleanupBody' : 'git.foreign.body'))}
          <a href="/help#k7-4" target="_blank"
             style="font-size:0.85em;color:var(--blue);text-decoration:none;opacity:0.8">${escHtml(t('git.foreign.helpLink'))}</a></p>
        <div class="gsi"><div class="gsi-block">
          <div class="gsi-block-body gsi-file-list">${rows}</div>
        </div></div>
        <p id="ff-err" style="color:var(--red);display:none;margin-top:8px"></p>
        <div class="dialog-actions">
          <button type="button" id="ff-cancel" class="btn-secondary">${escHtml(t('git.foreign.cancel'))}</button>
          <button type="button" id="ff-ok" class="btn-primary">${escHtml(t(isCleanup ? 'git.upkeep.apply' : 'git.foreign.apply'))}</button>
        </div>
      </div>`;
    document.body.appendChild(overlay);

    function _close(result) { overlay.remove(); resolve(result); }

    overlay.querySelector('#ff-cancel').addEventListener('click', () => _close(false));
    overlay.querySelector('#ff-ok').addEventListener('click', async () => {
      const ins = [], outs = [];
      files.forEach((f, i) => {
        const v = overlay.querySelector(`input[name="ff-${i}"]:checked`)?.value;
        (v === 'in' ? ins : outs).push(f);
      });
      const url  = isCleanup ? '/api/git/foreign-cleanup' : '/api/git/foreign-decide';
      const body = isCleanup ? { keep: ins, untrack: outs } : { include: ins, exclude: outs };
      try {
        const res  = await csrfFetch(url, {
          method:  'POST',
          headers: { 'Content-Type': 'application/json' },
          body:    JSON.stringify(body),
        });
        const data = await res.json();
        if (!data.success) throw new Error(data.error || '');
        _close(true);
      } catch (e) {
        const err = overlay.querySelector('#ff-err');
        err.textContent = tErr(e.message) || t('toast.error');
        err.style.display = '';
      }
    });
  });
}

// ── Zu große Dateien bereinigen ────────────────────────────────────────────

function _mb(bytes) { return (bytes / (1024 * 1024)).toFixed(0); }

/**
 * Cleanup for files too large for git. What can be done depends entirely on
 * where the file sits, so the dialog shows that per file: on disk only (just
 * remove it), in local commits (rewritable, nothing published breaks), or
 * already on the server (refused – a rewrite there breaks other machines).
 */
/** A check found nothing: one window with the result and Close. */
async function _nothingFound(title, text) {
  const flow = openFlowDialog(title);
  await flow.step({ text, buttons: [{ label: t('dialog.close'), value: 'cancel', primary: true }] });
  flow.close();
}

async function openLargeFileCleanup() {
  let rep;
  try {
    const res = await csrfFetch('/api/git/large-files');
    rep = await res.json();
  } catch { showToast(t('toast.error'), 'error'); return; }

  const fixable = [
    ...rep.worktree.map(([p, s]) => ({ path: p, size: s, where: 'worktree' })),
    ...rep.local.map(([p, s])    => ({ path: p, size: s, where: 'local' })),
  ];
  // Nothing found: said in a window, not a green toast – a toast right
  // after the click looked as if something had been done (Martin, 2026-11-25).
  if (!fixable.length && !rep.published.length) {
    await _nothingFound(t('git.large.title'), t('git.large.none'));
    return;
  }

  const row = (f, i) => `
    <div style="display:flex;gap:10px;align-items:center;margin:3px 0">
      <input type="checkbox" id="lf-${i}" checked style="margin:0">
      <label for="lf-${i}" style="flex:1;margin:0;cursor:pointer;overflow-wrap:anywhere">
        ${escHtml(f.path)} <span style="color:var(--fg-muted)">· ${_mb(f.size)} MB · ${escHtml(t(
          f.where === 'worktree' ? 'git.large.whereDisk' : 'git.large.whereLocal'))}</span>
      </label>
    </div>`;
  const publishedRows = rep.published.map(([p, s]) => `
    <div style="margin:3px 0;color:var(--fg-muted);overflow-wrap:anywhere">
      ${escHtml(p)} · ${_mb(s)} MB · ${escHtml(t('git.large.wherePublished'))}
    </div>`).join('');

  const overlay = document.createElement('div');
  overlay.className = 'overlay';
  overlay.innerHTML = `
    <div class="modal">
      <div class="modal-logo">${escHtml(t('git.large.title'))}</div>
      <p>${escHtml(t('git.large.body', rep.limit_mb))}</p>
      ${fixable.length ? `<div class="gsi"><div class="gsi-block">
        <div class="gsi-block-body">${fixable.map(row).join('')}</div>
      </div></div>
      <label style="display:flex;gap:8px;align-items:center;margin-top:10px;cursor:pointer">
        <input type="checkbox" id="lf-delete" style="margin:0">
        <span>${escHtml(t('git.large.deleteFromDisk'))}</span>
      </label>` : ''}
      ${rep.published.length ? `<p style="margin-top:12px">${escHtml(t('git.large.publishedNote'))}</p>
      <div class="gsi"><div class="gsi-block">
        <div class="gsi-block-body">${publishedRows}</div>
      </div></div>` : ''}
      <p id="lf-err" style="color:var(--red);display:none;margin-top:8px"></p>
      <div class="dialog-actions">
        <button type="button" id="lf-cancel" class="btn-secondary">${escHtml(t('git.large.cancel'))}</button>
        ${fixable.length ? `<button type="button" id="lf-ok" class="btn-primary">${escHtml(t('git.upkeep.apply'))}</button>` : ''}
      </div>
    </div>`;
  document.body.appendChild(overlay);

  await new Promise(resolve => {
    overlay.querySelector('#lf-cancel').addEventListener('click', () => { overlay.remove(); resolve(); });
    overlay.querySelector('#lf-ok')?.addEventListener('click', async () => {
      const btn   = overlay.querySelector('#lf-ok');
      const paths = fixable.filter((_, i) => overlay.querySelector(`#lf-${i}`).checked).map(f => f.path);
      if (!paths.length) { overlay.remove(); resolve(); return; }
      btn.disabled = true;
      try {
        const res = await csrfFetch('/api/git/large-files/clean', {
          method:  'POST',
          headers: { 'Content-Type': 'application/json' },
          body:    JSON.stringify({ paths, delete: overlay.querySelector('#lf-delete').checked }),
        });
        const data = await res.json();
        if (!data.success) throw new Error(data.message || '');
        overlay.remove();
        await _showCleanupResult(data);
        loadGitignoreEditor();
        loadAdminGitLog();
        resolve();
      } catch (e) {
        const err = overlay.querySelector('#lf-err');
        err.textContent = tErr(e.message) || t('toast.error');
        err.style.display = '';
        btn.disabled = false;
      }
    });
  });
}

/**
 * Result of a cleanup. The backup branch is the point of this screen: until it
 * is gone the old commits still exist, so nothing is lost – but the disk space
 * is only reclaimed once the user drops it.
 */
function _showCleanupResult(data) {
  return new Promise(resolve => {
    const lines = [];
    if (data.removed?.length) lines.push(t('git.large.resultRemoved', data.removed.join(', ')));
    if (data.deleted?.length) lines.push(t('git.large.resultDeleted', data.deleted.join(', ')));
    if (data.commit)          lines.push(t('git.large.resultCommit', data.commit));

    const overlay = document.createElement('div');
    overlay.className = 'overlay';
    overlay.innerHTML = `
      <div class="modal">
        <div class="modal-logo">${escHtml(t('git.large.resultTitle'))}</div>
        ${lines.map(l => `<p style="margin:4px 0">${escHtml(l)}</p>`).join('')}
        ${data.backup_branch ? `<p style="margin-top:10px">${escHtml(
          t('git.large.backupNote', data.backup_branch))}</p>` : ''}
        <div style="display:flex;gap:10px;justify-content:flex-end;margin-top:14px">
          <button type="button" id="lfr-keep" class="btn-surface">${escHtml(
            data.backup_branch ? t('git.large.keepBackup') : 'OK')}</button>
          ${data.backup_branch ? `<button type="button" id="lfr-drop" class="btn-primary">${escHtml(
            t('git.large.dropBackup'))}</button>` : ''}
        </div>
      </div>`;
    document.body.appendChild(overlay);

    overlay.querySelector('#lfr-keep').addEventListener('click', () => { overlay.remove(); resolve(); });
    overlay.querySelector('#lfr-drop')?.addEventListener('click', async () => {
      const btn = overlay.querySelector('#lfr-drop');
      btn.disabled = true;
      try {
        const res  = await csrfFetch('/api/git/backup-branch/drop', {
          method:  'POST',
          headers: { 'Content-Type': 'application/json' },
          body:    JSON.stringify({ branch: data.backup_branch }),
        });
        const d = await res.json();
        showToast(d.success ? t('git.large.dropped') : (tErr(d.message) || t('toast.error')),
                  d.success ? 'success' : 'error');
      } catch { showToast(t('toast.error'), 'error'); }
      overlay.remove();
      resolve();
    });
  });
}

// ── Git-Hygiene (start check) ──────────────────────────────────────────────

/** One line per problem, plus which fixes the dialog may apply directly. */
function _hygieneLine(item) {
  if (item.id === 'gitignore') {
    return item.exists
      ? t('git.hygiene.gitignoreIncomplete', item.missing.length)
      : t('git.hygiene.gitignoreMissing');
  }
  if (item.id === 'old_log') {
    return item.tracked
      ? t('git.hygiene.oldLogTracked', item.size_mb)
      : t('git.hygiene.oldLog', item.size_mb);
  }
  if (item.id === 'foreign') return t('git.hygiene.foreign', item.files.length);
  if (item.id === 'large') {
    return item.published
      ? t('git.hygiene.largePublished', item.published)
      : t('git.hygiene.large', item.worktree + item.local);
  }
  return '';
}

/**
 * Checked on every start: the validator rules covering this only ran when the
 * user opened "Prüfen" by hand, so a config could collect NiCo artefacts for
 * months without anyone noticing – until a 2.1 GB log blocked every push.
 */
async function checkGitHygiene() {
  let items;
  try {
    const res = await csrfFetch('/api/git/hygiene');
    items = (await res.json()).items || [];
  } catch { return; }
  if (!items.length) return;

  const hasForeign = items.some(i => i.id === 'foreign');
  const hasLarge   = items.some(i => i.id === 'large');
  const autoFix    = items.filter(i => i.id === 'gitignore' || i.id === 'old_log').map(i => i.id);
  const rows = items.map(i =>
    `<div style="margin:4px 0;overflow-wrap:anywhere">• ${escHtml(_hygieneLine(i))}</div>`).join('');

  const overlay = document.createElement('div');
  overlay.className = 'overlay';
  overlay.innerHTML = `
    <div class="modal">
      <div class="modal-logo">${escHtml(t('git.hygiene.title'))}</div>
      <p>${escHtml(t('git.hygiene.body'))}</p>
      <div class="gsi"><div class="gsi-block">
        <div class="gsi-block-body">${rows}</div>
      </div></div>
      <p id="hyg-err" style="color:var(--red);display:none;margin-top:8px"></p>
      <div style="display:flex;gap:10px;justify-content:flex-end;margin-top:14px">
        <button type="button" id="hyg-later" class="btn-surface">${escHtml(t('git.hygiene.later'))}</button>
        <button type="button" id="hyg-fix" class="btn-primary">${escHtml(t('git.hygiene.fix'))}</button>
      </div>
    </div>`;
  document.body.appendChild(overlay);

  await new Promise(resolve => {
    overlay.querySelector('#hyg-later').addEventListener('click', () => { overlay.remove(); resolve(); });
    overlay.querySelector('#hyg-fix').addEventListener('click', async () => {
      const btn = overlay.querySelector('#hyg-fix');
      btn.disabled = true;
      try {
        if (autoFix.length) {
          const res  = await csrfFetch('/api/git/hygiene/fix', {
            method:  'POST',
            headers: { 'Content-Type': 'application/json' },
            body:    JSON.stringify({ actions: autoFix }),
          });
          const data = await res.json();
          if (!data.success) throw new Error((data.failed || []).join(', '));
        }
        overlay.remove();
        showToast(t('git.hygiene.done'), 'success');
        loadGitignoreEditor();
        // Both of these need a decision per file, so they get their own dialog
        // instead of NiCo guessing on the user's behalf. One after the other,
        // never stacked.
        if (hasLarge)   await openLargeFileCleanup();
        if (hasForeign) await openForeignCleanup();
        resolve();
      } catch (e) {
        const err = overlay.querySelector('#hyg-err');
        err.textContent = tErr(e.message) || t('toast.error');
        err.style.display = '';
        btn.disabled = false;
      }
    });
  });
}

/** Cleanup dialog for tracked foreign files (Zeitmaschine button). */
async function openForeignCleanup() {
  let data;
  try {
    const res = await csrfFetch('/api/git/foreign-files');
    data = await res.json();
  } catch { showToast(t('toast.error'), 'error'); return; }
  if (!data.tracked || !data.tracked.length) {
    await _nothingFound(t('git.foreign.cleanupTitle'), t('git.foreign.cleanupNone'));
    return;
  }
  const done = await showForeignFilesDialog(data.tracked, 'cleanup');
  if (done) {
    showToast(t('git.foreign.cleanupDone'), 'success');
    loadGitignoreEditor();
    loadAdminGitLog();
  }
}

function _updateGitStatusLabel(data) {
  const label   = document.getElementById('git-status-label');
  const initBtn = document.getElementById('git-init-btn');
  if (!label) return;
  // Only something to say without a working repository (2026-11-25): with
  // one, the list of checkpoints below says it all.
  document.getElementById('git-status-row')
    ?.classList.toggle('hidden', !!(data.git_installed && data.has_git));
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

// ── Time machine, System side: system states (generations) ─────────────────
// Decision 2026-11-21: the time machine has two sides. Config – checkpoints
// (commits), Restore – is the list above. System – the generations Nix keeps
// of the built system, Rollback – is read only with the release
// read_host_generations (hostread.py) and switched in the rebuild monitor.

// The two sides are the admin sub-tabs everyone uses (ui.js); the System side
// loads its list when it is shown.
document.addEventListener('subtab-shown', e => {
  if (e.detail === 'tm-system') loadGenerations();
});

document.addEventListener('host-read-released', e => {
  if (e.detail === 'read_host_generations'
      && !document.getElementById('tm-system')?.classList.contains('hidden')) {
    loadGenerations();
  }
});

async function loadGenerations() {
  const body = document.getElementById('generations-body');
  if (!body) return;
  body.innerHTML = `<span class="admin-loading">${escHtml(t('admin.loading'))}</span>`;
  let data;
  try {
    data = await fetch('/api/generations').then(r => r.json());
  } catch {
    body.innerHTML = `<span class="admin-empty">${escHtml(t('toast.error'))}</span>`;
    return;
  }
  if (!data.allowed) {
    body.innerHTML = `<p class="admin-hint">${escHtml(t('generations.off'))}</p>`
                   + hostReadButton('read_host_generations');
    return;
  }
  const gens = data.generations || [];
  if (!gens.length) {
    body.innerHTML = `<span class="admin-empty">${escHtml(t('generations.empty'))}</span>`;
    return;
  }
  // The same row as a checkpoint on the Config side (rollback-*). At the end
  // of every row one box of the same width with exactly one state (Martin,
  // 2026-11-21, replaces the badges running / booted / next start):
  //   green  - this state runs. A plain mark while it also starts next; a
  //            button when another state is chosen for the next start - one
  //            click and the next start is this one again, yellow is gone.
  //   yellow - chosen for the next start, not running.
  //   red    - button "use this state".
  const rows = gens.map(g => {
    const what = [g.nixos_version && `NixOS ${g.nixos_version}`, g.kernel && `Linux ${g.kernel}`]
      .filter(Boolean).join(' · ');
    let state;
    if (g.running && g.default) {
      state = `<span class="rollback-btn gen-state gen-state-running">${escHtml(t('generations.stateRunning'))}</span>`;
    } else if (g.running) {
      state = `<button type="button" class="rollback-btn gen-state gen-state-running" data-gen-back="${g.number}"
                 title="${escHtml(t('generations.backTitle'))}">${escHtml(t('generations.stateRunning'))}</button>`;
    } else if (g.default) {
      state = `<span class="rollback-btn gen-state gen-state-next">${escHtml(t('generations.stateNext'))}</span>`;
    } else {
      state = `<button type="button" class="rollback-btn gen-state" data-gen="${g.number}">${escHtml(t('generations.useBtn'))}</button>`;
    }
    return `<div class="rollback-item">
      <span class="rollback-hash">#${g.number}</span>
      <span class="rollback-msg" title="${escHtml(what)}">${escHtml(what)}</span>
      <span class="rollback-date">${escHtml(g.date)}</span>
      ${state}
    </div>`;
  }).join('');
  // No separate rollback button (Martin, 2026-11-21): the previous state is
  // a row like any other, one click.
  body.innerHTML = `<div class="rollback-list">${rows}</div>`;
  body.querySelectorAll('[data-gen]').forEach(btn =>
    btn.addEventListener('click', () => _useGeneration(Number(btn.dataset.gen))));
  body.querySelectorAll('[data-gen-back]').forEach(btn =>
    btn.addEventListener('click', () => _keepRunningGeneration(Number(btn.dataset.genBack),
                                                                gens.find(g => g.default)?.number)));
}

/** Set by a switch started here: when the monitor closes, back to the list. */
let _returnToGenerations = false;

/** The running state starts next again - the chosen one is dropped. Now or
 *  from the next start is the same here, so only a short question. */
async function _keepRunningGeneration(number, chosen) {
  if (!(await nicoConfirm(t('generations.backConfirm', number, chosen ?? '?')))) return;
  await _startGenerationSwitch(number, 'boot');
}

/** Ask now / from the next start, then switch in the rebuild monitor. */
async function _useGeneration(number) {
  const mode = await new Promise(resolve => {
    const overlay = document.createElement('div');
    overlay.className = 'overlay';
    overlay.innerHTML = `
      <div class="dialog">
        <h2 class="dialog-title">${escHtml(t('generations.useTitle', number))}</h2>
        <p class="dialog-info">${escHtml(t('generations.useInfo'))}</p>
        <div class="dialog-actions">
          <button type="button" class="btn-secondary" data-mode="">${escHtml(t('unsaved.cancel'))}</button>
          <button type="button" class="btn-secondary" data-mode="boot">${escHtml(t('generations.modeBoot'))}</button>
          <button type="button" class="btn-primary" data-mode="switch">${escHtml(t('generations.modeSwitch'))}</button>
        </div>
      </div>`;
    document.body.appendChild(overlay);
    // Closed by its buttons only, never by a click beside it.
    overlay.querySelectorAll('[data-mode]').forEach(b => b.addEventListener('click', () => {
      overlay.remove();
      resolve(b.dataset.mode || null);
    }));
  });
  if (!mode) return;
  await _startGenerationSwitch(number, mode);
}

/** Switch in the rebuild monitor; the list shows again when it closes. */
async function _startGenerationSwitch(number, mode) {
  const sudoNonce = await acquireSudoNonce();
  if (sudoNonce === null) return;
  const res = await csrfFetch('/api/generations/switch', {
    method:  'POST',
    headers: { 'Content-Type': 'application/json' },
    body:    JSON.stringify({ number, mode, sudo_nonce: sudoNonce }),
  }).catch(() => null);
  const data = res ? await res.json().catch(() => null) : null;
  if (!data?.success) {
    if (handleBusy(data?.error)) return;
    showToast(tErr(data?.error) || t('toast.error'), 'error');
    return;
  }
  _returnToGenerations = true;
  closeAdmin();
  attachRebuildMonitor();
}
