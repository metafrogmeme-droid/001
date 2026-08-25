'use strict';
/**
 * The operator can SEE the seasons they authored, and remove a mistake.
 *
 * A season was created and the operator reported "I made a new one and started
 * today, I don't see it". Nothing in the API could answer that. `/season`
 * returns only the current pick; `/seasons` lists only ENDED ones. So an
 * upcoming season — or one whose dates came out wrong — was invisible on every
 * surface, INCLUDING to the person who created it, and there was no way to ask
 * whether it existed, when it ran, or why it was not winning.
 *
 * A create endpoint with no read is a write into the dark. These tests cover
 * the read, and the delete that lets a mis-authored window be undone.
 */

const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const { codeOnly } = require('./helpers/code_only');
const SRC = codeOnly(fs.readFileSync(
  path.join(__dirname, '..', 'routes', 'arena.js'), 'utf8'));

/**
 * One route's body — bounded by the NEXT route, not by a character count.
 *
 * The first version took a fixed 2600 chars from the marker, which ran past the
 * end of the handler and into whatever followed. A mutation proved the cost:
 * deleting the admin check from DELETE /seasons/:id changed nothing, because
 * the window reached into POST /season and matched ITS `adminOnly` call. The
 * test was reading a neighbour's guard and crediting it to this route — the
 * strongest assertion in the file, passing for the wrong reason, on the route
 * that destroys rows.
 *
 * Bounded properly, each route is checked against its own body only.
 */
const slice = (marker) => {
  const at = SRC.indexOf(marker);
  assert.ok(at > 0, `${marker} is gone`);
  const rest = SRC.slice(at + marker.length);
  const next = rest.search(/\nrouter\.(get|post|put|patch|delete)\(|\nasync function |\nmodule\.exports/);
  return SRC.slice(at, next === -1 ? SRC.length : at + marker.length + next);
};

// ── both new routes are admin-gated ───────────────────────────────────────

test('listing and deleting seasons both require the operator', () => {
  // They expose unannounced product decisions and they destroy rows. Neither
  // is a public read, and the check must be on the route rather than assumed
  // from the path prefix — /api/arena has public routes on it.
  for (const marker of ["router.get('/seasons/all'", "router.delete('/seasons/:id'"]) {
    const body = slice(marker);
    assert.match(body.slice(0, 120), /authMiddleware/, `${marker} has no authMiddleware`);
    assert.match(body, /adminOnly\(req, res\)/, `${marker} does not check for admin`);
  }
});

test('the admin check is written once, not three times', () => {
  // Three copies of an authorisation check is three chances to relax one.
  const inlineChecks = (SRC.match(/String\(u\[0\]\.plan\) !== 'admin'/g) || []).length;
  assert.equal(inlineChecks, 1,
    `${inlineChecks} inline admin checks — they should all route through adminOnly()`);
  assert.ok((SRC.match(/adminOnly\(req, res\)/g) || []).length >= 3,
    'the shared admin check is not used by all three admin routes');
});

test('adminOnly denies by default when the row is missing', () => {
  // A user id that resolves to nothing is not an operator. `!u[0] ||` is what
  // makes an absent row a refusal instead of a crash that some outer catch
  // turns into a 500 — or worse, a truthy skip.
  const fn = slice('async function adminOnly');
  assert.match(fn, /!u\[0\] \|\|/, 'a missing user row no longer denies');
  assert.match(fn, /return null/);
});

// ── the listing answers the question that was asked ───────────────────────

test('the listing reports every season with its status', () => {
  const body = slice("router.get('/seasons/all'");
  // No WHERE, no LIMIT: the whole point is to show what EXISTS, including the
  // upcoming season that no other surface will admit to.
  assert.match(body, /FROM arena_seasons/);
  assert.doesNotMatch(body.slice(0, 900), /WHERE|LIMIT 1/,
    'the listing filters rows out — it exists to show all of them');
  assert.match(body, /seasons\.seasonStatus\(r, now\)/,
    'rows carry no status, so an operator cannot tell upcoming from live');
});

test('the listing says WHICH season is actually in force', () => {
  // With two live seasons an operator cannot otherwise tell which one the board
  // and the trade gate are using — which is the question that brought them here.
  const body = slice("router.get('/seasons/all'");
  assert.match(body, /is_current/);
  assert.match(body, /pickCurrentSeason\(rows, now\)/,
    'is_current is computed some other way than the picker the board uses, so '
    + 'the two can disagree');
});

test('an unreadable listing is 503, never an empty list', () => {
  // "No seasons exist" and "we could not read them" are different answers, and
  // the empty one sends the operator to create a duplicate of a season that is
  // already there.
  const body = slice("router.get('/seasons/all'");
  assert.match(body, /503/);
  assert.match(body, /seasons_unavailable/);
  assert.doesNotMatch(body, /res\.json\(\{ seasons: \[\] \}\)/,
    'a read failure renders as "there are none"');
});

// ── delete refuses the two cases that would cause harm ────────────────────

test('the live season cannot be deleted', () => {
  // Deleting it silently changes which rules gate every open — the exact
  // confusion these endpoints exist to end.
  const body = slice("router.delete('/seasons/:id'");
  assert.match(body, /=== 'live'/);
  assert.match(body, /409/);
  assert.match(body, /season_is_live/);
});

test('deleting a season that does not exist is 404, not a silent success', () => {
  // Reporting "deleted" for a row that was never there tells the operator
  // their mistake is cleaned up when it is not.
  const body = slice("router.delete('/seasons/:id'");
  assert.match(body, /no_such_season/);
  assert.match(body, /404/);
});

test('a non-numeric id is refused before it reaches the database', () => {
  const body = slice("router.delete('/seasons/:id'");
  assert.match(body, /Number\.isInteger\(id\)/);
  assert.match(body, /bad_id/);
});

test('delete removes the season row and nothing else', () => {
  // Trades are NOT season-owned — arena_trades carries no season id, and every
  // ranking matches closed_at against a window. Deleting a season removes a
  // WINDOW; the trades inside it keep existing. A DELETE that also touched
  // arena_trades would discard real results to undo an authoring typo.
  const body = slice("router.delete('/seasons/:id'");
  assert.match(body, /DELETE FROM arena_seasons WHERE id = \?/);
  assert.doesNotMatch(body, /DELETE FROM arena_trades|DELETE FROM arena_positions/,
    'deleting a season is discarding trade history');
});

test('the delete reports what it removed', () => {
  // So the operator can confirm they removed the season they meant to, rather
  // than reading a bare 200 and hoping.
  const body = slice("router.delete('/seasons/:id'");
  assert.match(body, /deleted: \{ id: row\.id, name: row\.name/);
});

// ── the public surfaces are unchanged ─────────────────────────────────────

test('the public season endpoints did not gain an auth requirement', () => {
  // The board is public and must stay public; adding the admin pair must not
  // have moved the line.
  for (const marker of ["router.get('/season'", "router.get('/seasons'"]) {
    const head = slice(marker).slice(0, 100);
    assert.doesNotMatch(head, /authMiddleware/, `${marker} became authenticated`);
    assert.match(head, /publicBoardLimit/, `${marker} lost its rate limit`);
  }
});
