/*
 * The receipt card: one Daily Duel call as a picture a player can save or
 * share, with a QR code that opens the call's receipt (/call/<key>).
 *
 * MADE FROM WHAT THIS BROWSER CHECKED. The card is drawn from the sealed
 * payload the receipt page has just hashed and found intact, never from the
 * server's description of it. No intact seal, no card.
 *
 * THREE MARKS, EACH THREE-VALUED. SEALED, SETTLED and ANCHORED are each done,
 * not yet, or (for the anchor) could not be checked, and each says which:
 *   SETTLED    done once a settlement is sealed; its verdict is the review's
 *              when a review exists, and a flagged call is "under review"
 *   ANCHORED   done only when the day is held by independent chains (the
 *              Anchor Quorum's multi_anchored, the bar /roots and the Pi
 *              proof view use); a quorum this page could not read is "not
 *              checked yet", never "not anchored"
 * Colour is a claim: the accent marks a step that is done; green and red are
 * kept for a verdict that was measured; everything else is muted.
 *
 * NO MONEY. A pick, a market, times, a seal and a verdict. Chips are left
 * off: the card is about when a call was made, not what rode on it.
 *
 * Pure until `paint`: `model` and `ops` take everything as arguments, so
 * app/test/a_receipt_card_says_what_the_seal_says.test.js runs them in Node
 * and the smoke test paints them in Chromium.
 */
(function (root, factory) {
  var api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  if (root) root.RCReceiptCard = api;
}(typeof window !== 'undefined' ? window : null, function () {
  'use strict';

  var W = 1080;
  var H = 1200;
  /** The site's palette (public/styles.css :root). */
  var C = {
    bg: '#0a0b10', surface: '#12141c', line: '#232838', accent: '#3fb6ff', bright: '#8fd9ff',
    text: '#edeef2', text2: '#a9b1c1', text3: '#8f99ab', up: '#31c48d', down: '#f05252', tile: '#edeef2',
  };
  /** The rune mark (scripts/brand_mark.py), in its own 13.1 × 24 units from (9.5, 4). */
  var MARK = 'M9.5 4 18.6 4 22.6 8.6 22.6 10.2 16.2 16.2 22.6 28 18.1 28 13.1 18.4 13.1 28 9.5 28ZM13.1 7.4 17.7 7.4 19.3 9.2 19.3 9.9 15.3 13.7 13.1 13.7Z';
  var SANS = 'InterVar, Inter, system-ui, sans-serif';
  var MONO = 'ui-monospace, SFMono-Regular, Menlo, Consolas, monospace';
  var BRAND = 'Cinzel, Georgia, serif';
  var IDENT = function (k, en) { return en; };

  /** "3f9a 61d0 … 8b2e c21e": the seal's ends, enough to match it by eye. */
  function shortSeal(seal) {
    var s = String(seal || '');
    if (!/^[0-9a-f]{64}$/.test(s)) return '';
    return s.slice(0, 4) + ' ' + s.slice(4, 8) + ' … ' + s.slice(56, 60) + ' ' + s.slice(60, 64);
  }

  /** "22 Oct 2026, 09:14 UTC" in the reader's language, or null when the time does not read. */
  function when(value, lang) {
    var ms = value == null || value === '' ? NaN : new Date(value).getTime();
    if (!Number.isFinite(ms)) return null;
    try {
      // English as the site writes dates (22 Oct 2026), not the US order.
      return new Intl.DateTimeFormat(!lang || lang === 'en' ? 'en-GB' : lang, { timeZone: 'UTC', day: 'numeric', month: 'short', year: 'numeric',
        hour: '2-digit', minute: '2-digit', hourCycle: 'h23' }).format(new Date(ms)) + ' UTC';
    } catch (e) {
      return new Date(ms).toISOString().slice(0, 16).replace('T', ' ') + ' UTC';
    }
  }

  /** A sentence with one {slot} split into parts, so the slot can carry its own weight and colour. */
  function parts(template, slot, value, style) {
    var at = template.indexOf('{' + slot + '}');
    if (at < 0) return [{ text: template }];
    return [{ text: template.slice(0, at) }, Object.assign({ text: value }, style || {}),
      { text: template.slice(at + slot.length + 2) }].filter(function (x) { return x.text !== ''; });
  }

  /**
   * The verdict a settlement adds up to, from its own receipt (`settle`, the
   * body of /api/call/settle:<seal>). Null `settle` is a settlement this page
   * could not read: the card says the result is on the receipt.
   */
  function verdictOf(settle, t) {
    if (!settle || !settle.current) return { text: t('rc.v_unread', 'the result is on the receipt'), tone: 'muted' };
    var word = function (state) {
      switch (state) {
        case 'correct': return { text: t('duel.st_correct', 'Correct'), tone: 'up' };
        case 'wrong': return { text: t('duel.st_wrong', 'Wrong'), tone: 'down' };
        case 'push': return { text: t('duel.st_push', 'No result'), tone: 'muted' };
        case 'unresolved': return { text: t('duel.st_unresolved', 'Unresolved — no price'), tone: 'muted' };
        default: return { text: t('rc.v_unread', 'the result is on the receipt'), tone: 'muted' };
      }
    };
    var review = settle.review;
    if (review && review.decision === 'stands') {
      var v = word(settle.current.settle_state);
      return { text: v.text + ' · ' + t('rc.v_reviewed', 'reviewed'), tone: v.tone };
    }
    if (review && review.decision === 'push') return { text: t('duel.st_push', 'No result'), tone: 'muted' };
    if (review) return { text: t('rc.v_corrected', 'corrected on review'), tone: 'muted' };
    if (settle.flagged === true) return { text: t('rc.v_flagged', 'under review'), tone: 'muted' };
    var out = word(settle.current.settle_state);
    if (settle.current.disputed === true) out = { text: out.text + ' · ' + t('duel.st_disputed', 'disputed'), tone: out.tone };
    return out;
  }

  /**
   * The settlement's facts from its receipt `sr`, whose sealed payload this
   * page hashed to `hex`. Null unless the hash is the seal and the payload is
   * a settlement: a verdict is read from what the seal covers, never from the
   * row beside it. The review and the flag are the receipt's links.
   */
  function settleFrom(sr, hex) {
    if (!sr || typeof sr.seal_payload !== 'string' || typeof sr.seal !== 'string' || hex !== sr.seal) return null;
    var sp;
    try { sp = JSON.parse(sr.seal_payload); } catch (e) { return null; }
    if (!sp || sp.kind !== 'duel_settle') return null;
    return { current: { settle_state: sp.settle_state, disputed: sp.disputed }, review: sr.review || null, flagged: sr.flagged === true };
  }

  /** The ANCHORED mark from the receipt's anchor and the day's quorum (`quorum` null: not read). */
  function anchorStep(anchor, quorum, t) {
    var label = t('rc.st_anchored', 'ANCHORED');
    if (!anchor || !anchor.day) return { id: 'anchored', label: label, state: 'pending', note: t('rc.a_none', 'Not in a day’s root yet') };
    if (!quorum || typeof quorum.status !== 'string') return { id: 'anchored', label: label, state: 'unknown', note: t('rc.a_unread', 'Not checked yet') };
    var groups = Array.isArray(quorum.groups) ? quorum.groups.length : null;
    if (quorum.status === 'multi_anchored') {
      return { id: 'anchored', label: label, state: 'done', note: t('rc.a_held', 'Held by {n} independent chains').replace('{n}', String(groups)) };
    }
    if (quorum.status === 'unknown') return { id: 'anchored', label: label, state: 'unknown', note: t('rc.a_unknown', 'Could not be checked just now') };
    return { id: 'anchored', label: label, state: 'pending',
      note: groups ? t('rc.a_short', '{n} of {k} independent chains so far').replace('{n}', String(groups)).replace('{k}', String(Number(quorum.k) || 2))
        : t('rc.a_not', 'Not anchored yet') };
  }

  /**
   * The card, as facts and words, or null when no card may be made.
   *   d        the receipt (/api/call/duel:<seal>)
   *   p        its sealed payload, parsed
   *   o.intact true only when this browser hashed the payload to the seal
   *   o.settle the settlement's receipt, or null (none, or not read)
   *   o.quorum the day's Anchor Quorum, or null (not read)
   *   o.t      the translator; o.duel the Duel's words (public/js/duel.js)
   */
  function model(d, p, o) {
    var opt = o || {};
    var t = opt.t || IDENT;
    var duel = opt.duel;
    if (opt.intact !== true || !d || d.kind !== 'duel_pick' || !p || p.kind !== 'duel_pick' || !duel) return null;
    var seal = shortSeal(d.seal);
    if (!seal) return null;
    // A football call's seal names the round, not the teams (lib/callseal.js
    // canonicalDuelPayload): the card says what the seal says.
    var sports = p.category === 'sports';
    var market = sports ? t('rc.match', 'Football match')
      : duel.base(p.symbol) + (p.symbol_b ? ' vs ' + duel.base(p.symbol_b) : '');
    var pickWord = sports && p.pick === 'home' ? t('rc.p_home', 'HOME WIN')
      : sports && p.pick === 'away' ? t('rc.p_away', 'AWAY WIN')
        : duel.pickLabel(p.pick, p, t);
    var sealedAt = when(p.picked_at || d.sealed_at, opt.lang) || '—';
    var settled = Boolean(d.settlement && d.settlement.key);
    var verdict = settled ? verdictOf(opt.settle || null, t) : null;
    var settleLine = settled
      ? parts(t('rc.settled', 'Settled: {verdict}.'), 'verdict', verdict.text, { tone: verdict.tone, strong: true })
      : parts(t('rc.settles', 'Settles {time}.'), 'time', when(p.resolves_at, opt.lang) || '—', { strong: true });
    return {
      eyebrow: t('rc.eyebrow', 'Daily Duel receipt'),
      market: market,
      glyph: duel.pickIcon(p.pick) || '',
      pick: pickWord,
      lines: [parts(t('rc.sealed', 'Sealed {time}, before the result.'), 'time', sealedAt, { strong: true }), settleLine],
      seal: seal,
      sealWord: t('rc.seal', 'seal'),
      steps: [
        { id: 'sealed', label: t('rc.st_sealed', 'SEALED'), state: 'done' },
        { id: 'settled', label: t('rc.st_settled', 'SETTLED'), state: settled ? 'done' : 'pending' },
        anchorStep(d.anchor, opt.quorum || null, t),
      ],
      verdict: verdict,
      scan: t('rc.scan', 'Scan to check it yourself.'),
      scanNote: t('rc.scan_p', 'The receipt page recomputes the seal in your browser.'),
    };
  }

  /** Wrap `text` to lines no wider than `max` at `font`. */
  function wrap(text, font, max, measure) {
    var words = String(text).split(/\s+/).filter(Boolean);
    var lines = [];
    var cur = '';
    words.forEach(function (w) {
      var next = cur ? cur + ' ' + w : w;
      if (cur && measure(font, next) > max) { lines.push(cur); cur = w; } else cur = next;
    });
    if (cur) lines.push(cur);
    return lines;
  }

  /**
   * The largest size, from `from` down to `min`, at which `text` fits `max`;
   * below `min` only when even `min` would run off the card.
   */
  function fit(text, weight, from, min, max, measure) {
    for (var s = from; s >= min; s -= 2) if (measure(weight + ' ' + s + 'px ' + SANS, text) <= max) return s;
    return Math.max(12, Math.floor(min * max / measure(weight + ' ' + min + 'px ' + SANS, text)));
  }

  var TONE = { up: C.up, down: C.down, muted: C.text3 };

  /**
   * The drawing, as a list of operations on a W × H canvas: `measure(font,
   * text)` is the canvas's own measureText width, `qr` the server's matrix
   * ({ url, size, rows }). Pure, so the tests can read what would be drawn.
   */
  function ops(m, qr, measure) {
    var out = [];
    var L = 88;
    var R = W - 88;
    out.push({ op: 'ground' });
    // The wordmark, and the receipt's name on the right.
    out.push({ op: 'mark', x: L, y: 78, h: 48, color: C.accent });
    out.push({ op: 'text', text: 'RUNECLAW', x: L + 44, y: 118, font: '700 34px ' + BRAND, color: C.text, spacing: 4 });
    out.push({ op: 'text', text: m.eyebrow.toUpperCase(), x: R, y: 114, font: '24px ' + MONO, color: C.text3, spacing: 6, align: 'right' });
    // The market, then the call.
    var ms = fit(m.market, '600', 92, 52, R - L, measure);
    out.push({ op: 'text', text: m.market, x: L, y: 268, font: '600 ' + ms + 'px ' + SANS, color: C.text });
    var callText = (m.glyph ? m.glyph + ' ' : '') + m.pick;
    var cs = fit(callText, '600', 64, 40, R - L, measure);
    out.push({ op: 'runs', x: L, y: 356, font: '600 ' + cs + 'px ' + SANS, runs: [
      { text: m.glyph ? m.glyph + ' ' : '', color: C.accent }, { text: m.pick, color: C.text }] });
    // Two sentences, each with its one weighted part, at the largest size at which both fit.
    var lineWidth = function (line, px) {
      return line.reduce(function (w, r) { return w + measure((r.strong ? '600 ' : '') + px + 'px ' + SANS, r.text); }, 0);
    };
    var body = 34;
    while (body > 24 && m.lines.some(function (line) { return lineWidth(line, body) > R - L; })) body -= 2;
    m.lines.forEach(function (line, i) {
      out.push({ op: 'runs', x: L, y: 448 + i * 52, font: body + 'px ' + SANS, boldFont: '600 ' + body + 'px ' + SANS,
        runs: line.map(function (r) {
          return { text: r.text, bold: Boolean(r.strong), color: r.tone ? TONE[r.tone] || C.text3 : (r.strong ? C.text : C.text2) };
        }) });
    });
    out.push({ op: 'text', text: m.sealWord + ' ' + m.seal, x: L, y: 560, font: '28px ' + MONO, color: C.text3 });
    // The three marks, at the largest size at which all three fit one row.
    var labels = m.steps.map(function (s) {
      return (s.state === 'done' ? '●' : s.state === 'unknown' ? '?' : '○') + '  ' + s.label;
    });
    var widths = function (px) {
      return labels.map(function (l) { return measure(px + 'px ' + MONO, l) + l.length * (px / 6) + 44; });
    };
    var size = 24;
    while (size > 16 && widths(size).reduce(function (a, b) { return a + b; }, 0) + 18 * (labels.length - 1) > R - L) size -= 2;
    var ws = widths(size);
    var x = L;
    m.steps.forEach(function (s, i) {
      out.push({ op: 'pill', x: x, y: 610, w: ws[i], h: 56, done: s.state === 'done',
        fill: s.state === 'done' ? 'rgba(63,182,255,0.13)' : 'rgba(0,0,0,0)',
        stroke: s.state === 'done' ? 'rgba(63,182,255,0.45)' : C.line });
      out.push({ op: 'text', text: labels[i], x: x + 22, y: 647, font: size + 'px ' + MONO, color: s.state === 'done' ? C.bright : C.text3, spacing: size / 6 });
      x += ws[i] + 18;
    });
    var anchored = m.steps[2];
    if (anchored && anchored.note) out.push({ op: 'text', text: anchored.note, x: L, y: 712, font: '26px ' + SANS, color: C.text3 });
    // The QR, on a light tile wide enough for the spec's four-module quiet zone.
    var tile = 360;
    var tx = R - tile;
    var ty = H - 80 - tile;
    var cell = qr && qr.size ? Math.floor(tile / (qr.size + 8)) : 0;
    if (cell >= 1) {
      var side = cell * qr.size;
      out.push({ op: 'qr', tile: { x: tx, y: ty, w: tile, h: tile, r: 24, fill: C.tile },
        x: tx + Math.floor((tile - side) / 2), y: ty + Math.floor((tile - side) / 2), cell: cell, rows: qr.rows, color: C.bg });
    }
    // What the QR opens, and why it is worth opening.
    var cap = wrap(m.scanNote, '26px ' + SANS, tx - L - 40, measure);
    var cy = H - 80 - 24 - 40 * (cap.length + 1);
    out.push({ op: 'text', text: m.scan, x: L, y: cy, font: '600 28px ' + SANS, color: C.text });
    cap.forEach(function (line, i) {
      out.push({ op: 'text', text: line, x: L, y: cy + 40 * (i + 1), font: '26px ' + SANS, color: C.text3 });
    });
    var host = '';
    try { host = new URL(qr.url).host; } catch (e) { host = ''; }
    if (host) out.push({ op: 'text', text: host, x: L, y: H - 92, font: '24px ' + MONO, color: C.accent });
    return out;
  }

  function roundRect(ctx, x, y, w, h, r) {
    ctx.beginPath();
    ctx.moveTo(x + r, y);
    ctx.arcTo(x + w, y, x + w, y + h, r);
    ctx.arcTo(x + w, y + h, x, y + h, r);
    ctx.arcTo(x, y + h, x, y, r);
    ctx.arcTo(x, y, x + w, y, r);
    ctx.closePath();
  }

  /** Draw `list` (from `ops`) on a 2D context of a W × H canvas. */
  function paint(ctx, list) {
    var spacing = function (px) { if ('letterSpacing' in ctx) ctx.letterSpacing = (px || 0) + 'px'; };
    list.forEach(function (o) {
      ctx.save();
      if (o.op === 'ground') {
        roundRect(ctx, 1, 1, W - 2, H - 2, 44);
        var g = ctx.createLinearGradient(0, 0, W * 0.6, H);
        g.addColorStop(0, C.surface);
        g.addColorStop(0.7, C.bg);
        ctx.fillStyle = g;
        ctx.fill();
        var glow = ctx.createRadialGradient(0, 0, 0, 0, 0, W * 0.9);
        glow.addColorStop(0, 'rgba(63,182,255,0.10)');
        glow.addColorStop(1, 'rgba(63,182,255,0)');
        ctx.fillStyle = glow;
        ctx.fill();
        ctx.lineWidth = 2;
        ctx.strokeStyle = C.line;
        ctx.stroke();
      } else if (o.op === 'mark') {
        var k = o.h / 24;
        ctx.translate(o.x - 9.5 * k, o.y - 4 * k);
        ctx.scale(k, k);
        ctx.shadowColor = 'rgba(63,182,255,0.55)';
        ctx.shadowBlur = 6;
        ctx.fillStyle = o.color;
        ctx.fill(new Path2D(MARK), 'evenodd');
      } else if (o.op === 'text') {
        ctx.font = o.font;
        ctx.fillStyle = o.color;
        ctx.textAlign = o.align || 'left';
        ctx.textBaseline = 'alphabetic';
        spacing(o.spacing);
        ctx.fillText(o.text, o.x, o.y);
      } else if (o.op === 'runs') {
        var x = o.x;
        ctx.textBaseline = 'alphabetic';
        spacing(0);
        o.runs.forEach(function (r) {
          if (!r.text) return;
          ctx.font = r.bold && o.boldFont ? o.boldFont : o.font;
          ctx.fillStyle = r.color;
          ctx.fillText(r.text, x, o.y);
          x += ctx.measureText(r.text).width;
        });
      } else if (o.op === 'pill') {
        roundRect(ctx, o.x, o.y, o.w, o.h, o.h / 2);
        ctx.fillStyle = o.fill;
        ctx.fill();
        ctx.lineWidth = 2;
        ctx.strokeStyle = o.stroke;
        ctx.stroke();
      } else if (o.op === 'qr') {
        roundRect(ctx, o.tile.x, o.tile.y, o.tile.w, o.tile.h, o.tile.r);
        ctx.fillStyle = o.tile.fill;
        ctx.fill();
        ctx.fillStyle = o.color;
        o.rows.forEach(function (row, y) {
          for (var i = 0; i < row.length; i += 1) if (row[i] === '1') ctx.fillRect(o.x + i * o.cell, o.y + y * o.cell, o.cell, o.cell);
        });
      }
      ctx.restore();
    });
  }

  return { model: model, ops: ops, paint: paint, settleFrom: settleFrom, verdictOf: verdictOf, anchorStep: anchorStep, shortSeal: shortSeal, when: when, W: W, H: H, C: C };
}));
