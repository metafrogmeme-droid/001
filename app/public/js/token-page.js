/**
 * The $RCLAW page: the one record, painted, with nothing invented.
 *
 * Three values for every field a chain can leave empty. A null authority is
 * "none (revoked)", which is the fact a holder wants. A null lock field is
 * "not created yet". A value of a shape the record did not promise, or a
 * link off the one host it may name, is "unreadable". A record that did not
 * arrive paints no address at all.
 *
 * NO PRESALE. The owner took the presale off the website and the bot on
 * 22 October 2026: this page is the token's facts (mint, supply, authorities,
 * locks, multisig, audits) and nothing about a sale. /api/token serves no
 * presale block, and a test holds the page to that.
 *
 * Dual export: browser (window.TokenPage) + Node (require), so the test
 * drives the same render the visitor sees.
 */
(function (root, factory) {
  const node = typeof module !== 'undefined' && module.exports;
  const api = factory();
  if (node) module.exports = api;
  else root.TokenPage = api;
}(typeof self !== 'undefined' ? self : this, function () {
  'use strict';

  const BASE58_ADDRESS = /^[1-9A-HJ-NP-Za-km-z]{32,44}$/;
  const SOLSCAN_TOKEN = 'https://solscan.io/token/';
  const UNREAD = 'The token record could not be read right now. No mint address is shown '
    + 'in its place — a guessed address is worse than none. Try again in a minute.';
  const AUTH_NONE = 'none (revoked)';
  const UNREADABLE = 'unreadable';
  /**
   * The only certificate link this page will print for a lock: Smithii's vesting certificate for a
   * Solana lock, a lowercase slug and nothing after it. The bot holds the same rule; the test holds
   * both to tests/fixtures/lock_certificate_cases.json.
   */
  const CERTIFICATE_URL = /^https:\/\/tools\.smithii\.io\/vesting\/solana\/[a-z0-9-]{1,30}$/;
  const SOLSCAN_ACCOUNT = 'https://solscan.io/account/';
  /** An unlock instant as the record writes it: a whole minute in UTC. */
  const UNLOCK_AT = /^(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2}):00Z$/;
  const LOCK_NONE = 'not created yet';
  const LOCKS_BODY = 'Each lock is an account of Smithii’s Token Vesting program, which has no instruction to '
    + 'cancel or edit a lock: only the wallet named can claim, and only on or after each date. Smithii can '
    + 'upgrade that program, and no audit of the version running is established.';
  /**
   * The only audit links this page will print: Halborn's report pages for Smithii and CoinFabrik's
   * published reports. A link to "the audit" is a claim people act on, so any other host is unreadable.
   */
  const AUDIT_URL = /^https:\/\/(?:www\.halborn\.com\/audits\/smithii\/[a-z0-9-]{1,60}|github\.com\/CoinFabrik\/coinfabrik-audit-reports\/blob\/main\/[A-Za-z0-9_.\/-]{1,160}\.pdf)$/;
  const AUDITS_BODY = 'This is an audit of Smithii’s Token Vesting program, which holds RUNECLAW’s locks; it is not an '
    + 'audit of RUNECLAW. Smithii can upgrade the program, and it was last deployed on 2025-02-03, before the audit’s '
    + 'final report, so the code running is not shown to be the audited code. The findings Halborn’s fixes closed '
    + '(how a multi-recipient Merkle vesting is split, a lock of zero tokens) do not touch RUNECLAW’s locks: each is one '
    + 'wallet, time-based and never zero. RUNECLAW’s own code has had no independent audit.';
  const MULTISIG_BODY = 'The vault is held by a Squads v4 multisig. Moving tokens out of it, or changing its members, '
    + 'its approvals or its time lock, needs the approvals shown and then the time lock’s wait. The Squads v4 '
    + 'program cannot be upgraded. This is custody with a delay, not a lock: once approved and past the wait, '
    + 'tokens can move.';
  function esc(t) {
    return String(t == null ? '' : t).replace(/[&<>"']/g, function (c) {
      return ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c];
    });
  }

  function say(key, en) {
    try {
      const i18n = (typeof window !== 'undefined') ? window.RCI18N : null;
      const v = i18n ? i18n.translate(key, i18n.getLang()) : '';
      return v || en;
    } catch (e) {
      return en;
    }
  }

  function fault() {
    return '<p class="tok-absent">' + esc(say('tok.unread', UNREAD)) + '</p>';
  }

  /** "1000000000" -> "1,000,000,000"; anything that is not digits is unreadable. */
  function supplyText(raw) {
    const s = String(raw == null ? '' : raw);
    if (!/^\d+$/.test(s)) return null;
    return s.replace(/\B(?=(\d{3})+(?!\d))/g, ',');
  }

  /** null on an SPL mint is a revoked authority: say that, never "null". */
  function authorityHtml(v) {
    if (v === null) return '<span class="tok-ok">' + esc(say('tok.auth_none', AUTH_NONE)) + '</span>';
    if (typeof v === 'string' && v.trim()) {
      return esc(say('tok.auth_held', 'held by')) + ' <code>' + esc(v) + '</code>';
    }
    return '<span class="tok-na">' + esc(say('tok.unreadable', UNREADABLE)) + '</span>';
  }

  function unreadable() {
    return '<span class="tok-na">' + esc(say('tok.unreadable', UNREADABLE)) + '</span>';
  }

  function row(key, en, valueHtml) {
    return '<div class="tok-row"><dt>' + esc(say(key, en)) + '</dt><dd>' + valueHtml + '</dd></div>';
  }

  function has(o, key) {
    return Object.prototype.hasOwnProperty.call(o, key);
  }

  function notCreated() {
    return '<span class="tok-na">' + esc(say('tok.lock_none', LOCK_NONE)) + '</span>';
  }

  /** An address a lock names, in three values: null is not created yet, a bad shape is unreadable. */
  function lockAddressCell(v, link) {
    if (v === null) return notCreated();
    if (typeof v !== 'string' || !BASE58_ADDRESS.test(v)) return unreadable();
    const code = '<code>' + esc(v) + '</code>';
    return link ? '<a href="' + SOLSCAN_ACCOUNT + esc(v) + '" target="_blank" rel="noopener">' + code + '</a>' : code;
  }

  /** True only for a link this page may print: see CERTIFICATE_URL. */
  function isCertificateUrl(v) {
    return typeof v === 'string' && CERTIFICATE_URL.test(v);
  }

  function certificateCell(v) {
    if (v === null) return notCreated();
    if (!isCertificateUrl(v)) return unreadable();
    return '<a href="' + esc(v) + '" target="_blank" rel="noopener noreferrer">' + esc(v) + '</a>';
  }

  /** Every unlock as "2026-10-29 14:00 UTC · 5%"; one entry of another shape makes the whole list unreadable. */
  function unlocksCell(v) {
    if (!Array.isArray(v) || v.length === 0) return unreadable();
    const lines = [];
    for (const u of v) {
      const m = u && typeof u.at === 'string' ? UNLOCK_AT.exec(u.at) : null;
      if (!m || !Number.isInteger(u.percent) || u.percent < 1 || u.percent > 100) return unreadable();
      lines.push(esc(m[1] + ' ' + m[2]) + ' UTC · <b>' + u.percent + '%</b>');
    }
    return lines.join('<br>');
  }

  function lockHtml(item, symbol) {
    const it = (item && typeof item === 'object') ? item : {};
    const get = (k) => (has(it, k) ? it[k] : undefined);
    const bucket = typeof it.bucket === 'string' && it.bucket ? esc(it.bucket) : unreadable();
    const n = supplyText(get('tokens'));
    let rows = '';
    rows += row('tok.lock_amount', 'Locked', n === null ? unreadable() : '<b>' + n + ' ' + esc(symbol) + '</b>');
    rows += row('tok.lock_schedule', 'Schedule',
      typeof it.schedule === 'string' && it.schedule ? esc(it.schedule) : unreadable());
    rows += row('tok.lock_unlocks', 'Unlocks', unlocksCell(get('unlocks')));
    rows += row('tok.lock_account', 'Lock account', has(it, 'vesting_account') ? lockAddressCell(it.vesting_account, true) : unreadable());
    rows += row('tok.lock_wallet', 'Claiming wallet', has(it, 'claim_wallet') ? lockAddressCell(it.claim_wallet, false) : unreadable());
    rows += row('tok.lock_certificate', 'Certificate', has(it, 'certificate_url') ? certificateCell(it.certificate_url) : unreadable());
    return '<h3 class="tok-lock-h">' + bucket + '</h3><dl class="tok-facts">' + rows + '</dl>';
  }

  /**
   * The time locks, from the record's locks block. A record without the block says the locks could
   * not be read; null says none is created yet; each lock then prints in its own three values.
   */
  function locksHtml(token, symbol) {
    const sym = typeof symbol === 'string' && symbol ? symbol : 'RCLAW';
    const head = '<section class="tok-card tok-locks"><h2>' + esc(say('tok.locks_h', 'Locked allocations')) + '</h2>';
    const t = (token && typeof token === 'object') ? token : {};
    if (!has(t, 'locks')) return head + '<p class="tok-absent">' + unreadable() + '</p></section>';
    if (t.locks === null) return head + '<p>' + notCreated() + '</p></section>';
    const items = t.locks && Array.isArray(t.locks.items) ? t.locks.items : null;
    if (items === null || items.length === 0) return head + '<p class="tok-absent">' + unreadable() + '</p></section>';
    let body = '';
    for (const item of items) body += lockHtml(item, sym);
    const checked = typeof t.locks.verified_at === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(t.locks.verified_at)
      ? '<p class="tok-prose">' + esc(say('tok.locks_checked', 'Checked against the chain on')) + ' ' + esc(t.locks.verified_at) + '.</p>'
      : '';
    return head + body
      + '<p class="tok-prose">' + esc(say('tok.locks_body', LOCKS_BODY)) + '</p>' + checked + '</section>';
  }

  /** "{n} of {m} members", from the threshold and the member list; anything else is unreadable. */
  function quorumCell(ms) {
    const n = ms.threshold;
    const m = Array.isArray(ms.members) && ms.members.every((k) => typeof k === 'string' && BASE58_ADDRESS.test(k))
      ? ms.members.length : null;
    if (!Number.isInteger(n) || m === null || n < 1 || n > m) return unreadable();
    return '<b>' + esc(say('tok.ms_quorum', '{n} of {m} members').replace('{n}', String(n)).replace('{m}', String(m))) + '</b>';
  }

  /** The delay in hours when it is whole hours, else in seconds; 0 is a real "none", a non-number unreadable. */
  function timeLockCell(seconds) {
    if (!Number.isInteger(seconds) || seconds < 0) return unreadable();
    if (seconds === 0) return '<span class="tok-na">' + esc(say('tok.ms_no_delay', 'none: an approved transfer executes at once')) + '</span>';
    const text = seconds % 3600 === 0
      ? say('tok.ms_hours', '{h} hours').replace('{h}', String(seconds / 3600))
      : say('tok.ms_seconds', '{s} seconds').replace('{s}', String(seconds));
    return '<b>' + esc(text) + '</b>';
  }

  /** What the vault holds, then each bucket on its own line; one bucket of another shape makes it unreadable. */
  function holdsCell(ms, symbol) {
    const total = supplyText(ms.holds_tokens);
    if (total === null || !Array.isArray(ms.buckets) || ms.buckets.length === 0) return unreadable();
    const lines = ['<b>' + total + ' ' + esc(symbol) + '</b>'];
    for (const b of ms.buckets) {
      const n = b && typeof b.bucket === 'string' && b.bucket ? supplyText(b.tokens) : null;
      if (n === null) return unreadable();
      lines.push(esc(b.bucket) + ' · ' + n);
    }
    return lines.join('<br>');
  }

  /**
   * The treasury multisig, from the record's multisig block, in three values: no block is
   * unreadable, null is not created yet, and each field prints on its own.
   */
  function multisigHtml(token, symbol) {
    const sym = typeof symbol === 'string' && symbol ? symbol : 'RCLAW';
    const head = '<section class="tok-card tok-multisig"><h2>' + esc(say('tok.ms_h', 'Treasury multisig')) + '</h2>';
    const t = (token && typeof token === 'object') ? token : {};
    if (!has(t, 'multisig')) return head + '<p class="tok-absent">' + unreadable() + '</p></section>';
    if (t.multisig === null) return head + '<p>' + notCreated() + '</p></section>';
    const ms = (typeof t.multisig === 'object' && !Array.isArray(t.multisig)) ? t.multisig : {};
    let rows = '';
    rows += row('tok.ms_holds', 'Holds', holdsCell(ms, sym));
    rows += row('tok.ms_approvals', 'Approvals', quorumCell(ms));
    rows += row('tok.ms_timelock', 'Time lock', timeLockCell(ms.time_lock_seconds));
    rows += row('tok.ms_vault', 'Vault (holds the tokens)', has(ms, 'vault') ? lockAddressCell(ms.vault, true) : unreadable());
    rows += row('tok.ms_account', 'Multisig account', has(ms, 'account') ? lockAddressCell(ms.account, true) : unreadable());
    const checked = typeof ms.verified_at === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(ms.verified_at)
      ? '<p class="tok-prose">' + esc(say('tok.locks_checked', 'Checked against the chain on')) + ' ' + esc(ms.verified_at) + '.</p>'
      : '';
    return head + '<dl class="tok-facts">' + rows + '</dl>'
      + '<p class="tok-prose">' + esc(say('tok.ms_body', MULTISIG_BODY)) + '</p>' + checked + '</section>';
  }

  /** True only for a link this page may print: see AUDIT_URL. */
  function isAuditUrl(v) {
    return typeof v === 'string' && AUDIT_URL.test(v);
  }

  /** One audit: "Halborn · Smithii Token Vesting · 2025-04", linked only when the link is the auditor's own. */
  function auditItem(it) {
    const a = (it && typeof it === 'object') ? it : {};
    const ok = (v) => typeof v === 'string' && v.trim() !== '';
    if (!ok(a.auditor) || !ok(a.subject) || typeof a.date !== 'string' || !/^\d{4}-\d{2}$/.test(a.date)) return '<li>' + unreadable() + '</li>';
    const label = esc(a.auditor) + ' · ' + esc(a.subject) + ' · ' + esc(a.date);
    if (!isAuditUrl(a.url)) return '<li>' + label + ' · ' + unreadable() + '</li>';
    return '<li><a href="' + esc(a.url) + '" target="_blank" rel="noopener noreferrer">' + label + '</a></li>';
  }

  /** Smithii's own audits, from the record, in three values, with what they do and do not cover. */
  function auditsHtml(token) {
    const head = '<section class="tok-card tok-audits"><h2>' + esc(say('tok.audits_h', 'Smithii’s audits')) + '</h2>';
    const t = (token && typeof token === 'object') ? token : {};
    if (!has(t, 'smithii_audits')) return head + '<p class="tok-absent">' + unreadable() + '</p></section>';
    const items = t.smithii_audits && Array.isArray(t.smithii_audits.items) ? t.smithii_audits.items : null;
    if (items === null || items.length === 0) return head + '<p class="tok-absent">' + unreadable() + '</p></section>';
    return head + '<ul class="tok-audits-list">' + items.map(auditItem).join('') + '</ul>'
      + '<p class="tok-prose">' + esc(say('tok.audits_body', AUDITS_BODY)) + '</p></section>';
  }

  function render(token) {
    if (!token || typeof token !== 'object') return fault();
    if (typeof token.mint !== 'string' || !BASE58_ADDRESS.test(token.mint)) return fault();
    const mint = token.mint;
    const symbol = typeof token.symbol === 'string' && token.symbol ? token.symbol : 'RCLAW';
    const explorer = (typeof token.explorer === 'string' && token.explorer.indexOf(SOLSCAN_TOKEN + mint) === 0)
      ? token.explorer : null;
    const supply = supplyText(token.supply_tokens);
    const decimals = (typeof token.decimals === 'number' && Number.isInteger(token.decimals))
      ? String(token.decimals) : null;
    const chain = (typeof token.chain === 'string' && token.chain)
      ? esc(token.chain) + (typeof token.cluster === 'string' && token.cluster ? ' (' + esc(token.cluster) + ')' : '')
      : unreadable();

    let out = '<section class="tok-card tok-mint">'
      + '<h2>' + esc(say('tok.mint_h', 'Mint address')) + '</h2>'
      + '<div class="tok-addr"><code id="tok-mint">' + esc(mint) + '</code>'
      + '<button type="button" class="btn btn--sm" id="tok-copy" data-mint="' + esc(mint) + '">'
      + esc(say('tok.copy', 'Copy')) + '</button></div>'
      + (explorer
        ? '<p class="tok-links"><a href="' + esc(explorer) + '" target="_blank" rel="noopener">'
          + esc(say('tok.solscan', 'View on Solscan')) + ' ↗</a></p>'
        : '')
      + '<p class="tok-warn">' + esc(say('tok.verify',
        'Only this mint address is $RCLAW. Any other token using the name or logo is not ours — '
        + 'check the address before you trade anything.')) + '</p>'
      + '</section>';

    out += '<section class="tok-card" id="tok-pool"><h2>' + esc(say('tok.pool_h', 'Where it trades')) + '</h2>'
      + '<p class="tok-absent">…</p></section>';

    let facts = '<section class="tok-card"><h2>' + esc(say('tok.facts_h', 'On-chain facts')) + '</h2><dl class="tok-facts">';
    facts += row('tok.chain', 'Chain', chain);
    facts += row('tok.standard', 'Standard',
      typeof token.standard === 'string' && token.standard ? esc(token.standard) : unreadable());
    facts += row('tok.program', 'Token program',
      typeof token.token_program === 'string' && BASE58_ADDRESS.test(token.token_program)
        ? '<code>' + esc(token.token_program) + '</code>' : unreadable());
    facts += row('tok.supply', 'Total supply',
      supply === null ? unreadable()
        : '<b>' + supply + ' ' + esc(symbol) + '</b> · ' + esc(say('tok.fixed',
          'fixed — the mint authority is revoked, so no more can ever be minted')));
    facts += row('tok.decimals', 'Decimals', decimals === null ? unreadable() : decimals);
    facts += row('tok.mint_auth', 'Mint authority', authorityHtml(token.mint_authority));
    facts += row('tok.freeze_auth', 'Freeze authority', authorityHtml(token.freeze_authority));
    if (Object.prototype.hasOwnProperty.call(token, 'created_at') && token.created_at !== null) {
      facts += row('tok.created', 'Created',
        typeof token.created_at === 'string' && token.created_at.length >= 10
          ? esc(token.created_at.slice(0, 10)) + ' UTC' : unreadable());
    }
    if (typeof token.verified_at === 'string' && token.verified_at) {
      facts += row('tok.verified', 'Record checked against the chain on', esc(token.verified_at));
    }
    facts += '</dl></section>';
    out += facts;
    out += locksHtml(token, symbol);
    out += multisigHtml(token, symbol);
    out += auditsHtml(token);
    out += '<p class="tok-bot">' + esc(say('tok.bot_hint', 'Also in the Telegram bot: /rclaw')) + '</p>';
    return out;
  }

  function wireCopy(el) {
    const btn = el.querySelector('#tok-copy');
    if (!btn) return;
    btn.addEventListener('click', function () {
      const mint = btn.getAttribute('data-mint') || '';
      const nav = (typeof navigator !== 'undefined') ? navigator : null;
      if (!nav || !nav.clipboard || !nav.clipboard.writeText) {
        btn.textContent = say('tok.copy_failed', 'Copy failed — select the address instead');
        return;
      }
      nav.clipboard.writeText(mint).then(function () {
        btn.textContent = say('tok.copied', 'Copied');
      }, function () {
        btn.textContent = say('tok.copy_failed', 'Copy failed — select the address instead');
      });
    });
  }

  /** $1,234,567 → "$1.2M"; a market figure at a panel's size; '' for a non-number. */
  function compactUsd(v) {
    // `Number(null)` is 0, a perfectly finite dollar: absence is refused
    // before the coercion, or an unread liquidity prints as $0.00.
    if (v === null || v === undefined || v === '') return '';
    const n = Number(v);
    if (!isFinite(n) || n < 0) return '';
    if (n >= 1e9) return '$' + (n / 1e9).toFixed(2) + 'B';
    if (n >= 1e6) return '$' + (n / 1e6).toFixed(2) + 'M';
    if (n >= 1e3) return '$' + (n / 1e3).toFixed(1) + 'K';
    return '$' + n.toFixed(n < 1 ? 4 : 2);
  }

  function agoText(iso) {
    const t = Date.parse(iso);
    if (!isFinite(t)) return '';
    const m = Math.max(0, Math.round((Date.now() - t) / 60000));
    return m < 1 ? 'just now' : m === 1 ? '1 minute ago' : m < 60 ? m + ' minutes ago' : Math.round(m / 60) + 'h ago';
  }

  /**
   * The pool panel, from /api/token/pool's three answers. `listed: false` is
   * CoinGecko's fact (no pool yet); `listed: null` or a failed fetch is a
   * read that failed, and the two get different sentences. A figure the
   * answer did not carry is unreadable, never 0.
   */
  function poolHtml(state) {
    let body;
    const s = state && typeof state === 'object' ? state : null;
    if (s && s.listed === true && s.pool && typeof s.pool === 'object') {
      const pool = s.pool;
      const fig = (v) => { const t = compactUsd(v); return t ? '<b>' + esc(t) + '</b>' : unreadable(); };
      const link = typeof pool.url === 'string' && pool.url.indexOf('https://www.geckoterminal.com/solana/pools/') === 0
        ? ' <a href="' + esc(pool.url) + '" target="_blank" rel="noopener">' + esc(say('tok.pool_open', 'View on GeckoTerminal')) + ' ↗</a>' : '';
      body = '<dl class="tok-facts">'
        + row('tok.pool_pool', 'Pool', esc(pool.name || pool.address || '') + (pool.dex ? ' · ' + esc(pool.dex) : '') + link)
        + row('tok.pool_liq', 'Liquidity', fig(pool.reserve_usd))
        + row('tok.pool_vol', '24h volume', fig(pool.volume_24h_usd))
        + row('tok.pool_price', 'Price', fig(pool.price_usd))
        + '</dl>'
        + '<p class="tok-prose small">' + esc(say('tok.pool_note', 'The largest pool on the first page CoinGecko answered, read {ago}. Market facts only: a price is not a promise.').replace('{ago}', agoText(s.read_at) || '—'))
        + ' ' + esc(say('tok.pool_credit', 'Data provided by CoinGecko')) + '</p>';
    } else if (s && s.listed === false) {
      body = '<p class="tok-prose">' + esc(say('tok.pool_none', 'Not listed on a DEX yet. CoinGecko reports no pool holding this mint; this will show the pool once there is one.')) + '</p>'
        + '<p class="tok-prose small">' + esc(say('tok.pool_credit', 'Data provided by CoinGecko')) + '</p>';
    } else {
      body = '<p class="tok-absent">' + esc(say('tok.pool_unread', 'The pool could not be read just now. This is a read that failed, not a token with no pool.')) + '</p>';
    }
    return '<h2>' + esc(say('tok.pool_h', 'Where it trades')) + '</h2>' + body;
  }

  function fetchPool() {
    return fetch('/api/token/pool', { headers: { Accept: 'application/json' }, signal: AbortSignal.timeout(12000) })
      .then(function (r) {
        return r.json().then(function (body) { return { status: r.status, body: body }; },
          function () { return { status: r.status, body: null }; });
      });
  }

  /** Paint the pool panel into its placeholder; a failed fetch is the unread sentence. */
  function bootPool(el) {
    const slot = el.querySelector('#tok-pool');
    if (!slot) return Promise.resolve();
    return fetchPool()
      .then(function (res) { slot.innerHTML = poolHtml(res.status === 200 ? res.body : null); })
      .catch(function () { slot.innerHTML = poolHtml(null); });
  }

  function fetchRecord() {
    return fetch('/api/token', { headers: { Accept: 'application/json' }, signal: AbortSignal.timeout(10000) })
      .then(function (r) {
        return r.json().then(function (body) {
          return { status: r.status, body: body };
        }, function () {
          return { status: r.status, body: null };
        });
      });
  }

  function boot(el) {
    fetchRecord()
      .then(function (res) {
        if (res.status !== 200 || !res.body || typeof res.body !== 'object') { el.innerHTML = fault(); return; }
        el.innerHTML = render(res.body.token);
        wireCopy(el);
        bootPool(el);
      })
      .catch(function () { el.innerHTML = fault(); });
  }

  if (typeof document !== 'undefined' && document.getElementById) {
    const el = document.getElementById('tok-root');
    if (el) boot(el);
  }

  return {
    render, fault, supplyText, authorityHtml, boot, wireCopy, poolHtml, bootPool, compactUsd, locksHtml, lockHtml, unlocksCell, certificateCell, isCertificateUrl,
    multisigHtml, quorumCell, timeLockCell, holdsCell, auditsHtml, auditItem, isAuditUrl,
    UNREAD, AUTH_NONE, UNREADABLE, BASE58_ADDRESS, CERTIFICATE_URL, LOCK_NONE, LOCKS_BODY, MULTISIG_BODY, AUDITS_BODY, AUDIT_URL,
  };
}));
