'use strict';
/**
 * The QR code on a receipt card (public/js/receipt-card.js): a receipt's own
 * page, /call/<key>, on this deployment's public origin, as a module matrix
 * the page draws.
 *
 * THE ORIGIN IS THE ONE lib/public_origin.js ANSWERS, as the phone-link QR's
 * is. A card is printed and passed on, and a QR is opened on a phone that
 * cannot say where it came from; an origin this server cannot vouch for (a
 * private host) is refused, never encoded.
 *
 * NO QUIET ZONE IN THE MATRIX. The page draws the four-module margin the QR
 * spec requires around it, on the card's light tile; `margin` here would put
 * light modules inside the matrix and make it look larger than it is.
 */

const QRCode = require('qrcode');

/** The receipt page a card's QR opens. */
function receiptUrl(origin, key) {
  return String(origin).replace(/\/+$/, '') + '/call/' + encodeURIComponent(String(key));
}

/**
 * → { url, size, rows } for `key` on `origin`: `rows` is `size` strings of
 * `size` characters, '1' a dark module and '0' a light one, top row first.
 */
function qrFor(origin, key) {
  const url = receiptUrl(origin, key);
  const qr = QRCode.create(url, { errorCorrectionLevel: 'M' });
  const size = qr.modules.size;
  const rows = [];
  for (let y = 0; y < size; y += 1) {
    let row = '';
    for (let x = 0; x < size; x += 1) row += qr.modules.get(y, x) ? '1' : '0';
    rows.push(row);
  }
  return { url, size, rows };
}

module.exports = { qrFor, receiptUrl };
