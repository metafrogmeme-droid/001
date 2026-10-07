'use strict';
/**
 * The calibration chart names what it draws.
 *
 *  - Its caption called each point's second number "the recorded rate". It is
 *    the calibrator's fitted value: the bin's recorded rate shrunk toward its
 *    stated confidence and pooled with its neighbours (a bin that won 0 of 3
 *    drew at 0.16). The bot now saves each bin's `trades` and `wins`, and the
 *    label prints them beside the fitted value; a file without them prints
 *    the fitted value alone.
 *  - A calibration file this server cannot find read as "unmeasured", the
 *    chart's word for "the calibrator has not fitted". The bot writes the
 *    file on every refit, fitted or not, so no file here says where this
 *    server looked: `absent`, with its own sentence.
 *
 * The document is the shape `ConfidenceCalibrator.to_dict` writes
 * (tests/test_the_calibration_file_records_each_bins_trades.py pins it).
 */
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');

const Cal = require('../public/js/calibration-chart');
const { readCalibrationCurve } = require('../lib/calibration_curve');
const { publicAnalytics, dollarKeys } = require('../lib/public_signal');

// The review's fit: 3 lost at 0.25, 14 of 26 at 0.65, 1 of 1 at 0.95.
function doc(over) {
  return Object.assign({
    bins: 10, min_samples: 30, shrinkage: 5.0, n_samples: 30, sample_reading: 2,
    x: [0.25, 0.65, 0.95], y: [0.15625, 0.5565, 0.9583],
    trades: [3, 26, 1], wins: [0, 14, 1],
  }, over || {});
}

const labels = (html) => [...html.matchAll(/<li data-bin="([^"]*)">([^<]*)<\/li>/g)].map((m) => ({ bin: m[1], text: m[2] }));

test('the caption says fitted, and never that the point is the recorded rate', () => {
  assert.doesNotMatch(Cal.CAPTION, /then the recorded rate/);
  assert.match(Cal.CAPTION, /fitted rate/);
  assert.match(Cal.CAPTION, /not the recorded win rate/);
});

test('each bin prints the trades and wins it rests on beside the fitted value', () => {
  const reading = Cal.readingFromCurve(doc());
  assert.equal(reading.state, 'fitted');
  assert.deepEqual(reading.bins.map((b) => [b.trades, b.wins]), [[3, 0], [26, 14], [1, 1]]);
  const got = labels(Cal.chart(reading));
  assert.deepEqual(got.map((l) => l.bin), ['0.25->0.16', '0.65->0.56', '0.95->0.96']);
  assert.equal(got[0].text, '0.25->0.16 · fitted; recorded 0 of 3 won');
  assert.equal(got[2].text, '0.95->0.96 · fitted; recorded 1 of 1 won');
});

test('a file without counts, or with counts that do not fit, prints the fitted value alone', () => {
  const old = doc(); delete old.trades; delete old.wins;
  // [document, the bins that print no record]; the others keep theirs.
  for (const [d, bare] of [
    [old, [0, 1, 2]],
    [doc({ trades: [3, 26], wins: [0, 14] }), [0, 1, 2]],       // not aligned with x
    [doc({ trades: [3, 26, 1], wins: [4, 14, 1] }), [0]],        // more wins than trades
    [doc({ trades: [3, 26.5, 1], wins: [0, 14, 1] }), [1]],      // not a count
    [doc({ trades: [3, -1, 1], wins: [0, 14, 1] }), [1]],
    [doc({ trades: [0, 26, 1], wins: [0, 14, 1] }), [0]],        // a stored bin rests on trades
  ]) {
    const got = labels(Cal.chart(Cal.readingFromCurve(d)));
    assert.equal(got.length, 3);
    got.forEach((l, i) => {
      if (bare.includes(i)) assert.equal(l.text, l.bin, `${JSON.stringify(d.trades)} bin ${i}`);
      else assert.match(l.text, / · fitted; recorded \d+ of \d+ won$/);
    });
  }
});

test('the counts reach the public payload as counts', () => {
  const payload = publicAnalytics({ calibration: Cal.readingFromCurve(doc()) });
  assert.deepEqual(dollarKeys(payload), []);
  assert.equal(payload.calibration.bins[0].trades, 3);
  assert.equal(payload.calibration.bins[0].wins, 0);
});

test('no file where this server looks is absent, not "not fitted"', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'rc-cal-absent-'));
  try {
    const missing = readCalibrationCurve({ file: path.join(dir, 'confidence_calibration.json') });
    assert.equal(missing.state, 'absent');
    const html = Cal.chart(missing);
    assert.match(html, /data-state="absent"/);
    assert.match(html, /not a reading of the bot/);
    assert.doesNotMatch(html, />unmeasured</);
    // A file that says it has not fitted is still "unmeasured", with its count.
    const file = path.join(dir, 'confidence_calibration.json');
    fs.writeFileSync(file, JSON.stringify(doc({ n_samples: 12, x: [], y: [], trades: [], wins: [] })));
    const unfitted = readCalibrationCurve({ file });
    assert.equal(unfitted.state, 'unmeasured');
    assert.match(Cal.chart(unfitted), /12 of 30 samples/);
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});
