// Shared 3D arm view (three.js + urdf-loader): one renderer, orbit controls, floor grid,
// +x arrow, the arm, and optionally a ghost of the home pose. Used by the console (real and
// simulated arm side by side) and the /sim page. Colours follow the page theme.
import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import URDFLoader from 'urdf-loader';

export const URDF_URL = '/urdf/i2rt/yam.urdf';
export const FINGER_MIN = -0.04695; // URDF finger prismatic limit (fully open)
const cssVar = (n) => (window.openyamTheme ? window.openyamTheme.cssVar(n) : '') || null;

export function loadRobot(materialFn) {
  return new Promise((resolve, reject) => {
    const loader = new URDFLoader();
    loader.packages = { yam_description: '/urdf' };
    const base = loader.defaultMeshLoader.bind(loader);
    loader.loadMeshCb = (path, manager, done) => {
      base(path, manager, (obj, err) => {
        done(obj, err); // urdf-loader assigns the URDF <material> here
        if (obj) obj.traverse((o) => { if (o.isMesh) materialFn(o); }); // then we override it
      });
    };
    loader.load(URDF_URL, resolve, undefined, reject);
  });
}

export const solidMaterial = (tint) => (m) => {
  const c = tint ? new THREE.Color(tint) : (m.material && m.material.color ? m.material.color.clone() : new THREE.Color(0xaaaaaa));
  m.material = new THREE.MeshStandardMaterial({ color: c, metalness: 0.15, roughness: 0.6 });
};
export const ghostMaterial = (color, opacity) => {
  const mat = new THREE.MeshBasicMaterial({ color, transparent: true, opacity, depthWrite: false });
  return (m) => { m.material = mat; m.renderOrder = 2; };
};

// createArmView(el, {armMaterial, ghost: true, label}) -> {robot (promise), ghost, scene, camera, controls, renderer, world, onFrame}
export function createArmView(el, opts = {}) {
  const renderer = new THREE.WebGLRenderer({ antialias: true });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
  el.appendChild(renderer.domElement);
  const scene = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera(40, 1, 0.01, 20);
  camera.position.set(0.95, 0.7, 0.95);
  const controls = new OrbitControls(camera, renderer.domElement);
  controls.target.set(0, 0.15, 0);
  controls.enableDamping = true;
  controls.update();
  scene.add(new THREE.HemisphereLight(0xdde8ff, 0x202530, 1.6));
  const sun = new THREE.DirectionalLight(0xffffff, 1.6);
  sun.position.set(1, 2, 1.5);
  scene.add(sun);

  let grid = null;
  function applyTheme() {
    renderer.setClearColor(new THREE.Color(cssVar('--sunken') || '#0a0d10'));
    if (grid) { scene.remove(grid); grid.geometry.dispose(); grid.material.dispose(); }
    grid = new THREE.GridHelper(1.2, 24, new THREE.Color(cssVar('--grid-major') || '#34404d'), new THREE.Color(cssVar('--grid-minor') || '#1c232b'));
    scene.add(grid);
  }
  applyTheme();
  window.addEventListener('themechange', applyTheme);

  // URDF is Z-up; three is Y-up. Everything robot-related lives in `world`.
  const world = new THREE.Group();
  world.rotation.x = -Math.PI / 2;
  scene.add(world);
  world.add(new THREE.ArrowHelper(new THREE.Vector3(1, 0, 0), new THREE.Vector3(0, 0, 0.002), 0.35, 0xff8a3d, 0.05, 0.03));
  {
    const c = document.createElement('canvas'); c.width = 256; c.height = 64;
    const g = c.getContext('2d'); g.fillStyle = '#ff8a3d'; g.font = 'bold 34px system-ui'; g.fillText('+x forward', 6, 44);
    const sp = new THREE.Sprite(new THREE.SpriteMaterial({ map: new THREE.CanvasTexture(c), depthTest: false }));
    sp.scale.set(0.16, 0.04, 1); sp.position.set(0.42, 0, 0.03);
    world.add(sp);
  }

  const view = { el, renderer, scene, camera, controls, world, robot: null, ghost: null, frameHooks: [] };
  view.ready = Promise.all([
    loadRobot(opts.armMaterial || solidMaterial()),
    opts.ghost === false ? Promise.resolve(null) : loadRobot(ghostMaterial(0x5cc8ff, 0.16)),
  ]).then(([robot, ghost]) => {
    view.robot = robot; view.ghost = ghost;
    world.add(robot);
    if (ghost) world.add(ghost);
    return view;
  });

  function resize() {
    const w = el.clientWidth, h = el.clientHeight;
    renderer.setSize(w, h, false);
    camera.aspect = w / Math.max(h, 1);
    camera.updateProjectionMatrix();
  }
  new ResizeObserver(resize).observe(el);
  resize();
  (function animate() {
    requestAnimationFrame(animate);
    controls.update();
    renderer.render(scene, camera);
    for (const f of view.frameHooks) f();
  })();
  return view;
}

// Set joints 1..6 from an array (rad), shown even beyond the URDF limits; grip 0 (closed)..1 (open).
export function setArmPose(robot, q, grip) {
  if (!robot) return;
  q.forEach((v, i) => {
    const j = robot.joints['joint' + (i + 1)];
    if (j) { j.ignoreLimits = true; j.setJointValue(v); }
  });
  if (grip !== undefined && grip !== null) {
    const g = FINGER_MIN * Math.min(1, Math.max(0, grip));
    robot.setJointValue('joint7', g);
    robot.setJointValue('joint8', g);
  }
}

// Keep two views' orbit cameras in sync while `enabled()` is true.
export function syncViews(a, b, enabled) {
  let busy = false;
  const link = (src, dst) => src.controls.addEventListener('change', () => {
    if (busy || !enabled()) return;
    busy = true;
    dst.camera.position.copy(src.camera.position);
    dst.controls.target.copy(src.controls.target);
    dst.controls.update();
    busy = false;
  });
  link(a, b); link(b, a);
}

// Text for the simulated-arm headers (console panel and /sim) from armctl's /status.
// -> {on, driver, detail, cls}. on = mock backend with a state; anything else is not a sim.
export function describeSim(s) {
  if (!s) return { on: false, driver: '–', detail: 'armctl daemon not reachable', cls: 'bad' };
  const st = s.state || {};
  if (s.backend !== 'mock') return { on: false, driver: '–', detail: `backend "${s.backend}" is not the mock: nothing simulated here`, cls: 'warn' };
  const h = s.lease || {};
  const driver = h.holder ? `${h.holder}${h.name ? ' — ' + h.name : ''}` : (h.switching ? 'switching…' : 'nobody (holding still)');
  const bits = [];
  if (s.estop) bits.push('E-STOP latched');
  bits.push(st.torque === false ? 'relaxed (torque off)' : (st.mode || 'idle'));
  if (st.busy) bits.push(st.busy);
  if (st.streaming) bits.push('streaming');
  if (s.pending_play) bits.push(`playable "${s.pending_play.name}" waiting for Play`);
  return { on: Array.isArray(st.q), driver, detail: bits.join(' · '), cls: s.estop ? 'bad' : (st.moving || st.streaming ? 'warn' : 'ok'), q: st.q, grip: st.grip };
}
