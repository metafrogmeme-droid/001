'use strict';

/**
 * The calibration chart is the curve already fitted, worded per C3.
 *
 * C3's default: stated confidence is a rung word or "unmeasured", not a
 * percentage read as a probability. The chart says that and stays muted.
 * An unreadable bin is omitted. A measured 0 stays on the chart as 0.
 * The reader does not fit a second curve.
 */
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');

const Cal = require('../public/js/calibration-chart');
const { readCalibrationCurve } = require('../lib/calibration_curve');
const { publicAnalytics, dollarKeys } = require('../lib/public_signal');
const { computeAnalytics } = require('../lib/signal_analytics');

function fittedDoc(over = {}) {
  return {
    bins: 10,
    min_samples: 30,
    shrinkage: 5,
    n_samples: 40,
    x: [0.65, 0.75],
    y: [0.18, 0],
    sample_reading: 1,
    ...over,
  };
}

function chartOf(doc) {
  return Cal.chart(Cal.readingFromCurve(doc));
}

test('a fitted bin renders as the curve already prints it', () => {
  const html = chartOf(fittedDoc());
  assert.equal(html.includes('data-state="fitted"'), true);
  assert.equal(html.includes('data-bin="0.65->0.18"'), true);
  assert.equal(html.includes('0.65->0.18'), true);
  assert.equal(html.includes('40 samples'), true);
  assert.match(html, /Provisional\./);
  assert.match(html, /rung word or unmeasured/);
  assert.match(html, /not a probability/);
  assert.equal(html.includes('%'), false);
  assert.equal(html.includes('$'), false);
  assert.equal(html.includes('survives'), false);
  assert.equal(html.includes('wr-pos'), false);
  assert.equal(html.includes('wr-neg'), false);
  assert.equal(html.includes('var(--up)'), false);
  assert.equal(html.includes('var(--down)'), false);
});

test('a measured zero stays on the axis and an unreadable bin is omitted', () => {
  const doc = fittedDoc({
    x: [0.65, 0.75, 0.85, 0.95],
    y: [0.18, 0, null, 'n/a'],
  });
  const reading = Cal.readingFromCurve(doc);
  assert.deepEqual(reading.bins.map((p) => [p.x, p.y]), [[0.65, 0.18], [0.75, 0]]);
  const html = Cal.chart(reading);
  assert.equal(html.includes('0.65->0.18'), true);
  assert.equal(html.includes('0.75->0.00'), true);
  assert.equal(html.includes('0.85'), false);
  assert.equal(html.includes('0.95'), false);
  assert.equal((html.match(/<circle /g) || []).length, 2);
  const zero = html.match(/<circle data-y="0\.00" cx="[\d.]+" cy="([\d.]+)"/);
  assert.ok(zero, 'the measured zero was not drawn');
  assert.equal(zero[1], Cal.dotY(0).toFixed(1));
  assert.equal(Number(zero[1]), Cal.PLOT.h - Cal.PLOT.pad);
  const above = html.match(/<circle data-y="0\.18" cx="[\d.]+" cy="([\d.]+)"/);
  assert.ok(above);
  assert.ok(Number(above[1]) < Number(zero[1]), 'a positive rate sits above zero');
});

test('a curve that is not fitted is unmeasured, not a line at zero', () => {
  const thin = Cal.readingFromCurve(fittedDoc({ n_samples: 0, x: [], y: [] }));
  assert.equal(thin.state, 'unmeasured');
  assert.equal(thin.n_samples, 0);
  assert.deepEqual(thin.bins, []);
  const html = Cal.chart(thin);
  assert.equal(html.includes('data-state="unmeasured"'), true);
  assert.match(html, />unmeasured</);
  assert.equal(html.includes('0 of 30 samples'), true);
  assert.equal(html.includes('<svg'), false);
  assert.equal(html.includes('<circle'), false);
  assert.equal(html.includes('0.00->'), false);

  const identity = Cal.chart(Cal.readingFromCurve(
    fittedDoc({ n_samples: 12, x: [0.65], y: [0.65] })));
  assert.equal(identity.includes('data-state="unmeasured"'), true);
  assert.equal(identity.includes('0.65'), false);
});

test('an unreadable document is unavailable, not a zero curve', () => {
  for (const doc of [null, [], { x: [0.65], y: [0.18] }, { x: [0.65], y: [0.18], n_samples: null, min_samples: 30 }]) {
    const reading = Cal.readingFromCurve(doc);
    assert.equal(reading.state, 'unavailable', JSON.stringify(doc));
    assert.equal(reading.n_samples, null);
    const html = Cal.chart(reading);
    assert.equal(html.includes('data-state="unavailable"'), true);
    assert.match(html, />unavailable</);
    assert.equal(html.includes('<svg'), false);
  }
  const missing = Cal.chart(undefined);
  assert.equal(missing.includes('unavailable'), true);
  assert.equal(missing.includes('<circle'), false);
});

test('a small non-zero rate is not painted as zero', () => {
  const html = chartOf(fittedDoc({ x: [0.65], y: [0.004] }));
  assert.equal(html.includes('data-bin="0.65->0.00"'), false);
  assert.equal(html.includes('data-bin="0.65->0.0040"'), true);
});

test('the public reading keeps rates and counts and drops a dollar key', () => {
  const reading = Cal.readingFromCurve(fittedDoc({ pnl_usd: 12.5, net_pnl: 3 }));
  assert.deepEqual(Object.keys(reading).sort(), ['bins', 'min_samples', 'n_samples', 'state']);
  const payload = publicAnalytics({
    ...computeAnalytics([]),
    calibration: reading,
  });
  assert.deepEqual(dollarKeys(payload), []);
  assert.equal(payload.calibration.bins[0].y, 0.18);
  assert.equal(payload.calibration.state, 'fitted');
});

test('the file reader returns the saved bins and does not invent a curve', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'rc-cal-'));
  const file = path.join(dir, 'confidence_calibration.json');
  fs.writeFileSync(file, JSON.stringify(fittedDoc()));
  const reading = readCalibrationCurve({ file });
  assert.equal(reading.state, 'fitted');
  assert.equal(reading.n_samples, 40);
  assert.deepEqual(reading.bins, [{ x: 0.65, y: 0.18 }, { x: 0.75, y: 0 }]);

  // No file where this process looks is not "not fitted": the bot writes
  // the document on every refit, so this says where the server looked.
  const missing = readCalibrationCurve({ file: path.join(dir, 'absent.json') });
  assert.equal(missing.state, 'absent');
  assert.deepEqual(missing.bins, []);

  fs.writeFileSync(file, '{');
  const broken = readCalibrationCurve({ file });
  assert.equal(broken.state, 'unavailable');
  assert.equal(broken.n_samples, null);
  assert.deepEqual(broken.bins, []);

  fs.rmSync(dir, { recursive: true, force: true });
});

test('the dashboard paints this chart from the analytics payload', () => {
  const dash = fs.readFileSync(path.join(__dirname, '..', 'public', 'js', 'dashboard.js'), 'utf8');
  assert.match(dash, /RCCalibration/);
  assert.match(dash, /\.chart\(a\.calibration\)/);
  const html = fs.readFileSync(path.join(__dirname, '..', 'public', 'dashboard.html'), 'utf8');
  const chart = html.indexOf('/js/calibration-chart.js?v=');
  const board = html.indexOf('/js/dashboard.js?v=');
  assert.ok(chart > 0 && board > chart);
});
