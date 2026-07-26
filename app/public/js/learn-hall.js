/**
 * Study-hall rune field — a slow 3D drift of golden runes behind the Study
 * Room hero. The glyph geometry is the CONTRACT's own: the same 12-branch
 * table RuneOfEntry.sol draws from, seeded per rune, at least two branches
 * guaranteed — so the atmosphere and the on-chain art are one family.
 *
 * House rules for ambient canvases, all honored: Canvas 2D only (no deps,
 * no WebGL), DPR-aware, pauses when the tab is hidden or the hero scrolls
 * out of view, and prefers-reduced-motion renders ONE static frame with no
 * animation loop at all. Pure decoration: it reads nothing and claims
 * nothing — every glyph is a decorative seed, not anyone's token.
 */
(function () {
  'use strict';
  var c = document.getElementById('rc-hall');
  if (!c || !c.getContext) return;
  var ctx = c.getContext('2d');
  var host = c.parentElement || document.body;

  // The contract's branch table: (x1,y1,x2,y2) in 0-255 glyph space.
  var BRANCHES = [
    [128, 80, 60, 160], [128, 80, 196, 160], [128, 60, 80, 20], [128, 60, 176, 20],
    [128, 120, 60, 80], [128, 120, 196, 80], [128, 160, 60, 200], [128, 160, 196, 200],
    [128, 200, 80, 240], [128, 200, 176, 240], [60, 110, 196, 110], [60, 160, 196, 160],
  ];

  function runeLines(seed) {
    // Staff + branches by seed bit, two guaranteed — the contract's rule.
    var lines = [[128, 40, 128, 246]];
    var drawn = 0;
    for (var i = 0; i < 12; i++) {
      var pick = (seed >> i) & 1;
      if (!pick && drawn === 0 && i === 10) pick = 1;
      if (!pick && drawn === 1 && i === 11) pick = 1;
      if (pick) { lines.push(BRANCHES[i]); drawn++; }
    }
    return lines;
  }

  var reduced = false;
  try { reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches; } catch (e) { /* animate */ }

  var runes = [];
  for (var i = 0; i < 12; i++) {
    runes.push({
      seed: Math.floor(Math.random() * 4096),
      x: (Math.random() - 0.5) * 2.2,        // world units
      y: (Math.random() - 0.5) * 1.1,
      z: 0.8 + Math.random() * 2.2,          // depth: 0.8 near … 3 far
      spin: (Math.random() - 0.5) * 0.35,    // per-rune yaw speed
      phase: Math.random() * Math.PI * 2,
    });
  }

  var W = 0, H = 0, dpr = 1;
  function resize() {
    dpr = Math.min(window.devicePixelRatio || 1, 2);
    W = host.clientWidth; H = host.clientHeight || 160;
    c.width = Math.round(W * dpr); c.height = Math.round(H * dpr);
    c.style.width = W + 'px'; c.style.height = H + 'px';
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  }

  function draw(t) {
    ctx.clearRect(0, 0, W, H);
    var f = 0.9 * Math.min(W, 900);          // focal length
    // painter's order: far first
    runes.slice().sort(function (a, b) { return b.z - a.z; }).forEach(function (r) {
      var yaw = reduced ? 0.4 : (t * 0.00012 * (1 + r.spin) + r.phase);
      var bob = reduced ? 0 : Math.sin(t * 0.0004 + r.phase) * 0.03;
      var s = f / (f + r.z * 340);           // perspective scale
      var cx = W / 2 + r.x * W * 0.42 * s;
      var cy = H / 2 + (r.y + bob) * H * 0.8 * s;
      var g = 0.09 + 0.5 / r.z;              // depth fades the far ones
      ctx.strokeStyle = 'rgba(230,195,75,' + g.toFixed(3) + ')';
      ctx.lineWidth = Math.max(0.6, 2.2 * s);
      ctx.lineCap = 'round';
      var cos = Math.cos(yaw);
      runeLines(r.seed).forEach(function (l) {
        // yaw = squeeze x around the staff, a cheap billboard rotation
        var scale = 0.16 * s * Math.min(W, 900) / 350;
        ctx.beginPath();
        ctx.moveTo(cx + (l[0] - 128) * scale * cos, cy + (l[1] - 143) * scale);
        ctx.lineTo(cx + (l[2] - 128) * scale * cos, cy + (l[3] - 143) * scale);
        ctx.stroke();
      });
    });
  }

  var running = false, raf = null;
  function loop(t) {
    raf = null;
    if (!running) return;
    draw(t);
    raf = requestAnimationFrame(loop);
  }
  function start() {
    if (running || reduced) return;
    running = true;
    if (!raf) raf = requestAnimationFrame(loop);
  }
  function stop() {
    running = false;
    if (raf) { cancelAnimationFrame(raf); raf = null; }
  }

  resize();
  window.addEventListener('resize', function () { resize(); if (reduced) draw(400); });
  if (reduced) {
    // One static frame — no loop, ever.
    draw(400);
  } else {
    document.addEventListener('visibilitychange', function () {
      if (document.hidden) stop(); else start();
    });
    if (typeof IntersectionObserver !== 'undefined') {
      new IntersectionObserver(function (entries) {
        entries.forEach(function (en) { if (en.isIntersecting) start(); else stop(); });
      }).observe(host);
    }
    start();
  }
})();
