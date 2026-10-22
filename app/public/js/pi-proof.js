/*
 * The daily proof in the Pi Browser app: read-only.
 *
 * A block for arena-session.js's signed-in screen, opened from the header
 * like About. It lists the latest finished days from /api/roots: each day's
 * root (the Merkle root of every seal minted that day) and how many seals it
 * covers, with the day's Anchor Quorum badge from /api/roots/quorum/:day
 * (lib/anchor_quorum.js): how many INDEPENDENT chains hold the root, each
 * checked live by the server's own readers.
 *
 * READ-ONLY. Nothing here signs, sends or anchors: the Pi app holds no
 * wallet, and the server never signs a transaction. The DogeOS app
 * (doge-proof.js) is where a player's own wallet anchors a day.
 *
 * NO LINK BACK TO THE SITE, AND NONE OUT. The Pi app links nowhere but its
 * two policies (routes/piapp.js), so a chain is named and a hash quoted,
 * never linked. A receipt on a Duel card is checked in place (pi-receipt.js).
 *
 * THE BADGE HAS THREE VALUES, as the quorum does:
 *   multi_anchored   held by k independent chains, each verified live
 *   short            fewer, and nothing unread could make up the difference
 *                    (what is pending is named)
 *   unknown          fewer, and a chain that could not be read might
 * plus a fourth state of the page's own: the badge has not been read (it is
 * still being asked for, or the ask failed), which says so and claims
 * nothing. A chain whose record does not match the root is named as such.
 * Only "held" takes a colour: the others are not results.
 *
 * TIME, NOT PERFORMANCE. The quorum proves when a day's calls existed, never
 * how good they were, and the block says so under its title.
 */
(function (root, factory) {
  if (typeof module === 'object' && module.exports) module.exports = factory();
  else root.RCPiProof = factory();
}(typeof self !== 'undefined' ? self : this, function () {
  'use strict';

  /** How many finished days the block lists, and asks a badge for. */
  var DAYS = 7;

  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }

  /** The Pi app's translation (pi-i18n.js) where it is on the page; the English elsewhere. */
  function T(en, vars) {
    var M = typeof window !== 'undefined' && window.RCMiniView;
    if (M && typeof M.T === 'function') return M.T(en, vars);
    return !vars ? en : en.replace(/\{(\w+)\}/g, function (m, k) { return vars[k] != null ? String(vars[k]) : m; });
  }

  function shortHash(h) {
    var s = String(h || '');
    return /^(0x)?[0-9a-f]{16,}$/i.test(s) ? s.slice(0, 10) + '…' + s.slice(-6) : s;
  }

  /** A witness's name: the chain's own, Bitcoin for the timestamp. */
  function nameOf(q, key) {
    if (key === 'bitcoin') return 'Bitcoin';
    var a = ((q && q.anchors) || []).filter(function (x) { return x.chain === key; })[0];
    return a && a.chain_name ? a.chain_name : key;
  }

  /**
   * The badge for one day's quorum body, or for none: { text, tone, sub }.
   * `q` null is "not read", never "not anchored".
   */
  function badge(q) {
    if (!q || typeof q.status !== 'string') return { text: T('Not checked yet'), tone: 'muted', sub: '' };
    var groups = Array.isArray(q.groups) ? q.groups.length : null;
    var k = Number(q.k) || 2;
    var subs = [];
    var mism = (q.mismatched || []).map(function (key) { return nameOf(q, key); });
    if (mism.length) subs.push(T('Does not match the root: {list}', { list: mism.join(', ') }));
    if (q.status === 'multi_anchored') {
      return { text: T('Held by {n} independent chains', { n: groups }), tone: 'up', sub: subs.join(' · ') };
    }
    if (q.status === 'unknown') {
      var unread = (q.unreadable || []).map(function (key) { return nameOf(q, key); });
      if (unread.length) subs.unshift(T('Could not read: {list}', { list: unread.join(', ') }));
      return { text: T('Could not be checked just now'), tone: 'muted', sub: subs.join(' · ') };
    }
    var pend = (q.pending || []).map(function (key) { return nameOf(q, key); });
    if (pend.length) subs.unshift(T('Waiting for: {list}', { list: pend.join(', ') }));
    return {
      text: groups ? T('{n} of {k} independent chains so far', { n: groups, k: k }) : T('Not anchored yet'),
      tone: 'muted',
      sub: subs.join(' · '),
    };
  }

  /** The chains a day's quorum verified, by name; empty when none or not read. */
  function heldBy(q) {
    if (!q) return [];
    var names = ((q.anchors) || []).filter(function (a) { return a.status === 'verified' && a.counts_toward_quorum; })
      .map(function (a) { return a.chain_name || a.chain; });
    if (q.bitcoin && q.bitcoin.status === 'verified') names.push('Bitcoin');
    return names;
  }

  function create() {
    var ctx = null;
    var roots = null;      // the feed's roots, once read
    var failed = false;    // the feed could not be read
    var quorum = {};       // day -> quorum body; absent: not read
    var asked = {};        // day -> true while its badge is being asked for

    function dayHtml(r) {
      var q = quorum[r.day] || null;
      var b = asked[r.day] && !q ? { text: T('Checking…'), tone: 'muted', sub: '' } : badge(q);
      var held = heldBy(q);
      var n = Number(r.seal_count);
      return '<div class="p-proof-row" data-day="' + esc(r.day) + '">'
        + '<div class="p-proof-hd"><b>' + esc(r.day) + '</b>'
        + (Number.isFinite(n) ? ' <span class="n">' + esc(T('{n} seals', { n: n })) + '</span>' : '') + '</div>'
        + '<div class="p-proof-q tone-' + esc(b.tone) + '">' + esc(b.text) + '</div>'
        + (held.length ? '<div class="p-proof-on n">' + esc(T('On: {list}', { list: held.join(', ') })) + '</div>' : '')
        + (b.sub ? '<div class="p-proof-sub n">' + esc(b.sub) + '</div>' : '')
        + '<div class="p-proof-root"><code title="' + esc(r.root) + '">' + esc(shortHash(r.root)) + '</code></div>'
        + '</div>';
    }

    function html() {
      var body;
      if (failed) {
        body = '<p class="m-problem m-problem--warn">' + esc(T('The daily roots could not be read just now.')) + '</p>';
      } else if (roots === null) {
        body = '<div class="e-load">' + esc(T('Reading the daily roots…')) + '</div>';
      } else if (!roots.length) {
        body = '<p class="n">' + esc(T('No finished day has sealed calls yet.')) + '</p>';
      } else {
        body = roots.slice(0, DAYS).map(dayHtml).join('');
      }
      return '<section class="m-open p-proof">'
        + '<div class="p-sig-head"><h2>' + esc(T('Daily proof')) + '</h2></div>'
        + '<p class="p-proof-intro">' + esc(T('Every call in this app is sealed when it is made. Each finished day’s seals fold into one root, and the root is written to public chains. A day held by two independent chains has a time no single chain can rewrite.')) + '</p>'
        + '<p class="p-proof-meaning n">' + esc(T('This proves when a call existed, never how good it was.')) + '</p>'
        + body
        + '</section>';
    }

    /** Ask one day's badge. A day already held is not asked again: that is settled. */
    function ask(day) {
      if (!ctx || asked[day]) return Promise.resolve();
      if (quorum[day] && quorum[day].status === 'multi_anchored') return Promise.resolve();
      asked[day] = true;
      return ctx.api('/api/roots/quorum/' + encodeURIComponent(day)).then(function (r) {
        // An unread badge is left unread: it is never drawn as "not anchored".
        if (r && r.ok && r.body && typeof r.body.status === 'string') quorum[day] = r.body;
      }, function () { /* stays unread, which is the truth */ }).then(function () { asked[day] = false; });
    }

    function load() {
      if (!ctx) return Promise.resolve();
      return ctx.api('/api/roots').then(function (r) {
        if (!r || !r.ok || !r.body || !Array.isArray(r.body.roots)) { failed = true; return; }
        failed = false;
        roots = r.body.roots;
        ctx.render();
        // One after another: the quorum route allows thirty a minute per
        // address, and seven at once from every open app would spend it.
        return roots.slice(0, DAYS).reduce(function (p, row) {
          return p.then(function () { return ask(row.day); }).then(function () { if (ctx) ctx.render(); });
        }, Promise.resolve());
      }, function () { failed = true; }).then(function () { if (ctx) ctx.render(); });
    }

    function signedIn(c) { ctx = c; load(); }

    /** The session's Refresh button: the roots, and every badge not yet settled. */
    function reload() { return load(); }

    return { place: 'main', tab: 'proof', bar: false, label: 'Proof', icon: '⛓', html: html, signedIn: signedIn, reload: reload };
  }

  return { create: create, badge: badge, heldBy: heldBy, DAYS: DAYS };
}));
