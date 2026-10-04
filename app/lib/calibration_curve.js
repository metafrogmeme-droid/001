'use strict';

/**
 * Read the calibration curve the bot already fitted.
 *
 * `bot/learning/confidence_calibration.py` writes
 * `data/learning/confidence_calibration.json` (`ConfidenceCalibrator.save`).
 * That document is the curve `calibrate()` applies. This module reads it.
 * It does not refit, and it does not turn
 * `CONFIDENCE_CALIBRATION_ENABLED` on.
 *
 * A missing file is unmeasured: the calibrator is identity until it has
 * a curve, and identity is not drawn. An unreadable file is unavailable,
 * not a curve of zeros. The first file that exists is the one; a corrupt
 * file is not skipped in favour of a second copy.
 */

const fs = require('fs');
const path = require('path');

const {
  readingFromCurve, unavailableReading, unmeasuredReading,
} = require('../public/js/calibration-chart');

const FILE_NAME = path.join('learning', 'confidence_calibration.json');

function unique(list) {
  const out = [];
  for (const item of list) {
    if (item && !out.includes(item)) out.push(item);
  }
  return out;
}

/**
 * Candidate files, most specific first.
 *
 * An explicit `RUNECLAW_STATE_DIR` is the bot's own directory and the
 * only place looked. Otherwise the repo `data/` (the web process often
 * starts in `app/`), then the process cwd.
 */
function candidateFiles() {
  const explicit = (process.env.RUNECLAW_STATE_DIR || '').trim();
  if (explicit) return [path.join(explicit, FILE_NAME)];
  return unique([
    path.join(__dirname, '..', '..', 'data', FILE_NAME),
    path.join(process.cwd(), 'data', FILE_NAME),
    path.join(process.cwd(), '..', 'data', FILE_NAME),
  ]);
}

function readCalibrationCurve(opts) {
  const options = opts || {};
  const files = options.file ? [options.file] : candidateFiles();
  let found = null;
  for (const file of files) {
    let st;
    try {
      st = fs.statSync(file);
    } catch (err) {
      if (err && err.code === 'ENOENT') continue;
      return unavailableReading();
    }
    found = { file: file, stat: st };
    break;
  }
  if (!found) return unmeasuredReading(null, null);
  if (!found.stat.isFile()) return unavailableReading();
  let text;
  try {
    text = fs.readFileSync(found.file, 'utf8');
  } catch (err) {
    return unavailableReading();
  }
  let doc;
  try {
    doc = JSON.parse(text);
  } catch (err) {
    return unavailableReading();
  }
  return readingFromCurve(doc);
}

module.exports = { readCalibrationCurve, candidateFiles, FILE_NAME };
