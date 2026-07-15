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
    body: t('git.startGuardErrorBody', check.detail || 'git fetch'),
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
            errEl.textContent   = t(errKeyMap[cardId], data.message || '');
            errEl.style.display = '';
            btn.disabled        = false;
            btn.textContent     = origText;
          }
        } catch (e) {
          errEl.textContent   = String(e);
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

async function loadAdminGitLog() {
  const container = document.getElementById('admin-git-log');
  container.innerHTML = `<span class="admin-loading">${escHtml(t('admin.loading'))}</span>`;

  try {
    const res  = await csrfFetch('/api/git/log');
    const data = await res.json();
    const commits = data.commits || [];

    if (!commits.length) {
      container.innerHTML = `<span class="admin-empty">${escHtml(t('admin.noCommits'))}</span>`;
      return;
    }

    // Populate diff selects
    const fromSel = document.getElementById('diff-from-select');
    const toSel   = document.getElementById('diff-to-select');
    if (fromSel && toSel) {
      fromSel.innerHTML = '';
      toSel.innerHTML   = `<option value="HEAD">HEAD</option>`;
      for (const c of commits) {
        const short   = c.hash.slice(0, 7);
        const dateParts = (c.date || '').split(' ');
        const dateStr   = dateParts.length >= 2 ? `${dateParts[0]} ${dateParts[1].slice(0, 5)}` : c.date;
        const label   = `${short}  ${dateStr}  ${c.message.slice(0, 40)}`;
        fromSel.appendChild(Object.assign(document.createElement('option'), { value: c.hash, textContent: label }));
        toSel.appendChild(Object.assign(document.createElement('option'),   { value: c.hash, textContent: label }));
      }
      // Default: from = second commit, to = HEAD
      if (fromSel.options.length > 1) fromSel.selectedIndex = 1;
    }

    // Show diff button in NixOS menu when git repo has commits
    if (commits.length >= 2) {
      document.getElementById('nixos-diff-btn')?.classList.remove('hidden');
    }

    container.innerHTML = '';
    for (const c of commits) {
      const shortHash = c.hash.slice(0, 7);
      // Format date: "2026-04-03 14:23:11 +0200" → "2026-04-03 14:23"
      const dateParts = (c.date || '').split(' ');
      const dateStr   = dateParts.length >= 2
        ? `${dateParts[0]} ${dateParts[1].slice(0, 5)}`
        : c.date;

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
  } catch (e) {
    container.innerHTML = `<span class="admin-empty">${escHtml(t('admin.gitError'))}</span>`;
    console.error('loadAdminGitLog:', e);
  }
}

async function rollbackTo(hash, dateStr, message) {
  if (!confirm(
    t('git.rollbackConfirm') + '\n\n' +
    `${hash.slice(0, 7)}  ${dateStr}\n${message}\n\n` +
    t('git.rollbackDetail')
  )) return;

  const res  = await csrfFetch('/api/git/rollback', {
    method:  'POST',
    headers: { 'Content-Type': 'application/json' },
    body:    JSON.stringify({ hash }),
  });
  const data = await res.json();

  if (data.success) {
    showToast(data.message || t('git.rollbackSuccess'), 'success');
    closeAdmin();
    await loadConfig();   // refresh form + preview to reflect restored files
  } else {
    showToast(data.message || t('git.rollbackError'), 'error');
  }
}

// ── Git integration ────────────────────────────────────────────────────────
// Git-Status wird gecacht damit openWriteConfirm() darauf zugreifen kann
let _gitStatus = { git_installed: true, has_git: true };

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
    showToast(t('git.initSuccess'), 'success');
  } else {
    showToast(d.message || t('git.initError'), 'error');
  }
}

async function loadGitignoreEditor() {
  const missing = document.getElementById('git-gitignore-missing');
  const editor  = document.getElementById('git-gitignore-editor');
  const textarea = document.getElementById('git-gitignore-content');
  if (!missing || !editor || !textarea) return;

  try {
    const res  = await csrfFetch('/api/git/gitignore');
    const data = await res.json();
    if (data.exists) {
      textarea.value = data.content;
      missing.classList.add('hidden');
      editor.classList.remove('hidden');
    } else {
      missing.classList.remove('hidden');
      editor.classList.add('hidden');
    }
  } catch { /* git not set up yet */ }

  const createBtn = document.getElementById('git-gitignore-create-btn');
  if (createBtn && !createBtn.dataset.bound) {
    createBtn.dataset.bound = '1';
    createBtn.addEventListener('click', async () => {
      const res  = await csrfFetch('/api/git/create-gitignore', { method: 'POST' });
      const data = await res.json();
      if (data.success) { showToast(t('admin.gitignore.created'), 'success'); loadGitignoreEditor(); }
      else showToast(data.message || t('toast.error'), 'error');
    });
  }

  const saveBtn = document.getElementById('git-gitignore-save-btn');
  if (saveBtn && !saveBtn.dataset.bound) {
    saveBtn.dataset.bound = '1';
    saveBtn.addEventListener('click', async () => {
      const res  = await csrfFetch('/api/git/create-gitignore', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ content: textarea.value }),
      });
      const data = await res.json();
      if (data.success) showToast(t('admin.gitignore.saved'), 'success');
      else showToast(data.message || t('toast.error'), 'error');
    });
  }

  const addBtn = document.getElementById('git-gitignore-add-btn');
  if (addBtn && !addBtn.dataset.bound) {
    addBtn.dataset.bound = '1';
    addBtn.addEventListener('click', async () => {
      const res  = await csrfFetch('/api/git/create-gitignore', { method: 'POST' });
      const data = await res.json();
      if (data.success) { showToast(t('admin.gitignore.added'), 'success'); loadGitignoreEditor(); }
      else showToast(data.message || t('toast.error'), 'error');
    });
  }

  const cleanupBtn = document.getElementById('git-foreign-cleanup-btn');
  if (cleanupBtn && !cleanupBtn.dataset.bound) {
    cleanupBtn.dataset.bound = '1';
    cleanupBtn.addEventListener('click', openForeignCleanup);
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
        <div style="display:flex;gap:10px;justify-content:flex-end;margin-top:14px">
          <button type="button" id="ff-cancel" class="btn-surface">${escHtml(t('git.foreign.cancel'))}</button>
          <button type="button" id="ff-ok" class="btn-primary">${escHtml(t('git.foreign.apply'))}</button>
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
        err.textContent = e.message || t('toast.error');
        err.style.display = '';
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
    showToast(t('git.foreign.cleanupNone'), 'success');
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
