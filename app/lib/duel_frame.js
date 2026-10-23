'use strict';
/**
 * The link-preview card of a Daily Duel receipt (/api/frame/call/<key>/image
 * for a round:, duel:, settle: or review: key): what a chat or a feed shows
 * when a player shares the receipt's link.
 *
 * The generic card is a trading call's: a symbol and a side, coloured by the
 * side. A Duel call has no side in that sense, so it drew the market in green
 * with nothing beside it, and a round still sealed (its payload opens when its
 * day ends) has no payload to hash, so it printed "SERVER RECOMPUTED:
 * MISMATCH" in red over a seal nobody had broken.
 *
 * WHAT THIS CARD SAYS, AND WHERE IT COMES FROM:
 *   - a call is drawn by the receipt card's own model (public/js/receipt-card.js
 *     `model`), so the picture on /call and the preview in a chat name the
 *     same market, the same pick and the same times; the model is built only
 *     when the server's hash of the payload is the seal
 *   - the verdict is the settlement's own sealed payload, hashed here
 *     (`settleFrom`, then `verdictOf`, the same reading): green and red only
 *     for a measured verdict, and a flag or a review is said as the model says it
 *   - a sealed receipt with nothing to hash says when it opens and claims no
 *     match either way
 *   - the hash was recomputed by the SERVER; the card says so and points at
 *     the page that recomputes it in the reader's own browser
 *
 * No chips, no prices: a pick, a market, times, a seal and a verdict.
 *
 * Pure: `lines` takes the receipts and returns what to draw, so a test reads
 * the words without decoding a PNG.
 */

const crypto = require('crypto');
const Card = require('../public/js/receipt-card.js');
const Duel = require('../public/js/duel.js');

const KINDS = Object.freeze({
  duel_pick: 'DAILY DUEL RECEIPT',
  duel_round: 'DAILY DUEL ROUND',
  duel_settle: 'DAILY DUEL SETTLEMENT',
  duel_review: 'DAILY DUEL REVIEW',
});

const sha = (s) => crypto.createHash('sha256').update(String(s), 'utf8').digest('hex');

/**
 * Text the 5×7 font can draw (lib/pixel_card.js): its glyphs, uppercase.
 * The words the model writes in typography become the font's spelling, so a
 * band reads "+/-1.5%" rather than losing its sign.
 */
function plain(s) {
  return String(s == null ? '' : s)
    .replace(/±/g, '+/-').replace(/[—–]/g, '-').replace(/…/g, '...').replace(/[’‘]/g, '')
    .replace(/[▲▼●◆■]/g, '')
    .toUpperCase().replace(/\s+/g, ' ').trim();
}

const joined = (parts) => parts.map((x) => x.text).join('');

/** Whether the receipt's payload hashes to its seal; null when it has no payload to hash. */
function intact(d) {
  if (!d || typeof d.seal_payload !== 'string') return null;
  return sha(d.seal_payload) === String(d.seal || '');
}

/**
 * The verdict a settlement receipt `sr` adds up to, read from its own sealed
 * payload. Null when there is no settlement receipt to read or it does not
 * hash to its seal: the card then says the result is on the receipt.
 */
function settleOf(sr) {
  if (!sr || typeof sr.seal_payload !== 'string') return null;
  return Card.settleFrom(sr, sha(sr.seal_payload));
}

/**
 * [{ text, size, tone }] top to bottom, for the receipt `d` (the body
 * lookupCall answers) and its settlement's receipt `sr` (null: none, or not
 * read). `tone` is one of text, muted, gold, up, down.
 */
function lines(d, sr) {
  const out = [];
  const title = KINDS[d && d.kind] || 'DAILY DUEL';
  const seal = String((d && d.seal) || '');
  const ok = intact(d);
  const sealLine = { text: 'SEAL ' + seal.slice(0, 32), size: 2, tone: 'text' };
  const recomputed = ok === null ? null
    : { text: ok ? 'SERVER RECOMPUTED: MATCH' : 'SERVER RECOMPUTED: MISMATCH', size: 2, tone: ok ? 'up' : 'down' };
  const pointer = { text: 'OPEN TO RE-DERIVE THE HASH IN YOUR OWN BROWSER', size: 2, tone: 'muted' };

  if (d && d.kind === 'duel_pick' && ok === true) {
    let p = null;
    try { p = JSON.parse(d.seal_payload); } catch (e) { p = null; }
    const settle = d.settlement && d.settlement.key ? settleOf(sr) : null;
    const m = p ? Card.model(d, p, { intact: true, duel: Duel, settle, lang: 'en' }) : null;
    if (m) {
      out.push({ text: plain(m.market), size: 4, tone: 'text' });
      out.push({ text: plain('Called ' + m.pick), size: 3, tone: 'gold' });
      out.push(sealLine, recomputed);
      out.push({ text: plain(joined(m.lines[0])), size: 2, tone: 'muted' });
      const tone = m.verdict ? m.verdict.tone : 'muted';
      out.push({ text: plain(joined(m.lines[1])), size: 2, tone: tone === 'up' || tone === 'down' ? tone : 'muted' });
      out.push(pointer);
      return { title, lines: out };
    }
  }
  if (ok === null) {
    // Sealed, and nothing to hash yet: say when it opens, claim nothing.
    const at = d && d.opens_at ? new Date(d.opens_at) : null;
    const opens = at && Number.isFinite(at.getTime()) ? at.toISOString().slice(0, 16).replace('T', ' ') + ' UTC' : null;
    out.push({ text: 'SEALED', size: 4, tone: 'text' });
    out.push(sealLine);
    out.push({ text: opens ? 'OPENS ' + opens : 'OPENS WHEN ITS DAY ENDS', size: 2, tone: 'muted' });
    out.push({ text: 'NOTHING TO RECOMPUTE UNTIL THEN - NOTHING CLAIMED', size: 2, tone: 'muted' });
    return { title, lines: out };
  }
  out.push({ text: ok ? 'SEALED' : 'NOT INTACT', size: 4, tone: ok ? 'text' : 'down' });
  out.push(sealLine, recomputed, pointer);
  return { title, lines: out };
}

module.exports = { lines, plain, intact, KINDS };
