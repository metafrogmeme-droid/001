/**
 * The /guard page: send one request to POST /api/guard/check and draw the
 * verdict it answers.
 *
 * `render(body, T)` is pure (the answer and a translator in, HTML out) so the
 * tests drive it without a browser; the bottom half wires it to the page.
 *
 * Colour is a claim. Red is for 'stop' and amber for 'check'. 'clear' is
 * muted, not green: no warning in what could be read is not a measurement
 * that the request is safe, and a green tick would say it is. 'unknown' and
 * 'invalid' are muted too. A check that could not run says so; it never
 * draws an empty verdict.
 */
(function (root, factory) {
  if (typeof module === 'object' && module.exports) module.exports = factory();
  else root.RCGuardPage = factory();
}(typeof self !== 'undefined' ? self : this, function () {
  'use strict';

  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }
  function fill(tpl, map) {
    return String(tpl).replace(/\{(\w+)\}/g, function (m, n) { return map[n] == null ? m : map[n]; });
  }

  var LOOK = {
    stop: { ico: '⛔', cls: 'danger' },
    check: { ico: '⚠️', cls: 'caution' },
    clear: { ico: '○', cls: 'muted' },
    unknown: { ico: '?', cls: 'muted' },
    invalid: { ico: '·', cls: 'muted' },
  };
  var SEV = { high: 'danger', medium: 'caution', info: 'info' };

  /** What changes for the account, in English; `grd.c_<kind>` carries the translations. */
  var CHANGE_EN = {
    native_out: 'Sends {amount} wei to {counterparty}',
    native_in: 'Receives {amount} wei from {counterparty}',
    token_out: 'Sends {amount} raw units of {token} to {counterparty}',
    token_in: 'Receives {amount} raw units of {token} from {counterparty}',
    nft_out: 'Sends item {id} of {token} to {counterparty}',
    nft_in: 'Receives item {id} of {token} from {counterparty}',
    approval: 'Lets {spender} spend {amount} raw units of {token}',
    nft_approval: 'Lets {spender} move item {id} of {token}',
    approval_for_all_on: 'Lets {operator} move every item of {token}',
    approval_for_all_off: 'Revokes {operator}’s control of {token}',
  };

  var CHAINS = { 8453: 'Base', 84532: 'Base Sepolia' };

  /** A deadline or an expiry as the model gives it: unix seconds, 'NEVER' or 'BLOCK'. */
  function when(v, T) {
    T = T || function (k, en) { return en; };
    if (v === 'NEVER') return T('grd.p_never', 'no end');
    if (v === 'BLOCK') return T('grd.p_block', 'the block it is used in');
    var n = Number(v);
    if (!Number.isFinite(n) || n <= 0) return String(v);
    var d = new Date(n * 1000);
    return isNaN(d.getTime()) ? String(v) : d.toISOString().slice(0, 16).replace('T', ' ') + ' UTC';
  }

  /** The words of one model action, in the reader's language when the table has it. */
  function actionLine(a, T) {
    var p = {};
    for (var k in (a.params || {})) p[k] = a.params[k];
    if (p.unlimited || p.amount === 'UNLIMITED') p.amount = T('txr.unl', 'UNLIMITED');
    ['deadline', 'expires'].forEach(function (k) { if (k in p) p[k] = when(p[k], T); });
    // Whole-line translation by the model's stable id; a miss falls back to
    // the model's own English whole, never to a mixed sentence.
    return fill(T(a.tid, a.en || a.tid), p);
  }

  function changeLine(c, T) {
    var key = c.kind === 'approval_for_all' ? (c.on ? 'approval_for_all_on' : 'approval_for_all_off') : c.kind;
    var p = {};
    for (var k in c) p[k] = c[k];
    if (p.amount === 'UNLIMITED') p.amount = T('txr.unl', 'UNLIMITED');
    return fill(T('grd.c_' + key, CHANGE_EN[key] || key), p);
  }

  function simLine(body, T) {
    var s = body.simulation;
    if (body.request !== 'tx') return '';
    if (s === null) return T('grd.sim_none', 'Not simulated: add your address to see what this transaction would do to your account.');
    if (!s.ran) {
      var why = s.reason === 'unreachable' ? T('grd.sim_r_unreachable', 'Base’s endpoints did not answer')
        : s.reason === 'rejected' ? T('grd.sim_r_rejected', 'Base’s endpoints refused the request')
          : T('grd.sim_r_other', 'the transaction could not be simulated');
      return fill(T('grd.sim_failed', 'The simulation could not run: {why}.'), { why: why });
    }
    return fill(T('grd.sim_at', 'Simulated on {chain} against block {block}. A simulation reads one block: the same request later can do something else.'),
      { chain: CHAINS[body.chain_id] || String(body.chain_id), block: s.block });
  }

  /**
   * The verdict as HTML. `body` is the route's answer, or null when the check
   * could not run (the network or the server failed): that is said, never
   * drawn as an empty verdict.
   */
  function render(body, T) {
    T = T || function (k, en) { return en; };
    if (!body || typeof body.level !== 'string') {
      return '<section class="panel g-out"><p class="s g-err">'
        + esc(T('grd.err', 'The check could not run just now. Nothing was decided about this request.')) + '</p></section>';
    }
    var look = LOOK[body.level] || LOOK.unknown;
    var out = '<section class="panel g-out" data-level="' + esc(body.level) + '">'
      + '<div class="verdict v-' + look.cls + '"><span class="ico">' + look.ico + '</span>'
      + '<div class="t">' + esc(T('grd.v_' + body.level, body.headline || body.level)) + '</div></div>';
    if (body.level === 'invalid') return out + '</section>';

    var reasons = (body.reasons || []).map(function (r) {
      return '<div class="flag sev-' + esc(SEV[r.sev] || 'info') + '"><span class="dot"></span><div>'
        + '<div class="wy">' + esc(T('grd.r_' + r.id, r.en || r.id)) + '</div></div></div>';
    }).join('');
    if (reasons) out += '<h3 class="g-h">' + esc(T('grd.reasons_h', 'Why')) + '</h3>' + reasons;

    var acts = (body.actions || []).map(function (a) {
      return '<div class="flag sev-none"><span class="dot"></span><div><div class="wy">'
        + esc(actionLine(a, T)) + '</div></div></div>';
    }).join('');
    if (acts) out += '<h3 class="g-h">' + esc(T('grd.actions_h', 'What it says')) + '</h3>' + acts;

    var s = body.simulation;
    if (s && s.ran) {
      var changes = (body.changes || []).map(function (c) {
        return '<li><code>' + esc(changeLine(c, T)) + '</code></li>';
      }).join('');
      out += '<h3 class="g-h">' + esc(T('grd.changes_h', 'What changes for your account')) + '</h3>'
        + (changes ? '<ul class="g-changes">' + changes + '</ul>'
          : '<p class="s">' + esc(T('grd.changes_none', 'The simulation shows nothing leaving or arriving and no approval granted.')) + '</p>');
      if (s.other_events) {
        out += '<p class="s n">' + esc(fill(T('grd.other_events', '{n} other event(s) not about your account.'), { n: s.other_events })) + '</p>';
      }
    }
    var sim = simLine(body, T);
    if (sim) out += '<p class="s n g-sim">' + esc(sim) + '</p>';

    if (body.receipt && body.receipt.verify) {
      out += '<p class="s g-receipt">' + esc(T('grd.receipt', 'This verdict is sealed in today’s root.'))
        + ' <a href="' + esc(body.receipt.verify) + '">' + esc(T('grd.receipt_open', 'Open the receipt')) + '</a></p>';
    } else {
      out += '<p class="s n g-receipt">' + esc(T('grd.signin', 'Sign in to get a sealed receipt of what you were told.')) + '</p>';
    }
    return out + '</section>';
  }

  /** The request body for the form's current mode, or null when it is incomplete. */
  function requestFor(mode, f) {
    var chain = Number(f.chain) || 8453;
    if (mode === 'tx') {
      if (!f.data && !f.to) return null;
      var tx = { to: f.to || undefined, data: f.data || '0x' };
      if (f.from) tx.from = f.from;
      if (f.value) tx.value = f.value;
      return { request: 'tx', tx: tx, chain_id: chain };
    }
    var text = String(f.text || '').trim();
    if (!text) return null;
    var v;
    try { v = JSON.parse(text); } catch (e) { v = text; }
    return mode === 'sig'
      ? { request: 'typed_data', typed_data: v, chain_id: chain }
      : { request: 'authorization', authorization: v, chain_id: chain };
  }

  return { render: render, requestFor: requestFor, when: when, CHANGE_EN: CHANGE_EN, LOOK: LOOK };
}));

(function () {
  'use strict';
  if (typeof document === 'undefined' || !document.getElementById('g-go')) return;
  var P = window.RCGuardPage;
  var $ = function (id) { return document.getElementById(id); };
  var T = function (k, en) {
    try { return (window.RCI18N && RCI18N.translate(k, RCI18N.getLang())) || en; } catch (e) { return en; }
  };
  var mode = 'tx';
  var PERMIT2 = '0x000000000022D473030F116dDEE9F6B43aC78BA3';
  var SPENDER = '0xEeeeeEeeeEeEeeEeEeEeeEEEeeeeEeeeeeeeEEeE'.toLowerCase().replace('0xeeee', '0xbad0');
  var EX = {
    approve: { mode: 'tx', to: '0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913',
      data: '0x095ea7b3000000000000000000000000' + SPENDER.slice(2) + 'ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff' },
    permit: { mode: 'sig', text: JSON.stringify({
      domain: { name: 'USD Coin', version: '2', chainId: 8453, verifyingContract: '0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913' },
      types: { Permit: [{ name: 'owner', type: 'address' }, { name: 'spender', type: 'address' }, { name: 'value', type: 'uint256' },
        { name: 'nonce', type: 'uint256' }, { name: 'deadline', type: 'uint256' }] },
      primaryType: 'Permit',
      message: { owner: '0x' + '12'.repeat(20), spender: SPENDER, value: '115792089237316195423570985008687907853269984665640564039457584007913129639935',
        nonce: '0', deadline: '99999999999' } }, null, 1) },
    p2: { mode: 'sig', text: JSON.stringify({
      domain: { name: 'Permit2', chainId: 8453, verifyingContract: PERMIT2 },
      types: { PermitSingle: [{ name: 'details', type: 'PermitDetails' }, { name: 'spender', type: 'address' }, { name: 'sigDeadline', type: 'uint256' }],
        PermitDetails: [{ name: 'token', type: 'address' }, { name: 'amount', type: 'uint160' }, { name: 'expiration', type: 'uint48' }, { name: 'nonce', type: 'uint48' }] },
      primaryType: 'PermitSingle',
      message: { details: { token: '0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913', amount: '1461501637330902918203684832716283019655932542975',
        expiration: '281474976710655', nonce: '0' }, spender: SPENDER, sigDeadline: '99999999999' } }, null, 1) },
    d7702: { mode: 'auth', text: JSON.stringify({ chainId: 0, address: SPENDER, nonce: 0 }) },
  };

  function setMode(m) {
    mode = m;
    document.querySelectorAll('[data-g-mode]').forEach(function (b) {
      b.setAttribute('aria-pressed', b.getAttribute('data-g-mode') === m ? 'true' : 'false');
    });
    $('g-tx').hidden = m !== 'tx';
    $('g-text').hidden = m === 'tx';
    $('g-text').setAttribute('placeholder', m === 'sig'
      ? T('grd.f_typed', 'Paste the typed data the wallet asks you to sign (eth_signTypedData_v4)')
      : T('grd.f_auth', 'Paste the authorization: {"chainId", "address", "nonce"}'));
  }

  function fields() {
    return { from: $('g-from').value.trim(), to: $('g-to').value.trim(), data: $('g-data').value.trim(),
      value: $('g-value').value.trim(), text: $('g-text').value, chain: $('g-chain').value };
  }

  function check() {
    var body = P.requestFor(mode, fields());
    if (!body) return;
    $('g-out').innerHTML = '<section class="panel g-out"><p class="s n">' + T('grd.busy', 'Checking…') + '</p></section>';
    var headers = { 'content-type': 'application/json' };
    // The session is the cookie. A same-origin fetch sends it. A leftover
    // localStorage token is traded for that cookie by i18n, not sent here.
    fetch('/api/guard/check', { method: 'POST', credentials: 'same-origin', headers: headers, body: JSON.stringify(body) })
      .then(function (r) { return r.ok ? r.json() : null; })
      .catch(function () { return null; })
      .then(function (b) {
        $('g-out').innerHTML = P.render(b, T);
        // Present only when the v0.1 script was served. Absent, this is the v0 page.
        if (window.RCGuardV01 && typeof window.RCGuardV01.onVerdict === 'function') {
          window.RCGuardV01.onVerdict({ mode: mode, fields: fields(), out: $('g-out') });
        }
      });
  }

  document.querySelectorAll('[data-g-mode]').forEach(function (b) {
    b.addEventListener('click', function () { setMode(b.getAttribute('data-g-mode')); });
  });
  document.querySelectorAll('[data-g-ex]').forEach(function (b) {
    b.addEventListener('click', function () {
      var ex = EX[b.getAttribute('data-g-ex')];
      if (!ex) return;
      setMode(ex.mode);
      if (ex.mode === 'tx') { $('g-to').value = ex.to; $('g-data').value = ex.data; }
      else $('g-text').value = ex.text;
      check();
    });
  });
  $('g-go').addEventListener('click', check);
  setMode('tx');
})();
