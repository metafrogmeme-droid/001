'use strict';
/**
 * Keccak-256 (the Ethereum one) — see `../public/js/keccak256.js` for the
 * implementation and the post-mortem that produced it.
 *
 * It lives under public/ because the browser needs it too: the operator's
 * registration page recomputes the manifest hash client-side before enabling
 * a send, which is the check nobody could run while a stubbed `ethers`
 * quietly served SHA-256. Shipping a second copy for the browser would
 * reintroduce that defect one level over — two implementations of the same
 * digest, drifting, indistinguishable from their output. So there is one,
 * UMD-wrapped, and this is the server's door to it.
 */

module.exports = require('../public/js/keccak256.js');
