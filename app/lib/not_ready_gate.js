'use strict';

/**
 * The not-ready gate: what an /api/ call meets before this instance has
 * migrated its schema.
 *
 * Until the schema is migrated, DB-backed routes must not be reached: a query
 * against an unmigrated or unreachable database produces an error, and an
 * error rendered into a panel is indistinguishable from real data meaning
 * "nothing". Refusing with an explicit 503 keeps "we cannot answer" separate
 * from "the answer is zero", the same unreadable-is-never-zero rule the rest
 * of the codebase follows.
 *
 * Static assets are deliberately NOT gated. The marketing pages, the docs and
 * the learn pages need no database, so a database outage degrades the site
 * instead of blacking it out. The gate is mounted after express.static and
 * ahead of every API router (server.js).
 *
 * A NEW INSTANCE HOLDS A CALL INSTEAD OF REFUSING IT. The web host starts a
 * new instance every minute or two and sends it calls as soon as its port is
 * open, a few seconds before its first database check has answered. Refused
 * at once, those calls painted "the website's database is not ready yet" on
 * panels at random (reported 15 October, every panel on the dashboard), and
 * the pages' own retry (public/js/api-retry.js) rescued only about 4 in 10:
 * the host hands each call to an instance with a free slot, and an instance
 * that refuses at once always has one. So while the first attempt is still
 * running (reason 'starting'), a call waits for it, up to `holdMs`, and then
 * goes on to its route if the instance became ready. Measured instances were
 * ready 1 to 6 seconds after starting.
 *
 * What is NOT held:
 *   - a failed attempt. A db_* reason is an outage every instance shares, and
 *     holding it only delays the error a page should show. A call already
 *     waiting is answered the moment an attempt fails.
 *   - more than `maxHeld` calls at once. A held call costs a timer and a
 *     socket; past this many the gate refuses as it did before.
 * A call still not ready when its hold ends is refused with HELD_HEADER, so
 * the page does not spend its own retries on an answer that was already
 * waited for.
 *
 * A call is held BEFORE any route runs, and a caller that hangs up while held
 * is dropped without running one, so nothing a held call asked for is done
 * twice or done for nobody.
 */

/** The /api/ paths that need no database and are served while not ready. */
const DB_FREE_API = new Set([
  '/api/version',
  // The public SaR feed. Flag off answers 404 without a database. The
  // operator copy is not in this set: it has to know who is asking.
  '/api/sar-oracle/packets',
]);

/** Set on a refusal that was held first. Read by public/js/api-retry.js. */
const HELD_HEADER = 'X-RC-Starting-Held';

const DEFAULT_HOLD_MS = 5000;
// The shortest time any page gives a call is 7 seconds (fetchJSON callers),
// so a hold longer than this would turn "starting" into a page's own timeout,
// which names no cause.
const MAX_HOLD_MS = 6000;
const DEFAULT_MAX_HELD = 256;

/** The hold, read from STARTING_HOLD_MS: 0 turns it off; capped at MAX_HOLD_MS. */
function holdMsFrom(env) {
  const raw = env && env.STARTING_HOLD_MS;
  if (raw == null || String(raw).trim() === '') return DEFAULT_HOLD_MS;
  const n = Number(raw);
  if (!Number.isFinite(n) || n < 0) return DEFAULT_HOLD_MS;
  return Math.min(n, MAX_HOLD_MS);
}

function notReadyGate({ readiness, holdMs = DEFAULT_HOLD_MS, maxHeld = DEFAULT_MAX_HELD }) {
  let held = 0;

  function refuse(res, wasHeld) {
    const snap = readiness.snapshot();
    res.set('Retry-After', '15');
    if (wasHeld) res.set(HELD_HEADER, '1');
    return res.status(503).json({ error: 'starting', reason: snap.reason });
  }

  return function notReadyGate(req, res, next) {
    if (readiness.isReady()) return next();
    if (!req.path.startsWith('/api/')) return next();
    if (DB_FREE_API.has(req.path)) return next();
    if (!(holdMs > 0) || readiness.snapshot().reason !== 'starting' || held >= maxHeld) {
      return refuse(res, false);
    }
    held += 1;
    let gone = false;
    const onClose = () => { gone = true; };
    res.once('close', onClose);
    return readiness.whenSettled(holdMs).then(() => {
      held -= 1;
      res.removeListener('close', onClose);
      if (gone) return undefined;
      if (readiness.isReady()) return next();
      return refuse(res, true);
    });
  };
}

module.exports = {
  notReadyGate, holdMsFrom, DB_FREE_API, HELD_HEADER,
  DEFAULT_HOLD_MS, MAX_HOLD_MS, DEFAULT_MAX_HELD,
};
