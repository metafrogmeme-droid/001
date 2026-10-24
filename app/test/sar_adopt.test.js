'use strict';

const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');

const alerts = require('../lib/sar/alerts');
const hip3 = require('../lib/sar/hip3');
const replay = require('../lib/sar/replay');
const adoption = require('../lib/sar/adoption');

function snap(bps) {
  return { sys: { sar95_bps: bps, esar95_bps: bps, reason: null } };
}

test('alerts stay quiet unless a threshold was actually read', async () => {
  assert.deepStrictEqual(alerts.evaluate({ sys: { sar95_bps: null, reason: 'partial_set' } }), {
    status: 'not_read', reason: 'partial_set', crossed: [], sar95_bps: null,
  });
  assert.deepStrictEqual(alerts.evaluate(snap('300')).crossed, []);
  assert.deepStrictEqual(alerts.evaluate(snap('301')).crossed, ['3pct']);
  assert.deepStrictEqual(alerts.evaluate(snap('500')).crossed, ['3pct']);
  assert.deepStrictEqual(alerts.evaluate(snap('501')).crossed, ['3pct', '5pct']);
  let calls = 0;
  const fetchImpl = async () => { calls += 1; return { ok: true }; };
  const off = await alerts.maybeSend(snap('900'), { SAR_ORACLE_ALERT_WEBHOOK: 'https://alerts.example/hook' }, fetchImpl);
  assert.equal(off.status, 'flag_off');
  assert.equal(calls, 0);
  const unset = await alerts.maybeSend(snap('900'), { SAR_ORACLE_ENABLED: '1' }, fetchImpl);
  assert.equal(unset.status, 'not_configured');
  assert.equal(calls, 0);
  const quiet = await alerts.maybeSend(snap('100'), {
    SAR_ORACLE_ENABLED: '1', SAR_ORACLE_ALERT_WEBHOOK: 'https://alerts.example/hook',
  }, fetchImpl);
  assert.equal(quiet.status, 'no_alert');
  assert.equal(calls, 0);
  let body = null;
  const sent = await alerts.maybeSend(snap('501'), {
    SAR_ORACLE_ENABLED: 'true', SAR_ORACLE_ALERT_WEBHOOK: 'https://alerts.example/hook',
  }, async (_url, opts) => {
    calls += 1;
    body = opts.body;
    return { ok: true };
  });
  assert.equal(sent.status, 'sent');
  assert.equal(calls, 1);
  assert.equal(String(body).includes('_usd_'), false);
  assert.deepStrictEqual(JSON.parse(body).crossed, ['3pct', '5pct']);
  const failed = await alerts.maybeSend(snap('501'), {
    SAR_ORACLE_ENABLED: '1', SAR_ORACLE_ALERT_WEBHOOK: 'https://alerts.example/hook',
  }, async () => { const err = new Error('no'); err.name = 'WebhookDown'; throw err; });
  assert.equal(failed.status, 'not_sent');
  assert.equal(failed.reason, 'WebhookDown');
});

test('the HIP-3 suggestion never signs and never goes under the floor', () => {
  const env = { SAR_ORACLE_HIP3_COIN: 'dex:OIL', SAR_ORACLE_HIP3_OI_UNITS: '5000000' };
  const quiet = hip3.suggest(env, snap('300'));
  assert.equal(quiet.status, 'below_threshold');
  assert.equal(quiet.action, null);
  assert.equal(quiet.signs, false);
  assert.equal(quiet.submits, false);
  const missing = hip3.suggest({}, snap('400'));
  assert.equal(missing.status, 'not_configured');
  assert.equal(missing.action, null);
  const noOi = hip3.suggest({ SAR_ORACLE_HIP3_COIN: 'dex:OIL' }, snap('400'));
  assert.equal(noOi.reason, 'oi_unreadable');
  assert.equal(noOi.action, null);
  const bad = hip3.suggest({ SAR_ORACLE_HIP3_COIN: 'dex:OIL', SAR_ORACLE_HIP3_OI_UNITS: '10.5' }, snap('400'));
  assert.equal(bad.reason, 'unit_unreadable');
  const unread = hip3.suggest(env, { sys: { sar95_bps: null, reason: 'partial_set' } });
  assert.equal(unread.status, 'not_read');
  assert.equal(unread.action, null);
  const small = hip3.suggest({ SAR_ORACLE_HIP3_COIN: 'dex:OIL', SAR_ORACLE_HIP3_OI_UNITS: '100' }, snap('400'));
  assert.equal(small.floor, '1000000');
  assert.deepStrictEqual(small.action.setOpenInterestCaps, [['dex:OIL', 1000000]]);
  const large = hip3.suggest(env, snap('600'));
  assert.equal(large.status, 'advisory');
  assert.equal(large.floor, '2500000');
  assert.equal(large.signs, false);
  assert.equal(large.submits, false);
  assert.deepStrictEqual(large.action, {
    type: 'perpDeploy',
    setOpenInterestCaps: [['dex:OIL', 2500000]],
  });
  const src = fs.readFileSync(path.join(__dirname, '..', 'lib', 'sar', 'hip3.js'), 'utf8');
  assert.equal(src.includes('signTypedData'), false);
  assert.equal(src.includes('/exchange'), false);
  assert.equal(src.includes('fetch('), false);
});

test('the fixture is labelled and the archive is not invented', () => {
  const report = replay.fixtureReport();
  assert.equal(report.label, 'fixture');
  assert.equal(report.not_october_2025, true);
  assert.equal(report.at_3pct.lead_min, '3');
  assert.equal(report.at_3pct.precision_e6, '1000000');
  assert.equal(report.at_3pct.recall_e6, '1000000');
  assert.equal(report.at_3pct.alerts, '4');
  assert.equal(report.at_5pct.lead_min, '1');
  assert.equal(report.at_5pct.alerts, '2');
  assert.equal(report.at_5pct.precision_e6, '1000000');
  assert.equal(report.placebo_3pct.precision_e6, '0');
  assert.equal(report.placebo_3pct.alerts, '1');
  assert.equal(report.placebo_3pct.false_alerts, '1');
  assert.equal(report.placebo_3pct.recall_e6, null);
  assert.equal(report.placebo_3pct.recall_reason, 'no_event');
  assert.equal(report.spearman_agree.coefficient_e6, '1000000');
  assert.equal(report.spearman_oppose.coefficient_e6, '-1000000');
  const gap = replay.score([{ t_min: 0n, sar95_bps: null, stress: false }], 300n);
  assert.equal(gap.status, 'not_read');
  assert.equal(gap.reason, 'partial_set');
  assert.equal(gap.precision_e6, undefined);
  const absent = replay.archiveStatus(undefined);
  assert.deepStrictEqual(absent, { status: 'not_read', reason: 'dataset_absent', label: 'archive' });
  const empty = fs.mkdtempSync(path.join(os.tmpdir(), 'sar-oct10-'));
  const unread = replay.archiveStatus(empty);
  assert.equal(unread.status, 'not_read');
  assert.equal(unread.reason, 'dataset_unreadable');
  assert.equal(unread.label, 'archive');
  fs.writeFileSync(path.join(empty, 'packets.json'), JSON.stringify([
    { t_min: 0, sar95_bps: 400, stress: false },
    { t_min: 5, sar95_bps: 400, stress: true },
  ]));
  const read = replay.archiveStatus(empty);
  assert.equal(read.status, 'read');
  assert.equal(read.label, 'archive');
  assert.equal(read.at_3pct.lead_min, '5');
  assert.equal(read.at_5pct.lead_reason, 'no_lead');
  assert.equal(read.at_5pct.recall_e6, '0');
  const view = adoption.view(snap('100'), {});
  assert.equal(JSON.stringify(view).includes('_usd_'), false);
  assert.equal(view.replay.reason, 'dataset_absent');
  assert.equal(view.hip3.status, 'not_configured');
  const sol = fs.readFileSync(path.join(__dirname, '..', '..', 'tools', 'sar-oracle', 'src', 'MorphoPreLiqSwitcher.sol'), 'utf8');
  assert.match(sol, /setAuthorization\(tight, true\)/);
  assert.equal(sol.includes('preLiquidate('), false);
  assert.equal(sol.includes('84532'), true);
});
