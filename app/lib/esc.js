'use strict';
/**
 * HTML-escape a value for a chat card.
 *
 * ONE copy. `letter.js`, `alerts.js` and `research.js` each carried a private
 * `esc` and the six intercept renderers that interpolate third-party text
 * (a DEXScreener token symbol, an OpenSea collection name, a venue key from
 * the report cache) carried none — a name holding `<b` was tolerated by the
 * browser's markup allowlist and refused by Telegram's parser the moment the
 * same card was forwarded there. Read by every renderer whose card leaves
 * the browser.
 */
function esc(s) {
  return String(s ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

module.exports = { esc };
