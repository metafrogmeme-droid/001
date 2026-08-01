'use strict';
/**
 * Blank JS comments IN PLACE, without eating string contents.
 *
 * Every source-scanning guard needs this, and there were two copies of it —
 * one in public_no_dollars.test.js (§4) and one in f15_error_responses.test.js
 * (F-15). Both were written as
 *
 *     src.replace(/\/\*[\s\S]*?\*\//g, ...)
 *
 * which treats a `/*` inside a STRING LITERAL as the start of a comment. When
 * that `/*` has no matching `*\/` in the same string, the fake comment runs to
 * the next `*\/` anywhere later in the file and blanks every line between.
 *
 * On server.js that cost 68% of the file: the F-15 guard was inspecting a
 * third of it and reporting the whole thing clean (#1041). The §4 copy is not
 * currently losing coverage — measured at 0.4% worst case across every route
 * — but it carries the identical trap, and would spring it the day a public
 * route gains a string containing `/*`.
 *
 *     THE SAME BUG IN TWO COPIES IS ONE BUG WITH TWO PLACES TO RECUR.
 *
 * Two properties every caller depends on:
 *
 *   LINE NUMBERS SURVIVE. Comments are replaced with spaces, never removed,
 *   so an offset computed on the output points at the right line in the
 *   original. A plain-delete version once reported tool8257.js:45 for a leak
 *   living at :57.
 *
 *   STRING CONTENTS SURVIVE VERBATIM. Guards match on what the code EMITS,
 *   and that is mostly string and key text.
 *
 * Regex literals are not tracked: telling `/` as division from `/` as a regex
 * needs real parsing, and the failure mode here is a regex containing a
 * comment marker, which does not occur in this codebase. Stated rather than
 * hidden — if it ever does, this is where to look.
 */
function codeOnly(src) {
  let out = '';
  let i = 0;
  const n = src.length;
  while (i < n) {
    const c = src[i];
    const d = src[i + 1];
    if (c === '/' && d === '*') {
      const end = src.indexOf('*/', i + 2);
      const stop = end === -1 ? n : end + 2;
      out += src.slice(i, stop).replace(/[^\n]/g, ' ');
      i = stop;
      continue;
    }
    if (c === '/' && d === '/') {
      let j = i;
      while (j < n && src[j] !== '\n') j += 1;
      out += ' '.repeat(j - i);
      i = j;
      continue;
    }
    if (c === "'" || c === '"' || c === '`') {
      let j = i + 1;
      while (j < n) {
        if (src[j] === '\\') { j += 2; continue; }
        if (src[j] === c) { j += 1; break; }
        j += 1;
      }
      out += src.slice(i, j);
      i = j;
      continue;
    }
    out += c;
    i += 1;
  }
  return out;
}

module.exports = { codeOnly };
