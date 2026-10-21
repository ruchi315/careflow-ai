// CareFlow staff dashboard. Plain JS, no build step.
// Security note: every value that originates from patients/reports is escaped with esc() before insertion.
const $ = id => document.getElementById(id);
const S = { me: null, view: 'queue', caseCode: null, caseData: null, timers: [], alertsSince: null, seenAlerts: new Set() };
const NAV = [
  ['queue', 'Queue', 'view_queue'], ['new', 'New case', 'create_case'], ['analytics', 'Analytics', 'view_analytics'],
  ['audit', 'Audit log', 'view_audit'], ['consent', 'Consent & data', 'manage_consent'],
];
const can = p => S.me && S.me.permissions.includes(p);
const LV = ['P1', 'P2', 'P3', 'P4', 'P5'];
const LVN = { P1: 'Immediate', P2: 'Emergent', P3: 'Urgent', P4: 'Less urgent', P5: 'Non-urgent' };

function clearTimers() { S.timers.forEach(clearInterval); S.timers = []; }

// ------------------------------------------------------------------------------------------- login
function renderLogin() {
  $('nav').innerHTML = ''; $('who').textContent = '';
  $('view').innerHTML = `
    <div class="panel" style="max-width:460px;margin:40px auto">
      <h2>Staff sign in</h2>
      <label>Username</label><input id="lg-user" autocomplete="username">
      <label>Password</label><input id="lg-pass" type="password" autocomplete="current-password">
      <div class="row" style="margin-top:12px"><button class="primary" id="lg-go">Sign in</button><span id="lg-err" class="small" style="color:var(--bad)"></span></div>
      <h4 style="margin-top:20px">Demo accounts (password: demo)</h4>
      <div class="row">
        ${['dr.mehta', 'nurse.asha', 'intake.ravi', 'admin.dpo'].map(u => `<button class="small" data-u="${u}">${u}</button>`).join('')}
      </div>
    </div>`;
  const go = async () => {
    try {
      const r = await CF.api('/api/auth/login', { method: 'POST', json: { username: $('lg-user').value.trim(), password: $('lg-pass').value } });
      CF.setSession(r.token, r.user); await boot();
    } catch (e) { $('lg-err').textContent = e.message; }
  };
  $('lg-go').onclick = go; $('lg-pass').onkeydown = e => { if (e.key === 'Enter') go(); };
  document.querySelectorAll('[data-u]').forEach(b => b.onclick = () => { $('lg-user').value = b.dataset.u; $('lg-pass').value = 'demo'; go(); });
}

async function boot() {
  if (!CF.token()) return renderLogin();
  try { S.me = await CF.api('/api/me'); } catch { CF.clear(); return renderLogin(); }
  $('who').innerHTML = `${esc(S.me.name)} &middot; <b>${esc(S.me.role)}</b> &nbsp; <button class="small" id="logout">Sign out</button>`;
  $('logout').onclick = () => { CF.clear(); location.reload(); };
  const items = NAV.filter(n => can(n[2]));
  $('nav').innerHTML = items.map(n => `<button data-v="${n[0]}">${n[1]}</button>`).join('');
  $('nav').querySelectorAll('button').forEach(b => b.onclick = () => go(b.dataset.v));
  if (can('view_clinical')) { S.alertsSince = null; pollAlerts(); setInterval(pollAlerts, 6000); }
  go(items.length ? items[0][0] : 'none');
}

function go(view, arg) {
  clearTimers(); S.view = view;
  $('nav').querySelectorAll('button').forEach(b => b.classList.toggle('active', b.dataset.v === view || (view === 'case' && b.dataset.v === 'queue')));
  ({ queue: renderQueue, case: () => renderCase(arg), new: renderNewCase, audit: renderAudit, consent: renderConsent, analytics: renderAnalytics }[view] || (() => {
    $('view').innerHTML = '<div class="panel">No views for this role.</div>';
  }))();
}

// ------------------------------------------------------------------------------------------- alerts
let audioCtx;
function beep() {
  try {
    audioCtx = audioCtx || new AudioContext();
    const o = audioCtx.createOscillator(), g = audioCtx.createGain(); o.frequency.value = 880; o.connect(g); g.connect(audioCtx.destination);
    g.gain.value = 0.08; o.start(); setTimeout(() => o.stop(), 250);
  } catch { /* audio blocked until user interaction */ }
}
async function pollAlerts() {
  try {
    const all = (await CF.api('/api/alerts')).alerts;
    const fresh = all.filter(a => !S.seenAlerts.has(a.code));
    if (S.alertsSince !== null && fresh.length) beep();
    all.forEach(a => S.seenAlerts.add(a.code));
    S.alertsSince = '';
    const shown = all.slice(0, 5);
    $('alert-slot').innerHTML = all.length ? `<div class="alert-banner"><span>&#9888; ${all.length} urgent case(s) awaiting review</span>
      ${shown.map(a => `<button class="small" data-open="${esc(a.code)}">${esc(a.priority)} ${esc(a.code)} ${esc(a.name || '')}</button>`).join(' ')}
      ${all.length > shown.length ? `<span>+${all.length - shown.length} more in the queue</span>` : ''}</div>` : '';
    $('alert-slot').querySelectorAll('[data-open]').forEach(b => b.onclick = () => go('case', b.dataset.open));
  } catch { /* ignore */ }
}

// ------------------------------------------------------------------------------------------- queue
async function renderQueue() {
  $('view').innerHTML = `<div class="panel"><div class="row"><h2 class="grow">Review queue</h2>
    <span class="small muted">Sorted by priority, then waiting time. Long waits move up (ageing) without changing the clinical priority.</span></div>
    <div id="q-table">Loading...</div></div>
    <p class="disclaimer">AI output is decision support only. Priorities are suggestions until a clinician approves them.</p>`;
  const load = async () => {
    const { cases } = await CF.api('/api/queue');
    const clinical = can('view_clinical');
    $('q-table').innerHTML = cases.length ? `<table><thead><tr><th>Priority</th><th>Case</th><th>Patient</th><th>Waiting</th><th>Status</th>
      ${clinical ? '<th>Summary</th><th>Red flags</th><th>Flags</th>' : ''}</tr></thead><tbody>
      ${cases.map(c => `<tr class="${clinical ? 'clickable' : ''} ${c.status === 'approved' ? 'approved' : ''}" data-code="${esc(c.code)}">
        <td>${pill(c.priority)}${c.aged ? ' <span class="chip warn" title="moved up because of waiting time">aged</span>' : ''}
          ${clinical && c.ai_priority && c.ai_priority !== c.priority ? `<div class="small muted">AI: ${esc(c.ai_priority)}</div>` : ''}</td>
        <td><b>${esc(c.code)}</b><div class="small muted">${esc(CF.langs[c.language] || c.language || '')}</div></td>
        <td>${esc(c.name || '-')}<div class="small muted">${esc(c.age ?? '?')} ${esc(c.sex || '')}</div></td>
        <td>${fmtWait(c.wait_min)}</td>
        <td>${esc(c.status.replace('_', ' '))}${c.failsafe ? ' <span class="chip bad">pipeline error</span>' : ''}</td>
        ${clinical ? `<td class="small">${esc(c.summary || (c.status === 'processing' ? 'processing...' : ''))}</td>
          <td class="small">${(c.red_flags || []).map(r => `<span class="chip bad">${esc(r)}</span>`).join('')}</td>
          <td>${Object.entries(c.flag_counts || {}).filter(([t]) => t !== 'missing').map(([t, n]) => `<span class="chip warn">${esc(t)} ${n}</span>`).join('')}
            ${c.flag_counts && c.flag_counts.missing ? `<span class="chip">missing ${c.flag_counts.missing}</span>` : ''}</td>` : ''}
      </tr>`).join('')}</tbody></table>` : '<p class="muted">No cases waiting.</p>';
    if (clinical) $('q-table').querySelectorAll('tr[data-code]').forEach(tr => tr.onclick = () => go('case', tr.dataset.code));
  };
  await load(); S.timers.push(setInterval(load, 8000));
}

// ------------------------------------------------------------------------------------------- case view
function evChip(ev, label) {
  const i = S.evidence.push(ev) - 1;
  return `<span class="chip link" data-ev="${i}" title="${esc(ev.quote)}">${esc(label || ev.quote || 'source').slice(0, 60)}</span>`;
}
function confTag(c, thr = 0.6) {
  if (c === null || c === undefined) return '';
  return `<span class="conf ${c < thr ? 'low' : ''}">conf ${(c * 100).toFixed(0)}%</span>`;
}
function durText(h) {
  if (h === null || h === undefined) return '<span class="muted">not stated</span>';
  if (h < 1) return `${Math.round(h * 60)} min`; if (h < 48) return `${+h.toFixed(1)} h`;
  if (h < 24 * 14) return `${+(h / 24).toFixed(1)} days`; if (h < 24 * 60) return `${+(h / 168).toFixed(1)} weeks`;
  return `${+(h / 720).toFixed(1)} months`;
}

async function renderCase(code) {
  S.caseCode = code;
  let c;
  try { c = await CF.api('/api/cases/' + encodeURIComponent(code)); } catch (e) { $('view').innerHTML = `<div class="panel">${esc(e.message)}</div>`; return; }
  S.caseData = c; S.evidence = [];
  const a = c.analysis || {};
  if (c.status === 'processing' || !c.analysis) {
    $('view').innerHTML = `<div class="panel"><button class="small" id="back">&larr; Queue</button> <h2>${esc(code)}</h2>
      <p>Processing inputs (speech recognition / OCR can take a few seconds)...</p></div>`;
    $('back').onclick = () => go('queue'); S.timers.push(setTimeout(() => S.view === 'case' && renderCase(code), 2500)); return;
  }
  if (a.error) {
    $('view').innerHTML = `<div class="panel"><button class="small" id="back">&larr; Queue</button><h2>${esc(code)}</h2>
      <div class="flag high">The pipeline failed (${esc(a.error)}). The case was routed to manual review at ${pill(c.priority)}. Review the raw inputs below.</div>
      ${c.sources.map(s => `<div class="evidence-text">${esc(s.type)}: ${esc(s.content || s.filename || '')}</div>`).join('')}</div>`;
    $('back').onclick = () => go('queue'); return;
  }
  const f = a.facts, p = a.priority, P = c.patient;
  const staleBanner = a.pipeline_error ? `<div class="flag high">The last AI re-run failed (${esc(a.pipeline_error)}). Showing the previous analysis - newer inputs are NOT reflected. Review the inputs manually.</div>` : '';
  S.segById = Object.fromEntries((a.segments || []).map(s => [s.id, s]));
  const lastNote = c.notes.length ? c.notes[c.notes.length - 1] : null;
  const sym = Object.values(f.symptoms), hist = Object.values(f.history);
  $('view').innerHTML = `
  <div class="row" style="margin-bottom:10px"><button class="small" id="back">&larr; Queue</button>
    <h2 class="grow" style="margin:0">${esc(c.code)} &middot; ${esc(P.name || 'Name not given')} <span class="muted">(${esc(P.age ?? '?')} ${esc(P.sex || '')})</span></h2>
    <span class="chip">${esc(CF.langs[c.language] || c.language || '')}</span><span class="chip">${esc(c.status.replace('_', ' '))}</span>
    <span class="chip">waiting ${fmtWait(c.wait_min)}</span><button class="small" id="reprocess">Re-run AI</button></div>
  ${staleBanner}
  <div class="case-grid">
   <div>
    <div class="panel">
      <div class="row"><div class="grow"><h4>Suggested review priority</h4>
        <div class="row">${pill(c.priority)} <b>${esc(LVN[c.priority])}</b>
          ${c.ai_priority !== c.priority ? `<span class="chip warn">AI suggested ${esc(c.ai_priority)} &middot; set by ${esc(c.priority_source)}</span>` : `<span class="chip">source: ${esc(c.priority_source)}</span>`}</div>
        <p class="small" style="margin:6px 0 0">${esc(p.explanation)}</p></div>
      ${can('raise_priority') ? `<div style="width:270px"><label class="small">Change priority</label><div class="row">
        <select id="pr-level" style="width:90px">${LV.map(l => `<option ${l === c.priority ? 'selected' : ''}>${l}</option>`).join('')}</select>
        <input id="pr-reason" class="grow" placeholder="reason (required)"></div><button class="small" id="pr-go" style="margin-top:6px">Apply</button>
        <div class="small muted">${can('lower_priority') ? 'Lowering requires a reason.' : 'Nurses can raise only; a doctor can lower.'}</div></div>` : ''}
      </div>
      <p style="margin:12px 0 4px"><b>Summary:</b> ${esc(a.summary)}</p>
      ${a.summary_slm ? `<p class="small muted">SLM summary: ${esc(a.summary_slm)}</p>` : ''}
    </div>

    <div class="panel"><h3>Possible urgency signals</h3>
      ${p.red_flags.length ? p.red_flags.map(r => `<div class="rf"><div class="row">${pill(r.priority)} <b class="grow">${esc(r.title)}</b>
        <span class="small muted">${esc(r.rule_id)}${r.via_slm ? ' &middot; via SLM' : ''}</span></div>
        <div class="small muted">Rule reference: ${esc(r.reference)}</div><div>Evidence: ${r.evidence.map(e => evChip(e)).join('')}</div></div>`).join('')
        : '<p class="muted">No red-flag rule matched. Base level applied.</p>'}
      <p class="small muted">Rulebook ${esc(p.rulebook_version)}. Signals are prompts for clinical assessment, not diagnoses.</p>
    </div>

    <div class="panel"><h3>Flags: missing, unclear or conflicting information</h3>
      ${a.flags.filter(x => x.type !== 'missing').map(x => flagHtml(x)).join('') || '<p class="muted">No conflicts or quality problems detected.</p>'}
      ${a.flags.some(x => x.type === 'missing') ? `<h4 style="margin-top:12px">Missing information</h4>${a.flags.filter(x => x.type === 'missing').map(x => flagHtml(x)).join('')}` : ''}
    </div>

    <div class="panel"><h3>Structured summary</h3>
      <h4>Symptoms</h4>
      ${sym.length ? `<table><thead><tr><th>Finding</th><th>Status</th><th>Duration</th><th>Severity / trend</th><th>Sources</th></tr></thead><tbody>
        ${sym.map(s => `<tr><td><b>${esc(s.label)}</b> ${s.origins.includes('slm') ? '<span class="chip">SLM</span>' : ''}</td>
          <td>${s.present ? '<span class="chip ok">reported</span>' : '<span class="chip">denied</span>'} ${confTag(s.confidence)}</td>
          <td>${durText(s.duration_hours)}</td><td>${s.severity ? esc(s.severity.value) + '/10 ' : ''}${s.trend ? esc(s.trend.value) : ''}</td>
          <td>${s.mentions.map(m => evChip(m.evidence, (m.negated ? 'denied: ' : '') + m.evidence.quote)).join('')}</td></tr>`).join('')}</tbody></table>` : '<p class="muted">None extracted.</p>'}
      ${hist.length ? `<h4 style="margin-top:12px">History</h4>${hist.map(h => `<div>${h.present ? '' : '<span class="chip">denied</span>'} <b>${esc(h.label)}</b> ${h.mentions.map(m => evChip(m.evidence)).join('')}</div>`).join('')}` : ''}
      <h4 style="margin-top:12px">Vitals</h4>
      ${Object.keys(f.vitals).length ? Object.entries(f.vitals).map(([k, v]) => `<span class="chip">${esc(k)} <b>${esc(v.value)}</b></span>${evChip(v.evidence, 'source')}`).join(' ') : '<span class="muted">None recorded.</span>'}
      <h4 style="margin-top:12px">Medications / allergies</h4>
      <div>${f.medications.map(m => evChip(m.evidence, 'med: ' + m.name)).join('') || '<span class="muted">Medications not stated.</span>'}
        ${f.allergies.map(m => evChip(m.evidence, 'allergy: ' + m.name)).join('') || (f.allergies_known ? '<span class="chip">no known allergies</span>' : '<span class="muted"> Allergies not stated.</span>')}</div>
      <h4 style="margin-top:12px">Report values</h4>
      ${f.labs.length ? `<table><thead><tr><th>Test</th><th>Value</th><th>Reference</th><th>Status</th><th>Source</th></tr></thead><tbody>
        ${f.labs.map(l => `<tr><td>${esc(l.label || l.test)}</td><td><b>${esc(l.value)}</b> ${esc(l.unit || '')}</td>
        <td class="small">${l.ref_low ?? ''}${l.ref_low != null || l.ref_high != null ? ' - ' : ''}${l.ref_high ?? ''} ${l.range_source === 'default' ? '<span class="muted">(default)</span>' : ''}</td>
        <td><span class="chip ${/critical/.test(l.status) ? 'bad' : (l.status && l.status !== 'normal' ? 'warn' : 'ok')}">${esc(l.status || 'from text')}</span> ${confTag(l.confidence, 0.7)}</td>
        <td>${evChip(l.evidence, 'view')}</td></tr>`).join('')}</tbody></table>` : '<span class="muted">No report values.</span>'}
    </div>

    <div class="panel"><h3>Symptom timeline</h3><div class="timeline">
      ${a.timeline.map(t => `<div class="tl"><b>${esc(t.label)}</b> &middot; ${t.onset ? 'since ' + esc(t.onset.replace('T', ' ')) + ' (' + durText(t.duration_hours) + ')' : '<span class="muted">onset not stated</span>'}
        ${t.trend ? `<span class="chip">${esc(t.trend)}</span>` : ''} ${t.evidence.map(e => evChip(e)).join('')}</div>`).join('') || '<p class="muted">No symptoms.</p>'}
    </div></div>

    <div class="panel"><h3>Suggested follow-up questions</h3>
      ${a.questions.map((q, i) => `<div class="flag low"><div class="row"><span class="chip">${esc(q.audience)}</span><b class="grow">${esc(q.text_en)}</b></div>
        ${q.lang !== 'en' ? `<div>${esc(q.text_local)} <span class="small muted">(${esc(CF.langs[q.lang] || q.lang)})</span></div>` : ''}
        <div class="small muted">Why: ${esc(q.reason)}</div>
        ${can('record_answers') && q.audience === 'patient' ? `<div class="row" style="margin-top:6px"><input id="qa-${i}" class="grow" placeholder="record the patient's answer">
          <button class="small" data-qa="${i}">Save answer &amp; re-run</button></div>` : ''}</div>`).join('') || '<p class="muted">No questions.</p>'}
    </div>

    <div class="panel"><div class="row"><h3 class="grow">Triage / referral note</h3>
      ${lastNote ? `<span class="chip ${lastNote.status === 'approved' ? 'ok' : ''}">v${lastNote.version} ${esc(lastNote.status)} by ${esc(lastNote.author)}</span>` : '<span class="chip warn">AI draft - not reviewed</span>'}</div>
      <div class="tabs"><button class="active" data-nt="edit">Edit</button><button data-nt="diff">Changes vs AI draft</button><button data-nt="hist">History</button></div>
      <textarea id="note" class="note-editor"></textarea><div id="note-diff" class="evidence-text hidden"></div><div id="note-hist" class="hidden"></div>
      <div class="row" style="margin-top:8px">
        ${can('edit_note') ? '<button id="note-save">Save draft</button>' : ''}
        ${can('approve_note') ? `<span class="grow"></span><label class="small" style="margin:0">Final priority</label>
          <select id="note-pr" style="width:80px">${LV.map(l => `<option ${l === c.priority ? 'selected' : ''}>${l}</option>`).join('')}</select>
          <input id="note-reason" style="width:220px" placeholder="override reason (if different from AI)">
          <button class="primary" id="note-approve">Approve &amp; sign</button>` : ''}
        ${c.status === 'approved' ? '<button id="fhir">Export FHIR</button>' : ''}
      </div>
    </div>
   </div>

   <div class="sticky">
    <div class="panel" id="ev-panel"><h3>Evidence viewer</h3><p class="muted small">Click any blue source chip to see exactly where a fact came from.</p><div id="ev"></div></div>
    <div class="panel"><h3>Inputs</h3>
      ${c.sources.map(s => `<div class="row" style="margin:4px 0"><span class="chip">${esc(s.type)}</span>
        <span class="grow small">${esc(s.filename || (s.content || '').slice(0, 80))} ${s.label ? `<span class="muted">(${esc(s.label)})</span>` : ''}</span>
        <button class="small" data-src="${s.id}">view</button></div>`).join('')}
      <p class="small muted">Engines: ASR ${esc(a.engines.asr || 'off')} &middot; OCR ${esc(a.engines.ocr || 'off')} &middot; SLM ${esc(a.engines.slm || 'off')} &middot;
        pipeline ${esc(a.pipeline_version)} &middot; ${esc(a.timings_ms.total)} ms</p>
    </div>
   </div>
  </div>`;
  $('back').onclick = () => go('queue');
  $('reprocess').onclick = async () => { await CF.api(`/api/cases/${code}/reprocess`, { method: 'POST' }); toast('Re-running'); renderCase(code); };
  document.querySelectorAll('[data-ev]').forEach(el => el.onclick = () => showEvidence(S.evidence[+el.dataset.ev]));
  document.querySelectorAll('[data-src]').forEach(el => el.onclick = () => showSource(+el.dataset.src));
  if ($('pr-go')) $('pr-go').onclick = async () => {
    try { await CF.api(`/api/cases/${code}/priority`, { method: 'POST', json: { level: $('pr-level').value, reason: $('pr-reason').value } }); toast('Priority updated'); renderCase(code); }
    catch (e) { alert(e.message); }
  };
  document.querySelectorAll('[data-flag]').forEach(b => b.onclick = async () => {
    const comment = b.dataset.st === 'open' ? '' : (prompt('Comment (optional)') ?? '');
    await CF.api(`/api/cases/${code}/flags/${encodeURIComponent(b.dataset.flag)}`, { method: 'POST', json: { status: b.dataset.st, comment } });
    renderCase(code);
  });
  document.querySelectorAll('[data-qa]').forEach(b => b.onclick = async () => {
    const q = a.questions[+b.dataset.qa], v = $('qa-' + b.dataset.qa).value.trim(); if (!v) return;
    await CF.api(`/api/cases/${code}/answers`, { method: 'POST', json: { question_id: q.id, question_text: q.text_local, answer: v } });
    toast('Answer recorded - re-running AI'); renderCase(code);
  });
  // note editor
  const draft = a.note_draft || '';
  $('note').value = lastNote ? lastNote.text : draft;
  document.querySelectorAll('[data-nt]').forEach(b => b.onclick = () => {
    document.querySelectorAll('[data-nt]').forEach(x => x.classList.toggle('active', x === b));
    $('note').classList.toggle('hidden', b.dataset.nt !== 'edit'); $('note-diff').classList.toggle('hidden', b.dataset.nt !== 'diff');
    $('note-hist').classList.toggle('hidden', b.dataset.nt !== 'hist');
    if (b.dataset.nt === 'diff') $('note-diff').innerHTML = lineDiff(draft, $('note').value);
    if (b.dataset.nt === 'hist') $('note-hist').innerHTML = c.notes.length ? c.notes.slice().reverse().map(n => `<div class="flag low"><b>v${n.version}</b> ${esc(n.status)} by ${esc(n.author)} (${esc(n.role)}) at ${esc(n.created_at)}
      ${n.priority_final ? ' &middot; final ' + pill(n.priority_final) : ''}${n.override_reason ? ' &middot; reason: ' + esc(n.override_reason) : ''}</div>`).join('') : '<p class="muted">No saved versions yet.</p>';
  });
  if ($('note-save')) $('note-save').onclick = async () => {
    try { await CF.api(`/api/cases/${code}/note`, { method: 'POST', json: { text: $('note').value } }); toast('Draft saved'); renderCase(code); } catch (e) { alert(e.message); }
  };
  if ($('note-approve')) $('note-approve').onclick = async () => {
    try {
      await CF.api(`/api/cases/${code}/note`, { method: 'POST', json: { text: $('note').value, approve: true, priority_final: $('note-pr').value, override_reason: $('note-reason').value || null } });
      toast('Note approved and signed'); renderCase(code);
    } catch (e) { alert(e.message); }
  };
  if ($('fhir')) $('fhir').onclick = async () => {
    const b = await CF.api(`/api/cases/${code}/fhir`);
    const url = URL.createObjectURL(new Blob([JSON.stringify(b, null, 2)], { type: 'application/json' }));
    const l = document.createElement('a'); l.href = url; l.download = code + '-fhir.json'; l.click();
  };
}

function flagHtml(x) {
  const btns = can('review_flags') ? (x.status === 'open'
    ? `<button class="small" data-flag="${esc(x.id)}" data-st="acknowledged">Acknowledge</button><button class="small" data-flag="${esc(x.id)}" data-st="resolved">Resolve</button>`
    : `<button class="small" data-flag="${esc(x.id)}" data-st="open">Re-open</button>`) : '';
  return `<div class="flag ${esc(x.severity)} ${esc(x.status)}"><div class="row"><span class="chip ${x.severity === 'high' ? 'bad' : 'warn'}">${esc(x.type.replace('_', ' '))}</span>
    <span class="grow">${esc(x.message)}</span>${btns}</div>
    ${x.evidence.length ? `<div>${x.evidence.map(e => evChip(e)).join('')}</div>` : ''}
    ${x.review ? `<div class="small muted">${esc(x.review.status)} by ${esc(x.review.reviewer)}${x.review.comment ? ': ' + esc(x.review.comment) : ''}</div>` : ''}</div>`;
}

function lineDiff(a, b) {
  const A = a.split('\n'), B = b.split('\n'), setA = new Set(A), setB = new Set(B);
  const out = [];
  B.forEach(l => out.push(setA.has(l) ? esc(l) : `<span class="diff-add">+ ${esc(l)}</span>`));
  A.forEach(l => { if (!setB.has(l)) out.push(`<span class="diff-del">- ${esc(l)}</span>`); });
  return out.join('\n') || '<span class="muted">No changes.</span>';
}

function fileUrl(sid) { return `/api/cases/${encodeURIComponent(S.caseCode)}/sources/${sid}/file?t=${encodeURIComponent(CF.token())}`; }
function highlight(text, s, e) {
  if (s === undefined || e === undefined || e <= s) return esc(text);
  return esc(text.slice(0, s)) + '<mark>' + esc(text.slice(s, e)) + '</mark>' + esc(text.slice(e));
}

function showEvidence(ev) {
  const seg = S.segById[ev.segment_id];
  const box = $('ev');
  if (!seg) { box.innerHTML = `<div class="evidence-text">${esc(ev.quote)}</div>`; return; }
  const src = S.caseData.sources.find(s => s.id === seg.source_id);
  const segs = Object.values(S.segById).filter(s => s.source_id === seg.source_id);
  let html = `<div class="small muted">${esc(seg.kind)} &middot; segment ${esc(seg.id)} ${src ? '&middot; ' + esc(src.filename || src.label || 'typed text') : ''} ${confTag(seg.conf, 0.6)}</div>`;
  if (seg.kind === 'text' || seg.kind === 'form') {
    html += `<div class="evidence-text">${highlight(seg.text, ev.start, ev.end)}</div>`;
    if (seg.gloss && seg.gloss.method !== 'identity') html += `<h4 style="margin-top:10px">English (${esc(seg.gloss.method)}) ${confTag(seg.gloss.confidence)}</h4><div class="evidence-text">${esc(seg.gloss.text_en)}</div>`;
  } else if (seg.kind === 'asr') {
    const deleted = !!src && (src.label || '').startsWith('recording deleted');
    html += deleted ? '<p class="small muted">Recording deleted after transcription (no consent to store voice).</p>'
      : `<audio id="ev-audio" controls src="${fileUrl(seg.source_id)}" style="width:100%"></audio>`;
    html += segs.map(s => `<div class="evidence-text" style="margin-top:6px">[${(s.start_ms / 1000).toFixed(1)}s] ${s.id === seg.id ? highlight(s.text, ev.start, ev.end) : esc(s.text)} ${confTag(s.conf, 0.55)}
      ${s.gloss && s.gloss.method !== 'identity' ? `<div class="small muted">EN: ${esc(s.gloss.text_en)} ${confTag(s.gloss.confidence)}</div>` : ''}</div>`).join('');
  } else if (seg.kind === 'ocr') {
    html += `<div class="ev-canvas-wrap"><img id="ev-img" src="${fileUrl(seg.source_id)}"><div id="ev-box" class="ev-box"></div></div>
      <div class="evidence-text" style="margin-top:6px">${esc(seg.text)}</div>`;
  } else if (seg.kind === 'pdf_text') {
    html += `<p class="small"><a href="${fileUrl(seg.source_id)}" target="_blank">Open original PDF</a></p>` +
      segs.map(s => `<div class="pdf-line ${s.id === seg.id ? 'hit' : ''} ${s.hidden ? 'hidden-line' : ''}" title="${s.hidden ? 'invisible on the printed page' : ''}">${esc(s.text)}</div>`).join('');
  }
  box.innerHTML = html;
  $('ev-panel').scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  if (seg.kind === 'asr' && $('ev-audio')) $('ev-audio').addEventListener('loadedmetadata', e => { e.target.currentTime = seg.start_ms / 1000; }, { once: true });
  if (seg.kind === 'ocr') {
    const img = $('ev-img');
    img.onload = () => {
      const k = img.clientWidth / img.naturalWidth, [x1, y1, x2, y2] = seg.bbox, b = $('ev-box');
      Object.assign(b.style, { left: x1 * k - 3 + 'px', top: y1 * k - 3 + 'px', width: (x2 - x1) * k + 6 + 'px', height: (y2 - y1) * k + 6 + 'px' });
      b.scrollIntoView({ block: 'center' });
    };
  }
}

function showSource(sid) {
  const src = S.caseData.sources.find(s => s.id === sid);
  const segs = Object.values(S.segById).filter(s => s.source_id === sid);
  if (segs.length) return showEvidence({ segment_id: segs[0].id, start: 0, end: 0, quote: '' });
  $('ev').innerHTML = src.type === 'report' ? `<p>No text could be read from this file.</p><img src="${fileUrl(sid)}" style="width:100%" onerror="this.remove()">`
    : `<div class="evidence-text">${esc(src.content || src.label || '')}</div>`;
}

// ------------------------------------------------------------------------------------------- new case (staff)
async function renderNewCase() {
  const c = await CF.api('/api/consent?lang=en');
  $('view').innerHTML = `<div class="panel" style="max-width:820px"><h2>New case (staff-assisted intake)</h2>
    <div class="row"><div class="grow"><label>Name</label><input id="n-name"></div><div style="width:110px"><label>Age</label><input id="n-age" type="number" step="0.1"></div>
      <div style="width:110px"><label>Sex</label><select id="n-sex"><option></option><option>F</option><option>M</option><option value="O">Other</option></select></div>
      <div style="width:170px"><label>Language</label><select id="n-lang">${Object.entries(CF.langs).map(([k, v]) => `<option value="${k}">${v}</option>`).join('')}</select></div></div>
    <h4 style="margin-top:12px">Vitals (optional)</h4>
    <div class="row">${[['spo2', 'SpO2 %'], ['hr', 'HR /min'], ['rr', 'RR /min'], ['sbp', 'SBP'], ['dbp', 'DBP'], ['temp_c', 'Temp C']].map(([k, l]) =>
      `<div style="width:105px"><label class="small">${l}</label><input id="v-${k}" type="number" step="0.1"></div>`).join('')}</div>
    <label>Complaint (patient's words, any language)</label><textarea id="n-text"></textarea>
    <label>Voice notes / reports</label><input id="n-files" type="file" multiple accept=".pdf,image/*,audio/*">
    <h4 style="margin-top:12px">Consent (read to the patient, recorded verbally)</h4>
    ${Object.entries(c.scopes).map(([k, s]) => `<label class="check"><input type="checkbox" data-scope="${k}" ${k === 'triage' ? '' : ''}><span>${esc(s.text)}${s.required ? ' <b>*</b>' : ''}</span></label>`).join('')}
    <div class="row"><button class="primary" id="n-go">Create case</button><span id="n-res"></span></div></div>`;
  $('n-go').onclick = async () => {
    const consent = { version: c.version, method: 'verbal, recorded by staff' };
    document.querySelectorAll('[data-scope]').forEach(cb => consent[cb.dataset.scope] = cb.checked);
    if (!consent.triage) { alert('Triage consent is required.'); return; }
    const vit = {}; ['spo2', 'hr', 'rr', 'sbp', 'dbp', 'temp_c'].forEach(k => { const v = $('v-' + k).value; if (v !== '') vit[k] = v; });
    const fd = new FormData();
    fd.append('patient', JSON.stringify({ name: $('n-name').value, age: $('n-age').value, sex: $('n-sex').value }));
    fd.append('consent', JSON.stringify(consent)); fd.append('language', $('n-lang').value); fd.append('vitals', JSON.stringify(vit));
    if ($('n-text').value.trim()) fd.append('text', $('n-text').value.trim());
    for (const f of $('n-files').files) fd.append('files', f);
    try {
      const r = await CF.api('/api/intake/staff', { method: 'POST', body: fd });
      $('n-res').innerHTML = `Created <b>${esc(r.code)}</b> &middot; patient access code <b>${esc(r.access_code)}</b> (give this to the patient)`;
    } catch (e) { $('n-res').textContent = e.message; }
  };
}

// ------------------------------------------------------------------------------------------- governance
async function renderAudit() {
  $('view').innerHTML = `<div class="panel"><div class="row"><h2 class="grow">Audit log</h2><input id="au-case" placeholder="filter by case code" style="width:220px"><button id="au-go">Filter</button></div>
    <div id="au-verify"></div><div id="au-table">Loading...</div></div>`;
  const load = async () => {
    const q = $('au-case').value.trim();
    const r = await CF.api('/api/audit' + (q ? '?case=' + encodeURIComponent(q) : ''));
    $('au-verify').innerHTML = r.verify.ok ? `<p class="chip ok">Hash chain intact &middot; ${r.verify.entries} entries &middot; head ${esc(r.verify.head.slice(0, 16))}...</p>`
      : `<p class="chip bad">Hash chain BROKEN at entry ${esc(r.verify.broken_at)}</p>`;
    $('au-table').innerHTML = `<table><thead><tr><th>Time</th><th>Actor</th><th>Role</th><th>Action</th><th>Case</th><th>Detail</th><th>Hash</th></tr></thead><tbody>
      ${r.entries.map(e => `<tr><td class="small">${esc(e.ts)}</td><td>${esc(e.actor)}</td><td><span class="chip">${esc(e.role)}</span></td><td><b>${esc(e.action)}</b></td>
      <td>${esc(e.case_code || '')}</td><td class="small" style="max-width:420px;word-break:break-word">${esc(JSON.stringify(e.detail))}</td>
      <td class="small muted">${esc(e.hash.slice(0, 10))}</td></tr>`).join('')}</tbody></table>`;
  };
  $('au-go').onclick = load; await load();
}

async function renderConsent() {
  $('view').innerHTML = `<div class="panel"><h2>Consent &amp; data rights</h2><p class="small muted">The DPO role sees consent records but no clinical content.</p><div id="cs">Loading...</div></div>`;
  const load = async () => {
    const r = await CF.api('/api/admin/cases');
    $('cs').innerHTML = `<table><thead><tr><th>Case</th><th>Created</th><th>Status</th><th>Triage</th><th>Store voice</th><th>Research</th><th>Version / method</th><th></th></tr></thead><tbody>
      ${r.cases.map(c => `<tr><td>${esc(c.code)}</td><td class="small">${esc(c.created_at)}</td><td>${esc(c.status)}</td>
        ${['triage', 'store_voice', 'research'].map(k => `<td>${c.consent[k] ? '<span class="chip ok">yes</span>' : '<span class="chip">no</span>'}</td>`).join('')}
        <td class="small">${esc(c.consent.version || '')} ${esc(c.consent.method || '')}</td>
        <td>${c.status !== 'erased' ? `<button class="small danger" data-erase="${esc(c.code)}">Erase</button>` : '<span class="muted small">erased</span>'}</td></tr>`).join('')}</tbody></table>`;
    document.querySelectorAll('[data-erase]').forEach(b => b.onclick = async () => {
      const reason = prompt('Reason for erasure (e.g. patient request ref #)'); if (!reason) return;
      await CF.api(`/api/admin/cases/${b.dataset.erase}/erase`, { method: 'POST', json: { reason } }); toast('Erased'); load();
    });
  };
  await load();
}

async function renderAnalytics() {
  const a = await CF.api('/api/analytics');
  const max = Math.max(1, ...Object.values(a.by_priority));
  $('view').innerHTML = `<div class="panel"><h2>Analytics</h2>
    <div><span class="kpi">Cases<b>${a.cases}</b></span><span class="kpi">Median minutes to review<b>${a.median_minutes_to_review ?? '-'}</b></span>
      <span class="kpi">Approved = AI<b>${a.approved_vs_ai.agreed}</b></span><span class="kpi">Clinician raised<b>${a.approved_vs_ai.raised}</b></span>
      <span class="kpi">Clinician lowered<b>${a.approved_vs_ai.lowered}</b></span></div>
    <h3>Cases by priority</h3>${Object.entries(a.by_priority).map(([k, v]) => `<div class="row" style="margin:4px 0">${pill(k)}<div class="grow"><div class="bar" style="width:${v / max * 100}%"></div></div><b style="width:30px">${v}</b></div>`).join('')}
    <h3 style="margin-top:14px">Flags raised by type</h3>${Object.entries(a.flags_by_type).map(([k, v]) => `<span class="chip">${esc(k)} ${v}</span>`).join('')}
    <p class="small muted" style="margin-top:12px">Override analytics show where clinicians disagree with the AI - use them to review the rulebook.</p></div>`;
}

boot();
