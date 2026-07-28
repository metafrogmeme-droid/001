'use strict';
/**
 * Compare tray — side-by-side agents with the evidence DECLARED. The contract:
 * - /agents/compare is a literal route registered BEFORE /agents/:slug
 *   (Express matches in order; moving it below would 404 into a slug lookup);
 * - every column declares its evidence tier: frozen backtest, member config,
 *   live copier record — different kinds of evidence are never blended;
 * - a missing number renders as "—", never as zero; a missing agent is
 *   "not found" ONLY when both catalogues actually answered; an unreadable
 *   record says so instead of showing 0;
 * - the tray caps at three, dedupes, and validates slugs.
 */
const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const path = require('path');

const read = (f) => fs.readFileSync(path.join(__dirname, '..', f), 'utf8');

test('the literal compare route is registered before the slug route', () => {
  const srv = read('server.js');
  const literal = srv.indexOf("app.get('/agents/compare'");
  const param = srv.indexOf("app.get('/agents/:slug'");
  assert.ok(literal !== -1 && param !== -1);
  assert.ok(literal < param, '/agents/compare must precede /agents/:slug');
  assert.match(srv, /Express matches in\s*\/\/ order/, 'the order constraint is documented in place');
});

test('every column declares its evidence tier', () => {
  const html = read('public/compare.html');
  for (const t of ['cmp.t_backtest', 'cmp.t_pending', 'cmp.t_config', 'cmp.t_live']) {
    assert.ok(html.includes(`'${t}'`), `${t} tier`);
  }
  assert.match(html, /Compare within a row, never across kinds/,
    'the non-commensurability note is the page subtitle');
});

test('missing data is honest: dash never zero, absence needs both catalogues', () => {
  const html = read('public/compare.html');
  assert.match(html, /never as zero/);
  assert.match(html, /bothRead \? T\('cmp\.not_found'/,
    'absence is claimed only when both catalogues answered');
  assert.match(html, /record unreadable — not zero/);
  assert.match(html, /INDEPENDENT sources/);
  assert.match(html, /name="robots" content="noindex"/, 'a parameterized view stays out of the index');
});

test('slugs are validated, deduped and capped at three', () => {
  const html = read('public/compare.html');
  assert.match(html, /SLUG_RE = \/\^\[a-z0-9\]\[a-z0-9-\]\{0,63\}\$\//);
  assert.match(html, /slugs\.length < 3/);
  assert.match(html, /slugs\.indexOf\(s\) === -1/);
});

test('the directory tray caps at three and never hijacks the card link', () => {
  const ag = read('public/agents.html');
  assert.match(ag, /data-cmp/);
  assert.match(ag, /CMP\.length < 3/);
  assert.match(ag, /e\.preventDefault\(\);\s*\/\/ the card is a link/);
  assert.match(ag, /\/agents\/compare\?slugs=/);
});

test('the dictionary carries the compare keys', () => {
  const i18n = read('public/js/i18n.js');
  for (const k of ['cmp.h1', 'cmp.note', 'cmp.t_backtest', 'cmp.rec_none', 'cmp.rec_unread',
    'cmp.not_found', 'cmp.unavailable', 'cmp.add_b', 'cmp.go_b']) {
    assert.ok(i18n.includes(`'${k}'`), `${k} in the dictionary`);
  }
});
