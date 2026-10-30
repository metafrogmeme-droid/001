/**
 * THE PLAN CARD: the member's tier and what each tier includes, drawn from
 * the bot's own reading (GET /api/tier, the bot gateway's /tier, the same two
 * readings the Telegram /tier card renders).
 *
 * The card used to carry its own table. It promised Pro "Live trading
 * eligibility" and "premium models on your scans", and Elite "Higher live
 * caps" and "Early access to new agent features". No tier granted any of
 * them, and live limits are never tier-gated (docs/TIER_MODEL.md §6). A tier
 * is decided in the bot, so the bot says what it buys, and this only draws it.
 *
 * Every line is one the bot's answer carries: the commands on the ladder (for
 * this member's role), the daily chat questions, the operator-funded model on
 * /analyze, and whether the ladder is enforced at all. An answer that is not
 * readable throws, so the panel shows an error state rather than a card with
 * no tier on it.
 *
 * A module rather than a closure in the panel, for the reason
 * `dashboard_helpers_are_in_scope.test.js` exists: a renderer reachable only
 * through a page load is one no test can drive.
 */
(function (root, factory) {
  const api = factory();
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.PlanCardModel = api;
}(typeof self !== 'undefined' ? self : this, function () {
  'use strict';

  const ICON = { basic: '🟢', pro: '🔵', elite: '🟡', admin: '🔴' };

  // Why a stake earns no tier, by the bot's reason word.
  const STAKE_WHY = {
    no_wallet: 'no wallet linked',
    unverified: 'your linked wallet is not verified yet',
    bad_wallet: 'the linked wallet is not a valid address',
    misconfigured: 'the stake reader is misconfigured on this bot',
    unavailable: 'the chain could not be read just now',
  };

  function readable(data) {
    return !!(data && data.reading && typeof data.reading === 'object'
      && data.ladder && Array.isArray(data.ladder.tiers) && data.ladder.tiers.length
      && typeof data.ladder.enforced === 'boolean');
  }

  function nameOf(ladder, tier) {
    const row = ladder.tiers.find((r) => r && r.tier === tier);
    return row ? String(row.name || tier) : String(tier);
  }

  // The tier's own lines: what it adds over the one below it.
  function points(row, previous) {
    const out = [];
    if (previous) out.push('Everything in ' + previous);
    const cmds = [];
    for (const u of row.unlocks || []) {
      const list = Array.isArray(u.commands) ? u.commands : [];
      if (list.length) for (const c of list) cmds.push('/' + c);
      else if (u.feature) cmds.push(String(u.feature));
    }
    if (cmds.length) out.push('In the Telegram bot: ' + cmds.join(' '));
    else if (!previous) out.push('Every command not listed under a higher tier');
    // null is "no daily cap": the quota is off, or this tier is exempt.
    out.push(row.chat_per_day == null ? 'AI chat: no daily cap'
      : 'AI chat: ' + Number(row.chat_per_day) + ' questions a day');
    if (row.premium_analyze === true) out.push('The operator-funded model on /analyze');
    return out;
  }

  function headline(reading, ladder, esc) {
    if (reading.tier == null) {
      return '<p class="small"><b>Your tier could not be read just now.</b> '
        + 'The bot could not read its member records; this is on our side.</p>';
    }
    let html = '<p class="small">Your tier: <b>' + esc(nameOf(ladder, reading.tier)) + '</b>';
    if (reading.granted && reading.granted_known === false) {
      html += '. Your stored tier <code>' + esc(reading.granted) + '</code> is not one the '
        + 'bot knows, so it counts as Basic; ask the operator to set it again';
    }
    html += '.</p>';
    if (ladder.stake === true) {
      const why = STAKE_WHY[reading.stake_reason] || 'it could not be read';
      html += '<p class="small muted">' + (reading.stake
        ? 'Your stake earns: ' + esc(nameOf(ladder, reading.stake)) + '.'
        : 'Your stake earns no tier: ' + esc(why) + '.')
        + ' Your tier is the higher of the operator\'s grant and your stake\'s.</p>';
    }
    return html;
  }

  /**
   * The card's HTML. `esc` is the page's escaper. Throws on an answer that
   * does not carry a ladder, so the panel's error state is what shows.
   */
  function cardHtml(data, esc) {
    if (!readable(data)) throw new Error('plan card: the tier answer was not readable');
    const reading = data.reading;
    const ladder = data.ladder;
    let previous = '';
    const tiles = ladder.tiers.map((row) => {
      const mine = reading.tier != null && row.tier === reading.tier;
      const name = String(row.name || row.tier);
      const lines = points(row, previous);
      previous = name;
      return '<div class="tile" style="flex:1;min-width:180px;border:1px solid '
        + (mine ? 'var(--gold)' : 'var(--line)')
        + ';border-radius:var(--radius);padding:var(--s3) var(--s4)">'
        + '<div style="display:flex;justify-content:space-between;align-items:baseline">'
        + '<b>' + esc((ICON[row.tier] ? ICON[row.tier] + ' ' : '') + name) + '</b>'
        + (mine ? '<span class="chip chip--gold">your plan</span>' : '') + '</div>'
        + '<ul class="small" style="margin:8px 0 0 16px;color:var(--text-2);display:flex;flex-direction:column;gap:4px">'
        + lines.map((l) => '<li>' + esc(l) + '</li>').join('') + '</ul></div>';
    }).join('');
    const notes = [];
    if (ladder.enforced === false) {
      notes.push('Tiers are not enforced yet: every member can use every command above.');
    }
    notes.push('Never tier-gated: the kill switch, your own positions and risk, why a trade '
      + 'was refused, and live trading limits.');
    let route = ladder.stars === true ? 'Pro and Elite can be bought with Telegram Stars'
      : 'Tiers are granted by the operator';
    if (ladder.stake === true && ladder.stake_min) {
      route += ', or earned by a stake of ' + Number(ladder.stake_min.pro).toLocaleString('en-US')
        + ' $RCLAW (Pro) or ' + Number(ladder.stake_min.elite).toLocaleString('en-US') + ' (Elite)';
    }
    notes.push(route + '. Send /upgrade to <a href="https://t.me/HTRUNECLAW_bot" target="_blank" '
      + 'rel="noopener">@HTRUNECLAW_bot</a>.');
    return headline(reading, ladder, esc)
      + '<div class="row" style="gap:var(--s3);flex-wrap:wrap;align-items:stretch">' + tiles + '</div>'
      + notes.map((n, i) => '<p class="muted small ' + (i ? 'mt-1' : 'mt-3') + '">' + n + '</p>').join('');
  }

  return { cardHtml: cardHtml, readable: readable, ICON: ICON, STAKE_WHY: STAKE_WHY };
}));
