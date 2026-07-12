/** NiCo frontend — sidebar & active file handling. Split from app.js; classic script sharing the global scope. */
'use strict';

// ══════════════════════════════════════════════════════════════════
// SIDEBAR & AKTIVE DATEI
// ══════════════════════════════════════════════════════════════════

const Sidebar = (() => {

  // State
  let activeFile  = null;   // {path, name} oder null
  let _formDirty      = false;  // config-form hat ungespeicherte Änderungen
  let _flakeFormDirty = false;  // flake-form hat ungespeicherte Änderungen

  // DOM refs (populated after DOMContentLoaded)
  let elSidebar, elToggleBtn, elTree, elPreviewPanel, elSaveAllBtn, elRefreshBtn;

  // ── Find-Bar state ─────────────────────────────────────────────
  let findMatches  = [];
  let findIndex    = 0;
  let elFindBar, elFindInput, elFindCount;

  function initFindBar() {
    elFindBar   = document.getElementById('find-bar');
    elFindInput = document.getElementById('find-input');
    elFindCount = document.getElementById('find-count');

    document.getElementById('find-close-btn').addEventListener('click', closeFindBar);
    document.getElementById('find-prev-btn').addEventListener('click', () => findStep(-1));
    document.getElementById('find-next-btn').addEventListener('click', () => findStep(1));
    elFindInput.addEventListener('input', runFind);
    elFindInput.addEventListener('keydown', e => {
      if (e.key === 'Enter') { e.preventDefault(); findStep(e.shiftKey ? -1 : 1); }
      if (e.key === 'Escape') closeFindBar();
    });
  }

  function openFindBar() {
    if (!activeFile) return;
    elFindBar.classList.remove('hidden');
    elFindInput.focus();
    elFindInput.select();
    runFind();
  }

  function closeFindBar() {
    elFindBar.classList.add('hidden');
    elFindInput.value = '';
    findMatches = [];
    elFindCount.textContent = '';
  }

  function runFind() {
    const needle = elFindInput.value;
    const ta = _activeTextarea();
    if (!ta || !needle) {
      findMatches = [];
      elFindCount.textContent = needle ? '0 / 0' : '';
      return;
    }
    const text  = ta.value;
    const lower = text.toLowerCase();
    const q     = needle.toLowerCase();
    findMatches = [];
    let pos = 0;
    while ((pos = lower.indexOf(q, pos)) !== -1) {
      findMatches.push(pos);
      pos += q.length;
    }
    findIndex = findMatches.length ? 0 : -1;
    elFindCount.textContent = findMatches.length
      ? `${findIndex + 1} / ${findMatches.length}`
      : '0 Treffer';
    if (findMatches.length) _highlightMatch(ta, findMatches[0], needle.length);
  }

  function findStep(dir) {
    if (!findMatches.length) return;
    findIndex = (findIndex + dir + findMatches.length) % findMatches.length;
    elFindCount.textContent = `${findIndex + 1} / ${findMatches.length}`;
    _highlightMatch(_activeTextarea(), findMatches[findIndex], elFindInput.value.length);
  }

  function _highlightMatch(ta, start, len) {
    if (!ta) return;
    ta.focus();
    ta.setSelectionRange(start, start + len);
    const linesBefore = ta.value.substring(0, start).split('\n').length - 1;
    const lineHeight  = parseInt(getComputedStyle(ta).lineHeight) || 22;
    ta.scrollTop = Math.max(0, linesBefore * lineHeight - ta.clientHeight / 2);
  }

  function _activeTextarea() {
    // Find-Bar only makes sense in the raw-view; return null if none active
    const rawView = document.getElementById('raw-file-view');
    if (!rawView) return null;
    return rawView.querySelector('textarea') || null;
  }

  function init() {
    elSidebar      = document.getElementById('sidebar');
    elToggleBtn    = document.getElementById('sidebar-toggle-btn');
    elTree         = document.getElementById('sidebar-tree');
    elPreviewPanel = document.getElementById('preview-panel');
    elSaveAllBtn   = document.getElementById('save-all-btn');
    elRefreshBtn   = document.getElementById('sidebar-refresh-btn');

    if (!elSidebar) return; // not on this page

    elToggleBtn.addEventListener('click', toggleSidebar);
    elRefreshBtn.addEventListener('click', () => loadTree());
    document.getElementById('sidebar-expand-all-btn')?.addEventListener('click', () => expandCollapseAll(true));
    document.getElementById('sidebar-collapse-all-btn')?.addEventListener('click', () => expandCollapseAll(false));

    // Sidebar standardmäßig geöffnet
    elToggleBtn.classList.add('active');
    loadTree();

    // Track form dirty state
    document.getElementById('config-form')?.addEventListener('input', () => { _formDirty = true; });
    document.addEventListener('nico:config-dirty', () => { _formDirty = true; });
    document.addEventListener('nico:config-saved', () => { _formDirty = false; });

    initFindBar();

    // Tools-Dropdown
    const toolsBtn      = document.getElementById('tools-btn');
    const toolsDropdown = document.getElementById('tools-dropdown');
    if (toolsBtn && toolsDropdown) {
      toolsBtn.addEventListener('click', e => {
        e.stopPropagation();
        toolsDropdown.classList.toggle('hidden');
      });
      document.addEventListener('click', () => toolsDropdown.classList.add('hidden'));
    }

    document.getElementById('tool-reload')?.addEventListener('click', () => {
      toolsDropdown?.classList.add('hidden');
      _reloadFile();
    });

    document.getElementById('tool-copy-file')?.addEventListener('click', async () => {
      toolsDropdown?.classList.add('hidden');
      if (!activeFile) return;
      try {
        const res  = await csrfFetch(`/api/file?path=${encodeURIComponent(activeFile.path)}`);
        const data = await res.json();
        if (data.error) { showToast(tErr(data.error), 'error'); return; }
        await navigator.clipboard.writeText(data.content);
        showToast(t('tools.fileCopied'), 'success');
      } catch {
        showToast(t('toast.error'), 'error');
      }
    });

    document.getElementById('tool-code-search')?.addEventListener('click', () => {
      toolsDropdown?.classList.add('hidden');
      openFindBar();
    });

    document.getElementById('tool-set-type')?.addEventListener('click', () => {
      toolsDropdown?.classList.add('hidden');
      openSetType();
    });

    document.getElementById('tool-brix-insert')?.addEventListener('click', () => {
      toolsDropdown?.classList.add('hidden');
      openBrixInsert();
    });
  }

  // ── Panel-Toggle ──────────────────────────────────────────────────

  let _panelToggleBusy = false;

  document.getElementById('panel-toggle-check')?.addEventListener('change', async (e) => {
    if (!activeFile) return;
    if (_panelToggleBusy) { e.target.checked = !e.target.checked; return; }
    _panelToggleBusy = true;
    const btn      = document.getElementById('panel-toggle-btn');
    const curMode  = btn?.dataset.mode || 'p';
    const newMode  = curMode === 'r' ? 'p' : 'r';

    // If currently in raw mode, include any unsaved edits in the request
    let bodyContent = null;
    if (_rawEditPath === activeFile.path) {
      const editorBody = document.getElementById('raw-file-editor')?.value ?? '';
      bodyContent = _rawEditHeader ? _rawEditHeader + '\n' + editorBody : editorBody;
    }

    try {
      const payload = { path: activeFile.path, mode: newMode };
      if (bodyContent !== null) payload.content = bodyContent;
      const res  = await csrfFetch('/api/file/panel-mode', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      const data = await res.json();
      if (data.error) {
        e.target.checked = !e.target.checked;
        if (data.error === 'ERR_BRIX_INCOMPLETE') {
          showToast(t('panelToggle.brixError'), 'error');
        } else {
          showToast(tErr(data.error), 'error');
        }
        return;
      }
      await _loadFileIntoView(activeFile.path, { skipTypeDialog: true });
    } catch {
      e.target.checked = !e.target.checked;
      showToast(t('toast.error'), 'error');
    } finally {
      _panelToggleBusy = false;
    }
  });

  // ── Dateityp-Dialog ──────────────────────────────────────────────

  const _FILE_TYPES = [
    { code: 'co', label: 'co',  i18n: 'setType.co' },
    { code: 'nd', label: 'nd',  i18n: 'setType.nd' },
    { code: 'fl', label: 'fl',  i18n: 'setType.fl' },
    { code: 'mo', label: 'mo',  i18n: 'setType.mo' },
    { code: 'hm', label: 'hm',  i18n: 'setType.hm' },
    { code: 'hw', label: 'hw',  i18n: 'setType.hw' },
  ];

  function openSetType() {
    if (!activeFile) return;
    const overlay = document.getElementById('set-type-overlay');
    const grid    = document.getElementById('set-type-grid');
    if (!overlay || !grid) return;

    const currentType = document.querySelector(`.tree-file[data-path="${CSS.escape(activeFile.path)}"]`)
                          ?.dataset.fileType || null;

    grid.innerHTML = '';
    _FILE_TYPES.forEach(({ code, i18n }) => {
      const btn = document.createElement('button');
      btn.className = `set-type-btn ft-${code}${code === currentType ? ' active' : ''}`;
      btn.dataset.code = code;
      btn.innerHTML = `<span class="set-type-code">${code}</span><span class="set-type-label" data-i18n="${i18n}">${t(i18n)}</span>`;
      btn.addEventListener('click', () => _applyFileType(code, overlay));
      grid.appendChild(btn);
    });

    overlay.classList.remove('hidden');
    document.getElementById('set-type-cancel')?.addEventListener('click', () => {
      overlay.classList.add('hidden');
    }, { once: true });
  }

  async function _applyFileType(ftype, overlay) {
    if (!activeFile) return;

    // 1. Typ auf dem Server setzen
    let data;
    try {
      const res = await csrfFetch('/api/file/set-type', {
        method:  'POST',
        headers: { 'Content-Type': 'application/json' },
        body:    JSON.stringify({ path: activeFile.path, file_type: ftype }),
      });
      data = await res.json();
    } catch (e) {
      showToast(tErr('ERR_FILE_WRITE'), 'error');
      return;
    }
    if (data.error) { showToast(tErr(data.error), 'error'); return; }

    // 2. Erfolg: Dialog schließen, Toast zeigen, Sidebar aktualisieren
    overlay.classList.add('hidden');
    showToast(t('setType.saved'), 'success');
    const el = document.querySelector(`.tree-file[data-path="${CSS.escape(activeFile.path)}"]`);
    if (el) {
      el.dataset.fileType = ftype;
      el.className = `tree-file active ft-${ftype}`;
    }

    // 3. Datei neu laden → Panel umschalten + Inhalt zeigen.
    //    Für nicht-CO-Typen: Panel sofort umschalten (vor dem Netzwerk-Round-Trip).
    //    Für CO: _loadFileIntoView zeigt das CO-Formular nach dem Laden.
    if (ftype !== 'co') _showLeftPanel(ftype, activeFile.name);
    try {
      await _loadFileIntoView(activeFile.path, { skipTypeDialog: true });
    } catch (e) { /* non-fatal */ }
  }

  // ── Sidebar toggle ───────────────────────────────────────────────

  function toggleSidebar() {
    const isHidden = elSidebar.classList.toggle('hidden');
    elToggleBtn.classList.toggle('active', !isHidden);
    if (!isHidden) {
      loadTree();
    }
  }

  // ── Tree loading ─────────────────────────────────────────────────

  async function loadTree() {
    elTree.innerHTML = '';
    _closeTreeContextMenu();
    try {
      const res  = await fetch('/api/files');
      const data = await res.json();
      if (data.error) {
        elTree.innerHTML = `<div class="sidebar-empty">${tErr(data.error)}</div>`;
        return;
      }
      if (!data.tree || data.tree.length === 0) {
        elTree.innerHTML = `<div class="sidebar-empty">${t('sidebar.empty')}</div>`;
        return;
      }
      const rootName = (data.root || '').split('/').filter(Boolean).pop() || t('sidebar.rootLabel');
      renderTree([{
        name: rootName,
        type: 'dir',
        path: '',
        is_root: true,
        open: true,
        children: data.tree,
      }], elTree);
    } catch (e) {
      elTree.innerHTML = `<div class="sidebar-empty">${t('sidebar.loadError')}</div>`;
    }
  }

  function expandCollapseAll(open) {
    elTree.querySelectorAll('.tree-children').forEach(ch => {
      ch.style.display = open ? 'block' : 'none';
    });
    elTree.querySelectorAll('.tree-dir-toggle').forEach(t => {
      t.classList.toggle('open', open);
    });
  }

  function _closeTreeContextMenu() {
    if (_treeContextMenu) {
      if (_treeContextMenu._triggerBtn) {
        _treeContextMenu._triggerBtn.setAttribute('aria-expanded', 'false');
      }
      _treeContextMenu.remove();
      _treeContextMenu = null;
    }
  }

  function _openTreeContextMenu(triggerBtn, buildItems) {
    if (_treeContextMenu?._triggerBtn === triggerBtn) {
      _closeTreeContextMenu();
      return;
    }
    _closeTreeContextMenu();

    const menu = document.createElement('div');
    menu.className = 'tree-context-menu';
    menu.addEventListener('click', e => e.stopPropagation());
    buildItems(menu);
    const rect = triggerBtn.getBoundingClientRect();
    menu.style.top = `${Math.min(rect.bottom + 4, window.innerHeight - 12)}px`;
    menu.style.left = `${Math.max(12, rect.right - 220)}px`;
    menu._triggerBtn = triggerBtn;
    triggerBtn.setAttribute('aria-expanded', 'true');
    document.body.appendChild(menu);
    _treeContextMenu = menu;
  }

  function _appendTreeMenuItem(menu, label, onClick) {
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'tree-menu-item';
    btn.textContent = label;
    btn.addEventListener('click', async e => {
      e.stopPropagation();
      _closeTreeContextMenu();
      await onClick();
    });
    menu.appendChild(btn);
  }

  async function _pickHardwareConfigForDir(targetDir, targetLabel = '') {
    let data;
    try {
      const res = await fetch('/api/files/hardware-configs');
      data = await res.json();
    } catch (e) {
      showToast(t('sidebar.loadError'), 'error');
      return;
    }

    if (data.error) {
      showToast(tErr(data.error), 'error');
      return;
    }

    const configs = Array.isArray(data.configs)
      ? data.configs.filter(item => !item.inside_config)
      : [];

    const selected = await new Promise(resolve => {
      const overlay = document.createElement('div');
      overlay.className = 'overlay';
      const renderOptions = () => {
        const filtered = configs.filter(item => !item.inside_config);
        return filtered.length
          ? filtered.map(cfg => `<option value="${escHtml(cfg.path)}">${escHtml(cfg.label)}</option>`).join('')
          : `<option value="">${escHtml(t('sidebar.hwImportEmptyList'))}</option>`;
      };
      overlay.innerHTML = `
        <div class="dialog tree-import-dialog">
          <h2 class="dialog-title">${escHtml(t('sidebar.hwImportTitle'))}</h2>
          <p class="dialog-info">${escHtml(t('sidebar.hwImportInfo').replace('{dir}', targetLabel || t('sidebar.rootLabel')))}</p>
          <div class="tree-import-manual">
            <input id="tree-hw-import-path" type="text" value="" placeholder="${escHtml(t('sidebar.hwImportPathPlaceholder'))}" spellcheck="false">
            <button id="tree-hw-import-browse" type="button" class="btn-secondary">${t('sidebar.hwImportBrowse')}</button>
          </div>
          <div id="tree-hw-import-status" class="tree-import-status hidden"></div>
          <select id="tree-hw-import-select" class="brix-select">
            ${renderOptions()}
          </select>
          <div class="dialog-actions">
            <button id="tree-hw-import-ok" class="btn-primary">${t('sidebar.hwImportAction')}</button>
            <button id="tree-hw-import-cancel" class="btn-secondary">${t('unsaved.cancel')}</button>
          </div>
        </div>`;
      document.body.appendChild(overlay);
      const selectEl = overlay.querySelector('#tree-hw-import-select');
      const inputEl = overlay.querySelector('#tree-hw-import-path');
      const statusEl = overlay.querySelector('#tree-hw-import-status');
      const setStatus = (message, isError = false) => {
        if (!statusEl) return;
        statusEl.textContent = message;
        statusEl.classList.remove('hidden', 'error');
        if (isError) statusEl.classList.add('error');
      };
      const refreshOptions = () => {
        const filtered = configs.filter(item => !item.inside_config);
        if (!selectEl) return;
        selectEl.innerHTML = filtered.length
          ? filtered.map(cfg => `<option value="${escHtml(cfg.path)}">${escHtml(cfg.label)}</option>`).join('')
          : `<option value="">${escHtml(t('sidebar.hwImportEmptyList'))}</option>`;
        selectEl.disabled = !filtered.length;
      };

      overlay.querySelector('#tree-hw-import-browse')?.addEventListener('click', async () => {
        const raw = inputEl?.value?.trim() || '';
        if (!raw) {
          setStatus(tErr('ERR_NO_PATH'), true);
          return;
        }
        try {
          const res = await csrfFetch('/api/files/hardware-configs/check', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ path: raw }),
          });
          const result = await res.json();
          if (result.error) {
            setStatus(tErr(result.error), true);
            return;
          }
          const cfg = result.config;
          if (!cfg || cfg.inside_config) {
            setStatus(t('sidebar.hwImportAlreadyListed'), true);
            return;
          }
          if (!configs.some(item => item.path === cfg.path)) {
            configs.unshift(cfg);
          }
          refreshOptions();
          selectEl.value = cfg.path;
          setStatus(t('sidebar.hwImportAdded'));
        } catch (e) {
          setStatus(t('toast.error'), true);
        }
      });
      overlay.querySelector('#tree-hw-import-ok')?.addEventListener('click', () => {
        const value = selectEl?.value || '';
        document.body.removeChild(overlay);
        resolve(value || null);
      });
      overlay.querySelector('#tree-hw-import-cancel')?.addEventListener('click', () => {
        document.body.removeChild(overlay);
        resolve(null);
      });
    });

    if (!selected) return;

    try {
      const res = await csrfFetch('/api/files/import-hardware', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          target_dir: targetDir,
          source_path: selected,
        }),
      });
      const result = await res.json();
      if (result.error) {
        showToast(tErr(result.error), 'error');
        return;
      }
      showToast(
        result.backup_created ? t('sidebar.hwImportSuccessBackup') : t('sidebar.hwImportSuccess'),
        'success',
      );
      await loadTree();
      if (result.target_path) {
        const fileName = result.target_path.split('/').pop();
        await selectFile(result.target_path, fileName, { force: true });
      }
    } catch (e) {
      showToast(t('toast.error'), 'error');
    }
  }

  async function _createTreeEntry(parentPath, type) {
    const label = type === 'dir' ? t('sidebar.newDirPrompt') : t('sidebar.newFilePrompt');
    const value = window.prompt(label, type === 'dir' ? '' : 'default.nix');
    if (!value) return;

    try {
      const res = await csrfFetch('/api/files/create', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          parent_path: parentPath,
          name: value.trim(),
          type,
        }),
      });
      const data = await res.json();
      if (data.error) {
        showToast(tErr(data.error), 'error');
        return;
      }
      await loadTree();
      if (data.type === 'file' && data.path) {
        const fileName = data.path.split('/').pop();
        await selectFile(data.path, fileName, { force: true });
      }
    } catch (e) {
      showToast(t('toast.error'), 'error');
    }
  }

  async function _renameTreeEntry(entry) {
    const label = window.prompt(t('sidebar.renamePrompt'), entry.name);
    if (!label || label.trim() === entry.name) return;

    try {
      const res = await csrfFetch('/api/files/rename', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          path: entry.path,
          new_name: label.trim(),
        }),
      });
      const data = await res.json();
      if (data.error) {
        showToast(tErr(data.error), 'error');
        return;
      }
      await loadTree();
      if (activeFile?.path === entry.path && data.new_path) {
        const fileName = data.new_path.split('/').pop();
        await selectFile(data.new_path, fileName, { force: true });
      }
    } catch (e) {
      showToast(t('toast.error'), 'error');
    }
  }

  async function _deleteTreeEntry(entry) {
    if (!confirm(t('sidebar.deleteConfirm').replace('{name}', entry.name))) return;

    try {
      const res = await csrfFetch('/api/files/delete', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ path: entry.path }),
      });
      const data = await res.json();
      if (data.error) {
        showToast(tErr(data.error), 'error');
        return;
      }
      if (activeFile?.path === entry.path || activeFile?.path?.startsWith(`${entry.path}/`)) {
        activeFile = null;
        _updateActiveFileLabel();
      }
      await loadTree();
    } catch (e) {
      showToast(t('toast.error'), 'error');
    }
  }

  function _openEntryMenu(entry, triggerBtn) {
    _openTreeContextMenu(triggerBtn, menu => {
      if (entry.type === 'dir') {
        _appendTreeMenuItem(menu, t('sidebar.newDir'), async () => _createTreeEntry(entry.path, 'dir'));
        _appendTreeMenuItem(menu, t('sidebar.newFile'), async () => _createTreeEntry(entry.path, 'file'));
        _appendTreeMenuItem(menu, t('sidebar.hwImportMenu'), async () => _pickHardwareConfigForDir(entry.path, entry.name));
      }
      if (!entry.is_root) {
        _appendTreeMenuItem(menu, t('sidebar.rename'), async () => _renameTreeEntry(entry));
        _appendTreeMenuItem(menu, t('sidebar.delete'), async () => _deleteTreeEntry(entry));
      }
    });
  }

  function _buildTreeActions(entry, rowEl) {
    const actions = document.createElement('div');
    actions.className = 'tree-entry-actions';

    const menuBtn = document.createElement('button');
    menuBtn.type = 'button';
    menuBtn.className = 'tree-action-btn tree-menu-btn';
    menuBtn.title = entry.type === 'dir' ? t('sidebar.dirMenuTitle') : t('sidebar.fileMenuTitle');
    menuBtn.setAttribute('aria-expanded', 'false');
    menuBtn.innerHTML = niIcon('more-vertical');
    menuBtn.addEventListener('click', e => {
      e.stopPropagation();
      _openEntryMenu(entry, menuBtn);
    });

    rowEl.addEventListener('contextmenu', e => {
      e.preventDefault();
      e.stopPropagation();
      _openEntryMenu(entry, menuBtn);
    });

    actions.appendChild(menuBtn);
    return actions;
  }

  function _buildDirRow(entry, childEl) {
    const dirEl = document.createElement('div');
    dirEl.className = 'tree-dir';
    dirEl.dataset.path = entry.path;

    const toggle = document.createElement('button');
    toggle.type = 'button';
    toggle.className = 'tree-dir-toggle ni-icon ni-icon-chevron-right';
    toggle.setAttribute('aria-label', entry.name);

    const name = document.createElement('span');
    name.className = 'tree-entry-name';
    name.textContent = entry.name;
    if (entry.is_root) dirEl.classList.add('tree-root');

    dirEl.addEventListener('click', e => {
      e.stopPropagation();
      const open = childEl.style.display !== 'none';
      childEl.style.display = open ? 'none' : 'block';
      toggle.classList.toggle('open', !open);
      _closeTreeContextMenu();
    });

    dirEl.appendChild(toggle);
    dirEl.appendChild(name);
    dirEl.appendChild(_buildTreeActions(entry, dirEl));
    return dirEl;
  }

  function _buildFileRow(entry) {
    const fileEl = document.createElement('div');
    const ftype = entry.file_type || 'unknown';
    fileEl.className = `tree-file ft-${ftype}`;
    fileEl.dataset.path = entry.path;
    fileEl.dataset.fileType = ftype;

    const name = document.createElement('span');
    name.className = 'tree-entry-name';
    name.textContent = entry.name;
    fileEl.appendChild(name);
    fileEl.appendChild(_buildTreeActions(entry, fileEl));

    if (activeFile && activeFile.path === entry.path) {
      fileEl.classList.add('active');
    }

    fileEl.addEventListener('click', () => {
      _closeTreeContextMenu();
      selectFile(entry.path, entry.name);
    });
    return fileEl;
  }

  function renderTree(entries, container) {
    entries.forEach(entry => {
      if (entry.type === 'dir') {
        const childEl = document.createElement('div');
        childEl.className = 'tree-children';
        childEl.style.display = entry.open ? 'block' : 'none';
        renderTree(entry.children, childEl);
        const dirEl = _buildDirRow(entry, childEl);
        dirEl.querySelector('.tree-dir-toggle')?.classList.toggle('open', !!entry.open);

        container.appendChild(dirEl);
        container.appendChild(childEl);
        return;
      }
      container.appendChild(_buildFileRow(entry));
    });
  }

  function updateTreeHighlights() {
    document.querySelectorAll('.tree-file').forEach(el => {
      const isActive = !!activeFile && el.dataset.path === activeFile.path;
      const ftype = el.dataset.fileType || 'unknown';
      el.className = `tree-file ft-${ftype}${isActive ? ' active' : ''}`;
    });
  }

  // ── Aktive Datei ─────────────────────────────────────────────────

  async function selectFile(path, name, { force = false } = {}) {
    if (!force && activeFile && activeFile.path === path) return;

    if (_formDirty) {
      const ok = await _autoSave();
      if (!ok) return;
      _formDirty = false;
    }
    if (_flakeFormDirty) {
      const ok = await _flakeSave();
      if (!ok) return;
    }
    // Ungespeicherte Raw-Edits vor dem Wechsel sichern (Formulare werden oben
    // auto-gespeichert; der Raw-Editor darf nicht stillschweigend verlieren)
    if (_rawEditPath) {
      const rawTa = document.getElementById('raw-file-editor');
      if (rawTa && rawTa.value !== _rawEditOrig) {
        const ok = await _saveRawFile();
        if (!ok) return;
      }
    }

    activeFile = { path, name };
    _brixTargetFile  = path;
    _brixContextFile = path;
    _updateActiveFileLabel();
    updateTreeHighlights();
    await _loadFileIntoView(path);
  }

  function _updateActiveFileLabel() {
    const el = document.getElementById('active-file-label');
    if (!el) return;
    if (activeFile) {
      el.textContent = activeFile.name;
      el.title = activeFile.path;
      el.classList.add('has-file');
    } else {
      el.textContent = '';
      el.classList.remove('has-file');
    }
  }

  // Aktiver Pfad der gerade im Raw-Editor angezeigten Datei
  let _rawEditPath   = null;
  // Header-Zeilen (nico-version + NiCo-Kommentare) der aktuellen Raw-Datei
  let _rawEditHeader = '';
  // Editor-Inhalt beim Öffnen der Raw-Ansicht (für Dirty-Erkennung beim Dateiwechsel)
  let _rawEditOrig   = '';
  // Panel-Default aus Config-Settings ('p' oder 'r')
  let _panelDefault  = 'p';

  function _splitNicoHeader(content) {
    const lines = content.split('\n');
    let headerEnd = -1;
    for (let i = 0; i < Math.min(lines.length, 6); i++) {
      if (lines[i].startsWith('# nico-version:')) { headerEnd = i; break; }
    }
    if (headerEnd === -1) return { header: '', body: content };
    const header = lines.slice(0, headerEnd + 1).join('\n');
    const body   = lines.slice(headerEnd + 1).join('\n');
    return { header, body: body.startsWith('\n') ? body.slice(1) : body };
  }

  function _getPanelModeFromContent(content) {
    const m = content.match(/^# nico-version: (?:[a-z]+#)?[0-9a-f]{8}#([pr])/m);
    return m ? m[1] : null;
  }

  function _effectivePanelMode(content) {
    return _getPanelModeFromContent(content) ?? _panelDefault;
  }

  function _updatePanelToggle(ftype, content) {
    const toggle = document.getElementById('panel-toggle');
    const btn    = document.getElementById('panel-toggle-btn');
    const label  = document.getElementById('panel-toggle-label');
    if (!toggle || !btn) return;
    const supportsPanels = ['co', 'fl', 'hm'].includes(ftype);
    toggle.classList.toggle('hidden', !supportsPanels);
    if (!supportsPanels) return;
    const mode    = _effectivePanelMode(content);
    const isPanel = mode !== 'r';
    btn.dataset.mode = mode;
    if (label) label.textContent = isPanel ? t('panelToggle.panel') : t('panelToggle.off');
    const check = document.getElementById('panel-toggle-check');
    if (check) check.checked = isPanel;
  }

  function _setRawModeUI(isRaw) {
    document.getElementById('preview-mode-btn')?.classList.toggle('hidden', isRaw);
    document.getElementById('tool-set-type')?.classList.toggle('hidden', isRaw);
    document.getElementById('tool-brix-sep')?.classList.toggle('hidden', isRaw);
    document.getElementById('tool-brix-insert')?.classList.toggle('hidden', isRaw);
  }

  async function _renderFileIntoView(path, data, { skipTypeDialog = false } = {}) {
    const fileName = path.split('/').pop();
    const ftype    = data.file_type || 'unknown';

    _updatePanelToggle(ftype, data.content || '');

    // Panel-capable files respect the #p/#r flag
    const panelCapable = ['co', 'fl', 'hm'].includes(ftype);
    if (panelCapable && _effectivePanelMode(data.content || '') === 'r') {
      _showRawView(data.content || '', fileName, path);
      _showLeftPanel('panel-off', fileName, { panelOff: true, flagSource: _getPanelModeFromContent(data.content || '') !== null ? 'file' : 'settings' });
      _setRawModeUI(true);
      return;
    }
    _setRawModeUI(false);

    if (ftype === 'co') {
      _clearRawView();
      _showLeftPanel('form');
      switchTab('configuration');
      _brixTargetFtype  = 'co';
      _brixContextFtype = 'co';
      const hostMatch = matchHostCoPath(path);
      if (fileName === 'configuration.nix' && !path.includes('/')) {
        _activeHost = '';
        await _populateCoFormFromFile(path, data.content);
        await updatePreview();
      } else if (hostMatch) {
        _activeHost = hostMatch[1];
        await _populateCoFormFromFile(path, data.content);
        await updatePreview();
      } else {
        _activeHost = '';
        await _populateCoFormFromFile(path, data.content);
        renderCodePreview(data.content, 'preview-configuration', path);
      }
    } else if (ftype === 'fl') {
      _clearRawView();
      _showLeftPanel('fl');
      switchTab('flake');
      _brixTargetFtype  = 'fl';
      _brixContextFtype = 'fl';
      await _populateFlakeFormFromFile(data.content);
    } else if (ftype === 'flk') {
      _showFlkView(data.content);
      _showLeftPanel('flk');
    } else if (ftype === 'hm') {
      _clearRawView();
      _showLeftPanel('hm', fileName);
      document.getElementById('hm-tab')?.classList.remove('hidden');
      _brixTargetFile  = path;
      _brixContextFile = path;
      _brixTargetFtype = 'hm';
      _brixContextFtype = 'hm';
      renderCodePreview(data.content, 'preview-hm', path);
      switchTab('hm');
      _populateHmPanel(data.content, path);
    } else {
      _showRawView(data.content, fileName, path);
      _showLeftPanel(ftype, fileName);
      if (data.file_type === null && !skipTypeDialog) openSetType();
    }
    document.querySelectorAll('.tree-file').forEach(el => {
      if (el.dataset.path === path) {
        el.dataset.fileType = ftype;
        el.className = `tree-file active ft-${ftype}`;
      }
    });
  }

  async function _loadFileIntoView(path, { skipTypeDialog = false } = {}) {
    try {
      const res  = await csrfFetch(`/api/file?path=${encodeURIComponent(path)}`);
      const data = await res.json();
      // Abbruch falls der Nutzer zwischenzeitlich eine andere Datei gewählt hat
      if (activeFile?.path !== path) return;
      if (data.error) { showToast(tErr(data.error), 'error'); return; }
      await _renderFileIntoView(path, data, { skipTypeDialog });
    } catch (e) {
      showToast(t('sidebar.loadError'), 'error');
    }
  }

  function _showLeftPanel(mode, fileName, { panelOff = false, flagSource = null } = {}) {
    // Hide all panels
    document.getElementById('config-form')?.classList.add('hidden');
    document.getElementById('flake-form')?.classList.add('hidden');
    document.getElementById('raw-panel')?.classList.add('hidden');
    document.querySelectorAll('.type-panel').forEach(p => p.classList.add('hidden'));

    if (mode === 'form') {
      document.getElementById('config-form')?.classList.remove('hidden');
    } else if (mode === 'fl') {
      document.getElementById('flake-form')?.classList.remove('hidden');
    } else if (mode === 'raw' || mode === 'panel-off') {
      const rawPanel = document.getElementById('raw-panel');
      rawPanel?.classList.remove('hidden');
      const hwWarn       = document.getElementById('raw-panel-hw-warn');
      const regularHint  = document.getElementById('raw-panel-regular-hint');
      const offHint      = document.getElementById('panel-off-hint');
      const offSource    = document.getElementById('panel-off-source');
      hwWarn?.classList.toggle('hidden', panelOff || fileName !== 'hardware-configuration.nix');
      regularHint?.classList.toggle('hidden', panelOff);
      offHint?.classList.toggle('hidden', !panelOff);
      if (offSource) {
        offSource.classList.toggle('hidden', !panelOff);
        if (panelOff && flagSource) {
          offSource.textContent = flagSource === 'file' ? t('panelToggle.sourceFile') : t('panelToggle.sourceSettings');
        }
      }
    } else {
      document.getElementById(`panel-${mode}`)?.classList.remove('hidden');
    }
  }

  // ── Panel-Listener-Bereinigung (AbortController) ─────────────────
  let _hmPanelAC  = null;
  let _hmCurrentContent = '';
  let _hmCurrentPath = '';
  let _hmSaveTimer   = null;
  let _hmPendingSave = null;

  /** Führt einen noch anstehenden HM-Autosave sofort aus (mit den alten
   *  DOM-Werten), bevor das Panel für eine andere Datei neu befüllt wird.
   *  Verhindert, dass ein verspäteter Timer die Werte der neuen Datei in
   *  die alte Datei schreibt (Vorfall 2026-07-12, guenther.nix). */
  function _hmFlushPendingSave() {
    if (!_hmSaveTimer) return;
    clearTimeout(_hmSaveTimer);
    _hmSaveTimer = null;
    const fn = _hmPendingSave;
    _hmPendingSave = null;
    fn?.();
  }

  // ── HM-Panel Parser ──────────────────────────────────────────────

  /** Liest einen String-Wert aus Nix-Inhalt (z.B. home.username = "martin").
   *  Gibt null zurück wenn der Schlüssel nicht vorhanden ist. */
  function _nixGetStr(content, key) {
    const ek = key.replace(/[.+*?^${}()|[\]\\-]/g, '\\$&');
    const m  = new RegExp(`${ek}\\s*=\\s*"([^"]*)"`, 'm').exec(content);
    return m ? m[1] : null;
  }

  /** Liest einen Bool-Wert aus Nix-Inhalt (z.B. programs.home-manager.enable = true).
   *  Gibt null zurück wenn der Schlüssel nicht vorhanden ist. */
  function _nixGetBool(content, key) {
    const ek = key.replace(/[.+*?^${}()|[\]\\-]/g, '\\$&');
    const m  = new RegExp(`${ek}\\s*=\\s*(true|false)`, 'm').exec(content);
    return m ? m[1] === 'true' : null;
  }

  /** Liest einen Integer-Wert aus Nix-Inhalt (z.B. configurationLimit = 5).
   *  Gibt null zurück wenn der Schlüssel nicht vorhanden ist. */
  function _nixGetInt(content, key) {
    const ek = key.replace(/[.+*?^${}()|[\]\\-]/g, '\\$&');
    const m  = new RegExp(`${ek}\\s*=\\s*(\\d+)`, 'm').exec(content);
    return m ? parseInt(m[1], 10) : null;
  }

  /** Wie _nixGetBool, prüft aber auch Block-Syntax: parentKey = { childKey = true; }.
   *  Nützlich für z.B. programs.steam = { enable = true; }. */
  function _nixGetBoolBlock(content, parentKey, childKey) {
    const direct = _nixGetBool(content, `${parentKey}.${childKey}`);
    if (direct !== null) return direct;
    const epk   = parentKey.replace(/[.+*?^${}()|[\]\\-]/g, '\\$&');
    const eck   = childKey.replace(/[.+*?^${}()|[\]\\-]/g, '\\$&');
    const block = new RegExp(`${epk}\\s*=\\s*\\{([^}]*)\\}`, 'm').exec(content);
    if (!block) return null;
    const inner = new RegExp(`\\b${eck}\\s*=\\s*(true|false)`, 'm').exec(block[1]);
    return inner ? inner[1] === 'true' : null;
  }

  /** Ersetzt einen Wert in Nix-Inhalt; unverändert wenn Schlüssel nicht gefunden */
  function _nixSetValue(content, key, nixValue) {
    const ek = key.replace(/[.+*?^${}()|[\]\\-]/g, '\\$&');
    const re = new RegExp(`(${ek}\\s*=\\s*)(?:"[^"]*"|true|false|\\d+)`, 'm');
    return re.test(content) ? content.replace(re, `$1${nixValue}`) : content;
  }

  /** Parst alle Argumente aus dem Nix-Funktionskopf (ohne '...'). */
  function _nixGetHmArgs(content) {
    const m = /^\s*\{([^}]*)\}\s*:/m.exec(content);
    if (!m) return [];
    return m[1].split(',')
      .map(s => s.trim()).filter(s => s && s !== '...');
  }

  /** Schreibt Argumente zurück in den Nix-Funktionskopf ('...' wird immer angehängt). */
  function _nixSetHmArgs(content, args) {
    const allArgs = [...args, '...'];
    return content.replace(/^\s*\{[^}]*\}\s*:/m, `{ ${allArgs.join(', ')} }:`);
  }

  function _hmGetShell(content) {
    for (const sh of ['bash', 'zsh', 'fish']) {
      if (new RegExp(`programs\\.${sh}\\s*=\\s*\\{`).test(content)) return sh;
    }
    return null;
  }

  function _hmGetInitExtra(content) {
    const m = /(?:initExtra|shellInit)\s*=\s*''([\s\S]*?)''/m.exec(content);
    if (!m) return '';
    const raw = m[1].replace(/^\n/, '').trimEnd();
    if (!raw) return '';
    const lines = raw.split('\n');
    const indent = Math.min(...lines.filter(l => l.trim()).map(l => l.match(/^(\s*)/)[1].length));
    return lines.map(l => l.slice(indent)).join('\n').trimEnd();
  }

  function _hmGetPackages(content) {
    const m = /home\.packages\s*=(?:\s*with\s+pkgs\s*;)?\s*\[([\s\S]*?)\]/m.exec(content);
    if (!m) return [];
    const inner = m[1];
    const fmt1 = [...inner.matchAll(/pkgs\.([\w_-]+)/g)].map(x => x[1]);
    if (fmt1.length > 0) return fmt1;
    return inner.trim().split(/\s+/).filter(s => s && !s.startsWith('#') && /^[\w_-]+$/.test(s));
  }

  /** Baut das HM-Benutzer-Formular und befüllt es mit geparsten Werten */
  function _populateHmPanel(content, filePath) {
    const container = document.getElementById('panel-hm-content');
    if (!container) return;

    // Anstehenden Autosave der vorherigen Datei ausführen, solange das
    // alte Formular noch im DOM steht
    _hmFlushPendingSave();

    const customArgs   = _nixGetHmArgs(content);
    const username     = _nixGetStr(content,  'home.username');
    const homeDir      = _nixGetStr(content,  'home.homeDirectory');
    const stateVersion = _nixGetStr(content,  'home.stateVersion');
    const hmEnable     = _nixGetBool(content, 'programs.home-manager.enable');
    const shell        = _hmGetShell(content);
    const initExtra    = shell ? _hmGetInitExtra(content) : '';
    const packages     = _hmGetPackages(content);
    _hmCurrentContent  = content;
    _hmCurrentPath     = filePath;

    const argsRowsHtml = customArgs.map(a => `
      <div class="hm-arg-row">
        <input type="text" class="hm-arg-name mono-input" value="${escHtml(a)}" placeholder="Argument (z.B. lib, osConfig)" spellcheck="false">
        <button type="button" class="fh-item-remove" title="Entfernen">${niIcon('x')}</button>
      </div>`).join('');

    container.innerHTML = `
      <div class="section-controls">
        <button type="button" id="hm-collapse-all-btn"
                data-i18n="sections.collapseAll"
                data-i18n-title="sections.collapseAllTitle"
                title="Alle Sektionen einklappen">${niIcon('chevron-up')} <span data-i18n="sections.collapseAll">Einklappen</span></button>
        <button type="button" id="hm-expand-all-btn"
                data-i18n-title="sections.expandAllTitle"
                title="Alle Sektionen aufklappen">${niIcon('chevron-down')} <span data-i18n="sections.expandAll">Aufklappen</span></button>
      </div>
      <section class="collapsible hm-section" data-section="Start">
        <h3 class="sec-toggle"><span data-i18n="hm.argsSection">Argumente</span></h3>
        <div class="sec-body">
          <div id="hm-args-list">${argsRowsHtml}</div>
          <button type="button" id="hm-add-arg-btn" class="fh-add-item-btn">+ ${escHtml(t('hm.addArg') || 'Argument hinzufügen')}</button>
        </div>
      </section>
      <section class="collapsible hm-section" data-section="Home Manager">
        <h3 class="sec-toggle"><span data-i18n="hm.userSection">Benutzer</span></h3>
        <div class="sec-body">
          <div class="hm-field">
            <label data-i18n="hm.username">Benutzername</label>
            <input id="hm-username" type="text" value="${escHtml(username)}" autocomplete="off" spellcheck="false" />
          </div>
          <div class="hm-field">
            <label data-i18n="hm.homeDir">Home-Verzeichnis</label>
            <input id="hm-homeDir" type="text" value="${escHtml(homeDir)}" autocomplete="off" spellcheck="false" />
          </div>
          <div class="hm-field">
            <label data-i18n="hm.stateVersion">State-Version</label>
            <input id="hm-stateVersion" type="text" value="${escHtml(stateVersion)}" autocomplete="off" spellcheck="false" />
          </div>
          <div class="hm-field hm-field-check">
            <input id="hm-hmEnable" type="checkbox" ${hmEnable ? 'checked' : ''} />
            <label for="hm-hmEnable" data-i18n="hm.hmEnable">programs.home-manager.enable</label>
          </div>
        </div>
      </section>
      ${shell ? `
      <section class="collapsible hm-section" data-section="Shell">
        <h3 class="sec-toggle"><span data-i18n="hm.shell">Shell</span> <span class="hint">(${escHtml(shell)})</span></h3>
        <div class="sec-body">
          <label for="hm-initExtra" data-i18n="hm.shellInitExtra">Shell-Init (extra)</label>
          <p class="hint" data-i18n="hm.shellInitExtraHint" style="margin-bottom:4px">Wird ans Ende der Shell-Init-Datei angehängt.</p>
          <textarea id="hm-initExtra" rows="4" spellcheck="false" class="mono-input"
                    placeholder='export PATH="$HOME/.local/bin:$PATH"'>${escHtml(initExtra)}</textarea>
        </div>
      </section>` : ''}
      <section class="collapsible hm-section" data-section="Pakete">
        <h3 class="sec-toggle"><span data-i18n="hm.packages">Pakete</span></h3>
        <div class="sec-body">
          <p class="hint" data-i18n="hm.packagesHint" style="margin-bottom:4px">(ein Nix-Attr pro Zeile)</p>
          <textarea id="hm-packages-ta" rows="4" spellcheck="false"
                    placeholder="ripgrep&#10;bat&#10;fastfetch">${escHtml(packages.join('\n'))}</textarea>
        </div>
      </section>
    `;

    HM_PREVIEW_SECTIONS.forEach(s => collapsedSections.add(s));
    applySectionCollapse();

    const argsList  = container.querySelector('#hm-args-list');
    const addArgBtn = container.querySelector('#hm-add-arg-btn');
    const collapseAllBtn = container.querySelector('#hm-collapse-all-btn');
    const expandAllBtn = container.querySelector('#hm-expand-all-btn');

    container.querySelectorAll('.sec-toggle').forEach(h3 => {
      h3.addEventListener('click', () => {
        const section = h3.closest('section');
        if (section?.dataset.section) toggleSection(section.dataset.section);
      });
    });

    collapseAllBtn?.addEventListener('click', () => {
      HM_PREVIEW_SECTIONS.forEach(s => collapsedSections.add(s));
      applySectionCollapse();
    });
    expandAllBtn?.addEventListener('click', () => {
      HM_PREVIEW_SECTIONS.forEach(s => collapsedSections.delete(s));
      applySectionCollapse();
      collapsedBrix.clear();
      localStorage.setItem('nico-collapsed-brix', '[]');
      document.querySelectorAll('.code-brix.collapsed').forEach(el => el.classList.remove('collapsed'));
    });

    // Remove-Buttons via Event-Delegation
    argsList.addEventListener('click', e => {
      if (e.target.classList.contains('fh-item-remove'))
        e.target.closest('.hm-arg-row').remove();
    });

    // Neue Zeile hinzufügen
    addArgBtn.addEventListener('click', () => {
      const row = document.createElement('div');
      row.className = 'hm-arg-row';
      row.innerHTML = `
        <input type="text" class="hm-arg-name mono-input" value="" placeholder="Argument (z.B. lib, osConfig)" spellcheck="false">
        <button type="button" class="fh-item-remove" title="Entfernen">${niIcon('x')}</button>
      `;
      argsList.appendChild(row);
      row.querySelector('.hm-arg-name').focus();
    });

    function _buildHmPatchPayload(path) {
      const args = [...argsList.querySelectorAll('.hm-arg-row')]
        .map(row => row.querySelector('.hm-arg-name')?.value.trim() ?? '')
        .filter(Boolean);
      const pkgRaw = document.getElementById('hm-packages-ta')?.value ?? '';
      const payload = {
        path,
        args,
        username:      document.getElementById('hm-username')?.value      ?? '',
        home_dir:      document.getElementById('hm-homeDir')?.value       ?? '',
        state_version: document.getElementById('hm-stateVersion')?.value  ?? '',
        hm_enable:     document.getElementById('hm-hmEnable')?.checked    ?? false,
        packages:      pkgRaw.trim().split(/\s+/).filter(Boolean),
      };
      const initExtraEl = document.getElementById('hm-initExtra');
      if (initExtraEl) payload.shell_init_extra = initExtraEl.value;
      return payload;
    }

    async function _doHmSave() {
      _hmSaveTimer = null;
      const payload = _buildHmPatchPayload(filePath);
      try {
        const res  = await csrfFetch('/api/hm/patch', {
          method:  'POST',
          headers: { 'Content-Type': 'application/json' },
          body:    JSON.stringify(payload),
        });
        const data = await res.json();
        if (data.success) {
          // Preview nur aktualisieren, wenn das Panel noch dieselbe Datei zeigt
          if (filePath === _hmCurrentPath) {
            _hmCurrentContent = data.content;
            renderCodePreview(data.content, 'preview-hm', filePath);
          }
          showToast(t('hm.saved'), 'success');
          return true;
        } else {
          showToast(tErr(data.error) || t('toast.error'), 'error');
          return false;
        }
      } catch (e) {
        showToast(t('toast.error'), 'error');
        return false;
      }
    }

    if (_hmPanelAC) _hmPanelAC.abort();
    _hmPanelAC = new AbortController();
    const _hmSig = _hmPanelAC.signal;
    container.addEventListener('input',  () => { clearTimeout(_hmSaveTimer); _hmPendingSave = _doHmSave; _hmSaveTimer = setTimeout(_doHmSave, 800); }, { signal: _hmSig });
    container.addEventListener('change', () => { clearTimeout(_hmSaveTimer); _hmPendingSave = _doHmSave; _hmSaveTimer = setTimeout(_doHmSave, 800); }, { signal: _hmSig });
  }

  // ── Flake-Panel ──────────────────────────────────────────────────

  let _flakePreviewDebounce = null;
  const FALLBACK_NIXOS_CHANNELS = [
    'nixos-unstable',
    'nixos-26.05',
    'nixos-25.11',
    'nixos-25.05',
    'nixos-24.11',
  ];

  function _scheduleFlakePreviewUpdate() {
    clearTimeout(_flakePreviewDebounce);
    _flakePreviewDebounce = setTimeout(_updateFlakePreview, 450);
  }

  async function _updateFlakePreview() {
    const formData = _getFlakeFormData();
    try {
      const res  = await csrfFetch('/api/preview/flake', {
        method:  'POST',
        headers: { 'Content-Type': 'application/json' },
        body:    JSON.stringify(formData),
      });
      const data = await res.json();
      if (data.flake_nix) renderCodePreview(data.flake_nix, 'preview-flake', 'flake.nix');
    } catch (e) { /* ignore */ }
  }

  function _getFlakeFormData() {
    const v = id => document.getElementById(id);

    const flake_hosts = [];
    document.querySelectorAll('#flake-hosts-list .flake-host-card').forEach(card => {
      const name = (card.dataset.host || '').trim();
      if (!name) return;
      flake_hosts.push({ name });
    });

    return {
      flake_description:     v('flake_description')?.value    ?? '',
      flake_arch:            v('flake_arch')?.value           || 'x86_64-linux',
      flake_nixpkgs_channel: v('flake_nixpkgs_channel')?.value ?? 'nixos-unstable',
      flake_hm_input:        v('flake_hm_input')?.checked     ?? false,
      flake_hm_follows:      v('flake_hm_follows')?.checked   ?? true,
      flake_hm_module:       v('flake_hm_module')?.checked    ?? true,
      flake_nixos_hardware:  v('flake_nixos_hardware')?.checked ?? false,
      flake_plasma_manager:  v('flake_plasma_manager')?.checked ?? false,
      flake_hosts,
    };
  }

  // Exotic architectures (riscv64, …) from imported flakes get an extra
  // option so the select can represent them instead of silently falling
  // back to x86_64-linux on save.
  function _ensureArchOption(val) {
    const el = document.getElementById('flake_arch');
    if (!el || !val) return;
    if (![...el.options].some(o => o.value === val)) {
      const opt = document.createElement('option');
      opt.value = val;
      opt.textContent = val;
      el.appendChild(opt);
    }
  }

  function _setNixpkgsChannelOptions(channels, selected) {
    const el = document.getElementById('flake_nixpkgs_channel');
    if (!el) return;
    const current = selected ?? el.value ?? 'nixos-unstable';
    const list = Array.isArray(channels) && channels.length ? [...channels] : [...FALLBACK_NIXOS_CHANNELS];
    if (current && !list.includes(current)) list.unshift(current);
    el.innerHTML = '';
    list.forEach(channel => {
      const opt = document.createElement('option');
      opt.value = channel;
      opt.textContent = channel;
      el.appendChild(opt);
    });
    el.value = current;
  }

  async function _loadNixpkgsChannelOptions(selected) {
    try {
      const res = await csrfFetch('/api/nixos/channels');
      const data = await res.json();
      _setNixpkgsChannelOptions(data.channels, selected);
    } catch (e) {
      _setNixpkgsChannelOptions(FALLBACK_NIXOS_CHANNELS, selected);
    }
  }

  /**
   * Flake-Formular aus dem Dateiinhalt befüllen (nicht aus nico.json).
   * Wird aufgerufen wenn der User flake.nix öffnet – Datei ist die Wahrheit.
   */
  async function _populateFlakeFormFromFile(content) {
    try {
      renderCodePreview(content, 'preview-flake', 'flake.nix');

      // Datei parsen
      const res    = await csrfFetch('/api/parse/flake', {
        method:  'POST',
        headers: { 'Content-Type': 'application/json' },
        body:    JSON.stringify({ content }),
      });
      const parsed = await res.json();
      if (parsed.error) {
        console.warn('parse/flake error:', parsed.error);
        await _populateFlakeForm();   // fallback auf nico.json
        return;
      }

      // Felder mit Defaults, dann geparste Werte drüber
      const d = {
        flake_description:     '',
        flake_arch:            'x86_64-linux',
        flake_nixpkgs_channel: 'nixos-unstable',
        flake_hm_input:        false,
        flake_hm_follows:      true,
        flake_hm_module:       true,
        flake_nixos_hardware:  false,
        flake_plasma_manager:  false,
        ...parsed,
      };

      const set = (id, val) => { const el = document.getElementById(id); if (el) el.value = val ?? ''; };
      const chk = (id, val) => { const el = document.getElementById(id); if (el) el.checked = !!val; };
      const sel = (id, val) => { const el = document.getElementById(id); if (el) el.value = val ?? el.value; };

      await _loadNixpkgsChannelOptions(d.flake_nixpkgs_channel);
      set('flake_description',    d.flake_description);
      _ensureArchOption(d.flake_arch);
      sel('flake_arch',            d.flake_arch);
      sel('flake_nixpkgs_channel', d.flake_nixpkgs_channel);
      chk('flake_hm_input',       d.flake_hm_input);
      chk('flake_hm_follows',     d.flake_hm_follows);
      chk('flake_hm_module',      d.flake_hm_module);
      chk('flake_nixos_hardware', d.flake_nixos_hardware);
      chk('flake_plasma_manager', d.flake_plasma_manager);

      _updateFlakeHmVisibility(!!d.flake_hm_input);
      await _renderFlakeHosts();

      _brixTargetFile  = 'flake.nix';
      _brixContextFile = 'flake.nix';

      ['__header__', 'Start', 'Inputs', 'Inputs-Extra', 'Outputs', 'Outputs-Hosts', 'Outputs-Extra', 'Hosts', 'End'].forEach(s => collapsedSections.add(s));
      applySectionCollapse();

      _flakeFormDirty = false;
      _initFlakeFormListeners();
    } catch (e) {
      console.error('_populateFlakeFormFromFile:', e);
      await _populateFlakeForm();   // fallback
    }
  }

  async function _populateFlakeForm() {
    try {
      const res  = await csrfFetch('/api/config');
      const data = await res.json();
      const set  = (id, val) => { const el = document.getElementById(id); if (el) el.value = val ?? el.value; };
      const chk  = (id, val) => { const el = document.getElementById(id); if (el) el.checked = !!val; };
      const sel  = (id, val) => { const el = document.getElementById(id); if (el && val !== undefined) el.value = val; };

      await _loadNixpkgsChannelOptions(data.flake_nixpkgs_channel ?? 'nixos-unstable');
      set('flake_description',    data.flake_description ?? '');
      _ensureArchOption(data.flake_arch);
      sel('flake_arch',            data.flake_arch ?? 'x86_64-linux');
      sel('flake_nixpkgs_channel', data.flake_nixpkgs_channel ?? 'nixos-unstable');
      chk('flake_hm_input',       data.flake_hm_input);
      chk('flake_hm_follows',     data.flake_hm_follows !== false);
      chk('flake_hm_module',      data.flake_hm_module  !== false);
      chk('flake_nixos_hardware', data.flake_nixos_hardware);
      chk('flake_plasma_manager', data.flake_plasma_manager);

      // Sub-options visibility
      _updateFlakeHmVisibility(!!data.flake_hm_input);
      await _renderFlakeHosts();

      // Set brix target to flake.nix for the duration of this panel
      _brixTargetFile = 'flake.nix';
      _brixContextFile = 'flake.nix';

      // Collapse all sections by default
      ['__header__', 'Start', 'Inputs', 'Inputs-Extra', 'Outputs', 'Outputs-Hosts', 'Outputs-Extra', 'Hosts', 'End'].forEach(s => collapsedSections.add(s));
      applySectionCollapse();

      // Render preview
      await _updateFlakePreview();

      // Wire up change handlers (once per form load – idempotent via flag)
      _flakeFormDirty = false;
      _initFlakeFormListeners();
    } catch (e) {
      console.error('_populateFlakeForm:', e);
    }
  }

  function _updateFlakeHmVisibility(hmActive) {
    document.getElementById('flake-hm-follows-opt')?.classList.toggle('hidden', !hmActive);
    document.getElementById('flake-hm-module-opt')?.classList.toggle('hidden', !hmActive);
  }

  function _flakeHostCard(host) {
    const name = typeof host === 'string' ? host : (host.name || '');
    const h    = escHtml(name);

    return `<div class="flake-host-card" data-host="${h}">
      <div class="flake-host-header">
        <span class="flake-host-label">${h}</span>
        <span class="fh-header-actions">
          <button type="button" class="fh-remove-btn" data-host="${h}"
                  title="${escHtml(t('fl.hosts.deleteTitle'))}">${niIcon('x')}</button>
        </span>
      </div>
    </div>`;
  }

  function _addHostRow() {
    return `<div class="fh-add-row">
      <button type="button" class="btn-add-user" id="fh-add-btn">
        ${escHtml(t('fl.hosts.addTitle') || '+ Host hinzufügen')}
      </button>
      <div class="fh-add-inline hidden">
        <input type="text" id="fh-add-input" maxlength="40"
               placeholder="${escHtml(t('fl.hosts.addPlaceholder') || 'hostname')}"
               spellcheck="false" autocomplete="off">
        <button type="button" id="fh-add-confirm" class="fh-add-confirm">✓</button>
        <button type="button" id="fh-add-cancel"  class="fh-add-cancel">✕</button>
      </div>
    </div>`;
  }

  function _bindAddHost(container) {
    const addBtn    = container.querySelector('#fh-add-btn');
    const addInline = container.querySelector('.fh-add-inline');
    const input     = container.querySelector('#fh-add-input');
    const confirmBtn = container.querySelector('#fh-add-confirm');
    const cancelBtn  = container.querySelector('#fh-add-cancel');
    if (!addBtn) return;

    function open()  { addBtn.classList.add('hidden'); addInline.classList.remove('hidden'); input.focus(); }
    function close() { input.value = ''; addBtn.classList.remove('hidden'); addInline.classList.add('hidden'); }

    async function doAdd() {
      const name = input.value.trim();
      if (!name) return;
      if (!/^[a-zA-Z][a-zA-Z0-9_-]*$/.test(name)) {
        showToast(t('fl.hosts.errInvalidName'), 'error'); return;
      }
      const r = await csrfFetch('/api/flake/host/add', {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ name }),
      });
      const j = await r.json();
      if (j.error === 'ERR_HOST_EXISTS') { showToast(t('fl.hosts.errExists'), 'error'); return; }
      if (j.error) { showToast(j.error, 'error'); return; }
      await _renderFlakeHosts();
      _scheduleFlakePreviewUpdate();
    }

    addBtn.addEventListener('click', open);
    confirmBtn.addEventListener('click', doAdd);
    cancelBtn.addEventListener('click', close);
    input.addEventListener('keydown', e => {
      if (e.key === 'Enter')  doAdd();
      if (e.key === 'Escape') close();
    });
  }

  async function _renderFlakeHosts() {
    const container = document.getElementById('flake-hosts-list');
    if (!container) return;
    try {
      const res  = await csrfFetch('/api/flake/hosts');
      const data = await res.json();

      if (!data.flake_mode || !data.hosts?.length) {
        container.innerHTML =
          `<p class="flake-hosts-empty">${escHtml(t('fl.hosts.none'))}</p>` +
          _addHostRow();
        _bindAddHost(container);
        return;
      }

      container.innerHTML = data.hosts.map(_flakeHostCard).join('') + _addHostRow();

      // Delete buttons
      container.querySelectorAll('.fh-remove-btn').forEach(btn => {
        btn.addEventListener('click', async () => {
          const name = btn.dataset.host;
          if (!confirm(t('fl.hosts.deleteConfirm').replace('{name}', name))) return;
          const r = await csrfFetch('/api/flake/host/delete', {
            method: 'POST', headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ name }),
          });
          const j = await r.json();
          if (j.error) { showToast(j.error, 'error'); return; }
          await _renderFlakeHosts();
          _scheduleFlakePreviewUpdate();
        });
      });

      _bindAddHost(container);

    } catch {
      container.innerHTML = '';
    }
  }

  let _flakeListenersInitialized = false;

  function _initFlakeFormListeners() {
    if (_flakeListenersInitialized) return;
    _flakeListenersInitialized = true;

    const form = document.getElementById('flake-form');
    if (!form) return;

    // Dirty-Flag setzen + Preview aktualisieren bei Änderung (kein Auto-Save)
    form.addEventListener('change', () => {
      _updateFlakeHmVisibility(!!document.getElementById('flake_hm_input')?.checked);
      _flakeFormDirty = true;
      _scheduleFlakePreviewUpdate();
    });

    // Collapse/expand buttons (form sections + code preview via collapsedSections)
    const FLAKE_PREVIEW_SECTIONS = ['__header__', 'Start', 'Inputs', 'Inputs-Extra', 'Outputs', 'Outputs-Hosts', 'Outputs-Extra', 'Hosts', 'End'];
    document.getElementById('flake-collapse-all-btn')?.addEventListener('click', () => {
      FLAKE_PREVIEW_SECTIONS.forEach(s => collapsedSections.add(s));
      applySectionCollapse();
    });
    document.getElementById('flake-expand-all-btn')?.addEventListener('click', () => {
      FLAKE_PREVIEW_SECTIONS.forEach(s => collapsedSections.delete(s));
      applySectionCollapse();
    });

    // NOTE: sec-toggle click is handled globally by the main init (toggleSection).
    // No extra listener needed here – adding one would double-fire and cancel the toggle.
  }

  /** Flake-Formular auf den Server speichern (wie _autoSave für CO). Nur wenn dirty. */
  async function _flakeSave() {
    if (!_flakeFormDirty) return true;
    try {
      const res  = await csrfFetch('/api/config/flake', {
        method:  'POST',
        headers: { 'Content-Type': 'application/json' },
        body:    JSON.stringify(_getFlakeFormData()),
      });
      const data = await res.json().catch(() => null);
      if (data?.success) { _flakeFormDirty = false; return true; }
      showToast(tErr(data?.error) || t('toast.error'), 'error');
      return false;
    } catch (e) {
      showToast(t('toast.error'), 'error');
      return false;
    }
  }

  // ── Datei neu laden ──────────────────────────────────────────────

  async function _reloadFile() {
    if (!activeFile) return;
    const ftype = document.querySelector(`.tree-file[data-path="${CSS.escape(activeFile.path)}"]`)?.dataset.fileType;
    if (ftype === 'co') {
      await loadConfig();
      _formDirty = false;
    } else {
      await _loadFileIntoView(activeFile.path, { skipTypeDialog: true });
    }
    showToast(t('tools.reloaded'), 'success');
  }

  function _showRawView(content, fileName, filePath) {
    _clearRawView();
    _rawEditPath = filePath;
    const previewTabs = elPreviewPanel.querySelector('.preview-tabs');
    if (previewTabs) { previewTabs.dataset.hiddenByRaw = '1'; previewTabs.style.display = 'none'; }
    elPreviewPanel.querySelectorAll('.preview-code-wrap, .preview-content').forEach(el => {
      el.dataset.hiddenByRaw = '1'; el.style.display = 'none';
    });
    const rawDiv = document.createElement('div');
    rawDiv.id = 'raw-file-view';
    rawDiv.className = 'raw-file-view';

    const { header, body } = _splitNicoHeader(content);
    _rawEditHeader = header;
    _rawEditOrig   = body;

    const lineCount = body.split('\n').length;
    const lineNums  = Array.from({length: lineCount}, (_, i) => i + 1).join('\n');

    const headerHtml = header
      ? `<div class="raw-header-block">
           <pre class="raw-header-pre language-nix">${escHtml(header)}</pre>
         </div>
         <div class="raw-header-sep"></div>`
      : '';

    rawDiv.innerHTML = `
      ${headerHtml}
      <div class="raw-editor-wrap">
        <div class="raw-line-nums" aria-hidden="true">${lineNums}</div>
        <textarea id="raw-file-editor" class="raw-file-editor" spellcheck="false">${escHtml(body)}</textarea>
      </div>
    `;
    elPreviewPanel.appendChild(rawDiv);
    const _ta  = rawDiv.querySelector('#raw-file-editor');
    const _lns = rawDiv.querySelector('.raw-line-nums');
    _ta.addEventListener('scroll', () => { _lns.scrollTop = _ta.scrollTop; });
    _ta.addEventListener('input',  () => {
      const c = _ta.value.split('\n').length;
      _lns.textContent = Array.from({length: c}, (_, i) => i + 1).join('\n');
    });
    // Show raw-save button, switch mode indicator to pencil
    const rawSaveTabBtn = document.getElementById('raw-save-tab-btn');
    if (rawSaveTabBtn) { rawSaveTabBtn.classList.remove('hidden'); rawSaveTabBtn.onclick = _saveRawFile; }
    const modBtn = document.getElementById('preview-mode-btn');
    if (modBtn) { modBtn.innerHTML = niIcon('pencil'); modBtn.dataset.mode = 'edit'; modBtn.title = t('preview.modeRaw'); }
  }

  function _showFlkView(content) {
    _clearRawView();
    const previewTabs = elPreviewPanel.querySelector('.preview-tabs');
    if (previewTabs) { previewTabs.dataset.hiddenByRaw = '1'; previewTabs.style.display = 'none'; }
    elPreviewPanel.querySelectorAll('.preview-code-wrap, .preview-content').forEach(el => {
      el.dataset.hiddenByRaw = '1'; el.style.display = 'none';
    });

    const lines     = content.split('\n');
    const lineNums  = lines.map((_, i) => i + 1).join('\n');
    const highlighted = (typeof Prism !== 'undefined' && Prism.languages.nix)
      ? Prism.highlight(content, Prism.languages.nix, 'nix')
      : escHtml(content);

    const flkDiv = document.createElement('div');
    flkDiv.id = 'flk-file-view';
    flkDiv.className = 'raw-file-view';
    flkDiv.innerHTML = `
      <div class="raw-editor-wrap">
        <div class="raw-line-nums" aria-hidden="true">${lineNums}</div>
        <pre class="flk-file-pre language-nix"><code>${highlighted}</code></pre>
      </div>`;
    elPreviewPanel.appendChild(flkDiv);

    const lns = flkDiv.querySelector('.raw-line-nums');
    const pre = flkDiv.querySelector('.flk-file-pre');
    pre.addEventListener('scroll', () => { lns.scrollTop = pre.scrollTop; });
  }

  async function _saveRawFile() {
    const path = _rawEditPath;
    const body = document.getElementById('raw-file-editor')?.value ?? '';
    if (!path) return;
    const content = _rawEditHeader ? _rawEditHeader + '\n' + body : body;

    const fileName = path.split('/').pop();
    // hardware-configuration.nix: Bestätigungsdialog
    if (fileName === 'hardware-configuration.nix') {
      if (!confirm(t('sidebar.hwSaveConfirm'))) return;
    }

    try {
      const res  = await csrfFetch('/api/file', {
        method:  'POST',
        headers: { 'Content-Type': 'application/json' },
        body:    JSON.stringify({ files: [{ path, content }] }),
      });
      const data = await res.json();
      if (data.success) {
        _rawEditOrig = body;
        showToast(t('sidebar.rawSaveOk'), 'success');
        return true;
      } else {
        showToast(tErr(data.error) || t('toast.error'), 'error');
        return false;
      }
    } catch (e) {
      showToast(t('toast.error'), 'error');
      return false;
    }
  }

  async function _togglePlainCodeViewInSidebar() {
    plainCodeView = !plainCodeView;
    applyPlainCodeViewBtn();
    csrfFetch('/api/app/settings', {
      method:  'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify({ code_view_plain: plainCodeView }),
    }).catch(() => {});
    // Reiner Ansichts-Umschalter: nichts speichern, nichts vom Server laden.
    // Re-Render aus dem zuletzt gerenderten Code (dataset.sourceCode ist durch
    // renderCodePreview immer aktuell). Der Raw-Editor ist davon unberührt.
    if (_rawEditPath) return;
    document.querySelectorAll('[data-source-code]').forEach(el => {
      if (el.id) renderCodePreview(el.dataset.sourceCode, el.id, el.dataset.sourceFile);
    });
  }

  function _clearRawView() {
    document.getElementById('raw-file-view')?.remove();
    document.getElementById('flk-file-view')?.remove();
    _rawEditPath = null;
    document.querySelectorAll('[data-hidden-by-raw]').forEach(el => {
      el.style.display = ''; delete el.dataset.hiddenByRaw;
    });
    const rawSaveTabBtn = document.getElementById('raw-save-tab-btn');
    if (rawSaveTabBtn) { rawSaveTabBtn.classList.add('hidden'); rawSaveTabBtn.onclick = null; }
    const modBtn = document.getElementById('preview-mode-btn');
    if (modBtn) delete modBtn.dataset.mode;
    applyPlainCodeViewBtn();
  }

  async function openFileViewer(relPath, forceReadonly = false) {
    const encoded = relPath.split('/').map(encodeURIComponent).join('/');
    const res  = await csrfFetch(`/api/file/${encoded}`);
    const info = await res.json();
    if (info.error) { showToast(tErr(info.error), 'error'); return; }

    const readonly  = forceReadonly || !info.writable;
    const isHwConfig = relPath.includes('hardware-configuration');

    document.getElementById('file-viewer-title').textContent = relPath.split('/').pop();
    const textarea = document.getElementById('file-viewer-content');
    textarea.value    = info.content;
    textarea.readOnly = readonly;

    const warning = document.getElementById('file-viewer-warning');
    if (isHwConfig && !readonly) {
      warning.textContent = t('file.hwWarning');
      warning.classList.remove('hidden');
    } else {
      warning.classList.add('hidden');
    }

    document.getElementById('file-viewer-actions').classList.toggle('hidden', readonly);
    document.getElementById('file-viewer-overlay').classList.remove('hidden');

    document.getElementById('file-viewer-close').onclick = () =>
      document.getElementById('file-viewer-overlay').classList.add('hidden');

    document.getElementById('file-viewer-save').onclick = async () => {
      const r = await csrfFetch(`/api/file/${encoded}`, {
        method:  'POST',
        headers: { 'Content-Type': 'application/json' },
        body:    JSON.stringify({ content: textarea.value }),
      });
      const d = await r.json();
      if (d.error) { showToast(tErr(d.error), 'error'); return; }
      document.getElementById('file-viewer-overlay').classList.add('hidden');
      showToast(t('file.saved'));
    };
  }

  function setActiveFile(path, name) {
    activeFile = { path, name };
    _updateActiveFileLabel();
    updateTreeHighlights();
  }

  function clearFlkIfActive() {
    if (activeFile?.name !== 'flake.lock') return;
    activeFile = null;
    _updateActiveFileLabel();
    _clearRawView();
    _showLeftPanel('none');
  }

  async function _refreshPanelToggle(path, ftype) {
    try {
      const r = await csrfFetch(`/api/file?path=${encodeURIComponent(path)}`);
      const d = await r.json();
      if (!d.error) _updatePanelToggle(ftype, d.content || '');
    } catch {}
  }

  return {
    init,
    openFileViewer,
    flakeSave: _flakeSave,
    setActiveFile,
    closeTreeContextMenu: _closeTreeContextMenu,
    updateFlakePreview: _updateFlakePreview,
    togglePlainCodeView: _togglePlainCodeViewInSidebar,
    loadTree,
    clearFlkIfActive,
    refreshPanelToggle: _refreshPanelToggle,
  };
})();
