// OpenYAM lab console frontend.
import * as THREE from 'three';
import { marked } from 'marked';
import { createArmView, solidMaterial, setArmPose, syncViews, describeSim, FINGER_MIN } from '/static/arm3d.js';

const $ = (id) => document.getElementById(id);
const DEG = 180 / Math.PI;
const SOFT_MARGIN = 0.1; // rad; soft limits = URDF limits shrunk by this. Amber between soft and hard.
const NEAR_LIMIT = SOFT_MARGIN;
// Joints 2/3 rest on their stops at ~0 = their lower limit and read e.g. -0.003; only call it
// "outside" (red) beyond this tolerance, otherwise amber.
const LIMIT_TOL = 0.02;
const lastMotors = {}; // latest decoded motor state by name
let hoverJoint = null;  // joint currently highlighted across panels
const tags = {};        // floating 3D joint tags
const STALE_S = 0.5;
const QS = new URLSearchParams(location.search);
// ?nophones: never load live phone-camera frames (screenshots, screen sharing). ?nocam: USB camera off at start.
const NO_PHONES = QS.has('nophones');
let mockOK = false, armctlStatus = null;  // armctl daemon: mock backend? last /status

// Gripper: motor rad -> URDF finger prismatic (joint7/joint8, [-0.04695, 0]).
// The motor range is not characterised yet; opening DEcreases motor position.
// Rough guess: 0 rad = closed, GRIPPER_OPEN_RAD = fully open.
const GRIPPER_OPEN_RAD = -4.5;

// ---------------------------------------------------------------- markdown
marked.setOptions({ gfm: true, breaks: false });

function renderMarkdown(el, text, { checkable = false } = {}) {
  el.innerHTML = marked.parse(text);
  el.querySelectorAll('input[type=checkbox]').forEach((cb, i) => {
    const li = cb.closest('li');
    if (li) { li.classList.add('task'); li.classList.toggle('done', cb.checked); }
    if (!checkable) return;
    cb.disabled = false;
    cb.dataset.index = i;
    cb.addEventListener('change', () => toggleCheck(i, cb.checked, cb));
  });
  el.querySelectorAll('a[href^="http"]').forEach((a) => { a.target = '_blank'; a.rel = 'noopener'; });
}

// ---------------------------------------------------------------- coach
let coachMtime = null;
let coachSelfWrite = 0;

async function loadCoach(flash) {
  const r = await fetch('/coach', { cache: 'no-store' });
  const text = await r.text();
  coachMtime = parseFloat(r.headers.get('X-Mtime')) || coachMtime;
  renderMarkdown($('coachBody'), text, { checkable: true });
  $('coachUpdated').textContent = coachMtime ? 'updated ' + new Date(coachMtime * 1000).toLocaleTimeString() : '';
  if (flash) {
    const p = $('coachPanel');
    p.classList.remove('flash'); void p.offsetWidth; p.classList.add('flash');
  }
}

async function toggleCheck(index, checked, cb) {
  coachSelfWrite = Date.now();
  const li = cb.closest('li');
  if (li) li.classList.toggle('done', checked);
  try {
    const r = await fetch('/api/coach/check', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ index, checked }),
    });
    if (!r.ok) throw new Error(await r.text());
    const j = await r.json();
    coachMtime = j.mtime;
  } catch (e) {
    cb.checked = !checked;
    if (li) li.classList.toggle('done', !checked);
    alert('Could not update coach.md: ' + e.message);
  }
}

$('noteForm').addEventListener('submit', async (ev) => {
  ev.preventDefault();
  const input = $('noteText');
  const text = input.value.trim();
  if (!text) return;
  $('noteStatus').textContent = 'sending…';
  try {
    const r = await fetch('/api/inbox', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ text }),
    });
    if (!r.ok) throw new Error(await r.text());
    input.value = '';
    $('noteStatus').textContent = 'sent ' + new Date().toLocaleTimeString();
  } catch (e) {
    $('noteStatus').textContent = 'failed: ' + e.message;
  }
});

// ---------------------------------------------------------------- log
let logMtime = null;
async function loadLog() {
  const r = await fetch('/log', { cache: 'no-store' });
  const text = await r.text();
  logMtime = parseFloat(r.headers.get('X-Mtime')) || null;
  const body = $('logBody');
  const atBottom = body.scrollHeight - body.scrollTop - body.clientHeight < 40 || !body.dataset.loaded;
  renderMarkdown(body, text);
  body.dataset.loaded = '1';
  if (atBottom) body.scrollTop = body.scrollHeight;
}
$('logDetails').addEventListener('toggle', () => { if ($('logDetails').open) loadLog(); });

// ---------------------------------------------------------------- 3D views: real arm and simulated arm
// Two separate views (arm3d.js): the measured arm from CAN, and armctl's mock-backend arm
// (what phone programs and playables drive). Each has its own orbit camera; "sync views"
// links them.
const viewer = $('viewer');
const realView = createArmView(viewer);
const simView = createArmView($('simViewer'), { armMaterial: solidMaterial(0xff8a3d) });
const renderer = realView.renderer, camera3 = realView.camera;
let liveRobot = null;
let simRobot = null;
realView.ready.then((v) => {
  liveRobot = v.robot;
  buildTags();
  $('viewerMsg').remove();
}).catch((e) => {
  $('viewerMsg').textContent = 'URDF load failed: ' + (e && e.message ? e.message : e);
  console.error(e);
});
simView.ready.then((v) => { simRobot = v.robot; $('simViewerMsg').remove(); if (armctlStatus) applySim(armctlStatus); })
  .catch((e) => { $('simViewerMsg').textContent = 'URDF load failed: ' + (e && e.message ? e.message : e); });
realView.frameHooks.push(() => updateTags());
let syncOn = false;
try { syncOn = localStorage.getItem('openyam-sync-views') === '1'; } catch (e) { /* ignore */ }
$('syncViews').checked = syncOn;
$('syncViews').addEventListener('change', () => {
  syncOn = $('syncViews').checked;
  try { localStorage.setItem('openyam-sync-views', syncOn ? '1' : '0'); } catch (e) { /* ignore */ }
  if (syncOn) { simView.camera.position.copy(camera3.position); simView.controls.target.copy(realView.controls.target); simView.controls.update(); }
});
syncViews(realView, simView, () => syncOn);

function applyRobot(motors) {
  for (const m of motors) lastMotors[m.name] = m;
  if (!liveRobot) return;
  for (const m of motors) {
    if (m.pos === undefined) continue;
    if (m.name === 'gripper') {
      const frac = Math.min(1, Math.max(0, m.pos / GRIPPER_OPEN_RAD));
      const v = FINGER_MIN * frac;
      liveRobot.setJointValue('joint7', v);
      liveRobot.setJointValue('joint8', v);
    } else if (liveRobot.joints[m.name]) {
      // Show the real measured value even beyond URDF limits.
      const j = liveRobot.joints[m.name];
      j.ignoreLimits = true;
      j.setJointValue(m.pos);
    }
  }
}

// ---------------------------------------------------------------- joints table
const VIEW_LO = -Math.PI - 0.6, VIEW_HI = Math.PI + 0.6;
const GRIP_LO = -6, GRIP_HI = 1;
const rows = {};

function pct(v, lo, hi) { return ((v - lo) / (hi - lo)) * 100; }

function buildRow(m) {
  const lim = m.limits;
  const [lo, hi] = lim ? [VIEW_LO, VIEW_HI] : [GRIP_LO, GRIP_HI];
  const el = document.createElement('div');
  el.className = 'jrow';
  let barInner = '';
  if (lim) {
    const a = pct(lim[0], lo, hi), b = pct(lim[1], lo, hi), w = pct(lo + SOFT_MARGIN, lo, hi);
    barInner += `<div class="range" style="left:${a}%;width:${b - a}%"></div>`;
    barInner += `<div class="warnzone" style="left:${a}%;width:${w}%"></div><div class="warnzone" style="left:${b - w}%;width:${w}%"></div>`;
    barInner += `<span class="lbl" style="left:${a}%">${(lim[0] * DEG).toFixed(0)}°</span><span class="lbl" style="left:${b}%">${(lim[1] * DEG).toFixed(0)}°</span>`;
  } else {
    barInner += `<span class="lbl left" style="left:2px" title="raw motor rad; range not characterised; opening = more negative">raw rad · open = more −</span>`;
  }
  barInner += `<div class="zero" style="left:calc(${pct(0, lo, hi)}% - 1px)"></div><div class="needle"></div>`;
  el.innerHTML = `
    <div class="jname">${m.name}<span class="sub">0x${m.id.toString(16).padStart(2, '0')} ${m.model}</span></div>
    <div class="barwrap"><div class="bar">${barInner}</div></div>
    <div class="jval"><div class="rad">—</div><div class="deg"></div></div>
    <div class="jmeta"></div>`;
  $('jointRows').appendChild(el);
  el.addEventListener('mouseenter', () => setHover(m.name, 'row'));
  el.addEventListener('mouseleave', () => setHover(null, 'row'));
  rows[m.name] = { el, lo, hi, needle: el.querySelector('.needle'), rad: el.querySelector('.rad'), deg: el.querySelector('.deg'), meta: el.querySelector('.jmeta') };
}

function fmtAge(a) {
  if (a === null || a === undefined) return 'never';
  if (a < 1) return (a * 1000).toFixed(0) + ' ms';
  if (a < 120) return a.toFixed(1) + ' s';
  return (a / 60).toFixed(0) + ' min';
}

function updateRows(motors) {
  for (const m of motors) {
    if (!rows[m.name]) buildRow(m);
    const r = rows[m.name];
    let cls = 'jrow';
    if (m.pos === undefined) {
      r.rad.textContent = '—'; r.deg.textContent = 'no reply yet';
      r.needle.style.display = 'none';
    } else {
      r.needle.style.display = '';
      const p = Math.min(100, Math.max(0, pct(m.pos, r.lo, r.hi)));
      r.needle.style.left = p + '%';
      r.rad.textContent = (m.pos >= 0 ? '+' : '') + m.pos.toFixed(3);
      r.deg.textContent = (m.deg >= 0 ? '+' : '') + m.deg.toFixed(1) + '°' + (m.limits ? '' : ' (raw)');
      if (m.limits) {
        const [lo, hi] = m.limits;
        if (m.pos < lo - LIMIT_TOL || m.pos > hi + LIMIT_TOL) cls += ' red';
        else if (m.pos - lo < NEAR_LIMIT || hi - m.pos < NEAR_LIMIT) cls += ' amber';
      }
    }
    if (m.fault) cls += ' red';
    if (hoverJoint === m.name) cls += ' hover';
    r.el.className = cls;
    const stCls = m.state === undefined ? '' : m.fault ? 'st-fault' : m.state === 1 ? 'st-enabled' : 'st-disabled';
    const stale = m.age === null || m.age > STALE_S;
    r.meta.innerHTML =
      `<span class="${stCls}">${m.state_name || 'unknown'}</span> · <span class="${stale ? 'stale' : ''}">${fmtAge(m.age)}</span> · ${Math.round(m.rate)}/s<br>` +
      (m.pos !== undefined
        ? `MOS ${m.t_mos}° rotor ${m.t_rotor}°C<br>vel ${m.vel.toFixed(2)} τ ${m.tau.toFixed(2)} (approx)`
        : '');
  }
}

// ---------------------------------------------------------------- bus + pills
function pill(text, cls) { return `<span class="pill ${cls || ''}">${text}</span>`; }

document.querySelectorAll('#modeSeg button').forEach((b) => b.addEventListener('click', async () => {
  await fetch('/api/mode', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ mode: b.dataset.mode }) });
}));

function updateBus(s) {
  const b = s.bus;
  document.querySelectorAll('#modeSeg button').forEach((x) => x.classList.toggle('on', x.dataset.mode === b.mode));
  const ext = b.external_controller;
  const activity = b.transmitting ? 'polling (refresh 10 Hz)'
    : b.mode === 'listen' ? 'listen-only (by choice)'
    : ext ? 'listening: external controller'
    : b.external_reader ? 'listening: another reader is polling'
    : 'listening (waiting)';
  const rows = [
    ['interface', `${b.iface} ${b.connected ? '<span class="ok">open</span>' : '<span class="bad">not open</span>'}`],
    ['activity', activity],
    ['external controller', ext ? `<span class="warn">DETECTED (DimOS?)</span> ${fmtAge(b.external_controller_age)} ago` : (b.external_controller_age ? `none (last ${fmtAge(b.external_controller_age)} ago)` : 'none seen')],
    ['last ext. frame', b.external_controller_frame || '—'],
    ['rx frames/s', `${b.rx_fps} (total ${b.rx_total})`],
    ['tx frames/s', `${b.tx_fps} (total ${b.tx_total})`],
    ['last error', b.last_error ? `<span class="bad">${escapeHtml(b.last_error)}</span> (${fmtAge(b.last_error_age)} ago)` : '—'],
  ];
  $('busKv').innerHTML = rows.map(([k, v]) => `<dt>${k}</dt><dd>${v}</dd>`).join('');

  const motors = s.motors;
  const fresh = motors.filter((m) => m.age !== null && m.age < STALE_S).length;
  const faults = motors.filter((m) => m.fault).length;
  const enabled = motors.filter((m) => m.state === 1).length;
  const outside = motors.filter((m) => m.limits && m.pos !== undefined && (m.pos < m.limits[0] - LIMIT_TOL || m.pos > m.limits[1] + LIMIT_TOL)).length;
  $('topPills').innerHTML = [
    pill(`${b.iface} ${b.connected ? 'up' : 'down'}`, b.connected ? 'ok' : 'bad'),
    pill(`mode ${b.mode}${b.transmitting ? ' · polling' : ' · silent'}`, b.mode === 'auto' ? '' : 'warn'),
    ext ? pill('external controller (DimOS?)', 'warn') : '',
    pill(`${fresh}/${motors.length} motors live`, fresh === motors.length ? 'ok' : fresh ? 'warn' : 'bad'),
    pill(`${enabled} enabled`, enabled ? 'warn' : 'ok'),
    faults ? pill(`${faults} FAULT`, 'bad') : '',
    outside ? pill(`${outside} outside limits`, 'bad') : '',
    pill(`rx ${b.rx_fps}/s`),
  ].join('');

  const c = s.camera;
  $('camStatus').textContent = c.running ? `${c.device} · ${c.fps} fps · ${c.clients} viewer(s)` : (c.error || `${c.device} idle`);
}

function escapeHtml(t) { return String(t).replace(/[&<>"]/g, (ch) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[ch])); }

// ---------------------------------------------------------------- camera
let camOn = false;
function setCam(on) {
  camOn = on;
  const img = $('camImg');
  $('camToggle').textContent = on ? 'Stop camera' : 'Start camera';
  if (on) {
    $('camMsg').textContent = 'Connecting…';
    $('camMsg').style.display = '';
    img.src = '/camera.mjpg?t=' + Date.now();
  } else {
    img.removeAttribute('src');
    $('camMsg').textContent = 'Camera off';
    $('camMsg').style.display = '';
  }
}
$('camImg').addEventListener('load', () => { $('camMsg').style.display = 'none'; });
$('camImg').addEventListener('error', () => {
  if (!camOn) return;
  $('camMsg').textContent = 'No camera stream (retrying)…';
  $('camMsg').style.display = '';
  setTimeout(() => camOn && setCam(true), 5000);
});
$('camToggle').addEventListener('click', () => setCam(!camOn));

// ---------------------------------------------------------------- stream
function onState(s) {
  updateRows(s.motors);
  applyRobot(s.motors);
  updateBus(s);
  $('logPath').textContent = s.log_path || '';
  if (s.coach_mtime !== null && coachMtime !== null && s.coach_mtime !== coachMtime) {
    const self = Date.now() - coachSelfWrite < 1500;
    coachMtime = s.coach_mtime;
    loadCoach(!self);
  }
  if (s.log_mtime !== logMtime && $('logDetails').open) { logMtime = s.log_mtime; loadLog(); }
}

function connect() {
  const es = new EventSource('/api/stream');
  es.onmessage = (ev) => { try { onState(JSON.parse(ev.data)); } catch (e) { console.error(e); } };
  es.onerror = () => { $('topPills').innerHTML = pill('server disconnected, retrying…', 'bad'); };
}

setInterval(() => { $('clock').textContent = new Date().toLocaleTimeString(); }, 500);
loadCoach(false);
connect();
if (!QS.has('nocam')) setCam(true);

// ---------------------------------------------------------------- hover linking (rows <-> 3D)
// Which URDF link(s) each motor moves (the joint's child link).
const JOINT_LINKS = { joint1: ['link1'], joint2: ['link2'], joint3: ['link3'], joint4: ['link4'], joint5: ['link5'], joint6: ['gripper'], gripper: ['tip_left', 'tip_right'] };
const LINK_JOINT = {};
for (const [j, ls] of Object.entries(JOINT_LINKS)) for (const l of ls) LINK_JOINT[l] = j;
const HILITE = new THREE.Color(0x5cc8ff);

function linkMeshes(link) {
  // Meshes belonging to this link only (stop at child joints).
  const out = [];
  (function walk(o) {
    for (const ch of o.children) {
      if (ch.isURDFJoint) continue;
      if (ch.isMesh) out.push(ch);
      walk(ch);
    }
  })(link);
  return out;
}

function setHover(name, source) {
  if (name === hoverJoint) return;
  hoverJoint = name;
  for (const [n, r] of Object.entries(rows)) r.el.classList.toggle('hover', n === name);
  if (liveRobot) {
    for (const [j, ls] of Object.entries(JOINT_LINKS)) {
      for (const l of ls) {
        const link = liveRobot.links[l];
        if (!link) continue;
        for (const mesh of linkMeshes(link)) {
          if (!mesh.material.emissive) continue;
          mesh.material.emissive.copy(j === name ? HILITE : new THREE.Color(0));
          mesh.material.emissiveIntensity = j === name ? 0.55 : 0;
        }
      }
    }
  }
  for (const [n, t] of Object.entries(tags)) t.el.classList.toggle('hover', n === name);
  if (source !== 'mesh') tooltip.style.display = 'none';
}

// Floating tags at each joint in the 3D view.
function buildTags() {
  for (const j of Object.keys(JOINT_LINKS)) {
    const el = document.createElement('div');
    el.className = 'tag3d';
    el.textContent = j;
    el.addEventListener('mouseenter', () => setHover(j, 'tag'));
    el.addEventListener('mouseleave', () => setHover(null, 'tag'));
    viewer.appendChild(el);
    tags[j] = { el };
  }
}
const _v = new THREE.Vector3();
function tagAnchor(j) {
  if (!liveRobot) return null;
  if (j === 'gripper') {
    const a = liveRobot.links.tip_left, b = liveRobot.links.tip_right;
    if (!a || !b) return null;
    const pa = new THREE.Vector3(), pb = new THREE.Vector3();
    a.getWorldPosition(pa); b.getWorldPosition(pb);
    return pa.add(pb).multiplyScalar(0.5);
  }
  const jt = liveRobot.joints[j];
  return jt ? jt.getWorldPosition(new THREE.Vector3()) : null;
}
function updateTags() {
  const w = viewer.clientWidth, h = viewer.clientHeight;
  for (const [j, t] of Object.entries(tags)) {
    const p = tagAnchor(j);
    if (!p) { t.el.style.display = 'none'; continue; }
    _v.copy(p).project(camera3);
    if (_v.z > 1) { t.el.style.display = 'none'; continue; }
    t.el.style.display = '';
    t.el.style.transform = `translate(${(_v.x * 0.5 + 0.5) * w + 8}px, ${(-_v.y * 0.5 + 0.5) * h - 10}px)`;
    const m = lastMotors[j];
    const st = rows[j] ? rows[j].el.classList : null;
    t.el.classList.toggle('red', !!(st && st.contains('red')));
    t.el.classList.toggle('amber', !!(st && st.contains('amber')));
    t.el.textContent = m && m.pos !== undefined ? `${j} ${(m.pos >= 0 ? '+' : '') + m.pos.toFixed(2)}` : j;
  }
}

// Raycast hover in the 3D view.
const tooltip = document.createElement('div');
tooltip.className = 'tip3d';
viewer.appendChild(tooltip);
const raycaster = new THREE.Raycaster();
const ndc = new THREE.Vector2();
let rayPending = null;
renderer.domElement.addEventListener('pointermove', (ev) => { rayPending = ev; });
renderer.domElement.addEventListener('pointerleave', () => { rayPending = null; tooltip.style.display = 'none'; setHover(null, 'mesh'); });
function jointInfoHtml(j) {
  const m = lastMotors[j];
  if (!m || m.pos === undefined) return `<b>${j}</b><br>no reply yet`;
  let html = `<b>${j}</b> <span class="muted">0x${m.id.toString(16).padStart(2, '0')} ${m.model}</span><br>` +
    `${m.pos.toFixed(3)} rad · ${m.deg.toFixed(1)}°`;
  if (m.limits) {
    const [lo, hi] = m.limits;
    const dLo = m.pos - lo, dHi = hi - m.pos, d = Math.min(dLo, dHi);
    const which = dLo < dHi ? 'lower' : 'upper';
    const cls = d < -LIMIT_TOL ? 'bad' : d < SOFT_MARGIN ? 'warn' : 'ok';
    html += `<br>limits [${lo.toFixed(2)}, ${hi.toFixed(2)}] rad = [${(lo * DEG).toFixed(0)}°, ${(hi * DEG).toFixed(0)}°]` +
      `<br><span class="${cls}">${d < 0 ? 'past ' + which + ' limit' + (d < -LIMIT_TOL ? ' (OUTSIDE)' : ' (within tolerance)') : 'to ' + which + ' limit'}: ${Math.abs(d).toFixed(3)} rad (${(Math.abs(d) * DEG).toFixed(1)}°)</span>`;
  } else {
    html += '<br>raw motor rad, range not characterised';
  }
  return html + `<br><span class="muted">${m.state_name}</span>`;
}
setInterval(() => {
  const ev = rayPending;
  if (!ev || !liveRobot) return;
  const rect = renderer.domElement.getBoundingClientRect();
  ndc.set(((ev.clientX - rect.left) / rect.width) * 2 - 1, -((ev.clientY - rect.top) / rect.height) * 2 + 1);
  raycaster.setFromCamera(ndc, camera3);
  const hits = raycaster.intersectObject(liveRobot, true);
  let j = null;
  for (const h of hits) {
    let o = h.object;
    while (o && !o.isURDFLink) o = o.parent;
    if (o && LINK_JOINT[o.name]) { j = LINK_JOINT[o.name]; break; }
    if (o) break; // base or other link: nothing
  }
  setHover(j, 'mesh');
  if (j) {
    tooltip.innerHTML = jointInfoHtml(j);
    tooltip.style.display = 'block';
    const x = ev.clientX - rect.left, y = ev.clientY - rect.top;
    tooltip.style.left = Math.min(x + 14, rect.width - 230) + 'px';
    tooltip.style.top = Math.min(y + 14, rect.height - 110) + 'px';
  } else {
    tooltip.style.display = 'none';
  }
}, 50);

// ---------------------------------------------------------------- phone cameras
const phoneCards = {};
async function pollPhones() {
  let data;
  try {
    const r = await fetch('/phonecam/cameras', { cache: 'no-store' });
    if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || r.status);
    data = await r.json();
    $('phoneStatus').textContent = `${data.cameras.length} camera(s)`;
  } catch (e) {
    $('phoneStatus').textContent = 'phonecam.py not running';
    data = { cameras: [] };
  }
  const pgrid = $('phoneGrid');
  const seen = new Set();
  for (const c of data.cameras) {
    seen.add(c.name);
    let card = phoneCards[c.name];
    if (!card) {
      const el = document.createElement('div');
      el.className = 'pcard';
      el.innerHTML = NO_PHONES ? `<div class="noimg">live frames hidden (?nophones)</div><div class="pcap"><b></b><span class="age"></span></div>`
        : `<img alt=""><div class="pcap"><b></b><span class="age"></span></div>`;
      pgrid.appendChild(el);
      card = phoneCards[c.name] = { el, img: el.querySelector('img'), name: el.querySelector('b'), age: el.querySelector('.age'), loading: false, frames: -1 };
      if (card.img) {
        card.img.alt = `phone camera ${c.name}`;
        card.img.addEventListener('load', () => { card.loading = false; });
        card.img.addEventListener('error', () => { card.loading = false; });
        card.img.addEventListener('click', () => window.open('/phonecam/cam/' + encodeURIComponent(c.name) + '.jpg', '_blank'));
      }
    }
    card.name.textContent = c.name;
    const stale = c.age > 5;
    card.el.classList.toggle('stale', stale);
    card.age.textContent = `${fmtAge(c.age)} ago · ${c.fps} fps · ${(c.bytes / 1024).toFixed(0)} KB`;
    if (card.img && !card.loading && c.frames !== card.frames) {
      card.loading = true; card.frames = c.frames;
      card.img.src = `/phonecam/cam/${encodeURIComponent(c.name)}.jpg?f=${c.frames}`;
    }
  }
  for (const n of Object.keys(phoneCards)) if (!seen.has(n)) { phoneCards[n].el.remove(); delete phoneCards[n]; }
  $('phoneEmpty').style.display = data.cameras.length ? 'none' : '';
}
setInterval(pollPhones, 500);
pollPhones();

// ---------------------------------------------------------------- armctl programs (lease, approvals, E-STOP)
// Talks to armctl/daemon.py through this server's /armctl/* proxy.
async function armctlPost(path, body) {
  try {
    const r = await fetch('/armctl/' + path, {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body || {}),
    });
    const j = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(j.detail || r.statusText);
    pollArmctl();
    return j;
  } catch (e) {
    alert(`armctl ${path}: ${e.message}`);
    return null;
  }
}

$('pgEstop').addEventListener('click', () => armctlPost('estop'));

function pgButton(label, cls, fn) {
  const b = document.createElement('button');
  b.textContent = label;
  if (cls) b.className = cls;
  b.addEventListener('click', fn);
  return b;
}
function pgItem(html, cls, buttons) {
  const d = document.createElement('div');
  d.className = 'pg-item ' + (cls || '');
  const w = document.createElement('span');
  w.className = 'who';
  w.innerHTML = html;
  d.appendChild(w);
  const bs = document.createElement('span');
  for (const b of buttons || []) bs.appendChild(b);
  d.appendChild(bs);
  return d;
}
const who = (c) => `<b>${escapeHtml(c.program)}</b> <span class="muted small">${escapeHtml(c.name || '')} · ${escapeHtml(c.id)} · ${escapeHtml(c.remote || '')}</span>`;

let pgSig = '';
function renderArmctl(s) {
  const st = s.state || {};
  $('pgStatus').textContent = (s.backend === 'mock' ? 'armctl: MOCK backend (simulated arm, no hardware) · ' : `armctl: ${s.backend} backend · `) + `${st.torque ? st.mode : 'relaxed (torque off)'}` +
    (st.busy ? ` · ${st.busy}` : '') + (st.streaming ? ' · streaming' : '') + (s.auto_approve ? ' · auto-approve ON' : '');
  $('pgEstop').classList.toggle('latched', !!s.estop);
  $('pgEstop').textContent = s.estop ? 'E-STOP (latched)' : 'E-STOP';

  // Only rebuild the lists when something changed (keeps buttons clickable).
  const sig = JSON.stringify([s.lease, s.estop, st.torque, s.pending.map((p) => p.id), s.clients.map((c) => [c.id, c.holder]),
    s.pending_play, s.log.length && s.log[s.log.length - 1]]);
  renderRefusals(s.refusals || []);
  if (sig === pgSig) return;
  pgSig = sig;

  const h = s.lease || {};
  $('pgHolder').innerHTML = h.holder ? `<b>${escapeHtml(h.holder)}</b> <span class="muted small">${escapeHtml(h.name || '')} · ${escapeHtml(h.client)}</span>`
    : (h.switching ? 'switching (stopping first)…' : 'nobody (arm holds still)');
  $('pgHolder').classList.toggle('free', !h.holder);
  const acts = $('pgActions');
  acts.innerHTML = '';
  if (s.estop) {
    const b = document.createElement('span'); b.className = 'banner'; b.textContent = 'E-STOP latched: all motion refused';
    acts.appendChild(b);
    acts.appendChild(pgButton('Reset E-STOP', '', () => confirm('Reset the E-STOP? Programs can then ask for control again.') && armctlPost('estop/reset')));
  }
  if (h.holder) acts.appendChild(pgButton('Revoke lease', '', () => armctlPost('lease', { revoke: true })));
  if (st.torque === false) acts.appendChild(pgButton('Enable torque (hold)', '', () => confirm('Enable torque and hold the current position?') && armctlPost('enable')));

  const pend = $('pgPending');
  pend.innerHTML = '';
  if (s.pending_play) {
    const p = s.pending_play;
    pend.appendChild(pgItem(`▶ playable <b>${escapeHtml(p.name)}</b> <span class="muted small">from ${escapeHtml(p.program)} · ${p.duration} s</span>`, 'pending', [
      pgButton('Play', 'play', () => confirm(`Play "${p.name}" (${p.duration} s)? The arm will move to the start pose first.`) && armctlPost('playable/confirm', { name: p.name })),
      pgButton('Cancel', '', () => armctlPost('playable/cancel')),
    ]));
  }
  for (const c of s.pending) {
    pend.appendChild(pgItem(who(c) + ` <span class="muted small">asks for control (${Math.round(c.since_s)} s)</span>`, 'pending', [
      pgButton('Approve', 'approve', () => armctlPost('lease', { client: c.id })),
      pgButton('Deny', '', () => armctlPost('lease/deny', { client: c.id })),
    ]));
  }
  if (!pend.children.length) pend.innerHTML = '<span class="muted small">none</span>';

  const cl = $('pgClients');
  cl.innerHTML = '';
  for (const c of s.clients) {
    const btns = c.holder ? [] : [pgButton('Give control', '', () => armctlPost('lease', { client: c.id }))];
    cl.appendChild(pgItem(who(c) + (c.holder ? ' <span class="small" style="color:var(--ok)">● holder</span>' : ''), c.holder ? 'holder' : (c.pending ? 'pending' : ''), btns));
  }
  if (!s.clients.length) cl.innerHTML = '<span class="muted small">none connected (phones: /tilt, /joystick on the phone server)</span>';

  const lg = $('pgLog');
  const atBottom = lg.scrollHeight - lg.scrollTop - lg.clientHeight < 20;
  lg.textContent = s.log.join('\n');
  if (atBottom) lg.scrollTop = lg.scrollHeight;
}

// Refusals: newest first, at most 20. Entries from before this page was opened are faded
// ("earlier"), so old test traffic doesn't look like something that just happened.
// Clear hides what is listed now (this page only; the daemon keeps its own log).
const PAGE_T0 = Date.now() / 1000;
const REF_MAX = 20;
let refClearedAt = 0, refSig = '', refLast = [];
function relTime(t) {
  const d = Math.max(0, Date.now() / 1000 - t);
  if (d < 5) return 'now';
  if (d < 60) return `${Math.floor(d)} s ago`;
  if (d < 3600) return `${Math.floor(d / 60)} min ago`;
  if (d < 86400) return `${Math.floor(d / 3600)} h ago`;
  return new Date(t * 1000).toLocaleDateString();
}
function renderRefusals(list) {
  refLast = list;
  const shown = list.filter((r) => r.t > refClearedAt).slice(-REF_MAX).reverse();
  const sig = JSON.stringify([shown.length && shown[0].t, shown.length, shown.map((r) => relTime(r.t))]);
  if (sig === refSig) return;
  refSig = sig;
  const rf = $('pgRefusals');
  rf.innerHTML = '';
  let sepDone = false;
  for (const r of shown) {
    const old = r.t < PAGE_T0;
    if (old && !sepDone) {
      sepDone = true;
      const sep = document.createElement('div');
      sep.className = 'pg-sep';
      sep.textContent = 'before this page was opened';
      rf.appendChild(sep);
    }
    const d = document.createElement('div');
    d.className = 'pg-ref' + (old ? ' old' : '');
    d.title = new Date(r.t * 1000).toLocaleString();
    const when = document.createElement('span');
    when.className = 'when';
    when.textContent = relTime(r.t);
    const body = document.createElement('span');
    const src = document.createElement('span');
    src.className = 'src';
    src.textContent = `${r.program || 'operator'} ${r.what}: `;
    body.appendChild(src);
    body.appendChild(document.createTextNode(r.reason));
    d.appendChild(when); d.appendChild(body);
    rf.appendChild(d);
  }
  if (!shown.length) rf.innerHTML = `<span class="muted small">${refClearedAt ? 'none since cleared' : 'none'}</span>`;
  $('pgRefCount').textContent = shown.length ? `(${shown.length})` : '';
}
$('pgRefClear').addEventListener('click', () => {
  refClearedAt = Date.now() / 1000;
  refSig = '';
  renderRefusals(refLast);
});

let pgBusy = false;
async function pollArmctl() {
  if (pgBusy) return;
  pgBusy = true;
  try {
    const r = await fetch('/armctl/status', { cache: 'no-store' });
    const j = await r.json();
    if (!r.ok) throw new Error(j.detail || r.statusText);
    renderArmctl(j);
    applySim(j);
    armctlStatus = j;
    const mock = j.backend === 'mock';
    if (mock !== mockOK) { mockOK = mock; renderPlayables(); }
  } catch (e) {
    $('pgStatus').textContent = 'armctl daemon not reachable (' + e.message + ')';
    $('pgHolder').textContent = '–';
    pgSig = '';
    mockOK = false;
    armctlStatus = null;
    applySim(null);
  } finally {
    pgBusy = false;
  }
}
setInterval(pollArmctl, 100);  // >= 10 Hz for the simulated arm view

// The simulated arm (armctl mock backend) in its own view, with who is driving it.
function applySim(status) {
  const d = describeSim(status);
  $('simDriver').textContent = d.driver;
  $('simDetail').textContent = d.detail;
  $('simDetail').className = 'small ' + d.cls;
  $('simViewer').classList.toggle('off', !d.on);
  if (simRobot && d.on) setArmPose(simRobot, d.q, d.grip || 0);
}
pollArmctl();

// ---------------------------------------------------------------- playables panel
// Lists playables/*.json (via the console's /api/playables). Preview opens /playables.
// "Try on sim" (only with the armctl MOCK backend, on this laptop) runs the normal program
// flow over the daemon's WebSocket: validate -> ask for the lease (approve it above) ->
// playable.play -> press Play in the Programs panel. The real arm is never driven from here.
const REAL_CMD = 'experiments/2026-10-16-linux-real-arm/session_play.sh';
let ARMCTL_WS = 'ws://127.0.0.1:8091/armctl';
fetch('/api/config').then((r) => r.json()).then((c) => { if (c.armctl_ws) ARMCTL_WS = c.armctl_ws; }).catch(() => {});
const LOCAL = ['127.0.0.1', 'localhost', '[::1]'].includes(location.hostname);
let playables = [], plSelected = null, plRun = null;

async function loadPlayables() {
  try {
    const r = await fetch('/api/playables', { cache: 'no-store' });
    if (!r.ok) throw new Error(r.status + ' ' + r.statusText);
    playables = await r.json();
    if (!plSelected || !playables.some((p) => p.name === plSelected)) plSelected = (playables.find((p) => p.ok) || playables[0] || {}).name || null;
    renderPlayables();
  } catch (e) {
    $('plList').innerHTML = `<span class="bad small">cannot list playables: ${escapeHtml(e.message)}</span>`;
  }
}

function renderPlayables() {
  const list = $('plList');
  if (!playables.length) { list.innerHTML = '<span class="muted small">no playables in playables/</span>'; updateRealCmd(); return; }
  list.innerHTML = '';
  for (const p of playables) {
    const el = document.createElement('div');
    el.className = 'pl-item' + (p.name === plSelected ? ' sel' : '');
    const badge = p.error ? '<span class="badge bad">error</span>'
      : p.ok ? '<span class="badge ok">valid</span>' : `<span class="badge bad">invalid · ${p.n_violations}</span>`;
    const running = plRun && plRun.name === p.name;
    const canTry = p.ok && mockOK && LOCAL;
    el.innerHTML = `<div class="pl-row"><span class="pl-name" title="select for the real-arm command">${escapeHtml(p.name)}</span>${badge}
      <span class="pl-actions"><a href="/playables?p=${encodeURIComponent(p.name)}" title="Animate it on the 3D model; nothing moves">Preview</a></span></div>
      <div class="pl-meta">${p.duration ? p.duration + ' s' : '—'} · ${escapeHtml(p.author || p.error || 'unknown author')}</div>`;
    const acts = el.querySelector('.pl-actions');
    const b = document.createElement('button');
    b.type = 'button';
    b.textContent = running ? 'Stop' : 'Try on sim';
    b.disabled = !running && (!canTry || !!plRun);
    b.title = !mockOK ? 'needs the armctl daemon with the mock backend' : !LOCAL ? 'only from the laptop itself' : !p.ok ? 'does not validate' : 'Play it on the simulated arm (armctl mock); you approve the lease and press Play above';
    b.addEventListener('click', () => (running ? stopSimRun('stopped') : startSimRun(p.name)));
    acts.appendChild(b);
    if (running) {
      const st = document.createElement('div');
      st.className = 'pl-status ' + (plRun.cls || 'warn');
      st.textContent = plRun.msg;
      el.appendChild(st);
    } else if (p.lastRun) {
      const st = document.createElement('div');
      st.className = 'pl-status ' + p.lastRun.cls;
      st.textContent = p.lastRun.msg;
      el.appendChild(st);
    }
    el.querySelector('.pl-name').addEventListener('click', () => { plSelected = p.name; renderPlayables(); });
    list.appendChild(el);
  }
  updateRealCmd();
}

function updateRealCmd() {
  $('plSel').textContent = plSelected || '–';
  $('plCmd').textContent = `${REAL_CMD} ${plSelected || '<name>'}`;
  $('plCopy').disabled = !plSelected;
}
$('plCopy').addEventListener('click', async () => {
  const t = $('plCmd').textContent;
  try { await navigator.clipboard.writeText(t); $('plCopy').textContent = 'Copied'; }
  catch (e) {  // clipboard API needs a secure context; 127.0.0.1 counts, a LAN IP doesn't
    const range = document.createRange(); range.selectNodeContents($('plCmd'));
    const sel = getSelection(); sel.removeAllRanges(); sel.addRange(range);
    $('plCopy').textContent = 'Selected: Ctrl+C';
  }
  setTimeout(() => { $('plCopy').textContent = 'Copy'; }, 1500);
});

function simRunStatus(msg, cls) { if (!plRun) return; plRun.msg = msg; plRun.cls = cls || 'warn'; renderPlayables(); }
function stopSimRun(msg, cls) {
  const run = plRun;
  if (!run) return;
  plRun = null;
  const p = playables.find((x) => x.name === run.name);
  if (p) p.lastRun = { msg: msg, cls: cls || 'muted' };
  try {
    if (run.ws.readyState === 1) {
      if (run.playing) run.ws.send(JSON.stringify({ op: 'do', cmd: 'stop' }));
      run.ws.send(JSON.stringify({ op: 'release' }));
    }
  } catch (e) { /* closing anyway */ }
  setTimeout(() => { try { run.ws.close(); } catch (e) { /* ignore */ } }, 200);
  renderPlayables();
}
function startSimRun(name) {
  if (plRun || !mockOK) return;
  const ws = new WebSocket(ARMCTL_WS);
  const run = plRun = { name, ws, me: null, msg: 'connecting to armctl…', cls: 'warn', asked: false, playing: false };
  const send = (m) => ws.send(JSON.stringify(m));
  renderPlayables();
  ws.onopen = () => send({ op: 'hello', program: 'console', name: 'playables' });
  ws.onerror = () => { if (plRun === run) stopSimRun('could not reach the armctl daemon', 'bad'); };
  ws.onclose = () => { if (plRun === run) stopSimRun('connection closed', 'bad'); };
  ws.onmessage = (ev) => {
    if (plRun !== run) return;
    let m; try { m = JSON.parse(ev.data); } catch (e) { return; }
    if (m.op === 'hello') {
      run.me = m.client;
      if (m.estop) { stopSimRun('E-STOP is latched: reset it first', 'bad'); return; }
      simRunStatus('validating…'); send({ op: 'playable.validate', id: 'v', name });
    } else if (m.op === 'playable.result') {
      if (!m.ok) { stopSimRun('daemon rejected it: ' + (m.violations || []).slice(0, 2).join('; '), 'bad'); return; }
      const holder = armctlStatus && armctlStatus.lease && armctlStatus.lease.client === run.me;
      if (holder) askPlay(); else { simRunStatus('asking for control: Approve "console" in Programs ↑'); send({ op: 'acquire', id: 'a' }); }
    } else if (m.op === 'lease') {
      if (m.client === run.me && !run.asked) askPlay();
    } else if (m.op === 'ack' && m.id === 'p') {
      if (m.pending) simRunStatus('press Play in Programs ↑ (moves the simulated arm only)');
      else if (m.playing) { run.playing = true; simRunStatus('playing on the simulated arm…', 'ok'); }
    } else if (m.op === 'done' && m.id === 'p') {
      stopSimRun(`played on the simulated arm (${new Date().toLocaleTimeString()})`, 'ok');
    } else if (m.op === 'refused' && !m.stream) {
      stopSimRun('refused: ' + m.reason, 'bad');
    }
  };
  function askPlay() { run.asked = true; simRunStatus('asking to play…'); send({ op: 'playable.play', id: 'p', name }); }
}
window.addEventListener('pagehide', () => { if (plRun) stopSimRun('page closed'); });
loadPlayables();
setInterval(() => { if (!plRun) loadPlayables(); }, 15000);
