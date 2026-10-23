'use strict';
/**
 * Scam or Safe? Daily.
 *
 * The hard rules, each named by a test below:
 *   a full address, a blanked amount, a signing url, a banned word
 *   a poisoned field is withheld before display
 *   the seal is the duel pick seal, and a second pick is the first
 *   an unverified seal withholds the verdict
 *   an empty tally is not zero percent
 *   one puzzle per UTC day, the duel's cutoff
 *   the explanation carries the coach's AI label
 *   the flag off seals nothing and adds no tab
 *   the direction card does not deal this game
 *   a mask cannot be copied out
 */

const { describe, test, before, after } = require('node:test');
const assert = require('node:assert');
const crypto = require('crypto');
const fs = require('fs');
const http = require('node:http');
const os = require('os');
const path = require('path');
const express = require('express');

const STATE = fs.mkdtempSync(path.join(os.tmpdir(), 'sos-'));
process.env.JWT_SECRET = 'j'.repeat(64);
process.env.BOT_SYNC_SECRET = 'b'.repeat(64);
delete process.env.DATABASE_URL;
delete process.env.SCAM_OR_SAFE_V0;
process.env.SCAM_OR_SAFE_STATE_DIR = STATE;

const sos = require('../lib/scam_or_safe');
const duel = require('../lib/duel');
const gate = require('../lib/daily_duel_gate');
const { codeOnly } = require('./helpers/code_only');

const NOW = '2026-10-23T15:00:00.000Z';
const DAY = '2026-10-23';
const KEY_A = 'ab'.repeat(32);
const KEY_B = 'cd'.repeat(32);
const KEY_C = 'ef'.repeat(32);
const CHAINS = { ethereum: 'Ethereum', base: 'Base' };
const CLIENT = fs.readFileSync(path.join(__dirname, '..', 'public', 'js', 'scam-or-safe.js'), 'utf8');
const ui = require('../public/js/scam-or-safe.js');

function flag(on) {
  if (on) process.env.SCAM_OR_SAFE_V0 = '1';
  else delete process.env.SCAM_OR_SAFE_V0;
}

function good(over = {}) {
  return Object.assign({
    id: 'sos-zz',
    chain: 'ethereum',
    method: 'approve',
    verdict: 'risky',
    explanation: 'This request would grant an allowance the account has not used. The amount stays hidden and the spender stays masked.',
    fields: [
      { k: 'Chain', v: 'Ethereum' },
      { k: 'Amount', v: '\u2592\u2592\u2592' },
      { k: 'Spender', v: '0x12ab\u20269f' },
    ],
  }, over);
}

function letterId(i) {
  const alphabet = 'abcdefghijklmnopqrstuvwxyz';
  return 'sos-' + alphabet[Math.floor(i / 26)] + alphabet[i % 26];
}

describe('scam or safe daily', { concurrency: false }, () => {
  test('a full address, a non-blanked amount, a signing url, and a banned word each fail the audit', () => {
    assert.deepEqual(sos.problems(good(), CHAINS), []);
    const full = good({
      fields: [
        { k: 'Chain', v: 'Ethereum' },
        { k: 'Amount', v: '\u2592\u2592\u2592' },
        { k: 'Spender', v: '0x' + 'ab'.repeat(20) },
      ],
    });
    assert.ok(sos.problems(full, CHAINS).some((s) => s.includes('full address')));
    const amount = good({
      fields: [
        { k: 'Chain', v: 'Ethereum' },
        { k: 'Amount', v: '1000' },
        { k: 'Spender', v: '0x12ab\u20269f' },
      ],
    });
    assert.ok(sos.problems(amount, CHAINS).some((s) => s.includes('amount not blanked')));
    const dotted = good({
      explanation: 'See https://sign.example/approve before calling this. The second sentence stays plain.',
    });
    assert.ok(sos.problems(dotted, CHAINS).some((s) => s.includes('url')));
    for (const bit of ['www.evil.com', 'javascript:alert(1)', 'walletconnect', 'metamask', 'wc:pair', 'lookalike.io']) {
      const puzzle = good({
        explanation: `This request mentions ${bit} in the first sentence. The second sentence stays plain.`,
      });
      assert.ok(sos.problems(puzzle, CHAINS).some((s) => s.includes('url')), bit);
    }
    for (const word of ['token', 'tokens', 'presale', 'price', 'prices', '$RCLAW', 'stake', 'stakes', '$']) {
      const puzzle = good({
        explanation: `This request names ${word} in the first sentence. The second sentence stays plain.`,
      });
      assert.ok(sos.problems(puzzle, CHAINS).some((s) => s.includes('banned word')), word);
    }
  });

  test('mistake and entry_price are not banned words, and an ascii ellipsis is not a mask', () => {
    const mistake = good({
      explanation: 'A mistake is still the first sentence. The second sentence stays clean.',
    });
    assert.deepEqual(sos.problems(mistake, CHAINS), []);
    const named = good({
      fields: [
        { k: 'Chain', v: 'Ethereum' },
        { k: 'Amount', v: '[amount hidden]' },
        { k: 'entry_price', v: 'not a figure' },
      ],
    });
    assert.deepEqual(sos.problems(named, CHAINS), []);
    const ascii = good({
      fields: [
        { k: 'Chain', v: 'Ethereum' },
        { k: 'Amount', v: '\u2592\u2592\u2592' },
        { k: 'Spender', v: '0x12ab...9f' },
      ],
    });
    assert.ok(sos.problems(ascii, CHAINS).some((s) => s.includes('amount or address')));
    const hidden = good({
      fields: [
        { k: 'Chain', v: 'Ethereum' },
        { k: 'Amount', v: '[amount hidden]' },
        { k: 'Spender', v: '0x12ab\u20269f' },
      ],
    });
    assert.deepEqual(sos.problems(hidden, CHAINS), []);
    assert.ok(sos.problems(good({ method: 'eth_sign' }), CHAINS).some((s) => s.includes('method')));
    assert.deepEqual(sos.problems(good({ method: 'increaseAllowance' }), CHAINS), []);
    const short = Array.from({ length: 29 }, (_, i) => good({ id: letterId(i) }));
    assert.ok(sos.audit(short, { ethereum: 'Ethereum' }).some((s) => s.includes('at least 30')));
  });

  test('the committed catalog passes, has at least 30, both verdicts, and every wallet chain', () => {
    const list = sos.catalog();
    assert.ok(list.length >= 30);
    assert.deepEqual(sos.audit(list), []);
    const chains = sos.supportedChains();
    assert.ok(chains.includes('ethereum') && chains.includes('base') && chains.includes('bnb'));
    for (const key of chains) assert.ok(list.some((p) => p.chain === key), key);
    assert.ok(list.some((p) => p.verdict === 'safe'));
    assert.ok(list.some((p) => p.verdict === 'risky'));
    for (const method of ['approve', 'permit', 'transfer', 'setApprovalForAll']) {
      assert.ok(list.some((p) => p.method === method), method);
    }
    const ids = list.map((p) => p.id);
    assert.equal(new Set(ids).size, ids.length);
  });

  test('a poisoned field is blanked or withheld before it can be shown', () => {
    assert.deepEqual(sos.publicField({ k: 'Amount', v: '1000000' }), { k: 'Amount', v: '\u2592\u2592\u2592' });
    assert.deepEqual(
      sos.publicField({ k: 'Spender', v: '0x' + 'ab'.repeat(20) }),
      { k: 'Spender', v: '[withheld]' },
    );
    assert.deepEqual(
      sos.publicField({ k: 'Note', v: 'open https://sign.example/go' }),
      { k: 'Note', v: '[withheld]' },
    );
    assert.equal(sos.publicField({ k: 'Note', v: 'a $RCLAW line' }).v, '[withheld]');
    const shown = sos.publicPuzzle(sos.catalog()[0]);
    assert.equal(shown.verdict, undefined);
    assert.equal(shown.explanation, undefined);
    assert.equal(JSON.stringify(shown).includes('0x' + 'a'.repeat(40)), false);
  });

  test('the puzzle for a UTC day is the sha256 index, and the cutoff is the duel cutoff', () => {
    const list = sos.catalog();
    const buf = crypto.createHash('sha256').update('scam-or-safe:v0:' + DAY, 'utf8').digest();
    const index = buf.readUInt32BE(0) % list.length;
    assert.equal(sos.indexForDay(DAY, list.length), index);
    assert.equal(sos.puzzleForDay(DAY).id, list[index].id);
    assert.equal(sos.puzzleForDay(DAY).id, sos.puzzleForDay(DAY).id);
    assert.equal(sos.cutoffFor(DAY), gate.cutoffFor(DAY));
    assert.equal(sos.cutoffFor(DAY), '2026-10-24T00:00:00.000Z');
    const ids = new Set();
    for (let i = 0; i < 64; i += 1) {
      const day = new Date(Date.UTC(2026, 0, 1 + i)).toISOString().slice(0, 10);
      ids.add(sos.puzzleForDay(day).id);
    }
    assert.ok(ids.size > 1);
    assert.equal(sos.indexForDay(DAY, 0), null);
    assert.equal(sos.puzzleForDay('not-a-day'), null);
    assert.equal(sos.cutoffFor('not-a-day'), null);
  });

  test('zero right calls of a non-zero total is 0 percent, and an empty total is unreadable', () => {
    assert.equal(sos.pctOf(0, 0), null);
    assert.equal(sos.pctOf(0, 2), 0);
    assert.equal(sos.pctOf(1, 2), 50);
    assert.equal(ui.rightText({ pct: 0, total: 2, unreadable: 0 }), '0% of 2 players called it right.');
    assert.match(ui.rightText({ pct: null, total: 0 }), /could not be read/);
    assert.doesNotMatch(ui.rightText(null), /0%/);
    assert.match(ui.streakText({ current: null }), /could not be read/);
    assert.doesNotMatch(ui.streakText(null), /Streak: 0/);
  });

  test('a wrong call is a streak of zero, an unreadable day is not, and a missed day keeps yesterday', () => {
    const now = new Date(NOW);
    assert.equal(sos.streakFrom([{ day: DAY, state: 'wrong' }], now).current, 0);
    const unread = sos.streakFrom([{ day: DAY, state: 'unreadable' }], now);
    assert.equal(unread.current, null);
    assert.equal(unread.unreadable, true);
    const grace = sos.streakFrom([{ day: '2026-10-22', state: 'correct' }], now);
    assert.equal(grace.current, 1);
    assert.equal(grace.active_today, false);
    const run = sos.streakFrom([
      { day: '2026-10-20', state: 'correct' },
      { day: '2026-10-21', state: 'correct' },
      { day: '2026-10-22', state: 'wrong' },
      { day: DAY, state: 'correct' },
    ], now);
    assert.equal(run.current, 1);
    assert.equal(run.best, 2);
    assert.equal(run.active_today, true);
    const gap = sos.streakFrom([
      { day: '2026-10-21', state: 'correct' },
      { day: DAY, state: 'correct' },
    ], now);
    assert.equal(gap.current, 1);
    const y = sos.streakFrom([{ day: '2026-10-22', state: 'unreadable' }], now);
    assert.equal(y.current, null);
    assert.equal(y.unreadable, true);
  });

  test('the flag off seals nothing and does not open a store', () => {
    flag(false);
    for (const value of ['', '0', 'false', 'off', 'no', '2']) {
      assert.equal(sos.enabled({ SCAM_OR_SAFE_V0: value }), false, value);
    }
    for (const value of ['1', 'true', 'on', 'yes', ' YES ', 'On']) {
      assert.equal(sos.enabled({ SCAM_OR_SAFE_V0: value }), true, value);
    }
    const quiet = fs.mkdtempSync(path.join(os.tmpdir(), 'sos-off-'));
    const prev = process.env.SCAM_OR_SAFE_STATE_DIR;
    process.env.SCAM_OR_SAFE_STATE_DIR = quiet;
    try {
      const offer = sos.offer({ now: NOW, player_key: KEY_A });
      assert.equal(offer.ok, false);
      assert.equal(offer.error, 'off');
      const sealed = sos.sealPick({ now: NOW, pick: 'safe', player_key: KEY_A });
      assert.equal(sealed.error, 'off');
      assert.equal(fs.existsSync(path.join(quiet, 'picks.sqlite')), false);
    } finally {
      process.env.SCAM_OR_SAFE_STATE_DIR = prev;
      flag(false);
    }
  });

  test('the direction card does not deal scam or safe', () => {
    assert.equal(duel.KINDS.scam_or_safe, undefined);
    assert.equal(duel.KIND_NAMES.includes('scam_or_safe'), false);
    assert.equal(duel.normPick('safe', 'direction'), null);
    assert.equal(duel.normPick('risky', 'direction'), null);
    const src = fs.readFileSync(path.join(__dirname, '..', 'lib', 'duel.js'), 'utf8');
    assert.equal(src.includes('scam_or_safe'), false);
  });

  test('the seal is the duel pick seal, entry prices are null, and a second pick is the first', () => {
    flag(true);
    const preview = sos.offer({ now: NOW });
    assert.equal(preview.ok, true);
    assert.equal(preview.sealed, false);
    assert.equal(Object.prototype.hasOwnProperty.call(preview, 'verdict'), false);
    assert.equal(Object.prototype.hasOwnProperty.call(preview, 'explanation'), false);
    assert.equal(JSON.stringify(preview).includes(sos.puzzleForDay(DAY).explanation), false);
    const out = sos.sealPick({ now: NOW, pick: 'safe', player_key: KEY_A, puzzle_id: preview.puzzle.id });
    assert.equal(out.ok, true);
    assert.equal(out.verified, true);
    assert.equal(out.pick, 'safe');
    assert.equal(out.ai_label, sos.AI_LABEL);
    assert.equal(out.ai_note, sos.AI_NOTE);
    assert.equal(out.explanation, sos.puzzleForDay(DAY).explanation);
    assert.equal(out.correct, out.verdict === 'safe');
    const body = JSON.parse(out.seal_payload);
    assert.equal(body.kind, 'duel_pick');
    assert.equal(body.category, 'scam_or_safe');
    assert.equal(body.pick, 'safe');
    assert.equal(body.symbol, preview.puzzle.id);
    assert.equal(body.day, DAY);
    assert.equal(body.entry_price, null);
    assert.equal(body.entry_price_b, null);
    assert.equal(body.round_id, null);
    assert.equal(body.handle, null);
    assert.equal(out.seal, sos.hashOf(out.seal_payload));
    assert.ok(sos.verifySeal(out.seal, out.seal_payload));
    assert.equal(out.seal_payload.includes('$'), false);
    assert.equal(out.seal_payload.includes(KEY_A), false);
    assert.equal(out.seal_payload.includes('player_key'), false);
    const tampered = out.seal_payload.replace('"pick":"safe"', '"pick":"risky"');
    assert.equal(sos.verifySeal(out.seal, tampered), null);
    const again = sos.sealPick({
      now: NOW, pick: 'risky', player_key: KEY_A, puzzle_id: preview.puzzle.id,
    });
    assert.equal(again.pick, 'safe');
    assert.equal(again.seal, out.seal);
    const moved = sos.sealPick({ now: NOW, pick: 'safe', player_key: KEY_B, puzzle_id: 'sos-zz' });
    assert.equal(moved.ok, false);
    assert.equal(moved.error, 'moved');
    assert.equal(moved.puzzle_id, preview.puzzle.id);
    const closed = sos.sealPick({ now: NOW, pick: 'safe', player_key: KEY_B, day: '2020-01-01' });
    assert.equal(closed.error, 'closed');
    const rightPick = sos.puzzleForDay(DAY).verdict;
    const wrongPick = rightPick === 'safe' ? 'risky' : 'safe';
    const right = sos.sealPick({ now: NOW, pick: rightPick, player_key: KEY_B, puzzle_id: preview.puzzle.id });
    const wrong = sos.sealPick({ now: NOW, pick: wrongPick, player_key: KEY_C, puzzle_id: preview.puzzle.id });
    assert.equal(right.correct, true);
    assert.equal(right.streak.current, 1);
    assert.equal(wrong.correct, false);
    assert.equal(wrong.streak.current, 0);
    const aCorrect = sos.puzzleForDay(DAY).verdict === 'safe';
    const correct = (aCorrect ? 1 : 0) + 1;
    assert.equal(right.rank.place, 1);
    assert.equal(right.rank.players, 2);
    assert.equal(wrong.rank.place, aCorrect ? 3 : 2);
    assert.equal(wrong.rank.players, 3);
    assert.equal(wrong.right.total, 3);
    assert.equal(wrong.right.correct, correct);
    assert.equal(wrong.right.pct, sos.pctOf(correct, 3));
  });

  test('a verified reveal names the AI label, and the note is the coach disclosure', () => {
    const coach = fs.readFileSync(path.join(__dirname, '..', 'public', 'js', 'pi-coach.js'), 'utf8');
    const quoted = "Education and practice, not advice. Your question and a summary";
    assert.ok(coach.includes(quoted));
    assert.ok(quoted.startsWith(sos.AI_NOTE));
    assert.equal(sos.AI_LABEL, 'AI-written explanation');
    flag(true);
    const row = sos.offer({ now: NOW, player_key: KEY_A });
    assert.equal(row.ai_label, sos.AI_LABEL);
    assert.ok(row.explanation);
    const html = ui.render({
      phase: 'reveal',
      puzzle: row.puzzle,
      pick: row.pick,
      verdict: row.verdict,
      correct: row.correct,
      explanation: row.explanation,
      ai_label: null,
      ai_note: row.ai_note,
      right: row.right,
      streak: row.streak,
      rank: row.rank,
    });
    assert.equal(html.includes(row.explanation), false);
    assert.match(html, /withheld until it carries its AI label/);
    const shown = ui.render({
      phase: 'reveal',
      puzzle: row.puzzle,
      pick: row.pick,
      verdict: row.verdict,
      correct: row.correct,
      explanation: row.explanation,
      ai_label: row.ai_label,
      ai_note: row.ai_note,
      right: row.right,
      streak: row.streak,
      rank: row.rank,
    });
    assert.ok(shown.includes(sos.AI_LABEL));
    assert.ok(shown.includes(row.explanation));
    assert.equal(shown.includes('tone-up'), false);
    assert.equal(shown.includes('color:green'), false);
  });

  test('a seal that does not verify withholds the verdict and the explanation', () => {
    flag(true);
    const { DatabaseSync } = require('node:sqlite');
    const db = new DatabaseSync(path.join(STATE, 'picks.sqlite'));
    db.prepare('UPDATE picks SET seal = ? WHERE player_key = ?').run('00'.repeat(32), KEY_A);
    const row = sos.offer({ now: NOW, player_key: KEY_A });
    assert.equal(row.verified, false);
    assert.equal(row.verdict, null);
    assert.equal(row.explanation, null);
    assert.equal(row.ai_label, null);
    assert.equal(row.correct, null);
    assert.equal(row.streak.current, null);
    assert.equal(row.streak.unreadable, true);
    assert.equal(row.rank.place, null);
    const other = sos.offer({ now: NOW, player_key: KEY_B });
    assert.equal(other.verified, true);
    assert.equal(other.right.unreadable, 1);
    assert.equal(other.right.total, 2);
    assert.equal(other.right.correct, 1);
    assert.equal(other.right.pct, 50);
  });

  test('the pick screen has no link, no verdict, and copying a mask yields nothing', () => {
    const html = ui.render({
      phase: 'pick',
      puzzle: {
        chain_label: 'Ethereum',
        method: 'approve',
        fields: [
          { k: 'Spender', v: '0x12ab\u20269f' },
          { k: 'Amount', v: '\u2592\u2592\u2592' },
        ],
      },
    });
    assert.match(html, /data-sos="safe"/);
    assert.match(html, /data-sos="risky"/);
    assert.match(html, /user-select:\s*none/);
    assert.equal(html.includes('href'), false);
    assert.equal(html.includes('Verdict'), false);
    assert.equal(/0x[a-fA-F0-9]{40}/.test(html), false);
    assert.match(html, /sos-mask/);
    assert.match(html, /sos-amt/);
    const el = { closest: () => el };
    const ev = {
      target: el,
      prevented: false,
      preventDefault() { this.prevented = true; },
      clipboardData: { text: 'full', setData(type, value) { this.text = value; this.type = type; } },
    };
    assert.equal(ui.guardCopy(ev), true);
    assert.equal(ev.prevented, true);
    assert.equal(ev.clipboardData.text, '');
    const select = { target: el, prevented: false, preventDefault() { this.prevented = true; } };
    assert.equal(ui.guardSelect(select), true);
    assert.equal(select.prevented, true);
    const outside = { target: { closest: () => null }, prevented: false, preventDefault() { this.prevented = true; } };
    assert.equal(ui.guardCopy(outside), false);
    assert.equal(outside.prevented, false);
    const src = codeOnly(CLIENT);
    assert.doesNotMatch(src, /presale|\$?RCLAW|smithii|\/token\b/i);
    assert.doesNotMatch(src, /0x[a-fA-F0-9]{40}/);
    assert.doesNotMatch(src, /href=|walletconnect|eth_sign|\bweb3\b/i);
    assert.equal(src.includes('scam_or_safe'), false);
  });

  test('a pick posts the puzzle id captured before the loading screen', async () => {
    const block = ui.create();
    let posted = null;
    const puzzle = {
      id: 'sos-aa',
      chain_label: 'Ethereum',
      method: 'approve',
      fields: [{ k: 'Amount', v: '\u2592\u2592\u2592' }],
    };
    block.signedIn({
      render() {},
      api(url, opts) {
        if (String(url).endsWith('/today')) {
          return Promise.resolve({ status: 200, body: { ok: true, sealed: false, puzzle } });
        }
        posted = opts && opts.body;
        return Promise.resolve({
          status: 200,
          body: {
            ok: true, sealed: true, puzzle, pick: 'risky', verdict: 'risky', correct: true,
            explanation: 'First sentence here. Second sentence here.',
            ai_label: sos.AI_LABEL, ai_note: sos.AI_NOTE,
            right: { pct: 0, total: 1, unreadable: 0 },
            streak: { current: 0, best: 0 }, rank: { place: 1, players: 1 },
          },
        });
      },
    });
    await new Promise((resolve) => setImmediate(resolve));
    assert.equal(block.badge(), 1);
    const btn = { getAttribute: () => 'risky', closest: () => btn };
    block.click({ closest: () => btn });
    await new Promise((resolve) => setImmediate(resolve));
    assert.deepEqual(posted, { pick: 'risky', puzzle_id: 'sos-aa' });
    assert.equal(block.badge(), 0);
  });

  describe('the http doors', () => {
    let server;
    let base;
    const tokens = [];
    const emails = ['sos0@example.com', 'sos1@example.com'];

    function req(method, reqPath, { token, body } = {}) {
      return new Promise((resolve, reject) => {
        const data = body === undefined ? null : JSON.stringify(body);
        const r = http.request(`${base}${reqPath}`, {
          method,
          headers: {
            ...(token ? { Authorization: `Bearer ${token}` } : {}),
            ...(data ? { 'Content-Type': 'application/json', 'Content-Length': Buffer.byteLength(data) } : {}),
          },
        }, (res) => {
          const chunks = [];
          res.on('data', (c) => chunks.push(c));
          res.on('end', () => {
            const raw = Buffer.concat(chunks).toString('utf8');
            let json = null;
            try { json = raw ? JSON.parse(raw) : null; } catch (e) { json = null; }
            resolve({ status: res.statusCode, body: json, raw, headers: res.headers });
          });
        });
        r.on('error', reject);
        if (data) r.write(data);
        r.end();
      });
    }

    before(async () => {
      flag(true);
      const authModule = require('../auth');
      const app = express();
      app.use(express.json());
      app.use('/api/auth', authModule.router);
      app.use('/api/scam-or-safe', require('../routes/scam_or_safe'));
      app.use('/scam', require('../routes/scam_page'));
      await new Promise((resolve) => {
        server = app.listen(0, '127.0.0.1', () => {
          base = `http://127.0.0.1:${server.address().port}`;
          resolve();
        });
      });
      for (let i = 0; i < emails.length; i += 1) {
        const res = await req('POST', '/api/auth/register', {
          body: { email: emails[i], password: 'longenough1' },
        });
        assert.ok(res.body && res.body.token, res.raw);
        tokens.push(res.body.token);
      }
    });

    after(async () => {
      flag(false);
      if (server) await new Promise((resolve) => server.close(resolve));
    });

    test('the authed route 404s while the flag is off, and a sealed call cannot be replaced', async () => {
      flag(false);
      const closed = await req('GET', '/api/scam-or-safe/today', { token: tokens[0] });
      assert.equal(closed.status, 404);
      assert.equal(closed.body.error, 'This game is not open.');
      const pageOff = await req('GET', '/scam');
      assert.equal(pageOff.status, 404);
      assert.match(pageOff.raw, /not open/);
      assert.equal(pageOff.raw.includes('scam-or-safe.js'), false);
      flag(true);
      const anon = await req('GET', '/api/scam-or-safe/today');
      assert.equal(anon.status, 401);
      const today = await req('GET', '/api/scam-or-safe/today', { token: tokens[0] });
      assert.equal(today.status, 200);
      assert.equal(today.headers['cache-control'], 'no-store');
      assert.equal(today.body.sealed, false);
      assert.equal(today.body.verdict, undefined);
      assert.equal(today.body.explanation, undefined);
      assert.equal(today.raw.includes('$'), false);
      assert.doesNotMatch(today.raw, /rclaw|presale|\btoken\b|\bstake\b/i);
      assert.equal(today.raw.includes(emails[0]), false);
      const bad = await req('POST', '/api/scam-or-safe/pick', {
        token: tokens[0], body: { pick: 'sideways', puzzle_id: today.body.puzzle.id },
      });
      assert.equal(bad.status, 400);
      const forged = '12'.repeat(32);
      const first = await req('POST', '/api/scam-or-safe/pick', {
        token: tokens[0],
        body: { pick: 'safe', puzzle_id: today.body.puzzle.id, player_key: forged },
      });
      assert.equal(first.status, 200);
      assert.equal(first.body.pick, 'safe');
      assert.equal(first.body.verified, true);
      assert.equal(first.body.ai_label, sos.AI_LABEL);
      assert.equal(first.headers['cache-control'], 'no-store');
      assert.equal(first.raw.includes(emails[0]), false);
      assert.equal(first.raw.includes(forged), false);
      const body = JSON.parse(first.body.seal_payload);
      assert.equal(body.entry_price, null);
      assert.equal(body.category, 'scam_or_safe');
      assert.equal(body.kind, 'duel_pick');
      const second = await req('POST', '/api/scam-or-safe/pick', {
        token: tokens[0], body: { pick: 'risky', puzzle_id: today.body.puzzle.id },
      });
      assert.equal(second.body.pick, 'safe');
      assert.equal(second.body.seal, first.body.seal);
      const other = await req('GET', '/api/scam-or-safe/today', { token: tokens[1] });
      assert.equal(other.body.sealed, false);
      const stranger = sos.offer({ now: new Date().toISOString(), player_key: 'w:' + forged });
      assert.equal(stranger.sealed, false);
      const moved = await req('POST', '/api/scam-or-safe/pick', {
        token: tokens[1], body: { pick: 'risky', puzzle_id: 'sos-zz' },
      });
      assert.equal(moved.status, 409);
      assert.equal(moved.body.error, 'moved');
      assert.equal(moved.body.puzzle_id, today.body.puzzle.id);
      const page = await req('GET', '/scam');
      assert.equal(page.status, 200);
      assert.equal(page.headers['cache-control'], 'no-store');
      assert.match(page.raw, /id="scam-root"/);
      assert.match(page.raw, /\/js\/scam-or-safe\.js\?v=1/);
      assert.doesNotMatch(page.raw, /<script(?![^>]*\bsrc=)/);
      assert.doesNotMatch(page.raw, /0x[a-fA-F0-9]{40}/);
    });
  });

  test('the page and the Pi tab stay out of the document while the flag is off', () => {
    const pi = require('../routes/piapp');
    assert.equal(pi.SCRIPTS.some((s) => s.includes('scam-or-safe')), false);
    flag(false);
    const off = pi._page('en', {});
    assert.equal(off.includes('scam-or-safe.js'), false);
    assert.match(off, /\/js\/pi-arena\.js\?v=8/);
    flag(true);
    const on = pi._page('en', {});
    const atScam = on.indexOf('/js/scam-or-safe.js?v=1');
    const atArena = on.indexOf('/js/pi-arena.js?v=8');
    assert.ok(atScam > 0 && atArena > atScam);
    flag(false);
    const arena = fs.readFileSync(path.join(__dirname, '..', 'public', 'js', 'pi-arena.js'), 'utf8');
    assert.match(arena, /window\.RCScamOrSafe/);
    assert.match(arena, /blocks\.splice\(1, 0,/);
  });
});
