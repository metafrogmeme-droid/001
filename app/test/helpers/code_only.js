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
 * REGEX LITERALS ARE TRACKED, and the note that used to sit here is why.
 *
 * It read: "Regex literals are not tracked … the failure mode here is a regex
 * containing a comment marker, which does not occur in this codebase. Stated
 * rather than hidden — if it ever does, this is where to look."
 *
 * The stated failure mode was too narrow. A comment marker is not the only
 * character that breaks the scan: a regex containing a QUOTE does it too, and
 * far more quietly. `/[&<>"']/g` — an HTML-escaper, the most ordinary line in
 * any renderer — put the scanner into string mode at the `"` inside the
 * character class, where it then ran to whatever quote came next. Everything
 * after that point desynchronised, and every subsequent comment survived the
 * blanking it was supposed to receive.
 *
 * Measured before the fix, across the files the guards actually scan: SIXTEEN
 * files desynchronised, including `public/js/dashboard.js` (40 comment lines
 * surviving) and `public/js/app.js` (28) — the two that
 * `panel_failure_honesty.test.js` reads as the structural enforcer of this
 * repo's central rule. That file's own docstring describes a comment-induced
 * false negative and records that blanking now prevents it. The blanking was
 * running; on those two files it stopped a third of the way in.
 *
 * So this is the helper's own lesson turned on itself: an instrument whose
 * coverage is narrower than the claim read off it. "Does not occur in this
 * codebase" was a measurement of the codebase on the day it was written, kept
 * as a property of the helper.
 *
 * Telling `/`-as-division from `/`-as-regex needs the preceding token, which
 * is what `startsRegex` reads. It is a heuristic, not a parser, and it is
 * deliberately the standard one: a regex may begin where a VALUE may begin.
 */

/** Characters after which a `/` begins a regex rather than a division. */
const REGEX_PRECEDERS = new Set('(,=:[!&|?{};+-*%~^<>'.split(''));

/** Keywords after which a `/` likewise begins a regex (`return /x/` is legal). */
const REGEX_KEYWORDS = new Set([
  'return', 'typeof', 'instanceof', 'in', 'of', 'case', 'delete', 'void',
  'new', 'do', 'else', 'yield', 'await', 'throw',
]);

/**
 * Would a `/` appended to `out` start a regex literal?
 *
 * Reads the last significant character already emitted. After a value — an
 * identifier, a number, `)`, `]` — a `/` is division. After an operator, an
 * opening bracket, or nothing at all, it opens a regex. The keyword set is the
 * exception that a bare character test cannot express: `return` is an
 * identifier-shaped token that a value may follow.
 */
function startsRegex(out) {
  let k = out.length - 1;
  while (k >= 0 && /\s/.test(out[k])) k -= 1;
  if (k < 0) return true;                      // start of file
  const c = out[k];
  if (REGEX_PRECEDERS.has(c)) return true;
  if (/[A-Za-z0-9_$]/.test(c)) {
    let j = k;
    while (j >= 0 && /[A-Za-z0-9_$]/.test(out[j])) j -= 1;
    return REGEX_KEYWORDS.has(out.slice(j + 1, k + 1));
  }
  return false;                                // ')' ']' '.' etc — division
}

function codeOnly(src) {
  let out = '';
  let i = 0;
  const n = src.length;

  // TEMPLATE LITERALS ARE TRACKED TOO, and this is the older half of the bug.
  //
  // The original scanner treated a backtick like any other quote: open at one,
  // close at the next. A NESTED template — utterly routine in a renderer —
  //
  //     `<div>${cond ? `<span>${x}</span>` : ''}</div>`
  //
  // closes on the INNER backtick, and everything after it is scanned as though
  // it were code while actually being template text. That is what desynced
  // dashboard.js long before regex tracking existed; adding regex tracking
  // merely made the wreckage louder, because the stray `/` in `</span>` then
  // looked like a regex opener.
  //
  // `stack` records what we are inside. 'tmpl' is the literal text of a
  // template; 'expr' is a ${...} hole, where full code rules apply again —
  // comments, regexes, strings and further nested templates all included.
  const stack = [];
  const top = () => (stack.length ? stack[stack.length - 1] : null);

  while (i < n) {
    const c = src[i];
    const d = src[i + 1];

    // Inside a template's LITERAL text: only three things matter.
    if (top() && top().kind === 'tmpl') {
      if (c === '\\') { out += src.slice(i, i + 2); i += 2; continue; }
      if (c === '$' && d === '{') { stack.push({ kind: 'expr', depth: 0 }); out += '${'; i += 2; continue; }
      if (c === '`') { stack.pop(); out += c; i += 1; continue; }
      out += c; i += 1; continue;
    }

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
    // AFTER the comment forms, because `//` and `/*` are always comments in JS
    // — an empty regex has no literal spelling — and BEFORE the quote handler,
    // which is the whole point: a quote inside a character class must not open
    // a string.
    if (c === '/' && startsRegex(out)) {
      let j = i + 1;
      let inClass = false;
      let closed = false;
      while (j < n) {
        const ch = src[j];
        if (ch === '\\') { j += 2; continue; }   // \/ and \] are literal
        if (ch === '\n') break;                  // unterminated: not a regex after all
        if (inClass) {
          if (ch === ']') inClass = false;
        } else if (ch === '[') {
          inClass = true;                        // `/` inside [...] is literal
        } else if (ch === '/') {
          j += 1; closed = true; break;
        }
        j += 1;
      }
      if (closed) {
        while (j < n && /[a-z]/i.test(src[j])) j += 1;   // flags
        out += src.slice(i, j);
        i = j;
        continue;
      }
      // Fell off a line without closing — a division after all. Emit the one
      // character rather than swallowing the rest of the file, which is the
      // failure this block exists to prevent.
      out += c;
      i += 1;
      continue;
    }
    if (c === '`') { stack.push({ kind: 'tmpl' }); out += c; i += 1; continue; }
    if (c === "'" || c === '"') {
      let j = i + 1;
      while (j < n) {
        if (src[j] === '\\') { j += 2; continue; }
        if (src[j] === c) { j += 1; break; }
        // A single- or double-quoted string cannot span a line. Bailing here
        // keeps one unbalanced apostrophe from consuming the rest of the file.
        if (src[j] === '\n') break;
        j += 1;
      }
      out += src.slice(i, j);
      i = j;
      continue;
    }
    // Brace depth inside a ${...} hole, so the `}` that CLOSES the hole is told
    // apart from the ones closing objects and blocks written inside it.
    if (top() && top().kind === 'expr') {
      if (c === '{') top().depth += 1;
      else if (c === '}') {
        if (top().depth === 0) { stack.pop(); out += c; i += 1; continue; }
        top().depth -= 1;
      }
    }
    out += c;
    i += 1;
  }
  return out;
}

module.exports = { codeOnly };
