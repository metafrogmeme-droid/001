'use strict';
/**
 * The number renderings a market card shares, and the sample caveat that goes
 * beside a partial figure.
 *
 * THREE cards rendered the same quantity — a 24h volume — from three private
 * copies of one function, and the copy that rotted is the one whose comment
 * was missing. `rwa.js` and `research.js` each carried a note recording that
 * `Number(v) || 0` had printed `$0` for a volume the venue never reported;
 * `meme.js` still did it, so a DEX pair whose liquidity block the feed did not
 * carry rendered as `$0 liq` — the most alarming claim a memecoin card can
 * make, and the row's own risk read had correctly declined to flag it. Two
 * answers about one unread field, in a single row.
 *
 * Nothing here manufactures a figure: `null`, `undefined`, `NaN` and the
 * infinities are an em dash. `cover` is the other half of that rule — it
 * discloses a PARTIAL figure and says nothing beside an absent one, because a
 * sample caveat next to a dash is a hedge about a number that is not there.
 */

const DASH = '—';

/** A signed percent, or an em dash. NEVER `+null%`, never a manufactured 0. */
function pct(v) {
  return v == null ? DASH : `${v >= 0 ? '+' : ''}${v}%`;
}

/** A volume, or an em dash. */
function fmtVol(v) {
  const n = (v == null || !isFinite(Number(v))) ? null : Number(v);
  if (n == null) return DASH;
  if (n >= 1e9) return '$' + (n / 1e9).toFixed(1) + 'B';
  if (n >= 1e6) return '$' + (n / 1e6).toFixed(1) + 'M';
  return '$' + Math.round(n).toLocaleString('en-US');
}

/**
 * `(2 of 3 reported one)` when the figure is PARTIAL — never over an absence.
 *
 * `scored === 0` means the figure itself is an em dash, and a sample caveat
 * beside a dash is a hedge about a number that is not there: the rule the
 * chat prompt's bounded lists already state, where the empty and unreadable
 * outcomes get no bound caveat at all.
 */
function cover(value, scored, total) {
  return (value != null && scored > 0 && total != null && scored < total)
    ? ` <i>(${scored} of ${total} reported one)</i>` : '';
}

module.exports = { DASH, pct, fmtVol, cover };
