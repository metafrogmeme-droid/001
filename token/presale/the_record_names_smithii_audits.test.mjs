// The record names Smithii's audits, and the sentence beside them is held to the deploy dates on record.
//
// token/config/rclaw.mainnet.json lists the audits Smithii commissioned of its own programs, and the
// website prints them with one sentence of context: both programs were last deployed before their
// latest audit's final report, so the code running is not shown to be the audited code. That
// sentence is a claim about two dates this repo already holds (smithii.config.json
// program.lastDeployedAt, locks.plan.json tool.programLastDeployed, both read from the chain and
// pinned by the verifiers), so it is held to them here, in every language the page speaks: a
// redeploy after an audit makes this fail, and the sentence must change with it.

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';

import { loadLocksPlan } from './locks_lib.mjs';
import { loadSmithiiConfig, loadTokenRecord } from './smithii_lib.mjs';

const require = createRequire(import.meta.url);
const page = require('../../app/public/js/token-page.js');
const { STRINGS, LANGS } = require('../../app/public/js/i18n.js');

const record = loadTokenRecord();
const cfg = loadSmithiiConfig();
const plan = loadLocksPlan();
const items = record.smithii_audits.items;
const BASE58 = /^[1-9A-HJ-NP-Za-km-z]{32,44}$/;
const SALE_DEPLOYED = cfg.program.lastDeployedAt.slice(0, 10);
const VESTING_DEPLOYED = plan.tool.programLastDeployed;

test('every audit has its fields, in the shapes the page prints, and a link the page will print', () => {
  assert.ok(items.length >= 1);
  for (const it of items) {
    assert.deepEqual(Object.keys(it), ['auditor', 'subject', 'date', 'program', 'final_report', 'url'], it.url);
    assert.ok(typeof it.auditor === 'string' && it.auditor && typeof it.subject === 'string' && it.subject);
    assert.match(it.date, /^\d{4}-\d{2}$/);
    assert.ok(it.program === null || BASE58.test(it.program), it.url);
    assert.ok(it.final_report === null || /^\d{4}-\d{2}-\d{2}$/.test(it.final_report), it.url);
    assert.ok(page.isAuditUrl(it.url), `the page would refuse ${it.url}`);
  }
  assert.equal(new Set(items.map((i) => i.url)).size, items.length, 'each audit once');
});

test('the audits name the two programs the sale and the locks run on', () => {
  const programs = new Set(items.map((i) => i.program).filter(Boolean));
  assert.ok(programs.has(cfg.program.id), `no audit names the sale program ${cfg.program.id}`);
  assert.ok(programs.has(plan.tool.program), `no audit names the vesting program ${plan.tool.program}`);
  assert.deepEqual([...programs].sort(), [cfg.program.id, plan.tool.program].sort(), 'an audit names a program the sale does not use');
});

test('both programs were last deployed before their latest audit\'s final report, as the page says', () => {
  for (const [program, deployed] of [[cfg.program.id, SALE_DEPLOYED], [plan.tool.program, VESTING_DEPLOYED]]) {
    const reports = items.filter((i) => i.program === program && i.final_report).map((i) => i.final_report).sort();
    assert.ok(reports.length, `no final report on record for ${program}`);
    const latest = reports[reports.length - 1];
    assert.ok(deployed < latest, `${program} was deployed ${deployed}, not before its latest audit's final report ${latest}: the page's sentence is no longer true`);
  }
});

test('the sentence beside the links carries both deploy dates, in every language', () => {
  for (const d of [SALE_DEPLOYED, VESTING_DEPLOYED]) assert.ok(page.AUDITS_BODY.includes(d), d);
  assert.equal(STRINGS['tok.audits_body'].en, page.AUDITS_BODY);
  for (const l of LANGS) {
    const t = STRINGS['tok.audits_body'][l.code];
    assert.ok(t, `tok.audits_body has no ${l.code}`);
    for (const d of [SALE_DEPLOYED, VESTING_DEPLOYED]) assert.ok(t.includes(d), `${l.code} lacks ${d}`);
    assert.ok(STRINGS['tok.audits_h'][l.code], `tok.audits_h has no ${l.code}`);
  }
});
