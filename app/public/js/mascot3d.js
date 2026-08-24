/**
 * RUNECLAW 3D agent — a real-time WebGL viewer for the AI Viking agent model.
 *
 * Loads a glTF-binary model from /mascot/agent.glb (which brings its own
 * rune-disc base + holographics) and lights it with room-environment PBR plus a
 * white key and a rune-blue rim/fill, with gentle auto-rotation and
 * drag-to-orbit. It plays the model's idle animation clip if it has one,
 * otherwise a soft idle bob. Framing is measured from the true SKINNED pose
 * (see skinnedUnionBox) so a rigged model that floats/spreads its arms stays
 * fully in shot. It pauses when off-screen or the tab is hidden, honours
 * prefers-reduced-motion, and — crucially — does NOTHING when no model is
 * present yet, so shipping this never changes the site until the artwork
 * (agent.glb) is dropped in.
 *
 * Two modes:
 *   • showcase (default) — the full character, drag-to-orbit, gentle auto-rotate
 *     (landing hero, /agent page).
 *   • avatar (opts.mode==='avatar' or [data-rc-agent3d="avatar"]) — a compact
 *     head-and-chest bust that faces the viewer and REACTS: it plays 'analyze'
 *     while the chat is thinking and one-shot 'alert'/'execute' on live signals
 *     and fills, then settles back to idle. Used in the dashboard chat header
 *     and Agent Hub.
 *
 * Reactive API (drive every mounted agent at once):
 *   window.RCAgent3D.react('analyze'|'alert'|'execute')   // one-shot then idle
 *   window.RCAgent3D.setThinking(true|false)               // hold 'analyze'
 *   window.RCAgent3D.mountIfAvailable(host, {mode})        // SPA dynamic mount
 *   window.RCAgent3D.disposeAll()                          // free GL on view change
 *
 * Mount targets: any element with [data-rc-agent3d]. Self-contained: three.js is
 * vendored under /vendor/three (no CDN); the page must declare the import map.
 */
import * as THREE from 'three';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { RoomEnvironment } from 'three/addons/environments/RoomEnvironment.js';

/**
 * WHICH MODEL, AND WHY THIS ONE.
 *
 * This pointed at `agent.glb`, which is byte-identical to the `_Luminous`
 * variant — and Luminous is the one built to glow twice as hard as every other
 * cut of the same model:
 *
 *     variant    emissive materials   max emissive strength   size
 *     base                        3                       5   703 KB
 *     Premium                     3                       5   963 KB
 *     Hero                        4                       5   1.3 MB
 *     Luminous                    4                      10   1.5 MB   <- was live
 *
 * On a landing page for a trading product, the loudest and heaviest cut was
 * shipping to every visitor. "It looks like too much" was not a matter of
 * taste; it was the one asset with double the glow, and it was also the one
 * costing the most to download. Premium is the same calm lighting as base with
 * the better texture work, and 523 KB lighter than what it replaces.
 *
 * The alternates stay committed. Swapping is this one line plus its entry in
 * `mascot_assets.test.js`, which pins the glow budget so a future edit cannot
 * quietly point back at Luminous.
 */
const MODEL_URL = '/mascot/RUNECLAW_Command_Core_Mascot_Premium.glb?v=1';
const RUNE = 0x3fb6ff;
const _instances = new Set();   // every live viewer, for global react()/disposeAll()

async function modelExists() {
  try {
    const r = await fetch(MODEL_URL, { method: 'HEAD', signal: AbortSignal.timeout(5000) });
    return r.ok && !/text\/html/.test(r.headers.get('content-type') || '');
  } catch (e) { return false; }
}

export function mountAgent(host, opts = {}) {
  if (!host || host.__rc3d) return null;
  host.__rc3d = true;
  const avatar = opts.mode === 'avatar';   // compact bust, faces viewer, reacts
  const reduce = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true, powerPreference: 'high-performance' });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  renderer.toneMapping = THREE.ACESFilmicToneMapping;
  // 1.15 pushed an already-emissive model into bloom. The showcase viewer sits
  // beside a headline it must not outshine, so it is exposed at neutral; the
  // avatar keeps the lift because it is small, dark-framed, and read at a
  // glance rather than looked past.
  renderer.toneMappingExposure = avatar ? 1.15 : 1.0;
  renderer.domElement.style.width = '100%';
  renderer.domElement.style.height = '100%';
  renderer.domElement.style.display = 'block';
  host.appendChild(renderer.domElement);

  const scene = new THREE.Scene();
  const pmrem = new THREE.PMREMGenerator(renderer);
  scene.environment = pmrem.fromScene(new RoomEnvironment(), 0.04).texture;

  const camera = new THREE.PerspectiveCamera(36, 1, 0.1, 100);
  camera.position.set(0, 1.05, 4.4);

  // Lights — a white key plus a rune-blue rim/fill so the metal reads cold-steel.
  scene.add(new THREE.AmbientLight(0x8ea0c0, 0.5));
  const key = new THREE.DirectionalLight(0xffffff, 2.2); key.position.set(3, 5, 4); scene.add(key);
  const rim = new THREE.DirectionalLight(RUNE, 3.0); rim.position.set(-4, 2, -3); scene.add(rim);
  const fill = new THREE.PointLight(RUNE, 12, 24); fill.position.set(0, 1.2, 4); scene.add(fill);

  // The model brings its own rune-disc base + holographics; we just light,
  // frame and rotate it.
  const controls = new OrbitControls(camera, renderer.domElement);
  controls.enableZoom = false; controls.enablePan = false;
  controls.enableRotate = !avatar;          // the avatar faces you; only the showcase orbits
  controls.enableDamping = true; controls.dampingFactor = 0.08;
  // A CONSTANT 1.0 SPIN IS THE PART THAT READS AS "TOO MUCH". Motion is the
  // strongest signal on a page, and spending it on decoration takes it from
  // the headline. Slow enough to look alive, slow enough to ignore — and it
  // still stops entirely for prefers-reduced-motion, off-screen, and hidden
  // tabs, which was already true and stays true.
  controls.autoRotate = !reduce && !avatar; controls.autoRotateSpeed = 0.35;
  controls.minPolarAngle = Math.PI * 0.30; controls.maxPolarAngle = Math.PI * 0.62;

  let mixer = null, model = null;
  let actionByName = {}, current = null, reactTimer = 0, wantThinking = false;
  host.setAttribute('data-rc3d-state', 'loading');

  // Box3.setFromObject reads BIND-POSE geometry — it is blind to skeletal
  // skinning, so a rigged model that floats or spreads its arms in its idle
  // pose gets mis-framed when you measure the rest pose. Measure the real
  // SKINNED pose instead: advance each clip, refresh the skeleton, and union
  // computeBoundingBox() across the motion so the agent stays fully in shot
  // whichever expression (idle / analyze / alert / execute) is playing.
  function skinnedUnionBox(mx, clips) {
    const box = new THREE.Box3(), tmp = new THREE.Box3();
    const sample = () => {
      model.updateMatrixWorld(true);
      model.traverse((o) => {
        if (o.isSkinnedMesh && o.skeleton) {
          o.skeleton.update(); o.computeBoundingBox();
          tmp.copy(o.boundingBox).applyMatrix4(o.matrixWorld);
        } else if (o.isMesh) { tmp.setFromObject(o); } else { return; }
        box.union(tmp);
      });
    };
    if (mx && clips.length) {
      for (const clip of clips) {
        const a = mx.clipAction(clip); a.stop(); a.reset(); a.play();
        for (let i = 0; i <= 6; i++) { a.time = (clip.duration * i) / 6; mx.update(0); sample(); }
        a.stop();
      }
    } else { sample(); }
    return box;
  }

  // Fit the camera to the measured animated extent. Recomputed on resize so the
  // wide-armed silhouette stays framed on square, portrait and landscape stages.
  function reframe() {
    if (!model || !model.__fit) return;
    const f = model.__fit, half = (camera.fov * Math.PI / 180) / 2;
    // Showcase frames the whole character; avatar crops to a head-and-chest bust
    // (fit a shorter vertical slice and lift the target toward the rune emblem).
    const fitH = avatar ? f.sy * 0.60 : f.sy;
    const fitW = avatar ? f.sy * 0.60 : f.sx;
    const ty = avatar ? f.cy + f.sy * 0.18 : f.cy;
    const dist = Math.max(fitH / 2 / Math.tan(half), fitW / 2 / Math.tan(half) / camera.aspect) * (avatar ? 1.04 : 1.12);
    const dir = new THREE.Vector3().subVectors(camera.position, controls.target);
    if (dir.lengthSq() === 0) dir.set(0, 0, 1);
    dir.normalize().multiplyScalar(dist);
    controls.target.set(0, ty, 0);
    camera.position.copy(controls.target).add(dir);
    controls.update();
  }

  // Cross-fade to a named clip. One-shot reactions (analyze/alert/execute) play
  // once then return to idle — or hold 'analyze' while the chat is thinking.
  function fadeTo(name, once) {
    if (!mixer) return;
    const to = actionByName[name]; if (!to) return;
    const same = to === current;
    to.reset();
    to.setLoop(once ? THREE.LoopOnce : THREE.LoopRepeat, once ? 1 : Infinity);
    to.clampWhenFinished = !!once;
    to.fadeIn(same ? 0 : 0.25); to.play();
    if (current && !same) current.fadeOut(0.25);
    current = to;
    clearTimeout(reactTimer);
    if (once) reactTimer = setTimeout(
      () => fadeTo(wantThinking ? 'analyze' : 'idle', false),
      (to.getClip().duration || 1) * 1000 + 120);
  }
  function react(name) { if (mixer && ['analyze', 'alert', 'execute', 'blink'].includes(name)) fadeTo(name, true); }
  function setThinking(on) { wantThinking = !!on; if (mixer) fadeTo(on ? 'analyze' : 'idle', false); }

  new GLTFLoader().load(MODEL_URL, (gltf) => {
    model = gltf.scene;
    const clips = gltf.animations || [];
    if (clips.length) mixer = new THREE.AnimationMixer(model);

    // 1) Scale to a stable on-screen size from the true animated extent.
    let ab = skinnedUnionBox(mixer, clips);
    const size = ab.getSize(new THREE.Vector3());
    model.scale.setScalar(2.4 / (Math.max(size.x, size.y, size.z) || 1));
    // 2) Re-measure at the new scale and centre horizontally over the origin.
    ab = skinnedUnionBox(mixer, clips);
    const c = ab.getCenter(new THREE.Vector3());
    model.position.x -= c.x; model.position.z -= c.z;
    // 3) Final measure drives the camera fit (see reframe()).
    ab = skinnedUnionBox(mixer, clips);
    const s2 = ab.getSize(new THREE.Vector3()), ctr = ab.getCenter(new THREE.Vector3());
    scene.add(model);
    model.__baseY = model.position.y;
    model.__fit = { sx: s2.x, sy: s2.y, cy: ctr.y };
    camera.position.set(0, ctr.y, 10); // head-on start; reframe() sets the distance
    controls.target.set(0, ctr.y, 0);
    reframe();

    // Build the clip actions and settle on the resting expression. Reactive
    // callers (chat "thinking", live signals/fills) cross-fade between these.
    if (mixer) {
      clips.forEach((cl) => { actionByName[cl.name] = mixer.clipAction(cl).stop(); });
      if (!actionByName.idle) actionByName.idle = mixer.clipAction(clips[0]);
      current = null;
      fadeTo(wantThinking ? 'analyze' : 'idle', false);
    }
    host.setAttribute('data-rc3d-state', 'ready');
  }, undefined, () => { host.setAttribute('data-rc3d-state', 'error'); });

  const clock = new THREE.Clock();
  let running = false, raf = 0, t0 = 0;
  function resize() {
    const w = host.clientWidth || 320, h = host.clientHeight || 360;
    renderer.setSize(w, h, false);
    camera.aspect = w / h; camera.updateProjectionMatrix();
    reframe();
  }
  function frame() {
    raf = 0;
    const dt = clock.getDelta(); t0 += dt;
    if (mixer) mixer.update(dt);
    else if (model && !reduce) model.position.y = (model.__baseY || 0) + Math.sin(t0 * 1.6) * 0.025;
    controls.update();
    renderer.render(scene, camera);
    if (running) raf = requestAnimationFrame(frame);
  }
  function play() { if (!running) { running = true; if (!raf) raf = requestAnimationFrame(frame); } }
  function pause() { running = false; if (raf) { cancelAnimationFrame(raf); raf = 0; } }

  resize();
  let ro = null, io = null;
  const onVis = () => (document.hidden ? pause() : play());
  if (window.ResizeObserver) { ro = new ResizeObserver(resize); ro.observe(host); }
  else window.addEventListener('resize', resize);
  document.addEventListener('visibilitychange', onVis);
  if ('IntersectionObserver' in window) {
    io = new IntersectionObserver((es) => es[0].isIntersecting ? play() : pause(), { threshold: 0.01 });
    io.observe(host);
  } else { play(); }
  play();
  if (reduce) { controls.autoRotate = false; }

  // Free the WebGL context + GPU resources. The dashboard SPA wipes a view's
  // DOM on navigation, which would otherwise orphan a live context; disposeAll()
  // (called from showView) reclaims it before the next view mounts.
  function dispose() {
    pause();
    _instances.delete(inst);
    clearTimeout(reactTimer);
    try { ro && ro.disconnect(); } catch (e) { /* gone */ }
    try { io && io.disconnect(); } catch (e) { /* gone */ }
    document.removeEventListener('visibilitychange', onVis);
    if (mixer) mixer.stopAllAction();
    scene.traverse((o) => {
      if (o.geometry) o.geometry.dispose();
      const mats = o.material ? (Array.isArray(o.material) ? o.material : [o.material]) : [];
      mats.forEach((m) => { for (const k in m) { const v = m[k]; if (v && v.isTexture) v.dispose(); } if (m.dispose) m.dispose(); });
    });
    try { pmrem.dispose(); } catch (e) { /* gone */ }
    try { renderer.dispose(); } catch (e) { /* gone */ }
    const gl = renderer.getContext && renderer.getContext();
    const lose = gl && gl.getExtension && gl.getExtension('WEBGL_lose_context');
    if (lose) lose.loseContext();
    if (renderer.domElement.parentNode) renderer.domElement.parentNode.removeChild(renderer.domElement);
    host.__rc3d = false;
  }

  const inst = { play, pause, react, setThinking, dispose, get model() { return model; } };
  _instances.add(inst);
  return inst;
}

let _modelOk = null;
async function modelReady() { if (_modelOk === null) _modelOk = await modelExists(); return _modelOk; }

/**
 * Should this device get a WebGL mascot at all?
 *
 * IT DOWNLOADED 2.15 MB TO EVERY PHONE — three.js at 671 KB plus the model —
 * to render decoration above the fold. On the landing page the mobile rule
 * turns the viewer into a static block ABOVE the headline, so the product's
 * actual claim was the thing pushed off screen. The most expensive asset on
 * the page was displacing the reason anyone is on it.
 *
 * Gated on width rather than a user-agent string: the question is how much
 * room there is beside the headline, and that is what a viewport measures. A
 * narrow desktop window gets the same treatment as a phone, which is right —
 * the layout is the same there.
 *
 * `save-data` and the coarse network hints are honoured where the browser
 * offers them. A visitor who has asked for less data has asked for exactly
 * this.
 *
 * AVATARS ARE EXEMPT. The dashboard bust is small, inside an authenticated app
 * the visitor chose to open, and it carries state — it reacts while the agent
 * is thinking. That is content, not decoration, and the reasoning above does
 * not reach it.
 */
function deviceWantsWebGL(host) {
  try {
    if (host && host.getAttribute('data-rc-agent3d') === 'avatar') return true;
    const w = window.innerWidth || document.documentElement.clientWidth || 0;
    // 900px is the same breakpoint the hero CSS uses to restack. Below it the
    // viewer is not beside the headline, it is on top of it.
    if (w && w < 900) return false;
    const c = navigator.connection || {};
    if (c.saveData) return false;
    if (typeof c.effectiveType === 'string' && /(^|-)2g$/.test(c.effectiveType)) return false;
    return true;
  } catch (e) {
    // Unreadable hints are not a reason to refuse a capable desktop.
    return true;
  }
}

export async function autoMount() {
  const hosts = Array.prototype.slice.call(document.querySelectorAll('[data-rc-agent3d]'));
  if (!hosts.length) return;

  // Decided BEFORE modelReady(), because that probe is itself a network
  // request. Asking a phone to HEAD an asset it will never render is a small
  // cost with no upside.
  const wanted = hosts.filter(deviceWantsWebGL);
  hosts.filter(h => !wanted.includes(h))
    .forEach(h => h.setAttribute('data-rc3d-state', 'skipped'));
  if (!wanted.length) return;

  // Non-breaking: with no model committed yet, leave the page exactly as-is.
  if (!(await modelReady())) {
    wanted.forEach(h => h.setAttribute('data-rc3d-state', 'absent'));
    return;
  }
  wanted.forEach(h => mountAgent(h, h.getAttribute('data-rc-agent3d') === 'avatar' ? { mode: 'avatar' } : {}));
}

// Mount into a dynamically-created host (SPA views) only when the model is
// present — cached probe so repeated view renders don't re-HEAD the asset.
export async function mountIfAvailable(host, opts) {
  if (!host || host.__rc3d) return null;
  return (await modelReady()) ? mountAgent(host, opts) : null;
}

window.RCAgent3D = {
  mountAgent, autoMount, mountIfAvailable,
  react: (n) => _instances.forEach((i) => i.react && i.react(n)),
  setThinking: (on) => _instances.forEach((i) => i.setThinking && i.setThinking(on)),
  disposeAll: () => Array.from(_instances).forEach((i) => i.dispose && i.dispose()),
};
autoMount();
