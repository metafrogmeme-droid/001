'use strict';
/**
 * What the "By venue" panel SAYS, as opposed to what the arithmetic under it
 * computes. Those are two claims and this repo has repeatedly shipped the
 * second while getting the first wrong.
 *
 * `venue_breakdown.test.js` covers the counting. This covers the rendering:
 * the colour, the em dashes, the caveat, and the sentence under the table.
 *
 * The seam exists at all because of #999 — a card built inline in a handler,
 * source-scanned, shipped, and rendered zero times in production. A scan can
 * see that a line is PRESENT. Only a call can see what it produces.
 */

const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const { venueRow, venueFootnote, DASH } = require('../public/js/venue-rows');
const { venueBreakdown } = require('../lib/venue_breakdown');

const rowFor = (rows) => venueRow(venueBreakdown(rows)[0]);


// ── Colour is a claim ────────────────────────────────────────────────────

test('a venue whose every close was unpriced is not painted as break-even', () => {
  // venueBreakdown hands back pnl: 0 for it — nothing summed to nothing. That
  // zero is the ABSENCE of a total, and `(x || 0) >= 0` painting it green is
  // verbatim off CLAUDE.md's table of banned shapes.
  const c = rowFor([{ venue: 'okx', pnl: null }, { venue: 'okx', pnl: 'x' }]);
  assert.equal(c.pnl, DASH, 'an unmeasurable book printed a dollar figure');
  assert.equal(c.cls, '', `an unmeasured venue was painted "${c.cls}"`);
  assert.equal(c.winRate, DASH, 'a rate over zero measurements was printed');
});

test('a measured break-even IS printed, and carries no colour', () => {
  // The same defect facing the other way. Somebody priced these closes and
  // they came out flat; hiding that is as wrong as inventing it.
  const c = rowFor([{ venue: 'bitget', pnl: 0 }, { venue: 'bitget', pnl: 0 }]);
  assert.equal(c.pnl, '+$0.00');
  assert.equal(c.cls, '', 'a flat result was painted as profit or loss');
  assert.equal(c.winRate, '0.0%', 'a measured 0% is a real rate and must print');
});

test('profit and loss each get their own colour, and they are not the same one', () => {
  assert.equal(rowFor([{ venue: 'a', pnl: 5 }]).cls, 'pos');
  assert.equal(rowFor([{ venue: 'a', pnl: -5 }]).cls, 'neg');
  assert.equal(rowFor([{ venue: 'a', pnl: 5 }]).pnl, '+$5.00');
  assert.equal(rowFor([{ venue: 'a', pnl: -5 }]).pnl, '-$5.00');
});


// ── The caveat, and when it is absent ────────────────────────────────────

test('a row built on fewer measurements than trades says so on its own line', () => {
  const c = rowFor([{ venue: 'bybit', pnl: 3 }, { venue: 'bybit', pnl: null },
    { venue: 'bybit', pnl: null }]);
  assert.equal(c.trades, 3);
  assert.equal(c.note, '1 of 3 priced');
  assert.equal(c.pnl, '+$3.00', 'the total covers the priced ones');
  assert.equal(c.winRate, '100.0%', 'the rate is over what was scored');
});

test('a complete row carries no caveat at all', () => {
  // A caveat printed every time is how a real one gets skipped. Same reason
  // trade-stats.js leaves `coverage` empty on a fully-priced window.
  assert.equal(rowFor([{ venue: 'bitget', pnl: 1 }, { venue: 'bitget', pnl: -1 }]).note, '');
});

test('the venue label is the venue, upper-cased, and nothing is invented', () => {
  assert.equal(rowFor([{ venue: ' Bybit ', pnl: 1 }]).venue, 'BYBIT');
  assert.equal(rowFor([{ pnl: 1 }]).venue, 'BITGET', 'an unlabelled trade is a Bitget trade');
});

test('a garbage entry renders dashes rather than throwing or inventing', () => {
  for (const junk of [{}, null, undefined, { venue: 'x' }]) {
    const c = venueRow(junk);
    assert.equal(c.pnl, DASH);
    assert.equal(c.cls, '');
    assert.equal(c.winRate, DASH);
  }
});


// ── The sentence under the table ─────────────────────────────────────────

test('one row says everything happened there, rather than implying a first of several', () => {
  // A panel headed "By venue" showing one row reads just as easily as "here is
  // the first" as it does "this is all of it". It says which.
  const rows = venueBreakdown([{ venue: 'bitget', pnl: 1 }]);
  const f = venueFootnote(rows, 1);
  assert.match(f, /Every closed trade happened on BITGET\./);
  assert.ok(!/other connected/.test(f), 'it invented an idle venue');
});

test('a connected venue that has not traded is NAMED as absent, not silently missing', () => {
  // venueBreakdown omits it on purpose — a row of zeroes beside a venue that
  // really traded invites reading one as the other. But omitting it with no
  // word said makes "connected two, traded on one" look like "connected one".
  const rows = venueBreakdown([{ venue: 'bitget', pnl: 1 }]);
  assert.match(venueFootnote(rows, 2), /1 other connected venue has not traded/);
  assert.match(venueFootnote(rows, 3), /2 other connected venues have not traded/);
});

test('an unreadable connected count omits the clause instead of guessing at it', () => {
  // OMIT, not guard: the table read fine and one dead decoration must not
  // blank it. What it must NOT do is fall back to counting the rows, which
  // would report "connected 1" for a user who connected three.
  const rows = venueBreakdown([{ venue: 'bitget', pnl: 1 }]);
  const f = venueFootnote(rows, null);
  assert.match(f, /Every closed trade happened on BITGET\./);
  assert.ok(!/other connected|0 other/.test(f),
    `an unread connected count produced a claim about it: ${f}`);
});

test('several rows point at where the totals elsewhere on the page come from', () => {
  const rows = venueBreakdown([{ venue: 'bitget', pnl: 1 }, { venue: 'bybit', pnl: 1 }]);
  assert.match(venueFootnote(rows, 2), /cover all 2/);
});

test('no rows means no sentence — there is nothing to caveat', () => {
  assert.equal(venueFootnote([], 2), '');
  assert.equal(venueFootnote(null, null), '');
});


// ── It is actually reachable ─────────────────────────────────────────────

test('the dashboard mounts the panel and ships the file that renders it', () => {
  // #58: a module nothing calls is indistinguishable from one that does not
  // work. Both halves are needed — a loader that calls VenueRows, and a script
  // tag that defines it.
  const dash = fs.readFileSync(
    path.join(__dirname, '..', 'public', 'js', 'dashboard.js'), 'utf8');
  const html = fs.readFileSync(
    path.join(__dirname, '..', 'public', 'dashboard.html'), 'utf8');

  assert.match(dash, /renderPanel\(C\('venuepnl'\)/, 'nothing loads the panel');
  assert.match(dash, /VenueRows\.venueRow\(/, 'the renderer is defined and never called');
  assert.match(dash, /VenueRows\.venueFootnote\(/, 'the footnote is defined and never called');
  assert.match(dash, /id="c-venuepnl"/, 'the loader targets an element the view never renders');
  assert.match(html, /venue-rows\.js\?v=\d+/,
    'the browser never loads venue-rows.js, so VenueRows is undefined at runtime');

  // A changed bundle behind an unchanged ?v= is a deploy that lands and does
  // nothing — CLAUDE.md's "Verifying a deploy" note, one layer up.
  const v = html.match(/dashboard\.js\?v=(\d+)/);
  assert.ok(v && Number(v[1]) >= 150,
    'dashboard.js changed but its cache-buster did not, so browsers keep the old one');
});

test('no two panels in the view claim the same element', () => {
  // This one nearly shipped, and it is worth the space because of HOW it
  // nearly shipped. The first draft of this panel used id="c-venues" — which
  // ALREADY belonged to the connected-venues status panel five hundred lines
  // up, in the same view. `C('venues')` is getElementById, so the two loaders
  // raced for one element and the later one won.
  //
  // The reachability assertions above PASSED throughout: `id="c-venues"` was
  // in the file and `renderPanel(C('venues')` was called. Both were true of
  // somebody else's panel. A scan can see that a string is present; it cannot
  // see who it belongs to — CLAUDE.md's "present is not reached", one turn
  // further in, inside a test written to check exactly that.
  const dash = fs.readFileSync(
    path.join(__dirname, '..', 'public', 'js', 'dashboard.js'), 'utf8');
  const ids = [...dash.matchAll(/id="(c-[a-z0-9-]+)"/g)].map((m) => m[1]);
  const dupes = [...new Set(ids.filter((v, i) => ids.indexOf(v) !== i))];
  assert.deepStrictEqual(dupes, [],
    `two panels mount into the same element, so one overwrites the other: ${dupes}`);
});

test('the panel reads the connected count from the credential route, not from the rows', () => {
  const dash = fs.readFileSync(
    path.join(__dirname, '..', 'public', 'js', 'dashboard.js'), 'utf8');
  const body = dash.slice(dash.indexOf("renderPanel(C('venuepnl')"));
  const loader = body.slice(0, body.indexOf("renderPanel(C('cal')"));
  assert.match(loader, /credentials\/status/,
    'the connected count is inferred rather than read');
  assert.match(loader, /mustRead\(r\)/,
    'a failed /trades/stats renders as "no venues" — the repo\'s central rule');
  assert.match(loader, /\.catch\(\(\) => null\)/,
    'the decorative second fetch is not individually caught, so one dead '
    + 'source blanks a table that read fine');
});
