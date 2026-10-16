// OpenYAM lab console frontend.
import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import URDFLoader from 'urdf-loader';
import { marked } from 'marked';

const $ = (id) => document.getElementById(id);
const DEG = 180 / Math.PI;
const NEAR_LIMIT = 5 / DEG; // amber within 5 degrees of a limit
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
]).then(([live, ghost]) => {
  liveRobot = live; ghostRobot = ghost;
  world.add(liveRobot); world.add(ghostRobot);
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
})();

function applyRobot(motors) {
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
    const a = pct(lim[0], lo, hi), b = pct(lim[1], lo, hi), w = pct(lo + NEAR_LIMIT, lo, hi);
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
        if (m.pos < lo || m.pos > hi) cls += ' red';
        else if (m.pos - lo < NEAR_LIMIT || hi - m.pos < NEAR_LIMIT) cls += ' amber';
      }
    }
    if (m.fault) cls += ' red';
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
  const outside = motors.filter((m) => m.limits && m.pos !== undefined && (m.pos < m.limits[0] || m.pos > m.limits[1])).length;
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
