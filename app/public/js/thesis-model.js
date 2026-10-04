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

  // The marker the thesis prompt asks the model to end with, before the
  // single strongest reason the trade fails. The LAST one counts: a model
  // quoting the word earlier has not started the section. The Python twin
  // is split_counter_case in bot/formatters/thesis_text.py.
  const AGAINST = /\bAgainst:\s*/i;

  /**
   * (thesis, counter-case) from prose that has already had its tag stripped.
   * null on either side means that part was not written. An empty string
   * after the marker is not a counter-case.
   */
  function splitCounterCase(text) {
    if (text === null || text === undefined) return { thesis: null, counter_case: null };
    const body = String(text);
    const marks = [...body.matchAll(new RegExp(AGAINST.source, 'gi'))];
    if (!marks.length) return { thesis: body, counter_case: null };
    const m = marks[marks.length - 1];
    const thesis = body.slice(0, m.index).trim() || null;
    const counter = body.slice(m.index + m[0].length).trim() || null;
    return { thesis, counter_case: counter };
  }

  /**
   * The stored reasoning column, as the two sentences a reader can be shown.
   * Tag-only, blank and null are not a thesis: both sides come back null.
   */
  function storedThesis(reasoning) {
    const body = prose(reasoning);
    if (body === null) return { thesis: null, counter_case: null };
    return splitCounterCase(body);
  }

  return { prose, provenance, PROVENANCE, splitCounterCase, storedThesis, AGAINST };
}));
