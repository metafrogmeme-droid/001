/**
 * RUNECLAW — the model's reasoning, told apart from the tag in front of it.
 *
 * The bot stamps a machine provenance tag onto every idea's reasoning before
 * it is stored, synced and sealed:
 *
 *     [gpt-4o|TREND_UP|swing|momentum|C=0.68 MTF:up] The 4H RSI...
 *
 * and when the model returns a direction and a confidence but no reasoning at
 * all — JSON without the key, plain text without the line, both of which the
 * bot accepts — the whole field is the tag and a trailing space. That string
 * is truthy. `p.thesis ? ... : ''` renders it, under the word "Reasoning", on
 * the one page built so a reader would not have to take the reason on trust.
 *
 * Absent is never a measurement. A model that gave no reason must not render
 * as a model that reasoned, for the same reason an unreadable price must not
 * render as 0.00%.
 *
 * `prose()` returns null for a tag-only string, so the receipt can say the
 * reason was not recorded instead of showing a tag that looks like one. The
 * tag is never removed from the SEALED payload — that is displayed verbatim
 * further down the same page, and the drift check still compares it byte for
 * byte. Only the labelled row changes.
 *
 * The Python twin is bot/formatters/thesis_text.py; the two are pinned against
 * each other by app/test/thesis_prose.test.js, because a receipt that
 * disagrees with the bot about what was said is its own kind of drift.
 *
 * Dual export: browser (window.ThesisModel) + Node (require) for unit tests.
 */
(function (root, factory) {
  const api = factory();
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.ThesisModel = api;
}(typeof self !== 'undefined' ? self : this, function () {
  'use strict';

  // Matched by SHAPE, not by naming the fields — a new segment in the bot's
  // tag must not quietly stop this matching. The pipe inside the brackets is
  // required, so reasoning that opens "[worth noting] the trend is..." is left
  // exactly as the model wrote it.
  const PROVENANCE = /^\s*\[[^[\]|]*\|[^[\]]*\]\s*/;

  /** The model's own words, or null when the string is provenance only. */
  function prose(reasoning) {
    if (reasoning === null || reasoning === undefined) return null;
    const body = String(reasoning).replace(PROVENANCE, '').trim();
    return body || null;
  }

  /** The bracketed tag's interior, or null when there is no tag. */
  function provenance(reasoning) {
    if (reasoning === null || reasoning === undefined) return null;
    const m = PROVENANCE.exec(String(reasoning));
    if (!m) return null;
    const inner = m[0].trim().replace(/^\[/, '').replace(/\]$/, '').trim();
    return inner || null;
  }

  return { prose, provenance, PROVENANCE };
}));
