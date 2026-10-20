'use strict';
/**
 * Flovia — the one client for the read-only data sources the product rents
 * through Flovia's MCP server (Nansen, CoinGecko, QuickNode, Exa, Firecrawl,
 * Zerion). https://api.flovia.dev/mcp, a bearer key that starts with `flv_`.
 *
 * WHAT THIS IS FOR, AND WHAT IT IS NOT
 *
 * Flovia reads data. Nothing on the trading path depends on it: the engine,
 * the risk gate and the duel's own settlement keep their venue feeds, and the
 * readers built on this client (smart-money context, a second settlement
 * source, an anchor fallback, a pool panel) add a line or a cross-check that
 * says "not read" when this answers nothing. The service is in alpha and its
 * free allowance is shared across every source in five-hour and weekly
 * windows, so every caller is written for the day the window is spent.
 *
 * THREE ANSWERS, NEVER TWO
 *
 * `call` resolves to `{ read: true, data }` or `{ read: false, reason }` and
 * never throws: `unconfigured` (no key: this deployment does not use Flovia),
 * `unreachable` (network, timeout, a 5xx), `refused` (Flovia or the provider
 * declined: the allowance, a bad argument, a provider outage; `detail` is
 * their short explanation), `malformed` (an answer this client could not
 * read). A caller that collapsed any of these into an empty result would
 * print "no smart money activity" over a window that was merely spent.
 *
 * ONE KEY, ONE PROCESS
 *
 * The key is read per call from FLOVIA_KEY (a vault restore takes effect
 * without a restart) and never logged; an error text that reaches a log or a
 * person names a class or Flovia's own short explanation, never the request.
 * The MCP session (`Mcp-Session-Id`) is kept per process and re-opened once
 * when the server forgets it; a second instance opens its own.
 *
 * THE WIRE
 *
 * MCP's Streamable HTTP transport: JSON-RPC 2.0 over POST, `initialize`
 * once, `notifications/initialized`, then `tools/call`. A reply is a JSON
 * body or a text/event-stream whose `data:` lines carry the JSON-RPC
 * messages; both are read here. A tool's answer is `result.content[0].text`,
 * JSON when the tool speaks JSON, and `isError` marks a refusal.
 */

const DEFAULT_URL = 'https://api.flovia.dev';
const PROTOCOL = '2025-06-18';
const CLIENT = { name: 'runeclaw-web', version: '1' };
const DEFAULT_TIMEOUT_MS = 12000;
const DETAIL_MAX = 240;

let fetchImpl = null;
let session = null;          // { id, url } once initialised
let initPromise = null;      // in-flight initialise, shared
let nextId = 1;

/** Injectable transport (tests). Null restores global fetch. */
function setFetch(fn) { fetchImpl = fn || null; session = null; initPromise = null; }
/** Forget the session (tests, or after the server lost it). */
function resetSession() { session = null; initPromise = null; }

function key() { return String(process.env.FLOVIA_KEY || '').trim(); }
function baseUrl() { return String(process.env.FLOVIA_URL || DEFAULT_URL).replace(/\/+$/, ''); }
function configured() { return key().length > 0; }

function notRead(reason, detail) {
  const out = { read: false, reason };
  if (detail) out.detail = String(detail).slice(0, DETAIL_MAX);
  return out;
}

/** The JSON-RPC message with this id, from a JSON body or an SSE body. */
function parseReply(text, contentType, id) {
  const t = String(text || '');
  if (/text\/event-stream/i.test(String(contentType || ''))) {
    let found = null;
    for (const block of t.split(/\n\n+/)) {
      const data = block.split('\n').filter((l) => l.startsWith('data:'))
        .map((l) => l.slice(5).trim()).join('\n');
      if (!data) continue;
      let msg;
      try { msg = JSON.parse(data); } catch (e) { continue; }
      if (msg && msg.id === id) found = msg;
    }
    return found;
  }
  try {
    const msg = JSON.parse(t);
    if (Array.isArray(msg)) return msg.find((m) => m && m.id === id) || null;
    return msg && msg.id === id ? msg : null;
  } catch (e) { return null; }
}

async function post(message, { timeoutMs, expectBody = true } = {}) {
  const f = fetchImpl || global.fetch;
  const headers = {
    'content-type': 'application/json',
    accept: 'application/json, text/event-stream',
    authorization: `Bearer ${key()}`,
    'mcp-protocol-version': PROTOCOL,
  };
  if (session && session.id) headers['mcp-session-id'] = session.id;
  const r = await f(`${baseUrl()}/mcp`, {
    method: 'POST', headers, body: JSON.stringify(message),
    signal: AbortSignal.timeout(timeoutMs || DEFAULT_TIMEOUT_MS),
  });
  const sid = r.headers && typeof r.headers.get === 'function' ? r.headers.get('mcp-session-id') : null;
  if (sid) session = { id: sid, url: baseUrl() };
  const body = expectBody ? await r.text() : '';
  return { status: r.status, contentType: r.headers && r.headers.get ? r.headers.get('content-type') : '', body };
}

async function initialise(timeoutMs) {
  const id = nextId++;
  const r = await post({
    jsonrpc: '2.0', id, method: 'initialize',
    params: { protocolVersion: PROTOCOL, capabilities: {}, clientInfo: CLIENT },
  }, { timeoutMs });
  if (r.status === 401 || r.status === 403) throw Object.assign(new Error('refused'), { kind: 'refused', detail: `key refused (${r.status})` });
  if (r.status >= 500 || r.status === 429) throw Object.assign(new Error('unreachable'), { kind: 'unreachable', detail: `initialize http ${r.status}` });
  const msg = parseReply(r.body, r.contentType, id);
  if (!msg || !msg.result) throw Object.assign(new Error('malformed'), { kind: 'malformed', detail: 'initialize answered no result' });
  if (!session) session = { id: null, url: baseUrl() };
  // The handshake's second half; a server may answer 202 with no body.
  try {
    await post({ jsonrpc: '2.0', method: 'notifications/initialized' }, { timeoutMs, expectBody: false });
  } catch (e) { /* a lost notification does not unseat the session */ }
}

async function ensureSession(timeoutMs) {
  if (session) return;
  if (!initPromise) {
    initPromise = initialise(timeoutMs).finally(() => { initPromise = null; });
  }
  await initPromise;
}

/** The tool's answer as data: JSON when it is JSON, else the text. */
function unpack(result) {
  const content = result && Array.isArray(result.content) ? result.content : [];
  const texts = content.filter((c) => c && c.type === 'text' && typeof c.text === 'string').map((c) => c.text);
  const text = texts.join('\n');
  if (!text) return result && result.structuredContent !== undefined ? result.structuredContent : null;
  try { return JSON.parse(text); } catch (e) { return text; }
}

/** A refusal's short explanation, from the shapes Flovia uses. */
function refusalDetail(data) {
  if (data && typeof data === 'object') {
    const ex = data.explanation;
    if (ex && typeof ex === 'object' && (ex.meaning || ex.summary)) return ex.meaning || ex.summary;
    if (typeof data.error === 'string') return data.error;
    if (typeof data.message === 'string') return data.message;
  }
  return typeof data === 'string' ? data : 'the tool reported an error';
}

/**
 * Call one Flovia tool. `args` is the tool's own argument object (request_text
 * and purpose included: Flovia wants the human request behind a lookup).
 */
async function call(tool, args, opts = {}) {
  if (!configured()) return notRead('unconfigured');
  const timeoutMs = opts.timeoutMs || DEFAULT_TIMEOUT_MS;
  let retriedSession = false;
  for (;;) {
    try {
      await ensureSession(timeoutMs);
    } catch (e) {
      return notRead(e && e.kind ? e.kind : 'unreachable', e && e.detail ? e.detail : (e && e.name) || 'Error');
    }
    const id = nextId++;
    let r;
    try {
      r = await post({ jsonrpc: '2.0', id, method: 'tools/call', params: { name: tool, arguments: args || {} } }, { timeoutMs });
    } catch (e) {
      return notRead('unreachable', (e && e.name) || 'Error');
    }
    if ((r.status === 404 || r.status === 400) && !retriedSession && /session/i.test(r.body || '')) {
      // The server forgot our session (a restart on their side): open one
      // more, once, then answer as it answers.
      retriedSession = true; resetSession(); continue;
    }
    if (r.status === 401 || r.status === 403) return notRead('refused', `key refused (${r.status})`);
    if (r.status === 429 || r.status >= 500) return notRead('unreachable', `http ${r.status}`);
    const msg = parseReply(r.body, r.contentType, id);
    if (!msg) return notRead('malformed', `http ${r.status}, no JSON-RPC reply`);
    if (msg.error) return notRead('refused', msg.error.message || `rpc ${msg.error.code}`);
    const data = unpack(msg.result);
    if (msg.result && msg.result.isError) return notRead('refused', refusalDetail(data));
    // Flovia can answer a tool call with its own structured refusal rather
    // than an isError: an explanation whose executed flag is false.
    if (data && typeof data === 'object' && data.explanation && data.explanation.executed === false) {
      return notRead('refused', refusalDetail(data));
    }
    return { read: true, data };
  }
}

module.exports = { call, configured, setFetch, resetSession, parseReply, DEFAULT_URL, PROTOCOL };
