'use strict';
/**
 * ∞ is not a score, on the web page too.
 *
 * `profit_factor` is the string 'inf' for a member who has not lost a
 * round-trip yet. Both surfaces already render it as a symbol rather than a
 * number — the page's own `pf()` and the Telegram card's `_pf()` agree. But
 * only the Telegram card said what it MEANS. An unexplained ∞ at rank 1 reads
 * as extraordinary skill; it usually means three trades and no drawdown yet,
 * which is a short record rather than a perfect one.
 *
 * Two properties, and the second is why this is executed rather than grepped:
 *
 * - the note appears only when a row actually reads ∞. A footnote nobody's row
 *   earns is clutter, and clutter trains people to skip the footnotes that
 *   matter.
 * - it is reset BEFORE the empty-board early return. The first draft set it
 *   after, so switching from a live board with an ∞ row to a season with no
 *   rows left a footnote on screen explaining a symbol that was no longer
 *   there. A source scan sees the assignment either way — only running it
 *   through the season switch tells the two apart.
 *
 * The renderer is inline in the page, so it is sliced out and run against a
 * stub DOM: the pattern CLAUDE.md records for the dashboard's engine-status
 * chip, for exactly this reason.
 */
const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const html = fs.readFileSync(
  path.join(__dirname, '..', 'public', 'leaderboard.html'), 'utf8');

function loadRender() {
  const start = html.indexOf('  function render(data) {');
  const end = html.indexOf('  function load(season) {');
  assert.ok(start > 0 && end > start, 'render() not found — did the page move?');

  const els = {
    rows: { innerHTML: '' },
    meta: { textContent: '' },
    infNote: { hidden: true },
    seasons: { innerHTML: '' },
  };
  const ctx = {
    rowsEl: els.rows,
    metaEl: els.meta,
    seasonsEl: els.seasons,
    activeSeason: '',
    renderSeasons() {},
    renderCountdown() {},
    esc: (s) => String(s == null ? '' : s),
    pf: (v) => (v === 'inf' ? '∞' : v == null ? '—' : String(v)),
    document: { getElementById: (id) => els[id] || null },
  };
  vm.createContext(ctx);
  vm.runInContext(html.slice(start, end), ctx);
  return { render: ctx.render, els, ctx };
}

const ROW = (pf) => ({ rank: 1, handle: 'nightjar', profit_factor: pf,
                       round_trips: 3, sharpe: 1.2, publish_hash: 'abc123def456' });

test('the note is hidden when no row has an unbounded profit factor', () => {
  const { render, els } = loadRender();
  render({ rows: [ROW('2.41')] });
  assert.strictEqual(els.infNote.hidden, true,
    'a footnote nobody earns trains people to skip the ones that matter');
});

test('the note appears when a row reads ∞', () => {
  const { render, els } = loadRender();
  render({ rows: [ROW('inf')] });
  assert.strictEqual(els.infNote.hidden, false);
  assert.match(els.rows.innerHTML, /∞/, 'the symbol itself must still render');
});

test('switching to an empty season clears a note the previous board earned', () => {
  const { render, els, ctx } = loadRender();
  render({ rows: [ROW('inf')] });
  assert.strictEqual(els.infNote.hidden, false, 'precondition');

  ctx.activeSeason = '2026-07';
  render({ rows: [] });
  assert.strictEqual(els.infNote.hidden, true,
    'the footnote explained a symbol that is no longer on screen');
});

test('the note text says what ∞ actually means', () => {
  assert.match(html, /no losing round-trip yet/,
    'rendering the symbol without its meaning is the defect this fixes');
});
