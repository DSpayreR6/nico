/** NiCo frontend — code search, toast/diff/viewer helpers, UI event bindings. Split from app.js; classic script sharing the global scope. */
'use strict';

function escHtml(str) {
  return String(str ?? '')
    .replace(/&/g, '&amp;').replace(/</g, '&lt;')
    .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

// The one notice function for all of NiCo. Notices stack instead of
// covering each other, stay longer the more they matter, and close on a
// click (Martin, VM test 2026-11-20: a warning was gone before it was read).
const _TOAST_MS  = { success: 3000, warning: 8000, error: 10000 };
const _TOAST_MAX = 5;

function showToast(message, type = 'success') {
  let stack = document.getElementById('toast-stack');
  if (!stack) {
    stack = document.createElement('div');
    stack.id = 'toast-stack';
    stack.className = 'toast-stack';
    document.body.appendChild(stack);
  }
  const el = document.createElement('div');
  el.className   = `toast toast-${type}`;
  el.textContent = message;
  el.setAttribute('role', type === 'error' ? 'alert' : 'status');
  const close = () => {
    if (!el.isConnected || el.dataset.closing) return;
    el.dataset.closing = '1';
    el.classList.remove('visible');
    setTimeout(() => el.remove(), 300);
  };
  el.addEventListener('click', close);
  stack.appendChild(el);
  // Oldest first out when too many are open.
  const open = [...stack.querySelectorAll('.toast:not([data-closing])')];
  open.slice(0, Math.max(0, open.length - _TOAST_MAX)).forEach(old => old.click());
  el.getBoundingClientRect();
  el.classList.add('visible');
  setTimeout(close, _TOAST_MS[type] ?? 3000);
}

// ── Diff viewer ────────────────────────────────────────────────────────────
async function openDiff(fromHash, toHash, title) {
  const label = title || `${fromHash.slice(0, 7)} → ${toHash === 'HEAD' ? 'HEAD' : toHash.slice(0, 7)}`;
  try {
    const res  = await csrfFetch(`/api/git/diff?from=${encodeURIComponent(fromHash)}&to=${encodeURIComponent(toHash)}`);
    const data = await res.json();
    if (data.error) { showToast(data.error, 'error'); return; }
    openViewer(label, _renderDiff(data));
  } catch {
    showToast(t('toast.error'), 'error');
  }
}

/** `emptyText`: what "no differences" means where the diff is shown – the
 *  git text ("between these commits") read wrong in the restructure and
 *  changed-outside dialogs (VM test 2026-11-20). */
function _renderDiff(data, emptyText = null) {
  const files = data.files || [];
  if (!files.length) {
    return `<div class="diff-empty">${escHtml(emptyText || t('diff.noChanges'))}</div>`;
  }
  const statusIcon  = { added: '+', modified: '~', deleted: '−' };
  const statusClass = { added: 'diff-status-added', modified: 'diff-status-modified', deleted: 'diff-status-deleted' };

  let html = '<div class="diff-viewer">';
  let anyFile = false;
  for (const f of files) {
    const icon = statusIcon[f.status]  || '~';
    const cls  = statusClass[f.status] || 'diff-status-modified';

    if (f.lock_updated) {
      anyFile = true;
      html += `
        <div class="diff-file">
          <div class="diff-file-header">
            <span class="${cls}">${icon}</span>
            <span class="diff-filename">${escHtml(f.filename)}</span>
          </div>
          <div class="diff-file-body open">
            <div class="diff-lock-note">${escHtml(t('diff.lockUpdated'))}</div>
          </div>
        </div>`;
      continue;
    }

    const relevant = (f.hunks || []).filter(h => h.type === 'added' || h.type === 'removed');
    if (!relevant.length) continue;
    anyFile = true;
    const lines = relevant.map(h => {
      const lineCls = `diff-line diff-line-${escHtml(h.type)}`;
      const prefix  = h.type === 'added' ? '+' : '−';
      return `<div class="${lineCls}">${escHtml(prefix + ' ' + h.content)}</div>`;
    }).join('');
    html += `
      <div class="diff-file">
        <div class="diff-file-header" onclick="this.nextElementSibling.classList.toggle('open')">
          <span class="${cls}">${icon}</span>
          <span class="diff-filename">${escHtml(f.filename)}</span>
        </div>
        <div class="diff-file-body open">${lines}</div>
      </div>`;
  }
  if (!anyFile) html += `<div class="diff-empty">${escHtml(t('diff.noChanges'))}</div>`;
  html += '</div>';
  return html;
}

// ── Generic content viewer ─────────────────────────────────────────────────
function openViewer(title, html) {
  document.getElementById('viewer-title').textContent = title;
  document.getElementById('viewer-content').innerHTML = html;
  document.getElementById('viewer-overlay').classList.remove('hidden');
}

function closeViewer() {
  document.getElementById('viewer-overlay').classList.add('hidden');
  document.getElementById('viewer-content').innerHTML = '';
}

// ── Git push error modal ───────────────────────────────────────────────────
// Top level on purpose: rebuild.js, save.js and admin.js report push failures
// through this modal. While it lived inside bindUI() those calls raised a
// ReferenceError and the failure was never shown to the user.

const _PUSH_ERR_MSGS = {
  NO_KEY:           'git.pushErr.NO_KEY',
  NO_WRITE:         'git.pushErr.NO_WRITE',
  NOT_FOUND:        'git.pushErr.NOT_FOUND',
  NO_NETWORK:       'git.pushErr.NO_NETWORK',
  NOT_FAST_FORWARD: 'git.pushErr.NOT_FAST_FORWARD',
  AUTH_FAILED:      'git.pushErr.AUTH_FAILED',
  FILE_TOO_LARGE:   'git.pushErr.FILE_TOO_LARGE',
  TIMEOUT:          'git.pushErr.TIMEOUT',
  UNKNOWN:          'git.pushErr.UNKNOWN',
  // Not push failures but states in which pushing cannot work at all.
  ERR_NO_GIT_REPO:  'errors.ERR_NO_GIT_REPO',
  ERR_NO_REMOTE:    'errors.ERR_NO_REMOTE',
};

function _showGitPushErrorModal(msg, errorCode) {
  const friendly = errorCode && _PUSH_ERR_MSGS[errorCode]
    ? t(_PUSH_ERR_MSGS[errorCode])
    : '';
  // The message may itself be an ERR_ code (git_manager returns those instead
  // of German plain text) – tErr translates it, other output stays verbatim.
  msg = tErr(msg);
  const overlay = document.createElement('div');
  overlay.className = 'overlay';
  overlay.innerHTML = `
    <div class="modal">
      <div class="modal-logo">${escHtml(t('git.pushFailedTitle'))}</div>
      ${friendly ? `<p style="margin-bottom:8px">${escHtml(friendly)}</p>` : ''}
      <details style="margin-bottom:12px">
        <summary style="font-size:0.8em;color:var(--fg-muted);cursor:pointer">${escHtml(t('git.pushErrDetail'))}</summary>
        <pre style="white-space:pre-wrap;font-size:0.78em;color:var(--red);margin-top:6px;overflow-x:auto">${escHtml(msg)}</pre>
      </details>
      <div class="confirm-buttons">
        ${errorCode === 'FILE_TOO_LARGE'
          ? `<button type="button" id="_push-err-clean" class="action-btn btn-yellow">${escHtml(t('git.large.btn'))}</button>`
          : ''}
        <button type="button" id="_push-err-ok" class="action-btn btn-red">OK</button>
      </div>
    </div>`;
  document.body.appendChild(overlay);
  // Closes via its button only – a stray background click must not wipe the
  // one message that explains why the push failed.
  overlay.querySelector('#_push-err-ok').addEventListener('click', () => overlay.remove());
  // Straight from the diagnosis to the fix: this error cannot be resolved by
  // retrying, the file has to leave the history first.
  overlay.querySelector('#_push-err-clean')?.addEventListener('click', () => {
    overlay.remove();
    openLargeFileCleanup();
  });
}

/**
 * What a push or pull actually moved. Both used to report nothing but "done",
 * so there was no way to tell what had been transferred – or, after a pull,
 * what had just changed underneath you.
 *
 * Only shown when something happened: confirming empty windows away teaches
 * people to click them away unread.
 *
 * Resolves false when there was nothing to show, and true once the user has
 * closed the window – callers that need to act afterwards (a pull changes the
 * files under the open form) must wait for it to be read, not race it.
 */
async function _showTransferModal(kind, info) {
  const commits = info?.commits || [];
  if (!commits.length) return false;

  const rows = commits.map(c => `
    <div style="margin:6px 0">
      <div style="overflow-wrap:anywhere"><code>${escHtml(c.hash)}</code> ${escHtml(c.message)}</div>
      <div style="font-size:0.82em;color:var(--fg-muted)">${escHtml(c.date)} · ${escHtml(c.author)}</div>
    </div>`).join('');
  const files = info.files?.length
    ? `<p style="margin-top:10px">${escHtml(t('git.transfer.files', info.files.length))}</p>
       <div style="font-size:0.85em;color:var(--fg-muted);overflow-wrap:anywhere">
         ${info.files.map(f => escHtml(f)).join('<br>')}</div>`
    : '';

  const overlay = document.createElement('div');
  overlay.className = 'overlay';
  overlay.innerHTML = `
    <div class="modal">
      <div class="modal-logo">${escHtml(t(kind === 'push' ? 'git.transfer.pushTitle' : 'git.transfer.pullTitle'))}</div>
      <p>${escHtml(t(kind === 'push' ? 'git.transfer.pushBody' : 'git.transfer.pullBody',
                     commits.length, info.remote || '?'))}</p>
      ${info.url ? `<p style="font-size:0.82em;color:var(--fg-muted);overflow-wrap:anywhere">${escHtml(info.url)}</p>` : ''}
      <div class="gsi"><div class="gsi-block">
        <div class="gsi-block-body">${rows}</div>
      </div></div>
      ${files}
      <div class="confirm-buttons">
        <button type="button" id="_transfer-ok" class="action-btn btn-green">OK</button>
      </div>
    </div>`;
  document.body.appendChild(overlay);
  await new Promise(resolve => {
    overlay.querySelector('#_transfer-ok').addEventListener('click', () => {
      overlay.remove();
      resolve();
    });
  });
  return true;
}

/**
 * Files the size guard kept out of a commit. Shown as a dialog rather than a
 * toast: the user just saved and would otherwise assume everything went into
 * git, which is how a 2.1 GB log went unnoticed until it blocked every push.
 */
function _showSkippedLargeModal(files) {
  const rows = files.map(f =>
    `<div style="overflow-wrap:anywhere;margin:2px 0">${escHtml(f)}</div>`).join('');
  const overlay = document.createElement('div');
  overlay.className = 'overlay';
  overlay.innerHTML = `
    <div class="modal">
      <div class="modal-logo">${escHtml(t('git.skippedLarge.title'))}</div>
      <p>${escHtml(t('git.skippedLarge.body'))}</p>
      <div class="gsi"><div class="gsi-block">
        <div class="gsi-block-body">${rows}</div>
      </div></div>
      <div class="confirm-buttons">
        <button type="button" id="_skipped-large-ok" class="action-btn btn-yellow">OK</button>
      </div>
    </div>`;
  document.body.appendChild(overlay);
  overlay.querySelector('#_skipped-large-ok').addEventListener('click', () => overlay.remove());
}

/**
 * Bricks sitting inside another brick. Never a toast: the symptom – a brick
 * missing from the list – says nothing about the cause, and the inner brick
 * dies unannounced when the outer one is moved or deleted.
 *
 * mode 'refused'  – the write was rejected, nothing changed on disk
 *      'existing' – written, but the file already contained the nesting
 *      'confirm'  – raw editor: ask first, resolves true when saved anyway
 */
function _showNestedBricksModal(nested, mode = 'existing') {
  const rows = (nested || []).map(n => {
    const where = n.file ? `${n.file}:${n.line}` : `Zeile ${n.line}`;
    return `<div style="overflow-wrap:anywhere;margin:2px 0">${
      escHtml(t('brix.nested.row', where, n.outer, n.inner))}</div>`;
  }).join('');

  return new Promise(resolve => {
    const overlay = document.createElement('div');
    overlay.className = 'overlay';
    const buttons = mode === 'confirm'
      ? `<button type="button" id="_nested-save" class="action-btn btn-yellow">${
           escHtml(t('brix.nested.saveAnyway'))}</button>
         <button type="button" id="_nested-back" class="action-btn btn-secondary">${
           escHtml(t('brix.nested.back'))}</button>`
      : `<button type="button" id="_nested-ok" class="action-btn btn-yellow">OK</button>`;
    overlay.innerHTML = `
      <div class="modal">
        <div class="modal-logo">${escHtml(t(`brix.nested.title.${mode}`))}</div>
        <p>${escHtml(t(`brix.nested.body.${mode}`))}</p>
        <div class="gsi"><div class="gsi-block">
          <div class="gsi-block-body">${rows}</div>
        </div></div>
        <p>${escHtml(t('brix.nested.fix'))}</p>
        <div class="confirm-buttons">${buttons}</div>
      </div>`;
    document.body.appendChild(overlay);
    const done = val => { overlay.remove(); resolve(val); };
    overlay.querySelector('#_nested-ok')?.addEventListener('click', () => done(false));
    overlay.querySelector('#_nested-save')?.addEventListener('click', () => done(true));
    overlay.querySelector('#_nested-back')?.addEventListener('click', () => done(false));
  });
}

// Files already reported this page load – a pre-existing nesting would
// otherwise pop up again on every single brick action in that file.
const _nestedReported = new Set();

/** Handle the `nested` field of a successful write. Returns nothing. */
function reportNestedBricks(nested) {
  if (!nested?.length) return;
  // Identity is the file plus the INNER brick name, never the outer one:
  // renaming the outer brick would otherwise make a long-known nesting look
  // new and pop the dialog up again. Same decision as in brix.new_nested_bricks().
  const key = nested.map(n => `${n.file}|${n.inner}`).join(',');
  if (_nestedReported.has(key)) return;
  _nestedReported.add(key);
  _showNestedBricksModal(nested, 'existing');
}

// ── Leaving: save, quit, restart (2026-11-25) ──────────────────────────────

/** Before NiCo stops (quit, restart) or is updated: save what is open, the
 *  same way as the disk button. False when something could not be saved –
 *  the caller then stops, NiCo stays open and nothing is lost. */
async function saveBeforeLeave() {
  // An unchanged file in text mode is not written again (for
  // hardware-configuration.nix that would ask once more).
  if (document.getElementById('raw-file-editor') && !Sidebar.hasUnsaved()) return true;
  const ok = await Sidebar.saveActive({ toast: false });
  if (!ok) await nicoAlert(t('leave.saveFailed'));
  return ok;
}

/** Save, start a new NiCo on this port (nico/restart.py), and reload this
 *  tab once /api/status answers with another instance. */
async function restartNico() {
  if (!(await saveBeforeLeave())) return;
  const res  = await csrfFetch('/api/restart', { method: 'POST' }).catch(() => null);
  const data = res ? await res.json().catch(() => null) : null;
  if (!data?.ok) {
    showToast(data?.error === 'ERR_REBUILD_RUNNING' ? t('rebuild.alreadyRunning')
                                                    : tErr(data?.error) || t('toast.error'), 'error');
    return;
  }
  const show = text => {
    document.body.innerHTML = `<p style="font-family:monospace;padding:2rem;color:#cdd6f4">${escHtml(text)}</p>`;
  };
  show(t('restart.waiting'));
  const until = Date.now() + 60000;
  const poll = async () => {
    try {
      const s = await (await fetch('/api/status', { cache: 'no-store' })).json();
      if (s.instance && s.instance !== data.instance) { location.reload(); return; }
    } catch { /* the old one is gone, the new one not up yet */ }
    if (Date.now() > until) { show(t('restart.failed')); return; }
    setTimeout(poll, 500);
  };
  setTimeout(poll, 500);
}

// ── Event bindings ─────────────────────────────────────────────────────────
//
// Project rule: a click on the overlay background never closes a dialog –
// anywhere. Someone who clicks next to a window and sees it vanish cannot tell
// what just happened, or whether it did something. Dialogs close through their
// own buttons (Escape on a focused input is fine). A dialog with no way to
// close it is a bug in that dialog, not a reason to bring the background click
// back.
function on(id, event, handler) {
  document.getElementById(id)?.addEventListener(event, handler);
}

function bindUI() {
  // Plain-Code-View Toggle
  on('preview-mode-btn', 'click', togglePlainCodeView);

  // Language switcher dropdown (buttons built dynamically by initLangSwitcher)
  on('lang-current-btn', 'click', e => {
    e.stopPropagation();
    document.getElementById('lang-dropdown')?.classList.toggle('hidden');
  });
  document.addEventListener('click', () => {
    document.getElementById('lang-dropdown')?.classList.add('hidden');
    Sidebar.closeTreeContextMenu();
  });
  document.addEventListener('keydown', e => {
    if (e.key === 'Escape') Sidebar.closeTreeContextMenu();
  });

  // Setup
  on('setup-btn',       'click',   () => handleSetup());
  on('nixos-dir-input', 'keydown', e => { if (e.key === 'Enter') handleSetup(); });
  on('confirm-yes-btn',       'click', () => handleSetup(true));
  on('confirm-no-btn',        'click', hideConfirm);
  on('setup-symlink-yes-btn', 'click', () => doSetupSymlink(true));
  on('setup-symlink-no-btn',  'click', () => doSetupSymlink(false));
  on('setup-browse-btn', 'click', () => {
    const cur = document.getElementById('nixos-dir-input').value.trim();
    openDirBrowser(cur || '~', path => {
      document.getElementById('nixos-dir-input').value = path;
    }, null, { newHint: true });
  });

  // Dir-Browser
  on('dirbrowser-select-btn', 'click', () => {
    if (_dirBrowserCallback && _dirBrowserCurrent) _dirBrowserCallback(_dirBrowserCurrent);
    closeDirBrowser();
  });
  on('dirbrowser-cancel-btn', 'click', closeDirBrowser);

  // Erststart-Import
  on('import-apply-btn', 'click', () => applyImport(true));  // first-run: no backup needed
  on('import-backup-yes-btn', 'click', () => {
    document.getElementById('import-backup-confirm').classList.add('hidden');
    applyImport(true);
  });
  on('import-backup-cancel-btn', 'click', () => {
    document.getElementById('import-backup-confirm').classList.add('hidden');
    document.getElementById('import-main-buttons').classList.remove('hidden');
  });
  on('import-skip-btn', 'click', () => {
    document.getElementById('import-overlay').classList.add('hidden');
  });
  // From a ZIP (VM test 2026-11-21): the one ZIP import NiCo has, in the
  // admin panel. Same click, so the browser still opens the file picker.
  on('import-zip-btn', 'click', () => {
    document.getElementById('import-overlay').classList.add('hidden');
    openAdmin();
    _switchAdminTab('administration');
    const zipBtn = document.getElementById('admin-zip-import-btn');
    // The groups of the admin tabs start closed (2026-11-25): open its group.
    zipBtn?.closest('.settings-group')?.classList.remove('collapsed');
    zipBtn?.scrollIntoView({ block: 'center' });
    zipBtn?.click();
  });

  // Form → live preview + section visibility update
  on('config-form', 'input',  schedulePreviewUpdate);
  on('config-form', 'change', schedulePreviewUpdate);
  on('config-form', 'change', updateSectionVisibility);
  on('config-form', 'input',  updateSectionVisibility);

  // GC sub-options visibility
  on('nix_gc', 'change', e => toggleGcOptions(e.target.checked));

  // Btrfs balance sub-options visibility
  on('btrfs_balance', 'change', e => toggleBtrfsBalanceOptions(e.target.checked));

  // Hardware / Virtualisierung / Backup sub-option visibility
  on('opengl',       'change', e => toggleOpenglOptions(e.target.checked));
  on('boot_loader',      'change', e => { toggleBootEfiOptions(e.target.value !== 'none'); schedulePreviewUpdate(); });
  on('plymouth_enabled', 'change', e => {
    togglePlymouthOptions(e.target.checked);
    schedulePreviewUpdate();
  });
  on('pipewire',  'change', e => { togglePipewireOptions(e.target.checked); schedulePreviewUpdate(); });
  on('virtualbox_guest', 'change', e => { toggleVboxGuestOptions(e.target.checked); schedulePreviewUpdate(); });
  on('docker',       'change', e => toggleDockerOptions(e.target.checked));
  on('podman',       'change', e => togglePodmanOptions(e.target.checked));
  on('libvirtd',     'change', e => toggleLibvirtdOptions(e.target.checked));
  on('tmp_use_tmpfs', 'change', () => { syncTmpfsRows(); schedulePreviewUpdate(); });
  on('snapper_enable', 'change', e => {
    document.getElementById('snapper-area')?.classList.toggle('hidden', !e.target.checked);
    schedulePreviewUpdate();
  });
  document.getElementById('snapper-add-btn')?.addEventListener('click', () => {
    const current = getAllSnapperConfigs();
    current.push({ ...SN_DEFAULTS });
    renderAllSnapperConfigs(current);
    const list  = document.getElementById('snapper-list');
    const cards = list?.querySelectorAll('.extra-user-card');
    if (cards?.length) {
      const last = cards[cards.length - 1];
      last.querySelector('.extra-user-body')?.classList.add('open');
      last.querySelector('.eu-toggle')?.classList.add('open');
    }
    schedulePreviewUpdate();
  });

  on('flatpak_enable', 'change', e => {
    document.getElementById('flatpak-area')?.classList.toggle('hidden', !e.target.checked);
    schedulePreviewUpdate();
  });
  document.getElementById('flatpak-add-flathub-btn')?.addEventListener('click', () => {
    const cur = getAllFlatpakRemotes();
    if (!cur.some(r => r.name === 'flathub')) {
      cur.push({ name: 'flathub', url: 'https://flathub.org/repo/flathub.flatpakrepo' });
      renderFlatpakRemotes(cur);
      schedulePreviewUpdate();
    }
  });
  document.getElementById('flatpak-add-remote-btn')?.addEventListener('click', () => {
    const cur = getAllFlatpakRemotes();
    cur.push({ name: '', url: '' });
    renderFlatpakRemotes(cur);
    schedulePreviewUpdate();
  });
  document.getElementById('extra_locale_enable')?.addEventListener('change', function() {
    document.getElementById('extra-locale-detail')?.classList.toggle('hidden', !this.checked);
  });
  document.getElementById('boot_config_limit_enabled')?.addEventListener('change', function() {
    document.getElementById('boot-config-limit-detail')?.classList.toggle('hidden', !this.checked);
  });
  document.getElementById('firewall_tcp_enable')?.addEventListener('change', function() {
    document.getElementById('firewall-tcp-detail')?.classList.toggle('hidden', !this.checked);
  });
  document.getElementById('firewall_udp_enable')?.addEventListener('change', function() {
    document.getElementById('firewall-udp-detail')?.classList.toggle('hidden', !this.checked);
  });
  document.getElementById('firefox')?.addEventListener('change', function() {
    document.getElementById('firefox-detail')?.classList.toggle('hidden', !this.checked);
  });
  document.getElementById('appimage')?.addEventListener('change', function() {
    document.getElementById('appimage-detail')?.classList.toggle('hidden', !this.checked);
  });
  // The generator leaves out anything that is not a plain package name
  // (generator.appimage_lib_names). Doing the same here and saying so keeps
  // an entry from vanishing without a word on the next save.
  document.getElementById('appimage_libs')?.addEventListener('change', function() {
    const ok = /^[A-Za-z_][\w'+-]*(\.[A-Za-z_][\w'+-]*)*$/;
    const kept = [], dropped = [];
    this.value.split(/[,\s]+/).filter(Boolean).forEach(tok => {
      const name = tok.replace(/^pkgs\./, '');
      if (!ok.test(name)) dropped.push(tok);
      else if (!kept.includes(name)) kept.push(name);
    });
    this.value = kept.join(', ');
    if (dropped.length) showToast(t('field.appimageLibsInvalid', dropped.join(', ')), 'error');
  });


  addSectionLineHints();

  // Ebene-2 Sektionen (aufklappbar innerhalb von Sektionen)
  initLvl2Sections();
  initNixosLinks();

  // Weiteren Benutzer hinzufügen
  on('add-user-btn', 'click', () => {
    const current = getAllUsers();
    current.push({ username: '', description: '', initial_password: '', uid: '',
                   groups: [...DEFAULT_EXTRA_USER_GROUPS], shell: 'bash', extra_nix: '' });
    renderAllUsers(current);
    schedulePreviewUpdate();
    // Zum neuen Eintrag scrollen und aufklappen
    const cards = document.querySelectorAll('.extra-user-card');
    const last  = cards[cards.length - 1];
    last?.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    last?.querySelector('.extra-user-body')?.classList.add('open');
    last?.querySelector('.eu-toggle')?.classList.add('open');
    last?.querySelector('.eu-username')?.focus();
  });

  // Sektionen-Steuerleiste
  on('collapse-all-btn', 'click', collapseAll);
  on('expand-all-btn',   'click', expandAll);

  // Section filter dropdown
  on('sec-filter-btn', 'click', _toggleFilterDropdown);
  document.addEventListener('click', _closeFilterDropdown);
  document.querySelectorAll('.sec-filter-option').forEach(opt => {
    opt.addEventListener('click', () => {
      sectionFilter = opt.dataset.filter;
      localStorage.setItem('nico-section-filter', sectionFilter);
      csrfFetch('/api/app/settings', {
        method:  'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body:    JSON.stringify({ section_filter: sectionFilter }),
      }).catch(() => {});
      document.getElementById('sec-filter-dropdown')?.classList.add('hidden');
      updateSectionVisibility();
    });
  });
  // Init dropdown active state
  updateSectionVisibility();

  // Section settings overlay
  on('sections-settings-btn',   'click', openSectionsSettings);
  on('sections-settings-close', 'click', closeSectionsSettings);
  on('sections-settings-save',  'click', saveSectionsSettings);
  on('sections-settings-cancel', 'click', closeSectionsSettings);

  // Admin sub-tabs: one component for every admin tab that has them
  // (settings: NiCo | Config, time machine: Config | System, 2026-11-21).
  // Each set switches only within its own admin tab; the event
  // "subtab-shown" (detail = sub-tab id) lets a tab load its content.
  document.querySelectorAll('.settings-subtab').forEach(btn => {
    btn.addEventListener('click', () => {
      const scope = btn.closest('.admin-tab-panel') || document;
      scope.querySelectorAll('.settings-subtab').forEach(b => b.classList.remove('active'));
      scope.querySelectorAll('.settings-subtab-panel').forEach(p => p.classList.add('hidden'));
      btn.classList.add('active');
      scope.querySelector(`.settings-subtab-panel[data-subtab-panel="${btn.dataset.subtab}"]`)
        ?.classList.remove('hidden');
      btn.dispatchEvent(new CustomEvent('subtab-shown', { bubbles: true, detail: btn.dataset.subtab }));
    });
  });

  // Left panel section toggles (click on h3)
  document.querySelectorAll('.sec-toggle').forEach(h3 => {
    h3.addEventListener('click', () => {
      const section = h3.closest('section');
      if (section?.dataset.section) toggleSection(section.dataset.section);
    });
  });

  async function _saveAllSilent() {
    await Sidebar.flakeSave();
    await _autoSave();
    await _writeNix();
  }

  // NixOS action dropdown
  (function initNixosMenu() {
    const menu  = document.getElementById('nixos-menu');
    const btn   = document.getElementById('nixos-btn');
    const drop  = document.getElementById('nixos-dropdown');
    if (!menu || !btn || !drop) return;

    function toggle(e) {
      e.stopPropagation();
      const opening = drop.classList.contains('hidden');
      drop.classList.toggle('hidden');
      if (opening) _saveAllSilent();
    }
    function close() { drop.classList.add('hidden'); }

    btn.addEventListener('click', toggle);
    document.addEventListener('click', e => { if (!menu.contains(e.target)) close(); });

    on('nixos-save-btn',     'click', () => { close(); openWriteConfirm(); });
    on('nixos-validate-btn', 'click', () => { close(); runValidation(); });
    on('nixos-dryrun-btn',   'click', () => { close(); runSaveAndDryRun(); });
    on('nixos-rebuild-btn',  'click', () => { close(); openRebuild('switch'); });
    on('nixos-git-push-btn', 'click', () => { close(); gitPushManual(); });
    on('nixos-git-pull-btn', 'click', () => { close(); gitPullManual(); });
    on('nixos-diff-btn',     'click', () => { close(); openDiff('HEAD~1', 'HEAD', t('diff.lastCommitTitle')); });
  })();

  async function gitPushManual() {
    try {
      const res  = await csrfFetch('/api/git/push', { method: 'POST' });
      const data = await res.json();
      if (data.success) {
        // Window only when something moved, otherwise the short note.
        if (!await _showTransferModal('push', data.info)) showToast(t('git.pushNothing'), 'success');
      } else {
        _showGitPushErrorModal(data.message || t('toast.error'), data.error_code);
      }
    } catch (e) {
      _showGitPushErrorModal(String(e));
    }
  }

  async function gitPullManual() {
    try {
      const res  = await csrfFetch('/api/git/pull', { method: 'POST' });
      const data = await res.json();
      if (!data.success) {
        _showGitPushErrorModal(data.message || t('toast.error'), data.error_code);
        return;
      }
      if (await _showTransferModal('pull', data.info)) {
        // Files on disk changed underneath the open form. Reload only after the
        // window has been read, otherwise the reload eats the very information
        // the user needs.
        location.reload();
      } else {
        showToast(t('git.pullNothing'), 'success');
      }
    } catch (e) {
      _showGitPushErrorModal(String(e));
    }
  }

  on('format-readonly-btn', 'click', _closeFormatDialog);
  on('format-proceed-btn',  'click', formatProceed);

  // Admin-Bereich: das Zahnrad öffnet und schließt – aber nie am Autosave
  // vorbei (openAdmin speichert, closeAdmin nicht) und nie unter einem
  // offenen Unterdialog hinweg.
  on('admin-btn', 'click', () => {
    const overlay = document.getElementById('admin-overlay');
    if (!overlay) return;
    if (overlay.classList.contains('hidden')) { openAdmin(); return; }
    if (_adminSubdialogOpen()) return;
    closeAdmin();
  });
  on('admin-close-btn', 'click', closeAdmin);
  on('admin-export-btn',   'click', exportZip);
  on('admin-symlink-btn',  'click', doAdminSymlink);
  on('admin-symlink-remove-btn', 'click', doAdminSymlinkRemove);
  on('admin-move-btn', 'click', _moveConfigFlow);

  // Validierungseinstellungen
  on('validation-settings-btn',   'click', openValidationSettings);
  on('validation-settings-close', 'click', closeValidationSettings);
  on('validation-settings-save',  'click', saveValidationSettings);
  on('validation-settings-cancel', 'click', closeValidationSettings);
  on('validation-results-close',  'click', closeValidationResults);

  initDirImport();
  initImportManual();
  initZipImport();
  initSettingsImport();
  on('admin-detach-btn', 'click', detachConfig);
  initAdminTabs();
  initSettingsPanel();

  // Rebuild output modal – closes only via its button. A background click must
  // not tear down the running rebuild stream: the user would lose the output
  // without knowing why, and the rebuild itself keeps running unattended.
  on('rebuild-close-btn', 'click', closeRebuild);
  on('rebuild-restart-btn', 'click', () => { closeRebuild(); restartNico(); });

  // Write-Confirm-Dialog (Sicherungspunkt)
  on('write-confirm-btn', 'click', writeFiles);
  on('write-cancel-btn',  'click', closeWriteConfirm);
  on('write-label-input', 'keydown', e => {
    if (e.key === 'Enter')  writeFiles();
    if (e.key === 'Escape') closeWriteConfirm();
  });

  // Dry-run modal (used by admin)
  on('dryrun-close-btn', 'click', () => document.getElementById('dryrun-overlay').classList.add('hidden'));

  // Help dropdown
  on('help-btn', 'click', (e) => {
    e.stopPropagation();
    document.getElementById('help-dropdown').classList.toggle('hidden');
  });
  document.addEventListener('click', () => {
    document.getElementById('help-dropdown').classList.add('hidden');
  });
  on('about-btn', 'click', () => {
    document.getElementById('help-dropdown').classList.add('hidden');
    document.getElementById('about-overlay').classList.remove('hidden');
  });
  // Feedback links open GitHub in a new tab – only on click, nothing is sent
  // by NiCo itself. The issue body carries just the version (as shown in the
  // About dialog), never config data; the user sees it before submitting.
  const NICO_REPO = 'https://github.com/DSpayreR6/nico';
  on('help-issue-btn', 'click', () => {
    document.getElementById('help-dropdown').classList.add('hidden');
    const ver = document.getElementById('about-version')?.textContent.trim() || '';
    const body = ver ? `NiCo ${ver}\n\n` : '';
    window.open(`${NICO_REPO}/issues/new?body=${encodeURIComponent(body)}`,
                '_blank', 'noopener');
  });
  // Email for beginners without a GitHub account. The address is assembled
  // at runtime so simple scrapers of the repo do not pick it up.
  const NICO_MAIL = ['feedback', 'nico4nixos.anonaddy.com'].join('@');
  on('help-mail-btn', 'click', () => {
    document.getElementById('help-dropdown').classList.add('hidden');
    document.getElementById('mail-address').textContent = NICO_MAIL;
    document.getElementById('mail-overlay').classList.remove('hidden');
  });
  on('mail-copy-btn', 'click', () => {
    navigator.clipboard.writeText(NICO_MAIL).then(
      () => showToast(t('mail.copied'), 'success'),
      () => showToast(t('toast.error'), 'error'),
    );
  });
  on('mail-open-btn', 'click', () => {
    const ver = document.getElementById('about-version')?.textContent.trim() || '';
    const body = ver ? `NiCo ${ver}\n\n` : '';
    window.location.href = `mailto:${NICO_MAIL}?subject=${encodeURIComponent('NiCo feedback')}`
                         + `&body=${encodeURIComponent(body)}`;
  });
  on('mail-close-btn', 'click', () => document.getElementById('mail-overlay').classList.add('hidden'));
  on('help-discuss-btn', 'click', () => {
    document.getElementById('help-dropdown').classList.add('hidden');
    window.open(`${NICO_REPO}/discussions`, '_blank', 'noopener');
  });
  // Update an installed NiCo (nix profile). Shown when NiCo runs from the
  // Nix store - its own path, nothing read (revision 2026-11-25, S3). Which
  // profile entry belongs to it is asked on the click, with the release to
  // read the installed programs (always or once).
  fetch('/api/selfupdate/status').then(r => r.json()).then(d => {
    if (!d.installed) return;
    document.getElementById('selfupdate-btn')?.classList.remove('hidden');
  }).catch(() => {});
  on('selfupdate-btn', 'click', async () => {
    document.getElementById('help-dropdown').classList.add('hidden');
    let entry = await fetch('/api/selfupdate/entry').then(r => r.json()).catch(() => null);
    if (entry?.error === 'ERR_HOST_READ_OFF') {
      if (!(await askHostRead('read_host_profile', t('hostRead.askProfile')))) return;
      entry = await fetch('/api/selfupdate/entry').then(r => r.json()).catch(() => null);
    }
    if (!entry || entry.error) { showToast(tErr(entry?.error) || t('toast.error'), 'error'); return; }
    if (!(await nicoConfirm(t('selfupdate.confirm', entry.url),
                            { okLabel: t('header.helpUpdate') }))) return;
    // Saved first (Martin, 2026-11-25): the update ends with the offer to
    // restart, and the state on disk is then clear from the start.
    if (!(await saveBeforeLeave())) return;
    const res = await csrfFetch('/api/selfupdate', { method: 'POST' }).catch(() => null);
    const data = res ? await res.json().catch(() => null) : null;
    if (!data?.success) {
      if (handleBusy(data?.error)) return;
      showToast(tErr(data?.error) || t('toast.error'), 'error');
      return;
    }
    attachRebuildMonitor();
  });
  on('help-docs-btn', 'click', () => {
    document.getElementById('help-dropdown').classList.add('hidden');
    window.open('/help', '_blank');
  });
  on('about-close-btn', 'click', () => document.getElementById('about-overlay').classList.add('hidden'));
  on('viewer-close-btn', 'click', closeViewer);

  on('diff-show-btn', 'click', () => {
    const from = document.getElementById('diff-from-select')?.value;
    const to   = document.getElementById('diff-to-select')?.value;
    if (from) openDiff(from, to || 'HEAD');
  });

  // Quit
  on('quit-btn', 'click', async () => {
    const choice = await nicoChoice(t('quit.confirm'),
                                    { okLabel: t('quit.quit'), altLabel: t('quit.restart') });
    if (!choice) return;
    if (choice === 'alt') { await restartNico(); return; }
    // Until 2026-11-25 only the form and flake.nix were saved here: unsaved
    // text mode edits were lost without a word.
    if (!(await saveBeforeLeave())) return;

    // Close-check: prompt push if remote exists and local is ahead/dirty,
    // but only when NiCo loaded a config normally (not in aborted state).
    if (!window._nicoAborted) {
      try {
        const ccRes  = await csrfFetch('/api/git/close-check');
        const ccData = await ccRes.json();
        if (ccData.has_remote && ccData.needs_push) {
          const doPush = await confirmClosePushPrompt();
          if (doPush) {
            const pushRes  = await csrfFetch('/api/git/commit-push', { method: 'POST' });
            const pushData = await pushRes.json();
            if (!pushData.success) {
              await nicoAlert(t('git.closePrompt.error', tErr(pushData.message) || ''));
            }
          }
        }
      } catch (_) { /* network down – proceed with shutdown */ }
    }

    await csrfFetch('/api/shutdown', { method: 'POST' }).catch(() => {});
    window.close();
    document.body.innerHTML = `<p style="font-family:monospace;padding:2rem;color:#cdd6f4">${escHtml(t('quit.done'))}</p>`;
  });

  // Tabs
  document.querySelectorAll('.tab').forEach(btn =>
    btn.addEventListener('click', () => switchTab(btn.dataset.tab))
  );

  // Package modal
  on('add-package-btn',  'click',   openPkgModal);
  on('pkg-close-btn',    'click',   closePkgModal);
  on('pkg-manual-add-btn', 'click', addManualPackage);
  document.getElementById('pkg-manual-input')?.addEventListener('keydown', e => {
    if (e.key === 'Enter') addManualPackage();
  });
  on('pkg-search-input', 'input',   e => {
    clearTimeout(pkgDebounce);
    const q = e.target.value.trim();
    // Spinner starts before the debounce: otherwise the first 400 ms look dead
    if (q.length >= 2) startPkgSpinner(q); else stopPkgSpinner(true);
    pkgDebounce = setTimeout(() => runPkgSearch(q), 400);
  });
  on('pkg-search-input', 'keydown', e => { if (e.key === 'Escape') closePkgModal(); });

  // One search for every file (search.js); Ctrl+F opens it instead of the
  // browser's own search
  Search.init();
  document.addEventListener('keydown', e => {
    if ((e.ctrlKey || e.metaKey) && e.key === 'f') {
      e.preventDefault();
      Search.open();
    }
    // Ctrl+S saves the open file, panel or raw, instead of the browser's
    // "save page" - as in every editor.
    if ((e.ctrlKey || e.metaKey) && !e.shiftKey && !e.altKey && e.key.toLowerCase() === 's') {
      e.preventDefault();
      Sidebar.saveActive();
    }
  });

  // Brix dialogs
  on('brix-insert-btn',         'click', openBrixInsert);
  on('brix-insert-confirm-btn', 'click', insertBrix);
  on('brix-insert-cancel-btn',  'click', closeBrixInsert);
  on('brix-name-input',         'keydown', e => { if (e.key === 'Enter') insertBrix(); if (e.key === 'Escape') closeBrixInsert(); });

  on('brix-move-btn',           'click', () => openBrixMove());
  on('brix-move-confirm-btn',   'click', moveBrix);
  on('brix-move-cancel-btn',    'click', closeBrixMove);

  on('brix-rename-confirm-btn', 'click', renameBrix);
  on('brix-rename-cancel-btn',  'click', closeBrixRename);
  on('brix-rename-new',         'keydown', e => { if (e.key === 'Enter') renameBrix(); if (e.key === 'Escape') closeBrixRename(); });

  on('brix-split-confirm-btn',  'click', splitBrix);
  on('brix-split-cancel-btn',   'click', closeBrixSplit);
  on('brix-split-line',         'keydown', e => { if (e.key === 'Enter') splitBrix(); if (e.key === 'Escape') closeBrixSplit(); });

  Sidebar.init();
}
