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

/**
 * NO SOURCE EXISTS, SO NOTHING IS READ. This walked the three places above
 * on every request and returned `[]` from each whatever it found, so the
 * route paid for file reads that could not change its answer, and the
 * reads made it look as if a record committed there would be honoured. It
 * would not: none of those places has a five-dimension schema, and a
 * second registry is plan C1's to define. Until one is committed, no cell
 * can read "survives", by construction, and this says so by doing nothing.
 * `readingForCell` already takes a registration list, so a loader that
 * reads a committed schema is the one change that opens the arm.
 */
function loadCellRegistrations() {
  return [];
}

module.exports = { loadCellRegistrations };
