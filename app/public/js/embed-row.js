/*
 * The signal card, as pure functions.
 *
 * Extracted from embed-signals.js rather than grown in place, for the reason
 * CLAUDE.md gives and this repo has paid for repeatedly: a card built inline in
 * a loader can only be tested by loading the page, so the interesting cases —
 * a signal with no readable age, an R:R that arrives as the string "0" — are
 * reachable only in production, on someone else's phone.
 *
 * The fields this adds are the exact shapes the honesty table names. `age` is
 * called out by name there: "an age of 0.0 rendering as '0m' (just opened) for
 * a position of unknown age". A ratio is the `parseFloat(x) || 0` row. Both are
 * new here, so both get the treatment on the way in rather than after someone
 * reads a fabricated number off a trading card.
 */
(function (root, factory) {
  if (typeof module === 'object' && module.exports) module.exports = factory(require('./embed-read'));
  else root.RCEmbedRow = factory(root.RCEmbedRead);
}(typeof self !== 'undefined' ? self : this, function (RD) {
  'use strict';

  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }

  /** A finite number, or null. Never a fallback zero — see the module comment. */
  function num(v) {
    if (v === null || v === undefined || v === '') return null;
    var n = Number(v);
    return isFinite(n) ? n : null;
  }

  /**
   * Split "NOKSTOCK/USDT:USDT" into the parts a reader wants.
   *
   * The board printed that raw, and beside "ICP/USDT" it read as two different
   * kinds of thing when it is one market. The suffix is venue notation, not
   * information anybody wants at 15px.
   *
   * THE BASE IS NOT PARSED HERE. `RCEmbedRead.displaySym` already owns that,
   * and getting it wrong is not hypothetical: the chart fetch asked Bitget
   * about a market that does not exist for exactly one release because two
   * different symbol forms were treated as one. Re-deriving it here would put
   * a second answer to the same question in a second file, which is how they
   * drift. Only the quote and perp legs — genuinely new — are read here.
   */
  function splitSymbol(sym) {
    var raw = String(sym == null ? '' : sym).trim();
    var base = RD.displaySym(raw);
    if (!raw) return { base: '', quote: null, perp: false };
    var perp = raw.indexOf(':') !== -1;
    var head = perp ? raw.slice(0, raw.indexOf(':')) : raw;
    var slash = head.indexOf('/');
    return {
      // displaySym strips a trailing USDT, so a contract-form symbol like
      // `BTCUSDT` yields `BTC` and never renders as an empty card heading.
      base: base || head,
      quote: slash === -1 ? null : head.slice(slash + 1),
      perp: perp,
    };
  }

  /**
   * "12m", "3h", "2d" — or NULL when the timestamp cannot be read.
   *
   * Null, not "0m". This is the failure CLAUDE.md names outright: a signal of
   * unknown age rendering as brand new is the most flattering possible lie on a
   * board whose whole claim is freshness. The caller omits the field instead.
   *
   * A genuinely fresh signal IS "now" — that is a real measurement of a real
   * zero, and it is not the same fact as an absent date. `nowMs` is injected so
   * a test can assert both without waiting.
   */
  function ageLabel(createdAt, nowMs) {
    if (createdAt === null || createdAt === undefined || createdAt === '') return null;
    var t = new Date(createdAt).getTime();
    if (!isFinite(t)) return null;
    var now = typeof nowMs === 'number' ? nowMs : Date.now();
    var secs = Math.floor((now - t) / 1000);
    // A timestamp in the future is not an age. Clocks disagree by a few seconds
    // routinely, so a small skew reads as "now"; a large one is unreadable.
    if (secs < -300) return null;
    if (secs < 60) return 'now';
    var mins = Math.floor(secs / 60);
    if (mins < 60) return mins + 'm';
    var hrs = Math.floor(mins / 60);
    if (hrs < 48) return hrs + 'h';
    return Math.floor(hrs / 24) + 'd';
  }

  /**
   * "4.2R" — or null when there is no readable ratio.
   *
   * A missing R:R rendering as `0` would say the trade risks everything for
   * nothing, which is a strong claim to manufacture out of an absent field.
   * Zero and negative are dropped too: both are real numbers the column can
   * hold and neither describes a reward-to-risk the reader can act on.
   */
  function rrLabel(rr) {
    var n = num(rr);
    if (n === null || n <= 0) return null;
    return (Math.round(n * 10) / 10) + 'R';
  }

  /**
   * A price, or an em dash. Never 0 for absent — 0 is a price nothing trades at
   * and printing it puts a real-looking number beside real ones.
   */
  function price(v) {
    var n = num(v);
    if (n === null) return '—';
    var abs = Math.abs(n);
    var dp = abs >= 1000 ? 2 : abs >= 1 ? 4 : 6;
    return n.toFixed(dp).replace(/\.?0+$/, function (m) {
      return m.indexOf('.') === 0 ? '' : m;
    });
  }

  function pct(v) {
    var n = num(v);
    return n === null ? '—' : Math.round(n * 100) + '%';
  }

  /**
   * The text the cast composer opens with.
   *
   * PUBLIC-SURFACE RULE APPLIES HERE HARDEST. This string leaves our page and
   * becomes a public post on somebody's timeline, so §4 governs it: percent,
   * ratio and count, never an amount. Prices are public market fact and are
   * fine — they are what the signal IS — but nothing derived from an account
   * goes near it. The card is built from `publicSignal` output, which has
   * already dropped `pnl`; this builds only from fields that survived that.
   *
   * Every part is omitted when unreadable. A cast reading "LONG BTC · 0% conf"
   * assembled from an absent confidence would be this repo's oldest bug, except
   * published under the reader's own name.
   */
  function shareText(sig, opts) {
    var s = sig || {};
    var o = opts || {};
    var sym = splitSymbol(s.symbol);
    var dir = String(s.direction || '').toUpperCase();

    var head = [];
    if (dir === 'LONG' || dir === 'SHORT') head.push(dir);
    if (sym.base) head.push(sym.base);
    // Nothing identifiable survived. A cast that names no market and no
    // direction is not a signal share, so the caller gets nothing and renders
    // no button rather than opening a composer full of punctuation.
    if (!head.length) return null;

    var bits = [head.join(' ')];
    var conf = num(s.confidence);
    if (conf !== null) bits.push(Math.round(conf * 100) + '% confidence');
    var rr = rrLabel(s.rr);
    if (rr) bits.push(rr + ' target');

    var line = bits.join(' · ');
    return o.suffix ? line + '\n\n' + o.suffix : line;
  }

  /** REGIME_LIKE_THIS -> "regime like this", for a quiet context line. */
  function regimeLabel(r) {
    var s = String(r == null ? '' : r).trim();
    if (!s) return null;
    return s.toLowerCase().replace(/_/g, ' ');
  }

  /**
   * One card.
   *
   * Every added field is OMITTED when unreadable rather than rendered as a
   * placeholder value — the composite-view strategy from CLAUDE.md's table,
   * which is right here because one missing timestamp must not blank a card
   * whose prices are perfectly good. The prices themselves keep the em dash:
   * they are the card's subject, and a level silently vanishing would change
   * what the trade appears to be.
   */
  function rowHtml(s, opts) {
    var o = opts || {};
    var sig = s || {};
    var dir = String(sig.direction || '').toUpperCase();
    var dirCls = dir === 'LONG' ? 'e-long' : dir === 'SHORT' ? 'e-short' : 'e-flat';
    var sym = splitSymbol(sig.symbol);

    // The quiet second line: only the parts that could actually be read.
    var meta = [];
    if (sym.quote) meta.push(esc(sym.quote) + (sym.perp ? ' perp' : ''));
    var regime = regimeLabel(sig.regime);
    if (regime) meta.push(esc(regime));
    var age = ageLabel(sig.created_at, o.nowMs);
    if (age) meta.push(esc(age));

    var rr = rrLabel(sig.rr);
    var geo = JSON.stringify({
      entry: sig.entry_price, stop: sig.stop_loss,
      target: sig.take_profit, direction: dir,
    });

    return '<article class="e-row">'
      + '<header class="e-head">'
      + '<span class="e-dir ' + dirCls + '">' + esc(dir || '—') + '</span>'
      + '<b class="e-sym">' + esc(sym.base || '—') + '</b>'
      + '<span class="e-conf">' + pct(sig.confidence) + '</span>'
      + '</header>'
      + (meta.length ? '<p class="e-meta">' + meta.join(' · ') + '</p>' : '')
      // TWO symbols, deliberately, and this is load-bearing. `data-sc-sym` is
      // what Bitget is ASKED about and must be the contract form; the label is
      // what the viewer reads. They were one value once — the display one — so
      // the fetch asked about a market that does not exist and every chart drew
      // "no candles", a claim about the market made from a bad request.
      + '<div class="e-chart" data-sc-sym="' + esc(RD.contractSym(sig.symbol))
      + '" data-sc-label="' + esc(RD.displaySym(sig.symbol)) + '" data-sc-geo=\''
      + esc(geo) + '\'><div class="e-load">…</div></div>'
      + '<dl class="e-lv">'
      + '<div><dt>entry</dt><dd>' + esc(price(sig.entry_price)) + '</dd></div>'
      + '<div><dt>stop</dt><dd>' + esc(price(sig.stop_loss)) + '</dd></div>'
      + '<div><dt>target</dt><dd>' + esc(price(sig.take_profit)) + '</dd></div>'
      + (rr ? '<div><dt>r:r</dt><dd>' + esc(rr) + '</dd></div>' : '')
      + '</dl>'
      + shareHtml(sig, o)
      + '</article>';
  }

  /**
   * The share affordance — rendered ONLY when there is a host to share to.
   *
   * `opts.canShare` is the caller's answer to "is a Mini App host present",
   * and without it this returns nothing at all. On a plain website embed there
   * is no cast composer in existence, and a button that silently does nothing
   * when tapped is the same defect as a card that renders a measurement it
   * never took: it asserts a capability that is not there.
   *
   * WHY THIS DOES NOT REOPEN THE CLICKJACKING HOLE the actionless rule closed.
   * The guard exists because a click landing on an invisible frame must not be
   * able to exercise the viewer's authority. This button exercises none: it
   * sends one postMessage to `window.parent` and stops. In an attack the
   * parent IS the attacker, so the entire effect is that our page tells the
   * attacker's page that somebody clicked — no cookie, no request to us, no
   * state anywhere. Composing actually happens in Warpcast's own UI behind an
   * explicit confirmation we neither see nor control. `embed_frame_policy`
   * pins that this remains the ONLY interactive element and that it still
   * cannot fetch, POST, or store.
   */
  function shareHtml(sig, opts) {
    if (!opts || !opts.canShare) return '';
    var text = shareText(sig, { suffix: opts.shareSuffix });
    if (!text) return '';
    return '<div class="e-act">'
      + '<button type="button" class="e-share" data-share-text="' + esc(text) + '">'
      + 'Share</button>'
      + '</div>';
  }

  return {
    rowHtml: rowHtml,
    shareText: shareText,
    splitSymbol: splitSymbol,
    ageLabel: ageLabel,
    rrLabel: rrLabel,
    regimeLabel: regimeLabel,
    price: price,
    pct: pct,
  };
}));
