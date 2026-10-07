#!/usr/bin/env node
'use strict';
/**
 * Re-print the web suite's failures at the end of the CI log.
 *
 *   node scripts/reprint_failures.js test-output.log
 *
 * The Actions log API serves only the last ~344KB of a job's log, so a
 * failure printed early is out of reach of anything that reads logs
 * programmatically (PR #993: "1929 pass / 1 fail", the failing test's name
 * unreachable). The CI step used to `grep -A 25 "^not ok"`, which reads TAP.
 * The suite prints the spec reporter's ✔/✖ lines, so on #546 it printed
 * nothing at all: `holdings.test.js` died on EADDRINUSE before its first
 * subtest, and that error sat mid-log, just above the file's own
 * `✖ test/holdings.test.js` line.
 *
 * Two shapes are found:
 *   - a file that died: the spec reporter prints the file's stderr, then
 *     `✖ test/<file>.test.js (…ms)`. The block is everything between the
 *     previous result line and that one, read above the closing summary;
 *   - a TAP `not ok` line, with the 25 lines after it.
 * A failing subtest's error is already in the spec reporter's closing
 * "failing tests" summary, which sits in the tail. When nothing is found,
 * this says so rather than printing an empty block.
 */
const fs = require('fs');

// A result line from the spec reporter: pass, fail or skip.
const RESULT = /^\s*[✔✖﹣]\s/;
const FILE_DIED = /^✖ test\/\S+\.test\.js \(/;
const TAP_FAIL = /^not ok\b/;
// The spec reporter's closing summary. It repeats each dead file's `✖` line
// with no error under it, so file blocks are read only above it.
const SUMMARY = /^✖ failing tests:/;

function failureBlocks(text) {
  const lines = String(text).split('\n');
  const blocks = [];
  let lastResult = -1;
  let inSummary = false;
  lines.forEach((line, i) => {
    if (SUMMARY.test(line)) inSummary = true;
    if (!inSummary && FILE_DIED.test(line)) {
      blocks.push(lines.slice(lastResult + 1, i + 1)
        .map((l, k) => `${lastResult + 2 + k}: ${l}`).join('\n'));
    } else if (TAP_FAIL.test(line)) {
      blocks.push(lines.slice(i, i + 26)
        .map((l, k) => `${i + 1 + k}: ${l}`).join('\n'));
    }
    if (RESULT.test(line)) lastResult = i;
  });
  return blocks;
}

function render(text) {
  const blocks = failureBlocks(text);
  if (!blocks.length) {
    return 'No failed file or `not ok` block was found in the log. '
      + "A failing subtest's error is in the spec reporter's \"failing tests\" summary above.";
  }
  return blocks.join('\n----\n');
}

if (require.main === module) {
  const file = process.argv[2];
  if (!file) {
    console.error('usage: node scripts/reprint_failures.js <test-output.log>');
    process.exit(2);
  }
  let text;
  try {
    text = fs.readFileSync(file, 'utf8');
  } catch (err) {
    console.log(`The test log could not be read (${err.code || err.name}), so no failure is re-printed.`);
    process.exit(0);
  }
  console.log(render(text));
}

module.exports = { failureBlocks, render };
