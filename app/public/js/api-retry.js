/**
 * The website's own "starting" refusal, retried before any page sees it.
 *
 * THE FINDING (2026-10-11). The web host replaces its instance about every 70
 * seconds and starts sending a new instance traffic before that instance has
 * checked its database. Until the check finishes (measured 8 to 12 seconds),
 * the not-ready gate in server.js answers every /api/ call 503
 * {error: 'starting', reason: 'starting'}. Sampled live, 8 of 55 calls in one
 * minute and 8 of 73 readiness reads in three got that answer, so panels said
 * "the website's database is not ready yet" at random while the instance
 * beside it was answering normally.
 *
 * The host spreads calls across the instances that are up, so the same call
 * made again a moment later almost always reaches a ready one. This wraps
 * `fetch` once, as the first script on every page, and retries exactly that
 * answer and nothing else:
 *
 *   - only this site's own /api/ URLs;
 *   - only a 503 whose body is the gate's {error: 'starting'} with reason
 *     'starting' (a fresh instance, not a database that is failing: a
 *     db_* reason is an outage every instance shares, and retrying it only
 *     delays the error the page should show);
 *   - only when the request can be sent again unchanged (a URL and a plain
 *     body, never a Request object or a stream);
 *   - at most RETRY_DELAYS_MS.length times, stopping when the caller aborts.
 *
 * Retrying a POST is safe here because the gate refuses BEFORE any route
 * runs: the refused call did nothing. Every other response, and the last
 * "starting" one if all retries meet it too, reaches the page unchanged.
 */
(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  if (root && typeof root.fetch === 'function' && !root.fetch.__rcStartingRetry) {
    const origin = root.location && root.location.origin;
    root.fetch = api.wrap(root.fetch.bind(root), { origin });
  }
})(typeof window !== 'undefined' ? window : null, function () {
  'use strict';

  // About 2.3 s of waiting in all: well inside the 8-10 s the pages give a call.
  const RETRY_DELAYS_MS = [300, 700, 1300];

  function urlOf(input) {
    if (typeof input === 'string') return input;
    if (input && typeof input.href === 'string') return input.href; // a URL object
    return null;
  }

  function isOwnApi(input, origin) {
    const raw = urlOf(input);
    if (raw == null || !origin) return false;
    try {
      const u = new URL(raw, origin);
      return u.origin === origin && u.pathname.startsWith('/api/');
    } catch (_) {
      return false;
    }
  }

  function canSendAgain(init) {
    const body = init && init.body;
    if (body == null || typeof body === 'string') return true;
    const ok = ['URLSearchParams', 'FormData', 'Blob', 'ArrayBuffer'];
    return ok.some((name) => typeof globalThis[name] === 'function' && body instanceof globalThis[name]);
  }

  /** The gate's own answer, read from a copy so the page still gets the body. */
  async function isStarting(res) {
    if (!res || res.status !== 503) return false;
    try {
      const body = await res.clone().json();
      return !!body && body.error === 'starting' && body.reason === 'starting';
    } catch (_) {
      return false;
    }
  }

  function wrap(fetchImpl, { origin, sleep } = {}) {
    const wait = sleep || ((ms) => new Promise((r) => setTimeout(r, ms)));
    const wrapped = async function (input, init) {
      let res = await fetchImpl(input, init);
      if (!isOwnApi(input, origin) || !canSendAgain(init)) return res;
      const signal = init && init.signal;
      for (const ms of RETRY_DELAYS_MS) {
        if (!(await isStarting(res))) return res;
        if (signal && signal.aborted) return res;
        await wait(ms);
        if (signal && signal.aborted) return res;
        res = await fetchImpl(input, init);
      }
      return res;
    };
    wrapped.__rcStartingRetry = true;
    return wrapped;
  }

  return { wrap, isOwnApi, canSendAgain, isStarting, RETRY_DELAYS_MS };
});
