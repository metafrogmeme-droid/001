'use strict';
/**
 * Strategy Foundry — archetype templates + derived risk profiles. The
 * contract:
 * - every template passes the SAME validateStrategy gate a member's draft
 *   passes (drift between gallery and validator is structurally impossible);
 * - templates are configs phrased as intent: no dollar figures, no
 *   performance-claim words, canonical rule values the builder can render;
 * - the risk profile is COMPUTED, never authored — a deterministic
 *   restatement of the rules, tier always paired with its literal facts,
 *   and selectivity (confidence/R:R) never counts as containment;
 * - the public card carries the profile so no author can type their own.
 */
process.env.JWT_SECRET = process.env.JWT_SECRET || 'j'.repeat(64);
delete process.env.DATABASE_URL;

const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const path = require('path');
const express = require('express');
const http = require('http');

const { TEMPLATES, findTemplate } = require('../lib/strategy_templates');
const { validateStrategy, deriveRiskProfile, toPublicCard, REGIMES, HORIZONS } = require('../lib/user_strategies');

test('every template passes the member-draft validator, in canonical form', () => {
  assert.ok(TEMPLATES.length >= 8, 'a real gallery, not a token pair');
  const keys = new Set();
  for (const t of TEMPLATES) {
    const v = validateStrategy(t);
    assert.strictEqual(v.ok, true, `${t.key}: ${v.error}`);
    assert.ok(!keys.has(t.key), 'unique keys');
    keys.add(t.key);
    assert.ok(REGIMES.has(t.regime), `${t.key} regime`);
    assert.ok(HORIZONS.has(t.horizon), `${t.key} horizon`);
    // Canonical values: what the validator would store is what we ship, so
    // the builder's fill logic (e.g. confidence ×100 for display) is exact.
    assert.deepEqual(v.data.rules, t.rules, `${t.key} rules must be canonical`);
    // Builder-renderable only: every rule maps to a form field.
    const RENDERABLE = new Set(['direction', 'min_confidence', 'min_rr', 'max_position_pct',
      'max_open_positions', 'allowed_symbols', 'max_daily_loss_pct', 'max_drawdown_pct']);
    for (const r of t.rules) assert.ok(RENDERABLE.has(r.type), `${t.key}: ${r.type} not in builder`);
  }
  assert.strictEqual(findTemplate('trend-rider').name, 'Trend Rider');
  assert.strictEqual(findTemplate('nope'), null);
});

test('templates are intent, never claims: no dollars, no performance words', () => {
  const blob = JSON.stringify(TEMPLATES);
  assert.ok(!blob.includes('$'), 'no dollar figure anywhere (§4)');
  for (const t of TEMPLATES) {
    const prose = `${t.name} ${t.tagline} ${t.how}`;
    assert.ok(!/\b(profit|return|win rate|winrate|apy|roi|guarantee|outperform|beat the market)\b/i.test(prose),
      `${t.key} prose reads as a performance claim: ${prose}`);
  }
});

test('deriveRiskProfile: computed tiers with facts, selectivity never contains', () => {
  const tight = deriveRiskProfile([
    { type: 'max_position_pct', value: 5 }, { type: 'max_daily_loss_pct', value: 3 },
    { type: 'max_open_positions', value: 3 }, { type: 'direction', value: 'long_only' },
  ]);
  assert.strictEqual(tight.tier, 'tight');
  assert.deepEqual(tight.axes, ['count', 'direction', 'loss', 'sizing']);
  const pos = tight.facts.find((f) => f.id === 'pos');
  assert.strictEqual(pos.params.v, 5);
  assert.match(pos.en, /\{v\}%/, 'facts carry translatable templates');

  const capped = deriveRiskProfile([
    { type: 'max_position_pct', value: 5 }, { type: 'max_open_positions', value: 3 },
  ]);
  assert.strictEqual(capped.tier, 'capped');

  // Being picky about entries contains nothing once in a position.
  const selective = deriveRiskProfile([
    { type: 'min_confidence', value: 0.9 }, { type: 'min_rr', value: 5 },
  ]);
  assert.strictEqual(selective.tier, 'light');
  assert.deepEqual(selective.axes, []);
  assert.strictEqual(selective.facts.find((f) => f.id === 'conf').params.v, 90,
    'confidence fact shown as a percent');

  assert.deepEqual(deriveRiskProfile([]), { tier: 'light', axes: [], facts: [] });
  // direction "both" is not a constraint
  assert.deepEqual(deriveRiskProfile([{ type: 'direction', value: 'both' }]).axes, []);
});

test('the public card carries the computed profile beside the authored label', () => {
  const card = toPublicCard({
    slug: 's-1', name: 'X', icon: '🧠', tagline: '', how: '', risk_label: 'chill vibes',
    regime: 'any', horizon: 'swing', visibility: 'public',
    rules: JSON.stringify([{ type: 'max_position_pct', value: 5 }, { type: 'max_drawdown_pct', value: 10 }]),
  });
  assert.strictEqual(card.risk_profile.tier, 'capped');
  assert.strictEqual(card.risk_label, 'chill vibes', 'the authored voice stays, visibly separate');
  assert.ok(card.risk_profile.facts.length === 2);
});

test('GET /api/public/strategy-templates serves the gallery, cacheable', async () => {
  const app = express();
  app.use('/api/public/strategy-templates', require('../routes/strategy_templates'));
  const server = await new Promise((res) => { const s = app.listen(0, '127.0.0.1', () => res(s)); });
  const base = `http://127.0.0.1:${server.address().port}`;
  const r = await new Promise((resolve, reject) => {
    http.get(`${base}/api/public/strategy-templates`, (res) => {
      const chunks = [];
      res.on('data', (c) => chunks.push(c));
      res.on('end', () => resolve({ status: res.statusCode, headers: res.headers,
        body: JSON.parse(Buffer.concat(chunks)) }));
    }).on('error', reject);
  });
  server.close();
  assert.strictEqual(r.status, 200);
  assert.strictEqual(r.body.count, TEMPLATES.length);
  assert.match(r.headers['cache-control'], /max-age/);
  assert.ok(!JSON.stringify(r.body).includes('$'), '§4 on the wire');
});

test('the UI wires the gallery and renders the computed profile', () => {
  const dash = fs.readFileSync(path.join(__dirname, '..', 'public', 'js', 'dashboard.js'), 'utf8');
  assert.ok(dash.includes("'/api/public/strategy-templates'"), 'builder loads the gallery');
  assert.match(dash, /tplGallery/);
  assert.match(dash, /sDayLoss/, 'builder gained the daily-loss halt field');
  assert.match(dash, /max_drawdown_pct', value: num\('sDd'\)/, 'and the drawdown halt field');
  assert.match(dash, /profileLine\(a\.risk_profile\)/, 'community card shows the computed profile');
  for (const f of ['agents.html', 'strategy.html']) {
    const s = fs.readFileSync(path.join(__dirname, '..', 'public', f), 'utf8');
    assert.match(s, /risk_profile/, `${f} renders the profile`);
    assert.match(s, /sp\.tier_/, `${f} translates the tier`);
    assert.match(s, /not a safety verdict/, `${f} carries the caveat`);
  }
  const i18n = fs.readFileSync(path.join(__dirname, '..', 'public', 'js', 'i18n.js'), 'utf8');
  for (const k of ['sp.h', 'sp.cv', 'sp.tier_tight', 'sp.tier_capped', 'sp.tier_light',
    'sp.f_pos', 'sp.f_daily', 'sp.f_dd', 'dp.tpl_h', 'dd.t_tpl']) {
    assert.ok(i18n.includes(`'${k}'`), `${k} in the dictionary`);
  }
});

test('a community link survives an engine-catalogue outage (independent sources)', () => {
  const html = fs.readFileSync(path.join(__dirname, '..', 'public', 'strategy.html'), 'utf8');
  assert.match(html, /\.catch\(function \(\) \{ tryCommunity\(false\); \}\)/,
    'an engine-read failure still tries the community endpoint');
  assert.match(html, /engineReadable\) notFound/,
    'notFound is claimed only when BOTH sources actually answered');
  assert.match(html, /engine unread — no absence claim/,
    'an unread catalogue forfeits the right to say a slug does not exist');
});

test('server mounts the templates route', () => {
  const src = fs.readFileSync(path.join(__dirname, '..', 'server.js'), 'utf8');
  assert.ok(src.includes("app.use('/api/public/strategy-templates', require('./routes/strategy_templates'))"));
});
