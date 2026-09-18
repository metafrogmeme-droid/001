'use strict';
/**
 * WHICH ROUTES ARE PUBLIC — driven off the express dispatch chain, in order.
 *
 * `public_no_dollars.test.js` asked the question at FILE level:
 *
 *     !fs.readFileSync(routeFile).includes('authMiddleware')
 *
 * A file that gates ONE route leaves the public set entirely, and every
 * unauthenticated route in it goes with it. `reports.js` gates `/yield` and
 * serves `GET /` to anyone; the guard could not see that route, and the route
 * was publishing the operator's realized net P&L. Driven position-aware, 17
 * unauthenticated routes were invisible for exactly this reason — across
 * airdrops, arena, farcaster_auth, learn, nft, reports and roots.
 *
 * That is `tests/command_gates.py`'s lesson one runtime over: COVERAGE OF A
 * SPELLING IS NOT COVERAGE OF THE GUARD. There, a baseline of what IS gated
 * was silent about a command carrying NO gate; here, a file that mentions the
 * gate is read as a file that applies it.
 *
 * SO THIS IS A DRIVE, NOT A SCAN. Each router is required and its `stack` is
 * walked IN ORDER — which is how express itself dispatches, and the only way
 * to get the two things a scan cannot have:
 *
 *   - ORDER. `router.use(authMiddleware)` gates what is registered AFTER it
 *     and nothing before. Five of arena's public board routes and both of
 *     learn's lesson routes sit above their own file's `use` line, and read
 *     correct in the source either way.
 *   - IDENTITY for what a name cannot give. `rateLimit({...})` returns an
 *     ANONYMOUS closure, so a chain read off `fn.name` cannot tell a limiter
 *     from a validator. That factory is tagged on its own module export BEFORE
 *     any router loads. The auth middlewares are all named functions and are
 *     read off `layer.name`; tagging them too was ceremony and is not done.
 *
 * STATED BLIND SPOTS, because a helper whose coverage is overstated is the
 * failure this repo spends its guard tests preventing:
 *
 *   - An IN-BODY gate is invisible here. `arena.js`'s three season-admin
 *     routes call `adminOnly(req, res)` inside the handler and read as
 *     unauthenticated from the chain. That is the same blind spot
 *     `command_gates.py` documents, and it fails in the SAFE direction for
 *     every caller of this helper: a route with an in-body gate is reported
 *     as public, so it is CHECKED rather than excused.
 *   - A router that does not export an express Router (`stream.js` exports a
 *     plain object) is reported as unreadable, never as public. NOT-FOUND IS
 *     NOT NO-GATE.
 *   - Mount-level middleware in `server.js` (`app.use(path, mw, router)`) is
 *     not read. No route in this tree is gated that way today; if one is, it
 *     reads as public here, which is again the safe direction.
 */

const fs = require('node:fs');
const path = require('node:path');

const APP = path.join(__dirname, '..', '..');
const ROUTES = path.join(APP, 'routes');

/**
 * Middleware that ANSWERS "who is this caller". A limiter is not one.
 *
 * DRIVEN, not remembered. The first draft also listed `requireAdmin` and
 * `adminMiddleware`, and neither exists anywhere in this tree — two rows no
 * input can reach, which is a claim that there is a check. They are gone: a
 * name nothing has is not protection, and the direction a MISSING spelling
 * fails in is the loud one — an unrecognised middleware leaves the chain
 * unidentified, the route reads as public, and it gets CHECKED rather than
 * excused. That is the same asymmetry `tests/command_gates.py` relies on.
 *
 * `botAuth` is not an `app/auth` export either; it is a named function in
 * `routes/sync.js`, read off `layer.name`.
 */
const AUTH_FAMILY = new Set(['authMiddleware', 'botAuth', 'optionalAuth']);

let CACHE = null;

function drive() {
  if (CACHE) return CACHE;
  process.env.JWT_SECRET = process.env.JWT_SECRET || 'j'.repeat(64);

  // Tag the gate factories BEFORE any router loads. `require` caches, so the
  // routers below receive these same objects.
  const rl = require(path.join(APP, 'lib', 'rate_limit'));
  if (!rl.__tagged) {
    const orig = rl.rateLimit;
    rl.rateLimit = function (...a) {
      const f = orig.apply(this, a);
      try { f.__gate = 'rateLimit'; } catch (e) { /* frozen — name stays unknown */ }
      return f;
    };
    rl.__tagged = true;
  }
  // No equivalent tagging for `app/auth`: driven, every auth-family middleware
  // in this tree is a NAMED function (`authMiddleware`, `optionalAuth`,
  // `botAuth`), so `layer.name` already answers, and tagging the exports would
  // be ceremony. It would also not help the case it looks like it helps — a
  // FACTORY export's tag sits on the factory, not on the middleware it
  // returns. The rateLimit tag above is load-bearing because that factory's
  // product is the anonymous closure this walk would otherwise call `<anon>`.

  const rows = [];
  for (const file of fs.readdirSync(ROUTES).filter((f) => f.endsWith('.js')).sort()) {
    let router;
    try { router = require(path.join(ROUTES, file)); } catch (err) {
      rows.push({ file, unreadable: `load failed: ${err.message}` });
      continue;
    }
    if (!router || !Array.isArray(router.stack)) {
      rows.push({ file, unreadable: `exports no express Router (${typeof router})` });
      continue;
    }
    const inherited = [];
    for (const layer of router.stack) {
      const gate = (layer.handle && layer.handle.__gate) || layer.name || '<anon>';
      if (!layer.route) { inherited.push(gate); continue; }
      const own = (layer.route.stack || []).slice(0, -1)
        .map((s) => (s.handle && s.handle.__gate) || s.name || '<anon>');
      const chain = [...inherited, ...own];
      rows.push({
        file,
        methods: Object.keys(layer.route.methods).filter((m) => layer.route.methods[m]),
        path: layer.route.path,
        chain,
        authed: chain.some((g) => AUTH_FAMILY.has(g)),
      });
    }
  }
  CACHE = rows;
  return rows;
}

/** Every route express will dispatch, with the chain it will dispatch through. */
function routes() { return drive().filter((r) => !r.unreadable); }

/** Routers that could not be read. Never folded into "public". */
function unreadable() { return drive().filter((r) => r.unreadable); }

/** Routes reachable with no auth-family middleware in the chain. */
function publicRoutes() { return routes().filter((r) => !r.authed); }

/** The FILES holding at least one such route — what a source scan can open. */
function publicRouteFiles() {
  return [...new Set(publicRoutes().map((r) => r.file))].sort();
}

module.exports = {
  AUTH_FAMILY, routes, unreadable, publicRoutes, publicRouteFiles,
};
