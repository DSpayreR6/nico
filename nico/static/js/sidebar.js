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
  let _openUnmappable = false;  // the active file is shown raw-only for a reason (warning left)

  // DOM refs (populated after DOMContentLoaded)
  let elSidebar, elToggleBtn, elTree, elPreviewPanel, elSaveAllBtn, elRefreshBtn;

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
    document.addEventListener('nico:config-clean', () => { _formDirty = false; });
    document.getElementById('save-tab-btn')
      ?.addEventListener('click', () => saveActive());
    // The unsaved marker follows every edit. Deferred, so the handlers that
    // set the dirty flags for this very event have run first.
    const _later = () => setTimeout(refreshUnsaved, 0);
    ['input', 'change'].forEach(ev => document.addEventListener(ev, _later, true));
    ['nico:config-dirty', 'nico:config-saved', 'nico:config-clean']
      .forEach(ev => document.addEventListener(ev, _later));
    // Leaving with unsaved changes asks - the browser's own dialog, its text
    // cannot be set.
    window.addEventListener('beforeunload', e => {
      if (!hasUnsaved()) return;
      e.preventDefault();
      e.returnValue = '';
    });

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
      Search.open();
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

    // Panel → raw: save the panel first. Unsaved form changes were neither in
    // the file the raw editor shows nor gone – the next background save wrote
    // them over the raw file and dropped its #r (2026-11-17).
    if (newMode === 'r') {
      let ok = true;
      // A CO file is saved even without changes (Martin, 2026-11-17): the
      // panel preview shows the file as the next save writes it – rescued
      // comments, rest bricks – and the raw editor has to show the same.
      // Saving only when dirty left raw showing the old file.
      const isCo = document.querySelector(
        `.tree-file[data-path="${CSS.escape(activeFile.path)}"]`)?.dataset.fileType === 'co';
      try {
        await _hmFlushPendingSave();
        if (_flakeFormDirty) ok = await _flakeSave();
        if (ok && (_formDirty || (isCo && _canSaveCurrentCoForm()))) {
          ok = await _autoSave();
          if (ok) _formDirty = false;
        }
      } catch { ok = false; }
      if (!ok) {
        e.target.checked = !e.target.checked;
        _panelToggleBusy = false;
        showToast(t('toast.error'), 'error');
        return;
      }
    }

    if (newMode === 'p') {
      let step = 'go';
      try { step = await _checkBeforePanel(activeFile.path); } catch { step = 'stay'; }
      if (step !== 'go') {
        if (step === 'stay') e.target.checked = !e.target.checked;
        _panelToggleBusy = false;
        return;
      }
    }

    // Raw → panel: include any unsaved raw edits in the request
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

  /** Raw → panel: would the panel write the file differently? Then ask first
   *  (Martin, 2026-11-18 – a stray `.` vanished on the switch). Resolves
   *  'go' (switch as usual), 'done' (restructured and shown) or 'stay'. */
  async function _checkBeforePanel(path) {
    if (_rawEditPath === path) {
      const ta = document.getElementById('raw-file-editor');
      // Unsaved raw edits go to disk first, so the check sees them.
      if (ta && ta.value !== _rawEditOrig && !await _saveRawFile()) return 'stay';
      // The save made it unreadable for Nix: it stays raw, the warning left
      // already says why and where (rule A, Martin 2026-11-20).
      if (_openUnmappable) return 'stay';
    }
    let check = null;
    try {
      check = await fetch(`/api/file/panel-check?path=${encodeURIComponent(path)}`).then(r => r.json());
    } catch { /* no answer: switch as before */ }
    if (!check?.changes) return 'go';
    const res = await resolveOutside(path, { switching: true, outside: !!check.outside });
    if (res !== 'restructured') return 'stay';
    await _loadFileIntoView(path, { skipTypeDialog: true });
    return 'done';
  }

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
          ${data.allowed === false
            ? `<div id="tree-hw-import-release" class="hint" style="margin:6px 0">${escHtml(t('hostRead.off.hardwareSearch'))}<br>${hostReadButton('read_host_hardware')}</div>`
            : ''}
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
      // Released in this dialog: run the search that was left out.
      overlay.addEventListener('host-read-released', async () => {
        try {
          const again = await fetch('/api/files/hardware-configs').then(r => r.json());
          for (const cfg of (again.configs || [])) {
            if (!configs.some(item => item.path === cfg.path)) configs.push(cfg);
          }
          refreshOptions();
        } catch { /* the manual path still works */ }
      });

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
    const value = await nicoPrompt(label, type === 'dir' ? '' : 'default.nix');
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
    const label = await nicoPrompt(t('sidebar.renamePrompt'), entry.name);
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
    if (!(await nicoConfirm(t('sidebar.deleteConfirm').replace('{name}', entry.name), { danger: true }))) return;

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

    // An HM change that is not on disk stops the switch, like the others.
    if (!await _hmSaveResult()) return;
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
    refreshUnsaved();
  }

  function _updateActiveFileLabel() {
    const el = document.getElementById('active-file-label');
    if (!el) return;
    el.textContent = '';
    if (activeFile) {
      // The path relative to the config directory, not only the name:
      // hosts/a/default.nix and hosts/b/default.nix looked the same. When it
      // does not fit, the front is cut (CSS), so the name stays visible.
      // The star marks unsaved changes, as in Gedit and Kate.
      const star = document.createElement('span');
      star.className = 'active-file-star';
      star.textContent = '*';
      star.hidden = !hasUnsaved();
      const pathEl = document.createElement('span');
      pathEl.className = 'active-file-path';
      const bdi = document.createElement('bdi');
      bdi.textContent = activeFile.path;
      pathEl.appendChild(bdi);
      el.append(star, pathEl);
      el.title = activeFile.path;
      el.classList.add('has-file');
    } else {
      el.removeAttribute('title');
      el.classList.remove('has-file');
    }
    refreshUnsaved();
  }

  // ── Saving: one button, one check, one routine for panel and raw ─────────

  function _rawEditor() {
    return _rawEditPath ? document.getElementById('raw-file-editor') : null;
  }

  /** Is a form panel (configuration/host, flake, Home Manager) shown? Read
   *  from the page, not from a flag: the first file after start is loaded
   *  another way than a click in the tree, and the flag only the click set
   *  left the disk hidden until the first file switch (VM test 2026-11-20). */
  function _panelShown() {
    return ['config-form', 'flake-form', 'panel-hm'].some(id => {
      const el = document.getElementById(id);
      return !!el && !el.classList.contains('hidden');
    });
  }

  /** Is anything of the open file not saved yet? Panel and raw alike. */
  function hasUnsaved() {
    if (_formDirty || _flakeFormDirty || _hmSaveTimer || _hmInFlight || _hmSaveFailed) return true;
    const ta = _rawEditor();
    return !!ta && ta.value !== _rawEditOrig;
  }

  /** Disk button, red dot and star follow hasUnsaved(). */
  function refreshUnsaved() {
    const dirty = hasUnsaved();
    const btn = document.getElementById('save-tab-btn');
    if (btn) {
      const visible = !!activeFile && (!!_rawEditor() || _panelShown());
      btn.classList.toggle('hidden', !visible);
      btn.classList.toggle('unsaved', visible && dirty);
    }
    const star = document.querySelector('#active-file-label .active-file-star');
    if (star) star.hidden = !dirty;
  }

  /** Save what is open: the raw text, or the panel's form. Disk button,
   *  Ctrl+S and leaving the file all come here. */
  async function saveActive({ toast = true } = {}) {
    if (!activeFile) return true;
    let ok = true;
    if (_rawEditor()) {
      ok = (await _saveRawFile()) === true;
    } else if (_panelShown()) {
      try {
        // The result of every save counts; a refused HM save used to be
        // reported as "saved" (Codex re-check 2026-11-20).
        ok = await _hmSaveResult();
        if (ok && _flakeFormDirty) ok = await _flakeSave();
        const isCo = document.querySelector(
          `.tree-file[data-path="${CSS.escape(activeFile.path)}"]`)?.dataset.fileType === 'co';
        if (ok && (_formDirty || (isCo && _canSaveCurrentCoForm()))) {
          ok = await _autoSave();
          if (ok) _formDirty = false;
        }
      } catch { ok = false; }
      if (ok && toast) showToast(t('toast.saved'), 'success');
    }
    refreshUnsaved();
    return ok;
  }

  // Aktiver Pfad der gerade im Raw-Editor angezeigten Datei
  let _rawEditPath   = null;
  // Header-Zeilen (nico-version + NiCo-Kommentare) der aktuellen Raw-Datei
  let _rawEditHeader = '';
  let _parseErr      = null;   // { line, col } in the text editor, from the last save/open
  let _paintTextEditor = null; // repaints the text editor's colored layer
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

  function _updatePanelToggle(ftype, content, forceMode = null) {
    const toggle = document.getElementById('panel-toggle');
    const btn    = document.getElementById('panel-toggle-btn');
    const label  = document.getElementById('panel-toggle-label');
    if (!toggle || !btn) return;
    const supportsPanels = ['co', 'fl', 'hm'].includes(ftype);
    toggle.classList.toggle('hidden', !supportsPanels);
    if (!supportsPanels) return;
    const mode    = forceMode || _effectivePanelMode(content);
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

    // A file NiCo cannot represent (let … in, mkIf, a flake with its own
    // outputs layout) has no form to show. The form would be empty, and
    // saving it would replace the file with defaults – so there is only the
    // raw editor, with the reason next to it. No panel switch either: there
    // is no panel to switch to.
    _openUnmappable = !!data.unmappable;
    if (data.unmappable) {
      _updatePanelToggle('unmappable', '');
      _showRawView(data.content || '', fileName, path);
      _showLeftPanel('panel-off', fileName, { panelOff: true, unmappable: true,
                                              reason: data.unmappable_reason });
      _setRawModeUI(true);
      return;
    }

    // Changed outside NiCo (Fall A): nothing is shown in a form – and so
    // nothing can be saved from one – before the user has answered. In raw
    // mode it is only a notice, further down.
    const _panelOn = !['co', 'fl', 'hm'].includes(ftype) ||
      _effectivePanelMode(data.content || '') !== 'r';
    if (data.outside && _panelOn) {
      if (!await resolveOutside(path)) return;
      const fresh = await csrfFetch(`/api/file?path=${encodeURIComponent(path)}`)
        .then(r => r.json()).catch(() => null);
      if (!fresh || fresh.error || fresh.outside || activeFile?.path !== path) return;
      return _renderFileIntoView(path, fresh, { skipTypeDialog });
    }

    _updatePanelToggle(ftype, data.content || '');

    // Panel-capable files respect the #p/#r flag
    const panelCapable = ['co', 'fl', 'hm'].includes(ftype);
    if (panelCapable && _effectivePanelMode(data.content || '') === 'r') {
      // Changed outside NiCo: in raw mode only a notice (Martin, 2026-11-18).
      if (data.outside) await showOutsideNotice(path);
      if (activeFile?.path !== path) return;
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

  /** The warning left of a raw-only file: why, and for a syntax error where.
   *  Nix counts the lines of the whole file, the raw editor starts below
   *  NiCo's header lines - the line is shifted to match what the user sees. */
  function _setUnmappableText(reason) {
    const txt = document.getElementById('unmappable-text');
    const det = document.getElementById('unmappable-detail');
    if (!txt || !det) return;
    if (reason?.kind === 'parse') {
      const hdr  = _rawEditHeader ? _rawEditHeader.split('\n').length : 0;
      const line = reason.line ? (reason.line > hdr ? reason.line - hdr : reason.line) : null;
      txt.textContent = line ? t('sidebar.unmappableParseLine', line) : t('sidebar.unmappableParse');
      det.textContent = reason.message || '';
      det.classList.toggle('hidden', !reason.message);
    } else {
      txt.textContent = t('sidebar.unmappableHint');
      det.textContent = '';
      det.classList.add('hidden');
    }
  }

  function _showLeftPanel(mode, fileName, { panelOff = false, flagSource = null, unmappable = false, reason = null } = {}) {
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
      offHint?.classList.toggle('hidden', !panelOff || unmappable);
      document.getElementById('unmappable-hint')?.classList.toggle('hidden', !unmappable);
      if (unmappable) _setUnmappableText(reason);
      _markParseError(unmappable ? reason : null);
      if (offSource) {
        offSource.classList.toggle('hidden', !panelOff || unmappable);
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
  // A Home Manager save the server refused: the change is still unsaved, and
  // the dot and star have to say so (Codex re-check 2026-11-20).
  let _hmSaveFailed  = false;
  let _hmSaveNow     = null;   // saves the HM panel now (set per panel)
  let _hmInFlight    = null;   // promise of a save already on its way

  /** Führt einen noch anstehenden HM-Autosave sofort aus (mit den alten
   *  DOM-Werten), bevor das Panel für eine andere Datei neu befüllt wird.
   *  Verhindert, dass ein verspäteter Timer die Werte der neuen Datei in
   *  die alte Datei schreibt (Vorfall 2026-07-12, guenther.nix). */
  /** Bring the HM panel's state to disk and say whether it worked: a pending
   *  autosave runs now, one on its way is waited for, a refused one is
   *  retried. true when nothing is left unsaved. */
  async function _hmSaveResult() {
    if (_hmSaveTimer) return (await _hmFlushPendingSave()) !== false;
    if (_hmInFlight) return (await _hmInFlight) !== false;
    if (_hmSaveFailed && _hmSaveNow) return (await _hmSaveNow()) !== false;
    return true;
  }

  function _hmFlushPendingSave() {
    if (!_hmSaveTimer) return;
    clearTimeout(_hmSaveTimer);
    _hmSaveTimer = null;
    const fn = _hmPendingSave;
    _hmPendingSave = null;
    return fn?.();
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
    _hmSaveFailed = false;
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
      <section class="collapsible hm-section" data-section="HEAD">
        <h3 class="sec-toggle"><span data-i18n="hm.argsSection">Argumente</span></h3>
        <div class="sec-body">
          <div id="hm-args-list">${argsRowsHtml}</div>
          <button type="button" id="hm-add-arg-btn" class="fh-add-item-btn">+ ${escHtml(t('hm.addArg') || 'Argument hinzufügen')}</button>
        </div>
      </section>
      <section class="collapsible hm-section" data-section="HOME">
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
            <label data-i18n="hm.stateVersion">Home-State-Version (home.stateVersion)</label>
            <input id="hm-stateVersion" type="text" value="${escHtml(stateVersion)}" autocomplete="off" spellcheck="false" />
          </div>
          <div class="hm-field hm-field-check">
            <input id="hm-hmEnable" type="checkbox" ${hmEnable ? 'checked' : ''} />
            <label for="hm-hmEnable" data-i18n="hm.hmEnable">programs.home-manager.enable</label>
          </div>
        </div>
      </section>
      ${shell ? `
      <section class="collapsible hm-section" data-section="SHEL">
        <h3 class="sec-toggle"><span data-i18n="hm.shell">Shell</span> <span class="hint">(${escHtml(shell)})</span></h3>
        <div class="sec-body">
          <label for="hm-initExtra" data-i18n="hm.shellInitExtra">Shell-Init (extra)</label>
          <p class="hint" data-i18n="hm.shellInitExtraHint" style="margin-bottom:4px">Wird ans Ende der Shell-Init-Datei angehängt.</p>
          <textarea id="hm-initExtra" rows="4" spellcheck="false" class="mono-input"
                    placeholder='export PATH="$HOME/.local/bin:$PATH"'>${escHtml(initExtra)}</textarea>
        </div>
      </section>` : ''}
      <section class="collapsible hm-section" data-section="HPKG">
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

    collapseAllBtn?.addEventListener('click', () => setAllCollapsed(true));
    expandAllBtn?.addEventListener('click',   () => setAllCollapsed(false));

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

    const _doHmSave = () => {
      _hmInFlight = _doHmSaveInner().finally(() => { _hmInFlight = null; refreshUnsaved(); });
      return _hmInFlight;
    };
    _hmSaveNow = _doHmSave;

    async function _doHmSaveInner() {
      _hmSaveTimer = null;
      refreshUnsaved();
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
          _hmSaveFailed = false;
          return true;
        } else {
          showToast(tErr(data.error) || t('toast.error'), 'error');
          _hmSaveFailed = true;
          return false;
        }
      } catch (e) {
        showToast(t('toast.error'), 'error');
        _hmSaveFailed = true;
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

  // Re-render flake.nix if it is the active file, otherwise refresh the preview
  async function _refreshFlakeView() {
    if (activeFile?.path === 'flake.nix') {
      const res  = await csrfFetch('/api/file?path=flake.nix');
      const data = await res.json();
      if (!data.error) {
        await _renderFileIntoView('flake.nix', data, { skipTypeDialog: true });
        return;
      }
    }
    await _updateFlakePreview();
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

      ['__header__', 'HEAD', 'INPU', 'INPX', 'OUTP', 'OUTH', 'OUTX', 'OUTH', 'TAIL'].forEach(s => collapsedSections.add(s));
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
      ['__header__', 'HEAD', 'INPU', 'INPX', 'OUTP', 'OUTH', 'OUTX', 'OUTH', 'TAIL'].forEach(s => collapsedSections.add(s));
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
      if (j.error) { showToast(tErr(j.error), 'error'); return; }
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
          if (!(await nicoConfirm(t('fl.hosts.deleteConfirm').replace('{name}', name), { danger: true }))) return;
          const r = await csrfFetch('/api/flake/host/delete', {
            method: 'POST', headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ name }),
          });
          const j = await r.json();
          if (j.error) { showToast(tErr(j.error) || t('toast.error'), 'error'); return; }
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

    // Collapse/expand buttons. setAllCollapsed reads the rendered sections,
    // which is what the host sections (HOST:<name>) need – a fixed list
    // cannot name them, so they used to stay open.
    document.getElementById('flake-collapse-all-btn')?.addEventListener('click',
      () => setAllCollapsed(true));
    document.getElementById('flake-expand-all-btn')?.addEventListener('click',
      () => setAllCollapsed(false));

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
      if (data?.success) { _flakeFormDirty = false; refreshUnsaved(); return true; }
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
    _parseErr    = null;
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
        <div class="text-editor">
          <pre class="text-editor-view" aria-hidden="true"><code class="language-nix"></code></pre>
          <textarea id="raw-file-editor" class="raw-file-editor" spellcheck="false" wrap="off">${escHtml(body)}</textarea>
        </div>
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
    // The header lines are colored like the rest.
    const _hdr = rawDiv.querySelector('.raw-header-pre');
    if (_hdr) highlightCode(_hdr, header);
    _attachTextEditor(_ta);
    // Switch mode indicator to pencil; the disk button follows refreshUnsaved
    refreshUnsaved();
    Search.refresh({ keep: true });
    const modBtn = document.getElementById('preview-mode-btn');
    if (modBtn) { modBtn.innerHTML = niIcon('pencil'); modBtn.dataset.mode = 'edit'; modBtn.title = t('preview.modeRaw'); }
  }

  /** Text mode: two layers on top of each other (Martin, 2026-11-20).
   *  Below, the text colored by Prism – the same highlightCode the panel
   *  preview uses, so colors and search marks look the same everywhere.
   *  On top, the textarea with transparent text: the caret and the selection
   *  are its own, what is seen is the layer below. Both use the same font,
   *  padding and no wrapping (wrap="off"), so every character sits in the
   *  same place; the view follows the textarea's scrolling, and is cut to
   *  the textarea's client area so the scrollbars do not shift it. No third-
   *  party editor on purpose. */
  function _attachTextEditor(ta) {
    const view = ta.parentElement.querySelector('.text-editor-view');
    const code = view?.querySelector('code');
    if (!view || !code) return;
    let pending = false;
    let brackets = [];
    const sync = () => { view.scrollTop = ta.scrollTop; view.scrollLeft = ta.scrollLeft; };
    const paint = () => {
      pending = false;
      // The trailing newline keeps an empty last line as tall as in the
      // textarea; positions before it are unchanged.
      highlightCode(code, ta.value + '\n');
      Search.remark(ta);
      brackets = _pairBrackets(code);
      _markBracketAtCaret(ta, brackets);
      _showParseError(ta, view, code);
      sync();
    };
    _paintTextEditor = paint;
    // Caret moves without input (arrows, click): only the pair mark follows.
    let caretPending = false;
    const caret = () => {
      if (caretPending) return;
      caretPending = true;
      requestAnimationFrame(() => { caretPending = false; _markBracketAtCaret(ta, brackets); });
    };
    ['keydown', 'mouseup', 'focus', 'blur'].forEach(ev => ta.addEventListener(ev, caret));
    const fit = () => {
      view.style.right  = `${ta.offsetWidth  - ta.clientWidth}px`;
      view.style.bottom = `${ta.offsetHeight - ta.clientHeight}px`;
      sync();
    };
    ta.addEventListener('input', () => {
      // Nix's error spot belongs to the saved text; once it is edited the
      // spot may have moved. It comes back with the next save (Martin,
      // 2026-11-21: while working on it, no errors).
      _parseErr = null;
      if (pending) return;
      pending = true;
      requestAnimationFrame(paint);
    });
    ta.addEventListener('scroll', sync);
    if (window.ResizeObserver) new ResizeObserver(fit).observe(ta);
    paint();
    fit();
  }

  /** Bracket pairs in the text editor (Martin, 2026-11-21, "like in Excel").
   *  Read from the layer Prism has just colored: only punctuation tokens
   *  count, so brackets inside strings and comments are left out, while
   *  those inside ${…} take part like code. A bracket without partner gets
   *  .bracket-bad; where a closing bracket meets an opener of another kind,
   *  the closer is the one marked - which bracket is really missing nobody
   *  can know. Returns [{ pos, el, partner }] in text order. */
  function _pairBrackets(code) {
    const OPEN = { '{': '}', '[': ']', '(': ')' };
    const list = [];
    const walker = document.createTreeWalker(code, NodeFilter.SHOW_TEXT);
    let pos = 0, n;
    while ((n = walker.nextNode())) {
      const s = n.textContent;
      if (s.length === 1 && '{}[]()'.includes(s)) {
        const el = n.parentElement?.closest('.token');
        if (el?.classList.contains('punctuation')) list.push({ ch: s, pos, el, partner: null });
      }
      pos += s.length;
    }
    const stack = [];
    for (const b of list) {
      if (OPEN[b.ch]) { stack.push(b); continue; }
      const top = stack[stack.length - 1];
      if (top && OPEN[top.ch] === b.ch) { stack.pop(); top.partner = b; b.partner = top; }
      else b.el.classList.add('bracket-bad');
    }
    stack.forEach(b => b.el.classList.add('bracket-bad'));
    return list;
  }

  /** Where Nix stopped reading (open or save of a file Nix cannot read):
   *  `reason` is unmappable_reason, its line counted over the whole file.
   *  The text editor starts below NiCo's header lines, so the line is
   *  shifted like in _setUnmappableText. null clears the mark. */
  function _markParseError(reason) {
    const hdr = _rawEditHeader ? _rawEditHeader.split('\n').length : 0;
    // Typed on while the check ran: the spot belongs to an older text.
    const ta = document.getElementById('raw-file-editor');
    const current = !ta || ta.value === _rawEditOrig;
    _parseErr = (current && reason?.kind === 'parse' && reason.line > hdr)
      ? { line: reason.line - hdr, col: reason.col || null }
      : null;
    _paintTextEditor?.();
  }

  /** The error line tinted in the text and the line numbers, the character
   *  Nix stopped at inverted. The bands sit inside the scrolled layers, so
   *  they follow the scrolling without their own code. */
  function _showParseError(ta, view, code) {
    const wrap = ta.closest('.raw-editor-wrap');
    const nums = wrap?.querySelector('.raw-line-nums');
    wrap?.querySelectorAll('.parse-error-line').forEach(el => el.remove());
    if (!_parseErr) return;
    const lh  = parseFloat(getComputedStyle(code).lineHeight) || 21.45;
    const top = `${parseFloat(getComputedStyle(view).paddingTop) + (_parseErr.line - 1) * lh}px`;
    const band = (parent, width) => {
      const el = document.createElement('div');
      el.className = 'parse-error-line';
      el.style.top = top;
      el.style.height = `${lh}px`;
      if (width) el.style.width = `${width}px`;
      parent.appendChild(el);
    };
    band(view, Math.max(view.scrollWidth, view.clientWidth));
    if (nums) band(nums);
    if (!_parseErr.col) return;
    // Offset of the character: start of the line plus the column (1-based).
    const lines = ta.value.split('\n');
    if (_parseErr.line > lines.length) return;
    let at = _parseErr.col - 1;
    for (let i = 0; i < _parseErr.line - 1; i++) at += lines[i].length + 1;
    const walker = document.createTreeWalker(code, NodeFilter.SHOW_TEXT);
    let pos = 0, n;
    while ((n = walker.nextNode())) {
      const len = n.textContent.length;
      if (at < pos + len) {
        const ch = n.textContent[at - pos];
        if (ch === '\n') return;   // end of the line: the band says enough
        const hit = n.splitText(at - pos);
        hit.splitText(1);
        const span = document.createElement('span');
        span.className = 'parse-error-char';
        hit.parentNode.replaceChild(span, hit);
        span.appendChild(hit);
        return;
      }
      pos += len;
    }
  }

  /** The caret on or right after a bracket with a partner: both are shown.
   *  The character after the caret wins over the one before. */
  function _markBracketAtCaret(ta, list) {
    list.forEach(b => b.el.classList.remove('bracket-pair'));
    if (document.activeElement !== ta || ta.selectionStart !== ta.selectionEnd) return;
    const at = ta.selectionStart;
    const b = list.find(x => x.pos === at && x.partner)
           || list.find(x => x.pos === at - 1 && x.partner);
    if (!b) return;
    b.el.classList.add('bracket-pair');
    b.partner.el.classList.add('bracket-pair');
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
    Search.refresh({ keep: true });
  }

  /** After a raw save: is the file (still) shown raw-only for a reason?
   *  Only the left side and the panel switch change - the raw editor stays
   *  as it is, cursor and scroll position included (G, VM test 2026-11-19:
   *  the switch stayed away until the file was opened again). */
  async function _recheckMappable(path) {
    if (activeFile?.path !== path) return;
    const fetchFile = () => csrfFetch(`/api/file?path=${encodeURIComponent(path)}`)
      .then(r => r.json()).catch(() => null);
    const fresh = await fetchFile();
    if (!fresh || fresh.error || activeFile?.path !== path) return;
    const fileName = path.split('/').pop();
    if (fresh.unmappable) {
      // Broken by this save, or a different line now.
      _openUnmappable = true;
      _updatePanelToggle('unmappable', '');
      _showLeftPanel('panel-off', fileName, { panelOff: true, unmappable: true,
                                              reason: fresh.unmappable_reason });
      return;
    }
    if (!_openUnmappable) return;
    // Readable again: it stays in the raw editor, marked raw (#r), and the
    // switch shows "panel off" - switching it on asks as usual before the
    // file is restructured.
    _openUnmappable = false;
    let content = fresh.content || '';
    try {
      const r = await csrfFetch('/api/file/panel-mode', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ path, mode: 'r' }),
      });
      const d = await r.json();
      if (d && !d.error) {
        const again = await fetchFile();
        if (again && !again.error) content = again.content || content;
      }
    } catch { /* the switch below still shows "panel off" */ }
    if (activeFile?.path !== path) return;
    // The header now carries the flag; the next raw save must keep it.
    _rawEditHeader = _splitNicoHeader(content).header;
    _updatePanelToggle(fresh.file_type || 'unknown', content, 'r');
    _showLeftPanel('panel-off', fileName, {
      panelOff: true,
      flagSource: _getPanelModeFromContent(content) !== null ? 'file' : 'settings' });
  }

  async function _saveRawFile() {
    const path = _rawEditPath;
    const body = document.getElementById('raw-file-editor')?.value ?? '';
    if (!path) return;
    const content = _rawEditHeader ? _rawEditHeader + '\n' + body : body;

    const fileName = path.split('/').pop();
    // hardware-configuration.nix: Bestätigungsdialog
    if (fileName === 'hardware-configuration.nix') {
      if (!(await nicoConfirm(t('sidebar.hwSaveConfirm')))) return;
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
        refreshUnsaved();
        showToast(t('sidebar.rawSaveOk'), 'success');
        await _recheckMappable(path);
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
    _parseErr    = null;
    _paintTextEditor = null;
    document.querySelectorAll('[data-hidden-by-raw]').forEach(el => {
      el.style.display = ''; delete el.dataset.hiddenByRaw;
    });
    refreshUnsaved();
    const modBtn = document.getElementById('preview-mode-btn');
    if (modBtn) delete modBtn.dataset.mode;
    applyPlainCodeViewBtn();
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

  /** Shows `path` in the raw editor when NiCo cannot represent it, and says
   *  so by returning true. For the ways into the form that do not come
   *  through selectFile() – the start of the program, above all. */
  async function _showIfUnmappable(path) {
    try {
      const r = await csrfFetch(`/api/file?path=${encodeURIComponent(path)}`);
      const d = await r.json();
      if (d.error || !d.unmappable) return false;
      setActiveFile(path, path.split('/').pop());
      await _renderFileIntoView(path, d, { skipTypeDialog: true });
      return true;
    } catch { return false; }
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
    selectFile,
    flakeSave: _flakeSave,
    setActiveFile,
    closeTreeContextMenu: _closeTreeContextMenu,
    updateFlakePreview: _updateFlakePreview,
    refreshFlakeView: _refreshFlakeView,
    togglePlainCodeView: _togglePlainCodeViewInSidebar,
    loadTree,
    clearFlkIfActive,
    refreshPanelToggle: _refreshPanelToggle,
    showIfUnmappable: _showIfUnmappable,
    hasUnsaved,
    refreshUnsaved,
    saveActive,
  };
})();
