/*
 * The proof block of the DogeOS app: the daily seal roots, their anchors on
 * every chain, and the anchoring itself, done by the player's own wallet.
 *
 * WHAT IT SHOWS. The latest completed days from /api/roots: each day's root
 * (the Merkle root of every seal minted that day), how many seals it covers,
 * and an anchor cell per chain the day is anchored on, painted only from what
 * that chain said (anchor_cell.js, the roots page's own renderer, re-checked
 * live through /api/roots/verify). A day with no anchor on the DogeOS testnet
 * offers the button.
 *
 * WHAT THE BUTTON DOES. It asks the server for the plan — the exact zero-value
 * transaction, a self-send whose data is RCROOT1:<day>:<root> — and hands it
 * to the wallet. The wallet signs and sends; the server never holds a key.
 * The hash goes back to POST /api/roots/anchor, which reads the chain and
 * records the anchor only once the calldata is confirmed. Until the chain
 * answers, the row says what it is waiting for; it never says "anchored" on
 * the strength of a hash the page has not seen confirmed.
 *
 * HONESTY. A feed that could not be read is said to be unreadable, never
 * shown as "no roots yet": that sentence is a claim about the calls. The
 * one-per-minute budget on anchor submissions is the server's; the page asks
 * every twelve seconds while a transaction is still being mined and stops
 * after two minutes with the hash on screen, so the person can submit it
 * again later rather than lose it.
 */
(function (root, factory) {
  if (typeof module === 'object' && module.exports) module.exports = factory();
  else root.RCDogeProof = factory();
}(typeof self !== 'undefined' ? self : this, function () {
  'use strict';

  var CHAIN_KEY = 'dogeos-testnet';
  /** How many completed days the block lists. */
  var DAYS = 7;
  /** Re-ask the server about a submitted hash this often, this many times (the submit route allows six a minute). */
  var POLL_MS = 12000;
  var POLL_TRIES = 10;

  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }

  /** The day's anchors: the feed's list, or its older Base-only column. */
  function anchorsOf(r) {
    var list = r && Array.isArray(r.anchors) ? r.anchors.slice() : [];
    if (!list.length && r && r.anchor_tx) list.push({ chain: 'base', tx: r.anchor_tx });
    return list;
  }

  function create() {
    var ctx = null;
    var feed = null;          // { roots, chains } once read
    var feedError = null;     // the reason the feed could not be read
    var verdicts = {};        // day -> chain -> verify body
    var notes = {};           // day -> what the anchoring is doing or did
    var busy = {};            // day -> true while a transaction is in flight

    function chainsByKey() {
      var m = {};
      ((feed && feed.chains) || []).forEach(function (c) { m[c.key] = c; });
      return m;
    }

    /** The roots page's own cell renderer: on the page, or required under Node. */
    function renderer() {
      if (typeof window !== 'undefined' && window.RCAnchorCell) return window.RCAnchorCell;
      if (typeof require === 'function') { try { return require('./anchor_cell.js'); } catch (e) { return null; } }
      return null;
    }
    function cells(r) {
      var R = renderer();
      if (!R) return '<span class="n">anchor state unavailable</span>';
      return R.anchorCells(r, verdicts[r.day] || {}, null, chainsByKey());
    }

    function rowHtml(r) {
      var hasDoge = anchorsOf(r).some(function (a) { return a.chain === CHAIN_KEY; });
      var button = hasDoge ? '' : (busy[r.day]
        ? '<button type="button" class="m-btn m-btn--ghost" disabled>Anchoring…</button>'
        : '<button type="button" class="m-btn m-btn--ghost" data-anchor-day="' + esc(r.day) + '">Anchor on DogeOS testnet</button>');
      return '<div class="p-proof-row" data-day="' + esc(r.day) + '">'
        + '<div class="p-proof-hd"><b>' + esc(r.day) + '</b> <span class="n">' + (Number(r.seal_count) || 0) + ' seals</span></div>'
        + '<div class="p-proof-root"><code>' + esc(r.root) + '</code></div>'
        + '<div class="p-proof-anchors">' + cells(r) + '</div>'
        + (button ? '<div class="p-proof-act">' + button + '</div>' : '')
        + (notes[r.day] ? '<div class="p-proof-note n">' + esc(notes[r.day]) + '</div>' : '')
        + '</div>';
    }

    function html() {
      var body;
      if (feedError) {
        body = '<p class="m-problem m-problem--warn">The roots feed could not be read: ' + esc(feedError) + '</p>';
      } else if (!feed) {
        body = '<p class="n">Reading the daily roots…</p>';
      } else if (!feed.roots.length) {
        body = '<p class="n">No completed day has sealed calls yet. The first root appears the day after the first seals are minted.</p>';
      } else {
        body = feed.roots.slice(0, DAYS).map(rowHtml).join('');
      }
      return '<section class="p-proof">'
        + '<h2 class="m-h2">Proof on DogeOS</h2>'
        + '<p class="n">Every call is sealed when it is made. Each completed day’s seals fold into one Merkle root, '
        + 'and a root anchored on the DogeOS testnet carries that chain’s block time as an independent upper bound on '
        + 'every seal in the day. Anchoring is done by your wallet: a zero-value transaction to yourself with the root as '
        + 'its data. The server never signs.</p>'
        + body
        + '</section>';
    }

    function verify(day, chainKey) {
      if (!ctx) return Promise.resolve();
      return ctx.api('/api/roots/verify/' + encodeURIComponent(day) + '?chain=' + encodeURIComponent(chainKey))
        .then(function (r) {
          if (!r.ok || !r.body) return;
          verdicts[day] = verdicts[day] || {};
          verdicts[day][chainKey] = r.body;
        }).catch(function () { /* stays unknown, which is the truth */ });
    }

    function reload() {
      if (!ctx) return Promise.resolve();
      return ctx.api('/api/roots').then(function (r) {
        if (!r.ok || !r.body || !Array.isArray(r.body.roots)) {
          feedError = 'HTTP ' + r.status;
          return;
        }
        feedError = null;
        feed = { roots: r.body.roots, chains: r.body.chains || [] };
        var asks = [];
        feed.roots.slice(0, DAYS).forEach(function (row) {
          anchorsOf(row).forEach(function (a) { asks.push(verify(row.day, a.chain)); });
        });
        return Promise.all(asks);
      }, function (err) {
        feedError = String((err && err.message) || err || 'unreadable');
      }).then(function () { if (ctx) ctx.render(); });
    }

    /** Submit a sent transaction until the chain has mined it, within the budget above. */
    function submit(day, txHash, tries) {
      return ctx.api('/api/roots/anchor', { method: 'POST', body: { day: day, tx_hash: txHash, chain: CHAIN_KEY } })
        .then(function (r) {
          if (r.ok) {
            notes[day] = 'Anchored on DogeOS testnet: ' + txHash;
            ctx.toast('ok', 'Anchored on the DogeOS testnet.');
            return true;
          }
          if (r.status === 409) {
            notes[day] = 'This day was anchored on DogeOS testnet by another wallet first.';
            return true;
          }
          if (r.status === 503 && tries > 1) {
            notes[day] = 'Sent ' + txHash + ' — waiting for DogeOS to mine it and confirm it 17 blocks deep (about a minute)…';
            ctx.render();
            return new Promise(function (resolve) { setTimeout(resolve, POLL_MS); })
              .then(function () { return submit(day, txHash, tries - 1); });
          }
          notes[day] = (r.body && r.body.error ? r.body.error : 'The anchor could not be recorded')
            + '. Transaction ' + txHash + ' — submit it again later from this row.';
          return false;
        });
    }

    function anchorDay(day) {
      var app = window.RCDogeApp;
      var eth = app && app.wallet();
      if (!eth) { notes[day] = 'No wallet found on this page.'; ctx.render(); return Promise.resolve(); }
      busy[day] = true;
      notes[day] = 'Asking the server for the exact transaction…';
      ctx.render();
      var from = '';
      return ctx.api('/api/roots/anchor-plan/' + encodeURIComponent(day) + '?chain=' + encodeURIComponent(CHAIN_KEY))
        .then(function (r) {
          if (!r.ok || !r.body) throw new Error((r.body && r.body.error) || ('HTTP ' + r.status));
          if (r.body.already_anchored) { notes[day] = 'Already anchored on DogeOS testnet.'; return null; }
          var plan = r.body;
          return app.chain().then(function (c) {
            return eth.request({ method: 'eth_requestAccounts' }).then(function (accounts) {
              from = String((accounts && accounts[0]) || '').trim();
              if (!from) throw { code: 4001 };
              return app.switchTo(eth, c);
            });
          }).then(function () {
            notes[day] = 'Confirm the zero-value transaction in your wallet (you pay only network gas).';
            ctx.render();
            return eth.request({ method: 'eth_sendTransaction', params: [{ from: from, to: from, value: '0x0', data: plan.data }] });
          }).then(function (txHash) {
            notes[day] = 'Sent ' + txHash + ' — waiting for DogeOS to mine it and confirm it 17 blocks deep (about a minute)…';
            ctx.render();
            return submit(day, String(txHash), POLL_TRIES);
          });
        })
        .catch(function (err) {
          notes[day] = app.declined(err) ? 'Not sent — nothing was changed.'
            : 'Anchoring did not complete: ' + String((err && err.message) || 'unknown error');
        })
        .then(function () { busy[day] = false; return reload(); });
    }

    function click(e) {
      var b = e.target.closest('[data-anchor-day]');
      if (!b || !ctx) return false;
      anchorDay(b.getAttribute('data-anchor-day'));
      return true;
    }

    function signedIn(c) { ctx = c; reload(); }

    return { place: 'main', tab: 'proof', label: 'Proof', icon: '⛓', html: html, click: click, signedIn: signedIn, reload: reload };
  }

  return { create: create, CHAIN_KEY: CHAIN_KEY, DAYS: DAYS, POLL_MS: POLL_MS, POLL_TRIES: POLL_TRIES, anchorsOf: anchorsOf };
}));
