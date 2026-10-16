// Playable preview: animate a playable on the URDF model, scrub it, see violations.
// Data: /api/playables (+ /{name}, POST /validate) from dashboard/playable_preview_server.py
// (or the console, if those routes are merged into server.py). Nothing here talks to the arm.
import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import URDFLoader from 'urdf-loader';

const $ = (id) => document.getElementById(id);
const JN = ['j1', 'j2', 'j3', 'j4', 'j5', 'j6'];
const COLORS = ['#ff8a3d', '#5cc8ff', '#3ecf8e', '#f2b134', '#c792ea', '#ff5d8f'];
const FINGER_MIN = -0.04695; // URDF finger prismatic limit (fully open), as in app.js

// API base: same origin, or the preview server when this page is opened from the console.
let API = '';
async function api(path, opts) {
  try {
    const r = await fetch(API + path, opts);
    if (r.status === 404 && !API && path === '/api/playables') throw new Error('no api here');
    return r;
  } catch (e) {
    if (API) throw e;
    API = 'http://127.0.0.1:8092';
    return fetch(API + path, opts);
  }
}

// ------------------------------------------------------------------ 3D
const viewer = $('viewer');
const renderer = new THREE.WebGLRenderer({ antialias: true });
renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
renderer.setClearColor(0x0a0d10);
viewer.appendChild(renderer.domElement);
const scene = new THREE.Scene();
const cam = new THREE.PerspectiveCamera(40, 1, 0.01, 20);
cam.position.set(0.95, 0.7, 0.95);
const controls = new OrbitControls(cam, renderer.domElement);
controls.target.set(0, 0.15, 0);
controls.enableDamping = true;
scene.add(new THREE.HemisphereLight(0xdde8ff, 0x202530, 1.6));
const sun = new THREE.DirectionalLight(0xffffff, 1.6);
sun.position.set(1, 2, 1.5);
scene.add(sun);
scene.add(new THREE.GridHelper(1.2, 24, 0x34404d, 0x1c232b));
const world = new THREE.Group();
world.rotation.x = -Math.PI / 2; // URDF Z-up -> three Y-up
scene.add(world);
world.add(new THREE.ArrowHelper(new THREE.Vector3(1, 0, 0), new THREE.Vector3(0, 0, 0.002), 0.35, 0xff8a3d, 0.05, 0.03));

const URDF_URL = '/urdf/i2rt/yam.urdf';
function loadRobot(materialFn, base) {
  return new Promise((resolve, reject) => {
    const loader = new URDFLoader();
    loader.packages = { yam_description: base + '/urdf' };
    const dflt = loader.defaultMeshLoader.bind(loader);
    loader.loadMeshCb = (path, manager, done) => {
      dflt(path, manager, (obj, err) => {
        done(obj, err);
        if (obj) obj.traverse((o) => { if (o.isMesh) materialFn(o); });
      });
    };
    loader.load(base + URDF_URL, resolve, undefined, reject);
  });
}
let robot = null, ghost = null;
const ghostMat = new THREE.MeshBasicMaterial({ color: 0x5cc8ff, transparent: true, opacity: 0.14, depthWrite: false });
const badMat = new THREE.MeshStandardMaterial({ color: 0xff5d5d, metalness: 0.1, roughness: 0.6 });
const normalMats = new Map();

function resize() {
  const w = viewer.clientWidth, h = viewer.clientHeight;
  renderer.setSize(w, h, false);
  cam.aspect = w / Math.max(h, 1);
  cam.updateProjectionMatrix();
}
new ResizeObserver(resize).observe(viewer);
resize();

// ------------------------------------------------------------------ state
let cur = null;          // {playable, validation, samples, envelope}
let tNow = 0, playing = false, lastFrame = null;

function setPose(r, q, grip) {
  if (!r) return;
  for (let i = 0; i < 6; i++) {
    const j = r.joints['joint' + (i + 1)];
    if (j) { j.ignoreLimits = true; j.setJointValue(q[i]); }
  }
  if (grip !== undefined && grip !== null) {
    r.setJointValue('joint7', FINGER_MIN * grip);
    r.setJointValue('joint8', FINGER_MIN * grip);
  }
}

function sampleAt(t) {
  const s = cur && cur.samples;
  if (!s) return null;
  const n = s.t.length;
  const f = Math.min(Math.max(t * s.rate, 0), n - 1);
  const i = Math.floor(f), k = Math.min(i + 1, n - 1), a = f - i;
  const q = s.q[i].map((v, j) => v + (s.q[k][j] - v) * a);
  const g = s.grip[i] + (s.grip[k] - s.grip[i]) * a;
  return { q, g };
}

function activeViolations(t) {
  if (!cur) return [];
  return cur.validation.violations.filter((v) => {
    const [a, b] = vSpan(v);
    return a !== null && t >= a - 0.02 && t <= b + 0.02;
  });
}

// playback time span of a violation (keyframe-level ones map to their keyframe time)
function vSpan(v) {
  if (v.t_play_span) return v.t_play_span;
  if (v.t_play !== null && v.t_play !== undefined) return [v.t_play, v.t_play];
  if (v.t !== null && v.t !== undefined && cur) {
    const off = cur.validation.transition ? cur.validation.transition.duration : 0;
    const tp = off + v.t / (cur.playable.speed || 1);
    return [tp, tp];
  }
  return [null, null];
}

function render() {
  if (!cur || !cur.samples) return;
  const D = cur.samples.duration;
  const s = sampleAt(tNow);
  setPose(robot, s.q, s.g);
  $('time').textContent = `${tNow.toFixed(2)} / ${D.toFixed(2)} s`;
  $('scrub').value = String(Math.round((tNow / D) * 1000));
  const act = activeViolations(tNow);
  const badJ = new Set(act.map((v) => v.joint));
  // joint table
  const soft = cur.envelope.soft_limits;
  JN.forEach((n, i) => {
    const row = $('jr' + i);
    if (!row) return;
    const [lo, hi] = soft[i];
    const vlo = lo - 0.3, vhi = hi + 0.3;
    row.querySelector('.nd').style.left = `${((s.q[i] - vlo) / (vhi - vlo)) * 100}%`;
    row.querySelector('.v').textContent = s.q[i].toFixed(3);
    row.querySelector('.v').className = 'v ' + (badJ.has(n) ? 'bad' : '');
  });
  $('jrg').querySelector('.v').textContent = s.g.toFixed(2);
  // tint the violating links red
  if (robot) {
    robot.traverse((o) => {
      if (!o.isMesh) return;
      if (!normalMats.has(o)) normalMats.set(o, o.material);
      o.material = normalMats.get(o);
    });
    for (const n of badJ) {
      if (!n || !n.startsWith('j')) continue;
      const link = robot.links['link' + n.slice(1)] || (n === 'j6' ? robot.links['gripper'] : null);
      if (link) paintOwn(link, badMat);
    }
  }
  $('banner').textContent = act.length ? `⚠ ${act.length} violation(s) now` : (cur.validation.ok ? 'valid' : '');
  $('banner').className = act.length ? 'bad' : (cur.validation.ok ? 'ok' : 'warn');
  drawTimeline();
}

(function loop(ts) {
  requestAnimationFrame(loop);
  if (playing && cur && cur.samples) {
    if (lastFrame !== null) tNow += ((ts - lastFrame) / 1000) * parseFloat($('rate').value);
    if (tNow >= cur.samples.duration) { tNow = cur.samples.duration; playing = false; $('play').textContent = 'Play'; }
    render();
  }
  lastFrame = ts;
  controls.update();
  renderer.render(scene, cam);
})(performance.now());

// ------------------------------------------------------------------ timeline
const tl = $('tl');
function drawTimeline() {
  const dpr = window.devicePixelRatio || 1;
  const W = tl.clientWidth, H = tl.clientHeight;
  if (tl.width !== W * dpr) { tl.width = W * dpr; tl.height = H * dpr; }
  const g = tl.getContext('2d');
  g.setTransform(dpr, 0, 0, dpr, 0, 0);
  g.clearRect(0, 0, W, H);
  g.fillStyle = '#0a0d10'; g.fillRect(0, 0, W, H);
  if (!cur || !cur.samples) return;
  const s = cur.samples, D = s.duration || 1, pad = 6;
  const X = (t) => (t / D) * W;
  const tr = cur.validation.transition;
  if (tr) { g.fillStyle = '#ffffff14'; g.fillRect(0, 0, X(tr.duration), H); }
  g.fillStyle = '#ff5d5d44';
  for (const v of cur.validation.violations) {
    const [a, b] = vSpan(v);
    if (a === null) continue;
    g.fillRect(X(a) - 1, 0, Math.max(2, X(b) - X(a) + 2), H);
  }
  // traces: deviation from the start pose, common auto scale; soft limits drawn when in view
  const q0 = s.q[0];
  let span = 0.2;
  for (const row of s.q) for (let j = 0; j < 6; j++) span = Math.max(span, Math.abs(row[j] - q0[j]));
  span *= 1.15;
  const Y = (d) => H / 2 - (d / span) * (H / 2 - pad);
  g.strokeStyle = '#262e38'; g.lineWidth = 1; g.beginPath(); g.moveTo(0, Y(0)); g.lineTo(W, Y(0)); g.stroke();
  g.fillStyle = '#7d8a99'; g.font = '10px system-ui';
  g.fillText(`+${span.toFixed(2)} rad`, 4, 12); g.fillText(`-${span.toFixed(2)} rad`, 4, H - 4);
  const soft = cur.envelope.soft_limits;
  g.setLineDash([6, 4]);
  JN.forEach((n, j) => {
    for (const lim of soft[j]) {
      const d = lim - q0[j];
      if (Math.abs(d) < span) { g.strokeStyle = COLORS[j]; g.beginPath(); g.moveTo(0, Y(d)); g.lineTo(W, Y(d)); g.stroke(); }
    }
  });
  g.setLineDash([]);
  JN.forEach((n, j) => {
    g.strokeStyle = COLORS[j]; g.lineWidth = 1.4; g.beginPath();
    s.t.forEach((t, i) => {
      const y = Y(s.q[i][j] - q0[j]);
      i ? g.lineTo(X(t), y) : g.moveTo(X(t), y);
    });
    g.stroke();
    g.fillStyle = COLORS[j]; g.fillText(n, W - 22 - (5 - j) * 22, 12);
  });
  // gripper (dashed, 0..1)
  g.setLineDash([3, 3]); g.strokeStyle = '#d7dee7aa'; g.beginPath();
  s.t.forEach((t, i) => { const y = H - pad - s.grip[i] * (H - 2 * pad) * 0.25; i ? g.lineTo(X(t), y) : g.moveTo(X(t), y); });
  g.stroke(); g.setLineDash([]);
  // keyframes
  const off = tr ? tr.duration : 0, sp = cur.playable.speed || 1, kf = cur.playable.keyframes || [];
  const loops = cur.playable.loop || 1, last = kf.length ? kf[kf.length - 1].t / sp : 0;
  g.fillStyle = '#d7dee7';
  for (let L = 0; L < loops; L++) for (const k of kf) {
    g.beginPath(); g.arc(X(off + L * last + k.t / sp), H - 3, 2.2, 0, 7); g.fill();
  }
  g.strokeStyle = '#fff'; g.lineWidth = 1.5; g.beginPath(); g.moveTo(X(tNow), 0); g.lineTo(X(tNow), H); g.stroke();
}
tl.addEventListener('click', (e) => {
  if (!cur || !cur.samples) return;
  const r = tl.getBoundingClientRect();
  tNow = ((e.clientX - r.left) / r.width) * cur.samples.duration;
  render();
});
new ResizeObserver(drawTimeline).observe(tl);

// ------------------------------------------------------------------ UI
$('play').onclick = () => {
  if (!cur || !cur.samples) return;
  if (!playing && tNow >= cur.samples.duration - 1e-3) tNow = 0;
  playing = !playing; $('play').textContent = playing ? 'Pause' : 'Play';
};
$('restart').onclick = () => { tNow = 0; render(); };
$('scrub').oninput = (e) => { if (cur && cur.samples) { tNow = (e.target.value / 1000) * cur.samples.duration; render(); } };
document.addEventListener('keydown', (e) => { if (e.code === 'Space' && e.target.tagName !== 'TEXTAREA') { e.preventDefault(); $('play').click(); } });

function show(bundle, label) {
  cur = bundle; tNow = 0; playing = false; $('play').textContent = 'Play';
  const p = bundle.playable, v = bundle.validation;
  $('title').textContent = label || p.name;
  $('notes').textContent = [p.notes, p.prompt ? `prompt: “${p.prompt}”` : '', p.author ? `by ${p.author}` : ''].filter(Boolean).join(' · ');
  $('envSrc').textContent = `envelope: ${bundle.envelope.source}`;
  $('stats').innerHTML = `<b class="${v.ok ? 'ok' : 'bad'}">${v.ok ? 'VALID' : 'REJECTED'}</b> · ${v.duration ?? '—'} s · loop ${p.loop ?? 1} · speed ${p.speed ?? 1}` +
    (v.transition ? ` · <span class="warn">+${v.transition.duration}s transition from ${v.transition.from}</span>` : '');
  const vc = bundle.envelope.vel_cap, ac = bundle.envelope.acc_cap;
  $('jt').innerHTML = '<tr class="muted"><td></td><td>q</td><td>soft box</td><td title="peak velocity / acceleration as % of cap">v% a%</td></tr>' + JN.map((n, i) => {
    const [lo, hi] = bundle.envelope.soft_limits[i], vlo = lo - 0.3, vhi = hi + 0.3;
    const pv = v.max_vel ? Math.round((v.max_vel[i] / vc[i]) * 100) : null, pa = v.max_acc ? Math.round((v.max_acc[i] / ac[i]) * 100) : null;
    return `<tr id="jr${i}"><td style="color:${COLORS[i]}">${n}</td><td class="v">—</td>
      <td><div class="jbar"><div class="soft" style="left:${((lo - vlo) / (vhi - vlo)) * 100}%;right:${((vhi - hi) / (vhi - vlo)) * 100}%"></div><div class="nd"></div></div></td>
      <td><span class="${pv > 100 ? 'bad' : ''}">${pv ?? '—'}</span> <span class="${pa > 100 ? 'bad' : ''}">${pa ?? '—'}</span></td></tr>`;
  }).join('') + '<tr id="jrg"><td>grip</td><td class="v">—</td><td class="muted small" colspan="2">0 closed · 1 open</td></tr>' +
    `<tr class="muted small"><td colspan="4">caps: ${vc[0]} rad/s, ${ac[0]} rad/s²</td></tr>`;
  $('viol').innerHTML = v.violations.length ? v.violations.slice(0, 200).map((x, i) =>
    `<li data-i="${i}"><span class="bad">[${x.kind}]</span> ${escapeHtml(x.msg)}</li>`).join('') : '<li class="ok">none</li>';
  $('viol').querySelectorAll('li[data-i]').forEach((li) => li.onclick = () => {
    const [a] = vSpan(v.violations[+li.dataset.i]); if (a !== null) { tNow = a; render(); }
  });
  if (ghost && bundle.envelope.named_poses) setPose(ghost, bundle.envelope.named_poses[p.start] || bundle.envelope.named_poses.ready, 0);
  if (bundle.samples) render(); else { drawTimeline(); $('time').textContent = 'cannot sample (schema errors)'; }
}
// paint the meshes of one link only (stop at child joints, which carry the next links)
function paintOwn(obj, mat) {
  for (const c of obj.children) {
    if (c.isURDFJoint) continue;
    if (c.isMesh) c.material = mat;
    paintOwn(c, mat);
  }
}
function escapeHtml(s) { return String(s).replace(/[&<>]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;' })[c]); }

async function loadList(select) {
  const r = await api('/api/playables');
  const items = await r.json();
  $('list').innerHTML = items.map((it) => `<li data-n="${it.name}"><span class="st ${it.ok ? 'ok' : 'bad'}">${it.ok ? '✓' : '✗ ' + (it.n_violations ?? '')} ${it.duration ? it.duration + 's' : ''}</span>
    <div class="nm">${it.name}</div><div class="small muted">${escapeHtml(it.author || '')}</div></li>`).join('') || '<li class="muted">no playables</li>';
  $('list').querySelectorAll('li[data-n]').forEach((li) => li.onclick = () => open(li.dataset.n));
  const want = select || new URLSearchParams(location.search).get('p') || (items[0] && items[0].name);
  if (want) open(want);
}
async function open(name) {
  $('list').querySelectorAll('li').forEach((li) => li.classList.toggle('sel', li.dataset.n === name));
  const r = await api('/api/playables/' + encodeURIComponent(name));
  if (!r.ok) return;
  show(await r.json());
  history.replaceState(null, '', '?p=' + encodeURIComponent(name));
}
$('pasteBtn').onclick = async () => {
  let pl;
  try { pl = JSON.parse($('paste').value); } catch (e) { $('pasteMsg').textContent = 'not JSON: ' + e.message; return; }
  const r = await api('/api/playables/validate', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(pl) });
  if (!r.ok) { $('pasteMsg').textContent = 'error ' + r.status + ': ' + (await r.text()); return; }
  $('pasteMsg').textContent = '';
  $('list').querySelectorAll('li').forEach((li) => li.classList.remove('sel'));
  show(await r.json(), (pl.name || 'pasted') + ' (pasted)');
};

// boot: list first (decides API base), then URDF from the same base
loadList().catch((e) => { $('list').innerHTML = `<li class="bad">cannot reach the playable API: ${e.message}</li>`; })
  .finally(() => {
    Promise.all([
      loadRobot((m) => {
        const c = m.material && m.material.color ? m.material.color.clone() : new THREE.Color(0xaaaaaa);
        m.material = new THREE.MeshStandardMaterial({ color: c, metalness: 0.15, roughness: 0.6 });
      }, API),
      loadRobot((m) => { m.material = ghostMat; m.renderOrder = 2; }, API),
    ]).then(([r, gh]) => {
      robot = r; ghost = gh; world.add(robot); world.add(ghost);
      $('viewerMsg').remove();
      if (cur) show(cur, $('title').textContent);
    }).catch((e) => { $('viewerMsg').textContent = 'URDF load failed: ' + (e && e.message ? e.message : e); });
  });
