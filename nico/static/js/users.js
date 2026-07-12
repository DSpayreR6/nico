/** NiCo frontend — user account cards. Split from app.js; classic script sharing the global scope. */
'use strict';

// ── Benutzer (einheitliche Karten) ────────────────────────────────────────

const DEFAULT_EXTRA_USER_GROUPS = ['wheel', 'networkmanager'];

function _userCard(idx, user, isOnly) {
  const uname  = escHtml(user.username      || '');
  const desc   = escHtml(user.description   || '');
  const pass   = escHtml(user.initial_password || '');
  const uid    = escHtml(String(user.uid    || ''));
  const groupsArr = Array.isArray(user.groups) ? user.groups : DEFAULT_EXTRA_USER_GROUPS;
  const groups = escHtml(groupsArr.join(' '));
  const shell  = user.shell || 'bash';
  const extra  = escHtml(user.extra_nix || '');
  const hasPw  = !!user.initial_password;
  const delTitle = isOnly ? escHtml(t('field.lastUser')) : escHtml(t('field.removeUser'));

  return `<div class="extra-user-card" data-eu-idx="${idx}">
    <div class="extra-user-header eu-toggle" data-eu-idx="${idx}">
      <span class="extra-user-label">${uname || t('field.newUser')}</span>
      <span class="eu-header-actions">
        ${niIcon('chevron-down').replace('class="', 'class="eu-chevron ')}
        <button type="button" class="eu-remove-btn" data-eu-idx="${idx}"
                ${isOnly ? 'disabled' : ''} title="${delTitle}">${niIcon('x')}</button>
      </span>
    </div>
    <div class="extra-user-body">
      <label>${escHtml(t('field.username'))}</label>
      <input type="text" class="eu-username" data-eu-idx="${idx}"
             value="${uname}" placeholder="benutzer"
             spellcheck="false" autocomplete="off">

      <label>${escHtml(t('field.userDesc'))}
        <span class="hint">${escHtml(t('field.userDescHint'))}</span></label>
      <input type="text" class="eu-description" data-eu-idx="${idx}"
             value="${desc}" placeholder="Max Mustermann">

      <label class="toggle-row" style="margin-top:4px">
        <span>${escHtml(t('field.userPass'))}
          <span class="hint">${escHtml(t('field.userPassHint'))}</span>
        </span>
        <span class="toggle-wrap">
          <input type="checkbox" class="eu-has-password" data-eu-idx="${idx}"${hasPw ? ' checked' : ''}>
          <span class="toggle-track"><span class="toggle-thumb"></span></span>
        </span>
      </label>
      <input type="password" class="eu-password${hasPw ? '' : ' hidden'}" data-eu-idx="${idx}"
             value="${pass}" autocomplete="new-password" style="margin-top:6px"
             placeholder="${escHtml(t('field.userPassPlaceholder') || '')}">

      <label>${escHtml(t('field.uid'))}
        <span class="hint">${escHtml(t('field.uidHint'))}</span></label>
      <input type="text" class="eu-uid" data-eu-idx="${idx}" value="${uid}" placeholder="1000">

      <label>${escHtml(t('field.groups'))}
        <span class="hint">${escHtml(t('field.groupsExtraHint'))}</span></label>
      <input type="text" class="eu-groups" data-eu-idx="${idx}" value="${groups}"
             placeholder="wheel networkmanager">

      <label>${escHtml(t('field.shell'))}</label>
      <select class="eu-shell" data-eu-idx="${idx}">
        ${['bash','zsh','fish','nushell'].map(s =>
          `<option value="${s}"${shell===s?' selected':''}>${s}</option>`).join('')}
      </select>

      <label>${escHtml(t('field.userExtraNix'))}</label>
      <textarea class="eu-extra-nix mono-input" data-eu-idx="${idx}" rows="3"
                placeholder='openssh.authorizedKeys.keys = [ "ssh-ed25519 …" ];'
                spellcheck="false">${extra}</textarea>
    </div>
  </div>`;
}

function renderAllUsers(users) {
  const list = document.getElementById('users-list');
  if (!list) return;
  const isOnly = users.length <= 1;
  list.innerHTML = users.map((u, i) => _userCard(i, u, isOnly)).join('');
  updateSectionVisibility();

  // First card starts expanded
  const firstCard = list.querySelector('.extra-user-card');
  if (firstCard) {
    firstCard.querySelector('.extra-user-body')?.classList.add('open');
    firstCard.querySelector('.eu-toggle')?.classList.add('open');
  }

  // Collapse/expand toggle
  list.querySelectorAll('.eu-toggle').forEach(header => {
    header.addEventListener('click', e => {
      if (e.target.closest('.eu-remove-btn')) return;
      const card = header.closest('.extra-user-card');
      const body = card?.querySelector('.extra-user-body');
      if (!body) return;
      const open = body.classList.toggle('open');
      header.classList.toggle('open', open);
    });
  });

  // Live-update of header label as user types
  list.querySelectorAll('.eu-username').forEach(inp => {
    inp.addEventListener('input', () => {
      const card = inp.closest('.extra-user-card');
      const label = card?.querySelector('.extra-user-label');
      if (label) label.textContent = inp.value || t('field.newUser');
      schedulePreviewUpdate();
    });
  });

  // Password toggle per Karte
  list.querySelectorAll('.eu-has-password').forEach(cb => {
    const idx   = cb.dataset.euIdx;
    const pwInp = list.querySelector(`.eu-password[data-eu-idx="${idx}"]`);
    cb.addEventListener('change', () => {
      pwInp?.classList.toggle('hidden', !cb.checked);
      schedulePreviewUpdate();
    });
  });

  // Remove buttons
  list.querySelectorAll('.eu-remove-btn:not([disabled])').forEach(btn => {
    btn.addEventListener('click', () => {
      const idx = parseInt(btn.dataset.euIdx, 10);
      const current = getAllUsers();
      current.splice(idx, 1);
      renderAllUsers(current);
      schedulePreviewUpdate();
    });
  });
  list.querySelectorAll('input, select, textarea').forEach(el => {
    if (!el.classList.contains('eu-username')) {
      el.addEventListener('input',  schedulePreviewUpdate);
      el.addEventListener('change', schedulePreviewUpdate);
    }
  });
}

function getAllUsers() {
  const list = document.getElementById('users-list');
  if (!list) return [];
  return [...list.querySelectorAll('.extra-user-card')].map(card => {
    const idx = card.dataset.euIdx;
    const hasPw = card.querySelector(`.eu-has-password[data-eu-idx="${idx}"]`)?.checked;
    const groupsRaw = card.querySelector(`.eu-groups[data-eu-idx="${idx}"]`)?.value?.trim() || '';
    return {
      username:         card.querySelector(`.eu-username[data-eu-idx="${idx}"]`)?.value?.trim()  || '',
      description:      card.querySelector(`.eu-description[data-eu-idx="${idx}"]`)?.value?.trim() || '',
      initial_password: hasPw ? (card.querySelector(`.eu-password[data-eu-idx="${idx}"]`)?.value || '') : '',
      uid:              card.querySelector(`.eu-uid[data-eu-idx="${idx}"]`)?.value?.trim()        || '',
      groups:           groupsRaw ? groupsRaw.split(/\s+/) : DEFAULT_EXTRA_USER_GROUPS,
      shell:            card.querySelector(`.eu-shell[data-eu-idx="${idx}"]`)?.value              || 'bash',
      extra_nix:        card.querySelector(`.eu-extra-nix[data-eu-idx="${idx}"]`)?.value?.trim()  || '',
    };
  });
}

// ── Benutzer-Detail aufklappen ─────────────────────────────────────────────
function initUserDetail() {
  const btn  = document.getElementById('user-detail-toggle');
  const card = document.getElementById('user-detail-card');
  if (!btn || !card) return;

  // Restore state from localStorage
  const stored = localStorage.getItem('nico_user_detail');
  if (stored === 'expanded') {
    card.classList.add('expanded');
    btn.classList.add('expanded');
  }

  btn.addEventListener('click', () => {
    const isExpanded = card.classList.toggle('expanded');
    btn.classList.toggle('expanded', isExpanded);
    localStorage.setItem('nico_user_detail', isExpanded ? 'expanded' : 'collapsed');
  });
}
