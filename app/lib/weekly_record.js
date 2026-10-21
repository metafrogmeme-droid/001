'use strict';
/**
 * The weekly public record, as GET /api/public/record serves it.
 *
 * `scripts/weekly_record.py` writes one file per ISO week into
 * `benchmark/record/` on the bot's server (`bot/core/public_record.py` builds
 * it), the operator commits it, and `app/scripts/sync_content.js` copies it
 * into `app/content/record/`, which is what this reads: the web deploy ships
 * `app/` alone. A committed week does not move, so the page shows what was
 * frozen that week, never a live read of the bot.
 *
 * NO WEEK YET IS NOT AN ERROR, AND AN UNREADABLE WEEK IS NOT "NO WEEK". The
 * folder is absent until the first week is committed (git keeps no empty
 * folder), and `weeks: []` says exactly that. Any other failure to list or
 * read throws, and the route answers 503 with the class.
 *
 * WHAT IS SERVED IS AN ALLOWLIST, as for the studies (app/lib/studies.js):
 * the page is public, so percent, ratio and count only. Each section keeps
 * the fields named below and nothing it gains later; a value of the wrong
 * type is left out, so the panel says "not recorded" for it rather than
 * printing whatever arrived. A section the script could not read keeps its
 * state and the exception's class, and nothing else.
 *
 * The sleeve section is counts and dates only, and this keeps it so: its
 * pre-registration forbids looking at the rows before 2027-10-17, so no
 * holding, weight or return is forwarded even if a file carries one.
 */

const fs = require('node:fs');
const path = require('node:path');

const RECORD_DIR = path.join(__dirname, '..', 'content', 'record');
const FORMAT = 'runeclaw.public_record.v1';
const WEEK = /^\d{4}-W\d{2}$/;
const DAY = /^\d{4}-\d{2}-\d{2}$/;
const STATES = ['read', 'absent', 'unreadable'];

class WeeklyRecordInvalid extends Error {
  constructor(message) {
    super(message);
    this.name = 'WeeklyRecordInvalid';
  }
}

const isObject = (v) => v !== null && typeof v === 'object' && !Array.isArray(v);
const finite = (v) => typeof v === 'number' && Number.isFinite(v);
const count = (v) => Number.isInteger(v) && v >= 0;

/** Fields per section: [name, test]. A value that fails its test is left out. */
const SLEEVE = [
  ['measured', (v) => typeof v === 'boolean'],
  ['measured_on', (v) => typeof v === 'string' && DAY.test(v)],
  ['prereg', (v) => typeof v === 'string' && /^docs\/research\/[A-Za-z0-9_./-]+\.md$/.test(v) && !v.includes('..')],
  ['places_orders', (v) => typeof v === 'boolean'],
  ['days_recorded', count], ['gap_days', count], ['skipped_rows', count],
  ['first_day', (v) => typeof v === 'string' && DAY.test(v)],
  ['last_day', (v) => typeof v === 'string' && DAY.test(v)],
];
const EQUITY = [
  ['book', (v) => v === 'live'],
  ['min_daily_returns', count], ['flows_separated', (v) => typeof v === 'boolean'],
  ['days_recorded', count], ['gap_days', count], ['skipped_rows', count], ['daily_returns', count],
  ['first_day', (v) => typeof v === 'string' && DAY.test(v)],
  ['last_day', (v) => typeof v === 'string' && DAY.test(v)],
  ['return_pct', finite], ['max_drawdown_pct', finite], ['sharpe', finite],
];
const PARITY = [
  ['trades', count], ['excluded_non_fills', count], ['unscored_pnl', count], ['aborts', count],
  ['fees_read', count], ['fees_stated', count], ['inferred_fills', count],
  ['win_rate', finite], ['pf', finite], ['realized_fee_rate', finite], ['modeled_fee_rate', finite],
  ['fee_vs_model', finite], ['fee_drag_of_gross', finite],
];
const WORD = (v) => typeof v === 'string' && /^[a-z_]{1,32}$/.test(v);
const VERDICT = [['edge', WORD], ['ballpark', WORD], ['n', count], ['in_universe', count],
  ['outside', count], ['inferred', count], ['min_trades', count]];
const BENCHMARK = [['state', WORD], ['dataset', (v) => typeof v === 'string' && /^[A-Za-z0-9_][A-Za-z0-9_./-]{0,63}$/.test(v) && !v.includes('..')],
  ['folds_run', count], ['profitable_folds', count], ['mean_oos_return_pct', finite],
  ['pooled_trades', count], ['pooled_win_rate', finite], ['pooled_pf', finite]];

function pick(src, fields) {
  const out = {};
  if (!isObject(src)) return out;
  for (const [k, ok] of fields) if (ok(src[k])) out[k] = src[k];
  return out;
}

/** The state every section carries, and for an unreadable one only its class. */
function head(sec) {
  if (!isObject(sec) || !STATES.includes(sec.state)) {
    return { state: 'unreadable', exception: 'WeeklyRecordInvalid' };
  }
  if (sec.state === 'unreadable') {
    return { state: 'unreadable',
      exception: typeof sec.exception === 'string' && /^\w{1,64}$/.test(sec.exception) ? sec.exception : null };
  }
  return null;
}

function sleeve(sec) {
  const h = head(sec);
  if (h) return h;
  return { state: sec.state, ...pick(sec, SLEEVE) };
}

function equity(sec) {
  return head(sec) || { state: sec.state, ...pick(sec, EQUITY) };
}

function parity(sec) {
  const h = head(sec);
  if (h) return h;
  const out = { state: sec.state, ...pick(sec, PARITY) };
  if (isObject(sec.verdict)) out.verdict = pick(sec.verdict, VERDICT);
  if (isObject(sec.benchmark)) out.benchmark = pick(sec.benchmark, BENCHMARK);
  return out;
}

/** The committed weeks, newest first. No folder is no week committed yet. */
function listWeeks(dir) {
  let names;
  try {
    names = fs.readdirSync(dir || RECORD_DIR);
  } catch (err) {
    if (err && err.code === 'ENOENT') return [];
    throw err;
  }
  return names.filter((n) => /^\d{4}-W\d{2}\.json$/.test(n)).map((n) => n.slice(0, -5)).sort().reverse();
}

/** One week, validated. A missing file or bad JSON throws as itself. */
function readWeek(week, dir) {
  if (!WEEK.test(String(week))) throw new WeeklyRecordInvalid('not an ISO week');
  const data = JSON.parse(fs.readFileSync(path.join(dir || RECORD_DIR, week + '.json'), 'utf8'));
  if (!isObject(data)) throw new WeeklyRecordInvalid('the record is not an object');
  if (data.format !== FORMAT) throw new WeeklyRecordInvalid('the record format is not ' + FORMAT);
  if (data.week !== week) throw new WeeklyRecordInvalid('the file is named for a week it does not hold');
  return data;
}

/** What the page is sent for one week. */
function publicWeek(rec) {
  return {
    week: rec.week,
    as_of: typeof rec.as_of === 'string' ? rec.as_of : null,
    week_starts: typeof rec.week_starts === 'string' && DAY.test(rec.week_starts) ? rec.week_starts : null,
    sleeve: sleeve(rec.sleeve),
    equity: equity(rec.equity),
    parity: parity(rec.parity),
  };
}

/** What GET /api/public/record answers: the weeks, and one of them in full. */
function publicRecord({ week, dir } = {}) {
  const weeks = listWeeks(dir);
  const want = week || weeks[0];
  if (week && !weeks.includes(week)) return { format: FORMAT, weeks, record: null, unknown_week: true };
  return {
    format: FORMAT,
    weeks,
    record: want ? publicWeek(readWeek(want, dir)) : null,
    source: 'https://github.com/metafrogmeme-droid/001/tree/main/benchmark/record',
  };
}

module.exports = {
  RECORD_DIR, FORMAT, WEEK, WeeklyRecordInvalid,
  listWeeks, readWeek, publicWeek, publicRecord,
};
