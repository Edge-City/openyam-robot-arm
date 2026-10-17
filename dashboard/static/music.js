// Music -> dance: analysis view, choreography timeline and a 3D preview in sync with the song.
// Data: same-origin /api/music/* (dashboard/music_routes.py, mounted by the console or by
// dashboard/music_server.py). Nothing here talks to the arm.
import { createArmView, setArmPose } from '/static/arm3d.js';

const $ = (id) => document.getElementById(id);
const cssVar = (n, d) => (window.openyamTheme && window.openyamTheme.cssVar(n)) || d;
const READY = [0, 0.5, 0.8, 0, 0, 0];
const MOTIF_COLORS = {
  hold: '#7d8a99', nod: '#5cc8ff', bounce: '#3ecf8e', sway: '#f2b134', pump: '#ff8a3d', sweep: '#c792ea',
  'wrist-roll': '#4dd0c8', 'wrist-wave': '#9ccc65', 'up-side': '#ff5d8f', hit: '#ff5d5d', freeze: '#b0bec5', breathe: '#82aaff',
};
const SECTION_COLORS = { intro: '#5cc8ff', verse: '#3ecf8e', chorus: '#ff8a3d', drop: '#ff5d8f', break: '#c792ea', outro: '#82aaff' };

// The nav points at the console (:8090) when this page is served by the standalone runner.
if (location.port === '8093') {
  document.querySelectorAll('a[data-console]').forEach((a) => { a.href = 'http://127.0.0.1:8090' + a.getAttribute('href'); });
}

const S = { slug: '', an: null, res: null, t: 0, playing: false, clockStart: 0, drag: null, busy: false };
const audio = new Audio();
audio.preload = 'auto';

// ------------------------------------------------------------------ 3D
const view = createArmView($('viewer'), { ghost: false });
view.camera.position.set(1.15, 0.8, 1.15);  // a little further back than the console: dances reach up
view.controls.target.set(0, 0.25, 0);
view.controls.update();
view.ready.then(() => { $('viewerMsg').textContent = ''; setArmPose(view.robot, READY, 0); })
  .catch((e) => { $('viewerMsg').textContent = 'URDF failed to load: ' + e; });

// ------------------------------------------------------------------ helpers
async function api(path, opts) {
  const r = await fetch(path, opts);
  const txt = await r.text();
  let data = null;
  try { data = JSON.parse(txt); } catch (e) { /* not JSON */ }
  if (!r.ok) throw new Error((data && data.detail) || txt || r.statusText);
  return data;
}
function msg(text, cls) { const m = $('msg'); m.textContent = text || ''; m.className = 'small ' + (cls || ''); }
const win = () => (S.res ? S.res.report.window : S.an ? S.an.excerpt : null);
const dur = () => (S.res ? S.res.samples.t[S.res.samples.t.length - 1] : 0);

function formatPlan(p) {
  const head = Object.entries(p).filter(([k]) => k !== 'segments').map(([k, v]) => ` ${JSON.stringify(k)}: ${JSON.stringify(v)}`);
  const segs = (p.segments || []).map((s) => '  ' + JSON.stringify(s));
  return '{\n' + head.join(',\n') + ',\n "segments": [\n' + segs.join(',\n') + '\n ]\n}\n';
}

function setupCanvas(c) {
  const dpr = Math.min(window.devicePixelRatio || 1, 2);
  const w = c.clientWidth, h = c.clientHeight;
  if (c.width !== Math.round(w * dpr) || c.height !== Math.round(h * dpr)) { c.width = Math.round(w * dpr); c.height = Math.round(h * dpr); }
  const g = c.getContext('2d');
  g.setTransform(dpr, 0, 0, dpr, 0, 0);
  return { g, w, h };
}

// ------------------------------------------------------------------ drawing
function drawOverview() {
  const { g, w, h } = setupCanvas($('overview'));
  g.fillStyle = cssVar('--sunken', '#0a0d10'); g.fillRect(0, 0, w, h);
  const an = S.an;
  if (!an) return;
  const X = (t) => (t / an.duration) * w;
  for (const s of an.sections) {   // section bands
    g.fillStyle = (SECTION_COLORS[s.label] || '#7d8a99') + '2e';
    g.fillRect(X(s.start), 0, X(s.end) - X(s.start), h);
    g.fillStyle = cssVar('--muted', '#7d8a99'); g.font = '10px system-ui';
    g.fillText(`${s.label} ${s.energy.toFixed(2)}`, X(s.start) + 3, 11);
  }
  const pk = an.waveform.peaks, mid = h * 0.55;   // waveform
  g.fillStyle = cssVar('--muted', '#7d8a99') + '88';
  for (let i = 0; i < pk.length; i++) {
    const x = X(i * an.waveform.dt), a = pk[i] * h * 0.32;
    g.fillRect(x, mid - a, Math.max(1, w / pk.length), 2 * a);
  }
  g.strokeStyle = cssVar('--ok', '#3ecf8e'); g.lineWidth = 1.5; g.beginPath();   // energy
  an.beats.forEach((t, i) => { const y = h - 4 - an.beat_energy[i] * (h - 22); i ? g.lineTo(X(t), y) : g.moveTo(X(t), y); });
  g.stroke();
  g.strokeStyle = cssVar('--text', '#d7dee7') + '66'; g.lineWidth = 1;   // downbeats
  for (const t of an.downbeats) { g.beginPath(); g.moveTo(X(t), h - 8); g.lineTo(X(t), h); g.stroke(); }
  g.fillStyle = cssVar('--warn', '#f2b134');   // key moments
  for (const m of an.key_moments) { const x = X(m.t); g.beginPath(); g.moveTo(x, 14); g.lineTo(x + 5, 19); g.lineTo(x, 24); g.lineTo(x - 5, 19); g.fill(); }
  const W = win();   // excerpt window (dragged preview if dragging)
  if (W) {
    let a = W.start, b = W.end;
    if (S.drag && S.drag.moved) { const d = S.drag.dt; a += d; b += d; }
    g.fillStyle = cssVar('--accent', '#5cc8ff') + '22'; g.fillRect(X(a), 0, X(b) - X(a), h);
    g.strokeStyle = cssVar('--accent', '#5cc8ff'); g.lineWidth = 2; g.strokeRect(X(a) + 1, 1, X(b) - X(a) - 2, h - 2);
    if (S.res) {
      const x = X(W.start + S.t);
      g.strokeStyle = cssVar('--bad', '#ff5d5d'); g.lineWidth = 2; g.beginPath(); g.moveTo(x, 0); g.lineTo(x, h); g.stroke();
    }
  }
}

function drawDetail() {
  const { g, w, h } = setupCanvas($('detail'));
  g.fillStyle = cssVar('--sunken', '#0a0d10'); g.fillRect(0, 0, w, h);
  const an = S.an, W = win();
  if (!an || !W) return;
  const t0 = W.start, span = Math.max(0.01, W.end - W.start);
  const X = (t) => ((t - t0) / span) * (w - 2) + 1;
  const bt = an.beats;
  if (S.res) {   // motif blocks
    for (const b of S.res.report.blocks) {
      const a = bt[W.start_beat + b.start_beat], e = bt[W.start_beat + b.end_beat];
      const c = MOTIF_COLORS[b.motif] || '#999';
      g.fillStyle = c + '55'; g.fillRect(X(a), 30, X(e) - X(a), h - 48);
      g.strokeStyle = c; g.lineWidth = 1; g.strokeRect(X(a) + 0.5, 30.5, X(e) - X(a) - 1, h - 49);
      g.fillStyle = cssVar('--text', '#d7dee7'); g.font = '11px system-ui';
      const label = `${b.motif} ${b.amplitude.toFixed(2)}`;
      if (X(e) - X(a) > g.measureText(label).width + 6) g.fillText(label, X(a) + 4, 45);
      else if (X(e) - X(a) > 16) g.fillText(b.motif.slice(0, 3), X(a) + 3, 45);
    }
  }
  const db = new Set(an.downbeat_beats);   // beat grid
  for (let i = W.start_beat; i <= W.end_beat; i++) {
    const x = X(bt[i]);
    g.strokeStyle = cssVar('--text', '#d7dee7') + (db.has(i) ? 'cc' : '55');
    g.lineWidth = db.has(i) ? 1.5 : 1;
    g.beginPath(); g.moveTo(x, 0); g.lineTo(x, db.has(i) ? 26 : 14); g.stroke();
    if (db.has(i) && (i - W.start_beat) % 8 === 0) {
      g.fillStyle = cssVar('--muted', '#7d8a99'); g.font = '10px system-ui'; g.fillText(String(i - W.start_beat), x + 2, 24);
    }
  }
  g.strokeStyle = cssVar('--ok', '#3ecf8e'); g.lineWidth = 1.5; g.beginPath();   // energy in the window
  for (let i = W.start_beat; i <= W.end_beat; i++) { const y = h - 3 - an.beat_energy[i] * 14; i > W.start_beat ? g.lineTo(X(bt[i]), y) : g.moveTo(X(bt[i]), y); }
  g.stroke();
  if (S.res) {
    const x = X(t0 + S.t);
    g.strokeStyle = cssVar('--bad', '#ff5d5d'); g.lineWidth = 2; g.beginPath(); g.moveTo(x, 0); g.lineTo(x, h); g.stroke();
  }
}

function drawAll() { drawOverview(); drawDetail(); }
window.addEventListener('resize', drawAll);
window.addEventListener('themechange', drawAll);

// ------------------------------------------------------------------ playback
function poseAt(t) {
  if (!S.res) return READY;
  const s = S.res.samples, i = Math.max(0, Math.min(s.q.length - 1, Math.round(t * s.rate)));
  return s.q[i];
}
function currentBlock(t) {
  if (!S.res || !S.an) return null;
  const W = win(), bt = S.an.beats, abs = W.start + t;
  return S.res.report.blocks.find((b) => abs >= bt[W.start_beat + b.start_beat] - 1e-6 && abs < bt[W.start_beat + b.end_beat]) || null;
}
function beatIndex(t) {
  const W = win(), bt = S.an.beats, abs = W.start + t;
  let i = W.start_beat;
  while (i < W.end_beat && bt[i + 1] <= abs + 1e-6) i++;
  return i;
}
function showTime() {
  $('time').textContent = `${S.t.toFixed(2)} / ${dur().toFixed(2)} s`;
  if (S.res) {
    const bi = beatIndex(S.t), W = win(), rel = bi - W.start_beat;
    $('beatInfo').textContent = `beat ${rel} (bar ${Math.floor(rel / 4) + 1}.${(rel % 4) + 1}) · song ${(W.start + S.t).toFixed(2)} s`;
    const b = currentBlock(S.t);
    $('nowMotif').textContent = b ? b.motif : '';
  }
}
function play() {
  if (!S.res) return;
  if (S.t >= dur() - 0.01) S.t = 0;
  S.playing = true;
  $('playBtn').textContent = '⏸ Pause';
  S.clockStart = performance.now() / 1000 - S.t;
  try {
    audio.currentTime = win().start + S.t;
    const p = audio.play();
    if (p && p.catch) p.catch((e) => msg('audio did not start (' + e.message + '); playing without sound', 'warn'));
  } catch (e) { msg('audio: ' + e, 'warn'); }
}
function pause() {
  S.playing = false;
  audio.pause();
  $('playBtn').textContent = '▶ Play';
}
function seek(t) {
  S.t = Math.max(0, Math.min(dur(), t));
  if (S.playing) { audio.currentTime = win().start + S.t; S.clockStart = performance.now() / 1000 - S.t; }
  update();
}
function update() {
  if (view.robot) setArmPose(view.robot, poseAt(S.t), 0);
  showTime();
  drawAll();
}
(function loop() {
  requestAnimationFrame(loop);
  if (!S.playing || !S.res) return;
  const W = win();
  const audioOk = !audio.paused && audio.readyState >= 2;
  S.t = audioOk ? audio.currentTime - W.start : performance.now() / 1000 - S.clockStart;
  if (S.t >= dur()) { S.t = dur(); pause(); }
  update();
})();
$('playBtn').onclick = () => (S.playing ? pause() : play());
$('rewBtn').onclick = () => seek(0);

// ------------------------------------------------------------------ data
async function loadSongs(select) {
  const songs = await api('/api/music/songs');
  const sel = $('song');
  sel.innerHTML = '';
  if (!songs.length) sel.innerHTML = '<option value="">(no songs in music/inbox)</option>';
  for (const s of songs) {
    const o = document.createElement('option');
    o.value = s.slug;
    o.textContent = s.file + (s.analyzed ? ` · ${s.tempo_bpm.toFixed(1)} BPM · ${s.duration.toFixed(0)} s` : ' · not analyzed');
    sel.appendChild(o);
  }
  const want = select || new URLSearchParams(location.search).get('song');
  if (want && songs.some((s) => s.slug === want)) sel.value = want;
  if (sel.value) await selectSong(sel.value);
}

async function selectSong(slug) {
  pause();
  S.slug = slug; S.an = null; S.res = null; S.t = 0;
  $('anSum').textContent = 'analyzing…';
  $('start').value = '';
  drawAll();
  try {
    S.an = await api(`/api/music/analysis/${encodeURIComponent(slug)}`);
  } catch (e) { $('anSum').textContent = 'analysis failed: ' + e.message; return; }
  $('anSum').textContent = S.an.summary;
  audio.src = `/api/music/audio/${encodeURIComponent(slug)}`;
  $('saveName').value = `${slug}-${$('style').value}`.slice(0, 64);
  drawAll();
  await choreograph();
}

async function choreograph(plan) {
  if (!S.slug) return;
  if (S.busy) { S.pending = { plan }; return; }  // run the latest request after this one
  S.busy = true;
  pause();
  msg('choreographing…');
  const body = { song: S.slug, style: $('style').value, seed: Number($('seed').value || 0) };
  if (plan) body.plan = plan;
  else if ($('start').value !== '') body.start = Number($('start').value);
  try {
    S.res = await api('/api/music/choreograph', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
    S.t = 0;
    $('plan').value = formatPlan(S.res.plan);
    showResult();
    msg(plan ? 'plan applied' : '');
  } catch (e) { msg(e.message, 'bad'); } finally { S.busy = false; }
  $('playBtn').disabled = $('rewBtn').disabled = !S.res;
  update();
  if (S.pending) { const p = S.pending; S.pending = null; await choreograph(p.plan); }
}

function showResult() {
  const r = S.res.report, v = r.validation, f = r.fk || {};
  const fkTxt = !f.available ? '<span class="warn">FK unavailable</span>'
    : f.ok ? `<span class="ok">FK ok</span> (tip ≥ ${f.min_tip_z.toFixed(3)} m)` : `<span class="bad">FK too low</span> (tip ${f.min_tip_z.toFixed(3)} m)`;
  $('status').innerHTML = `${v.ok ? '<span class="ok">valid</span>' : '<span class="bad">REJECTED</span>'} at the dance caps · ${v.duration} s · `
    + `max ${Math.max(...v.max_vel).toFixed(2)} rad/s, ${Math.max(...v.max_acc).toFixed(2)} rad/s² · ${fkTxt}`
    + `<br>${S.an.tempo_bpm.toFixed(1)} BPM · window ${r.window.start.toFixed(2)}–${r.window.end.toFixed(2)} s · ${r.window.n_beats} beats`;
  const rows = r.blocks.map((b) => `<tr title="${(b.note || '').replace(/"/g, '&quot;')}"><td>${b.start_beat}–${b.end_beat}</td>`
    + `<td><span class="sw" style="background:${MOTIF_COLORS[b.motif] || '#999'}"></span>${b.motif}</td>`
    + `<td>${b.step_beats}b</td><td>${b.amplitude.toFixed(2)}${b.limited_by === 'caps' ? '*' : ''}</td></tr>`).join('');
  $('blocks').innerHTML = `<tr><th>beats</th><th>motif</th><th>step</th><th>amp</th></tr>${rows}`;
  $('saveBtn').disabled = !r.ok;
  $('cmd').textContent = 'Save first: writes the playable, plan and audio clip.';
  if ($('start').value === '') $('start').placeholder = `${r.window.start.toFixed(2)} (suggested)`;
}

// ------------------------------------------------------------------ controls
$('song').onchange = () => selectSong($('song').value);
$('style').onchange = () => { if (S.slug) $('saveName').value = `${S.slug}-${$('style').value}`.slice(0, 64); choreograph(); };
$('seed').onchange = () => choreograph();
$('start').onchange = () => choreograph();
$('choreoBtn').onclick = () => choreograph();
$('suggestBtn').onclick = () => { $('start').value = ''; choreograph(); };
$('applyBtn').onclick = () => {
  let p;
  try { p = JSON.parse($('plan').value); } catch (e) { msg('plan is not valid JSON: ' + e.message, 'bad'); return; }
  choreograph(p);
};
$('motifHelp').textContent = 'motifs: …';
api('/api/music/motifs').then((m) => {
  $('motifHelp').textContent = 'motifs: ' + m.motifs.map((x) => x.name).join(', ');
  $('motifHelp').title = m.doc;
}).catch(() => {});

$('saveBtn').onclick = async () => {
  if (!S.res) return;
  const name = $('saveName').value.trim();
  try {
    const r = await api('/api/music/choreograph', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ song: S.slug, style: $('style').value, plan: S.res.plan, save: name }),
    });
    S.res = r;
    showResult();
    $('cmd').textContent = r.report.command;
    msg('saved: ' + r.files.join(', '), 'ok');
    update();
  } catch (e) { msg(e.message, 'bad'); }
};
$('copyBtn').onclick = async () => {
  const text = $('cmd').textContent;
  try { await navigator.clipboard.writeText(text); msg('command copied', 'ok'); } catch (e) {
    const r = document.createRange(); r.selectNodeContents($('cmd'));
    const s = getSelection(); s.removeAllRanges(); s.addRange(r); msg('selected: press Ctrl+C', 'warn');
  }
};

$('uploadBtn').onclick = async () => {
  const f = $('file').files[0];
  if (!f) { $('uploadMsg').textContent = 'choose a file first'; return; }
  if (f.size > 30 * 1024 * 1024) { $('uploadMsg').textContent = 'larger than 30 MB'; return; }
  $('uploadMsg').textContent = `uploading and analyzing ${f.name}…`;
  try {
    const r = await api('/api/music/upload?filename=' + encodeURIComponent(f.name), {
      method: 'POST', headers: { 'Content-Type': f.type || 'application/octet-stream' }, body: f,
    });
    $('uploadMsg').textContent = `${r.file}: ${r.tempo_bpm.toFixed(1)} BPM, ${r.duration.toFixed(0)} s`;
    await loadSongs(r.slug);
  } catch (e) { $('uploadMsg').textContent = 'upload failed: ' + e.message; }
};

// timeline interaction: drag the excerpt box on the overview; click to seek
function songTimeFromEvent(ev, canvas) {
  const r = canvas.getBoundingClientRect();
  return ((ev.clientX - r.left) / r.width) * S.an.duration;
}
$('overview').addEventListener('pointerdown', (ev) => {
  if (!S.an) return;
  const t = songTimeFromEvent(ev, $('overview')), W = win();
  S.drag = { x0: ev.clientX, t0: t, dt: 0, moved: false, inside: W && t >= W.start && t <= W.end };
  $('overview').setPointerCapture(ev.pointerId);
});
$('overview').addEventListener('pointermove', (ev) => {
  if (!S.drag || !S.drag.inside) return;
  const t = songTimeFromEvent(ev, $('overview'));
  if (Math.abs(ev.clientX - S.drag.x0) > 4) S.drag.moved = true;
  const W = win();
  S.drag.dt = Math.max(-W.start, Math.min(S.an.duration - W.end, t - S.drag.t0));
  drawOverview();
});
$('overview').addEventListener('pointerup', (ev) => {
  const d = S.drag; S.drag = null;
  if (!d || !S.an) return;
  const W = win();
  if (d.moved) {
    const want = W.start + d.dt;
    const snap = S.an.downbeats.reduce((a, b) => (Math.abs(b - want) < Math.abs(a - want) ? b : a));
    $('start').value = snap.toFixed(2);
    choreograph();
  } else if (d.inside && S.res) {
    seek(d.t0 - W.start);
  } else {
    drawOverview();
  }
});
$('detail').addEventListener('click', (ev) => {
  if (!S.res) return;
  const r = $('detail').getBoundingClientRect(), W = win();
  seek(((ev.clientX - r.left) / r.width) * (W.end - W.start));
});
document.addEventListener('keydown', (ev) => {
  if (ev.code === 'Space' && !['TEXTAREA', 'INPUT', 'SELECT', 'BUTTON'].includes(document.activeElement.tagName)) {
    ev.preventDefault(); S.playing ? pause() : play();
  }
});

loadSongs().catch((e) => { $('anSum').textContent = 'could not list songs: ' + e.message; });
