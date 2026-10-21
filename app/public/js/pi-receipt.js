/*
 * A Duel receipt, checked inside the Pi Browser app.
 *
 * The Duel tab quoted each seal ("Dealt sealed aaaaaaaa…") and nothing more:
 * the website's receipt page (/call/<key>) is where a seal is checked, and the
 * Pi app links nowhere on the site. So the check runs here, the same check
 * the receipt page runs, in the person's own browser:
 *
 *   1. read the receipt from our API (/api/call/<kind>:<seal>), with the
 *      session's token, so a player who has called a round opens its payload
 *      now (lib/duel_receipts.js), exactly as on the website;
 *   2. hash the sealed payload with the browser's own SHA-256 and compare it
 *      with the seal: equal is "verified", different is "does not match";
 *   3. when the day's root is out, replay the Merkle proof from the seal to
 *      that root, so a receipt cannot have been added to the day afterwards.
 *
 * WHAT IT DOES NOT CLAIM. A round still sealed (its day not over, and not
 * called by this player) has no payload to hash: it is shown as sealed, with
 * the seal and when it opens, and no verdict either way. "Verified" would be
 * a claim about bytes this page never saw; "does not match" an accusation.
 * A receipt that could not be read is said to be unreadable, never empty.
 * Whether the day's root is on a chain is the website's receipt page's
 * question (it asks the chain); this sheet names the root and stops there.
 *
 * Same canonical construction as call.html: sha256 over the payload's bytes
 * as stored, and each proof step hashes (sibling + h) when the sibling is on
 * the left, (h + sibling) when on the right.
 * app/test/the_pi_app_says_what_just_happened.smoke.test.js drives all of it
 * against a real seal and a real proof. Memeclaw's page (memeclaw.js) runs
 * the same check on its launch receipts.
 */
(function (root, factory) {
  if (typeof module === 'object' && module.exports) module.exports = factory();
  else root.RCPiReceipt = factory();
}(typeof self !== 'undefined' ? self : this, function () {
  'use strict';

  var KEY_RE = /^(round|duel|settle):[0-9a-f]{64}$/;

  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }

  function sha256hex(str) {
    var subtle = typeof crypto !== 'undefined' && crypto.subtle;
    if (!subtle) return Promise.reject(new Error('no WebCrypto'));
    return subtle.digest('SHA-256', new TextEncoder().encode(str)).then(function (buf) {
      return Array.from(new Uint8Array(buf)).map(function (b) { return b.toString(16).padStart(2, '0'); }).join('');
    });
  }

  /** The proof replayed from the seal; resolves to the root it reaches. */
  function replay(seal, proof) {
    var chain = Promise.resolve(String(seal));
    proof.forEach(function (st) {
      chain = chain.then(function (h) {
        return sha256hex(st.pos === 'L' ? (st.hash + h) : (h + st.hash));
      });
    });
    return chain;
  }

  /** The Pi app's translation (pi-i18n.js) where it is on the page; the English on /doge. */
  function T(en, vars) {
    var I = typeof window !== 'undefined' && window.RCPiI18n;
    if (I) return I.t(en, vars);
    return !vars ? en : en.replace(/\{(\w+)\}/g, function (m, k) { return vars[k] != null ? String(vars[k]) : m; });
  }

  function val(x) { return (x === null || x === undefined) ? '—' : String(x); }
  function up(x) { return x == null ? '—' : String(x).toUpperCase(); }

  /** The facts a sealed payload commits to, as [label, text] rows. */
  function facts(p) {
    var market = p.match ? val(p.match.home) + ' vs ' + val(p.match.away)
      : val(p.symbol) + (p.symbol_b ? ' vs ' + p.symbol_b : '');
    if (p.kind === 'duel_round') {
      return [
        [T('What it is'), T('The round as it was dealt')],
        [T('Round'), market + ' · ' + val(p.category) + ' · ' + val(p.day)],
        [T('The Claw’s call'), p.agent_direction == null ? T('none on this round')
          : T('{dir}, sealed before anyone called', { dir: up(p.agent_direction) })],
      ].concat(p.threshold_pct != null ? [[T('Band'), '±' + p.threshold_pct + '%']] : [])
        .concat(p.match && p.match.kickoff ? [[T('Kick-off'), String(p.match.kickoff)]] : [])
        .concat([[T('Dealt at'), val(p.dealt_at)]]);
    }
    if (p.kind === 'duel_pick') {
      return [
        [T('What it is'), T('Your call, as it was recorded')],
        [T('Call'), T('{pick} on {market}', { pick: up(p.pick), market: market })],
        [T('Entry'), p.entry_price == null ? T('none, settled on the score') : val(p.entry_price) + (p.entry_price_b != null ? ' / ' + p.entry_price_b : '')],
        [T('Called at'), val(p.picked_at)],
        [T('Resolves at'), val(p.resolves_at)],
      ];
    }
    if (p.kind === 'duel_settle') {
      return [
        [T('What it is'), T('The settlement of your call')],
        [T('Call'), up(p.pick) + ' · ' + val(p.category)],
        [T('Result'), val(p.result) + (p.marks != null ? ' · ' + (p.marks === 1 ? T('1 mark') : T('{n} marks', { n: p.marks })) : '')],
        [p.category === 'sports' ? T('Final score') : T('Close price'), p.settle_price == null ? T('none, unresolved')
          : val(p.settle_price) + (p.settle_price_b != null ? (p.category === 'sports' ? '–' : ' / ') + p.settle_price_b : '')],
        [T('Settled at'), val(p.settled_at)],
      ];
    }
    return [[T('What it is'), T('A receipt of a kind this app does not describe')]];
  }

  /**
   * Read and check one receipt. Resolves, never rejects:
   *   { state: 'verified' | 'mismatch' | 'sealed' | 'unreadable' | 'bad_key',
   *     seal, sealedAt, opensAt, facts, payload, anchor }
   * anchor: null (no root yet), or { state: 'reaches' | 'misses' | 'unchecked', day, root, count }.
   */
  function check(api, key, opts) {
    // Memeclaw checks its launch receipts with this same code, on its own
    // key shape (opts.keyRe), and draws their facts itself.
    var re = (opts && opts.keyRe) || KEY_RE;
    if (!re.test(String(key || ''))) return Promise.resolve({ state: 'bad_key' });
    // KEY_RE allows a kind, a colon and hex only, so the key goes in the path
    // as it is: the receipt page addresses the same receipt the same way.
    return api('/api/call/' + key).then(function (r) {
      var d = r && r.body;
      if (!r || !r.ok || !d || !d.seal) return { state: 'unreadable', status: r && r.status };
      var base = { seal: String(d.seal), sealedAt: d.sealed_at || null };
      if (d.locked === true) {
        return anchorOf(d).then(function (anchor) {
          return Object.assign(base, { state: 'sealed', opensAt: d.opens_at || null, anchor: anchor });
        });
      }
      if (typeof d.seal_payload !== 'string') return { state: 'unreadable', status: r.status };
      return sha256hex(d.seal_payload).then(function (hex) {
        var p = null;
        try { p = JSON.parse(d.seal_payload); } catch (e) { p = null; }
        return anchorOf(d).then(function (anchor) {
          return Object.assign(base, {
            state: hex === base.seal ? 'verified' : 'mismatch',
            payload: d.seal_payload,
            // The parsed payload is kept so the rows are worded at render
            // time, in the language on screen then.
            parsed: p,
            facts: p ? facts(p) : [],
            anchor: anchor,
          });
        });
      }, function () {
        // No WebCrypto: the check could not run, which is not a mismatch.
        return Object.assign(base, { state: 'unreadable', why: 'crypto' });
      });
    }, function () { return { state: 'unreadable' }; });
  }

  function anchorOf(d) {
    var a = d.anchor;
    if (!a || !a.root || !Array.isArray(a.proof)) return Promise.resolve(null);
    var out = { day: a.day || null, root: String(a.root), count: Number(a.seal_count) || null };
    return replay(d.seal, a.proof).then(function (h) {
      out.state = h === out.root ? 'reaches' : 'misses';
      return out;
    }, function () {
      out.state = 'unchecked';
      return out;
    });
  }

  function verdictHtml(res) {
    if (res.state === 'verified') {
      return '<p class="p-rc-v p-rc-v--ok">✅ ' + esc(T('Verified in your browser')) + '</p>'
        + '<p class="p-rc-sub">' + esc(T('The SHA-256 of what was sealed equals this seal, so not one character of it has changed since.')) + '</p>';
    }
    if (res.state === 'mismatch') {
      return '<p class="p-rc-v p-rc-v--bad">✖ ' + esc(T('Does not match')) + '</p>'
        + '<p class="p-rc-sub">' + esc(T('The SHA-256 of the payload is not this seal. This receipt is not intact.')) + '</p>';
    }
    if (res.state === 'sealed') {
      return '<p class="p-rc-v">🔏 ' + esc(T('Still sealed')) + '</p>'
        + '<p class="p-rc-sub">' + esc(res.opensAt
          ? T('What this seal commits to opens when you call the round, or when its day ends ({at}). Nothing can be checked against it until then, and nothing is claimed.', { at: res.opensAt })
          : T('What this seal commits to opens when you call the round, or when its day ends. Nothing can be checked against it until then, and nothing is claimed.')) + '</p>';
    }
    if (res.state === 'bad_key') return '<p class="p-rc-v p-rc-v--bad">' + esc(T('That is not a receipt this app can check.')) + '</p>';
    return '<p class="p-rc-v p-rc-v--bad">' + esc(T('The receipt could not be read just now.')) + '</p>'
      + '<p class="p-rc-sub">' + esc(res.why === 'crypto'
        ? T('This browser cannot compute SHA-256, so the check could not run. Nothing is claimed either way.')
        : T('This is on our side, not a statement about the receipt. Try again in a moment.')) + '</p>';
  }

  function anchorHtml(a) {
    if (!a) return '<p class="p-rc-sub">' + esc(T('Not yet in a daily root. Every seal of a day is folded into one root after the day ends.')) + '</p>';
    if (a.state === 'reaches') {
      return '<p class="p-rc-sub">🌳 ' + esc(a.count
        ? T('Folded into the {day} daily root, one of {n} seals. Your browser replayed the proof just now.', { day: a.day || '', n: a.count })
        : T('Folded into the {day} daily root. Your browser replayed the proof just now.', { day: a.day || '' })) + '</p>'
        + '<p class="p-rc-hash">' + esc(a.root) + '</p>';
    }
    if (a.state === 'misses') return '<p class="p-rc-v p-rc-v--bad">✖ ' + esc(T('The proof does not reach the day’s root.')) + '</p>';
    return '<p class="p-rc-sub">' + esc(T('The daily root could not be checked in this browser.')) + '</p>';
  }

  /** The sheet for one receipt: loading, or the check's result. */
  function sheetHtml(label, res) {
    var body;
    if (!res) {
      body = '<p class="p-rc-sub">' + esc(T('Checking in your browser…')) + '</p>';
    } else {
      var rows = (res.parsed ? facts(res.parsed) : (res.facts || [])).map(function (f) {
        return '<div class="p-rc-row"><span class="k">' + esc(f[0]) + '</span><span>' + esc(f[1]) + '</span></div>';
      }).join('');
      body = verdictHtml(res)
        + (rows ? '<div class="p-rc-rows">' + rows + '</div>' : '')
        + (res.seal ? '<p class="p-rc-k">' + esc(res.sealedAt ? T('Seal (SHA-256), sealed {at}', { at: res.sealedAt }) : T('Seal (SHA-256)')) + '</p>'
          + '<p class="p-rc-hash">' + esc(res.seal) + '</p>' : '')
        + (res.state === 'verified' || res.state === 'mismatch' || res.state === 'sealed' ? anchorHtml(res.anchor) : '')
        + (res.payload ? '<details class="p-rc-raw"><summary>' + esc(T('What was sealed (hash it yourself)')) + '</summary>'
          + '<p class="p-rc-hash">' + esc(res.payload) + '</p></details>' : '');
    }
    return '<section class="m-open p-rc-sheet" role="region" aria-label="' + esc(T('Receipt check')) + '">'
      + '<div class="p-sig-head"><h2>' + esc(label || T('Receipt')) + '</h2>'
      + '<button type="button" class="p-rc-close" aria-label="' + esc(T('Close the receipt')) + '">✕</button></div>'
      + body + '</section>';
  }

  return { check: check, sheetHtml: sheetHtml, facts: facts, KEY_RE: KEY_RE };
}));
