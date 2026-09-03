'use strict';
/**
 * A helper declared inside one view's render function must not be called
 * from another view's. dashboard.js is one IIFE; each view is an indent-2
 * `function renderX()` and helpers declared at indent 4 belong to it alone.
 *
 * 2026-09-03: `yieldTotalsCopy` was declared inside renderEngine() and called
 * from renderAccount(). `node --check` passed (it is valid syntax), the seam
 * test passed (it slices the function's text into a VM), preflight was green,
 * and in production the Account view's yield panel threw ReferenceError in
 * its loader on every render. No gate here ran the page; this one resolves
 * every call to a nested helper against the declarations actually in scope.
 *
 * String and template-literal TEXT is blanked before matching -- "close(s)",
 * "Prepare first (" and "${...}s (analyzing" are prose -- but `${ ... }`
 * expressions are kept, because that is exactly where the bad call lived.
 */
const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const DASH = path.join(__dirname, '..', 'public', 'js', 'dashboard.js');

/** Replace comment and string/template text with spaces, keeping newlines and `${}` code. */
function blankLiterals(src) {
  const out = []; let i = 0; const n = src.length;
  const keep = (c) => out.push(c); const blank = (c) => out.push(c === '\n' ? '\n' : ' ');
  const stack = [];   // template nesting: number of open braces inside the current ${ }
  while (i < n) {
    const c = src[i], d = src[i + 1];
    if (c === '/' && d === '/') { while (i < n && src[i] !== '\n') { blank(src[i]); i++; } continue; }
    if (c === '/' && d === '*') { blank(c); blank(d); i += 2; while (i < n && !(src[i] === '*' && src[i + 1] === '/')) { blank(src[i]); i++; } blank(' '); blank(' '); i += 2; continue; }
    if (c === '/' && regexMayStart(out)) {   // a regex literal: blank its body, its quotes are not strings
      keep(c); i++; let cls = false;
      while (i < n && (cls || src[i] !== '/') && src[i] !== '\n') {
        if (src[i] === '\\') { blank(src[i]); i++; blank(src[i]); i++; continue; }
        if (src[i] === '[') cls = true; else if (src[i] === ']') cls = false;
        blank(src[i]); i++;
      }
      keep('/'); i++; while (i < n && /[a-z]/.test(src[i])) { keep(src[i]); i++; }
      continue;
    }
    if (c === "'" || c === '"') { const q = c; keep(q); i++; while (i < n && src[i] !== q) { if (src[i] === '\\') { blank(src[i]); i++; } blank(src[i]); i++; } keep(q); i++; continue; }
    if (c === '`') {
      keep(c); i++;
      while (i < n && src[i] !== '`') {
        if (src[i] === '\\') { blank(src[i]); i++; blank(src[i]); i++; continue; }
        if (src[i] === '$' && src[i + 1] === '{') {
          keep('$'); keep('{'); i += 2; let depth = 1;
          while (i < n && depth > 0) {
            if (src[i] === '`') { const inner = blankLiteralsFrom(src, i); out.push(...inner.text); i = inner.end; continue; }
            if (src[i] === '{') depth++; else if (src[i] === '}') depth--;
            keep(src[i]); i++;
          }
          continue;
        }
        blank(src[i]); i++;
      }
      keep('`'); i++; continue;
    }
    keep(c); i++;
  }
  return out.join('');
}
/** Can a `/` here begin a regex literal? Yes after an operator, an opener, or a keyword; no after a value. */
function regexMayStart(out) {
  let k = out.length - 1;
  while (k >= 0 && (out[k] === ' ' || out[k] === '\n')) k--;
  if (k < 0) return true;
  const prev = out[k];
  if ('(,=:[!&|?{};+-*%<>~^'.includes(prev)) return true;
  if (/[\w$]/.test(prev)) {
    let j = k; while (j >= 0 && /[\w$]/.test(out[j])) j--;
    const word = out.slice(j + 1, k + 1).join('');
    return ['return', 'typeof', 'in', 'of', 'case', 'do', 'else', 'void', 'delete', 'throw', 'new', 'instanceof', 'yield', 'await'].includes(word);
  }
  return false;
}
/** A nested template literal inside `${}`: blank it and return where it ends. */
function blankLiteralsFrom(src, start) {
  let i = start; const text = ['`']; i++;
  while (i < src.length && src[i] !== '`') { if (src[i] === '\\') { text.push(' '); i++; } text.push(src[i] === '\n' ? '\n' : ' '); i++; }
  text.push('`'); return { text, end: i + 1 };
}

function unresolvedCalls(src) {
  const code = blankLiterals(src).split('\n');
  const spans = [];
  for (let i = 0; i < code.length; i++) {
    const m = /^  (?:async )?function (\w+)\s*\(/.exec(code[i]);
    if (m) { let j = i + 1; while (j < code.length && code[j] !== '  }') j++; spans.push({ name: m[1], start: i, end: j }); }
  }
  const enclosing = (i) => spans.find(s => i > s.start && i < s.end);
  const decls = {};
  for (let i = 0; i < code.length; i++) {
    const m = /^( +)(?:async )?function (\w+)\s*\(/.exec(code[i])
      || /^( +)(?:const|let|var) (\w+)\s*=\s*(?:async\s*)?(?:\(|function\b|\w+\s*=>)/.exec(code[i]);
    if (!m) continue;
    (decls[m[2]] ||= []).push({ line: i + 1, scope: m[1].length <= 2 ? null : enclosing(i) });
  }
  const bad = [];
  for (const name of Object.keys(decls).filter(k => decls[k].some(d => d.scope))) {
    const re = new RegExp(`(?<![\\w.$])${name}\\s*\\(`);
    for (let i = 0; i < code.length; i++) {
      if (!re.test(code[i]) || decls[name].some(d => d.line === i + 1)) continue;
      const sp = enclosing(i);
      if (!decls[name].some(d => d.scope === null || (sp && d.scope === sp))) {
        bad.push(`${name}( at line ${i + 1} in ${sp ? sp.name + '()' : 'module level'} — declared only in `
          + decls[name].map(d => (d.scope ? d.scope.name + '()' : 'module') + ':' + d.line).join(', '));
      }
    }
  }
  return bad;
}

test('the scanner catches a helper called from a view that does not declare it', () => {
  const fixture = [
    '(function () {',
    '  function renderA() {',
    '    function helper(x) { return x; }',
    '    return helper(1);',
    '  }',
    '  function renderB() {',
    '    return `${helper(2)} close(s) first (`;',
    '  }',
    '})();',
    '',
  ].join('\n');
  const bad = unresolvedCalls(fixture);
  assert.equal(bad.length, 1, bad.join('\n'));
  assert.match(bad[0], /helper\( at line 7 in renderB\(\)/);
});

test('string and template text never counts as a call', () => {
  const fixture = [
    '(function () {',
    '  function renderA() {',
    '    const close = () => 1;',
    '    return close();',
    '  }',
    '  function renderB() {',
    "    return 'close(s) excluded' + `Prepare first (x)` + \"first (\";",
    '  }',
    '})();',
    '',
  ].join('\n');
  assert.deepStrictEqual(unresolvedCalls(fixture), []);
});

test('every nested helper in dashboard.js is called only where it is declared', () => {
  const bad = unresolvedCalls(fs.readFileSync(DASH, 'utf8'));
  assert.deepStrictEqual(bad, [], 'a helper is called outside the function that declares it:\n  ' + bad.join('\n  '));
});
