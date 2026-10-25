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

  const hostOptions = (data.hosts || []).map(h =>
    `<option value="${escHtml(h)}">${escHtml(h)}</option>`).join('');

  const picker = isPick || isAdd
    ? `<div style="margin:10px 0">
         <label for="hw-host" style="display:block;margin-bottom:4px">${escHtml(t('hw.pickLabel'))}</label>
         <select id="hw-host">${hostOptions}</select>
       </div>`
    : '';

  // No source means nothing can be put in place: the only honest offer left is
  // to say so and name the command that creates one.
  const canReplace = data.source_usable;
  const sourceNote = canReplace
    ? `<p style="margin-top:8px">${escHtml(t('hw.sourceOk', data.source_path))}</p>`
    : `<p style="margin-top:8px;color:var(--red)">${escHtml(
         data.source_verdict === 'mismatch' ? t('hw.sourceMismatch', data.source_path)
                                            : t('hw.sourceMissing'))}</p>`;

  overlay.innerHTML = `
    <div class="modal">
      <div class="modal-logo">${escHtml(t('hw.title'))}</div>
      <p>${escHtml(intro)}</p>
      <div class="gsi"><div class="gsi-block">
        <div class="gsi-block-body">${rows}</div>
      </div></div>
      ${picker}
      <p style="margin-top:10px"><strong>${escHtml(t('hw.warning'))}</strong></p>
      ${sourceNote}
      <p id="hw-err" style="color:var(--red);display:none;margin-top:8px"></p>
      <div style="display:flex;gap:10px;justify-content:flex-end;margin-top:14px;flex-wrap:wrap">
        <button type="button" id="hw-confirm" class="btn-surface">${escHtml(t('hw.keepFile'))}</button>
        <button type="button" id="hw-replace" class="btn-primary"${canReplace ? '' : ' disabled'}>${escHtml(t('hw.useLocal'))}</button>
      </div>
    </div>`;
  document.body.appendChild(overlay);

  return new Promise(resolve => {
    const err = overlay.querySelector('#hw-err');
    const chosenHost = () => {
      const sel = overlay.querySelector('#hw-host');
      if (sel) return sel.value || null;
      return target ? target.host : null;
    };

    const send = async (action, btn) => {
      btn.disabled = true;
      err.style.display = 'none';
      try {
        const res = await csrfFetch('/api/hardware/apply', {
          method:  'POST',
          headers: { 'Content-Type': 'application/json' },
          body:    JSON.stringify({ action, host: chosenHost() }),
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
        btn.disabled = false;
      }
    };

    overlay.querySelector('#hw-confirm')
      .addEventListener('click', ev => send('confirm', ev.currentTarget));
    overlay.querySelector('#hw-replace')
      .addEventListener('click', ev => send('replace', ev.currentTarget));
  });
}
