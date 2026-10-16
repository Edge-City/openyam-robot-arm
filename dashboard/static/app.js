// OpenYAM lab console frontend.
import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import URDFLoader from 'urdf-loader';
import { marked } from 'marked';

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

// Gripper: motor rad -> URDF finger prismatic (joint7/joint8, [-0.04695, 0]).
// The motor range is not characterised yet; opening DEcreases motor position.
// Rough guess: 0 rad = closed, GRIPPER_OPEN_RAD = fully open.
const GRIPPER_OPEN_RAD = -4.5;
const FINGER_MIN = -0.04695;

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

// ---------------------------------------------------------------- 3D arm
const viewer = $('viewer');
const renderer = new THREE.WebGLRenderer({ antialias: true });
renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
renderer.setClearColor(0x0a0d10);
viewer.appendChild(renderer.domElement);
const scene = new THREE.Scene();
const camera3 = new THREE.PerspectiveCamera(40, 1, 0.01, 20);
camera3.position.set(0.95, 0.7, 0.95);
const controls = new OrbitControls(camera3, renderer.domElement);
controls.target.set(0, 0.15, 0);
controls.enableDamping = true;
controls.update();

scene.add(new THREE.HemisphereLight(0xdde8ff, 0x202530, 1.6));
const sun = new THREE.DirectionalLight(0xffffff, 1.6);
sun.position.set(1, 2, 1.5);
scene.add(sun);
const grid = new THREE.GridHelper(1.2, 24, 0x34404d, 0x1c232b);
scene.add(grid);

// URDF is Z-up; three is Y-up. Everything robot-related lives in `world`.
const world = new THREE.Group();
world.rotation.x = -Math.PI / 2;
scene.add(world);

// +x forward marker (model frame), on the floor.
const fwd = new THREE.ArrowHelper(new THREE.Vector3(1, 0, 0), new THREE.Vector3(0, 0, 0.002), 0.35, 0xff8a3d, 0.05, 0.03);
world.add(fwd);
{
  const c = document.createElement('canvas'); c.width = 256; c.height = 64;
  const g = c.getContext('2d'); g.fillStyle = '#ff8a3d'; g.font = 'bold 34px system-ui'; g.fillText('+x forward', 6, 44);
  const sp = new THREE.Sprite(new THREE.SpriteMaterial({ map: new THREE.CanvasTexture(c), depthTest: false }));
  sp.scale.set(0.16, 0.04, 1); sp.position.set(0.42, 0, 0.03);
  world.add(sp);
}

const URDF_URL = '/urdf/i2rt/yam.urdf';
function makeLoader(materialFn) {
  const loader = new URDFLoader();
  loader.packages = { yam_description: '/urdf' };
  const base = loader.defaultMeshLoader.bind(loader);
  loader.loadMeshCb = (path, manager, done) => {
    base(path, manager, (obj, err) => {
      done(obj, err); // urdf-loader assigns the URDF <material> here
      if (obj) obj.traverse((o) => { if (o.isMesh) materialFn(o); }); // then we override it
    });
  };
  return loader;
}

let liveRobot = null;
let ghostRobot = null;
let simRobot = null;  // armctl's simulated (mock backend) arm, shown in orange
const simMat = new THREE.MeshBasicMaterial({ color: 0xff8a3d, transparent: true, opacity: 0.35, depthWrite: false });
const ghostMat = new THREE.MeshBasicMaterial({ color: 0x5cc8ff, transparent: true, opacity: 0.16, depthWrite: false });

function loadRobot(materialFn) {
  return new Promise((resolve, reject) => {
    makeLoader(materialFn).load(URDF_URL, resolve, undefined, reject);
  });
}

Promise.all([
  loadRobot((m) => {
    const c = m.material && m.material.color ? m.material.color.clone() : new THREE.Color(0xaaaaaa);
    m.material = new THREE.MeshStandardMaterial({ color: c, metalness: 0.15, roughness: 0.6 });
  }),
  loadRobot((m) => { m.material = ghostMat; m.renderOrder = 2; }),
  loadRobot((m) => { m.material = simMat; m.renderOrder = 3; }),
]).then(([live, ghost, sim]) => {
  liveRobot = live; ghostRobot = ghost; simRobot = sim;
  simRobot.visible = false;
  buildTags();
  world.add(liveRobot); world.add(ghostRobot); world.add(simRobot);
  $('viewerMsg').remove();
}).catch((e) => {
  $('viewerMsg').textContent = 'URDF load failed: ' + (e && e.message ? e.message : e);
  console.error(e);
});

function resize() {
  const w = viewer.clientWidth, h = viewer.clientHeight;
  renderer.setSize(w, h, false);
  camera3.aspect = w / Math.max(h, 1);
  camera3.updateProjectionMatrix();
}
new ResizeObserver(resize).observe(viewer);
resize();
(function animate() {
  requestAnimationFrame(animate);
  controls.update();
  renderer.render(scene, camera3);
  updateTags();
})();

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
    barInner += `<span class="lbl left" style="left:2px">raw motor rad · range not characterised · opening = more negative</span>`;
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
if (!new URLSearchParams(location.search).has("nocam")) setCam(true);

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
  const grid = $('phoneGrid');
  const seen = new Set();
  for (const c of data.cameras) {
    seen.add(c.name);
    let card = phoneCards[c.name];
    if (!card) {
      const el = document.createElement('div');
      el.className = 'pcard';
      el.innerHTML = `<img alt=""><div class="pcap"><b></b><span class="age"></span></div>`;
      grid.appendChild(el);
      card = phoneCards[c.name] = { el, img: el.querySelector('img'), name: el.querySelector('b'), age: el.querySelector('.age'), loading: false, frames: -1 };
      card.img.addEventListener('load', () => { card.loading = false; });
      card.img.addEventListener('error', () => { card.loading = false; });
      card.img.addEventListener('click', () => window.open('/phonecam/cam/' + c.name + '.jpg', '_blank'));
    }
    card.name.textContent = c.name;
    const stale = c.age > 5;
    card.el.classList.toggle('stale', stale);
    card.age.textContent = `${fmtAge(c.age)} ago · ${c.fps} fps · ${(c.bytes / 1024).toFixed(0)} KB`;
    if (!card.loading && c.frames !== card.frames) {
      card.loading = true; card.frames = c.frames;
      card.img.src = `/phonecam/cam/${encodeURIComponent(c.name)}.jpg?f=${c.frames}`;
    }
  }
  for (const n of Object.keys(phoneCards)) if (!seen.has(n)) { phoneCards[n].el.remove(); delete phoneCards[n]; }
  $('phoneEmpty').style.display = data.cameras.length ? 'none' : '';
}
setInterval(pollPhones, 500);
pollPhones();
$('phoneAdd').addEventListener('toggle', () => {
  if ($('phoneAdd').open) $('phoneQr').src = '/phonecam/qr.png?ts=' + Date.now();
});

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
    s.pending_play, s.refusals.length && s.refusals[s.refusals.length - 1].t, s.log.length && s.log[s.log.length - 1]]);
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

  const rf = $('pgRefusals');
  rf.innerHTML = '';
  for (const r of s.refusals.slice(-6).reverse()) {
    const d = document.createElement('div');
    d.className = 'pg-ref';
    d.textContent = `${new Date(r.t * 1000).toLocaleTimeString()} ${r.program || 'operator'} ${r.what}: ${r.reason}`;
    rf.appendChild(d);
  }
  if (!s.refusals.length) rf.innerHTML = '<span class="muted small">none</span>';
  const lg = $('pgLog');
  const atBottom = lg.scrollHeight - lg.scrollTop - lg.clientHeight < 20;
  lg.textContent = s.log.join('\n');
  if (atBottom) lg.scrollTop = lg.scrollHeight;
}

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
  } catch (e) {
    $('pgStatus').textContent = 'armctl daemon not reachable (' + e.message + ')';
    $('pgHolder').textContent = '–';
    pgSig = '';
  } finally {
    pgBusy = false;
  }
}
setInterval(pollArmctl, 150);

// Show armctl's simulated arm (mock backend) as an orange ghost, so phone
// programs can be tried and seen without the real arm moving.
function applySim(status) {
  if (!simRobot) return;
  const st = status && status.state;
  const on = !!(st && status.backend === 'mock' && Array.isArray(st.q));
  simRobot.visible = on;
  const badge = $('simBadge');
  if (badge) badge.hidden = !on;
  if (!on) return;
  st.q.forEach((v, i) => {
    const j = simRobot.joints['joint' + (i + 1)];
    if (j) { j.ignoreLimits = true; j.setJointValue(v); }
  });
  const g = FINGER_MIN * Math.min(1, Math.max(0, st.grip || 0));
  simRobot.setJointValue('joint7', g);
  simRobot.setJointValue('joint8', g);
}
pollArmctl();
