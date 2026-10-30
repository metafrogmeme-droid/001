'use strict';
/**
 * Coach AI disclosure (lib/coach_ai_disclosure.js).
 *
 * Off unless COACH_AI_DISCLOSURE_ENABLED is 1 or true AND
 * COACH_AI_DISCLOSURE_NETWORK is testnet. While that public reader is off,
 * an operator sees the sentences and an admin-preview mark; anyone else
 * sees neither field. A model's answer is marked. A fixed answer is not.
 * The Pi page draws the same sentences, and only when the response carries
 * them.
 */

delete process.env.DATABASE_URL;
process.env.JWT_SECRET = process.env.JWT_SECRET || 'j'.repeat(64);
delete process.env.COACH_AI_DISCLOSURE_ENABLED;
delete process.env.COACH_AI_DISCLOSURE_NETWORK;

const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const http = require('node:http');
const path = require('node:path');
const express = require('express');

const { pool } = require('../db');
const { findOrCreateOAuthUser, signToken } = require('../auth');
const router = require('../routes/pi_coach');
const disc = require('../lib/coach_ai_disclosure');
const preview = require('../lib/admin_preview');
const page = require('../public/js/pi-coach.js');

const T0 = new Date('2026-10-30T12:00:00Z');
const OPEN = { enabled: true, limit: 20, left: 20, resets_at: '2026-10-31T00:00:00.000Z', plus: { offered: null, daily: 100, until: null } };
const PUBLIC = { COACH_AI_DISCLOSURE_ENABLED: '1', COACH_AI_DISCLOSURE_NETWORK: 'testnet' };
const LIB = fs.readFileSync(path.join(__dirname, '..', 'lib', 'coach_ai_disclosure.js'), 'utf8');
const CLIENT = fs.readFileSync(path.join(__dirname, '..', 'public', 'js', 'pi-coach.js'), 'utf8');

function fakeGateway(reply = { reply_html: 'A <b>stop</b> is a planned exit.', intent: 'pi_coach', answered: true }) {
  const calls = [];
  return {
    calls,
    isConfigured: () => true,
    postGateway: async (p, body) => { calls.push({ p, body }); return { status: 200, data: reply }; },
  };
}

async function serve(env = {}) {
  const gateway = fakeGateway();
  router._deps = {
    gateway, env, now: () => T0,
    arena: { loadPositions: async () => [] },
    duel: { cardFor: async () => ({ day: '2026-10-30', rounds: [] }) },
    tickers: { getTickersWithin: async () => ({ map: {}, ageMs: 0 }) },
  };
  const app = express();
  app.use(express.json());
  app.use('/api/pi/coach', router);
  const server = http.createServer(app);
  await new Promise((r) => server.listen(0, '127.0.0.1', r));
  return { server, gateway, base: `http://127.0.0.1:${server.address().port}` };
}

let seq = 0;
async function account({ admin = false } = {}) {
  seq += 1;
  const user = await findOrCreateOAuthUser({ provider: 'pi', providerId: `disc-${seq}-${Date.now()}` });
  if (admin) pool.users.find((u) => u.id === user.id).plan = 'admin';
  return { id: user.id, token: signToken(user) };
}

async function hit(base, token, body, method = 'POST') {
  const r = await fetch(`${base}/api/pi/coach`, {
    method,
    headers: { ...(token ? { authorization: `Bearer ${token}` } : {}), ...(method === 'POST' ? { 'content-type': 'application/json' } : {}) },
    body: method === 'POST' ? JSON.stringify(body) : undefined,
  });
  return { status: r.status, body: await r.json().catch(() => null) };
}

function screen(getBody) {
  const view = page.create();
  const ctx = {
    api(p, opts) {
      if (opts && opts.method === 'POST') return Promise.resolve({ ok: true, body: getBody.post });
      return Promise.resolve({ ok: true, body: getBody.get });
    },
    render() {},
  };
  return view.signedIn(ctx).then(() => ({ view, ctx }));
}

test('the flag is off unless it is 1 or true and the network is testnet', () => {
  assert.equal(disc.flagOn({}), false);
  assert.equal(disc.flagOn({ COACH_AI_DISCLOSURE_ENABLED: '1' }), true);
  assert.equal(disc.flagOn({ COACH_AI_DISCLOSURE_ENABLED: ' TRUE ' }), true);
  for (const v of ['', '0', 'false', 'yes', 'on', '2']) {
    assert.equal(disc.flagOn({ COACH_AI_DISCLOSURE_ENABLED: v }), false, v);
  }
  assert.equal(disc.testnet({}), false);
  assert.equal(disc.testnet({ COACH_AI_DISCLOSURE_NETWORK: 'mainnet' }), false);
  assert.equal(disc.testnet({ COACH_AI_DISCLOSURE_NETWORK: ' Testnet ' }), true);
  assert.equal(disc.publicOn({}), false);
  assert.equal(disc.publicOn({ COACH_AI_DISCLOSURE_ENABLED: '1' }), false);
  assert.equal(disc.publicOn({ COACH_AI_DISCLOSURE_NETWORK: 'testnet' }), false);
  assert.equal(disc.publicOn(PUBLIC), true);
  assert.equal(disc.publicOn({ COACH_AI_DISCLOSURE_ENABLED: '1', COACH_AI_DISCLOSURE_NETWORK: 'mainnet' }), false);
  assert.equal(disc.publicOn({ COACH_AI_DISCLOSURE_ENABLED: 'yes', COACH_AI_DISCLOSURE_NETWORK: 'testnet' }), false);
  assert.equal(preview.publicOn('COACH_AI_DISCLOSURE_ENABLED', PUBLIC), true);
  assert.equal(preview.publicOn('COACH_AI_DISCLOSURE_ENABLED', {}), false);
});

test('a thrown read is off, and a fixed answer is not marked generated', async () => {
  const boom = new Proxy({}, { get() { throw new Error('unread'); } });
  assert.deepStrictEqual(await disc.viewFor({ user: { user_id: 1 } }, boom), { show: false, preview: false });
  const on = { show: true, preview: false };
  assert.equal(disc.payload({ show: false, preview: false }), null);
  assert.deepStrictEqual(disc.replyFields({ show: false }, 'pi_coach'), {});
  const model = disc.replyFields(on, 'pi_coach');
  assert.equal(model.ai_generated, true);
  assert.equal(model.ai_mark, disc.MARK);
  assert.equal(model.ai_disclosure.preview, false);
  assert.equal(model.ai_disclosure.notice, disc.NOTICE);
  for (const intent of page.FIXED_INTENTS) {
    const fixed = disc.replyFields(on, intent);
    assert.equal(Object.prototype.hasOwnProperty.call(fixed, 'ai_generated'), false, intent);
    assert.equal(fixed.ai_disclosure.notice, disc.NOTICE);
  }
  const previewOn = disc.payload({ show: true, preview: true });
  assert.equal(previewOn.preview, true);
});

test('the page draws the module sentences, and nothing else decides the mark', () => {
  assert.equal(page.NOTICE, disc.NOTICE);
  assert.equal(page.MARK, disc.MARK);
  assert.equal(page.PREVIEW, preview.BANNER);
  assert.ok(CLIENT.includes("T('" + disc.NOTICE + "')"));
  assert.ok(CLIENT.includes("T('" + disc.MARK + "')"));
  assert.ok(CLIENT.includes("var NOTICE = '" + disc.NOTICE + "';"));
  assert.ok(CLIENT.includes('Education and practice, not advice. Your question and a summary'));
  assert.doesNotMatch(LIB, /Art\.?\s*50|MiCA|MiFID|GDPR|EU AI Act/i);
  assert.doesNotMatch(LIB, /\b(tokens?|presales?|prices?|stakes?)\b|\$|rclaw|PRIVATE_KEY|mnemonic/i);
  assert.doesNotMatch(LIB, /signTransaction|broadcast|sendTransaction/);
});

test('the page shows the notice only when the server sent it, and marks only a generated reply', async () => {
  global.window = { RCMiniView: require('../public/js/miniapp-view.js') };
  const quiet = await screen({ get: OPEN });
  const quietHtml = quiet.view.html();
  assert.equal(quietHtml.includes(disc.NOTICE), false);
  assert.equal(quietHtml.includes('data-ai-system'), false);
  assert.match(quietHtml, /Education and practice, not advice/);

  const shown = await screen({
    get: { ...OPEN, ai_disclosure: { notice: disc.NOTICE, mark: disc.MARK, preview: false } },
    post: {
      reply_html: 'A <b>stop</b> is a planned exit.', intent: 'pi_coach', ai_generated: true,
      ai_mark: disc.MARK, ai_disclosure: { notice: disc.NOTICE, mark: disc.MARK, preview: false },
      limit: 20, left: 19,
    },
  });
  const shownHtml = shown.view.html();
  assert.ok(shownHtml.includes(disc.NOTICE));
  assert.ok(shownHtml.includes('data-ai-system="coach"'));
  assert.equal(shownHtml.includes(preview.BANNER), false);
  assert.match(shownHtml, /Education and practice, not advice/);

  const admin = await screen({
    get: { ...OPEN, ai_disclosure: { notice: disc.NOTICE, mark: disc.MARK, preview: true } },
  });
  assert.ok(admin.view.html().includes(preview.BANNER));

  const wrong = await screen({
    get: { ...OPEN, ai_disclosure: { notice: 'something else', mark: disc.MARK, preview: false } },
  });
  assert.equal(wrong.view.html().includes('something else'), false);
  assert.equal(wrong.view.html().includes('data-ai-system'), false);

  await shown.view.ask('what is a stop?', null, shown.ctx);
  const marked = shown.view.html();
  assert.ok(marked.includes('data-ai-generated="true"'));
  assert.ok(marked.includes(disc.MARK));
  assert.match(marked, /A <b>stop<\/b> is a planned exit/);

  const carried = { notice: disc.NOTICE, mark: disc.MARK, preview: false };
  const fixed = await screen({
    get: { ...OPEN, ai_disclosure: carried },
    post: { reply_html: 'This app is practice only.', intent: 'pi_coach_token', ai_disclosure: carried, limit: 20, left: 20 },
  });
  await fixed.view.ask('what is the project?', null, fixed.ctx);
  const fixedHtml = fixed.view.html();
  assert.equal(fixedHtml.includes('data-ai-generated'), false);
  assert.ok(fixedHtml.includes(disc.NOTICE));

  const unmarked = await screen({
    get: { ...OPEN, ai_disclosure: carried },
    post: { reply_html: '<b>Hi</b>', intent: 'pi_coach', ai_disclosure: carried, limit: 20, left: 19 },
  });
  await unmarked.view.ask('hello', null, unmarked.ctx);
  const unmarkedHtml = unmarked.view.html();
  assert.equal(unmarkedHtml.includes('data-ai-generated'), false);
  assert.ok(unmarkedHtml.includes(disc.NOTICE));

  const dropped = await screen({
    get: { ...OPEN, ai_disclosure: carried },
    post: { reply_html: '<b>Hi</b>', intent: 'pi_coach', limit: 20, left: 19 },
  });
  await dropped.view.ask('hello', null, dropped.ctx);
  assert.equal(dropped.view.html().includes(disc.NOTICE), false);
  delete global.window;
});

test('flag off hides the disclosure; an operator previews it; testnet on is public', async () => {
  const user = await account();
  const admin = await account({ admin: true });
  const off = await serve({});
  try {
    const anon = await hit(off.base, null, null, 'GET');
    assert.equal(anon.status, 401);
    const pub = await hit(off.base, user.token, null, 'GET');
    assert.equal(pub.status, 200);
    assert.equal(pub.body.enabled, true);
    assert.equal(Object.prototype.hasOwnProperty.call(pub.body, 'ai_disclosure'), false);
    const prev = await hit(off.base, admin.token, null, 'GET');
    assert.equal(prev.body.ai_disclosure.preview, true);
    assert.equal(prev.body.ai_disclosure.notice, disc.NOTICE);
    assert.equal(prev.body.ai_disclosure.mark, disc.MARK);
    const asked = await hit(off.base, user.token, { text: 'what is a stop?' });
    assert.equal(asked.status, 200);
    assert.equal(asked.body.reply_html, 'A <b>stop</b> is a planned exit.');
    assert.equal(Object.prototype.hasOwnProperty.call(asked.body, 'ai_generated'), false);
    assert.equal(Object.prototype.hasOwnProperty.call(asked.body, 'ai_disclosure'), false);
    assert.deepStrictEqual(Object.keys(off.gateway.calls[0].body).sort(), ['context', 'text']);
  } finally {
    off.server.close();
    router._deps = undefined;
  }

  const mainnet = await serve({ COACH_AI_DISCLOSURE_ENABLED: '1', COACH_AI_DISCLOSURE_NETWORK: 'mainnet' });
  try {
    const pub = await hit(mainnet.base, user.token, null, 'GET');
    assert.equal(Object.prototype.hasOwnProperty.call(pub.body, 'ai_disclosure'), false);
    const prev = await hit(mainnet.base, admin.token, null, 'GET');
    assert.equal(prev.body.ai_disclosure.preview, true);
  } finally {
    mainnet.server.close();
    router._deps = undefined;
  }

  const on = await serve(PUBLIC);
  try {
    const pub = await hit(on.base, user.token, null, 'GET');
    assert.equal(pub.body.ai_disclosure.preview, false);
    assert.equal(pub.body.ai_disclosure.notice, disc.NOTICE);
    const model = await hit(on.base, user.token, { text: 'what is a stop?' });
    assert.equal(model.body.ai_generated, true);
    assert.equal(model.body.ai_mark, disc.MARK);
    assert.equal(model.body.ai_disclosure.preview, false);
    on.gateway.postGateway = async () => ({
      status: 200,
      data: { reply_html: 'This app is practice only.', intent: 'pi_coach_token', answered: false },
    });
    const fixed = await hit(on.base, admin.token, { text: 'tell me about the project' });
    assert.equal(fixed.status, 200);
    assert.equal(Object.prototype.hasOwnProperty.call(fixed.body, 'ai_generated'), false);
    assert.equal(fixed.body.ai_disclosure.notice, disc.NOTICE);
    assert.equal(fixed.body.intent, 'pi_coach_token');
  } finally {
    on.server.close();
    router._deps = undefined;
  }
});
