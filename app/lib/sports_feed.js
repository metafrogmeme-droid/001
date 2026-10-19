'use strict';
/**
 * The Daily Duel's football round: one match a day, from football-data.org.
 *
 * The card's other rounds are settled by the market; this one is settled by
 * a final score. The feed is football-data.org's v4 API (the free tier covers
 * the major European competitions at ten calls a minute, with a token set in
 * the website server's environment as FOOTBALL_DATA_TOKEN). Unset, the card
 * deals no match, and says nothing about football: an absent feed is not an
 * empty fixture list.
 *
 * WHAT IS DEALT. The day's card takes the earliest kick-off of the best
 * competition on offer that is still at least LEAD_MS away when the card is
 * dealt, so there is time to call it; a match belongs to the UTC day it kicks
 * off on, and calls close at kick-off (lib/duel.js isCallable). A call
 * resolves FULL_TIME_MS after kick-off, when a result is normally published;
 * a match not yet FINISHED then is read again on the next settle, and one
 * that never finishes (postponed, abandoned) is unresolved after the give-up
 * window, never anyone's loss.
 *
 * HONESTY. A feed that cannot be read THROWS out of candidateFor, so the
 * caller can say "unreadable" rather than "no match today"; a day with no
 * qualifying match answers null, which is the other sentence. A result is
 * returned only from a FINISHED match with both scores read as numbers; an
 * in-play score, a cancelled match, a body without a score, answer null.
 *
 * The transport is injectable (setSportsTransport) so tests drive the parsing
 * and the choosing without the network; nothing here logs the token.
 */

const https = require('https');

const PROVIDER = 'football-data';
const TOKEN_ENV = 'FOOTBALL_DATA_TOKEN';
const DEFAULT_BASE = 'https://api.football-data.org/v4';
/** Competitions by preference, best first; anything else ranks after these. */
const PREFERRED = Object.freeze(['CL', 'PL', 'PD', 'BL1', 'SA', 'FL1', 'DED', 'PPL', 'ELC', 'BSA', 'WC', 'EC']);
/** A match must be at least this far off when the card is dealt. */
const LEAD_MS = 60 * 60000;
/** Kick-off to a published result: 90 minutes, half-time, stoppage, and slack. */
const FULL_TIME_MS = 150 * 60000;
const TTL_MS = 10 * 60000;
const TIMEOUT_MS = 8000;
/** Matches that have not started. */
const UPCOMING = Object.freeze(['SCHEDULED', 'TIMED']);

let transport = null;
const cache = new Map();

/** Injectable transport: `(path) => parsed JSON`. Null restores the network. */
function setSportsTransport(fn) {
  transport = fn || null;
  cache.clear();
}

function _resetCache() { cache.clear(); }

function token(env) {
  const e = env != null ? env : process.env;
  return String(e[TOKEN_ENV] || '').trim();
}

function isConfigured(env) {
  return token(env).length > 0;
}

function baseUrl(env) {
  const e = env != null ? env : process.env;
  return String(e.FOOTBALL_DATA_URL || DEFAULT_BASE).replace(/\/+$/, '');
}

/** One authenticated GET, parsed. Rejects on any status but 200. */
function request(path) {
  const url = baseUrl() + path;
  return new Promise((resolve, reject) => {
    const req = https.get(url, {
      timeout: TIMEOUT_MS,
      headers: { 'X-Auth-Token': token(), Accept: 'application/json', 'User-Agent': 'RUNECLAW-Duel/1.0' },
    }, (res) => {
      let body = '';
      res.on('data', (d) => { body += d; });
      res.on('end', () => {
        if (res.statusCode !== 200) {
          const err = new Error(`football-data answered ${res.statusCode}`);
          err.status = res.statusCode;
          return reject(err);
        }
        try { resolve(JSON.parse(body)); } catch (e) { reject(new Error('Invalid JSON')); }
      });
    });
    req.on('error', reject);
    req.on('timeout', () => { req.destroy(); reject(new Error('Timeout')); });
  });
}

async function get(path) {
  const hit = cache.get(path);
  if (hit && Date.now() - hit.ts < TTL_MS) return hit.data;
  const data = await (transport ? transport(path) : request(path));
  cache.set(path, { ts: Date.now(), data });
  return data;
}

/** The feed's match, in the shape the round stores (duel_rounds.meta). */
function matchOf(m) {
  const x = m || {};
  const team = (t) => ({
    name: String((t && t.name) || ''),
    short: String((t && (t.shortName || t.tla || t.name)) || ''),
  });
  const home = team(x.homeTeam);
  const away = team(x.awayTeam);
  return {
    provider: PROVIDER,
    event_id: x.id == null ? null : String(x.id),
    home: home.name,
    away: away.name,
    home_short: home.short,
    away_short: away.short,
    competition: String((x.competition && x.competition.name) || ''),
    code: String((x.competition && x.competition.code) || ''),
    kickoff: x.utcDate == null ? null : new Date(x.utcDate).toISOString(),
    status: String(x.status || ''),
  };
}

function rank(code) {
  const i = PREFERRED.indexOf(String(code || ''));
  return i < 0 ? PREFERRED.length : i;
}

/**
 * Which of a day's matches is dealt, if any. PURE: given the feed's rows,
 * the day and the clock. Not started, kicking off on the day and at least
 * LEAD_MS from now, both teams named; the best competition first, then the
 * earliest kick-off; null when nothing qualifies.
 */
function pickCandidate(matches, day, now = new Date()) {
  const t = new Date(now).getTime();
  const rows = (Array.isArray(matches) ? matches : []).map(matchOf).filter((m) => (
    m.event_id && m.home && m.away && m.kickoff
    && UPCOMING.indexOf(m.status) >= 0
    && m.kickoff.slice(0, 10) === String(day)
    && Date.parse(m.kickoff) - t >= LEAD_MS
  ));
  rows.sort((a, b) => (rank(a.code) - rank(b.code)) || (Date.parse(a.kickoff) - Date.parse(b.kickoff)));
  return rows.length ? rows[0] : null;
}

/** The day's dealt match, or null; THROWS when the feed cannot be read. */
async function candidateFor(day, now = new Date()) {
  const body = await get(`/matches?dateFrom=${encodeURIComponent(day)}&dateTo=${encodeURIComponent(day)}`);
  if (!body || !Array.isArray(body.matches)) {
    const err = new Error('football-data answered without a matches list');
    err.rcReason = 'sports_unreadable';
    throw err;
  }
  return pickCandidate(body.matches, day, now);
}

/** A FINISHED match's full-time score as numbers, or null. Never a guess. */
function resultOf(body) {
  const m = body || {};
  if (String(m.status || '') !== 'FINISHED') return null;
  const ft = (m.score && m.score.fullTime) || {};
  const home = Number(ft.home);
  const away = Number(ft.away);
  if (ft.home == null || ft.away == null || !Number.isFinite(home) || !Number.isFinite(away)
      || home < 0 || away < 0) return null;
  return { home, away };
}

/** The final score of a match, or null when it has none yet; THROWS when unreadable. */
async function finalScore(eventId) {
  const id = String(eventId == null ? '' : eventId).replace(/^FD:/, '');
  if (!/^\d+$/.test(id)) return null;
  return resultOf(await get(`/matches/${id}`));
}

module.exports = {
  PROVIDER, TOKEN_ENV, PREFERRED, LEAD_MS, FULL_TIME_MS, TTL_MS, UPCOMING,
  isConfigured, matchOf, pickCandidate, candidateFor, resultOf, finalScore,
  setSportsTransport, _resetCache,
};
