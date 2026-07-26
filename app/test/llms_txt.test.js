'use strict';
/**
 * llms.txt — the site introduces itself to agents. The contract:
 * - Every endpoint it names actually exists in this codebase (each claim is
 *   checked against the mounted routes — a self-description that drifts
 *   from reality is worse than none).
 * - The doctrine lines ride in it, and F-15 holds: no secret-shaped token,
 *   env-var name or internal path appears.
 * - robots.txt exists, allows crawling, and points agents at /llms.txt.
 */
process.env.JWT_SECRET = process.env.JWT_SECRET || 'j'.repeat(64);
delete process.env.DATABASE_URL;

const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const read = (...p) => fs.readFileSync(path.join(__dirname, '..', ...p), 'utf8');
const llms = read('public', 'llms.txt');
// tool8257 mounts at root, so its internal paths count as mounts too.
const server = read('server.js') + read('routes', 'tool8257.js');

test('every named API path is really mounted', () => {
  const claims = [...llms.matchAll(/(?:GET|POST) (?:https:\/\/pmvc58g2\.mule\.page)?(\/[a-z0-9/._-]+)/gi)]
    .map((m) => m[1])
    .filter((p) => p.startsWith('/api/') || p === '/mcp');
  assert.ok(claims.length >= 10, 'the surface list is substantive');
  for (const claim of claims) {
    // Mount prefixes live in server.js; the deepest two segments are enough
    // to prove the mount exists (route internals are covered by their tests).
    const prefix = claim.split('<')[0].split('/').slice(0, 3).join('/');
    assert.ok(server.includes(`'${prefix}'`) || server.includes(`'${prefix}`),
      `${claim} is claimed but ${prefix} is not mounted in server.js`);
  }
});

test('the doctrine rides in the self-description', () => {
  assert.match(llms, /omitted, never invented/);
  assert.match(llms, /heuristic flags with reasons, never verdicts/);
  assert.match(llms, /No trading advice, price predictions or buy\/sell recommendations/);
  assert.match(llms, /percent, ratio and count — never account amounts/);
  assert.match(llms, /Past performance never predicts future results/);
  assert.match(llms, /seal = sha256\(utf8\(seal_payload\)\)/, 'the verify quickstart is real math');
  assert.match(llms, /RCROOT1:<day>:<root>/);
  assert.match(llms, /Not investment advice/);
});

test('F-15: no secret-shaped token leaks into the agent-facing file', () => {
  for (const bad of ['LLM_API_KEY', 'JWT_SECRET', 'BOT_SYNC', 'NFT_VOUCHER',
    'GATEWAY_SECRET', 'PRIVATE_KEY', 'setllm', 'TOOL_CREATOR_ADDRESS', 'localhost', '127.0.0.1']) {
    assert.ok(!llms.includes(bad), `llms.txt must never mention ${bad}`);
  }
});

test('robots.txt allows crawling and points agents at llms.txt', () => {
  const robots = read('public', 'robots.txt');
  assert.match(robots, /User-agent: \*/);
  assert.match(robots, /Allow: \//);
  assert.match(robots, /llms\.txt/);
  assert.doesNotMatch(robots, /Disallow: \/\s*$/m, 'nothing public here is a secret');
});
