/** NiCo frontend — config form: option toggles, snapper/flatpak cards, form data in/out, host switching, HM file list. Split from app.js; classic script sharing the global scope. */
'use strict';

// ── Nix GC options toggle ──────────────────────────────────────────────────
function toggleGcOptions(show) {
  document.getElementById('nix-gc-options')
    ?.classList.toggle('hidden', !show);
}

// ── Home Manager CO-Form ──────────────────────────────────────────────────

async function loadHmFileList() {
  const container = document.getElementById('hm-files-list');
  if (!container) return;
  try {
    const res   = await fetch('/api/hm/files');
    const data  = await res.json();
    const files = data.files || [];
    const cards = files.map(f => `
      <div class="flake-host-card">
        <div class="flake-host-header">
          <span class="flake-host-label">${_esc(f.filename)}</span>
        </div>
      </div>`).join('');
    const addRow = `<div class="fh-add-row">
      <button type="button" class="btn-add-user" id="hm-create-btn">
        ${_esc(t('hm.createFile') || '+ HM-Datei erstellen')}
      </button>
    </div>`;
    container.innerHTML = cards + addRow;
    container.querySelector('#hm-create-btn')?.addEventListener('click', _hmCreateFile);
  } catch (_) {
    container.innerHTML = '';
  }
}

async function _hmCreateFile() {
  const username = (prompt(t('hm.promptUsername') || 'Benutzername (z.B. martin):') || '').trim();
  if (!username) return;
  const stateVersion = document.getElementById('state_version')?.value?.trim() || '';
  const primaryUser  = getAllUsers()[0];
  const homeDir      = primaryUser?.username === username
    ? `/home/${username}` : `/home/${username}`;
  const res  = await csrfFetch('/api/hm/create', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username, home_dir: homeDir, state_version: stateVersion }),
  });
  const data = await res.json();
  if (data.error) {
    const msg = data.error === 'ERR_FILE_EXISTS'
      ? (t('hm.errorFileExists') || 'Datei existiert bereits.')
      : (t('hm.errorCreate') || 'Fehler beim Erstellen.');
    alert(msg);
    return;
  }
  await loadHmFileList();
  _openFileInHmPanel(data.path);
}

// ── Hardware / Virtualisierung / Backup visibility toggles ────────────────
function toggleOpenglOptions(show) {
  document.getElementById('opengl-options')?.classList.toggle('hidden', !show);
}
function toggleBootEfiOptions(show) {
  document.getElementById('boot-efi-options')?.classList.toggle('hidden', !show);
}
function togglePlymouthOptions(show) {
  document.getElementById('plymouth-options')?.classList.toggle('hidden', !show);
}
function updateStateVersionStyle() {
  const el = document.getElementById('state_version');
  if (!el) return;
  el.classList.toggle('field-warn', !el.value.trim());
}
function togglePipewireOptions(show) {
  document.getElementById('pipewire-options')?.classList.toggle('hidden', !show);
}
function toggleVboxGuestOptions(show) {
  document.getElementById('vbox-guest-options')?.classList.toggle('hidden', !show);
}
function toggleDockerOptions(show) {
  document.getElementById('docker-options')?.classList.toggle('hidden', !show);
}
function togglePodmanOptions(show) {
  document.getElementById('podman-options')?.classList.toggle('hidden', !show);
}
function toggleLibvirtdOptions(show) {
  document.getElementById('libvirtd-options')?.classList.toggle('hidden', !show);
}
// ── Snapper-Karten ─────────────────────────────────────────────────────────

const SN_DEFAULTS = { name: '', mountpoint: '', hourly: 5, daily: 7, weekly: 0, monthly: 1, yearly: 0 };
const SNAPPPER_NAME_RE = /^[A-Za-z0-9._-]+$/;

function _snapperCardTitle(name, mountpoint) {
  if (name) return name;
  if (mountpoint) return mountpoint;
  return t('field.snapperNewConfigUnnamed');
}

function _snapperValidateCard(card) {
  const nameInput = card?.querySelector('.sn-name');
  const mountInput = card?.querySelector('.sn-mountpoint');
  const nameErr = card?.querySelector('.sn-name-error');
  const mountErr = card?.querySelector('.sn-mountpoint-error');
  if (!nameInput || !mountInput || !nameErr || !mountErr) return;

  const name = nameInput.value.trim();
  const mount = mountInput.value.trim();

  let nameMsg = '';
  if (name && !SNAPPPER_NAME_RE.test(name)) {
    nameMsg = t('field.snapperConfigNameError');
  }

  let mountMsg = '';
  if (mount && (!mount.startsWith('/') || /\s/.test(mount))) {
    mountMsg = t('field.snapperMountpointError');
  }

  nameInput.setCustomValidity(nameMsg);
  mountInput.setCustomValidity(mountMsg);
  nameErr.textContent = nameMsg;
  mountErr.textContent = mountMsg;
  nameErr.classList.toggle('hidden', !nameMsg);
  mountErr.classList.toggle('hidden', !mountMsg);
}

function _snapperCard(idx, cfg) {
  const name   = escHtml(cfg.name       || '');
  const mount  = escHtml(cfg.mountpoint || '');
  const title  = escHtml(_snapperCardTitle(cfg.name || '', cfg.mountpoint || ''));
  return `<div class="extra-user-card" data-sn-idx="${idx}">
    <div class="extra-user-header eu-toggle" data-sn-idx="${idx}">
      <span class="extra-user-label">${title}</span>
      <span class="eu-header-actions">
        ${niIcon('chevron-down').replace('class="', 'class="eu-chevron ')}
        <button type="button" class="sn-remove-btn" data-sn-idx="${idx}"
                title="${escHtml(t('field.snapperRemove'))}">${niIcon('x')}</button>
      </span>
    </div>
    <div class="extra-user-body">
      <label data-i18n="field.snapperConfigName">${escHtml(t('field.snapperConfigName'))}</label>
      <p class="hint" data-i18n="field.snapperConfigNameHint" style="margin:0 0 4px">${escHtml(t('field.snapperConfigNameHint'))}</p>
      <input type="text" class="sn-name" data-sn-idx="${idx}" value="${name}"
             placeholder="${escHtml(t('field.snapperConfigNamePlaceholder'))}" spellcheck="false" autocomplete="off">
      <p class="raw-panel-hint sn-name-error hidden" style="margin-top:4px;color:var(--red)"></p>

      <label data-i18n="field.snapperMountpoint">${escHtml(t('field.snapperMountpoint'))}</label>
      <p class="hint" data-i18n="field.snapperMountpointHint" style="margin:0 0 4px">${escHtml(t('field.snapperMountpointHint'))}</p>
      <input type="text" class="sn-mountpoint" data-sn-idx="${idx}" value="${mount}"
             placeholder="${escHtml(t('field.snapperMountpointPlaceholder'))}" spellcheck="false" autocomplete="off">
      <p class="raw-panel-hint sn-mountpoint-error hidden" style="margin-top:4px;color:var(--red)"></p>

      <div class="lvl2-section" data-lvl2="sn-timeline-${idx}">
        <div class="lvl2-toggle" data-i18n="field.snapperTimeline">${escHtml(t('field.snapperTimeline'))}</div>
        <div class="lvl2-body">
          <div class="hm-xdg-grid" style="grid-template-columns: repeat(3, 1fr)">
            ${['hourly','daily','weekly','monthly','yearly'].map(f => `
            <div>
              <label data-i18n="field.snapper${f[0].toUpperCase()+f.slice(1)}">${escHtml(t('field.snapper'+f[0].toUpperCase()+f.slice(1)))}</label>
              <input type="number" class="sn-${f}" data-sn-idx="${idx}"
                     min="0" max="99" value="${cfg[f] ?? SN_DEFAULTS[f]}" style="width:70px">
            </div>`).join('')}
          </div>
        </div>
      </div>
    </div>
  </div>`;
}

function renderAllSnapperConfigs(configs) {
  const list = document.getElementById('snapper-list');
  if (!list) return;
  list.innerHTML = configs.map((c, i) => _snapperCard(i, c)).join('');

  list.querySelectorAll('.eu-toggle').forEach(header => {
    header.addEventListener('click', e => {
      if (e.target.closest('.sn-remove-btn')) return;
      const card = header.closest('.extra-user-card');
      const body = card?.querySelector('.extra-user-body');
      if (!body) return;
      const open = body.classList.toggle('open');
      header.classList.toggle('open', open);
    });
  });

  list.querySelectorAll('.sn-name').forEach(inp => {
    inp.addEventListener('input', () => {
      const card  = inp.closest('.extra-user-card');
      const label = card?.querySelector('.extra-user-label');
      const mount = card?.querySelector('.sn-mountpoint')?.value?.trim() || '';
      if (label) label.textContent = _snapperCardTitle(inp.value.trim(), mount);
      if (card) _snapperValidateCard(card);
      schedulePreviewUpdate();
    });
  });

  list.querySelectorAll('.sn-mountpoint').forEach(inp => {
    const card = inp.closest('.extra-user-card');
    if (card) _snapperValidateCard(card);
    inp.addEventListener('input', () => {
      const currentCard = inp.closest('.extra-user-card');
      const label = currentCard?.querySelector('.extra-user-label');
      const name = currentCard?.querySelector('.sn-name')?.value?.trim() || '';
      if (label) label.textContent = _snapperCardTitle(name, inp.value.trim());
      if (currentCard) _snapperValidateCard(currentCard);
      schedulePreviewUpdate();
    });
    inp.addEventListener('change', () => {
      const currentCard = inp.closest('.extra-user-card');
      if (currentCard) _snapperValidateCard(currentCard);
      schedulePreviewUpdate();
    });
  });

  list.querySelectorAll('.sn-remove-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      const idx     = parseInt(btn.dataset.snIdx, 10);
      const current = getAllSnapperConfigs();
      current.splice(idx, 1);
      renderAllSnapperConfigs(current);
      schedulePreviewUpdate();
    });
  });

  list.querySelectorAll('input').forEach(el => {
    if (!el.classList.contains('sn-name') && !el.classList.contains('sn-mountpoint')) {
      el.addEventListener('input',  schedulePreviewUpdate);
      el.addEventListener('change', schedulePreviewUpdate);
    }
  });

  initLvl2Sections(list);
}

function getAllSnapperConfigs() {
  const list = document.getElementById('snapper-list');
  if (!list) return [];
  return [...list.querySelectorAll('.extra-user-card[data-sn-idx]')].map(card => {
    const idx = card.dataset.snIdx;
    return {
      name:       card.querySelector(`.sn-name[data-sn-idx="${idx}"]`)?.value?.trim()       || '',
      mountpoint: card.querySelector(`.sn-mountpoint[data-sn-idx="${idx}"]`)?.value?.trim() || '',
      hourly:     parseInt(card.querySelector(`.sn-hourly[data-sn-idx="${idx}"]`)?.value    || '5', 10),
      daily:      parseInt(card.querySelector(`.sn-daily[data-sn-idx="${idx}"]`)?.value     || '7', 10),
      weekly:     parseInt(card.querySelector(`.sn-weekly[data-sn-idx="${idx}"]`)?.value    || '0', 10),
      monthly:    parseInt(card.querySelector(`.sn-monthly[data-sn-idx="${idx}"]`)?.value   || '1', 10),
      yearly:     parseInt(card.querySelector(`.sn-yearly[data-sn-idx="${idx}"]`)?.value    || '0', 10),
    };
  });
}

// ── Flatpak-Remotes ────────────────────────────────────────────────────────
function renderFlatpakRemotes(remotes) {
  const list = document.getElementById('flatpak-remotes-list');
  if (!list) return;
  list.innerHTML = remotes.map((r, i) => `
    <div class="fp-remote-row" data-fp-idx="${i}">
      <input type="text" class="fp-name" data-fp-idx="${i}" value="${escHtml(r.name || '')}"
             placeholder="${escHtml(t('field.flatpakRemoteName'))}" spellcheck="false" autocomplete="off">
      <input type="text" class="fp-url" data-fp-idx="${i}" value="${escHtml(r.url || '')}"
             placeholder="https://..." spellcheck="false" autocomplete="off">
      <button type="button" class="eu-remove-btn fp-remove-btn" data-fp-idx="${i}"
              title="${escHtml(t('field.flatpakRemoteRemove'))}">${niIcon('x')}</button>
    </div>
  `).join('');
  list.querySelectorAll('.fp-remove-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      const idx = parseInt(btn.dataset.fpIdx, 10);
      const cur = getAllFlatpakRemotes();
      cur.splice(idx, 1);
      renderFlatpakRemotes(cur);
      schedulePreviewUpdate();
    });
  });
  list.querySelectorAll('input').forEach(el => {
    el.addEventListener('input', schedulePreviewUpdate);
  });
}

function getAllFlatpakRemotes() {
  const list = document.getElementById('flatpak-remotes-list');
  if (!list) return [];
  return [...list.querySelectorAll('.fp-remote-row')].map(row => {
    const idx = row.dataset.fpIdx;
    return {
      name: row.querySelector(`.fp-name[data-fp-idx="${idx}"]`)?.value?.trim() || '',
      url:  row.querySelector(`.fp-url[data-fp-idx="${idx}"]`)?.value?.trim()  || '',
    };
  });
}

// ── Passwort-Toggle ────────────────────────────────────────────────────────
function initPasswordToggle() {
  const cb    = document.getElementById('user_has_password');
  const input = document.getElementById('user_initial_password');
  if (!cb || !input) return;
  cb.addEventListener('change', () => {
    input.classList.toggle('hidden', !cb.checked);
    if (!cb.checked) input.value = '';
    schedulePreviewUpdate();
  });
}

// ── Alles ein-/aufklappen ──────────────────────────────────────────────────
function collapseAll() {
  document.querySelectorAll('section.collapsible').forEach(s => {
    collapsedSections.add(s.dataset.section);
    s.classList.add('collapsed');
  });
  // Also add code-only sections (Home Manager) that have no left-panel element
  BRICK_SECTIONS.forEach(s => collapsedSections.add(s));
  collapsedSections.add('__header__');
  applySectionCollapse();
}

function expandAll() {
  document.querySelectorAll('section.collapsible').forEach(s => {
    collapsedSections.delete(s.dataset.section);
    s.classList.remove('collapsed');
  });
  // Also remove code-only sections
  BRICK_SECTIONS.forEach(s => collapsedSections.delete(s));
  collapsedSections.delete('__header__');
  applySectionCollapse();
  // Auch individuelle Brix aufklappen
  collapsedBrix.clear();
  localStorage.setItem('nico-collapsed-brix', '[]');
  document.querySelectorAll('.code-brix.collapsed').forEach(el => el.classList.remove('collapsed'));
}

// ── UI helpers ─────────────────────────────────────────────────────────────
function showSetupOverlay() {
  document.getElementById('setup-overlay').classList.remove('hidden');
  document.getElementById('app').classList.add('hidden');
  // Reset all setup steps
  document.getElementById('setup-input-section')?.classList.remove('hidden');
  document.getElementById('setup-confirm')?.classList.add('hidden');
  document.getElementById('setup-symlink-step')?.classList.add('hidden');
  _pendingSetupData = null;
  document.getElementById('nixos-dir-input')?.focus();
}

function showApp(configDir) {
  document.getElementById('setup-overlay').classList.add('hidden');
  document.getElementById('app').classList.remove('hidden');
  document.getElementById('config-dir-label').textContent = configDir;
}

function markConfigDirty() {
  document.dispatchEvent(new CustomEvent('nico:config-dirty'));
}

function setField(id, value) {
  const el = document.getElementById(id);
  if (el) el.value = value ?? '';
}

function setCheck(id, value) {
  const el = document.getElementById(id);
  if (el) el.checked = !!value;
}

function getFormData() {
  const v  = id => document.getElementById(id)?.value  ?? '';
  const ch = id => document.getElementById(id)?.checked ?? false;

  // Collect checked font packages from the Schriftarten section
  const fonts = [...document.querySelectorAll('.font-check:checked')]
    .map(cb => cb.value);

  const packages = getPackageListData();

  return {
    hostname:            v('hostname'),
    state_version:       v('state_version'),
    nix_args:            v('nix_args') || 'config, pkgs, lib',
    timezone:            v('timezone'),
    locale:              v('locale'),
    extra_locale: document.getElementById('extra_locale_enable')?.checked
      ? (document.getElementById('extra_locale')?.value || '') : '',
    keyboard_layout:     v('keyboard_layout'),
    keyboard_variant:    '',        // no separate UI field yet
    keyboard_console:    v('keyboard_console'),
    networkmanager:       ch('networkmanager'),
    ssh:                  ch('ssh'),
    firewall_disable:     ch('firewall_disable'),
    firewall_tcp_enable:  ch('firewall_tcp_enable'),
    firewall_tcp_ports:   v('firewall_tcp_ports'),
    firewall_udp_enable:  ch('firewall_udp_enable'),
    firewall_udp_ports:   v('firewall_udp_ports'),
    boot_loader:         v('boot_loader') || 'none',
    boot_efi_can_touch:  ch('boot_efi_can_touch'),
    boot_efi_mount_point: v('boot_efi_mount_point') || '/boot',
    boot_config_limit:   parseInt(v('boot_config_limit') || '5', 10),
    boot_kernel_params:  v('boot_kernel_params'),
    plymouth_enabled:    ch('plymouth_enabled'),
    boot_initrd_systemd: ch('boot_initrd_systemd'),
    plymouth_theme:      v('plymouth_theme'),
    printing:            ch('printing'),
    avahi:               ch('avahi'),
    bluetooth:           ch('bluetooth'),
    blueman:             ch('blueman'),
    libinput:            ch('libinput'),
    fprintd:             ch('fprintd'),
    pcscd:               ch('pcscd'),
    sunshine:            ch('sunshine'),
    pipewire_32bit:      ch('pipewire_32bit'),
    desktop_environment: v('desktop_environment'),
    autologin_user:      v('autologin_user'),
    pipewire:            ch('pipewire'),
    ...(() => {
      const allUsers = getAllUsers();
      const primary  = allUsers[0] || {};
      const groups   = Array.isArray(primary.groups) ? primary.groups : DEFAULT_EXTRA_USER_GROUPS;
      return {
        username:              primary.username         || '',
        user_description:      primary.description      || '',
        user_initial_password: primary.initial_password || '',
        user_uid:              primary.uid              || '',
        user_groups:           groups,
        user_groups_extra:     '',
        user_shell:            primary.shell            || 'bash',
        user_extra_nix:        primary.extra_nix        || '',
        extra_users:           allUsers.slice(1),
      };
    })(),
    guest_user:          ch('guest_user'),
    allowUnfree:         ch('allowUnfree'),
    steam:               ch('steam'),
    appimage:            ch('appimage'),
    firefox:             ch('firefox'),
    firefox_lang_packs:  ch('firefox') ? v('firefox_lang_packs') : '',
    firefox_prefs:       ch('firefox') ? v('firefox_prefs')      : '',
    flatpak_enable:      ch('flatpak_enable'),
    flatpak_remotes:     ch('flatpak_enable') ? getAllFlatpakRemotes() : [],
    packages,
    fonts,
    fonts_extra:         v('fonts_extra'),
    flakes:              ch('flakes'),
    nix_optimize_store:  ch('nix_optimize_store'),
    nix_gc:              ch('nix_gc'),
    nix_gc_frequency:    v('nix_gc_frequency'),
    nix_gc_age:          v('nix_gc_age'),

    // Home Manager NixOS-Modul
    hm_use_global_pkgs:      ch('hm_use_global_pkgs'),
    hm_use_user_packages:    ch('hm_use_user_packages'),
    hm_plasma_manager:       ch('hm_plasma_manager'),
    hm_shared_modules_extra: v('hm_shared_modules_extra'),

    // Hardware
    enable_all_firmware: ch('enable_all_firmware'),
    cpu_microcode:       v('cpu_microcode') || 'none',
    opengl:              ch('opengl'),
    opengl_32bit:        ch('opengl_32bit'),
    zram_swap:           ch('zram_swap'),

    openrgb:             ch('openrgb'),
    ledger:              ch('ledger'),
    ratbagd:             ch('ratbagd'),

    // Virtualisierung
    docker:               ch('docker'),
    docker_rootless:      ch('docker_rootless'),
    podman:               ch('podman'),
    podman_docker_compat: ch('podman_docker_compat'),
    virtualbox_host:          ch('virtualbox_host'),
    virtualbox_guest:         ch('virtualbox_guest'),
    virtualbox_guest_drag_drop: ch('virtualbox_guest_drag_drop'),
    libvirtd:             ch('libvirtd'),
    virt_manager:         ch('virt_manager'),

    // Dateisystem & Backup
    btrfs_scrub:     ch('btrfs_scrub'),
    snapper_enable:  ch('snapper_enable'),
    snapper_configs: getAllSnapperConfigs(),

    // Multi-host context: tells the preview endpoint which host is active
    _host: _activeHost,
    _co_path: _coLoadedPath,
    _co_ready: _coFormReady,
  };
}

// ── Multi-Host Support ────────────────────────────────────────────────────────


async function switchHost(hostName) {
  if (hostName === _activeHost) return;

  if (_formDirty || _flakeFormDirty) {
    const ok = await confirmHostSwitch();
    if (!ok) return;
  }

  _activeHost = hostName;

  if (hostName === '') {
    _brixTargetFile  = 'configuration.nix';
    _brixContextFile = 'configuration.nix';
    await _populateCoFormFromFile('configuration.nix');
    Sidebar.refreshPanelToggle('configuration.nix', 'co');
    await updatePreview();
  } else {
    await loadHostConfig(hostName);
  }
}

async function confirmHostSwitch() {
  return new Promise(resolve => {
    const overlay = document.createElement('div');
    overlay.className = 'overlay';
    overlay.innerHTML = `
      <div class="dialog">
        <h2 class="dialog-title">${t('host.switchTitle')}</h2>
        <p>${t('host.switchInfo')}</p>
        <div class="dialog-actions">
          <button id="host-switch-save"     class="btn-primary">${t('host.switchSave')}</button>
          <button id="host-switch-continue" class="btn-secondary">${t('host.switchContinue')}</button>
          <button id="host-switch-cancel"   class="btn-danger">${t('host.switchCancel')}</button>
        </div>
      </div>`;
    document.body.appendChild(overlay);

    overlay.querySelector('#host-switch-save').addEventListener('click', async () => {
      document.body.removeChild(overlay);
      const ok = await saveAndWrite();
      resolve(!!ok);
    });
    overlay.querySelector('#host-switch-continue').addEventListener('click', () => {
      document.body.removeChild(overlay);
      resolve(true);
    });
    overlay.querySelector('#host-switch-cancel').addEventListener('click', () => {
      document.body.removeChild(overlay);
      resolve(false);
    });
  });
}

async function confirmClosePushPrompt() {
  return new Promise(resolve => {
    const overlay = document.createElement('div');
    overlay.className = 'overlay';
    overlay.innerHTML = `
      <div class="dialog">
        <h2 class="dialog-title">${escHtml(t('git.closePrompt.title'))}</h2>
        <p>${escHtml(t('git.closePrompt.body'))}</p>
        <div class="dialog-actions">
          <button id="close-push-confirm" class="btn-primary">${escHtml(t('git.closePrompt.yes'))}</button>
          <button id="close-push-skip" class="btn-secondary">${escHtml(t('git.closePrompt.no'))}</button>
        </div>
      </div>`;
    document.body.appendChild(overlay);

    const cleanup = (result) => {
      if (overlay.parentNode) document.body.removeChild(overlay);
      resolve(result);
    };

    overlay.querySelector('#close-push-confirm')?.addEventListener('click', () => cleanup(true));
    overlay.querySelector('#close-push-skip')?.addEventListener('click', () => cleanup(false));
    overlay.addEventListener('click', e => {
      if (e.target === overlay) cleanup(false);
    });
  });
}

async function loadHostConfig(hostName) {
  await _populateCoFormFromFile(hostCoPath(hostName));
  Sidebar.refreshPanelToggle(hostCoPath(hostName), 'co');
  await updatePreview();
}

function populateFormFromData(data) {
  const v  = (id, val) => { const el = document.getElementById(id); if (el) el.value = val ?? ''; };
  const ch = (id, val) => { const el = document.getElementById(id); if (el) el.checked = !!val; };

  if ('hostname'           in data) v('hostname',            data.hostname);
  if ('state_version'      in data) { v('state_version', data.state_version); updateStateVersionStyle(); }
  if ('nix_args'           in data) v('nix_args',            data.nix_args || 'config, pkgs, lib');
  if ('timezone'           in data) v('timezone',            data.timezone);
  if ('locale'             in data) v('locale',              data.locale);
  if ('keyboard_layout'    in data) v('keyboard_layout',     data.keyboard_layout);
  if ('keyboard_console'   in data) v('keyboard_console',    data.keyboard_console);
  if ('desktop_environment' in data) v('desktop_environment', data.desktop_environment);
  if ('autologin_user'     in data) v('autologin_user',      data.autologin_user);
  if ('username'           in data) v('username',            data.username);
  if ('user_description'   in data) v('user_description',    data.user_description);
  if ('user_uid'           in data) v('user_uid',            data.user_uid);
  if ('user_groups_extra'  in data) v('user_groups_extra',   data.user_groups_extra);
  if ('user_shell'         in data) v('user_shell',          data.user_shell || 'bash');
  if ('user_extra_nix'     in data) v('user_extra_nix',      data.user_extra_nix);
  if ('nix_gc_frequency'   in data) v('nix_gc_frequency',    data.nix_gc_frequency);
  if ('nix_gc_age'         in data) v('nix_gc_age',          data.nix_gc_age);
  if ('fonts_extra'        in data) v('fonts_extra',         data.fonts_extra);
  if ('firewall_tcp_ports' in data) v('firewall_tcp_ports',  data.firewall_tcp_ports);
  if ('firewall_udp_ports' in data) v('firewall_udp_ports',  data.firewall_udp_ports);
  if ('boot_loader'        in data) v('boot_loader',         data.boot_loader || 'none');
  if ('boot_efi_mount_point' in data) v('boot_efi_mount_point', data.boot_efi_mount_point || '/boot');
  if ('boot_config_limit'  in data) v('boot_config_limit',  data.boot_config_limit ?? 5);
  if ('cpu_microcode'      in data) v('cpu_microcode',       data.cpu_microcode || 'none');

  if ('guest_user'         in data) ch('guest_user',          data.guest_user);
  if ('networkmanager'      in data) ch('networkmanager',       data.networkmanager);
  if ('ssh'                 in data) ch('ssh',                  data.ssh);
  if ('firewall_disable'    in data) ch('firewall_disable',     data.firewall_disable);
  if ('firewall_tcp_enable' in data) {
    ch('firewall_tcp_enable', data.firewall_tcp_enable);
    document.getElementById('firewall-tcp-detail')?.classList.toggle('hidden', !data.firewall_tcp_enable);
  }
  if ('firewall_udp_enable' in data) {
    ch('firewall_udp_enable', data.firewall_udp_enable);
    document.getElementById('firewall-udp-detail')?.classList.toggle('hidden', !data.firewall_udp_enable);
  }
  if ('boot_efi_can_touch'  in data) ch('boot_efi_can_touch',   data.boot_efi_can_touch);
  if ('boot_kernel_params'  in data) v('boot_kernel_params',    data.boot_kernel_params || '');
  if ('plymouth_enabled'    in data) {
    ch('plymouth_enabled', data.plymouth_enabled);
    togglePlymouthOptions(!!data.plymouth_enabled);
  }
  if ('boot_initrd_systemd' in data) ch('boot_initrd_systemd', data.boot_initrd_systemd);
  if ('plymouth_theme'      in data) v('plymouth_theme',        data.plymouth_theme || '');
  if ('printing'           in data) ch('printing',            data.printing);
  if ('avahi'              in data) ch('avahi',               data.avahi);
  if ('bluetooth'          in data) ch('bluetooth',           data.bluetooth);
  if ('blueman'            in data) ch('blueman',             data.blueman);
  if ('libinput'           in data) ch('libinput',            data.libinput);
  if ('fprintd'            in data) ch('fprintd',             data.fprintd);
  if ('pcscd'              in data) ch('pcscd',               data.pcscd);
  if ('sunshine'           in data) ch('sunshine',            data.sunshine);
  if ('pipewire'           in data) ch('pipewire',            data.pipewire);
  if ('pipewire_32bit'     in data) ch('pipewire_32bit',      data.pipewire_32bit);
  if ('allowUnfree'        in data) ch('allowUnfree',         data.allowUnfree);
  if ('steam'              in data) ch('steam',               data.steam);
  if ('appimage'           in data) ch('appimage',            data.appimage);
  if ('firefox'            in data) ch('firefox',             data.firefox);
  if ('flatpak_enable' in data) {
    ch('flatpak_enable', data.flatpak_enable);
    document.getElementById('flatpak-area')?.classList.toggle('hidden', !data.flatpak_enable);
    renderFlatpakRemotes(data.flatpak_remotes || []);
  }
  if ('flakes'             in data) ch('flakes',              data.flakes);
  if ('nix_optimize_store' in data) ch('nix_optimize_store',  data.nix_optimize_store);
  if ('nix_gc'             in data) ch('nix_gc',              data.nix_gc);
  if ('enable_all_firmware' in data) ch('enable_all_firmware', data.enable_all_firmware);
  if ('opengl'             in data) ch('opengl',              data.opengl);
  if ('opengl_32bit'       in data) ch('opengl_32bit',        data.opengl_32bit);
  if ('zram_swap'          in data) ch('zram_swap',           data.zram_swap);
  if ('openrgb'            in data) ch('openrgb',             data.openrgb);
  if ('ledger'             in data) ch('ledger',              data.ledger);
  if ('ratbagd'            in data) ch('ratbagd',             data.ratbagd);
  if ('docker'             in data) ch('docker',              data.docker);
  if ('docker_rootless'    in data) ch('docker_rootless',     data.docker_rootless);
  if ('podman'             in data) ch('podman',              data.podman);
  if ('podman_docker_compat' in data) ch('podman_docker_compat', data.podman_docker_compat);
  if ('virtualbox_host'    in data) ch('virtualbox_host',     data.virtualbox_host);
  if ('virtualbox_guest'   in data) ch('virtualbox_guest',    data.virtualbox_guest);
  if ('virtualbox_guest_drag_drop' in data) ch('virtualbox_guest_drag_drop', data.virtualbox_guest_drag_drop);
  if ('libvirtd'           in data) ch('libvirtd',            data.libvirtd);
  if ('virt_manager'       in data) ch('virt_manager',        data.virt_manager);
  if ('btrfs_scrub'     in data) ch('btrfs_scrub',    data.btrfs_scrub);
  if ('snapper_enable'  in data) {
    ch('snapper_enable', data.snapper_enable);
    document.getElementById('snapper-area')?.classList.toggle('hidden', !data.snapper_enable);
    renderAllSnapperConfigs(data.snapper_configs || []);
  }

  if ('user_groups' in data) {
    const selectedGroups = new Set(data.user_groups || []);
    document.querySelectorAll('.user-group-check').forEach(cb => {
      cb.checked = selectedGroups.has(cb.value);
    });
  }
  if ('username' in data || 'extra_users' in data) {
    const primary = {
      username:         data.username || '',
      description:      data.user_description || '',
      initial_password: data.user_initial_password || '',
      uid:              data.user_uid || '',
      groups:           data.user_groups || DEFAULT_EXTRA_USER_GROUPS,
      shell:            data.user_shell || 'bash',
      extra_nix:        data.user_extra_nix || '',
    };
    renderAllUsers([primary, ...(data.extra_users || [])]);
  }
  if ('fonts' in data) {
    const selectedFonts = new Set(data.fonts || []);
    document.querySelectorAll('.font-check').forEach(cb => {
      cb.checked = selectedFonts.has(cb.value);
    });
  }
  if ('packages' in data) renderPackageList(data.packages || []);

  document.getElementById('extra-locale-detail')?.classList.toggle('hidden', !document.getElementById('extra_locale_enable')?.checked);
  document.getElementById('firewall-tcp-detail')?.classList.toggle('hidden', !document.getElementById('firewall_tcp_enable')?.checked);
  document.getElementById('firewall-udp-detail')?.classList.toggle('hidden', !document.getElementById('firewall_udp_enable')?.checked);
  document.getElementById('firefox-detail')?.classList.toggle('hidden', !document.getElementById('firefox')?.checked);
  toggleBootEfiOptions((document.getElementById('boot_loader')?.value || 'none') !== 'none');
  togglePipewireOptions(!!document.getElementById('pipewire')?.checked);
  toggleGcOptions(!!document.getElementById('nix_gc')?.checked);
  toggleOpenglOptions(!!document.getElementById('opengl')?.checked);
  toggleDockerOptions(!!document.getElementById('docker')?.checked);
  togglePodmanOptions(!!document.getElementById('podman')?.checked);
  toggleLibvirtdOptions(!!document.getElementById('libvirtd')?.checked);
  toggleVboxGuestOptions(!!document.getElementById('virtualbox_guest')?.checked);
  updateSectionVisibility();
}

/** Setzt alle CO-Formularfelder auf leer/unchecked zurück. */
function clearCoForm() {
  const form = document.getElementById('config-form');
  if (!form) return;
  form.querySelectorAll('input[type="text"], textarea, select').forEach(el => { el.value = ''; });
  form.querySelectorAll('input[type="checkbox"]').forEach(el => { el.checked = false; });
  renderAllUsers([{
    username: '',
    description: '',
    initial_password: '',
    uid: '',
    groups: [...DEFAULT_EXTRA_USER_GROUPS],
    shell: 'bash',
    extra_nix: '',
  }]);
  renderPackageList([]);
  document.querySelectorAll('.font-check').forEach(cb => { cb.checked = false; });
  setField('boot_loader', 'none');
  setField('cpu_microcode', 'none');
  setField('boot_efi_mount_point', '/boot');
  setField('boot_config_limit', 5);
  setField('boot_kernel_params', '');
  setField('plymouth_theme', '');
  setField('nix_gc_frequency', 'weekly');
  setField('nix_gc_age', '30d');
  renderAllSnapperConfigs([]);
  document.getElementById('extra-locale-detail')?.classList.add('hidden');
  document.getElementById('firewall-tcp-detail')?.classList.add('hidden');
  document.getElementById('firewall-udp-detail')?.classList.add('hidden');
  document.getElementById('firefox-detail')?.classList.add('hidden');
  toggleBootEfiOptions(false);
  togglePlymouthOptions(false);
  togglePipewireOptions(false);
  toggleGcOptions(false);
  toggleOpenglOptions(false);
  toggleDockerOptions(false);
  togglePodmanOptions(false);
  toggleLibvirtdOptions(false);
  toggleVboxGuestOptions(false);
  document.getElementById('snapper-area')?.classList.add('hidden');
  document.getElementById('flatpak-area')?.classList.add('hidden');
  renderFlatpakRemotes([]);
  updateSectionVisibility();
}

/**
 * Öffnet eine CO-Datei, parst ihren Inhalt und befüllt das Formular
 * ausschließlich mit den gefundenen Werten. Nicht vorhandene Felder
 * bleiben leer – nico.json-Werte werden nicht eingemischt.
 */
async function _populateCoFormFromFile(path, _content) {
  _coFormReady = false;
  _coLoadedPath = path;
  clearCoForm();
  let loaded = false;
  try {
    const res  = await fetch(`/api/parse/co?path=${encodeURIComponent(path)}`);
    const data = await res.json();
    if (!data.error) {
      populateFormFromData(data);
      loaded = true;
    }
  } catch { /* non-fatal: leeres Formular ist besser als falsche Werte */ }
  _coFormReady = loaded;
}
