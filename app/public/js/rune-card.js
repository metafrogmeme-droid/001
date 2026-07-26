/**
 * Rune ceremony card — the minted Rune of Entry, drawn on stroke by stroke,
 * then held as a gently tilting holo card.
 *
 * The SVG arrives as the contract's own tokenURI image (base64 data URI read
 * from Base). To animate strokes it must live in the DOM — so it is NEVER
 * inlined raw: extractLines() whitelist-parses the source and the card is
 * REBUILT from numeric line coordinates only. Nothing else in the on-chain
 * payload (elements, attributes, anything) can reach the page.
 *
 * prefers-reduced-motion: no draw-on, no tilt — the finished rune renders
 * immediately. Pure decoration around a real on-chain fact; it invents
 * nothing and moves nothing.
 */
(function (root, factory) {
  if (typeof module === 'object' && module.exports) module.exports = factory();
  else root.RCRuneCard = factory();
}(typeof self !== 'undefined' ? self : this, function () {
  'use strict';

  /** Whitelist parse: data:image/svg+xml;base64,… → [{x1,y1,x2,y2}, …].
   *  Anything that is not a <line> with four finite numeric coordinates is
   *  dropped. Regex on the decoded text — no DOM parsing of untrusted input. */
  function extractLines(dataUri) {
    var m = /^data:image\/svg\+xml;base64,([A-Za-z0-9+/=]+)$/.exec(String(dataUri || ''));
    if (!m) return [];
    var text;
    try {
      text = typeof atob === 'function'
        ? atob(m[1])
        : Buffer.from(m[1], 'base64').toString('utf8');
    } catch (e) { return []; }
    var out = [];
    var re = /<line\s+x1='(-?\d+(?:\.\d+)?)'\s+y1='(-?\d+(?:\.\d+)?)'\s+x2='(-?\d+(?:\.\d+)?)'\s+y2='(-?\d+(?:\.\d+)?)'\s*\/>/g;
    var hit;
    while ((hit = re.exec(text)) && out.length < 32) {
      var l = { x1: +hit[1], y1: +hit[2], x2: +hit[3], y2: +hit[4] };
      if ([l.x1, l.y1, l.x2, l.y2].every(isFinite)) out.push(l);
    }
    return out;
  }

  var NS = 'http://www.w3.org/2000/svg';

  function mount(el, dataUri, opts) {
    opts = opts || {};
    var lines = extractLines(dataUri);
    if (!el || !lines.length) return false;
    var reduced = false;
    try { reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches; } catch (e) { /* animate */ }

    var size = opts.size || 140;
    el.innerHTML = '';
    var wrap = document.createElement('div');
    wrap.style.cssText = 'width:' + size + 'px;height:' + size + 'px;border-radius:10px;'
      + 'border:1px solid var(--line, #333);overflow:hidden;position:relative;'
      + 'transition:transform .18s ease;will-change:transform;';
    var svg = document.createElementNS(NS, 'svg');
    svg.setAttribute('viewBox', '0 0 350 350');
    svg.setAttribute('width', '100%');
    svg.setAttribute('height', '100%');
    svg.setAttribute('role', 'img');
    var bg = document.createElementNS(NS, 'rect');
    bg.setAttribute('width', '350'); bg.setAttribute('height', '350');
    bg.setAttribute('fill', '#0a0b10');
    svg.appendChild(bg);
    lines.forEach(function (l, i) {
      var ln = document.createElementNS(NS, 'line');
      ln.setAttribute('x1', l.x1); ln.setAttribute('y1', l.y1);
      ln.setAttribute('x2', l.x2); ln.setAttribute('y2', l.y2);
      ln.setAttribute('stroke', '#e6c34b');
      ln.setAttribute('stroke-width', '7');
      ln.setAttribute('stroke-linecap', 'round');
      if (!reduced) {
        var len = Math.hypot(l.x2 - l.x1, l.y2 - l.y1) || 1;
        ln.style.strokeDasharray = String(len);
        ln.style.strokeDashoffset = String(len);
        ln.style.transition = 'stroke-dashoffset .5s ease ' + (0.15 + i * 0.22) + 's';
      }
      svg.appendChild(ln);
    });
    wrap.appendChild(svg);
    // Holo glare — a soft moving highlight, purely decorative.
    var glare = document.createElement('div');
    glare.style.cssText = 'position:absolute;inset:0;pointer-events:none;opacity:0;'
      + 'transition:opacity .2s ease;background:radial-gradient(220px at 50% 50%,'
      + 'rgba(230,195,75,.16),transparent 65%)';
    wrap.appendChild(glare);
    el.appendChild(wrap);

    if (!reduced) {
      // Kick the draw-on on the next frame so the transitions actually run.
      requestAnimationFrame(function () {
        requestAnimationFrame(function () {
          for (var i = 0; i < svg.children.length; i++) {
            if (svg.children[i].tagName === 'line') svg.children[i].style.strokeDashoffset = '0';
          }
        });
      });
      wrap.addEventListener('pointermove', function (e) {
        var r = wrap.getBoundingClientRect();
        var px = (e.clientX - r.left) / r.width - 0.5;
        var py = (e.clientY - r.top) / r.height - 0.5;
        wrap.style.transform = 'perspective(500px) rotateY(' + (px * 14).toFixed(1)
          + 'deg) rotateX(' + (-py * 14).toFixed(1) + 'deg)';
        glare.style.opacity = '1';
        glare.style.background = 'radial-gradient(220px at ' + ((px + 0.5) * 100).toFixed(0)
          + '% ' + ((py + 0.5) * 100).toFixed(0) + '%, rgba(230,195,75,.16), transparent 65%)';
      });
      wrap.addEventListener('pointerleave', function () {
        wrap.style.transform = '';
        glare.style.opacity = '0';
      });
    }
    return true;
  }

  return { mount: mount, extractLines: extractLines };
}));
