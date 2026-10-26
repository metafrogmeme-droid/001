'use strict';
/**
 * Claw Guard signals for a Claw Watch check.
 *
 * v0.1 of Claw Guard gates a page. It does not publish a signal reader.
 * Until that reader exists, this returns unread. A throw is also unread.
 * Neither result is a clear chain.
 */

function publicSignals() {
  try {
    const guard = require('./claw_guard_v01');
    if (guard && typeof guard.publicSignals === 'function') {
      const got = guard.publicSignals();
      if (got && typeof got === 'object' && got.status) return got;
    }
    return { status: 'unread', reason: 'claw_guard_signals_not_shipped' };
  } catch (err) {
    return { status: 'unread', reason: 'unreadable' };
  }
}

module.exports = { publicSignals: publicSignals };
