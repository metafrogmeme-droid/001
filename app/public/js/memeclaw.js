/*
 * Memeclaw's page: launch and trade memecoins, as practice (no wallet, no
 * chain) or on the DogeOS testnet from your own wallet
 * (docs/memeclaw/MEMECLAW_PLAN.md).
 *
 * PRACTICE. Practice coins and practice DOGE live on this site and nowhere
 * else. A practice player is a random key this browser keeps
 * (localStorage); the server keeps only its hash. Clearing the browser's
 * data loses it, and the page says so.
 *
 * TESTNET. The server writes every transaction; the wallet sends it. The page
 * asks /api/memeclaw for a plan (to, value, data), hands it to the wallet with
 * eth_sendTransaction, and gives the hash back so the server can read the
 * receipt from the chain. Nothing here encodes calldata, prices a trade or
 * decides what happened: the contract prices, the chain says, the server
 * reads it. The chain is the server's reading too: id, name, public RPCs and
 * explorer come from /api/memeclaw/config.
 *
 * RECEIPTS are checked here, in this browser (pi-receipt.js, the Pi app's
 * check), so no one is sent to another site to check one.
 *
 * HONESTY. A wallet that is missing is said to be missing; a person who
 * declined gets no warning colour; a server or chain that did not answer is
 * "could not be read", never "none"; a figure not read is "not read", never
 * 0. Every name and word a creator wrote is drawn as text.
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
  var SLIPPAGE_BPS = 100;
  var KEY_STORE = 'memeclaw.practice.key';
  var MODE_STORE = 'memeclaw.mode';
  var LAUNCH_KEY = /^launch:[0-9a-f]{64}$/;

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
    return whole.replace(/^0+(?=\d)/, '');
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

  /** `units` less SLIPPAGE_BPS, rounded down: the floor a quote is accepted at. */
  function floorOf(units) {
    if (!/^\d+$/.test(String(units))) return '0';
    var s = String(units);
    // BigInt where the browser has it; every browser this page targets does.
    return (BigInt(s) * BigInt(10000 - SLIPPAGE_BPS) / 10000n).toString();
  }

  function short(addr) {
    var a = String(addr || '');
    return a.length > 12 ? a.slice(0, 6) + '…' + a.slice(-4) : a;
  }

  function pct(bps) {
    if (bps === null || bps === undefined || !isFinite(Number(bps))) return null;
    return (Number(bps) / 100).toFixed(2) + '%';
  }

  /** A coin's mark: its symbol's first letter on a colour drawn from its id. */
  function hue(id) {
    var h = 0;
    var a = String(id || '').toLowerCase();
    for (var i = 0; i < a.length; i += 1) h = (h * 31 + a.charCodeAt(i)) % 360;
    return h;
  }

  /** The sentence for why the testnet launchpad is off. */
  function offSentence(reason) {
    switch (reason) {
      case 'not_deployed': return 'The testnet launchpad is not open yet: its contract has not been deployed on the DogeOS testnet. Practice mode works now.';
      case 'factory_mismatch': return 'The testnet launchpad is closed: the address it is set to does not hold the Memeclaw contract this site carries.';
      case 'factory_misconfigured': return 'The testnet launchpad is closed: its contract address is not set correctly.';
      case 'factory_unknown': return 'The DogeOS testnet could not be read just now, so this site cannot check the Memeclaw contract. Try again shortly.';
      case 'no_testnet': return 'The launchpad runs on a testnet only, and none is configured.';
      default: return 'The testnet launchpad is not open right now.';
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
      dev_buy_amount: 'The first buy is an amount of DOGE, like 0.5.',
      dev_buy_too_large: 'That first buy is more than the whole supply costs (40 DOGE).',
      dev_buy_too_small: 'That first buy is too small to receive a coin.',
      creator_address: 'Connect a wallet first.',
      sold_out: 'That buy is larger than the supply left.',
      too_small: 'That amount is too small to trade.',
      not_held: 'You do not hold that many.',
      unknown_coin: 'That coin is not listed.',
      amount: 'Enter an amount.',
      memeclaw_off: 'The testnet launchpad is not open right now.',
      not_deployed: 'The testnet launchpad is not open yet: its contract is not deployed.',
      coin_address: 'That is not a coin.',
      wallet_address: 'Connect a wallet first.',
      slippage: 'The price moved before your trade: someone traded first. Get a new quote.',
      action: 'That is neither a buy nor a sell.',
      not_enough_doge: 'You do not have that much practice DOGE.',
      launches_today: 'That is five practice launches today: the next one opens tomorrow (UTC).',
      refill_not_needed: 'You still have practice DOGE: the top-up is for when you have less than 1.',
      refill_too_soon: 'The top-up is once a day.',
      practice_key: 'This browser\'s practice player was not found. Start a new one.',
    };
    return map[code] || 'The server refused that (' + esc(code || 'unknown') + ').';
  }

  /** The launch receipt's facts, from its sealed payload (Provable Calls v8). */
  function launchFacts(p) {
    if (!p || p.kind !== 'memeclaw_launch') return [];
    return [
      ['What it is', 'A launch on the Memeclaw testnet, sealed once this site read it from the chain'],
      ['Coin', String(p.symbol) + ' · ' + String(p.name)],
      ['Coin address', String(p.token)],
      ['Creator', String(p.creator)],
      ['Terms hash', String(p.terms_hash) + ' (recorded by the factory in its Launched event)'],
      ['Launch transaction', String(p.tx) + ' · block ' + String(p.block_number)],
      ['Launched at', String(p.launched_at) + ' (the block\'s time)'],
    ];
  }

  // ── the page ───────────────────────────────────────────────────────────────

  function start(doc) {
    if (start.started) return;
    start.started = true;
    var W = (typeof window !== 'undefined' && window.RCEvmWallet) || null;
    var R = (typeof window !== 'undefined' && window.RCPiReceipt) || null;
    var root = doc.getElementById('mc-root');
    var walletBox = doc.getElementById('mc-wallet');
    var modeChip = doc.getElementById('mc-mode-chip');
    var state = { cfg: null, cfgFailed: false, account: null, mode: 'practice', view: 'coins', coin: null,
      operator: null, starters: [], key: null, me: null, quote: null };

    function store(k, v) {
      try { if (v === null) window.localStorage.removeItem(k); else window.localStorage.setItem(k, v); } catch (e) { /* private mode */ }
    }
    function load(k) {
      try { return window.localStorage.getItem(k); } catch (e) { return null; }
    }

    function api(path, opts) {
      var o = opts || {};
      var headers = { accept: 'application/json' };
      if (o.body) headers['content-type'] = 'application/json';
      if (o.practice && state.key) headers['x-memeclaw-practice'] = state.key;
      return fetch(path, { method: o.method || 'GET', headers: headers, body: o.body ? JSON.stringify(o.body) : undefined })
        .then(function (r) {
          return r.json().then(function (b) { return { ok: r.ok, status: r.status, body: b }; },
            function () { return { ok: false, status: r.status, body: null, unreadable: true }; });
        }, function () { return { ok: false, status: 0, body: null, unreadable: true }; });
    }
    function papi(path, opts) { return api(path, Object.assign({}, opts || {}, { practice: true })); }

    function explorerTx(tx) { return state.cfg && state.cfg.chain && state.cfg.chain.explorer_tx ? state.cfg.chain.explorer_tx + tx : null; }
    function explorerAddr(a) {
      var base = state.cfg && state.cfg.chain ? String(state.cfg.chain.explorer_tx || '').replace(/\/tx\/?$/, '') : '';
      return base ? base + '/address/' + a : null;
    }
    function link(href, text) { return href ? '<a href="' + esc(href) + '" rel="noopener" target="_blank">' + esc(text) + '</a>' : esc(text); }

    function note(el, kind, html) {
      if (!el) return;
      el.className = 'mc-note' + (kind ? ' mc-note-' + kind : '');
      el.innerHTML = html;
    }
    function declinedNote(el, err) {
      note(el, W && W.declined(err) ? '' : 'warn', W && W.declined(err) ? 'You declined in the wallet. Nothing was sent.' : 'The wallet did not send it.');
    }

    // ── the practice player ──
    function readMe() {
      if (!state.key) { state.me = null; drawWallet(); return Promise.resolve(null); }
      return papi('/api/memeclaw/paper/me').then(function (r) {
        if (r.status === 401) { state.key = null; store(KEY_STORE, null); state.me = null; }
        else if (r.ok) state.me = r.body;
        else state.me = { unread: true };
        drawWallet();
        return state.me;
      });
    }
    function needPlayer() {
      if (state.key) return Promise.resolve(true);
      return api('/api/memeclaw/paper/players', { method: 'POST', body: {} }).then(function (r) {
        if (!r.ok || !r.body || !r.body.key) return false;
        state.key = r.body.key;
        store(KEY_STORE, state.key);
        return readMe().then(function () { return true; });
      });
    }

    // ── the wallet box ──
    function drawWallet() {
      if (state.mode === 'practice') {
        if (!state.key) { walletBox.innerHTML = '<button type="button" class="mc-btn" data-act="paper-start">Start practising</button>'; return; }
        if (!state.me) { walletBox.innerHTML = '<span class="mc-muted">Practice…</span>'; return; }
        if (state.me.unread) { walletBox.innerHTML = '<span class="mc-muted">Practice balance not read</span>'; return; }
        var doge = state.me.player && state.me.player.practice_doge;
        var low = doge && BigInt(doge) < BigInt(state.me.refill.below);
        walletBox.innerHTML = '<span class="mc-addr" title="Practice DOGE: no value, this site only">'
          + esc(formatUnits(doge, 2)) + ' practice DOGE</span>'
          + (low && !state.me.refill.next_at ? ' <button type="button" class="mc-btn" data-act="paper-refill">Top up</button>' : '');
        return;
      }
      if (!W || !W.wallet()) { walletBox.innerHTML = '<span class="mc-muted">No wallet in this browser</span>'; return; }
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
      }).then(function () { drawWallet(); return { status: 'connected' }; },
        function (err) { drawWallet(); return { status: W.declined(err) ? 'rejected' : 'unknown' }; });
    }
    function needWallet() { return state.account ? Promise.resolve({ status: 'connected' }) : connect(); }
    function walletNote(el, r) {
      if (r.status === 'no-wallet') note(el, 'info', 'This browser has no wallet. Open this page in a wallet\'s browser, or install one, and add the DogeOS testnet. Practice mode needs no wallet.');
      else if (r.status === 'rejected') note(el, '', 'You declined in the wallet. Nothing was sent.');
      else note(el, 'warn', 'The wallet did not connect. Try again.');
    }
    function send(plan) {
      var tx = { from: state.account, value: plan.value || '0x0', data: plan.data };
      if (plan.to) tx.to = plan.to;
      return W.wallet().request({ method: 'eth_sendTransaction', params: [tx] });
    }
    /** Ask `path` with { tx_hash } until the chain has it: a 503 is "not yet", anything else is the answer. */
    function settle(path, tx) {
      var tries = 0;
      return new Promise(function (resolve) {
        (function again() {
          tries += 1;
          api(path, { method: 'POST', body: { tx_hash: tx } }).then(function (r) {
            if (r.status === 503 && tries < POLL_TRIES) { setTimeout(again, POLL_MS); return; }
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

    // ── views ──
    function setMode(m) {
      state.mode = m === 'testnet' ? 'testnet' : 'practice';
      store(MODE_STORE, state.mode);
      var b = doc.querySelectorAll('.mc-mode');
      for (var i = 0; i < b.length; i += 1) b[i].setAttribute('aria-pressed', b[i].getAttribute('data-mode') === state.mode ? 'true' : 'false');
      if (modeChip) modeChip.textContent = state.mode === 'testnet' ? 'DogeOS testnet' : 'Practice';
      state.coin = null; state.quote = null;
      if (state.view === 'coin') state.view = 'coins';
      drawWallet();
      draw();
    }
    function setView(v, coin) {
      state.view = v;
      state.coin = coin || null;
      state.quote = null;
      var tabs = doc.querySelectorAll('.mc-tab');
      for (var i = 0; i < tabs.length; i += 1) tabs[i].setAttribute('aria-pressed', tabs[i].getAttribute('data-view') === v ? 'true' : 'false');
      draw();
    }

    function draw() {
      if (state.view === 'about') return drawAbout();
      if (state.mode === 'practice') {
        if (state.view === 'launch') return drawLaunch();
        if (state.view === 'coin' && state.coin) return drawPaperCoin(state.coin);
        return drawCoins();
      }
      if (!state.cfg) {
        root.innerHTML = state.cfgFailed
          ? '<div class="mc-card"><p class="mc-warn">The testnet launchpad\'s settings could not be read. Practice mode does not need them.</p><button type="button" class="mc-btn" data-act="reload">Try again</button></div>'
          : '<div class="mc-load">Loading…</div>';
        return undefined;
      }
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
        + '<p><b>Two modes, neither with value.</b> <b>Practice</b> needs no wallet: practice coins and practice DOGE live on this site and nowhere else, and you start with 100 practice DOGE. <b>Testnet</b> uses your own wallet on the DogeOS testnet, where coins have no value and testnet DOGE is free from ' + faucet + '.</p>'
        + '<p><b>Launching.</b> A coin has a fixed supply of 1,000,000,000, all held by its bonding curve. Nothing can create more of it, and the curve has no owner: nobody, this site included, can change it or take DOGE out of it except by selling coins back.</p>'
        + '<p><b>Trading.</b> The price rises as coins are bought and falls as they are sold back, with no fee. Buying out the whole supply costs 40 DOGE. Practice coins run the very same curve as the testnet contract.</p>'
        + '<p><b>The proof, on the testnet.</b> Before your wallet is asked, the page writes the launch\'s terms and the chain records their hash in the launch itself. Once this site has read the launch from the chain, it seals it into the day\'s public proof, with a receipt this page checks in your browser.</p>'
        + '<p><b>What it is not.</b> Not a sale of anything with value, not advice, and not the RUNECLAW agent: the agent never launches, buys or sells these coins. Names and descriptions are their creators\' words; a coin that breaks the page\'s rules is not listed.</p>'
        + (state.key ? '<p><b>Your practice player</b> lives in this browser. <button type="button" class="mc-btn" data-act="paper-forget">Forget my practice player</button></p>' : '')
        + '<div id="mc-about-note" class="mc-note"></div>'
        + '</div>';
    }

    function coinMark(c) {
      return '<span class="mc-mark" style="background:hsl(' + hue(c.address || c.id) + ',55%,38%)" aria-hidden="true">'
        + esc(String(c.symbol || '?').charAt(0)) + '</span>';
    }

    function drawCoins() {
      var practice = state.mode === 'practice';
      root.innerHTML = '<div class="mc-load">Reading the coins…</div>';
      api(practice ? '/api/memeclaw/paper/coins' : '/api/memeclaw/coins').then(function (r) {
        if (state.view !== 'coins' || (state.mode === 'practice') !== practice) return;
        if (!r.ok || !r.body || !Array.isArray(r.body.coins)) {
          root.innerHTML = '<div class="mc-card"><p class="mc-warn">The coins could not be read (HTTP ' + esc(r.status) + '). Nothing here is a statement that there are none.</p>'
            + '<button type="button" class="mc-btn" data-act="reload">Try again</button></div>';
          return;
        }
        var list = r.body.coins;
        if (!list.length) {
          root.innerHTML = '<div class="mc-card"><p>No ' + (practice ? 'practice ' : '') + 'coin has been launched yet. Be the first: <button type="button" class="mc-btn" data-view-go="launch">Launch a coin</button></p></div>'
            + (practice ? '' : operatorBox());
          return;
        }
        root.innerHTML = '<ul class="mc-list">' + list.map(function (c) {
          var key = practice ? c.id : c.address;
          return '<li><button type="button" class="mc-row" data-coin="' + esc(key) + '">'
            + coinMark(c)
            + '<span class="mc-row-main"><b>' + esc(c.symbol) + '</b> <span class="mc-muted">' + esc(c.name) + '</span>'
            + (practice ? ' <span class="mc-chip mc-chip-dim">practice</span>' : '')
            + (c.terms === 'not_recorded' ? ' <span class="mc-chip mc-chip-dim">launched outside this page</span>' : '')
            + (c.hidden ? ' <span class="mc-chip mc-chip-warn">hidden: ' + esc(c.hidden) + '</span>' : '')
            + '<span class="mc-bar"><span style="width:' + Math.min(100, Number(c.sold_bps) / 100) + '%"></span></span>'
            + '<span class="mc-muted">' + esc(pct(c.sold_bps)) + ' sold · ' + esc(c.trade_count) + ' trade' + (c.trade_count === 1 ? '' : 's') + '</span></span>'
            + '<span class="mc-row-price">' + esc(c.price_doge) + ' <span class="mc-muted">' + (practice ? 'practice DOGE' : 'DOGE') + '</span></span>'
            + '</button></li>';
        }).join('') + '</ul>' + (practice ? '' : operatorBox());
      });
    }

    function tradeForms(unit) {
      return '<div class="mc-card mc-trade">'
        + '<h3>Buy</h3><label>' + esc(unit) + ' to spend <input type="text" inputmode="decimal" id="mc-buy-amt" placeholder="0.5" autocomplete="off"></label>'
        + '<button type="button" class="mc-btn" data-act="buy">Get a quote</button><div id="mc-buy-note" class="mc-note"></div>'
        + '<h3>Sell</h3><label>Coins to sell <input type="text" inputmode="decimal" id="mc-sell-amt" placeholder="1000000" autocomplete="off"></label>'
        + '<button type="button" class="mc-btn" data-act="sell">Get a quote</button><div id="mc-sell-note" class="mc-note"></div>'
        + '<p class="mc-muted">Quotes come from the curve as it is now; a trade is refused rather than filled at more than 1% under its quote.</p>'
        + '</div>';
    }

    function tradesCard(trades, practice) {
      return '<div class="mc-card"><h3>Trades</h3>' + (trades.length ? '<ul class="mc-trades">' + trades.map(function (t) {
        return '<li><span class="mc-' + (t.side === 'buy' ? 'buy' : 'sell') + '">' + esc(t.side) + '</span> '
          + esc(formatUnits(t.token_amount, 0)) + ' for ' + esc(formatUnits(t.doge_amount, 4)) + (practice ? ' practice' : '') + ' DOGE · '
          + (practice ? '<span class="mc-muted">player ' + esc(short(t.trader)) + '</span>'
            : link(explorerAddr(t.trader), short(t.trader)) + ' · ' + link(explorerTx(t.tx_hash), 'tx')) + '</li>';
      }).join('') + '</ul>' : '<p class="mc-muted">No trade yet.</p>');
    }

    function drawPaperCoin(id) {
      root.innerHTML = '<div class="mc-load">Reading the coin…</div>';
      Promise.all([api('/api/memeclaw/paper/coins/' + encodeURIComponent(id)), readMe()]).then(function (both) {
        var r = both[0];
        if (state.view !== 'coin' || state.coin !== id || state.mode !== 'practice') return;
        if (r.status === 404) {
          root.innerHTML = '<div class="mc-card"><p>No such practice coin is listed.</p><button type="button" class="mc-btn" data-view-go="coins">All coins</button></div>';
          return;
        }
        if (!r.ok || !r.body || !r.body.coin) {
          root.innerHTML = '<div class="mc-card"><p class="mc-warn">The coin could not be read (HTTP ' + esc(r.status) + ').</p><button type="button" class="mc-btn" data-act="reload">Try again</button></div>';
          return;
        }
        var c = r.body.coin;
        // Holdings not read are "not read"; read and absent is a held 0.
        var holdings = state.me && !state.me.unread && Array.isArray(state.me.holdings) ? state.me.holdings : null;
        var mine = holdings ? holdings.filter(function (h) { return h.coin === c.id; })[0] : null;
        var held = !holdings ? 'not read' : mine ? formatUnits(mine.amount, 2) : '0';
        root.innerHTML = '<div class="mc-card">'
          + '<div class="mc-head">' + coinMark(c) + '<div><h2>' + esc(c.symbol) + ' <span class="mc-muted">' + esc(c.name) + '</span> <span class="mc-chip mc-chip-dim">practice</span></h2>'
          + (c.description ? '<p class="mc-desc">' + esc(c.description) + '</p>' : '') + '</div></div>'
          + '<div class="mc-kv">'
          + '<span>Price</span><span>' + esc(c.price_doge) + ' practice DOGE per coin</span>'
          + '<span>Sold</span><span>' + esc(pct(c.sold_bps)) + ' of 1,000,000,000 · ' + esc(formatUnits(c.doge_reserve, 4)) + ' practice DOGE in the curve</span>'
          + '<span>Creator</span><span class="mc-muted">practice player ' + esc(short(c.creator)) + '</span>'
          + '<span>Launched</span><span>' + esc(String(c.created_at || '').replace('T', ' ').slice(0, 16)) + ' UTC</span>'
          + (state.key ? '<span>You hold</span><span>' + esc(held) + '</span>' : '')
          + '</div><p class="mc-muted">A practice coin exists on this site only. It is on no chain and has no value.</p></div>'
          + tradeForms('Practice DOGE')
          + tradesCard(r.body.trades || [], true) + '</div>'
          + operatorHide(c);
      });
    }

    function drawCoin(addr) {
      root.innerHTML = '<div class="mc-load">Reading the coin…</div>';
      var q = state.account ? '?wallet=' + encodeURIComponent(state.account) : '';
      api('/api/memeclaw/coins/' + encodeURIComponent(addr) + q).then(function (r) {
        if (state.view !== 'coin' || state.coin !== addr || state.mode !== 'testnet') return;
        if (r.status === 404) {
          root.innerHTML = '<div class="mc-card"><p>This site lists no such coin' + (r.body && r.body.error === 'hidden' ? ' (it is hidden from the page)' : '') + '.</p>'
            + '<button type="button" class="mc-btn" data-view-go="coins">All coins</button></div>';
          return;
        }
        if (!r.ok || !r.body || !r.body.coin) {
          root.innerHTML = '<div class="mc-card"><p class="mc-warn">The coin could not be read (HTTP ' + esc(r.status) + ').</p><button type="button" class="mc-btn" data-act="reload">Try again</button></div>';
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
          + '<span>Receipt</span><span>' + (c.receipt ? '<button type="button" class="mc-btn" data-act="receipt" data-receipt="' + esc(c.receipt) + '">Check the sealed launch</button>'
            : '<span class="mc-muted">none: ' + (c.terms === 'recorded' ? 'not sealed yet' : 'no terms recorded here') + '</span>') + '</span>'
          + (held !== null ? '<span>You hold</span><span>' + esc(held) + '</span>' : '')
          + '</div><div id="mc-receipt"></div>'
          + '<details class="mc-reading"><summary>About this launch</summary><ul>'
          + '<li>Made by ' + esc(rd.made_by || 'the Memeclaw factory') + '.</li>'
          + '<li>The creator bought ' + esc(pct(rd.creator_bought_at_launch_bps)) + ' of the supply in the launch transaction.</li>'
          + '<li>The creator\'s net buys from the curve: ' + (rd.creator_net_from_curve_bps === null || rd.creator_net_from_curve_bps === undefined ? 'not read' : esc(pct(rd.creator_net_from_curve_bps))) + '.</li>'
          + '<li>Coins this creator has launched here: ' + esc(rd.creator_coins_launched == null ? 'not read' : rd.creator_coins_launched) + '.</li>'
          + '<li>Holders: ' + esc(rd.holders_note || 'not read') + '.</li>'
          + '</ul><p class="mc-muted">Facts about how the coin was made, not a judgement of it.</p></details>'
          + '</div>'
          + tradeForms('Testnet DOGE')
          + tradesCard(trades, false)
          + '<p class="mc-muted">Read up to block ' + esc(c.read_to_block) + '.</p></div>'
          + operatorHide(c);
      });
    }

    function drawReceipt(key) {
      var box = doc.getElementById('mc-receipt');
      if (!box) return;
      if (!R) { box.innerHTML = '<p class="mc-warn">The receipt check did not load.</p>'; return; }
      box.innerHTML = '<p class="mc-muted">Checking in your browser…</p>';
      R.check(api, key, { keyRe: LAUNCH_KEY }).then(function (res) {
        var verdict = res.state === 'verified' ? '<p class="mc-note-ok">✅ Verified in your browser: the SHA-256 of what was sealed equals the seal.</p>'
          : res.state === 'mismatch' ? '<p class="mc-note-warn">✖ Does not match: this receipt is not intact.</p>'
            : '<p class="mc-note-warn">The receipt could not be read or checked just now. Nothing is claimed either way.</p>';
        var a = res.anchor;
        var anchor = !a ? (res.state === 'verified' ? '<p class="mc-muted">Not yet in a daily root: every seal of a day is folded into one root after the day ends.</p>' : '')
          : a.state === 'reaches' ? '<p class="mc-muted">🌳 Folded into the ' + esc(a.day || '') + ' daily root' + (a.count ? ', one of ' + esc(a.count) + ' seals' : '') + '. Your browser replayed the proof just now.</p>'
            : a.state === 'misses' ? '<p class="mc-note-warn">✖ The proof does not reach the day\'s root.</p>'
              : '<p class="mc-muted">The daily root could not be checked in this browser.</p>';
        var rows = launchFacts(res.parsed).map(function (f) { return '<span>' + esc(f[0]) + '</span><span class="mc-hashish">' + esc(f[1]) + '</span>'; }).join('');
        box.innerHTML = '<div class="mc-receipt">' + verdict + anchor + (rows ? '<div class="mc-kv">' + rows + '</div>' : '')
          + (res.payload ? '<details><summary>What was sealed (hash it yourself)</summary><p class="mc-hashish">' + esc(res.payload) + '</p></details>' : '')
          + '</div>';
      });
    }

    function operatorHide(c) {
      if (!state.operator) return '';
      return '<div class="mc-card mc-op"><h3>Operator</h3><label>Reason to hide (empty shows it again) <input type="text" id="mc-hide-reason"></label>'
        + '<button type="button" class="mc-btn" data-act="hide" data-coin-hide="' + esc(c.address || c.id) + '">Save</button><div id="mc-hide-note" class="mc-note"></div></div>';
    }

    function drawLaunch() {
      var practice = state.mode === 'practice';
      root.innerHTML = '<div class="mc-card mc-launch"><h2>Launch a ' + (practice ? 'practice ' : 'testnet ') + 'coin</h2>'
        + '<label>Name <input type="text" id="mc-name" maxlength="32" placeholder="Much Claw" autocomplete="off"></label>'
        + '<label>Symbol <input type="text" id="mc-symbol" maxlength="10" placeholder="CLAW" autocomplete="off"></label>'
        + '<label>Description (optional, no links) <textarea id="mc-desc" maxlength="280" rows="3"></textarea></label>'
        + '<label>Buy first, in the same step (' + (practice ? 'practice' : 'testnet') + ' DOGE, optional) <input type="text" inputmode="decimal" id="mc-dev" placeholder="0" autocomplete="off"></label>'
        + '<button type="button" class="mc-btn mc-btn-main" data-act="launch">' + (practice ? 'Launch as practice' : 'Launch from my wallet') + '</button>'
        + '<div id="mc-launch-note" class="mc-note"></div>'
        + '<p class="mc-muted">The supply is 1,000,000,000, created once and held by the curve. The page will not take a name that names this project, a stock or fund, a major coin or a stablecoin, or carries a link.'
        + (practice ? ' Five practice launches a day.' : '') + '</p>'
        + '</div>';
      var sym = doc.getElementById('mc-symbol');
      if (sym) sym.addEventListener('input', function () { sym.value = sym.value.toUpperCase(); });
    }

    function operatorBox() {
      if (!state.operator || state.mode !== 'testnet') return '';
      if (state.cfg && state.cfg.enabled) {
        return '<div class="mc-card mc-op"><h3>Operator: the starter coins</h3>'
          + '<p>Launch the first coins from your own wallet: ' + esc(state.starters.map(function (s) { return s.symbol; }).join(', '))
          + '. Your wallet asks you to sign each one.</p>'
          + '<button type="button" class="mc-btn" data-act="starters">Launch the starter coins</button>'
          + '<div id="mc-starter-note" class="mc-note"></div></div>';
      }
      return '<div class="mc-card mc-op"><h3>Operator: deploy the factory</h3>'
        + '<p>Your own wallet sends the factory\'s creation code to the DogeOS testnet. The contract refuses every other chain. Then put the two values the check prints in the website\'s environment (web.env) and publish it again.</p>'
        + '<button type="button" class="mc-btn" data-act="deploy">Deploy from my wallet</button>'
        + '<div id="mc-deploy-note" class="mc-note"></div></div>';
    }

    // ── practice actions ──
    function paperLaunch() {
      var el = doc.getElementById('mc-launch-note');
      var dev = (doc.getElementById('mc-dev').value || '').trim();
      var devUnits = dev === '' ? '0' : parseUnits(dev);
      if (devUnits === null) { note(el, 'warn', esc(refusal('dev_buy_amount'))); return; }
      needPlayer().then(function (ok) {
        if (!ok) { note(el, 'warn', 'A practice player could not be started just now. Try again.'); return undefined; }
        return papi('/api/memeclaw/paper/launch', { method: 'POST', body: {
          name: doc.getElementById('mc-name').value, symbol: doc.getElementById('mc-symbol').value,
          description: doc.getElementById('mc-desc').value, dev_buy: devUnits } }).then(function (r) {
          if (r.ok && r.body && r.body.coin) { readMe(); setView('coin', r.body.coin.id); return; }
          note(el, 'warn', r.status === 400 || r.status === 401 ? esc(refusal(r.body && r.body.error, r.body && r.body.detail))
            : 'The launch could not be made (HTTP ' + esc(r.status) + ').');
        });
      });
    }

    function paperTrade(action) {
      var el = doc.getElementById(action === 'buy' ? 'mc-buy-note' : 'mc-sell-note');
      var amount = parseUnits(doc.getElementById(action === 'buy' ? 'mc-buy-amt' : 'mc-sell-amt').value);
      if (amount === null || /^0+$/.test(amount)) { note(el, 'warn', esc(refusal('amount'))); return; }
      var coin = state.coin;
      needPlayer().then(function (ok) {
        if (!ok) { note(el, 'warn', 'A practice player could not be started just now. Try again.'); return undefined; }
        return api('/api/memeclaw/paper/quote', { method: 'POST', body: { coin: coin, action: action, amount: amount } }).then(function (q) {
          if (!q.ok || !q.body) {
            note(el, 'warn', q.status === 400 ? esc(refusal(q.body && q.body.error)) + (q.body && q.body.max_buy ? ' At most ' + esc(formatUnits(q.body.max_buy, 4)) + ' practice DOGE now.' : '')
              : 'The quote could not be made (HTTP ' + esc(q.status) + ').');
            return;
          }
          state.quote = { action: action, coin: coin, amount: amount, gets: q.body.gets };
          var gets = action === 'buy' ? esc(formatUnits(q.body.gets, 0)) + ' coins' : esc(formatUnits(q.body.gets, 6)) + ' practice DOGE';
          note(el, 'info', 'You get about ' + gets + '. <button type="button" class="mc-btn" data-act="paper-confirm">Confirm the ' + esc(action) + '</button>');
        });
      });
    }

    function paperConfirm() {
      var q = state.quote;
      if (!q) return;
      var el = doc.getElementById(q.action === 'buy' ? 'mc-buy-note' : 'mc-sell-note');
      papi('/api/memeclaw/paper/trade', { method: 'POST', body: { coin: q.coin, action: q.action, amount: q.amount, min_out: floorOf(q.gets) } })
        .then(function (r) {
          state.quote = null;
          if (r.ok) { note(el, 'ok', 'Done.'); setTimeout(function () { if (state.view === 'coin') drawPaperCoin(state.coin); }, 600); return; }
          note(el, 'warn', r.status === 400 || r.status === 401 ? esc(refusal(r.body && r.body.error))
            + (r.body && r.body.doge ? ' You have ' + esc(formatUnits(r.body.doge, 2)) + ' practice DOGE.' : '')
            + (r.body && r.body.held ? ' You hold ' + esc(formatUnits(r.body.held, 2)) + '.' : '')
            : 'The trade could not be made (HTTP ' + esc(r.status) + '). Nothing changed.');
        });
    }

    // ── testnet actions ──
    function doLaunch() {
      var el = doc.getElementById('mc-launch-note');
      var dev = (doc.getElementById('mc-dev').value || '').trim();
      var devUnits = dev === '' ? '0' : parseUnits(dev);
      if (devUnits === null) { note(el, 'warn', esc(refusal('dev_buy_amount'))); return; }
      launchOne({ name: doc.getElementById('mc-name').value, symbol: doc.getElementById('mc-symbol').value,
        description: doc.getElementById('mc-desc').value, dev_buy: devUnits }, el).then(function (coin) {
        if (coin) setView('coin', coin.address);
      });
    }

    /** One testnet launch from the wallet: plan, send, read back. Resolves the coin, or null. */
    function launchOne(terms, el) {
      note(el, 'info', 'Asking your wallet…');
      return needWallet().then(function (w) {
        if (w.status !== 'connected') { walletNote(el, w); return null; }
        return api('/api/memeclaw/launch-plan', { method: 'POST', body: Object.assign({ creator: state.account }, terms) }).then(function (r) {
          if (!r.ok || !r.body || !r.body.data) {
            note(el, 'warn', r.status === 400 ? esc(refusal(r.body && r.body.error, r.body && r.body.detail))
              : 'The plan could not be made (HTTP ' + esc(r.status) + '). Nothing was sent.');
            return null;
          }
          var plan = r.body;
          note(el, 'info', 'Confirm the launch of ' + esc(terms.symbol) + ' in your wallet' + (plan.dev_buy !== '0' ? ': it also buys ' + esc(formatUnits(plan.expected_tokens, 0)) + ' coins for ' + esc(formatUnits(plan.dev_buy, 4)) + ' testnet DOGE' : '') + '.');
          return send(plan).then(function (tx) {
            note(el, 'info', 'Sent. Waiting for the DogeOS testnet to confirm it… ' + link(explorerTx(tx), 'view transaction'));
            return settle('/api/memeclaw/coins', tx).then(function (s) {
              if (s.ok && s.body && s.body.coin) {
                note(el, 'ok', 'Launched: ' + esc(s.body.coin.symbol) + '. ' + (s.body.sealed ? 'Its terms are sealed.' : ''));
                return s.body.coin;
              }
              if (s.status === 503) note(el, 'info', 'The chain has not confirmed it yet. It will appear in the list once it does. ' + link(explorerTx(tx), 'view transaction'));
              else note(el, 'warn', 'The chain does not show a launch in that transaction: ' + esc((s.body && s.body.reason) || 'unknown') + '.');
              return null;
            });
          }, function (err) { declinedNote(el, err); return null; });
        });
      });
    }

    /** The starter coins this wallet has not launched yet, one wallet prompt each, stopping at the first that does not land. */
    function doStarters() {
      var el = doc.getElementById('mc-starter-note');
      needWallet().then(function (w) {
        if (w.status !== 'connected') { walletNote(el, w); return undefined; }
        return api('/api/memeclaw/coins').then(function (r) {
          if (!r.ok || !r.body || !Array.isArray(r.body.coins)) {
            note(el, 'warn', 'The coins could not be read, so the page cannot tell which starter coins this wallet already launched. Nothing was sent.');
            return;
          }
          var me = String(state.account).toLowerCase();
          var mine = {};
          r.body.coins.forEach(function (c) { if (String(c.creator).toLowerCase() === me) mine[c.symbol] = true; });
          var list = state.starters.filter(function (s) { return !mine[s.symbol]; });
          if (!list.length) { note(el, 'ok', 'Every starter coin is already launched from this wallet.'); return; }
          var done = [];
          (function next() {
            var s = list.shift();
            if (!s) { note(el, 'ok', 'Launched: ' + esc(done.join(', ')) + '.'); return; }
            launchOne({ name: s.name, symbol: s.symbol, description: s.description, dev_buy: '0' }, el).then(function (coin) {
              if (!coin) return; // declined or not confirmed: the note says which, and the rest wait for the next press
              done.push(coin.symbol);
              next();
            });
          }());
        });
      });
    }

    function doTrade(action) {
      var el = doc.getElementById(action === 'buy' ? 'mc-buy-note' : 'mc-sell-note');
      var amount = parseUnits(doc.getElementById(action === 'buy' ? 'mc-buy-amt' : 'mc-sell-amt').value);
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
      }, function (err) { declinedNote(el, err); });
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
                  + ' and MEMECLAW_FACTORY_BLOCK=' + esc(s.body.set.MEMECLAW_FACTORY_BLOCK) + ' in the website\'s environment (web.env), then publish it again.');
              } else note(el, 'warn', 'The deployment did not check out: ' + esc((s.body && s.body.reason) || ('HTTP ' + s.status)) + '.');
            });
          }, function (err) { declinedNote(el, err); });
        });
      });
    }

    function doHide(id) {
      var el = doc.getElementById('mc-hide-note');
      var path = state.mode === 'practice' ? '/api/memeclaw/paper/coins/' : '/api/memeclaw/coins/';
      api(path + encodeURIComponent(id) + '/hide', { method: 'POST', body: { reason: doc.getElementById('mc-hide-reason').value } })
        .then(function (r) { note(el, r.ok ? 'ok' : 'warn', r.ok ? (r.body.hidden ? 'Hidden from the page.' : 'Shown again.') : 'Not saved (HTTP ' + esc(r.status) + ').'); });
    }

    doc.addEventListener('click', function (e) {
      var t = e.target && e.target.closest ? e.target.closest('[data-act],[data-view],[data-view-go],[data-coin],[data-mode]') : null;
      if (!t) return;
      if (t.hasAttribute('data-mode')) { setMode(t.getAttribute('data-mode')); return; }
      if (t.hasAttribute('data-view')) { setView(t.getAttribute('data-view')); return; }
      if (t.hasAttribute('data-view-go')) { setView(t.getAttribute('data-view-go')); return; }
      if (t.hasAttribute('data-coin')) { setView('coin', t.getAttribute('data-coin')); return; }
      var act = t.getAttribute('data-act');
      var practice = state.mode === 'practice';
      if (act === 'connect') connect().then(function (r) { if (r.status === 'connected' && state.view === 'coin') drawCoin(state.coin); });
      else if (act === 'reload') { if (state.mode === 'testnet' && !state.cfg) boot(); else draw(); }
      else if (act === 'launch') { if (practice) paperLaunch(); else doLaunch(); }
      else if (act === 'buy' || act === 'sell') { if (practice) paperTrade(act); else doTrade(act); }
      else if (act === 'paper-confirm') paperConfirm();
      else if (act === 'paper-start') needPlayer().then(function () { draw(); });
      else if (act === 'paper-refill') papi('/api/memeclaw/paper/refill', { method: 'POST', body: {} }).then(function () { readMe(); });
      else if (act === 'paper-forget') {
        if (typeof window !== 'undefined' && window.confirm && !window.confirm('Forget this browser\'s practice player? Its practice DOGE and coins held are gone for good.')) return;
        papi('/api/memeclaw/paper/me', { method: 'DELETE' }).then(function (r) {
          if (r.ok || r.status === 401) { state.key = null; store(KEY_STORE, null); state.me = null; drawWallet(); draw(); }
          else note(doc.getElementById('mc-about-note'), 'warn', 'Not forgotten (HTTP ' + esc(r.status) + ').');
        });
      }
      else if (act === 'receipt') drawReceipt(t.getAttribute('data-receipt'));
      else if (act === 'deploy') doDeploy();
      else if (act === 'starters') doStarters();
      else if (act === 'hide') doHide(t.getAttribute('data-coin-hide'));
    });

    function boot() {
      state.cfgFailed = false;
      api('/api/memeclaw/config').then(function (r) {
        if (!r.ok || !r.body) { state.cfgFailed = true; draw(); return; }
        state.cfg = r.body;
        drawWallet();
        // The operator's doors answer 401 or 403 to anyone else, and the page shows them nothing.
        api('/api/memeclaw/deploy-plan').then(function (d) {
          state.operator = d.ok;
          state.starters = d.ok && d.body && Array.isArray(d.body.starters) ? d.body.starters : [];
          if (state.operator) draw();
        });
        draw();
      });
    }

    state.key = load(KEY_STORE);
    if (state.key && !/^[0-9a-f]{64}$/.test(state.key)) { state.key = null; store(KEY_STORE, null); }
    setMode(load(MODE_STORE) === 'testnet' ? 'testnet' : 'practice');
    readMe();
    boot();
  }

  return { start: start, esc: esc, parseUnits: parseUnits, formatUnits: formatUnits, floorOf: floorOf, short: short, pct: pct,
    hue: hue, offSentence: offSentence, refusal: refusal, launchFacts: launchFacts };
}));
