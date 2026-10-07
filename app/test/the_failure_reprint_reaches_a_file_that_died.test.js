'use strict';
/**
 * The CI re-print reaches a file that died.
 *
 * The web job re-prints failures at the end of its log, because the Actions
 * log API serves only the last ~344KB. It used `grep "^not ok"`, which reads
 * TAP, and the suite prints the spec reporter's ✔/✖ lines. On #546 the
 * re-print was empty: `holdings.test.js` died on EADDRINUSE before its first
 * subtest, and the error sat mid-log. These tests drive
 * `scripts/reprint_failures.js` on that log, as it was printed.
 */
const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const path = require('path');
const { failureBlocks, render } = require('../scripts/reprint_failures');

// #546's web job, timestamps stripped: the lines around the dead file, then
// the closing summary.
const LOG_546 = [
  '✔ hero.free_note exists in all six languages (13.729021ms)',
  'Using in-memory database (no DATABASE_URL found)',
  'node:internal/test_runner/harness:124',
  '      throw err;',
  '      ^',
  '',
  'Error: listen EADDRINUSE: address already in use 127.0.0.1:39878',
  '    at Server.setupListenHandle [as _listen2] (node:net:2328:16)',
  "  code: 'EADDRINUSE',",
  '  port: 39878',
  '}',
  '',
  'Node.js v24.21.0',
  '✖ test/holdings.test.js (175.409046ms)',
  '✔ radar chip phrases that left are not claimed by a local matcher (14.879214ms)',
  'ℹ tests 4873',
  'ℹ fail 1',
  '',
  '✖ failing tests:',
  '',
  'test at test/holdings.test.js:1:1',
  '✖ test/holdings.test.js (175.409046ms)',
  "  'test failed'",
].join('\n');

test("a file that died is re-printed with its error, once", () => {
  const blocks = failureBlocks(LOG_546);
  assert.equal(blocks.length, 1, blocks.join('\n====\n'));
  const [b] = blocks;
  assert.match(b, /EADDRINUSE: address already in use 127\.0\.0\.1:39878/);
  assert.match(b, /✖ test\/holdings\.test\.js/);
  // Bounded by the previous result line: the passing test above is not in it.
  assert.ok(!b.includes('hero.free_note'), b);
  // Line numbers point into the log.
  assert.match(b, /^2: Using in-memory database/);
});

test('a TAP failure is re-printed with the lines after it', () => {
  const tap = ['ok 1 - fine', 'not ok 2 - broke', '  ---', '  error: boom', '  ...', 'ok 3 - fine'].join('\n');
  const blocks = failureBlocks(tap);
  assert.equal(blocks.length, 1);
  assert.match(blocks[0], /^2: not ok 2 - broke/);
  assert.match(blocks[0], /error: boom/);
});

test('a clean log says nothing was found, rather than printing nothing', () => {
  const clean = ['✔ a (1ms)', '✔ b (1ms)', 'ℹ fail 0'].join('\n');
  assert.deepEqual(failureBlocks(clean), []);
  assert.match(render(clean), /No failed file or `not ok` block was found/);
  assert.match(render(LOG_546), /EADDRINUSE/);
});

// The `run: |` block of the step that tees the web suite into its log, read
// by YAML indentation: the block is every line indented deeper than `run:`.
function webTestsRunBlock(ci) {
  const lines = ci.split('\n');
  const tee = lines.findIndex((l) => l.includes('npm test 2>&1 | tee test-output.log'));
  assert.ok(tee > 0, 'expected the web Tests step');
  let r = tee;
  while (r >= 0 && !/^\s*run: \|\s*$/.test(lines[r])) r -= 1;
  assert.ok(r >= 0, 'expected the step to be a `run: |` block');
  const indent = lines[r].search(/\S/);
  const block = [];
  for (let k = r + 1; k < lines.length; k += 1) {
    const l = lines[k];
    if (l.trim() && l.search(/\S/) <= indent) break;
    block.push(l);
  }
  return block.join('\n');
}

test('the web job runs the re-print on its log', () => {
  const ci = fs.readFileSync(path.join(__dirname, '..', '..', '.github', 'workflows', 'ci.yml'), 'utf8');
  const block = webTestsRunBlock(ci);
  assert.match(block, /npm test 2>&1 \| tee test-output\.log/);
  assert.match(block, /node scripts\/reprint_failures\.js test-output\.log/);
  assert.match(block, /exit \$code/);
});
