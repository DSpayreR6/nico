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

/**
 * Short verdict label.
 *
 * Deliberately weaker than "belongs to this machine": the check answers
 * whether the devices a file names exist here, not whether it is the right
 * file. A hardware config describing a USB stick passes and is still wrong.
 */
function _hwVerdictTag(verdict) {
  if (verdict === 'match')    return t('hw.vMatch');
  if (verdict === 'mismatch') return t('hw.vMismatch');
  if (verdict === 'unknown')  return t('hw.vUnknown');
  return '';
}

/**
 * The files of the config, grouped by verdict – path only.
 *
 * The device identifiers behind a verdict used to stand in this list, three
 * UUIDs per line. They are evidence, not information: the decision does not
 * depend on *which* identifier is missing, and the list was unreadable. They
 * moved behind the details toggle.
 */
function _hwFileGroups(data) {
  const groups = [
    ['match',    'hw.gMatch',    'var(--green)'],
    ['mismatch', 'hw.gMismatch', 'var(--red)'],
    ['unknown',  'hw.gUnknown',  'var(--fg-dim)'],
  ];
  return groups.map(([verdict, key, colour]) => {
    const hits = (data.files || []).filter(f => f.verdict === verdict);
    const body = hits.length
      ? hits.map(f => `<div style="margin-left:14px;overflow-wrap:anywhere">${escHtml(f.path)}</div>`).join('')
      : '<div style="margin-left:14px;opacity:0.6">–</div>';
    return `<div style="margin:6px 0">
              <div style="color:${colour};font-weight:600">${escHtml(t(key))}</div>
              ${body}
            </div>`;
  }).join('');
}

/** The device identifiers behind the verdicts – hidden until asked for. */
function _hwDetails(data) {
  const lines = (data.files || []).map(f =>
    `<div style="margin:3px 0;overflow-wrap:anywhere">• ${escHtml(_hwVerdictLine(f))}</div>`).join('');
  if (!lines) return '';
  return `<details style="margin-top:6px">
            <summary style="cursor:pointer;opacity:0.8">${escHtml(t('hw.details'))}</summary>
            <div style="margin-top:6px;font-size:0.9em">${lines}</div>
          </details>`;
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
    const suffix = bits.length ? `  —  ${bits.join(', ')}` : '';
    // The host travels back, not the path: the route rebuilds the path from
    // hosts_dir itself and refuses a host it does not know.
    return `<option value="${escHtml(e.host || '')}">${escHtml(e.path + suffix)}</option>`;
  }).join('');
}

/**
 * What to write at the chosen place.
 *
 * One list, one action. "Keep the file that is there" used to be a second
 * button that ignored this list entirely – two buttons answering different
 * questions, which is what made the dialog unreadable. It is an entry here
 * now, available whenever a file actually sits at the chosen place.
 *
 * The files parked before the import come first – they are the ones it just
 * replaced. A confirmed mismatch is listed but not selectable: naming another
 * machine's disks would look like a repair and leave the config unbootable.
 */
const HW_KEEP = '__keep__';

function _hwSourceOptions(data) {
  const keep = `<option value="${HW_KEEP}">${escHtml(t('hw.keepFile'))}</option>`;
  const rest = (data.sources || []).map(s => {
    const bits = [_hwVerdictTag(s.verdict)];
    if (s.kind === 'stash') bits.unshift(t('hw.srcStash'));
    if (s.host)             bits.unshift(s.host);
    const label = `${s.label}  —  ${bits.filter(Boolean).join(', ')}`;
    return `<option value="${escHtml(s.id)}"${s.usable ? '' : ' disabled'}>${escHtml(label)}</option>`;
  }).join('');
  return keep + rest;
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

  // Nothing is preselected (decision 2026-10-25): with a wrong answer the
  // damage is the whole installation, so the choice is made deliberately. The
  // placeholder stays as the selected entry until it is.
  const placeholder = `<option value="" disabled selected>${escHtml(t('hw.choose'))}</option>`;
  const targets     = data.targets || [];

  // With a single possible place there is nothing to choose: the line above
  // already names the machine, and a dropdown holding one entry is noise.
  const targetBlock = targets.length > 1
    ? `<div style="margin:10px 0">
         <label for="hw-host" style="display:block;margin-bottom:4px">${escHtml(t('hw.pickLabel'))}</label>
         <select id="hw-host" style="max-width:100%">${placeholder}${_hwTargetOptions(data)}</select>
       </div>`
    : '';

  // A place with no file and no usable source is the one dead end: there is
  // nothing to select, so the dialog says what creates a hardware config and
  // lets go instead of trapping the user behind a button that cannot work.
  const anySource  = (data.sources || []).some(s => s.usable);
  const anyFile    = targets.some(e => e.exists);
  const deadEnd    = !anySource && !anyFile;

  const body = deadEnd
    ? `<p style="margin-top:10px;color:var(--red)">${escHtml(t('hw.noSource'))}</p>
       <div style="display:flex;justify-content:flex-end;margin-top:14px">
         <button type="button" id="hw-close" class="btn-primary">${escHtml(t('hw.close'))}</button>
       </div>`
    : `${targetBlock}
       <div style="margin:10px 0">
         <label for="hw-source" style="display:block;margin-bottom:4px">${escHtml(t('hw.sourceLabel'))}</label>
         <select id="hw-source" style="max-width:100%">${placeholder}${_hwSourceOptions(data)}</select>
       </div>
       <p id="hw-err" style="color:var(--red);display:none;margin-top:8px"></p>
       <div style="display:flex;gap:10px;justify-content:flex-end;margin-top:14px;flex-wrap:wrap">
         <button type="button" id="hw-apply" class="btn-primary" disabled>${escHtml(t('hw.useLocal'))}</button>
       </div>`;

  overlay.innerHTML = `
    <div class="modal">
      <div class="modal-logo">${escHtml(t('hw.title'))}</div>
      <p>${escHtml(intro)}</p>
      <div class="gsi"><div class="gsi-block">
        <div class="gsi-block-body">${_hwFileGroups(data)}${_hwDetails(data)}</div>
      </div></div>
      <p style="margin-top:10px"><strong>${escHtml(t('hw.warning'))}</strong></p>
      ${body}
    </div>`;
  document.body.appendChild(overlay);

  return new Promise(resolve => {
    if (deadEnd) {
      overlay.querySelector('#hw-close').addEventListener('click', () => {
        overlay.remove();
        resolve();
      });
      return;
    }

    const err     = overlay.querySelector('#hw-err');
    const hostSel = overlay.querySelector('#hw-host');   // absent when single
    const srcSel  = overlay.querySelector('#hw-source');
    const btn     = overlay.querySelector('#hw-apply');

    const chosenTarget = () => {
      if (!hostSel) return targets[0] || null;
      return hostSel.selectedIndex > 0 ? targets[hostSel.selectedIndex - 1] : null;
    };

    // "Keep the file that is there" only means something where a file is.
    const sync = () => {
      const tgt  = chosenTarget();
      const keep = srcSel.querySelector(`option[value="${HW_KEEP}"]`);
      if (keep) {
        keep.disabled = !tgt || !tgt.exists;
        if (keep.disabled && srcSel.value === HW_KEEP) srcSel.selectedIndex = 0;
      }
      btn.disabled = !tgt || srcSel.selectedIndex <= 0;
    };
    hostSel?.addEventListener('change', sync);
    srcSel.addEventListener('change', sync);
    sync();

    btn.addEventListener('click', async ev => {
      const el = ev.currentTarget;
      el.disabled = true;
      err.style.display = 'none';
      const tgt    = chosenTarget();
      const keep   = srcSel.value === HW_KEEP;
      try {
        const res = await csrfFetch('/api/hardware/apply', {
          method:  'POST',
          headers: { 'Content-Type': 'application/json' },
          body:    JSON.stringify({
            action: keep ? 'confirm' : 'replace',
            host:   tgt ? (tgt.host || null) : null,
            source: keep ? null : srcSel.value,
          }),
        });
        const out = await res.json();
        if (!res.ok || out.error) throw new Error(tErr(out.error));
        overlay.remove();
        resolve();
        // Settling one host can leave another case open – ask again rather
        // than letting a second problem through on the same run.
        if (out.hardware && out.hardware.blocking) await _showHardwareDialog(out.hardware);
      } catch (e) {
        err.textContent = String(e.message || e);
        err.style.display = 'block';
        sync();
      }
    });
  });
}
