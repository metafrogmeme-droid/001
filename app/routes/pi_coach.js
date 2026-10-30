'use strict';
/**
 * Ask the Claw, the coach in the Pi Browser app.
 *   GET  /api/pi/coach                  { enabled, limit, left, plus }
 *   POST /api/pi/coach { text, about? } { reply_html, intent, limit, left, plus }
 *
 * A Pi account only (users.pi_uid, as the tips): the coach is the Pi app's,
 * and its allowance is counted per person. The person's question goes to the
 * bot's /chat/pi-coach with the facts this server read for them
 * (lib/pi_coach.js), never facts the page sent: `about` names WHICH signal
 * or round they asked "Why?" about, and the server reads it.
 *
 * Answers: 400 a question that is empty or too long, or an `about` of the
 * wrong shape; 403 not a Pi account; 429 the day's allowance used, with when
 * it resets and what Coach Plus offers (or null); 502/503 the coach could
 * not be asked, which spends nothing. A question no model answered (a fixed
 * answer, an outage, a budget refusal) is given back; a reply the coach's
 * output check withheld is not, because a model ran.
 *
 * When the Coach AI disclosure is on for this caller, the JSON carries
 * `ai_disclosure` (the notice, the mark, and whether this is an admin
 * preview). A model's answer also carries `ai_generated` and `ai_mark`.
 * A caller the disclosure is off for gets neither field.
 */

const express = require('express');
const { pool } = require('../db');
const { authMiddleware } = require('../auth');
const { rateLimit, userKey } = require('../lib/rate_limit');
const gateway = require('../lib/gateway');
const coach = require('../lib/pi_coach');
const disclosure = require('../lib/coach_ai_disclosure');

const router = express.Router();

const MAX_TEXT = 1000;
const TIMEOUT_MS = 45000;
const limit = rateLimit({ name: 'pi_coach', windowMs: 60000, max: 6, key: userKey,
  message: 'One question at a time: give it a few seconds.' });

/** Test seam: { env, gateway, arena, duel, now }. */
function deps() { return router._deps || {}; }
function env() { return deps().env || process.env; }
function gw() { return deps().gateway || gateway; }
function now() { return deps().now ? deps().now() : new Date(); }

async function isPiAccount(userId) {
  const [rows] = await pool.execute('SELECT pi_uid FROM users WHERE id = ? LIMIT 1', [userId]);
  const uid = rows[0] && rows[0].pi_uid;
  return typeof uid === 'string' && uid.length > 0;
}

function nextUtcMidnight(t) {
  return new Date(Date.UTC(t.getUTCFullYear(), t.getUTCMonth(), t.getUTCDate() + 1)).toISOString();
}

/** The allowance as the page shows it: today's limit, what is left, Coach Plus. */
async function allowance(userId, t) {
  const lim = coach.limits(env());
  const offer = coach.plusOffer(env());
  // The window a bought Coach Plus runs for, whether or not it is offered today.
  const until = await coach.plusUntil(userId, coach.plusDays(env()), t);
  const max = until ? lim.plus : lim.free;
  const used = await coach.usedOn(userId, coach.utcDay(t));
  return {
    limit: max,
    left: Math.max(0, max - used),
    resets_at: nextUtcMidnight(t),
    plus: { offered: offer, daily: lim.plus, until: until ? until.toISOString() : null },
  };
}

/** `about`: absent, { kind: 'signal', key }, or { kind: 'round', id }. Anything else is refused. */
function readAbout(raw) {
  if (raw === undefined || raw === null) return { ok: true, about: null };
  if (typeof raw !== 'object') return { ok: false };
  if (raw.kind === 'signal' && typeof raw.key === 'string' && raw.key.length > 0 && raw.key.length <= 128) {
    return { ok: true, about: { kind: 'signal', key: raw.key } };
  }
  if (raw.kind === 'round' && Number.isInteger(raw.id) && raw.id > 0) {
    return { ok: true, about: { kind: 'round', id: raw.id } };
  }
  return { ok: false };
}

router.get('/', authMiddleware, limit, async (req, res) => {
  try {
    if (!(await isPiAccount(req.user.user_id))) {
      return res.json(await withDisclosure({ enabled: false, reason: 'pi_account_required' }, req));
    }
    if (!gw().isConfigured()) {
      return res.json(await withDisclosure({ enabled: false, reason: 'coach_not_configured' }, req));
    }
    return res.json(await withDisclosure({ enabled: true, ...(await allowance(req.user.user_id, now())) }, req));
  } catch (err) {
    console.error('Pi coach read error:', err.constructor.name);
    return res.status(503).json({ error: 'coach_unavailable' });
  }
});

router.post('/', authMiddleware, limit, async (req, res) => {
  const userId = req.user.user_id;
  const body = req.body || {};
  const text = typeof body.text === 'string' ? body.text.trim() : '';
  if (!text) return res.status(400).json({ error: 'text_required' });
  if (text.length > MAX_TEXT) return res.status(400).json({ error: 'text_too_long', max: MAX_TEXT });
  const ab = readAbout(body.about);
  if (!ab.ok) return res.status(400).json({ error: 'about_unreadable' });

  const t = now();
  const day = coach.utcDay(t);
  let taken = false;
  try {
    if (!(await isPiAccount(userId))) return res.status(403).json({ error: 'pi_account_required' });
    if (!gw().isConfigured()) return res.status(503).json({ error: 'coach_not_configured' });
    const before = await allowance(userId, t);
    taken = await coach.take(userId, day, before.limit);
    if (!taken) {
      return res.status(429).json({ error: 'daily_limit', limit: before.limit, left: 0,
        resets_at: before.resets_at, plus: before.plus });
    }
    const context = await coach.facts(userId, ab.about, { arena: deps().arena, duel: deps().duel, tickers: deps().tickers }, text);
    const lang = typeof body.lang === 'string' && /^[a-zA-Z-]{2,12}$/.test(body.lang) ? body.lang : '';
    const r = await gw().postGateway('/chat/pi-coach', { text, context, ...(lang ? { lang } : {}) }, TIMEOUT_MS);
    const data = r && r.data;
    if (!r || r.status !== 200 || !data || typeof data.reply_html !== 'string') {
      await coach.giveBack(userId, day);
      taken = false;
      return res.status(502).json({ error: 'coach_unavailable' });
    }
    // Only a model's answer is a question spent.
    if (data.answered !== true) {
      await coach.giveBack(userId, day);
      taken = false;
    }
    const after = await allowance(userId, t);
    const intent = data.intent || 'pi_coach';
    const view = await disclosure.viewFor(req, env());
    return res.json({
      reply_html: data.reply_html, intent,
      limit: after.limit, left: after.left, resets_at: after.resets_at, plus: after.plus,
      ...disclosure.replyFields(view, intent),
    });
  } catch (err) {
    console.error('Pi coach error:', err.constructor.name);
    if (taken) { try { await coach.giveBack(userId, day); } catch (e) { /* the count stays, which is the safe side */ } }
    return res.status(503).json({ error: 'coach_unavailable' });
  }
});

/** Attach the disclosure when this caller may see it. Omit the key otherwise. */
async function withDisclosure(body, req) {
  const extra = disclosure.payload(await disclosure.viewFor(req, env()));
  if (!extra) return body;
  return { ...body, ai_disclosure: extra };
}

module.exports = router;
module.exports.readAbout = readAbout;
module.exports.MAX_TEXT = MAX_TEXT;
