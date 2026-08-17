'use strict';
/**
 * The Arena's rules live in `public/js/arena_engine.js`.
 *
 * They moved there so the browser practice sandbox and this server load the
 * SAME bytes. A sandbox with its own copy of the liquidation maths teaches a
 * habit that costs money the first time somebody uses it for real, and a
 * second copy diverges the moment either side is touched.
 *
 * This file keeps its path and its exports so nothing that depends on it
 * changed. `test/arena_engine_shim.test.js` asserts the re-export is complete
 * — a shim that silently drops an export would make every caller of it fail
 * somewhere far away from here.
 */
module.exports = require('../public/js/arena_engine.js');
