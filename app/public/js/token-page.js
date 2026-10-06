/**
 * The $RCLAW page: the one record, painted, with nothing invented.
 *
 * Three values for every field a chain can leave empty. A null authority is
 * "none (revoked)", which is the fact a holder wants. A null presale field
 * is "not announced yet". A value of a shape the record did not promise is
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

  /**
   * A presale field in three values: absent from the record is unreadable,
   * null is not announced, anything else is the announced term as written.
   */
  function presaleCell(presale, key) {
    if (!Object.prototype.hasOwnProperty.call(presale, key)) return unreadable();
    const v = presale[key];
    if (v === null) return '<span class="tok-na">' + esc(say('tok.not_announced', NOT_ANNOUNCED)) + '</span>';
    return '<b>' + esc(String(v)) + '</b>';
  }

  function presaleHtml(presale) {
    const p = (presale && typeof presale === 'object') ? presale : {};
    let status;
    if (p.status === 'coming_soon') status = esc(say('tok.presale_soon', 'Coming soon'));
    else if (typeof p.status === 'string' && p.status.trim()) status = esc(p.status.replace(/_/g, ' '));
    else status = unreadable();
    return '<section class="tok-card tok-presale">'
      + '<h2>' + esc(say('tok.presale_h', 'Presale')) + ' · <span class="tok-status">' + status + '</span></h2>'
      + '<dl class="tok-facts">'
      + row('tok.date', 'Date', presaleCell(p, 'date'))
      + row('tok.price', 'Price', presaleCell(p, 'price'))
      + row('tok.venue', 'Venue', presaleCell(p, 'venue'))
      + '</dl>'
      + '<p class="tok-prose">' + esc(say('tok.presale_body',
        'A presale is being prepared. The date, price and venue will be announced on this page, '
        + 'in the Telegram bot and on the project’s X account before anything opens. '
        + 'Until then there is nothing to buy from us.')) + '</p>'
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
    out += presaleHtml(token.presale);
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
    render, fault, supplyText, authorityHtml, presaleHtml, presaleCell, boot, wireCopy,
    UNREAD, NOT_ANNOUNCED, AUTH_NONE, UNREADABLE, BASE58_ADDRESS,
  };
}));
