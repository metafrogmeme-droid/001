'use strict';
/**
 * The Pi app says the server's own sentences in the language on screen.
 *
 * The Pi plan (docs/hackathon/PI_HACKATHON_PLAN.md) listed what was still
 * English in every language: the sentences the server sends back when it
 * refuses something (a name already taken, a round already called) and the
 * coach's four fixed answers (a token question, no data feed, a withheld
 * reply, coach unavailable). A reader who chose Türkçe got the refusal in
 * English, at the moment they most needed to understand it.
 *
 * The English stays where it is said: the dictionary builder reads the
 * literal refusals out of the Duel and Leaders code and the four answers out
 * of bot/web/pi_coach.py (build_pi_i18n.js SERVER_SOURCES), so a sentence
 * the server stops sending leaves the table and one it starts sending is
 * missing in every language until it is translated. The Pi blocks pass what
 * they were sent through T: a sentence the table knows is translated, any
 * other prints as sent.
 */

const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const { execFileSync } = require('node:child_process');

const build = require('../scripts/build_pi_i18n.js');

const ROOT = path.join(__dirname, '..', '..');
const PI_COACH_PY = path.join(ROOT, 'bot', 'web', 'pi_coach.py');
const GATEWAY_PY = path.join(ROOT, 'bot', 'web', 'user_gateway.py');

/** The coach's `*_ANSWER` constants, evaluated by Python's own parser (no bot import). */
function pythonAnswers() {
  const code = [
    'import ast, json, sys',
    'src = open(sys.argv[1], encoding="utf-8").read()',
    'out = {}',
    'for n in ast.parse(src).body:',
    '    if isinstance(n, ast.Assign) and len(n.targets) == 1 and getattr(n.targets[0], "id", "").endswith("_ANSWER"):',
    '        out[n.targets[0].id] = ast.literal_eval(n.value)',
    'print(json.dumps(out))',
  ].join('\n');
  return JSON.parse(execFileSync('python3', ['-c', code, PI_COACH_PY], { encoding: 'utf8' }));
}

/** A fresh Pi i18n with one language's built chunk loaded, as the page would have it. */
function piI18n(code) {
  delete require.cache[require.resolve('../public/js/pi-i18n.js')];
  const I = require('../public/js/pi-i18n.js');
  global.window = { RCPiI18n: I };
  const chunk = fs.readFileSync(path.join(__dirname, '..', 'public', 'js', 'pi-i18n', `${code}.js`), 'utf8');
  new Function(chunk)();
  assert.strictEqual(I.use(code), code);
  return I;
}

const settle = () => new Promise((r) => setTimeout(r, 30));

test('the coach\'s four fixed answers are read from the bot, word for word', () => {
  const py = pythonAnswers();
  assert.deepStrictEqual(Object.keys(py).sort(), [...build.PY_ANSWERS].sort(),
    'an answer was added to pi_coach.py and not to PY_ANSWERS, or one listed there is gone');
  const read = build.serverSentences().filter(([, file]) => file.endsWith('pi_coach.py')).map(([en]) => en);
  assert.deepStrictEqual(read, build.PY_ANSWERS.map((n) => py[n]));
});

test('every literal refusal of the Duel call and the Leaders name is collected, and only those', () => {
  const read = new Set(build.serverSentences().map(([en]) => en));
  for (const rel of ['lib/duel_service.js', 'routes/leaderboard.js']) {
    const src = fs.readFileSync(path.join(__dirname, '..', rel), 'utf8');
    for (const m of src.matchAll(/error: '((?:[^'\\\n]|\\.)*)'/g)) {
      assert.ok(read.has(m[1].replace(/\\'/g, "'")), `${rel}: ${m[1]} is not in the table`);
    }
  }
  assert.ok(read.has('That handle is taken — try another.') && read.has('You have already called this round.'));
  // A template is not a literal: its words change with each answer, so it is not collected.
  assert.ok(![...read].some((s) => s.includes('${')), 'a template literal was collected as a sentence');
});

test('every server sentence is translated in every language the Pi app speaks', () => {
  const out = build.build();
  assert.deepStrictEqual(out.problems, []);
  const server = [...new Set(build.serverSentences().map(([en]) => en))];
  // Eighteen, plus the confidence refusal ("Choose low, medium or high…").
  assert.strictEqual(server.length, 19);
  for (const [code, missing] of Object.entries(out.missing)) {
    for (const en of server) assert.ok(!missing.includes(en), `${code} lacks: ${en}`);
  }
  for (const code of ['de', 'tr', 'vi', 'ar']) {
    const I = piI18n(code);
    for (const en of server) assert.notStrictEqual(I.t(en), en, `${code}: ${en}`);
  }
  delete global.window;
});

test('the intents the Pi app draws as fixed answers are the gateway\'s, both ways', () => {
  const src = fs.readFileSync(GATEWAY_PY, 'utf8');
  const pairs = [...src.matchAll(/"reply_html": pi_coach\.(\w+_ANSWER),\s*"intent": "(pi_coach_\w+)"/g)];
  const byIntent = Object.fromEntries(pairs.map((m) => [m[2], m[1]]));
  const C = require('../public/js/pi-coach.js');
  assert.deepStrictEqual(Object.keys(byIntent).sort(), [...C.FIXED_INTENTS].sort());
  assert.deepStrictEqual(Object.values(byIntent).sort(), [...build.PY_ANSWERS].sort());
  // The model's own answer is not one of them: translating it would replace a reply with nothing.
  assert.ok(!C.FIXED_INTENTS.includes('pi_coach'));
});

test('a fixed answer arrives in the reader\'s language; the model\'s answer is drawn as sent', async () => {
  const py = pythonAnswers();
  const I = piI18n('de');
  try {
    delete require.cache[require.resolve('../public/js/pi-coach.js')];
    const C = require('../public/js/pi-coach.js');
    const replies = [
      { reply_html: py.TOKEN_ANSWER, intent: 'pi_coach_token' },
      { reply_html: '<b>Ein Stop</b> begrenzt den Verlust.', intent: 'pi_coach' },
      { reply_html: 'A sentence the table has never seen.', intent: 'pi_coach_unavailable' },
    ];
    const block = C.create();
    const ctx = {
      api: (p) => Promise.resolve(p === '/api/pi/coach' && replies.length
        ? { ok: true, status: 200, body: { ...replies.shift(), limit: 3, left: 2 } }
        : { ok: false, status: 404, body: null }),
      render() {},
    };
    for (const q of ['token?', 'stop?', 'x?']) { block.ask(q, null, ctx); await settle(); }
    const html = block.html();
    assert.ok(html.includes(I.t(py.TOKEN_ANSWER)), 'the token answer in German');
    assert.ok(!html.includes(py.TOKEN_ANSWER), 'the English token answer still on screen');
    assert.ok(html.includes('<b>Ein Stop</b> begrenzt den Verlust.'), 'the model\'s answer, emphasis kept');
    assert.ok(html.includes('A sentence the table has never seen.'), 'an unknown fixed answer prints as sent');
  } finally {
    delete global.window;
  }
});

test('a Duel refusal arrives in the reader\'s language, and one the table lacks prints as sent', async () => {
  const I = piI18n('tr');
  global.window.RCDuel = require('../public/js/duel.js');
  global.window.RCMiniView = require('../public/js/miniapp-view.js');
  global.document = { hidden: true, addEventListener() {} };
  try {
    delete require.cache[require.resolve('../public/js/pi-duel.js')];
    const { create } = require('../public/js/pi-duel.js');
    const card = { day: '2026-10-22', horizon_hours: 24, open: 1, rounds: [
      { id: 1, kind: 'direction', symbol: 'BTCUSDT', options: ['long', 'short', 'pass'], callable: true }] };
    for (const [error, want] of [
      ['You have already called this round.', I.t('You have already called this round.')],
      ['Cannot read a BTCUSDT price right now — your call was not recorded.', 'Cannot read a BTCUSDT price right now — your call was not recorded.'],
    ]) {
      const block = create();
      const ctx = {
        api(p) {
          if (p === '/api/duel/today') return Promise.resolve({ ok: true, status: 200, body: card });
          if (p === '/api/duel/me') return Promise.resolve({ ok: true, status: 200, body: {} });
          if (p === '/api/duel/pick') return Promise.resolve({ ok: false, status: 409, body: { error } });
          return Promise.resolve({ ok: false, status: 404, body: null });
        },
        render() {}, refresh() { return Promise.resolve(); },
      };
      block.signedIn(ctx);
      await settle();
      const wrap = { getAttribute: () => '1', querySelector: () => null };
      const btn = { closest: () => wrap, getAttribute: () => 'long' };
      block.click({ closest: (sel) => (sel.indexOf('d-btn') >= 0 ? btn : null) }, ctx);
      await settle();
      const html = block.html();
      assert.ok(html.includes(want.replace(/'/g, '&#39;')) || html.includes(want), `${error} → ${want}`);
    }
    assert.notStrictEqual(I.t('You have already called this round.'), 'You have already called this round.');
  } finally {
    delete global.window;
    delete global.document;
  }
});
