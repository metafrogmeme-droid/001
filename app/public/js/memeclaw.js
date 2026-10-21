/*
 * Memeclaw's page: launch a memecoin from your own wallet on the DogeOS
 * testnet, and trade it on its bonding curve (docs/memeclaw/MEMECLAW_PLAN.md).
 *
 * THE SERVER WRITES EVERY TRANSACTION; THE WALLET SENDS IT. The page asks
 * /api/memeclaw for a plan (to, value, data), hands it to the wallet with
 * eth_sendTransaction, and gives the hash back so the server can read the
 * receipt from the chain. Nothing here encodes calldata, prices a trade or
 * decides what happened: the contract prices, the chain says, the server
 * reads it.
 *
 * THE CHAIN IS THE SERVER'S READING: id, name, public RPCs and explorer come
 * from /api/memeclaw/config, and evm-wallet.js asks the wallet for exactly
 * that chain.
 *
 * HONESTY. A wallet that is missing is said to be missing; a person who
 * declined gets no warning colour; a chain that did not answer is "could not
 * be read", never "failed"; a figure this site has not read is "not read",
 * never 0. Every name and word a creator wrote is drawn as text.
 */
(function (root, factory) {
  var api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  if (root) {
    root.RCMemeclaw = api;
    if (root.document && root.document.getElementById('mc-root')) {
      root.document.addEventListener('DOMContentLoaded', function () { api.start(root.document); });
      if (root.document.readyState !== 'loading') api.start(root.document);
    }
  }
}(typeof window !== 'undefined' ? window : null, function () {
  'use strict';

  var DECIMALS = 18;
  var POLL_MS = 6000;
  var POLL_TRIES = 30;

  // ── pure helpers (exported for tests) ──────────────────────────────────────

  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }

  /** "1.5" -> base units as a decimal string; null for anything that is not a plain amount. */
  function parseUnits(text, decimals) {
    var d = decimals == null ? DECIMALS : decimals;
    var s = String(text == null ? '' : text).trim().replace(/,/g, '');
    var m = /^(\d{0,30})(?:\.(\d*))?$/.exec(s);
    if (!m || (m[1] === '' && !m[2])) return null;
    var frac = (m[2] || '');
    if (frac.length > d) return null;
    var whole = (m[1] || '0') + frac + '0'.repeat(d - frac.length);
    var out = whole.replace(/^0+(?=\d)/, '');
    return out;
  }

  /** Base units -> "1.5", at most `maxFrac` decimals, rounded down. null stays null. */
  function formatUnits(units, maxFrac, decimals) {
    if (units === null || units === undefined || !/^\d+$/.test(String(units))) return null;
    var d = decimals == null ? DECIMALS : decimals;
    var s = String(units).replace(/^0+(?=\d)/, '');
    if (s.length <= d) s = '0'.repeat(d - s.length + 1) + s;
    var whole = s.slice(0, s.length - d);
    var frac = s.slice(s.length - d).slice(0, maxFrac == null ? 4 : maxFrac).replace(/0+$/, '');
    whole = whole.replace(/\B(?=(\d{3})+(?!\d))/g, ',');
    return frac ? whole + '.' + frac : whole;
  }

  function short(addr) {
    var a = String(addr || '');
    return a.length > 12 ? a.slice(0, 6) + '…' + a.slice(-4) : a;
  }

  function pct(bps) {
    if (bps === null || bps === undefined || !isFinite(Number(bps))) return null;
    return (Number(bps) / 100).toFixed(2) + '%';
  }

  /** A coin's mark: its symbol's first letter on a colour drawn from its address. */
  function hue(addr) {
    var h = 0;
    var a = String(addr || '').toLowerCase();
    for (var i = 2; i < a.length; i += 1) h = (h * 31 + a.charCodeAt(i)) % 360;
    return h;
  }

  /** The sentence for why Memeclaw is off. */
  function offSentence(reason) {
    switch (reason) {
      case 'not_deployed': return 'Memeclaw is not open yet: its contract has not been deployed on the DogeOS testnet.';
      case 'factory_mismatch': return 'Memeclaw is closed: the address it is set to does not hold the Memeclaw contract this site carries.';
      case 'factory_misconfigured': return 'Memeclaw is closed: its contract address is not set correctly.';
      case 'factory_unknown': return 'The DogeOS testnet could not be read just now, so this site cannot check the Memeclaw contract. Try again shortly.';
      case 'no_testnet': return 'Memeclaw runs on a testnet only, and none is configured.';
      default: return 'Memeclaw is not open right now.';
    }
  }

  /** The sentence for a refused launch or trade, by the server's reason code. */
  function refusal(code, detail) {
    var map = {
      name_length: 'A name is 1 to 32 characters.',
      name_characters: 'A name uses plain letters, digits, spaces and punctuation only (no accents, emoji or other scripts).',
      name_spaces: 'A name cannot start or end with a space.',
      symbol_length: 'A symbol is 1 to 10 characters.',
      symbol_characters: 'A symbol uses A to Z and 0 to 9 only.',
      symbol_reserved: detail === 'tokenized' ? 'That symbol is a stock, fund or tokenized asset: Memeclaw does not launch those.'
        : detail === 'majors' ? 'That symbol is a major coin: a launch may not imitate it.'
          : detail === 'money' ? 'That symbol is a stablecoin or a wrapped coin: a launch may not imitate it.'
            : 'That symbol is reserved.',
      name_reserved: 'That name is reserved: it names this project or a tokenized asset.',
      presented_as_security: 'A coin here may not present itself as a stock, a share, a fund or a tokenized asset.',
      name_link: 'A name cannot carry a link.',
      description_length: 'A description is at most 280 characters.',
      description_characters: 'The description has a character it cannot carry.',
      description_link: 'A description cannot carry a link.',
      dev_buy_amount: 'The first buy is an amount of testnet DOGE, like 0.5.',
      dev_buy_too_large: 'That first buy is more than the whole supply costs (40 testnet DOGE).',
      dev_buy_too_small: 'That first buy is too small to receive a coin.',
      creator_address: 'Connect a wallet first.',
      sold_out: 'That buy is larger than the supply left.',
      too_small: 'That amount is too small to trade.',
      not_held: 'Your wallet does not hold that many.',
      unknown_coin: 'The factory does not know that coin.',
      amount: 'Enter an amount.',
      memeclaw_off: 'Memeclaw is not open right now.',
      not_deployed: 'Memeclaw is not open yet: its contract is not deployed.',
      coin_address: 'That is not a coin address.',
      wallet_address: 'Connect a wallet first.',
      slippage: 'The price tolerance is between 0% and 10%.',
      action: 'That is neither a buy nor a sell.',
    };
    return map[code] || 'The server refused that (' + esc(code || 'unknown') + ').';
  }

  // ── the page ───────────────────────────────────────────────────────────────

  function start(doc) {
    if (start.started) return;
    start.started = true;
    var W = (typeof window !== 'undefined' && window.RCEvmWallet) || null;
    var root = doc.getElementById('mc-root');
    var walletBox = doc.getElementById('mc-wallet');
    var state = { cfg: null, account: null, view: 'coins', coin: null, busy: false, operator: null };

    function api(path, opts) {
      var o = opts || {};
      return fetch(path, {
        method: o.method || 'GET',
        headers: o.body ? { 'content-type': 'application/json', accept: 'application/json' } : { accept: 'application/json' },
        body: o.body ? JSON.stringify(o.body) : undefined,
      }).then(function (r) {
        return r.json().then(function (b) { return { ok: r.ok, status: r.status, body: b }; },
          function () { return { ok: false, status: r.status, body: null, unreadable: true }; });
      }, function () { return { ok: false, status: 0, body: null, unreadable: true }; });
    }

    function explorerTx(tx) { return state.cfg && state.cfg.chain && state.cfg.chain.explorer_tx ? state.cfg.chain.explorer_tx + tx : null; }
    function explorerAddr(a) {
      var base = state.cfg && state.cfg.chain ? String(state.cfg.chain.explorer_tx || '').replace(/\/tx\/?$/, '') : '';
      return base ? base + '/address/' + a : null;
    }
    function link(href, text) { return href ? '<a href="' + esc(href) + '" rel="noopener" target="_blank">' + esc(text) + '</a>' : esc(text); }

    // ── wallet ──
    function drawWallet() {
      if (!W || !W.wallet()) {
        walletBox.innerHTML = '<span class="mc-muted">No wallet in this browser</span>';
        return;
      }
      if (state.account) {
        walletBox.innerHTML = '<span class="mc-addr" title="' + esc(state.account) + '">' + esc(short(state.account)) + '</span>';
        return;
      }
      walletBox.innerHTML = '<button type="button" class="mc-btn" data-act="connect">Connect wallet</button>';
    }

    function connect() {
      var eth = W && W.wallet();
      if (!eth) return Promise.resolve({ status: 'no-wallet' });
      if (!state.cfg || !state.cfg.chain) return Promise.resolve({ status: 'unknown' });
      return W.account(eth).then(function (a) {
        state.account = a;
        return W.switchTo(eth, state.cfg.chain);
      }).then(function () {
        drawWallet();
        return { status: 'connected' };
      }, function (err) {
        drawWallet();
        return { status: W.declined(err) ? 'rejected' : 'unknown' };
      });
    }

    function needWallet() {
      if (state.account) return Promise.resolve({ status: 'connected' });
      return connect();
    }

    function send(plan) {
      var eth = W.wallet();
      var tx = { from: state.account, value: plan.value || '0x0', data: plan.data };
      if (plan.to) tx.to = plan.to;
      return eth.request({ method: 'eth_sendTransaction', params: [tx] });
    }

    /** Ask `path` with { tx_hash } until the chain has it: a 503 is "not yet", anything else is the answer. */
    function settle(path, tx, onWait) {
      var tries = 0;
      return new Promise(function (resolve) {
        (function again() {
          tries += 1;
          api(path, { method: 'POST', body: { tx_hash: tx } }).then(function (r) {
            if (r.status === 503 && tries < POLL_TRIES) { if (onWait) onWait(tries); setTimeout(again, POLL_MS); return; }
            resolve(r);
          });
        }());
      });
    }

    function minedState(tx) {
      var tries = 0;
      return new Promise(function (resolve) {
        (function again() {
          tries += 1;
          api('/api/memeclaw/tx/' + encodeURIComponent(tx)).then(function (r) {
            var s = r.ok && r.body ? r.body.status : null;
            if ((s === 'pending' || s === 'missing' || r.status === 503) && tries < POLL_TRIES) { setTimeout(again, POLL_MS); return; }
            resolve(s || 'unknown');
          });
        }());
      });
    }

    function note(el, kind, html) {
      if (!el) return;
      el.className = 'mc-note' + (kind ? ' mc-note-' + kind : '');
      el.innerHTML = html;
    }

    function walletNote(el, r) {
      if (r.status === 'no-wallet') note(el, 'info', 'This browser has no wallet. Open this page in a wallet\'s browser, or install one, and add the DogeOS testnet.');
      else if (r.status === 'rejected') note(el, '', 'You declined in the wallet. Nothing was sent.');
      else note(el, 'warn', 'The wallet did not connect. Try again.');
    }

    // ── views ──
    function setView(v, coin) {
      state.view = v;
      state.coin = coin || null;
      var tabs = doc.querySelectorAll('.mc-tab');
      for (var i = 0; i < tabs.length; i += 1) tabs[i].setAttribute('aria-pressed', tabs[i].getAttribute('data-view') === v ? 'true' : 'false');
      draw();
    }

    function draw() {
      if (!state.cfg) { root.innerHTML = '<div class="mc-load">Loading…</div>'; return; }
      if (state.view === 'about') return drawAbout();
      if (!state.cfg.enabled) {
        root.innerHTML = '<div class="mc-card"><p>' + esc(offSentence(state.cfg.reason)) + '</p></div>' + operatorBox();
        return undefined;
      }
      if (state.view === 'launch') return drawLaunch();
      if (state.view === 'coin' && state.coin) return drawCoin(state.coin);
      return drawCoins();
    }

    function drawAbout() {
      var c = state.cfg || {};
      var faucet = c.chain && c.chain.faucet ? link(c.chain.faucet, 'the DogeOS testnet faucet') : 'the DogeOS testnet faucet';
      root.innerHTML = '<div class="mc-card mc-prose">'
        + '<h2>How it works</h2>'
        + '<p>Memeclaw is a launchpad on the <b>DogeOS testnet</b>. A coin launched here has no value, and testnet DOGE is free from ' + faucet + '.</p>'
        + '<p><b>Launching.</b> Your wallet sends the launch to the Memeclaw contract. The contract creates the coin with a fixed supply of 1,000,000,000 and holds all of it. It cannot create more, and it has no owner: nobody, this site included, can change it or take DOGE out of it except by selling coins back.</p>'
        + '<p><b>Trading.</b> Each coin trades on its own bonding curve, priced in testnet DOGE, with no fee. The price rises as coins are bought and falls as they are sold back. Buying out the whole supply costs 40 testnet DOGE. Once it is sold out, buys stop and sells still work.</p>'
        + '<p><b>The proof.</b> Before your wallet is asked, the page writes the launch\'s terms (name, symbol, description, your address) and the chain records their hash in the launch itself. Once this site has read the launch from the chain, it seals it into the day\'s public proof, with a receipt anyone can check.</p>'
        + '<p><b>What it is not.</b> Not a sale of anything with value, not advice, and not the RUNECLAW agent: the agent never launches, buys or sells these coins. Names and descriptions are their creators\' words; a coin that breaks the page\'s rules is not listed.</p>'
        + '</div>';
    }

    function coinMark(c) {
      return '<span class="mc-mark" style="background:hsl(' + hue(c.address) + ',55%,38%)" aria-hidden="true">'
        + esc(String(c.symbol || '?').charAt(0)) + '</span>';
    }

    function drawCoins() {
      root.innerHTML = '<div class="mc-load">Reading the coins…</div>';
      api('/api/memeclaw/coins').then(function (r) {
        if (state.view !== 'coins') return;
        if (!r.ok || !r.body || !Array.isArray(r.body.coins)) {
          root.innerHTML = '<div class="mc-card"><p class="mc-warn">The coins could not be read (HTTP ' + esc(r.status) + '). Nothing here is a statement that there are none.</p>'
            + '<button type="button" class="mc-btn" data-act="reload">Try again</button></div>';
          return;
        }
        var list = r.body.coins;
        if (!list.length) {
          root.innerHTML = '<div class="mc-card"><p>No coin has been launched yet. Be the first: <button type="button" class="mc-btn" data-view-go="launch">Launch a coin</button></p></div>';
          return;
        }
        root.innerHTML = '<ul class="mc-list">' + list.map(function (c) {
          return '<li><button type="button" class="mc-row" data-coin="' + esc(c.address) + '">'
            + coinMark(c)
            + '<span class="mc-row-main"><b>' + esc(c.symbol) + '</b> <span class="mc-muted">' + esc(c.name) + '</span>'
            + (c.terms === 'not_recorded' ? ' <span class="mc-chip mc-chip-dim">launched outside this page</span>' : '')
            + (c.hidden ? ' <span class="mc-chip mc-chip-warn">hidden: ' + esc(c.hidden) + '</span>' : '')
            + '<span class="mc-bar"><span style="width:' + Math.min(100, Number(c.sold_bps) / 100) + '%"></span></span>'
            + '<span class="mc-muted">' + esc(pct(c.sold_bps)) + ' sold · ' + esc(c.trade_count) + ' trade' + (c.trade_count === 1 ? '' : 's') + '</span></span>'
            + '<span class="mc-row-price">' + esc(c.price_doge) + ' <span class="mc-muted">DOGE</span></span>'
            + '</button></li>';
        }).join('') + '</ul>';
      });
    }

    function drawCoin(addr) {
      root.innerHTML = '<div class="mc-load">Reading the coin…</div>';
      var q = state.account ? '?wallet=' + encodeURIComponent(state.account) : '';
      api('/api/memeclaw/coins/' + encodeURIComponent(addr) + q).then(function (r) {
        if (state.view !== 'coin' || state.coin !== addr) return;
        if (r.status === 404) {
          root.innerHTML = '<div class="mc-card"><p>This site lists no such coin' + (r.body && r.body.error === 'hidden' ? ' (it is hidden from the page)' : '') + '.</p>'
            + '<button type="button" class="mc-btn" data-view-go="coins">All coins</button></div>';
          return;
        }
        if (!r.ok || !r.body || !r.body.coin) {
          root.innerHTML = '<div class="mc-card"><p class="mc-warn">The coin could not be read (HTTP ' + esc(r.status) + ').</p>'
            + '<button type="button" class="mc-btn" data-act="reload">Try again</button></div>';
          return;
        }
        var c = r.body.coin; var rd = r.body.reading || {}; var trades = r.body.trades || [];
        var held = r.body.wallet ? (r.body.wallet.held === null ? 'not read' : formatUnits(r.body.wallet.held, 2)) : null;
        root.innerHTML = '<div class="mc-card">'
          + '<div class="mc-head">' + coinMark(c) + '<div><h2>' + esc(c.symbol) + ' <span class="mc-muted">' + esc(c.name) + '</span></h2>'
          + (c.description ? '<p class="mc-desc">' + esc(c.description) + '</p>' : (c.terms === 'not_recorded' ? '<p class="mc-muted">Launched straight at the contract, not through this page: its terms are not recorded here.</p>' : ''))
          + '</div></div>'
          + '<div class="mc-kv">'
          + '<span>Price</span><span>' + esc(c.price_doge) + ' testnet DOGE per coin</span>'
          + '<span>Sold</span><span>' + esc(pct(c.sold_bps)) + ' of 1,000,000,000 · ' + esc(formatUnits(c.doge_reserve, 4)) + ' testnet DOGE in the curve</span>'
          + '<span>Coin</span><span>' + link(explorerAddr(c.address), short(c.address)) + '</span>'
          + '<span>Creator</span><span>' + link(explorerAddr(c.creator), short(c.creator)) + '</span>'
          + '<span>Launched</span><span>' + link(explorerTx(c.tx_hash), String(c.launched_at || '').replace('T', ' ').slice(0, 16) + ' UTC') + '</span>'
          + '<span>Receipt</span><span>' + (c.receipt ? '<a href="/call/' + esc(c.receipt) + '">sealed launch receipt</a>' : '<span class="mc-muted">none: ' + (c.terms === 'recorded' ? 'not sealed yet' : 'no terms recorded here') + '</span>') + '</span>'
          + (held !== null ? '<span>You hold</span><span>' + esc(held) + '</span>' : '')
          + '</div>'
          + '<details class="mc-reading"><summary>About this launch</summary><ul>'
          + '<li>Made by ' + esc(rd.made_by || 'the Memeclaw factory') + '.</li>'
          + '<li>The creator bought ' + esc(pct(rd.creator_bought_at_launch_bps)) + ' of the supply in the launch transaction.</li>'
          + '<li>The creator\'s net buys from the curve: ' + (rd.creator_net_from_curve_bps === null || rd.creator_net_from_curve_bps === undefined ? 'not read' : esc(pct(rd.creator_net_from_curve_bps))) + '.</li>'
          + '<li>Coins this creator has launched here: ' + esc(rd.creator_coins_launched == null ? 'not read' : rd.creator_coins_launched) + '.</li>'
          + '<li>Holders: ' + esc(rd.holders_note || 'not read') + '.</li>'
          + '</ul><p class="mc-muted">Facts about how the coin was made, not a judgement of it.</p></details>'
          + '</div>'
          + '<div class="mc-card mc-trade">'
          + '<h3>Buy</h3><label>Testnet DOGE to spend <input type="text" inputmode="decimal" id="mc-buy-amt" placeholder="0.5" autocomplete="off"></label>'
          + '<button type="button" class="mc-btn" data-act="buy">Get a quote</button><div id="mc-buy-note" class="mc-note"></div>'
          + '<h3>Sell</h3><label>Coins to sell <input type="text" inputmode="decimal" id="mc-sell-amt" placeholder="1000000" autocomplete="off"></label>'
          + '<button type="button" class="mc-btn" data-act="sell">Get a quote</button><div id="mc-sell-note" class="mc-note"></div>'
          + '<p class="mc-muted">Quotes come from the contract\'s curve as it is now; your wallet is asked to accept at most 1% less.</p>'
          + '</div>'
          + '<div class="mc-card"><h3>Trades</h3>' + (trades.length ? '<ul class="mc-trades">' + trades.map(function (t) {
            return '<li><span class="mc-' + (t.side === 'buy' ? 'buy' : 'sell') + '">' + esc(t.side) + '</span> '
              + esc(formatUnits(t.token_amount, 0)) + ' for ' + esc(formatUnits(t.doge_amount, 4)) + ' DOGE · '
              + link(explorerAddr(t.trader), short(t.trader)) + ' · ' + link(explorerTx(t.tx_hash), 'tx') + '</li>';
          }).join('') + '</ul>' : '<p class="mc-muted">No trade read yet.</p>')
          + '<p class="mc-muted">Read up to block ' + esc(c.read_to_block) + '.</p></div>'
          + operatorHide(c);
      });
    }

    function operatorHide(c) {
      if (!state.operator) return '';
      return '<div class="mc-card mc-op"><h3>Operator</h3><label>Reason to hide (empty shows it again) <input type="text" id="mc-hide-reason"></label>'
        + '<button type="button" class="mc-btn" data-act="hide" data-coin-hide="' + esc(c.address) + '">Save</button><div id="mc-hide-note" class="mc-note"></div></div>';
    }

    function drawLaunch() {
      root.innerHTML = '<div class="mc-card mc-launch"><h2>Launch a coin</h2>'
        + '<label>Name <input type="text" id="mc-name" maxlength="32" placeholder="Much Claw" autocomplete="off"></label>'
        + '<label>Symbol <input type="text" id="mc-symbol" maxlength="10" placeholder="CLAW" autocomplete="off"></label>'
        + '<label>Description (optional, no links) <textarea id="mc-desc" maxlength="280" rows="3"></textarea></label>'
        + '<label>Buy first, in the same transaction (testnet DOGE, optional) <input type="text" inputmode="decimal" id="mc-dev" placeholder="0" autocomplete="off"></label>'
        + '<button type="button" class="mc-btn mc-btn-main" data-act="launch">Launch from my wallet</button>'
        + '<div id="mc-launch-note" class="mc-note"></div>'
        + '<p class="mc-muted">The supply is 1,000,000,000, created once and held by the curve. The page will not take a name that names this project, a stock or fund, a major coin or a stablecoin, or carries a link.</p>'
        + '</div>';
      var sym = doc.getElementById('mc-symbol');
      if (sym) sym.addEventListener('input', function () { sym.value = sym.value.toUpperCase(); });
    }

    function operatorBox() {
      if (!state.operator) return '';
      return '<div class="mc-card mc-op"><h3>Operator: deploy the factory</h3>'
        + '<p>Your own wallet sends the factory\'s creation code to the DogeOS testnet. The contract refuses every other chain. Then set the two values the check prints and restart.</p>'
        + '<button type="button" class="mc-btn" data-act="deploy">Deploy from my wallet</button>'
        + '<div id="mc-deploy-note" class="mc-note"></div></div>';
    }

    // ── actions ──
    function doLaunch() {
      var el = doc.getElementById('mc-launch-note');
      var dev = (doc.getElementById('mc-dev').value || '').trim();
      var devUnits = dev === '' ? '0' : parseUnits(dev);
      if (devUnits === null) { note(el, 'warn', esc(refusal('dev_buy_amount'))); return; }
      note(el, 'info', 'Asking your wallet…');
      needWallet().then(function (w) {
        if (w.status !== 'connected') { walletNote(el, w); return undefined; }
        return api('/api/memeclaw/launch-plan', { method: 'POST', body: {
          creator: state.account, name: doc.getElementById('mc-name').value, symbol: doc.getElementById('mc-symbol').value,
          description: doc.getElementById('mc-desc').value, dev_buy: devUnits,
        } }).then(function (r) {
          if (!r.ok || !r.body || !r.body.data) {
            note(el, r.status === 400 ? 'warn' : 'warn', r.status === 400 ? esc(refusal(r.body && r.body.error, r.body && r.body.detail))
              : 'The plan could not be made (HTTP ' + esc(r.status) + '). Nothing was sent.');
            return undefined;
          }
          var plan = r.body;
          note(el, 'info', 'Confirm the launch in your wallet' + (plan.dev_buy !== '0' ? ': it also buys ' + esc(formatUnits(plan.expected_tokens, 0)) + ' coins for ' + esc(formatUnits(plan.dev_buy, 4)) + ' testnet DOGE' : '') + '.');
          return send(plan).then(function (tx) {
            note(el, 'info', 'Sent. Waiting for the DogeOS testnet to confirm it… ' + link(explorerTx(tx), 'view transaction'));
            return settle('/api/memeclaw/coins', tx).then(function (s) {
              if (s.ok && s.body && s.body.coin) {
                note(el, 'ok', 'Launched: ' + esc(s.body.coin.symbol) + '. ' + (s.body.sealed ? 'Its terms are sealed.' : ''));
                setView('coin', s.body.coin.address);
              } else if (s.status === 503) {
                note(el, 'info', 'The chain has not confirmed it yet. It will appear in the list once it does. ' + link(explorerTx(tx), 'view transaction'));
              } else {
                note(el, 'warn', 'The chain does not show a launch in that transaction: ' + esc((s.body && s.body.reason) || 'unknown') + '.');
              }
            });
          }, function (err) {
            note(el, W.declined(err) ? '' : 'warn', W.declined(err) ? 'You declined in the wallet. Nothing was sent.' : 'The wallet did not send it.');
          });
        });
      });
    }

    function doTrade(action) {
      var el = doc.getElementById(action === 'buy' ? 'mc-buy-note' : 'mc-sell-note');
      var raw = doc.getElementById(action === 'buy' ? 'mc-buy-amt' : 'mc-sell-amt').value;
      var amount = parseUnits(raw);
      if (amount === null || /^0+$/.test(amount)) { note(el, 'warn', esc(refusal('amount'))); return; }
      var coin = state.coin;
      needWallet().then(function (w) {
        if (w.status !== 'connected') { walletNote(el, w); return undefined; }
        return api('/api/memeclaw/trade-plan', { method: 'POST', body: { action: action, coin: coin, wallet: state.account, amount: amount } })
          .then(function (r) {
            if (!r.ok || !r.body || !r.body.plans) {
              if (r.status === 400) {
                var extra = r.body && r.body.max_buy ? ' At most ' + esc(formatUnits(r.body.max_buy, 4)) + ' testnet DOGE now.'
                  : r.body && r.body.held ? ' You hold ' + esc(formatUnits(r.body.held, 2)) + '.' : '';
                note(el, 'warn', esc(refusal(r.body && r.body.error)) + extra);
              } else note(el, 'warn', 'The quote could not be made (HTTP ' + esc(r.status) + '): the chain may not have answered.');
              return undefined;
            }
            var q = r.body.quote;
            var gets = action === 'buy' ? esc(formatUnits(q.gets, 0)) + ' coins (at least ' + esc(formatUnits(q.floor, 0)) + ')'
              : esc(formatUnits(q.gets, 6)) + ' testnet DOGE (at least ' + esc(formatUnits(q.floor, 6)) + ')';
            note(el, 'info', 'You get about ' + gets + '. Confirm in your wallet' + (r.body.plans.length > 1 ? ': first the approval, then the sale.' : '.'));
            return runSteps(r.body.plans, el, action, amount);
          });
      });
    }

    function runSteps(plans, el, action, amount) {
      var first = plans[0];
      return send(first).then(function (tx) {
        if (first.step === 'approve') {
          note(el, 'info', 'Approval sent. Waiting for it to confirm before the sale…');
          return minedState(tx).then(function (s) {
            if (s !== 'success') { note(el, 'warn', 'The approval ' + (s === 'failed' ? 'failed on chain' : 'was not confirmed') + '. Nothing was sold.'); return undefined; }
            // Ask again: the quote may have moved while the approval was mined.
            return api('/api/memeclaw/trade-plan', { method: 'POST', body: { action: action, coin: state.coin, wallet: state.account, amount: amount } })
              .then(function (r) {
                if (!r.ok || !r.body || !r.body.plans) { note(el, 'warn', 'The sale could not be planned after the approval. Your coins were not sold.'); return undefined; }
                return runSteps(r.body.plans.filter(function (p) { return p.step !== 'approve'; }), el, action, amount);
              });
          });
        }
        note(el, 'info', 'Sent. Waiting for the chain… ' + link(explorerTx(tx), 'view transaction'));
        return settle('/api/memeclaw/trades', tx).then(function (s) {
          if (s.ok) { note(el, 'ok', 'Done. ' + link(explorerTx(tx), 'view transaction')); setTimeout(function () { if (state.view === 'coin') drawCoin(state.coin); }, 1500); }
          else if (s.status === 503) note(el, 'info', 'Not confirmed yet; it will show once the chain has it. ' + link(explorerTx(tx), 'view transaction'));
          else note(el, 'warn', 'The chain does not show that trade: ' + esc((s.body && s.body.reason) || 'unknown') + '.');
        });
      }, function (err) {
        note(el, W.declined(err) ? '' : 'warn', W.declined(err) ? 'You declined in the wallet. Nothing was sent.' : 'The wallet did not send it.');
      });
    }

    function doDeploy() {
      var el = doc.getElementById('mc-deploy-note');
      needWallet().then(function (w) {
        if (w.status !== 'connected') { walletNote(el, w); return undefined; }
        return api('/api/memeclaw/deploy-plan').then(function (r) {
          if (!r.ok || !r.body || !r.body.data) { note(el, 'warn', 'The deploy plan could not be read (HTTP ' + esc(r.status) + ').'); return undefined; }
          note(el, 'info', 'Confirm the deployment in your wallet.');
          return send(r.body).then(function (tx) {
            note(el, 'info', 'Sent. Waiting for the chain, then checking the code…');
            return settle('/api/memeclaw/deployment', tx).then(function (s) {
              if (s.ok && s.body && s.body.set) {
                note(el, 'ok', 'The factory is deployed and its code checks out. Set MEMECLAW_FACTORY=' + esc(s.body.set.MEMECLAW_FACTORY)
                  + ' and MEMECLAW_FACTORY_BLOCK=' + esc(s.body.set.MEMECLAW_FACTORY_BLOCK) + ', then restart.');
              } else note(el, 'warn', 'The deployment did not check out: ' + esc((s.body && s.body.reason) || ('HTTP ' + s.status)) + '.');
            });
          }, function (err) {
            note(el, W.declined(err) ? '' : 'warn', W.declined(err) ? 'You declined in the wallet. Nothing was sent.' : 'The wallet did not send it.');
          });
        });
      });
    }

    function doHide(addr) {
      var el = doc.getElementById('mc-hide-note');
      api('/api/memeclaw/coins/' + encodeURIComponent(addr) + '/hide', { method: 'POST', body: { reason: doc.getElementById('mc-hide-reason').value } })
        .then(function (r) { note(el, r.ok ? 'ok' : 'warn', r.ok ? (r.body.hidden ? 'Hidden from the page.' : 'Shown again.') : 'Not saved (HTTP ' + esc(r.status) + ').'); });
    }

    doc.addEventListener('click', function (e) {
      var t = e.target && e.target.closest ? e.target.closest('[data-act],[data-view],[data-view-go],[data-coin]') : null;
      if (!t) return;
      if (t.hasAttribute('data-view')) { setView(t.getAttribute('data-view')); return; }
      if (t.hasAttribute('data-view-go')) { setView(t.getAttribute('data-view-go')); return; }
      if (t.hasAttribute('data-coin')) { setView('coin', t.getAttribute('data-coin')); return; }
      var act = t.getAttribute('data-act');
      if (act === 'connect') connect().then(function (r) { if (r.status === 'connected' && state.view === 'coin') drawCoin(state.coin); });
      else if (act === 'reload') { if (state.cfg) draw(); else boot(); }
      else if (act === 'launch') doLaunch();
      else if (act === 'buy' || act === 'sell') doTrade(act);
      else if (act === 'deploy') doDeploy();
      else if (act === 'hide') doHide(t.getAttribute('data-coin-hide'));
    });

    function boot() {
      root.innerHTML = '<div class="mc-load">Loading…</div>';
      api('/api/memeclaw/config').then(function (r) {
        if (!r.ok || !r.body) {
          root.innerHTML = '<div class="mc-card"><p class="mc-warn">Memeclaw\'s settings could not be read (HTTP ' + esc(r.status) + ').</p>'
            + '<button type="button" class="mc-btn" data-act="reload">Try again</button></div>';
          return;
        }
        state.cfg = r.body;
        drawWallet();
        // The operator's doors answer 403 to anyone else, and the page shows them nothing.
        api('/api/memeclaw/deploy-plan').then(function (d) { state.operator = d.ok; if (state.operator) draw(); });
        draw();
      });
    }

    drawWallet();
    boot();
  }

  return { start: start, esc: esc, parseUnits: parseUnits, formatUnits: formatUnits, short: short, pct: pct,
    hue: hue, offSentence: offSentence, refusal: refusal };
}));
