'use strict';

/**
 * Setup-cell registrations the scoreboard may consult.
 *
 * `readingForCell` is the one decision. This loader is the only place that
 * reads the records already in the tree, and none of them is a
 * pre-registered setup × regime × timeframe × source × direction cell
 * whose prospective window replicated:
 *
 *   benchmark/eligibility/*.json
 *     A strategy-hash grant for autonomous live orders. No file ships.
 *     A verdict of "survives" there names the running strategy, not a
 *     setup cell, so it is not returned. The live gate is not asked.
 *
 *   benchmark/poc_retest/result.json
 *     One replay. Its verdict word means the interval cleared zero, which
 *     is the claim this scoreboard does not copy, and the windows do not
 *     name the five dimensions.
 *
 *   benchmark/hypotheses/
 *     Plan C1's registry. The directory is not in the tree. This loader
 *     does not invent a second registry and does not parse a schema that
 *     has not been committed. A file dropped there is not a cell.
 *
 *   C7 pattern history
 *     bot/core/pattern_history.py is not in the tree. The plan says that
 *     table assigns no survives labels, so there is nothing to read.
 *
 *   The vwap_reversion line in docs/FROZEN_BENCHMARK.md
 *     was written down before its fresh window, and that window did not
 *     clear zero. The prose is not parsed: a second copy of the interval
 *     would be a second answer, and there is no committed artefact of
 *     the cell.
 *
 * An absent or unreadable file is no registration. It is not a survives,
 * and it does not blank the scoreboard.
 */

const fs = require('fs');
const path = require('path');

const REPO = path.join(__dirname, '..', '..');

function readJson(file) {
  try {
    return JSON.parse(fs.readFileSync(file, 'utf8'));
  } catch (err) {
    return null;
  }
}

/**
 * Strategy grants. A verdict of "survives" is still not a setup cell:
 * the record has no five dimensions this scoreboard publishes.
 */
function registrationsFromEligibility(dir) {
  let names;
  try {
    names = fs.readdirSync(dir);
  } catch (err) {
    return [];
  }
  for (const name of names) {
    if (!name.endsWith('.json')) continue;
    const data = readJson(path.join(dir, name));
    if (!data || typeof data !== 'object') continue;
    // Seen, including a verdict of "survives", and not copied. A strategy
    // grant has no setup cell to attach the word to.
    if (typeof data.verdict === 'string') continue;
  }
  return [];
}

/**
 * The POC-retest replay. Its verdict word is an interval claim about one
 * setup the scoreboard does not key. Copying "survives" off a window
 * would be that claim under a different name.
 */
function registrationsFromPoc(file) {
  const data = readJson(file);
  const windows = data && Array.isArray(data.windows) ? data.windows : [];
  for (const w of windows) {
    if (!w || typeof w !== 'object') continue;
    // Seen, and not copied. The replay's verdict is not a five-dimension cell.
    if (typeof w.verdict === 'string') continue;
  }
  return [];
}

/**
 * Plan C1's directory. Absent today. Present, it is still not parsed:
 * there is no committed schema to read, and a free-text "survives" is
 * not a registration.
 */
function registrationsFromHypotheses(dir) {
  try {
    fs.readdirSync(dir);
  } catch (err) {
    return [];
  }
  return [];
}

function loadCellRegistrations(root) {
  const base = typeof root === 'string' && root ? root : REPO;
  return [
    ...registrationsFromEligibility(path.join(base, 'benchmark', 'eligibility')),
    ...registrationsFromPoc(path.join(base, 'benchmark', 'poc_retest', 'result.json')),
    ...registrationsFromHypotheses(path.join(base, 'benchmark', 'hypotheses')),
  ];
}

module.exports = { loadCellRegistrations };
