/*
 * The public page for one agent — /a/:slug.
 *
 * Three independent sources, and the whole design turns on that word:
 *
 *   /api/public/agent-identity/:slug   who claimed this slug, when, and the
 *                                      chain from that claim to a Base block
 *   /api/public/agent-record/:slug     the copiers' record, and the agent's own
 *   (the seal payload)                 re-derived in the visitor's browser
 *
 * OMIT, NOT GUARD, AND DELIBERATELY SO
 *
 * CLAUDE.md gives two honest strategies for an unreadable read: GUARD (throw,
 * the caller paints an error) for a single-source panel, OMIT (catch each
 * source, leave missing ones out) for a composite view where one dead source
 * must not blank the rest. This is the second case. An identity endpoint that
 * 503s must not take the track record down with it, and vice versa — so each
 * block renders its own failure and the others still stand.
 *
 * What "omit" must never become is silence. Each block that could not be read
 * SAYS it could not be read, in its own place on the page. Dropping it would
 * turn an outage into a page that quietly asserts the agent has no record.
 *
 * COLOUR IS A CLAIM
 *
 * The profit colour appears in exactly one place here: a chain status of
 * `anchored`, meaning a Base transaction was read and its block time bounds the
 * claim. Every other chain state — including "the day's root is computed but
 * nobody has anchored it yet" — is muted, because they are all still resting on
 * our own clock. `not_in_root` gets the loss colour, because a seal missing
 * from a committed leaf set is the shape of a back-inserted row and should
 * alarm a reader rather than read as one more routine absence.
 *
 * A return of null is muted too. `pctClass(null)` returning the win colour is
 * the exact defect this repo's own history is mostly made of.
 */
(function (root, factory) {
  if (typeof module === 'object' && module.exports) module.exports = factory();
  else root.RCAgentPage = factory();
}(typeof self !== 'undefined' ? self : this, function () {
  'use strict';

  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }

  /** A finite number, or null. Never a fallback zero. */
  function num(v) {
    if (v === null || v === undefined || v === '') return null;
    var n = Number(v);
    return isFinite(n) ? n : null;
  }

  /** Colour is a claim: an unreadable number gets a muted one, never a verdict. */
  function pctClass(v) {
    var n = num(v);
    if (n === null) return 'ap-flat';
    return n > 0 ? 'ap-up' : n < 0 ? 'ap-down' : 'ap-flat';
  }

  /** A percent, or an em dash. Never "0.0%" standing in for "we do not know". */
  function pct(v) {
    var n = num(v);
    return n === null ? '—' : (n > 0 ? '+' : '') + n.toFixed(1) + '%';
  }

  function count(v) {
    var n = num(v);
    return n === null ? '—' : String(Math.round(n));
  }

  /**
   * A source that could not be read. Present ON the page, in the place the
   * block would have been — an omitted source that says nothing is
   * indistinguishable from a source that had nothing to say.
   */
  function errorHtml(what, detail) {
    return '<div class="ap-block ap-err">'
      + '<h3>' + esc(what) + '</h3>'
      + '<p>Could not be read. This is a fault on our side, not a statement '
      + 'about this agent.</p>'
      + (detail ? '<p class="ap-dim">' + esc(detail) + '</p>' : '')
      + '</div>';
  }

  // ── the chain from a claim to a block timestamp ─────────────────────────

  var CHAIN_COPY = {
    anchored: {
      label: 'Anchored on Base',
      cls: 'ap-up',
      lead: 'The day\'s Merkle root is calldata in a Base transaction. Its block '
        + 'time is an upper bound on when this claim was made — a fact nobody here controls.',
    },
    rooted: {
      label: 'Rooted, not yet anchored',
      cls: 'ap-flat',
      lead: 'The day\'s root is computed and committed, but nobody has put it '
        + 'on-chain yet — so this claim\'s date still rests on our clock.',
    },
    day_open: {
      label: 'Claimed today',
      cls: 'ap-flat',
      lead: 'Roots are computed only for COMPLETED UTC days. Committing to a day '
        + 'still in progress would be a lie, so there is nothing to show yet.',
    },
    no_root: {
      label: 'No root for that day yet',
      cls: 'ap-flat',
      lead: 'The day is over but its root has not been computed. It is built '
        + 'lazily, the first time anyone asks for that day.',
    },
    not_in_root: {
      label: 'NOT in that day\'s committed leaf set',
      cls: 'ap-down',
      lead: 'A claim sealed on a day cannot be missing from that day\'s root. '
        + 'Treat this record as unproven.',
    },
    unknown: {
      label: 'Could not read the chain',
      cls: 'ap-flat',
      lead: 'Nothing is claimed either way. This is not a verdict — an anchored '
        + 'day and an unreachable node look the same from here.',
    },
  };

  function chainHtml(chain) {
    var c = chain || {};
    var copy = CHAIN_COPY[c.status] || CHAIN_COPY.unknown;
    var out = '<p class="ap-chain ' + copy.cls + '">' + esc(copy.label) + '</p>'
      + '<p class="ap-dim">' + esc(copy.lead) + '</p>';
    if (c.anchor_tx) {
      out += '<p><a class="ap-tx" href="https://basescan.org/tx/' + esc(c.anchor_tx)
        + '" target="_blank" rel="noopener">' + esc(c.anchor_tx) + '</a></p>';
    }
    if (c.day) {
      out += '<p class="ap-dim">Day <code>' + esc(c.day) + '</code>'
        + (c.root ? ' · root <code>' + esc(String(c.root).slice(0, 16)) + '…</code>' : '')
        + ' · <a href="/api/roots/verify/' + esc(c.day) + '">re-verify against Base</a></p>';
    }
    return out;
  }

  /** The claim itself, with the bytes a visitor hashes to check it. */
  function identityHtml(identity) {
    var d = identity || {};
    return '<div class="ap-block">'
      + '<h3>Identity</h3>'
      + '<p class="ap-name">' + esc(d.display_name || d.slug || '—') + '</p>'
      + '<p class="ap-dim">Claimed <code>' + esc(d.claimed_at || '—') + '</code></p>'
      + chainHtml(d.verify && d.verify.chain)
      + '<details class="ap-proof"><summary>Check it yourself</summary>'
      + '<p class="ap-dim">sha256 over the UTF-8 bytes of this payload, verbatim, '
      + 'is the seal below. The seal is a leaf in that day\'s Merkle root.</p>'
      + '<pre class="ap-payload">' + esc(d.seal_payload || '') + '</pre>'
      + '<p class="ap-dim">seal <code>' + esc(d.seal || '—') + '</code></p>'
      + '</details>'
      // Said out loud, because a page like this invites the stronger reading.
      + '<p class="ap-dim ap-limits"><b>Proves:</b> this slug was claimed on this '
      + 'date and has not been altered since. <b>Does not prove:</b> who operates '
      + 'this agent — no key signature is bound to the claim yet.</p>'
      + '</div>';
  }

  // ── the two records ─────────────────────────────────────────────────────

  function statsHtml(s) {
    var n = num(s.trades);
    return '<ul class="ap-stats">'
      + '<li><span>Trades</span><b>' + count(s.trades) + '</b></li>'
      + '<li><span>Median return</span><b class="' + pctClass(s.median_rom_pct) + '">'
        + pct(s.median_rom_pct) + '</b></li>'
      + '<li><span>Best</span><b class="' + pctClass(s.best_rom_pct) + '">'
        + pct(s.best_rom_pct) + '</b></li>'
      + '<li><span>Worst</span><b class="' + pctClass(s.worst_rom_pct) + '">'
        + pct(s.worst_rom_pct) + '</b></li>'
      + '<li><span>Won / lost</span><b>' + count(s.wins) + ' / ' + count(s.losses) + '</b></li>'
      + '<li><span>Liquidated</span><b>' + count(s.liquidations) + '</b></li>'
      + '</ul>'
      + (s.low_sample
        ? '<p class="ap-warn">Fewer than 10 trades — too few to mean much yet.</p>' : '')
      + (n === 0
        ? '<p class="ap-dim">No closed trades yet. Nothing has been measured.</p>' : '');
  }

  /** The copiers' record — members who copied this agent's picks. */
  function recordHtml(rec) {
    var d = rec || {};
    return '<div class="ap-block">'
      + '<h3>Copiers’ record</h3>'
      + '<p class="ap-dim">How copying this agent’s picks actually went, for the '
      + '<b>' + count(d.copiers) + '</b> member(s) who did. Forward-only: every row '
      + 'was sealed at OPEN, before its outcome existed. Sized by each member, so '
      + 'this answers how copying went — not how good the agent is.</p>'
      + statsHtml(d)
      + '</div>';
  }

  /**
   * The agent's own Arena trading.
   *
   * `own == null` means it has never traded for itself, which is a DIFFERENT
   * fact from having traded and scored nothing — and the difference is exactly
   * what a zeroed stats block would erase.
   */
  function ownHtml(own) {
    if (!own) {
      return '<div class="ap-block">'
        + '<h3>The agent’s own trading</h3>'
        + '<p class="ap-dim">This agent has never opened a position for itself. '
        + 'That is not a score of zero — there is nothing here to score.</p>'
        + '</div>';
    }
    return '<div class="ap-block">'
      + '<h3>The agent’s own trading</h3>'
      + '<p class="ap-dim">Positions this agent opened itself, through its own '
      + 'Arena key. Sized by the agent. Kept separate from the copiers’ record '
      + 'above and never added to it — they measure different things.</p>'
      + statsHtml(own)
      + '</div>';
  }

  /** Paper, always. Never let this page imply otherwise. */
  function footerHtml() {
    return '<p class="ap-foot">Paper trading · virtual stakes · percent '
      + 'returns only. Nothing on this page is a real-money result.</p>';
  }

  return {
    esc: esc, num: num, pct: pct, pctClass: pctClass, count: count,
    errorHtml: errorHtml, chainHtml: chainHtml, identityHtml: identityHtml,
    statsHtml: statsHtml, recordHtml: recordHtml, ownHtml: ownHtml,
    footerHtml: footerHtml, CHAIN_COPY: CHAIN_COPY,
  };
}));
