/**
 * hardware-configuration.nix: does it belong to this machine?
 *
 * The file names the disks of exactly one computer. It arrives from elsewhere
 * in three ways - an import, a restore, or a `git clone` NiCo never witnesses -
 * and building a config that describes another machine's disks produces a
 * system that does not boot. The validator catches it before a build, but only
 * then; this runs at start and right after the two events that can introduce it.
 *
 * Everything except a confirmed match stops and asks. There is no preselected
 * answer and no "decide later" that quietly continues: a wrong file costs the
 * whole installation, so the choice is the user's and it is made now.
 */

/** Show the decision dialog if the situation calls for one. Resolves when settled. */
async function checkHardwareMatch(decision) {
  let data = decision;
  if (!data) {
    try {
      const res = await fetch('/api/hardware/check');
      if (!res.ok) return;
      data = await res.json();
    } catch { return; }
  }
  if (!data || !data.blocking) return;
  await _showHardwareDialog(data);
}

function _hwVerdictLine(f) {
  const where = f.path;
  if (f.verdict === 'mismatch') {
    const list = (f.missing || []).slice(0, 3).join(', ');
    const more = (f.missing || []).length - 3;
    return t('hw.line.mismatch', where, more > 0 ? `${list} (+${more})` : list);
  }
  if (f.verdict === 'unknown') {
    const list = (f.unresolved || []).slice(0, 3).join(', ') || '–';
    return t('hw.line.unknown', where, list);
  }
  return t('hw.line.match', where);
}

/** Short verdict label shown behind an entry in both dropdowns. */
function _hwVerdictTag(verdict) {
  if (verdict === 'match')    return t('hw.vMatch');
  if (verdict === 'mismatch') return t('hw.vMismatch');
  if (verdict === 'unknown')  return t('hw.vUnknown');
  return '';
}

/**
 * Where this machine's hardware config belongs.
 *
 * Every host of the config plus the root, whether a file sits there or not: a
 * config arriving from another machine may have no host for this one, and then
 * the answer is a new file rather than a replacement.
 */
function _hwTargetOptions(data) {
  return (data.targets || []).map(e => {
    const bits = [];
    if (e.is_this_host) bits.push(t('hw.thisHost'));
    if (!e.exists)      bits.push(t('hw.targetNew'));
    else if (e.verdict) bits.push(_hwVerdictTag(e.verdict));
    const suffix = bits.length ? `  —  ${bits.join(', ')}` : '';
    // The host travels back, not the path: the route rebuilds the path from
    // hosts_dir itself and refuses a host it does not know.
    return `<option value="${escHtml(e.host || '')}">${escHtml(e.path + suffix)}</option>`;
  }).join('');
}

/**
 * Which file is this machine's.
 *
 * The files parked before the import come first – they are the ones it just
 * replaced. A confirmed mismatch is listed but not selectable: naming another
 * machine's disks would look like a repair and leave the config unbootable.
 */
function _hwSourceOptions(data) {
  return (data.sources || []).map(s => {
    const bits = [_hwVerdictTag(s.verdict)];
    if (s.kind === 'stash') bits.unshift(t('hw.srcStash'));
    if (s.host)             bits.unshift(s.host);
    const label = `${s.label}  —  ${bits.filter(Boolean).join(', ')}`;
    return `<option value="${escHtml(s.id)}"${s.usable ? '' : ' disabled'}>${escHtml(label)}</option>`;
  }).join('');
}

async function _showHardwareDialog(data) {
  const overlay = document.createElement('div');
  overlay.className = 'overlay';

  const target = data.target;
  const isPick = data.action === 'pick_host';
  const isAdd  = data.action === 'add';

  let intro;
  if (isPick)      intro = t('hw.intro.pick', data.this_host);
  else if (isAdd)  intro = t('hw.intro.add', data.this_host);
  else             intro = t('hw.intro.replace', data.this_host, target ? target.path : '');

  // Only files that say something about this machine are listed. The hardware
  // config of another host is supposed to not match here, and showing it as a
  // problem would push the user toward "fixing" another machine's config.
  const shown = isPick ? (data.files || [])
                       : (data.files || []).filter(f => target && f.path === target.path);
  const rows = shown.map(f =>
    `<div style="margin:4px 0;overflow-wrap:anywhere">• ${escHtml(_hwVerdictLine(f))}</div>`).join('');

  // Nothing is preselected in either list (decision 2026-10-25): with a wrong
  // answer the damage is the whole installation, so the user picks both ends
  // of the operation deliberately. The placeholder stays as the selected entry
  // until they do.
  const placeholder = `<option value="" disabled selected>${escHtml(t('hw.choose'))}</option>`;
  const anySource   = (data.sources || []).some(s => s.usable);

  const sourceBlock = anySource
    ? `<div style="margin:10px 0">
         <label for="hw-source" style="display:block;margin-bottom:4px">${escHtml(t('hw.sourceLabel'))}</label>
         <select id="hw-source" style="max-width:100%">${placeholder}${_hwSourceOptions(data)}</select>
       </div>`
    : `<p style="margin-top:8px;color:var(--red)">${escHtml(t('hw.noSource'))}</p>`;

  overlay.innerHTML = `
    <div class="modal">
      <div class="modal-logo">${escHtml(t('hw.title'))}</div>
      <p>${escHtml(intro)}</p>
      <div class="gsi"><div class="gsi-block">
        <div class="gsi-block-body">${rows}</div>
      </div></div>
      <div style="margin:10px 0">
        <label for="hw-host" style="display:block;margin-bottom:4px">${escHtml(t('hw.pickLabel'))}</label>
        <select id="hw-host" style="max-width:100%">${placeholder}${_hwTargetOptions(data)}</select>
      </div>
      ${sourceBlock}
      <p style="margin-top:10px"><strong>${escHtml(t('hw.warning'))}</strong></p>
      <p id="hw-err" style="color:var(--red);display:none;margin-top:8px"></p>
      <div style="display:flex;gap:10px;justify-content:flex-end;margin-top:14px;flex-wrap:wrap">
        <button type="button" id="hw-confirm" class="btn-surface" disabled>${escHtml(t('hw.keepFile'))}</button>
        <button type="button" id="hw-replace" class="btn-primary" disabled>${escHtml(t('hw.useLocal'))}</button>
      </div>
    </div>`;
  document.body.appendChild(overlay);

  return new Promise(resolve => {
    const err     = overlay.querySelector('#hw-err');
    const hostSel = overlay.querySelector('#hw-host');
    const srcSel  = overlay.querySelector('#hw-source');
    const btnOk   = overlay.querySelector('#hw-confirm');
    const btnRep  = overlay.querySelector('#hw-replace');

    // Confirming only needs a place, replacing needs a place and a file.
    const sync = () => {
      const hasTarget = hostSel.selectedIndex > 0;
      const chosen    = hasTarget ? (data.targets || [])[hostSel.selectedIndex - 1] : null;
      btnOk.disabled  = !hasTarget || !chosen || !chosen.exists;
      btnRep.disabled = !hasTarget || !srcSel || srcSel.selectedIndex <= 0;
    };
    hostSel.addEventListener('change', sync);
    srcSel?.addEventListener('change', sync);
    sync();

    const send = async (action, btn) => {
      btn.disabled = true;
      err.style.display = 'none';
      try {
        const res = await csrfFetch('/api/hardware/apply', {
          method:  'POST',
          headers: { 'Content-Type': 'application/json' },
          body:    JSON.stringify({
            action,
            host:   hostSel.value || null,
            source: action === 'replace' ? (srcSel?.value || null) : null,
          }),
        });
        const out = await res.json();
        if (!res.ok || out.error) throw new Error(tErr(out.error));
        overlay.remove();
        resolve();
        // Replacing one host's file can leave another case open – ask again
        // rather than letting a second problem through on the same run.
        if (out.hardware && out.hardware.blocking) await _showHardwareDialog(out.hardware);
      } catch (e) {
        err.textContent = String(e.message || e);
        err.style.display = 'block';
        sync();
      }
    };

    btnOk.addEventListener('click', ev => send('confirm', ev.currentTarget));
    btnRep.addEventListener('click', ev => send('replace', ev.currentTarget));
  });
}
