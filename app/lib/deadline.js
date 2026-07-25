'use strict';
/**
 * Run a promise with a wall-clock deadline.
 *
 * For handlers a browser is waiting on. A request that answers late is not
 * "slow" from the user's side — fetchJSON aborts it, and the page reports a
 * failure for work the server was still doing. So a handler that fans out to
 * upstream services has to bound each leg and degrade, rather than let one
 * slow dependency spend the whole client budget.
 *
 * Returns `fallback` if the deadline passes first. The underlying promise is
 * left running (and its rejection swallowed) — it may warm a cache for the
 * next caller, and it must never surface as an unhandled rejection.
 *
 * This only makes sense where a missing value is REPRESENTABLE. Callers that
 * would have to invent a number instead should fail loudly, not degrade.
 */
function withDeadline(promise, ms, fallback = null) {
  const p = Promise.resolve(promise);
  p.catch(() => {});
  let timer;
  const deadline = new Promise((resolve) => {
    timer = setTimeout(() => resolve(Symbol.for('rc.deadline')), Math.max(0, Number(ms) || 0));
  });
  return Promise.race([p.catch(() => Symbol.for('rc.deadline')), deadline])
    .then((v) => (v === Symbol.for('rc.deadline') ? fallback : v))
    .finally(() => clearTimeout(timer));
}

module.exports = { withDeadline };
