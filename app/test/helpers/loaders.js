'use strict';
/**
 * Every `renderPanel(target, loader, opts)` call in a source file, sliced by
 * paren-matching from the call — ONE walker for every guard that inspects
 * loader bodies.
 *
 * There were two: panel_failure_honesty.test.js walked every call and
 * panel_timeout_budget.test.js walked only the inline `async () => {` form,
 * each with its own twenty lines of the same loop. The deck's chip-row slice
 * needed a third, and CLAUDE.md names that shape — the second copy of a
 * helper is a second answer, sitting inside the advice against it. So the
 * walk lives here and each caller keeps only its own filter.
 *
 * Pass the source through `codeOnly` FIRST. The walker finds a loader's end
 * by counting parentheses, and a comment can hold one that never closes:
 * prose about a method call (`.referralTierState(`) once made a walker run
 * past its panel and swallow the eighteen that followed. `codeOnly` blanks in
 * place, so `line` still points at the right line of the original file.
 *
 * @param {string} src  the (comment-blanked) source
 * @returns {Array<{target: string, body: string, line: number, inline: boolean}>}
 *   target  the first argument's text, e.g. `C('hero')`
 *   body    from `renderPanel(` to its matching `)`, inclusive
 *   line    1-based line of the call
 *   inline  true when the loader is written as `async () => {` in the call —
 *           a named loader function (`renderPanel(el, watchStripLoader)`)
 *           has a body the walker cannot see, and a guard that reads budgets
 *           or fetches out of the body must know it is looking at nothing
 */
function loaderBodies(src) {
  const out = [];
  const re = /renderPanel\(/g;
  let m;
  while ((m = re.exec(src))) {
    const open = m.index + 'renderPanel'.length;   // the call's own '('
    let depth = 0, i = open, comma = -1;
    for (; i < src.length; i++) {
      const c = src[i];
      if (c === '(') depth++;
      else if (c === ')') { depth--; if (depth === 0) break; }
      else if (c === ',' && depth === 1 && comma === -1) comma = i;
    }
    const target = src.slice(open + 1, comma === -1 ? i : comma).trim();
    const rest = comma === -1 ? '' : src.slice(comma + 1, i);
    out.push({
      target,
      body: src.slice(m.index, i + 1),
      line: src.slice(0, m.index).split('\n').length,
      inline: /^\s*async \(\) => \{/.test(rest),
    });
  }
  return out;
}

module.exports = { loaderBodies };
