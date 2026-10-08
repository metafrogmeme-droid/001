'use strict';
/**
 * The agents intro claims only what a card carries.
 *
 * The /agents lede said "Each agent is one of the engine's real strategies,
 * backtested on frozen, content-hashed benchmark data", and the landing
 * page, the link previews and the per-agent meta said each preset has "a
 * verified, reproducible backtest". The catalogue says otherwise: a
 * rotation preset is run by neither the live bot nor a backtest, and a
 * moving-average preset is a reading `/run` never places
 * (`bot/core/strategy_catalog.py::live_runs`). The catalogue's own lineup
 * sentence (`catalogue_note`) already said "a card says when the engine does
 * not run its rule" and "where a frozen backtest is attached".
 *
 * These tests hold the intro, its inline fallback, the landing-page blurb,
 * the strategy page's footer and the link-preview text to that: a backtest
 * is named where a card carries one, and nowhere as a property of every
 * agent.
 */
const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const path = require('path');

const i18n = require('../public/js/i18n');
const seo = require('../lib/agent_seo');

const PUB = path.join(__dirname, '..', 'public');
const read = (f) => fs.readFileSync(path.join(PUB, f), 'utf8');
const flat = (s) => String(s).replace(/<[^>]+>/g, '').replace(/[’]/g, "'").replace(/\s+/g, ' ').trim();

// Every shape the universal claim took. Each one says all agents carry a
// backtest, which the rotation preset does not.
const UNIVERSAL = [
  /real strategies, backtested/i,
  /each (one )?(a real preset )?with a verified, reproducible backtest/i,
  /Real engine presets, each with/i,
  /Every RUNECLAW agent is one of/i,
];
const claimsEvery = (s) => UNIVERSAL.some((re) => re.test(flat(s)));

function elementText(html, attr, key) {
  const re = new RegExp('<p ' + attr + '="' + key.replace('.', '\\.') + '">([\\s\\S]*?)</p>');
  const m = html.match(re);
  assert.ok(m, 'expected one <p ' + attr + '="' + key + '"> element');
  assert.equal(html.split(attr + '="' + key + '"').length, 2, key + ' must appear once');
  return m[1];
}

test('the agents lede and the landing blurb name no backtest for every agent', () => {
  for (const key of ['ag.lede', 'sec.mkt_p']) {
    const en = i18n.STRINGS[key].en;
    assert.ok(!claimsEvery(en), key + ' still claims a backtest for every agent: ' + en);
    // The positive arm: the sentence the catalogue stands behind.
    assert.match(en, /preset from the engine.s own config/, key);
    assert.match(en, /does not run/, key);
    assert.match(en, /where (a frozen backtest|one)[^.]* is attached/i, key);
  }
});

test('the inline fallbacks say what the English string says', () => {
  const lede = elementText(read('agents.html'), 'data-i18n-html', 'ag.lede');
  assert.equal(flat(lede), flat(i18n.STRINGS['ag.lede'].en));
  const blurb = elementText(read('index.html'), 'data-i18n', 'sec.mkt_p');
  assert.equal(flat(blurb), flat(i18n.STRINGS['sec.mkt_p'].en));
});

test('every language carries the rewritten lede, not a stale translation', () => {
  // Across scripts, a test can check only that no language is blank, holds
  // the old English claim, or is the English left untranslated.
  for (const key of ['ag.lede', 'sec.mkt_p']) {
    const entry = i18n.STRINGS[key];
    for (const { code } of i18n.LANGS) {
      const s = entry[code];
      assert.ok(typeof s === 'string' && s.length > 40, key + ':' + code);
      assert.ok(!claimsEvery(s), key + ':' + code + ' carries the old English claim');
      if (code !== 'en') assert.notEqual(s, entry.en, key + ':' + code + ' is untranslated');
    }
  }
});

test('the agents page link previews name no backtest for every agent', () => {
  const html = read('agents.html');
  const metas = html.match(/<meta (name|property)="(description|og:description|twitter:description)" content="[^"]*">/g) || [];
  assert.equal(metas.length, 3, 'expected the three description metas');
  for (const m of metas) {
    assert.ok(!claimsEvery(m), m);
    assert.match(m, /where one is attached/, m);
  }
  const generic = seo.genericMeta('https://example.test');
  assert.ok(!claimsEvery(generic), generic);
  assert.match(generic, /where one is attached/);
});

test('a per-agent preview names a backtest only when the card carries its metrics', () => {
  const ORIGIN = 'https://example.test';
  const card = (scorecard) => ({ id: 'rot', name: 'Rotation', how: '', scorecard });
  // No tagline, so the fallback sentence is what renders.
  const withRun = seo.agentMeta(card({ metrics: { profit_factor: 1.2 } }), ORIGIN, 'rot');
  assert.match(withRun, /a RUNECLAW engine preset with a frozen backtest you can reproduce in the Lab\./);
  for (const sc of [null, undefined, { omitted: 'No frozen run of this preset exists.' }, {}]) {
    const m = seo.agentMeta(card(sc), ORIGIN, 'rot');
    assert.match(m, /The Rotation strategy agent — a RUNECLAW engine preset\./, JSON.stringify(sc));
    assert.ok(!/backtest/i.test(m), 'no backtest claimed for ' + JSON.stringify(sc) + ': ' + m);
  }
});

test("the strategy page's metrics footer speaks for this agent, not every agent", () => {
  const html = read('strategy.html');
  assert.ok(!claimsEvery(html), 'strategy.html still says every agent is backtested');
  // The sentence is the metrics branch's own value, read where the ternary
  // hands it out.
  assert.match(html,
    /\(a\.scorecard && a\.scorecard\.metrics\s*\?\s*'This agent\\'s backtest ran on frozen, content-hashed benchmark data/);
});
