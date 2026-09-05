'use strict';
/**
 * ask_runeclaw — the public website chat, as an MCP tool.
 *
 * Every other tool on POST /mcp answers "what has RUNECLAW done?" or "is what
 * my agent is about to do safe?". None let an agent ASK the agent anything.
 * The website's anonymous chat already does exactly that, account-free by
 * construction (bot/web/user_gateway.py `_public_chat_turn`), so the tool is
 * that chat on a different wire — same gateway route, same bounds, same rate.
 *
 * The properties pinned here are the ones a consumer would otherwise learn the
 * hard way: the answer arrives as text an agent can read (the bot speaks
 * Telegram HTML), a dead gateway is `available: false` and never an empty
 * answer, a scan-shaped question comes back with the intent that says it was
 * gated, and six calls a minute per caller is the website's ceiling, not the
 * router's sixty.
 */
process.env.JWT_SECRET = process.env.JWT_SECRET || 'j'.repeat(64);
process.env.WEB_GATEWAY_SECRET = 'g'.repeat(64);      // isConfigured() needs >= 32
delete process.env.DATABASE_URL;

const test = require('node:test');
const assert = require('node:assert');
const http = require('node:http');

const seen = [];
let mockGateway;
function startMockGateway() {
  return new Promise((resolve) => {
    mockGateway = http.createServer((req, res) => {
      let data = '';
      req.on('data', (d) => { data += d; });
      req.on('end', () => {
        const body = data ? JSON.parse(data) : {};
        seen.push({ url: req.url, body, secret: req.headers['x-gateway-secret'] });
        res.setHeader('Content-Type', 'application/json');
        if (req.url !== '/gateway/chat/public') { res.statusCode = 404; return res.end('{}'); }
        if (body.text === 'down') { res.statusCode = 503; return res.end(JSON.stringify({ error: 'no model' })); }
        if (/scan/i.test(body.text)) {
          return res.end(JSON.stringify({ reply_html: 'Sign in for a live scan.', intent: 'public_scan_gate' }));
        }
        res.end(JSON.stringify({
          reply_html: '<b>Risk first.</b><br>A sweep &amp; a reclaim; never a verdict &lt;3',
          intent: 'chat',
        }));
      });
    });
    mockGateway.listen(0, '127.0.0.1', () => resolve(mockGateway.address().port));
  });
}

let mcp;
test.before(async () => {
  const port = await startMockGateway();
  // Read once when lib/gateway.js loads, so it is set before the first require.
  process.env.BOT_GATEWAY_URL = `http://127.0.0.1:${port}`;
  mcp = require('../routes/mcp');
});
test.after(() => { if (mockGateway) mockGateway.close(); });

const call = (args, ctx) => mcp.handleRpc({ jsonrpc: '2.0', id: 1, method: 'tools/call',
  params: { name: 'ask_runeclaw', arguments: args } }, ctx || { ip: '10.0.0.1' });
const payload = (r) => JSON.parse(r.result.content[0].text);

test('it is listed as a read-only tool of the published-data family', async () => {
  const r = await mcp.handleRpc({ jsonrpc: '2.0', id: 1, method: 'tools/list' }, {});
  const t = r.result.tools.find((x) => x.name === 'ask_runeclaw');
  assert.ok(t, 'ask_runeclaw is not listed');
  assert.equal(t.annotations.readOnlyHint, true);
  assert.deepEqual(t.inputSchema.required, ['question']);
  assert.ok(!mcp.TOOLS.ask_runeclaw.computesOnInput, 'it serves the public chat, it is not a Guardian model');
  assert.ok(!mcp.TOOLS.ask_runeclaw.requiresKey, 'anonymous, like the website chat');
  assert.match(t.description, /account-free/i);
  assert.match(t.description, /public_scan_gate/);
  assert.match(t.description, /not always an answer/i);
});

test('the answer is the public chat reply, as text and as the HTML it came in', async () => {
  seen.length = 0;
  const out = payload(await call({ question: '  what is a liquidity sweep?  ', lang: 'es' }));
  assert.equal(out.available, true);
  assert.equal(out.answer, 'Risk first.\nA sweep & a reclaim; never a verdict <3');
  assert.equal(out.reply_html, '<b>Risk first.</b><br>A sweep &amp; a reclaim; never a verdict &lt;3');
  assert.equal(out.intent, 'chat');
  assert.equal(out.lang, 'es');
  assert.match(out.note, /no memory between calls/);
  const gw = seen.find((s) => s.url === '/gateway/chat/public');
  assert.ok(gw, 'the gateway public chat route was asked');
  assert.deepEqual(gw.body, { text: 'what is a liquidity sweep?', lang: 'es' });
  assert.equal(gw.secret, 'g'.repeat(64), 'the shared secret rides the call');
});

test('a scan-shaped question comes back gated, with the intent that says so', async () => {
  const out = payload(await call({ question: 'scan BTC' }, { ip: '10.0.0.2' }));
  assert.equal(out.available, true);
  assert.equal(out.intent, 'public_scan_gate');
  assert.equal(out.answer, 'Sign in for a live scan.');
});

test('a dead gateway is unavailable, never an empty answer', async () => {
  const out = payload(await call({ question: 'down' }, { ip: '10.0.0.3' }));
  assert.deepEqual(out, { available: false, error: 'unavailable' });
  assert.ok(!('answer' in out));
});

test('a malformed language tag is dropped, never forwarded', async () => {
  seen.length = 0;
  const out = payload(await call({ question: 'hello', lang: 'x!; drop' }, { ip: '10.0.0.4' }));
  assert.equal(out.available, true);
  assert.ok(!('lang' in out));
  assert.deepEqual(seen[0].body, { text: 'hello' });
});

test('the schema is enforced before the handler: missing, wrong type, too long, unknown', async () => {
  let r = await call({});
  assert.equal(r.error.code, -32602);
  r = await call({ question: 5 });
  assert.equal(r.error.code, -32602);
  r = await call({ question: 'x'.repeat(2001) });
  assert.equal(r.error.code, -32602);
  assert.match(r.error.message, /2000 max/);
  r = await call({ question: 'hi', extra: 1 });
  assert.equal(r.error.code, -32602);
});

test('a declared maxLength is the cap; an undeclared string keeps the old 200', () => {
  const { validateArgs } = mcp;
  assert.equal(validateArgs({ type: 'object', properties: { q: { type: 'string', maxLength: 2000 } } },
    { q: 'x'.repeat(1500) }), null);
  assert.match(validateArgs({ type: 'object', properties: { q: { type: 'string', maxLength: 10 } } },
    { q: 'x'.repeat(11) }), /10 max/);
  assert.match(validateArgs({ type: 'object', properties: { q: { type: 'string' } } },
    { q: 'x'.repeat(201) }), /200 max/);
});

test('the direct invoker still refuses an empty question without a gateway round-trip', async () => {
  seen.length = 0;
  assert.deepEqual(await mcp.TOOLS.ask_runeclaw.handler({}), { error: 'question is required' });
  assert.equal(seen.length, 0);
});

test('six a minute per caller, like the website public chat, and callers do not share a bucket', async () => {
  const ctx = { ip: '10.9.9.9' };
  for (let i = 0; i < 6; i++) {
    const out = payload(await call({ question: `q${i}` }, ctx));
    assert.equal(out.available, true, `call ${i + 1} should be allowed`);
  }
  const out = payload(await call({ question: 'q7' }, ctx));
  assert.equal(out.available, false);
  assert.equal(out.error, 'rate_limited');
  assert.equal(out.retry_after_seconds, 60);
  assert.equal(payload(await call({ question: 'q' }, { ip: '10.9.9.10' })).available, true);
});

test('the POST handler hands the tool the caller address and nothing else identifies them', () => {
  const fs = require('node:fs');
  const src = fs.readFileSync(require.resolve('../routes/mcp'), 'utf8');
  const i = src.indexOf("router.post('/'");
  assert.ok(i > 0);
  assert.match(src.slice(i, i + 1500), /ip:\s*ipKey\(req\)/,
    'handleRpc is called without the caller address, so the per-caller window keys everyone as "unknown"');
});
