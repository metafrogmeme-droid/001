/**
 * The $RCLAW page: the one record, painted, with nothing invented.
 *
 * Three values for every field a chain can leave empty. A null authority is
 * "none (revoked)", which is the fact a holder wants. A null presale field
 * is "not announced yet" (a null sale link: "not created yet"). A value of a
 * shape the record did not promise, or a sale link off smithii.io, is
 * "unreadable". A record that did not arrive paints no address at all.
 *
 * Dual export: browser (window.TokenPage) + Node (require), so the test
 * drives the same render the visitor sees.
 */
(function (root, factory) {
  const api = factory();
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.TokenPage = api;
}(typeof self !== 'undefined' ? self : this, function () {
  'use strict';

  const BASE58_ADDRESS = /^[1-9A-HJ-NP-Za-km-z]{32,44}$/;
  const SOLSCAN_TOKEN = 'https://solscan.io/token/';
  const UNREAD = 'The token record could not be read right now. No mint address is shown '
    + 'in its place — a guessed address is worse than none. Try again in a minute.';
  const NOT_ANNOUNCED = 'not announced yet';
  const AUTH_NONE = 'none (revoked)';
  const UNREADABLE = 'unreadable';
  const SALE_LINK_NONE = 'not created yet. It is posted here and in the Telegram bot before the sale opens; '
    + 'a sale link anywhere else is not ours.';
  /**
   * The only sale link this page will print: https on smithii.io or a subdomain, written plainly.
   * A sale link is what a phishing clone forges first, so anything else is unreadable, never a
   * link. The bot holds the same rule; tests/fixtures/sale_url_cases.json holds both to it.
   */
  const SALE_URL = /^https:\/\/(?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)*smithii\.io(?:\/[A-Za-z0-9._~%\/?#=&+-]*)?$/;
  const SALE_URL_MAX = 300;
  /**
   * The presale terms, in the order a buyer reads them: [record key, i18n key, English label].
   * Every key is a key of the record's presale block, and the test holds this list to the
   * record both ways, so a term the record announces cannot go unprinted.
   */
  const TERMS = [
    ['date', 'tok.date', 'Date'],
    ['price', 'tok.price', 'Price'],
    ['venue', 'tok.venue', 'Venue'],
    ['allocation_tokens', 'tok.for_sale', 'For sale'],
    ['hard_cap', 'tok.hard_cap', 'Hard cap'],
    ['per_wallet', 'tok.per_wallet', 'Per wallet'],
    ['whitelist', 'tok.whitelist', 'Whitelist'],
    ['claim', 'tok.claim', 'Claim'],
    ['refunds', 'tok.refunds', 'Refunds'],
    ['after_sale', 'tok.after_sale', 'After the sale'],
  ];
  const BODY_SOON = 'A presale is being prepared. The date, price and venue will be announced on this page, '
    + 'in the Telegram bot and on the project’s X account before anything opens. '
    + 'Until then there is nothing to buy from us.';
  const BODY_ANNOUNCED = 'These are the sale’s terms as announced. Smithii’s sale contract enforces the price, '
    + 'the hard cap, the per-wallet limits and the claim; the soft cap and everything after the sale are the '
    + 'team’s commitments, not the contract’s. We never DM first and never ask for a seed phrase.';

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

  /**
   * A presale field in three values: absent from the record is unreadable,
   * null is not announced, anything else is the announced term as written.
   */
  function presaleCell(presale, key) {
    if (!has(presale, key)) return unreadable();
    const v = presale[key];
    if (v === null) return '<span class="tok-na">' + esc(say('tok.not_announced', NOT_ANNOUNCED)) + '</span>';
    return '<b>' + esc(String(v)) + '</b>';
  }

  /** The number of tokens for sale: digits are grouped and named, anything else is unreadable. */
  function allocationCell(presale, symbol) {
    if (!has(presale, 'allocation_tokens') || presale.allocation_tokens === null) {
      return presaleCell(presale, 'allocation_tokens');
    }
    const n = supplyText(presale.allocation_tokens);
    return n === null ? unreadable() : '<b>' + n + ' ' + esc(symbol) + '</b>';
  }

  /** True only for a link this page may print: see SALE_URL. */
  function isSaleUrl(v) {
    return typeof v === 'string' && v.length <= SALE_URL_MAX && SALE_URL.test(v);
  }

  /** The sale link in three values: null is "not created yet", a refused link is unreadable. */
  function saleLinkCell(presale) {
    if (!has(presale, 'sale_url')) return unreadable();
    const v = presale.sale_url;
    if (v === null) return '<span class="tok-na">' + esc(say('tok.sale_link_none', SALE_LINK_NONE)) + '</span>';
    if (!isSaleUrl(v)) return unreadable();
    return '<a href="' + esc(v) + '" target="_blank" rel="noopener noreferrer"><code>' + esc(v) + '</code></a>';
  }

  function presaleHtml(presale, symbol) {
    const p = (presale && typeof presale === 'object') ? presale : {};
    const sym = typeof symbol === 'string' && symbol ? symbol : 'RCLAW';
    let status;
    let body = null;
    if (p.status === 'coming_soon') {
      status = esc(say('tok.presale_soon', 'Coming soon'));
      body = say('tok.presale_body', BODY_SOON);
    } else if (p.status === 'announced') {
      status = esc(say('tok.presale_announced', 'Announced'));
      body = say('tok.presale_body_announced', BODY_ANNOUNCED);
    } else if (typeof p.status === 'string' && p.status.trim()) {
      status = esc(p.status.replace(/_/g, ' '));
    } else {
      status = unreadable();
    }
    let rows = '';
    for (const [key, i18nKey, en] of TERMS) {
      rows += row(i18nKey, en, key === 'allocation_tokens' ? allocationCell(p, sym) : presaleCell(p, key));
    }
    rows += row('tok.sale_link', 'Sale link', saleLinkCell(p));
    return '<section class="tok-card tok-presale">'
      + '<h2>' + esc(say('tok.presale_h', 'Presale')) + ' · <span class="tok-status">' + status + '</span></h2>'
      + '<dl class="tok-facts">' + rows + '</dl>'
      + (body === null ? '' : '<p class="tok-prose">' + esc(body) + '</p>')
      + '</section>';
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
    out += presaleHtml(token.presale, symbol);
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

  function boot(el) {
    fetch('/api/token', { headers: { Accept: 'application/json' }, signal: AbortSignal.timeout(10000) })
      .then(function (r) {
        return r.json().then(function (body) {
          return { status: r.status, body: body };
        }, function () {
          return { status: r.status, body: null };
        });
      })
      .then(function (res) {
        if (res.status !== 200 || !res.body || typeof res.body !== 'object') { el.innerHTML = fault(); return; }
        el.innerHTML = render(res.body.token);
        wireCopy(el);
      })
      .catch(function () { el.innerHTML = fault(); });
  }

  if (typeof document !== 'undefined' && document.getElementById) {
    const el = document.getElementById('tok-root');
    if (el) boot(el);
  }

  return {
    render, fault, supplyText, authorityHtml, presaleHtml, presaleCell, allocationCell, saleLinkCell,
    isSaleUrl, boot, wireCopy,
    UNREAD, NOT_ANNOUNCED, AUTH_NONE, UNREADABLE, BASE58_ADDRESS, SALE_URL, SALE_LINK_NONE, TERMS,
    BODY_SOON, BODY_ANNOUNCED,
  };
}));
