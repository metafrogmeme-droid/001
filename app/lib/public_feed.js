'use strict';
/**
 * The public shape of one agent mind-stream event.
 *
 * Every reader of the feed is public: GET /api/feed/recent (no auth, the
 * landing page and the dashboard timeline), the SSE `activity` event on the
 * unauthenticated /api/stream, a web push to every subscriber, and the MCP
 * tool `get_agent_feed`. The bot's producers are meant to send no dollar
 * amount of the account, and one of them did: every operator close was
 * emitted as `Closed BTC/USDT -$41.20` with `data: {pnl: -41.2}`, and the
 * receiver stored, streamed and pushed it verbatim. `public_no_dollars`
 * could not see it, because that guard reads KEYS in route files and the
 * dollar was inside stored TEXT.
 *
 * This is the backstop, and it runs at the ingest AND at both readers,
 * because rows already stored in the ring were written before it existed.
 * It reuses the flight scrubber rather than holding a second money rule:
 *
 * - `data` goes through `scrub`: a key naming an amount (`pnl`, `equity`,
 *   `margin`, anything with `usd`) is dropped, ratio keys (`_pct`, `_r`) stay,
 *   and a dollar figure in a string value is redacted.
 * - The title never carries a price in any producer, so every dollar figure
 *   in it is redacted.
 * - A body carries PRICES on three event types (a trade's entry and stop, a
 *   stop move, a thesis naming a level). Prices are public market facts, so
 *   those bodies lose only a SIGNED dollar figure, which is the shape of a
 *   P&L and never of a price. Every other type's body loses every dollar
 *   figure.
 */

const { scrub, scrubSignedDollars } = require('./flight');

const PRICE_BODY_TYPES = new Set(['trade_open', 'sl_move', 'thesis']);

function scrubText(text, pricesAllowed) {
  if (typeof text !== 'string') return text;
  const signed = scrubSignedDollars(text);
  return pricesAllowed ? signed : scrub(signed);
}

/**
 * A copy of `ev` with every dollar amount of the account removed. Fields that
 * are absent stay absent, and a `data` that is not an object or an array is
 * passed through untouched (the ingest stores only an object).
 */
function publicFeedEvent(ev) {
  if (!ev || typeof ev !== 'object') return ev;
  const out = { ...ev };
  if ('title' in ev) out.title = scrubText(ev.title, false);
  if ('body' in ev) out.body = scrubText(ev.body, PRICE_BODY_TYPES.has(ev.event_type));
  if (ev.data && typeof ev.data === 'object') out.data = scrub(ev.data);
  return out;
}

module.exports = { publicFeedEvent, PRICE_BODY_TYPES };
