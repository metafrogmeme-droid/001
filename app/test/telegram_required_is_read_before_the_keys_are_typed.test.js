'use strict';
/**
 * "Link Telegram first" was written, styled, and unreachable — twice over.
 *
 * The exchange-keys panel and the live-controls panel each carried a branch
 * for `r.status === 409` that rendered a note and a "Link Telegram first →"
 * button. It sat BELOW `mustRead(r)`, which throws on every non-2xx but 404,
 * so it could not run. And it could not have run above it either: neither
 * `GET /api/credentials/status` nor `GET /api/controls/status` ever answers
 * 409 — both answer 200 with `linked: false`. The 409 `telegram_required` is
 * what the POSTs send. So an unlinked user was handed a keys form whose
 * submit could only be refused, with their real API keys already typed into
 * it — the "409 dead-end" the hub's own ladder comment says its step order
 * was changed to avoid, still standing on the panel the ladder points at.
 *
 * The reading is `CredsGateModel.linkState`, three-valued, off the field the
 * GET carries; both loaders consult it after `mustRead` (a failed read still
 * throws) and before any form; the note is one function with the vocabulary's
 * own sentence; and `telegram_required` joins the panel-error vocabulary so
 * the 409 a POST answers is worded identically wherever it lands.
 */

process.env.JWT_SECRET = 'j'.repeat(64);
process.env.WEB_GATEWAY_SECRET = 'g'.repeat(64);

const test = require('node:test');
const assert = require('node:assert');
const http = require('node:http');
const fs = require('node:fs');
const path = require('node:path');
const { codeOnly } = require('./helpers/code_only');
const { linkState, needsTelegramFirst } = require('../public/js/creds-gate-model');
const M = require('../public/js/panel-error-model');
const i18n = require('../public/js/i18n');

const KEY = 'dd.err_telegram_required';
const DASH = codeOnly(fs.readFileSync(path.join(__dirname, '..', 'public', 'js', 'dashboard.js'), 'utf8'));

/** A loader's body: from its `renderPanel(C('<id>')` anchor to the next renderPanel call. */
function loader(id) {
  const from = DASH.indexOf(`renderPanel(C('${id}'), async () => {`);
  assert.ok(from >= 0, `no loader for ${id}`);
  const next = DASH.indexOf('renderPanel(', from + 20);
  return DASH.slice(from, next < 0 ? undefined : next);
}

// ── the reading ─────────────────────────────────────────────────────────────

test('linkState reads a boolean and refuses to guess from anything else', () => {
  assert.strictEqual(linkState({ linked: true }), 'linked');
  assert.strictEqual(linkState({ linked: false }), 'unlinked');
  // Absent, malformed, or no payload at all: not a reading. `'false'`, `0`
  // and `null` would each be a claim manufactured from a shape.
  for (const s of [{}, null, undefined, { linked: null }, { linked: 'false' },
                   { linked: 0 }, { linked: 1 }, { linked: 'true' }]) {
    assert.strictEqual(linkState(s), 'unknown', JSON.stringify(s));
    assert.strictEqual(needsTelegramFirst(s), false, `unknown must render the form: ${JSON.stringify(s)}`);
  }
  assert.strictEqual(needsTelegramFirst({ linked: false }), true);
  assert.strictEqual(needsTelegramFirst({ linked: true }), false);
});

// ── the server's actual shape ───────────────────────────────────────────────

let appServer;
let base;
let tokenLinked;
let tokenUnlinked;

function request(method, p, token) {
  return new Promise((resolve, reject) => {
    const req = http.request(`${base}${p}`, {
      method, headers: { Authorization: `Bearer ${token}` },
    }, (res) => {
      let text = '';
      res.on('data', (d) => text += d);
      res.on('end', () => {
        let data;
        try { data = JSON.parse(text); } catch (e) { data = undefined; }
        resolve({ status: res.statusCode, data, text });
      });
    });
    req.on('error', reject);
    req.end();
  });
}

test.before(async () => {
  const jwt = require('jsonwebtoken');
  const { pool } = require('../db');
  async function seed(email, linked) {
    await pool.execute('INSERT INTO users (email, password_hash, name) VALUES (?, ?, ?)', [email, 'x', email]);
    const [rows] = await pool.execute('SELECT id, email FROM users WHERE email = ?', [email]);
    const u = rows[0];
    if (linked) {
      await pool.execute('UPDATE users SET telegram_id = ? WHERE id = ?', ['779', u.id]);
      // MemoryDB's UPDATE sets telegram_id but not telegram_linked; set it directly.
      const [lrows] = await pool.execute('SELECT * FROM users WHERE id = ?', [u.id]);
      lrows[0].telegram_linked = true;
    }
    return jwt.sign({ user_id: u.id, email: u.email }, process.env.JWT_SECRET);
  }
  tokenLinked = await seed('linked@tg.io', true);
  tokenUnlinked = await seed('unlinked@tg.io', false);

  const express = require('express');
  const app = express();
  app.use(express.json());
  app.use('/api/credentials', require('../routes/credentials'));
  app.use('/api/controls', require('../routes/controls'));
  await new Promise((resolve) => { appServer = app.listen(0, '127.0.0.1', resolve); });
  base = `http://127.0.0.1:${appServer.address().port}`;
});

test.after(() => { if (appServer) appServer.close(); });

test('both status GETs answer 200 with linked:false for an unlinked account — never a 409', async () => {
  for (const p of ['/api/credentials/status', '/api/controls/status']) {
    const r = await request('GET', p, tokenUnlinked);
    assert.strictEqual(r.status, 200, `${p}: ${r.text}`);
    assert.strictEqual(r.data.linked, false, `${p}: ${r.text}`);
    assert.strictEqual(linkState(r.data), 'unlinked');
  }
});

test('and linked:true for a linked one, so the panels have a real reading to branch on', async () => {
  for (const p of ['/api/credentials/status', '/api/controls/status']) {
    const r = await request('GET', p, tokenLinked);
    assert.strictEqual(r.status, 200, `${p}: ${r.text}`);
    assert.strictEqual(linkState(r.data), 'linked', `${p}: ${r.text}`);
  }
});

test('the 409 is the POST\'s answer, and it carries the code the vocabulary names', async () => {
  const r = await new Promise((resolve, reject) => {
    const req = http.request(`${base}/api/controls`, {
      method: 'POST',
      headers: { Authorization: `Bearer ${tokenUnlinked}`, 'Content-Type': 'application/json' },
    }, (res) => {
      let text = '';
      res.on('data', (d) => text += d);
      res.on('end', () => resolve({ status: res.statusCode, data: JSON.parse(text) }));
    });
    req.on('error', reject);
    req.end(JSON.stringify({ paused: true }));
  });
  assert.strictEqual(r.status, 409);
  assert.strictEqual(M.codeOf(r.data), 'telegram_required');
});

// ── the panels read the field, after mustRead and before any form ───────────

test('both loaders branch on the reading, not on a status the GET never sends', () => {
  for (const id of ['akeys', 'actl']) {
    const body = loader(id);
    assert.ok(!/status\s*===\s*409/.test(body), `${id} still waits for a 409 its GET never sends`);
    const mr = body.indexOf('mustRead(r)');
    const link = body.indexOf('tgFirst(r.data)');
    assert.ok(mr >= 0, `${id}: mustRead is gone`);
    assert.ok(link >= 0, `${id}: the link reading is gone`);
    assert.ok(mr < link, `${id}: the link note is rendered before the read is known to have succeeded`);
    assert.ok(body.indexOf('tgFirstNote()') > link, `${id}: the unlinked branch does not render the note`);
  }
});

test('one note, one door, one sentence — and the door exists', () => {
  assert.strictEqual((DASH.match(/const tgFirstNote = /g) || []).length, 1);
  const from = DASH.indexOf('const tgFirstNote = ');
  const note = DASH.slice(from, DASH.indexOf('\n', DASH.indexOf('#account/atg', from)));
  assert.ok(note.includes(`T('${KEY}'`), 'the note does not use the vocabulary sentence');
  assert.ok(note.includes("T('dd.cta_tg'"), 'the button label is not translated');
  assert.ok(note.includes('href="#account/atg"'), 'the note names no door');
  assert.ok(DASH.includes("renderPanel(C('atg')"), 'the door it names is not a panel on this page');
  // The reading lives in the model, so the two panels cannot drift from each
  // other or from a test that plants a payload.
  assert.ok(/const tgFirst = \(data\) => \(window\.CredsGateModel \? CredsGateModel\.needsTelegramFirst\(data\) : false\)/.test(DASH),
    'the decision does not come from the model');
});

// ── the vocabulary ──────────────────────────────────────────────────────────

test('telegram_required is a named refusal with no Retry and the door in its sentence', () => {
  const v = M.panelFailure({ status: 409, code: 'telegram_required' });
  assert.strictEqual(v.key, KEY);
  assert.strictEqual(v.action, 'none', 'a Retry cannot link a Telegram account');
  assert.strictEqual(v.icon, 'icon-link');
  assert.match(v.fallback, /Telegram/);
  assert.match(v.fallback, /Account/);
  assert.match(v.fallback, /Paper trading works/);
});

test('the English sentence is one sentence in three places', () => {
  const en = i18n.STRINGS[KEY] && i18n.STRINGS[KEY].en;
  assert.ok(en, `${KEY} is not in the dictionary`);
  assert.strictEqual(M.BY_CODE.telegram_required.fallback, en, 'the model fallback drifted from the dictionary');
  const from = DASH.indexOf(`T('${KEY}', '`) + `T('${KEY}', '`.length;
  const inline = DASH.slice(from, DASH.indexOf("')", from));
  assert.strictEqual(inline, en, 'the dashboard fallback drifted from the dictionary');
  for (const l of i18n.LANGS) {
    assert.ok(String(i18n.STRINGS[KEY][l.code] || '').includes('Telegram'), `${l.code} lost the word Telegram`);
  }
});
