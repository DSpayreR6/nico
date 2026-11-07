/** NiCo frontend — Nix-Brix actions: context menu, insert, delete, move, rename, split, inline edit. Split from app.js; classic script sharing the global scope. */
'use strict';

// ── Brix-Kontextmenü ────────────────────────────────────────────────────────
/**
 * Readable text for an unbalanced-bracket report.
 *
 * The server sends data, not prose: {kind, n, char}. It used to send German
 * sentences, which the interface showed to everyone regardless of language -
 * they do not look like translatable text, which is why they were missed in
 * the i18n rounds. Same shape the Validator uses with detail_key/detail_params.
 */
function _bracketDetails(details) {
  return details.map(d => {
    if (typeof d === 'string') return d;        // älterer Server
    const key = d.kind === 'extra' ? 'brix.bracketExtra' : 'brix.bracketUnclosed';
    return t(key, d.n, d.char);
  }).join(', ');
}

/**
 * Error text for a refused brick write.
 *
 * ERR_BRICK_BREAKS_FILE carries Nix's own message in `detail`. The interface
 * used to show only the generic sentence, so the user learned that the file
 * would break but not why (solidcraft, 2026-11-07). Nix's message is several
 * lines with a code excerpt; the first line and the position are enough for
 * a toast. It stays English – it is Nix speaking, not NiCo.
 */
function _brickErrText(data) {
  let msg = tErr(data.error);
  if (data.error === 'ERR_BRICK_BREAKS_FILE' && data.detail) {
    const lines = String(data.detail).split('\n');
    const first = lines[0].replace(/^error:\s*/, '').trim();
    const pos   = String(data.detail).match(/\bat\s*\S*?:(\d+):(\d+)/);
    msg += ' ' + first + (pos ? ` (${pos[1]}:${pos[2]})` : '');
  }
  return msg;
}

function closeAllBrixMenus() {
  document.querySelectorAll('.brix-context-menu').forEach(m => m.classList.add('hidden'));
  // Hand the action bar back to :hover - see the comment at the menu button
  // in preview.js for why an open menu holds it open itself.
  document.querySelectorAll('.code-brix.brix-menu-open')
          .forEach(w => w.classList.remove('brix-menu-open'));
}
document.addEventListener('click', closeAllBrixMenus);

// ── Brix-Block einfügen ─────────────────────────────────────────────────────
function openBrixInsert() {
  document.getElementById('brix-insert-overlay').classList.remove('hidden');
  document.getElementById('brix-name-input').value = '';
  document.getElementById('brix-insert-error').classList.add('hidden');
  const sel = document.getElementById('brix-section-select');
  if (sel) {
    // Repopulate sections based on target file
    sel.innerHTML = '';
    const sections = _brixTargetFtype === 'fl' ? _getFlakeBrickSections()
                   : _brixTargetFtype === 'hm' ? HM_BRICK_SECTIONS
                   : BRICK_SECTIONS;
    sections.forEach(s => {
      const opt = document.createElement('option');
      opt.value = s; opt.textContent = secLabel(s);
      sel.appendChild(opt);
    });
    sel.value = 'TAIL';
  }
  document.getElementById('brix-name-input').focus();
}

function closeBrixInsert() {
  document.getElementById('brix-insert-overlay').classList.add('hidden');
}

async function insertBrix() {
  const name    = document.getElementById('brix-name-input').value.trim();
  const section = document.getElementById('brix-section-select')?.value || 'TAIL';
  const errorEl = document.getElementById('brix-insert-error');
  errorEl.classList.add('hidden');

  if (!name) {
    errorEl.textContent = t('brix.errNoName');
    errorEl.classList.remove('hidden');
    return;
  }
  if (!/^[\w\-]+$/.test(name)) {
    errorEl.textContent = t('brix.errInvalidName');
    errorEl.classList.remove('hidden');
    return;
  }

  const res  = await csrfFetch('/api/brick', {
    method:  'POST',
    headers: { 'Content-Type': 'application/json' },
    body:    JSON.stringify({ name, section, file: _brixTargetFile }),
  });
  const data = await res.json();
  if (_handleNestedResponse(data)) return;
  if (data.error) {
    errorEl.textContent = _brickErrText(data);
    errorEl.classList.remove('hidden');
    return;
  }

  closeBrixInsert();
  showToast(t('brix.inserted', data.brick_name));

  await _refreshPreviewForFile(_brixTargetFile);

  const brickEl = document.querySelector(`[data-brick-name="${CSS.escape(data.brick_name)}"]`);
  brickEl?.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
}

// ── Nix-Brick löschen ───────────────────────────────────────────────────────
async function confirmDeleteBrix(name, file = 'configuration.nix') {
  if (!confirm(t('brix.confirmDelete', name))) {
    return;
  }
  const res  = await csrfFetch(`/api/brick/${encodeURIComponent(name)}?file=${encodeURIComponent(file)}`, { method: 'DELETE' });
  const data = await res.json().catch(() => ({}));
  if (_handleNestedResponse(data)) return;
  if (data.error) {
    showToast(_brickErrText(data), 'error');
    return;
  }
  showToast(t('brix.deleted', name));
  await _refreshPreviewForFile(file);
}

/**
 * Deactivate or activate a brick: its body is commented out in place.
 *
 * Deleting loses the content, and NiCo has nowhere else to keep it –
 * vorgaben.txt wants NixOS data in the .nix file, so an archive in JSON is
 * out. Commenting out keeps everything where it belongs and Nix never sees
 * it. Reversible byte for byte.
 *
 * No confirmation dialog: nothing is lost, the step is undone from the same
 * menu, and every write makes a checkpoint anyway.
 */
async function setBrixInactive(name, inactive, file = 'configuration.nix') {
  const res = await csrfFetch(`/api/brick/${encodeURIComponent(name)}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ file, inactive }),
  });
  const data = await res.json().catch(() => ({}));
  if (_handleNestedResponse(data)) return;
  if (data.error) {
    showToast(_brickErrText(data), 'error');
    return;
  }
  showToast(t(inactive ? 'brix.deactivated' : 'brix.activated', name));
  await _refreshPreviewForFile(file);
}

/**
 * Bricks of one file, read fresh from disk. Single source for every dialog –
 * the lists in /api/config only cover configuration.nix, flake.nix and a
 * legacy root home.nix, so host, module and HM files got the wrong bricks.
 */
async function loadBrixFor(file) {
  const res  = await csrfFetch(`/api/brix/blocks?file=${encodeURIComponent(file)}`);
  const data = await res.json().catch(() => ({}));
  if (data.error) {
    showToast(tErr(data.error), 'error');
    return null;
  }
  return data.blocks || {};
}

// ── Nix-Brick löschen (Dialog) ───────────────────────────────────────────────
async function openBrixDelete() {
  const file   = _brixContextFile || 'configuration.nix';
  const blocks = await loadBrixFor(file);
  if (blocks === null) return;
  const names  = Object.keys(blocks);
  if (names.length === 0) {
    showToast(t('brix.noBlocks'), 'error');
    return;
  }
  const selected = await new Promise(resolve => {
    const overlay = document.createElement('div');
    overlay.className = 'overlay';
    overlay.innerHTML = `
      <div class="dialog">
        <h2 class="dialog-title" data-i18n="brix.deleteTitle">${t('brix.deleteTitle')}</h2>
        <select id="brix-delete-select" class="brix-select">
          ${names.map(n => `<option value="${escHtml(n)}">${escHtml(n)}</option>`).join('')}
        </select>
        <div class="dialog-actions">
          <button id="brix-delete-confirm" class="btn-danger">${t('brix.deleteConfirmBtn')}</button>
          <button id="brix-delete-cancel"  class="btn-secondary">${t('unsaved.cancel') || 'Abbrechen'}</button>
        </div>
      </div>`;
    document.body.appendChild(overlay);
    overlay.querySelector('#brix-delete-confirm').addEventListener('click', () => {
      const val = overlay.querySelector('#brix-delete-select').value;
      document.body.removeChild(overlay);
      resolve(val);
    });
    overlay.querySelector('#brix-delete-cancel').addEventListener('click', () => {
      document.body.removeChild(overlay);
      resolve(null);
    });
  });
  if (selected) confirmDeleteBrix(selected, file);
}

// ── Brix verschieben ────────────────────────────────────────────────────────
const FLAKE_BRICK_SECTIONS = ['HEAD', 'INPX', 'OUTX', 'TAIL'];
const HM_BRICK_SECTIONS    = ['HEAD', 'HOME', 'TAIL'];
let _brixMoveTarget = null;

function _getFlakeBrickSections(cfg = null) {
  const sections = [...FLAKE_BRICK_SECTIONS];
  const hosts = cfg?.flake_hosts
    || Array.from(document.querySelectorAll('#flake-hosts-list .flake-host-card'))
      .map(card => ({ name: card.dataset.host || '' }));
  hosts.forEach(host => {
    const name = typeof host === 'string' ? host : (host?.name || '');
    if (!name) return;
    const section = SECTION_HOST_PREFIX + name;
    if (!sections.includes(section)) sections.push(section);
  });
  return sections;
}

async function openBrixMove(targetName = null) {
  const file    = _brixContextFile || 'configuration.nix';
  const isFlake = file === 'flake.nix';
  // Bricks always come from the open file; only the selectable target
  // sections still depend on the file type.
  const blocks  = await loadBrixFor(file);
  if (blocks === null) return;
  const names   = Object.keys(blocks);
  const cfg     = isFlake
    ? await csrfFetch('/api/config').then(r => r.json()).catch(() => ({}))
    : {};

  if (names.length === 0) {
    showToast(t('brix.noBlocks'), 'error');
    return;
  }

  // Guard against being wired straight to an event handler: a click event as
  // targetName would silently preselect the first brick instead of the meant one
  const wanted = typeof targetName === 'string' ? targetName : null;
  _brixMoveTarget = (wanted && blocks[wanted]) ? wanted : (names[0] || null);
  const block = _brixMoveTarget ? blocks[_brixMoveTarget] : null;
  const sourceName = document.getElementById('brix-move-source-name');
  const cancelBtn = document.getElementById('brix-move-cancel-btn');
  if (sourceName) {
    sourceName.textContent = _brixMoveTarget
      ? `${block?.section ?? '?'} #${block?.order ?? '?'} · ${_brixMoveTarget}`
      : '';
  }
  if (cancelBtn) {
    cancelBtn.setAttribute('data-i18n', 'brix.cancelBtn');
    cancelBtn.textContent = t('brix.cancelBtn');
  }

  // Populate section select – repopulate every time (context may have changed)
  const sectionSel = document.getElementById('brix-move-section');
  if (sectionSel) {
    sectionSel.innerHTML = '';
    (isFlake ? _getFlakeBrickSections(cfg) : _brixContextFtype === 'hm' ? HM_BRICK_SECTIONS : BRICK_SECTIONS).forEach(s => {
      const opt = document.createElement('option');
      opt.value = s; opt.textContent = secLabel(s);
      sectionSel.appendChild(opt);
    });
  }

  // Pre-fill section/order from the selected brick
  if (sectionSel && block) sectionSel.value = block.section || 'TAIL';
  document.getElementById('brix-move-order').value = block?.order ?? 1;
  document.getElementById('brix-move-warning').classList.add('hidden');
  document.getElementById('brix-move-overlay').classList.remove('hidden');
}

function closeBrixMove() {
  document.getElementById('brix-move-overlay').classList.add('hidden');
  _brixMoveTarget = null;
  // Reset cancel button back to "Abbrechen"
  const cancelBtn = document.getElementById('brix-move-cancel-btn');
  if (cancelBtn) {
    cancelBtn.setAttribute('data-i18n', 'brix.cancelBtn');
    cancelBtn.textContent = t('brix.cancelBtn');
  }
}

async function moveBrix() {
  const name     = _brixMoveTarget;
  const section  = document.getElementById('brix-move-section')?.value || '';
  const orderVal = document.getElementById('brix-move-order').value.trim();
  const order    = parseInt(orderVal, 10);

  if (!name) { showToast(t('brix.noBlocks'), 'error'); return; }
  if (!section || !orderVal || isNaN(order) || order < 1) {
    document.getElementById('brix-move-warning').classList.remove('hidden');
    return;
  }

  const res  = await csrfFetch('/api/brick/move', {
    method:  'POST',
    headers: { 'Content-Type': 'application/json' },
    body:    JSON.stringify({ name, section, order, file: _brixContextFile }),
  });
  const data = await res.json();
  if (_handleNestedResponse(data)) return;
  if (data.error) {
    showToast(_brickErrText(data), 'error');
    return;
  }

  showToast(t('brix.moved', name));
  closeBrixMove();

  // Refresh preview after closing the dialog
  try {
    await _refreshPreviewForFile(_brixContextFile);
  } catch (e) {
    console.warn('moveBrix refresh failed:', e);
  }
}

// ── Brix verschieben (aus Kontextmenü, mit vorausgewähltem Brick) ──────────
async function openBrixMoveFor(name) {
  await openBrixMove(name);
}

// ── Brix umbenennen ──────────────────────────────────────────────────────────
let _brixRenameTarget = null;

function openBrixRename(name) {
  _brixRenameTarget = name;
  document.getElementById('brix-rename-new').value = name;
  document.getElementById('brix-rename-error').classList.add('hidden');
  document.getElementById('brix-rename-overlay').classList.remove('hidden');
  document.getElementById('brix-rename-new').focus();
}

function closeBrixRename() {
  document.getElementById('brix-rename-overlay').classList.add('hidden');
  _brixRenameTarget = null;
}

async function renameBrix() {
  const newName = document.getElementById('brix-rename-new').value.trim();
  const errorEl = document.getElementById('brix-rename-error');
  errorEl.classList.add('hidden');

  if (!newName) {
    errorEl.textContent = t('brix.errNoName');
    errorEl.classList.remove('hidden');
    return;
  }
  if (!/^[\w\-]+$/.test(newName)) {
    errorEl.textContent = t('brix.errInvalidName');
    errorEl.classList.remove('hidden');
    return;
  }

  const res  = await csrfFetch('/api/brick/rename', {
    method:  'POST',
    headers: { 'Content-Type': 'application/json' },
    body:    JSON.stringify({ old_name: _brixRenameTarget, new_name: newName, file: _brixContextFile }),
  });
  const data = await res.json();
  if (_handleNestedResponse(data)) { closeBrixRename(); return; }
  if (data.error) {
    errorEl.textContent = _brickErrText(data);
    errorEl.classList.remove('hidden');
    return;
  }
  closeBrixRename();
  showToast(t('brix.renamed', data.new_name));
  await _refreshPreviewForFile(_brixContextFile);
}

// ── Brix splitten ───────────────────────────────────────────────────────────
let _brixSplitTarget = null;

function openBrixSplit(name) {
  _brixSplitTarget = name;
  document.getElementById('brix-split-line').value = '1';
  document.getElementById('brix-split-error').classList.add('hidden');
  document.getElementById('brix-split-overlay').classList.remove('hidden');
  document.getElementById('brix-split-line').focus();
}

function closeBrixSplit() {
  document.getElementById('brix-split-overlay').classList.add('hidden');
  _brixSplitTarget = null;
}

async function splitBrix() {
  const splitLine = parseInt(document.getElementById('brix-split-line').value, 10);
  const errorEl   = document.getElementById('brix-split-error');
  errorEl.classList.add('hidden');

  if (isNaN(splitLine) || splitLine < 1) {
    errorEl.textContent = t('brix.errSplitLine');
    errorEl.classList.remove('hidden');
    return;
  }

  const newName = `${_brixSplitTarget}-2`;
  const res  = await csrfFetch('/api/brick/split', {
    method:  'POST',
    headers: { 'Content-Type': 'application/json' },
    body:    JSON.stringify({ name: _brixSplitTarget, split_line: splitLine, new_name: newName, file: _brixContextFile }),
  });
  const data = await res.json();
  if (_handleNestedResponse(data)) { closeBrixSplit(); return; }
  if (data.error) {
    errorEl.textContent = _brickErrText(data);
    errorEl.classList.remove('hidden');
    return;
  }
  closeBrixSplit();
  showToast(t('brix.split', data.new_name));
  await _refreshPreviewForFile(_brixContextFile);
}

// ── Brix inline editing ──────────────────────────────────────────────────────

function openBrixInlineEdit(name, currentContent, wrapEl, bodyWrapper, file = 'configuration.nix') {
  bodyWrapper.style.display = 'none';

  const editor = document.createElement('div');
  editor.className = 'brix-inline-editor';

  const textarea = document.createElement('textarea');
  textarea.className = 'brix-inline-textarea';
  textarea.value = currentContent;
  textarea.rows  = Math.max(5, currentContent.split('\n').length + 2);

  const actions = document.createElement('div');
  actions.className = 'brix-edit-actions';

  const saveBtn = document.createElement('button');
  saveBtn.className   = 'btn-primary';
  saveBtn.textContent = t('brix.editSave');
  saveBtn.addEventListener('click', async () => {
    saveBtn.disabled = true;
    const ok = await saveBrixInline(name, textarea.value, file);
    if (ok) {
      bodyWrapper.style.display = '';
      editor.remove();
      await _refreshPreviewForFile(file);
    } else {
      saveBtn.disabled = false;
    }
  });

  const cancelBtn = document.createElement('button');
  cancelBtn.className   = 'btn-secondary';
  cancelBtn.textContent = t('brix.editCancel');
  cancelBtn.addEventListener('click', () => {
    bodyWrapper.style.display = '';
    editor.remove();
  });

  actions.appendChild(saveBtn);
  actions.appendChild(cancelBtn);
  editor.appendChild(textarea);
  editor.appendChild(actions);
  wrapEl.appendChild(editor);
  textarea.focus();
}

async function saveBrixInline(name, content, file = 'configuration.nix') {
  const res  = await csrfFetch(`/api/brick/${encodeURIComponent(name)}`, {
    method:  'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body:    JSON.stringify({ content, file }),
  });
  const data = await res.json();
  if (_handleNestedResponse(data)) return false;
  if (data.error) {
    let msg = _brickErrText(data);
    if (data.details?.length) msg += ': ' + _bracketDetails(data.details);
    showToast(msg, 'error');
    return false;
  }
  showToast(t('brix.editSaved'));
  return true;
}

/**
 * Shared handling for every brick write. Returns true when the write was
 * refused for nesting – the caller must then stop, nothing was written.
 * A nesting that was already in the file is only reported, never blocking:
 * otherwise a single bad file would lock the user out of all its bricks.
 */
function _handleNestedResponse(data) {
  if (data?.error === 'ERR_BRICK_NESTED') {
    _showNestedBricksModal(data.nested, 'refused');
    return true;
  }
  reportNestedBricks(data?.nested);
  return false;
}
