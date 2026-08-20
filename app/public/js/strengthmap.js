/**
 * RUNECLAW — 3D Strength Map.
 * Every Bitget USDT-perp plotted in 3D by selectable factor axes, coloured by
 * long-vs-short strength (sized by open interest). Click a coin for its factor
 * breakdown and where to trade it (CEX + DEX). Public market data only — no
 * account or P&L (§4). Data-viz, not investment advice.
 *
 * Progressive enhancement: if WebGL/three fails, a 2D table renders the same
 * data. three.js is vendored under /vendor/three (the page declares the map).
 * Motion (glow, morphing, auto-orbit, count-ups) is gated on prefers-reduced-motion.
 */
const $ = (id) => document.getElementById(id);
const FACTORS = window.__SM_FACTORS;
const AXIS_OPTS = FACTORS.concat([{ key: 'volume', label: 'Volume' }, { key: 'oi', label: 'Open interest' }]);
const state = { coins: [], bias: 'long', ax: { x: 'momentum', y: 'funding', z: 'volume' }, sel: null, vmm: null, omm: null };
const REDUCED = !!(window.matchMedia && matchMedia('(prefers-reduced-motion: reduce)').matches);
const now = () => (window.performance && performance.now ? performance.now() : Date.now());

const esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g, (c) =>
  ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
// A readable finite number, or null. The single gate every value passes
// through before this file is willing to print or colour it.
const rd = (v) => {
  if (v === null || v === undefined) return null;
  if (typeof v === 'string' && v.trim() === '') return null;
  const n = Number(v);
  return isFinite(n) ? n : null;
};
const fmtUsd = (v) => {
  // `Number(v) || 0` printed "$0.00" for an open interest nobody could read —
  // the smallest number on the screen, presented as a measurement.
  const n = rd(v); if (n === null) return '—';
  const a = Math.abs(n);
  if (a >= 1e9) return '$' + (n / 1e9).toFixed(2) + 'B';
  if (a >= 1e6) return '$' + (n / 1e6).toFixed(2) + 'M';
  if (a >= 1e3) return '$' + (n / 1e3).toFixed(1) + 'K';
  return '$' + n.toFixed(2);
};
// Price is in fact guaranteed here — the scorer drops any row it could not
// read one for — but this is a rendering boundary, and the file now holds the
// rule that nothing prints a number it did not receive.
const fmtPrice = (v) => { const n = rd(v); return n === null ? '—' : '$' + (n >= 1 ? n.toFixed(4) : n.toPrecision(4)); };
const pct = (v) => {
  // `(v >= 0 ? '+' : '') + (Number(v) || 0).toFixed(2)` rendered FOUR kinds of
  // absent as two different confident answers:
  //     null      -> "+0.00%" green      undefined -> "0.00%" red
  //     ""        -> "+0.00%" green      NaN       -> "0.00%" red
  // Same missing data, opposite claims, decided by which flavour of absent
  // arrived. `null >= 0` is TRUE and `undefined >= 0` is FALSE; `Number('')`
  // is 0 AND finite. Every row of CLAUDE.md's table in one expression.
  if (v == null || (typeof v === 'string' && v.trim() === '')) return '—';
  const n = Number(v);
  if (!isFinite(n)) return '—';
  return (n >= 0 ? '+' : '') + n.toFixed(2) + '%';
};

// Colour is a claim, so an unreadable value gets no colour class at all.
// 0 is a real, measured, flat move and keeps its own.
const moveClass = (v) => {
  if (v == null || (typeof v === 'string' && v.trim() === '')) return '';
  const n = Number(v);
  if (!isFinite(n)) return '';
  return n >= 0 ? 'up' : 'down';
};
const smooth = (t) => { t = Math.max(0, Math.min(1, t)); return t * t * (3 - 2 * t); };

// ── axis / colour maths (shared by 3D + fallback) ────────────────────
function norm(v, mm) { if (!mm || mm.max <= mm.min) return 0; return ((v - mm.min) / (mm.max - mm.min)) * 2 - 1; }
// A coordinate on the chosen axis, or null when this coin has no readable
// value for it. `|| 0` used to park an unreadable open interest at the very
// bottom of the axis, which on a scatter plot reads as "lowest in the
// universe" — a position is a claim exactly like a colour is.
function axisValue(c, key) {
  if (key === 'volume') { const v = rd(c.volume_usd); return v === null ? null : norm(Math.log10(v + 1), state.vmm); }
  if (key === 'oi') { const v = rd(c.oi_usd); return v === null ? null : norm(Math.log10(v + 1), state.omm); }
  const f = rd(c.factors && c.factors[key]);
  return f === null ? null : Math.max(-1, Math.min(1, f));
}
//: Coins the server could not score at all still exist and still have readable
//: market data; they are drawn in a neutral grey rather than dropped, so the
//: map does not quietly shrink. Grey is the one hue that claims nothing.
const UNSCORED_HUE = 220;
// Green (long-dominant) ↔ red (short-dominant); brightness by the chosen bias.
function coinColor(c) {
  const dir = rd(c.dir);
  const score = rd(state.bias === 'long' ? c.long_score : c.short_score);
  if (dir === null || score === null) {
    // `c.dir >= 0` with dir null is TRUE in JS, so an unscored coin used to
    // come out at hue 135 — full green, "long-dominant", from no data at all.
    return { hue: UNSCORED_HUE, sat: 0.05, light: 0.38, strong: 0, unscored: true };
  }
  return {
    hue: dir >= 0 ? 135 : 0,                      // green vs red
    sat: 0.55 + 0.35 * Math.min(1, Math.abs(dir) * 1.6),
    light: 0.34 + 0.30 * (score / 100),
    strong: score / 100,
    unscored: false,
  };
}

// ── small animation helpers (panel) ──────────────────────────────────
function countUp(el, to, dec) {
  // A score that does not exist has nothing to count up TO. Left alone, the
  // element keeps whatever the template put there — an em dash — rather than
  // animating from 0.0 to a number nobody computed.
  if (!el || to === null || to === undefined || !isFinite(to)) return;
  if (REDUCED) { el.textContent = to.toFixed(dec); return; }
  const t0 = now(), dur = 620;
  (function step() {
    const p = Math.min(1, (now() - t0) / dur), e = 1 - Math.pow(1 - p, 3);
    el.textContent = (to * e).toFixed(dec);
    if (p < 1) requestAnimationFrame(step);
  })();
}

// ── detail panel ─────────────────────────────────────────────────────
function facBar(label, v) {
  // A factor with nothing behind it gets no bar and no colour. It used to
  // arrive here as `Number(x) || 0` and render a centred zero-width bar
  // labelled "+0.000" in green — the panel's own heading calls this the
  // "Factor breakdown", so a row reading +0.000 is a measured neutral.
  if (v === null) {
    return `<div class="sm-frow"><span>${esc(label)}</span>`
      + `<span class="bar"><span class="mid"></span></span>`
      + `<span class="val muted">—</span></div>`;
  }
  const w = Math.min(50, Math.abs(v) * 50);
  const col = v >= 0 ? 'var(--up)' : 'var(--down)';
  const left = v >= 0 ? 50 : 50 - w;
  // Render collapsed; the frame after insert we animate to the real width.
  return `<div class="sm-frow"><span>${esc(label)}</span>`
    + `<span class="bar"><span class="mid"></span>`
    + `<i data-w="${w}" data-left="${left}" style="left:50%;width:0;background:${col}"></i></span>`
    + `<span class="val" style="color:${col}">${v >= 0 ? '+' : ''}${v.toFixed(3)}</span></div>`;
}
async function openPanel(c) {
  state.sel = c.symbol;
  const facs = FACTORS.map((f) => facBar(f.label, rd(c.factors && c.factors[f.key]))).join('');
  const dir = rd(c.dir);
  // THE TRADE TICKET. This link pre-fills a direction on the dashboard, so
  // `c.dir >= 0 ? 'LONG' : 'SHORT'` on a null turned an unreadable coin into a
  // pre-filled LONG ticket — the furthest downstream a manufactured zero got.
  // With no direction to claim, the link still opens the ticket; it just does
  // not choose a side for you.
  const trade = dir === null
    ? { href: `/dashboard?trade=${encodeURIComponent(c.base)}#trade`,
        tag: '<span class="muted">Direction not scored</span>' }
    : { href: `/dashboard?trade=${encodeURIComponent(c.base)}&dir=${dir >= 0 ? 'LONG' : 'SHORT'}#trade`,
        tag: `${dir >= 0 ? '▲ Long' : '▼ Short'} · paper/live` };
  const fundingPct = rd(c.funding);
  const body = $('smPanelBody');
  body.innerHTML = `
    <h2>${esc(c.base)}<span class="muted" style="font-size:var(--fs-sm)">USDT</span></h2>
    <div class="px">${fmtPrice(c.price)} <span class="${moveClass(c.change_pct)}">${pct(c.change_pct)}</span></div>
    <div class="sm-ls">
      <div class="c long${state.bias === 'long' ? ' on' : ''}"><div class="k">Long</div><div class="v">${dir === null ? '—' : '0.0'}</div></div>
      <div class="c short${state.bias === 'short' ? ' on' : ''}"><div class="k">Short</div><div class="v">${dir === null ? '—' : '0.0'}</div></div>
    </div>
    <div class="sm-stats">
      <span class="k">24h volume</span><span class="v">${fmtUsd(c.volume_usd)}</span>
      <span class="k">Open interest</span><span class="v">${fmtUsd(c.oi_usd)}</span>
      <span class="k">Funding</span><span class="v ${moveClass(c.funding)}">${fundingPct === null ? '—' : (fundingPct * 100).toFixed(4) + '%'}</span>
      <span class="k">ΔOI</span><span class="v ${moveClass(c.doi_pct)}">${pct(c.doi_pct)}</span>
    </div>
    <div class="sm-fac"><div class="h">Factor breakdown</div>${facs}</div>
    <div class="sm-venues"><div class="h">Open the trade — pick a venue</div>
      <div class="sm-vgrid">
        <a class="sm-v sm-v--rc" href="${trade.href}">
          <span class="nm">Trade in RUNECLAW</span>
          <span class="tag">${trade.tag}</span>
          <span class="rc">◆ risk-gated</span><span class="go">Open ticket →</span></a>
      </div>
      <div class="h" style="margin-top:var(--s2)">…or on an exchange</div>
      <div class="sm-vgrid" id="smVenues"><span class="muted small">Finding venues…</span></div></div>
    <p class="sm-disc">Public Bitget market data · scores are data-viz, not investment advice. Venue links are where the coin is tradeable — RUNECLAW never auto-routes an order.</p>`;
  $('smPanel').classList.add('open');
  // Count the scores up and grow the factor bars from zero.
  countUp(body.querySelector('.c.long .v'), c.long_score, 1);
  countUp(body.querySelector('.c.short .v'), c.short_score, 1);
  const grow = () => body.querySelectorAll('.sm-frow i').forEach((el) => {
    el.style.width = el.dataset.w + '%'; el.style.left = el.dataset.left + '%';
  });
  if (REDUCED) grow(); else requestAnimationFrame(() => requestAnimationFrame(grow));
  try {
    const r = await fetch('/api/market/venues/' + encodeURIComponent(c.base),
      { headers: { Accept: 'application/json' }, signal: AbortSignal.timeout(12000) });
    // A non-OK status is UNREADABLE, not empty. Falling through with an
    // empty list rendered "No venues found." — a claim about the market
    // made from a 503. Throw so the catch below says what is true.
    if (!r.ok) throw new Error('venues');
    const d = await r.json();
    const vs = (d && d.venues) || [];
    $('smVenues').innerHTML = vs.map((v) =>
      `<a class="sm-v" href="${esc(v.url)}" target="_blank" rel="noopener">
        <span class="nm">${esc(v.name)}</span>
        <span class="tag ${v.type === 'DEX' ? 'dex' : ''}">${esc(v.type)} · ${esc(v.kind)}</span>
        ${v.runeclaw ? '<span class="rc">◆ RUNECLAW</span>' : ''}
        <span class="go">Trade ↗</span></a>`).join('')
      || '<span class="muted small">No venues found.</span>';
  } catch (e) { $('smVenues').innerHTML = '<span class="muted small">Venue lookup unavailable.</span>'; }
}
function closePanel() { state.sel = null; $('smPanel').classList.remove('open'); }

// ── 2D fallback (no WebGL) ───────────────────────────────────────────
function renderFallback() {
  $('smFallback').style.display = 'block';
  $('smEmpty').style.display = 'none';
  // Unscored coins sort LAST rather than wherever `null - null` (NaN) leaves
  // them: a NaN comparator is not a ranking, it is an arbitrary order that
  // looks like one. They are still listed — the market data on the row is real.
  const rank = (c) => { const s = rd(c.long_score); return s === null ? -Infinity : s; };
  const rows = state.coins.slice().sort((a, b) => rank(b) - rank(a)).slice(0, 80).map((c) => {
    const ls = rd(c.long_score), ss = rd(c.short_score), fu = rd(c.funding);
    return `<tr style="cursor:pointer" data-sym="${esc(c.symbol)}"><td><b>${esc(c.base)}</b></td>
      <td class="${moveClass(c.change_pct)}">${pct(c.change_pct)}</td>
      <td class="${ls === null ? 'muted' : 'up'}">${ls === null ? '—' : ls.toFixed(1)}</td>
      <td class="${ss === null ? 'muted' : 'down'}">${ss === null ? '—' : ss.toFixed(1)}</td>
      <td class="${moveClass(c.funding)}">${fu === null ? '—' : (fu * 100).toFixed(3) + '%'}</td></tr>`;
  }).join('');
  $('smFbBody').innerHTML = rows;
  $('smFbBody').addEventListener('click', (e) => {
    const tr = e.target.closest('[data-sym]'); if (!tr) return;
    const c = state.coins.find((x) => x.symbol === tr.dataset.sym); if (c) openPanel(c);
  });
}

// ── controls wiring ──────────────────────────────────────────────────
function fillAxisSelects() {
  ['x', 'y', 'z'].forEach((a) => {
    const sel = $('ax' + a.toUpperCase());
    sel.innerHTML = AXIS_OPTS.map((o) => `<option value="${o.key}"${o.key === state.ax[a] ? ' selected' : ''}>${o.label}</option>`).join('');
    sel.addEventListener('change', () => { state.ax[a] = sel.value; if (window.__smRelayout) window.__smRelayout(); });
  });
}
function wireBias(onChange) {
  const set = (b) => {
    state.bias = b;
    $('biasLong').setAttribute('aria-pressed', String(b === 'long'));
    $('biasShort').setAttribute('aria-pressed', String(b === 'short'));
    onChange();
  };
  $('biasLong').addEventListener('click', () => set('long'));
  $('biasShort').addEventListener('click', () => set('short'));
}

async function loadData() {
  const r = await fetch('/api/market/strengthmap?limit=240',
    { headers: { Accept: 'application/json' }, signal: AbortSignal.timeout(15000) });
  if (!r.ok) throw new Error('data');
  const d = await r.json();
  state.coins = (d && d.coins) || [];
  // The axis extents are taken over READ values only. `|| 0` dragged the
  // minimum down to log10(1) = 0 for every coin whose open interest was
  // missing, which then rescaled where every OTHER coin sat on that axis —
  // one unreadable field moving the whole map.
  const logs = (key) => state.coins
    .map((c) => rd(c[key])).filter((v) => v !== null).map((v) => Math.log10(v + 1));
  const extent = (xs) => (xs.length ? { min: Math.min.apply(null, xs), max: Math.max.apply(null, xs) } : null);
  state.vmm = extent(logs('volume_usd'));
  state.omm = extent(logs('oi_usd'));
  $('smCount').textContent = state.coins.length + ' coins · updated ' + new Date().toLocaleTimeString();
}

// ── boot ─────────────────────────────────────────────────────────────
(async function boot() {
  $('smClose').addEventListener('click', closePanel);
  fillAxisSelects();
  try { await loadData(); } catch (e) {
    $('smEmpty').textContent = 'The market feed is unavailable right now. Try the app.'; return;
  }
  if (!state.coins.length) { $('smEmpty').textContent = 'No market data right now.'; return; }

  let THREE, OrbitControls;
  try {
    THREE = await import('three');
    ({ OrbitControls } = await import('three/addons/controls/OrbitControls.js'));
  } catch (e) { wireBias(renderFallback); renderFallback(); return; }

  const canvas = $('smCanvas');
  let renderer;
  try { renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: true }); }
  catch (e) { wireBias(renderFallback); renderFallback(); return; }
  $('smEmpty').style.display = 'none';
  renderer.setPixelRatio(Math.min(2, window.devicePixelRatio || 1));

  const scene = new THREE.Scene();
  scene.fog = new THREE.FogExp2(0x06070b, 0.012);
  const camera = new THREE.PerspectiveCamera(52, 1, 0.1, 200);
  camera.position.set(9, 7, 13);
  const controls = new OrbitControls(camera, renderer.domElement);
  controls.enableDamping = true; controls.dampingFactor = 0.08;
  controls.autoRotate = !REDUCED; controls.autoRotateSpeed = 0.55;
  controls.minDistance = 6; controls.maxDistance = 46;

  scene.add(new THREE.AmbientLight(0x9fb0d0, 0.85));
  const key = new THREE.PointLight(0xffffff, 70, 90); key.position.set(8, 12, 8); scene.add(key);
  const rim = new THREE.PointLight(0x4a6cff, 26, 90); rim.position.set(-11, -6, -9); scene.add(rim);

  const SPREAD = 6;
  const geo = new THREE.SphereGeometry(1, 18, 14);
  const group = new THREE.Group(); scene.add(group);

  // Reference cube + a faint floor grid, so the axes read as a 3D volume.
  const box = new THREE.LineSegments(
    new THREE.EdgesGeometry(new THREE.BoxGeometry(SPREAD * 2, SPREAD * 2, SPREAD * 2)),
    new THREE.LineBasicMaterial({ color: 0x2a3550, transparent: true, opacity: 0.42 }));
  scene.add(box);
  const grid = new THREE.GridHelper(SPREAD * 2, 12, 0x2a3856, 0x161d30);
  grid.position.y = -SPREAD; grid.material.transparent = true; grid.material.opacity = 0.32; scene.add(grid);

  // Starfield backdrop for depth.
  (function makeStars() {
    const N = 700, pos = new Float32Array(N * 3);
    for (let i = 0; i < N; i++) {
      const r = 34 + Math.random() * 62, th = Math.random() * Math.PI * 2, ph = Math.acos(2 * Math.random() - 1);
      pos[i * 3] = r * Math.sin(ph) * Math.cos(th);
      pos[i * 3 + 1] = r * Math.sin(ph) * Math.sin(th);
      pos[i * 3 + 2] = r * Math.cos(ph);
    }
    const g = new THREE.BufferGeometry(); g.setAttribute('position', new THREE.BufferAttribute(pos, 3));
    const stars = new THREE.Points(g, new THREE.PointsMaterial({
      color: 0x8aa0c8, size: 0.14, transparent: true, opacity: 0.5, sizeAttenuation: true, depthWrite: false }));
    scene.add(stars);
    scene.userData.stars = stars;
  })();

  // Soft radial sprite for the additive glow halos.
  const haloTex = (function () {
    const cv = document.createElement('canvas'); cv.width = cv.height = 64;
    const g = cv.getContext('2d');
    const grad = g.createRadialGradient(32, 32, 0, 32, 32, 32);
    grad.addColorStop(0, 'rgba(255,255,255,1)');
    grad.addColorStop(0.25, 'rgba(255,255,255,0.5)');
    grad.addColorStop(1, 'rgba(255,255,255,0)');
    g.fillStyle = grad; g.fillRect(0, 0, 64, 64);
    return new THREE.CanvasTexture(cv);
  })();

  // Persistent node per symbol so relayout/bias/refresh MORPH rather than snap.
  const nodes = new Map();
  let pickables = [];
  let createSeq = 0;
  const tgtColor = new THREE.Color(), tgtEmis = new THREE.Color();

  // null when this coin cannot be PLACED — it has no readable value for one of
  // the three axes currently selected, or no open interest to size it by.
  // There is no honest position for it in a scatter plot, and `null * SPREAD`
  // is NaN, which three.js renders as a sphere at the origin: dead centre of
  // every axis, the most authoritative-looking spot on the map.
  function targetFor(c) {
    const x = axisValue(c, state.ax.x);
    const y = axisValue(c, state.ax.y);
    const z = axisValue(c, state.ax.z);
    const oi = rd(c.oi_usd);
    if (x === null || y === null || z === null || oi === null) return null;
    return {
      x: x * SPREAD, y: y * SPREAD, z: z * SPREAD,
      s: 0.12 + 0.42 * ((norm(Math.log10(oi + 1), state.omm) + 1) / 2),
    };
  }
  function colorFor(c) {
    const col = coinColor(c);
    return {
      c: tgtColor.clone().setHSL(col.hue / 360, col.sat, col.light),
      e: tgtEmis.clone().setHSL(col.hue / 360, col.sat, 0.5),
      ei: 0.15 + 0.85 * col.strong, strong: col.strong,
    };
  }
  function layout() {
    const seen = new Set();
    let unplaced = 0;
    state.coins.forEach((c) => {
      const tgt = targetFor(c), cl = colorFor(c);
      // Omitted, not faked. The coin is still in the 2D table with its readable
      // fields intact — this view just has no coordinates to draw it at, and
      // the count below says so rather than letting the map quietly shrink.
      if (tgt === null) { unplaced++; return; }
      seen.add(c.symbol);
      let n = nodes.get(c.symbol);
      if (!n) {
        const mat = new THREE.MeshStandardMaterial({
          color: cl.c.clone(), emissive: cl.e.clone(), emissiveIntensity: cl.ei, roughness: 0.35, metalness: 0.12 });
        const mesh = new THREE.Mesh(geo, mat);
        mesh.position.set(tgt.x, tgt.y, tgt.z); mesh.scale.setScalar(0.001);
        const halo = new THREE.Sprite(new THREE.SpriteMaterial({
          map: haloTex, color: cl.c.clone(), transparent: true, opacity: 0,
          blending: THREE.AdditiveBlending, depthWrite: false }));
        halo.position.copy(mesh.position);
        group.add(mesh); group.add(halo);
        n = { mesh, halo, mat, cur: { x: tgt.x, y: tgt.y, z: tgt.z, s: 0 }, tgt, cl, coin: c,
          born: now(), delay: REDUCED ? 0 : (createSeq++ % Math.max(1, state.coins.length)) * 4, hover: 0, hoverT: 0 };
        mesh.userData.node = n;
        nodes.set(c.symbol, n);
      } else {
        n.tgt = tgt; n.cl = cl; n.coin = c;
      }
    });
    for (const [sym, n] of nodes) if (!seen.has(sym)) { group.remove(n.mesh); group.remove(n.halo); nodes.delete(sym); }
    pickables = Array.from(nodes.values()).map((n) => n.mesh);
    if (unplaced) {
      const el = $('smCount');
      if (el && el.textContent.indexOf('not plotted') === -1) {
        el.textContent += ` · ${unplaced} not plotted on these axes`;
      }
    }
  }
  window.__smRelayout = layout;
  layout();
  wireBias(layout);

  // ── picking + hover ────────────────────────────────────────────────
  const raycaster = new THREE.Raycaster();
  const ndc = new THREE.Vector2();
  let downXY = null, hoverNDC = null, lastInteract = 0;
  const tip = $('smTip');

  function setNDC(clientX, clientY) {
    const rect = canvas.getBoundingClientRect();
    ndc.x = ((clientX - rect.left) / rect.width) * 2 - 1;
    ndc.y = -((clientY - rect.top) / rect.height) * 2 + 1;
    return rect;
  }
  canvas.addEventListener('pointerdown', (e) => { downXY = [e.clientX, e.clientY]; lastInteract = now(); });
  canvas.addEventListener('pointerup', (e) => {
    if (!downXY) return;
    const moved = Math.hypot(e.clientX - downXY[0], e.clientY - downXY[1]); downXY = null;
    if (moved > 6) return; // a drag, not a tap
    setNDC(e.clientX, e.clientY);
    raycaster.setFromCamera(ndc, camera);
    const hit = raycaster.intersectObjects(pickables, false)[0];
    if (hit && hit.object.userData.node) openPanel(hit.object.userData.node.coin);
  });
  canvas.addEventListener('pointermove', (e) => {
    if (downXY) { if (tip) tip.classList.remove('on'); return; } // dragging: no hover
    hoverNDC = [e.clientX, e.clientY];
  });
  canvas.addEventListener('pointerleave', () => { hoverNDC = null; if (tip) tip.classList.remove('on'); });
  // Only the USER-initiated 'start' resets the idle timer — not the 'change'
  // events auto-rotate itself fires (that would stop it re-arming after 3.2s).
  controls.addEventListener('start', () => { lastInteract = now(); });

  function processHover() {
    if (!hoverNDC) { hovered = null; return; }
    setNDC(hoverNDC[0], hoverNDC[1]);
    raycaster.setFromCamera(ndc, camera);
    const hit = raycaster.intersectObjects(pickables, false)[0];
    hovered = (hit && hit.object.userData.node) || null;
    if (tip) {
      if (hovered) {
        const c = hovered.coin;
        tip.innerHTML = `<b>${esc(c.base)}</b> <span class="${moveClass(c.change_pct)}">${pct(c.change_pct)}</span>`
          + `<span class="sm-tip-ls"><span class="up">L ${c.long_score.toFixed(0)}</span> · <span class="down">S ${c.short_score.toFixed(0)}</span></span>`;
        tip.style.left = hoverNDC[0] + 'px'; tip.style.top = hoverNDC[1] + 'px';
        tip.classList.add('on');
        canvas.style.cursor = 'pointer';
      } else { tip.classList.remove('on'); canvas.style.cursor = 'grab'; }
    }
  }
  let hovered = null;

  // ── axis labels (DOM, projected each frame) ────────────────────────
  const axLabels = ['x', 'y', 'z'].map((a) => {
    const el = document.createElement('div');
    el.className = 'sm-axlabel sm-ax-' + a;
    document.body.appendChild(el);
    return { a, el, v: new THREE.Vector3(), txt: '' };
  });
  const AX_POS = { x: [SPREAD * 1.15, 0, 0], y: [0, SPREAD * 1.15, 0], z: [0, 0, SPREAD * 1.15] };
  function labelFor(key) { const o = AXIS_OPTS.find((x) => x.key === key); return o ? o.label : key; }
  function updateAxisLabels() {
    const rect = canvas.getBoundingClientRect();
    axLabels.forEach((L) => {
      const p = AX_POS[L.a]; L.v.set(p[0], p[1], p[2]).project(camera);
      const x = rect.left + (L.v.x * 0.5 + 0.5) * rect.width;
      const y = rect.top + (-L.v.y * 0.5 + 0.5) * rect.height;
      const inFront = L.v.z < 1;
      L.el.style.transform = `translate(-50%,-50%) translate(${x}px,${y}px)`;
      L.el.style.opacity = inFront ? '1' : '0';
      const t = L.a.toUpperCase() + ' · ' + labelFor(state.ax[L.a]);
      if (t !== L.txt) { L.txt = t; L.el.textContent = t; }
    });
  }

  // ── resize + frame loop ────────────────────────────────────────────
  function resize() {
    const w = canvas.clientWidth || window.innerWidth, h = canvas.clientHeight || window.innerHeight;
    renderer.setSize(w, h, false); camera.aspect = w / h; camera.updateProjectionMatrix();
  }
  window.addEventListener('resize', resize); resize();

  let last = now();
  (function frame() {
    const t = now(), dt = Math.min(60, t - last); last = t;
    const k = REDUCED ? 1 : (1 - Math.pow(0.0026, dt / 1000)); // frame-rate-independent smoothing

    if (hoverNDC || hovered) processHover();

    nodes.forEach((n) => {
      const gate = REDUCED ? 1 : smooth((t - n.born - n.delay) / 430);
      n.cur.x += (n.tgt.x - n.cur.x) * k;
      n.cur.y += (n.tgt.y - n.cur.y) * k;
      n.cur.z += (n.tgt.z - n.cur.z) * k;
      n.cur.s += (n.tgt.s * gate - n.cur.s) * k;
      // hover / selection emphasis
      n.hoverT += (((hovered === n) ? 1 : 0) - n.hoverT) * (REDUCED ? 1 : 0.25);
      const selPulse = (!REDUCED && state.sel === n.coin.symbol) ? (1 + 0.16 * Math.sin(t * 0.006)) : 1;
      const s = Math.max(0.001, n.cur.s * (1 + 0.55 * n.hoverT) * selPulse);
      n.mesh.position.set(n.cur.x, n.cur.y, n.cur.z);
      n.mesh.scale.setScalar(s);
      n.halo.position.copy(n.mesh.position);
      n.halo.scale.setScalar(s * 3.4);
      n.mat.color.lerp(n.cl.c, k); n.mat.emissive.lerp(n.cl.e, k);
      n.mat.emissiveIntensity += (n.cl.ei - n.mat.emissiveIntensity) * k;
      n.halo.material.color.lerp(n.cl.c, k);
      const sel = state.sel === n.coin.symbol;
      const haloOp = (0.10 + 0.5 * n.cl.strong + 0.4 * n.hoverT + (sel ? 0.35 : 0)) * gate;
      n.halo.material.opacity += (haloOp - n.halo.material.opacity) * k;
    });

    if (scene.userData.stars && !REDUCED) scene.userData.stars.rotation.y += 0.00006 * dt;
    controls.autoRotate = !REDUCED && !state.sel && (t - lastInteract > 3200);
    controls.update();
    updateAxisLabels();
    renderer.render(scene, camera);
    requestAnimationFrame(frame);
  })();

  // Live refresh: pull fresh scores every 20s and morph in place.
  setInterval(async () => {
    try { await loadData(); layout(); } catch (e) { /* keep the last frame */ }
  }, 20000);
})();
