/**
 * The Agent Letter — a weekly fund-style letter written from REAL data.
 *
 * Every number and every sentence is derived from what the bot actually
 * recorded during the ISO week: closed trades, equity snapshots, the signal
 * stream, the public agent feed, and the bot's intelligence reports. The
 * phrasing adapts to the numbers (a losing week reads like a losing week),
 * but nothing is invented — no data for a section means the section says so.
 *
 * Letters are generated once per completed ISO week (UTC, Mon..Sun),
 * stored in agent_letters, and announced with a web push. Deterministic:
 * regenerating the same week from the same data yields the same letter.
 */

const { pool } = require('../db');
const { winStats, realizedTotal, profitFactor } = require('../public/js/trade-stats');

const OPERATOR_USER_ID = parseInt(process.env.BOT_USER_ID) || 1;

function round2(v) { return Math.round(v * 100) / 100; }

function money(v) {
  const n = Number(v) || 0;
  return (n < 0 ? '-$' : '$') + Math.abs(n).toLocaleString('en-US', { maximumFractionDigits: 2 });
}

function esc(s) {
  return String(s ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

/**
 * The week's figures, computed once for both composers.
 *
 * Both used to open with
 *
 *     const pnls = trades.map(t => parseFloat(t.pnl) || 0);
 *
 * and `trades.length - wins` for the L. `trades.pnl` is nullable, so that
 * summed every unpriced close as a break-even, scored it as a loss, and let
 * it compete for "best trade of the week" at $0.00. Three wrong claims about
 * one row, in a letter that goes to the operator AND out publicly.
 *
 * Rows leave the series; they do not zero it. `scored` and `unpriced` travel
 * with the figures so the caller can disclose the window rather than print a
 * partial total as a whole one.
 */
function weekFigures(trades) {
  const rows = trades || [];
  const ws = winStats(rows);
  const total = realizedTotal(rows);
  let best = null, worst = null;
  for (const t of rows) {
    const p = Number.isFinite(parseFloat(t.pnl)) ? parseFloat(t.pnl) : null;
    if (p === null) continue;                 // never the week's best at $0.00
    if (!best || p > best.pnl) best = { symbol: t.symbol, pnl: p };
    if (!worst || p < worst.pnl) worst = { symbol: t.symbol, pnl: p };
  }
  return {
    net: total === null ? null : round2(total),
    wins: ws.wins,
    losses: ws.losses,
    flat: ws.breakeven,
    scored: ws.scored,
    unpriced: ws.unscored,
    // Over what we could price, not over what closed.
    wr: ws.rate === null ? null : Math.round(ws.rate * 100),
    pf: profitFactor(rows),
    best,
    worst,
  };
}

/** The disclosure for a week only partly priceable. Empty when it is whole. */
function coverageLine(f, n) {
  if (!f.unpriced) return '';
  return ` (${f.scored} of ${n} closes carry a recorded P&amp;L; the rest are `
    + 'scored neither way)';
}

// ── ISO week math (UTC) ──────────────────────────────────────────────────────

/** ISO-8601 week key ('2026-W29') for a Date, computed in UTC. */
function weekKey(date) {
  const d = new Date(Date.UTC(date.getUTCFullYear(), date.getUTCMonth(), date.getUTCDate()));
  const day = d.getUTCDay() || 7;               // Mon=1..Sun=7
  d.setUTCDate(d.getUTCDate() + 4 - day);       // nearest Thursday
  const yearStart = new Date(Date.UTC(d.getUTCFullYear(), 0, 1));
  const week = Math.ceil(((d - yearStart) / 86_400_000 + 1) / 7);
  return `${d.getUTCFullYear()}-W${String(week).padStart(2, '0')}`;
}

/** The last COMPLETED ISO week relative to `now`: { key, start, end }. */
function lastCompletedWeek(now = new Date()) {
  const d = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate()));
  const day = d.getUTCDay() || 7;
  // Monday of the CURRENT week, then step back one week.
  const currentMonday = new Date(d);
  currentMonday.setUTCDate(d.getUTCDate() - (day - 1));
  const start = new Date(currentMonday);
  start.setUTCDate(currentMonday.getUTCDate() - 7);
  const end = currentMonday;                     // exclusive
  return { key: weekKey(start), start, end };
}

// ── Data gathering (all fail-soft) ───────────────────────────────────────────

async function loadWeekData(start, end) {
  const inWindow = (ts) => {
    const t = new Date(ts).getTime();
    return t >= start.getTime() && t < end.getTime();
  };

  let trades = [];
  try {
    const [rows] = await pool.execute(
      `SELECT symbol, direction, entry_price, exit_price, pnl, fees, size_usd, opened_at, closed_at
         FROM trades
        WHERE user_id = ? AND status = 'CLOSED' AND closed_at IS NOT NULL
        ORDER BY closed_at ASC`, [OPERATOR_USER_ID]);
    trades = rows.filter(t => inWindow(t.closed_at));
  } catch (e) { /* section reports no data */ }

  let equity = { start: null, end: null };
  try {
    const [snaps] = await pool.execute(
      'SELECT equity, snapshot_at FROM equity_snapshots WHERE user_id = ? ORDER BY snapshot_at ASC',
      [OPERATOR_USER_ID]);
    for (const s of snaps) {
      const t = new Date(s.snapshot_at).getTime();
      const v = parseFloat(s.equity);
      if (!isFinite(v)) continue;
      if (t < end.getTime()) equity.end = v;         // last one before week end
      if (t < start.getTime()) equity.start = v;     // last one before week start
    }
    // A week with snapshots only inside it: use the first in-window as start.
    if (equity.start === null) {
      const first = snaps.find(s => inWindow(s.snapshot_at));
      if (first) equity.start = parseFloat(first.equity);
    }
  } catch (e) { /* no equity section */ }

  let signals = [];
  try {
    const [rows] = await pool.execute(
      `SELECT symbol, direction, confidence, regime, created_at
         FROM signals ORDER BY created_at DESC LIMIT 500`, []);
    signals = rows.filter(s => inWindow(s.created_at));
  } catch (e) { /* no signals section */ }

  let openCount = 0;
  try {
    const [r] = await pool.execute(
      "SELECT COUNT(*) AS open_count FROM trades WHERE user_id = ? AND status = 'OPEN'",
      [OPERATOR_USER_ID]);
    openCount = parseInt(r[0]?.open_count) || 0;
  } catch (e) { /* stays 0 */ }

  let reports = null;
  try {
    const [r] = await pool.execute('SELECT reports_json FROM reports_cache WHERE id = 1');
    if (r.length && r[0].reports_json) reports = JSON.parse(r[0].reports_json);
  } catch (e) { /* no reports section */ }

  return { trades, equity, signals, openCount, reports };
}

// ── Composition (pure, deterministic) ────────────────────────────────────────

/**
 * Alpha-vs-holding section, shared verbatim by the private AND public letter:
 * it is percent-only by construction (never a dollar figure), so it clears the
 * public letter's privacy line without a second composition. Returns null when
 * no trade in the window carries usable entry/exit prices.
 */
function composeAlphaSection(trades) {
  const { computeIntel } = require('./intel');
  const a = computeIntel(trades).alpha;
  if (!a || a.priced < 1) return null;
  const sign = a.mean_alpha_pct >= 0 ? '+' : '';
  const bits = [
    `Against simply buying and holding each asset over the same windows, the desk ran `
      + `<b>${sign}${a.mean_alpha_pct}%</b> of pure alpha per trade — `
      + `${a.beat_market} of ${a.priced} closes beat their own market.`,
    a.best && a.best.alpha_pct > 0
      ? `Cleanest edge: ${esc(a.best.symbol)} (+${a.best.alpha_pct}% vs holding).` : null,
    a.unpriced ? `${a.unpriced} close${a.unpriced === 1 ? '' : 's'} lacked recorded prices and sat out the comparison.` : null,
  ].filter(Boolean).join(' ');
  // `parts` mirrors `html`, unassembled: one {tid, params} per sentence, so a
  // renderer can rebuild the section in another language. Additive — `html`
  // stays the stored/English truth, and letters stored before parts existed
  // simply render their English.
  const parts = [
    { tid: 'alpha_main', params: { a: sign + a.mean_alpha_pct, b: a.beat_market, p: a.priced } },
    a.best && a.best.alpha_pct > 0
      ? { tid: 'alpha_best', params: { sym: String(a.best.symbol), a: a.best.alpha_pct } } : null,
    a.unpriced ? { tid: 'alpha_unpriced', params: { n: a.unpriced } } : null,
  ].filter(Boolean);
  return { title: 'Alpha vs holding', title_tid: 'alpha', html: bits, parts, sep: ' ' };
}

function composeLetter({ key, start, end }, data) {
  const { trades, equity, signals, openCount, reports } = data;
  const fmtDay = (d) => d.toISOString().slice(0, 10);
  const endInclusive = new Date(end.getTime() - 86_400_000);

  const f = weekFigures(trades);
  const { wins, losses, scored, unpriced, wr, best, worst } = f;
  const net = f.net;
  const pf = f.pf === null ? null : round2(f.pf);

  const sections = [];

  // ── The week ──
  let deskLine, deskPart;
  if (!trades.length) {
    deskLine = 'The desk closed no positions this week — patience is a position too, '
      + 'and the risk gate saw nothing worth paying fees for.';
    deskPart = { tid: 'week_flat', params: {} };
  } else if (!scored) {
    // Closes happened and none of them can be priced. Every branch below
    // states a result; this one is the absence of one, and without it the
    // week fell through to "A losing week, plainly" — a verdict manufactured
    // from a gap in the record.
    deskLine = `${trades.length} positions closed this week and none of them `
      + 'carry a recorded P&amp;L, so there is no result to report — not a flat '
      + 'week, an unreadable one.';
    deskPart = { tid: 'week_unpriced', params: { n: trades.length } };
  } else if (net > 0 && (wr ?? 0) >= 60) {
    deskLine = `A clean week: ${trades.length} closed trades, ${wr}% winners, `
      + `<b>${money(net)}</b> net after fees.`;
    deskPart = { tid: 'week_clean_priv', params: { n: trades.length, wr, net: money(net) } };
  } else if (net > 0) {
    deskLine = `A grinder's week: ${trades.length} closed trades and only ${wr}% winners, `
      + `but the winners paid for the losers — <b>${money(net)}</b> net.`;
    deskPart = { tid: 'week_grind_priv', params: { n: trades.length, wr, net: money(net) } };
  } else {
    deskLine = `A losing week, plainly: ${trades.length} closed trades, ${wr}% winners, `
      + `<b>${money(net)}</b> net. The numbers below are the honest post-mortem.`;
    deskPart = { tid: 'week_loss_priv', params: { n: trades.length, wr, net: money(net) } };
  }
  sections.push({ title: 'The week', title_tid: 'week', html: deskLine, parts: [deskPart], sep: ' ' });

  // ── Performance ──
  if (scored) {
    const bits = [
      // Counted, not subtracted, and the total names the window it covers.
      `Net PnL <b>${money(net)}</b> across ${scored} closes (${wins}W/${losses}L)`
        + coverageLine(f, trades.length),
      pf !== null ? `profit factor ${pf}` : null,
      best ? `best: ${esc(String(best.symbol).split('/')[0])} ${money(best.pnl)}` : null,
      worst && worst.pnl < 0 ? `worst: ${esc(String(worst.symbol).split('/')[0])} ${money(worst.pnl)}` : null,
    ].filter(Boolean).join(' · ');
    sections.push({ title: 'Performance', title_tid: 'performance', html: bits, sep: ' · ', parts: [
      { tid: 'perf_net_priv', params: { net: money(net), n: scored, w: wins, l: losses } },
      pf !== null ? { tid: 'perf_pf', params: { pf } } : null,
      best ? { tid: 'perf_best_p', params: { sym: String(best.symbol).split('/')[0], pnl: money(best.pnl) } } : null,
      worst && worst.pnl < 0 ? { tid: 'perf_worst_p', params: { sym: String(worst.symbol).split('/')[0], pnl: money(worst.pnl) } } : null,
    ].filter(Boolean) });
  }
  if (equity.start !== null && equity.end !== null && equity.start > 0) {
    const delta = round2(equity.end - equity.start);
    const pct = round2(delta / equity.start * 100);
    sections.push({
      title: 'Equity', title_tid: 'equity',
      html: `${money(equity.start)} → <b>${money(equity.end)}</b> `
        + `(${delta >= 0 ? '+' : ''}${money(delta).replace('$', '$')} · ${pct >= 0 ? '+' : ''}${pct}%).`,
      sep: ' ',
      parts: [{ tid: 'eq_priv', params: { a: money(equity.start), b: money(equity.end),
        d: (delta >= 0 ? '+' : '') + money(delta), pct: (pct >= 0 ? '+' : '') + pct } }],
    });
  }

  // ── Alpha vs holding — did the week's trading beat simply holding what it
  // traded? Reconstructed from each trade's own recorded entry/exit prices.
  const alphaSection = composeAlphaSection(trades);
  if (alphaSection) sections.push(alphaSection);

  // ── The tape ──
  if (signals.length) {
    const longs = signals.filter(s => String(s.direction).toUpperCase().includes('LONG')).length;
    const regimes = {};
    for (const s of signals) {
      const r = String(s.regime || '').trim();
      if (r) regimes[r] = (regimes[r] || 0) + 1;
    }
    const topRegime = Object.entries(regimes).sort((a, b) => b[1] - a[1])[0];
    sections.push({
      title: 'The tape', title_tid: 'tape',
      html: `${signals.length} signals generated (${longs} long / ${signals.length - longs} short)`
        + (topRegime ? ` — the dominant read was <b>${esc(topRegime[0])}</b> `
          + `(${topRegime[1]} of ${signals.length}).` : '.'),
      sep: '',
      parts: [
        { tid: 'tape_n', params: { n: signals.length, lo: longs, sh: signals.length - longs } },
        topRegime ? { tid: 'tape_regime', params: { r: topRegime[0], c: topRegime[1], n: signals.length } }
          : { tid: 'tape_dot', params: {} },
      ],
    });
  } else {
    sections.push({
      title: 'The tape', title_tid: 'tape', sep: ' ',
      html: 'No signals recorded this week — either a quiet tape or the engine was resting.',
      parts: [{ tid: 'tape_none', params: {} }],
    });
  }

  // ── Side desks (bot intelligence reports, if the bot pushed them) ──
  if (reports) {
    const bits = [];
    const sideParts = [];
    try {
      const arb = reports.arb || {};
      if (arb.total_accrued_usd != null) {
        bits.push(`the funding-arb PAPER tracker has accrued ${money(arb.total_accrued_usd)} of hypothetical carry`);
        sideParts.push({ tid: 'side_arb', params: { m: money(arb.total_accrued_usd) } });
      }
      const parity = reports.parity || {};
      if (parity.verdict) {
        bits.push(`live↔backtest parity reads <b>${esc(parity.verdict)}</b>`);
        sideParts.push({ tid: 'side_parity', params: { v: parity.verdict } });
      }
    } catch (e) { /* skip */ }
    if (bits.length) {
      sections.push({ title: 'Side desks', title_tid: 'side',
        html: bits.join('; ') + '.', parts: sideParts, sep: '; ', end: '.' });
    }
  }

  // ── Looking ahead ──
  sections.push({
    title: 'Looking ahead', title_tid: 'ahead', sep: ' ',
    html: (openCount
      ? `The desk carries <b>${openCount}</b> open position${openCount === 1 ? '' : 's'} into the new week, each with a hard stop working. `
      : 'The desk enters the week flat. ')
      + 'Same discipline as always: no trade without a stop, no size without conviction, '
      + 'and the risk gate has the final word.',
    parts: [
      openCount ? { tid: 'ahead_open', params: { n: openCount } } : { tid: 'ahead_flat', params: {} },
      { tid: 'ahead_discipline', params: {} },
    ],
  });

  // The headline is composed separately from the body and was missed by the
  // body's fix — it read "$0 net — null% winners" for an unpriceable week,
  // which is the same fabrication with a `null` left visible in it. It is
  // also the line that goes out as the push notification.
  const headline = !trades.length
    ? 'A flat week, by choice'
    : !scored ? `${trades.length} closes, no recorded P&L — no result to report`
      : net >= 0 ? `${money(net)} net — ${wr}% winners`
        : `${money(net)} net — the honest post-mortem`;
  const headline_part = !trades.length
    ? { tid: 'h_flat', params: {} }
    : !scored ? { tid: 'h_unpriced', params: { n: trades.length } }
      : net >= 0 ? { tid: 'h_net_win', params: { net: money(net), wr } }
        : { tid: 'h_net_loss', params: { net: money(net) } };

  return {
    week_key: key,
    period: { start: fmtDay(start), end: fmtDay(endInclusive) },
    headline, headline_part,
    sections,
    footer: 'Every figure above is derived from recorded trades and snapshots — nothing hand-written. '
      + 'Past performance does not predict future results.',
    footer_tid: 'f_priv',
  };
}

// ── Public variant (dollar-free) ─────────────────────────────────────────────

/** Inverse of weekKey: '2026-W29' -> { key, start (Mon, UTC), end (next Mon,
 *  exclusive) } or null when malformed. */
function weekRangeFromKey(key) {
  const m = /^(\d{4})-W(\d{2})$/.exec(String(key || ''));
  if (!m) return null;
  const year = parseInt(m[1]), week = parseInt(m[2]);
  if (week < 1 || week > 53) return null;
  // ISO: week 1 contains Jan 4. Monday of week 1, then step forward.
  const jan4 = new Date(Date.UTC(year, 0, 4));
  const day = jan4.getUTCDay() || 7;
  const week1Monday = new Date(jan4);
  week1Monday.setUTCDate(jan4.getUTCDate() - (day - 1));
  const start = new Date(week1Monday);
  start.setUTCDate(week1Monday.getUTCDate() + (week - 1) * 7);
  if (weekKey(start) !== `${m[1]}-W${m[2]}`) return null;   // e.g. W53 in a 52-week year
  const end = new Date(start);
  end.setUTCDate(start.getUTCDate() + 7);
  return { key: String(key), start, end };
}

/**
 * The PUBLIC letter: same recorded data, recomposed with NO dollar figure —
 * counts, win rate, profit factor, equity PERCENT change, regime reads. Never
 * derived by stripping the private letter's HTML (too fragile to trust with a
 * privacy line); this is a parallel composition from the same loadWeekData.
 */
function composePublicLetter({ key, start, end }, data) {
  const { trades, equity, signals, openCount, reports } = data;
  const fmtDay = (d) => d.toISOString().slice(0, 10);
  const endInclusive = new Date(end.getTime() - 86_400_000);

  // Same reader as the private composer above. Two letters describing one
  // week must not be able to disagree about which closes were legible — and
  // this one is PUBLIC, so a fabricated verdict here is published.
  const f = weekFigures(trades);
  const { wins, losses, scored, wr, best, worst } = f;
  const net = f.net;
  const pf = f.pf === null ? null : round2(f.pf);

  const sections = [];

  let deskLine, deskPart;
  if (!trades.length) {
    deskLine = 'The desk closed no positions this week — patience is a position too, '
      + 'and the risk gate saw nothing worth paying fees for.';
    deskPart = { tid: 'week_flat', params: {} };
  } else if (!scored) {
    deskLine = `${trades.length} positions closed this week and none of them `
      + 'carry a recorded P&amp;L, so there is no result to report — not a flat '
      + 'week, an unreadable one.';
    deskPart = { tid: 'week_unpriced', params: { n: trades.length } };
  } else if (net > 0 && (wr ?? 0) >= 60) {
    deskLine = `A clean week: ${trades.length} closed trades, <b>${wr}% winners</b>, `
      + 'finished green.';
    deskPart = { tid: 'week_clean_pub', params: { n: trades.length, wr } };
  } else if (net > 0) {
    deskLine = `A grinder's week: ${trades.length} closed trades and only ${wr}% winners, `
      + 'but the winners paid for the losers — finished green.';
    deskPart = { tid: 'week_grind_pub', params: { n: trades.length, wr } };
  } else {
    deskLine = `A losing week, plainly: ${trades.length} closed trades, ${wr}% winners, `
      + 'finished red. The reads below are the honest post-mortem.';
    deskPart = { tid: 'week_loss_pub', params: { n: trades.length, wr } };
  }
  sections.push({ title: 'The week', title_tid: 'week', html: deskLine, parts: [deskPart], sep: ' ' });

  if (scored) {
    const bits = [
      `${scored} closes (${wins}W/${losses}L)` + coverageLine(f, trades.length),
      pf !== null ? `profit factor <b>${pf}</b>` : 'no losing trades',
      best ? `best: ${esc(String(best.symbol).split('/')[0])}` : null,
      worst && worst.pnl < 0 ? `worst: ${esc(String(worst.symbol).split('/')[0])}` : null,
    ].filter(Boolean).join(' · ');
    sections.push({ title: 'Performance', title_tid: 'performance', html: bits, sep: ' · ', parts: [
      { tid: 'perf_closes_pub', params: { n: scored, w: wins, l: losses } },
      pf !== null ? { tid: 'perf_pf', params: { pf } } : { tid: 'perf_noloss', params: {} },
      best ? { tid: 'perf_best', params: { sym: String(best.symbol).split('/')[0] } } : null,
      worst && worst.pnl < 0 ? { tid: 'perf_worst', params: { sym: String(worst.symbol).split('/')[0] } } : null,
    ].filter(Boolean) });
  }
  if (equity.start !== null && equity.end !== null && equity.start > 0) {
    const pct = round2((equity.end - equity.start) / equity.start * 100);
    sections.push({
      title: 'Equity', title_tid: 'equity', sep: ' ',
      html: `Equity moved <b>${pct >= 0 ? '+' : ''}${pct}%</b> on the week.`,
      parts: [{ tid: 'eq_pub', params: { pct: (pct >= 0 ? '+' : '') + pct } }],
    });
  }

  // Percent-only by construction (see composeAlphaSection) — safe here.
  const alphaSection = composeAlphaSection(trades);
  if (alphaSection) sections.push(alphaSection);

  if (signals.length) {
    const longs = signals.filter(s => String(s.direction).toUpperCase().includes('LONG')).length;
    const regimes = {};
    for (const s of signals) {
      const r = String(s.regime || '').trim();
      if (r) regimes[r] = (regimes[r] || 0) + 1;
    }
    const topRegime = Object.entries(regimes).sort((a, b) => b[1] - a[1])[0];
    sections.push({
      title: 'The tape', title_tid: 'tape',
      html: `${signals.length} signals generated (${longs} long / ${signals.length - longs} short)`
        + (topRegime ? ` — the dominant read was <b>${esc(topRegime[0])}</b> `
          + `(${topRegime[1]} of ${signals.length}).` : '.'),
      sep: '',
      parts: [
        { tid: 'tape_n', params: { n: signals.length, lo: longs, sh: signals.length - longs } },
        topRegime ? { tid: 'tape_regime', params: { r: topRegime[0], c: topRegime[1], n: signals.length } }
          : { tid: 'tape_dot', params: {} },
      ],
    });
  } else {
    sections.push({
      title: 'The tape', title_tid: 'tape', sep: ' ',
      html: 'No signals recorded this week — either a quiet tape or the engine was resting.',
      parts: [{ tid: 'tape_none', params: {} }],
    });
  }

  // Side desks: parity verdict only — the funding-arb tracker's dollar accrual
  // stays operator-private.
  if (reports && reports.parity && reports.parity.verdict) {
    sections.push({
      title: 'Side desks', title_tid: 'side', sep: ' ',
      html: `Live↔backtest parity reads <b>${esc(reports.parity.verdict)}</b>.`,
      parts: [{ tid: 'side_parity_pub', params: { v: reports.parity.verdict } }],
    });
  }

  sections.push({
    title: 'Looking ahead', title_tid: 'ahead', sep: ' ',
    html: (openCount
      ? `The desk carries <b>${openCount}</b> open position${openCount === 1 ? '' : 's'} into the new week, each with a hard stop working. `
      : 'The desk enters the week flat. ')
      + 'Same discipline as always: no trade without a stop, no size without conviction, '
      + 'and the risk gate has the final word.',
    parts: [
      openCount ? { tid: 'ahead_open', params: { n: openCount } } : { tid: 'ahead_flat', params: {} },
      { tid: 'ahead_discipline', params: {} },
    ],
  });

  const headline = !trades.length
    ? 'A flat week, by choice'
    : !scored ? `${trades.length} closes, no recorded P&L — no result to report`
      : `${wr}% winners over ${scored} trades — `
        + (net >= 0 ? 'a green week' : 'a red week, honestly told');
  const headline_part = !trades.length
    ? { tid: 'h_flat', params: {} }
    : !scored ? { tid: 'h_unpriced', params: { n: trades.length } }
      : net >= 0 ? { tid: 'h_pub_win', params: { wr, n: scored } }
        : { tid: 'h_pub_loss', params: { wr, n: scored } };

  return {
    week_key: key,
    period: { start: fmtDay(start), end: fmtDay(endInclusive) },
    headline, headline_part,
    sections,
    footer: 'Every figure above is derived from recorded trades and snapshots — nothing '
      + 'hand-written. Percentages and counts only: account size is never published. '
      + 'Past performance does not predict future results.',
    footer_tid: 'f_pub',
  };
}

// The public letter for a COMPLETED week. Recomposed on demand from the same
// recorded data (deterministic for past weeks — the tables are append-only),
// cached in memory because a completed week is immutable. Never writes the DB.
const _publicCache = new Map();          // week_key -> public letter
const _PUBLIC_CACHE_MAX = 64;

async function getPublicLetter(key) {
  const week = weekRangeFromKey(key);
  if (!week) return null;
  // Only completed weeks: the in-progress week's letter doesn't exist yet.
  if (week.end.getTime() > lastCompletedWeek().end.getTime()) return null;
  if (_publicCache.has(week.key)) return _publicCache.get(week.key);
  const pub = composePublicLetter(week, await loadWeekData(week.start, week.end));
  if (_publicCache.size >= _PUBLIC_CACHE_MAX) {
    _publicCache.delete(_publicCache.keys().next().value);
  }
  _publicCache.set(week.key, pub);
  return pub;
}

// ── Storage + lazy generation ────────────────────────────────────────────────

async function getLetter(week) {
  const [rows] = await pool.execute(
    'SELECT week_key, generated_at, letter_json FROM agent_letters WHERE week_key = ?',
    [week.key]);
  if (rows.length) {
    return { generated_at: rows[0].generated_at, created: false,
             letter: JSON.parse(rows[0].letter_json) };
  }
  const letter = composeLetter(week, await loadWeekData(week.start, week.end));
  // week_key is UNIQUE, and this is a SELECT-then-INSERT: the dashboard's
  // letter panel and /letter can both miss for a newly-complete week and both
  // compose it, and the loser would duplicate-key. The no-op update keeps the
  // row that won — the letter is composed from recorded facts for a finished
  // week, so both callers produced the same text.
  await pool.execute(
    `INSERT INTO agent_letters (week_key, generated_at, letter_json) VALUES (?, ?, ?)
     ON DUPLICATE KEY UPDATE week_key = week_key`,
    [week.key, new Date(), JSON.stringify(letter)]);
  const [stored] = await pool.execute(
    'SELECT generated_at, letter_json FROM agent_letters WHERE week_key = ?', [week.key]);
  if (stored[0]) {
    return { generated_at: stored[0].generated_at, created: true,
             letter: JSON.parse(stored[0].letter_json) };
  }
  return { generated_at: new Date().toISOString(), created: true, letter };
}

async function listLetters(limit = 12) {
  const [rows] = await pool.execute(
    `SELECT week_key, generated_at FROM agent_letters ORDER BY week_key DESC LIMIT ${Math.min(limit, 52)}`,
    []);
  return rows;
}

async function getLetterByKey(key) {
  if (!/^\d{4}-W\d{2}$/.test(String(key))) return null;
  const [rows] = await pool.execute(
    'SELECT week_key, generated_at, letter_json FROM agent_letters WHERE week_key = ?', [key]);
  if (!rows.length) return null;
  return { generated_at: rows[0].generated_at, letter: JSON.parse(rows[0].letter_json) };
}

/**
 * Ensure the last completed week's letter exists; when this sweep is the one
 * that creates it, announce it with a push to every subscriber. Never throws.
 */
async function sweepLetters(notify) {
  try {
    const week = lastCompletedWeek();
    const r = await getLetter(week);
    if (r.created) {
      let send = notify;
      if (!send) {
        const { notifySubscribers } = require('./push');
        send = notifySubscribers;
      }
      try {
        await send({
          title: '📜 Your weekly agent letter is ready',
          body: `${week.key}: ${r.letter.headline}`,
          url: '/dashboard#home',
        }, null);
      } catch (e) { /* push best-effort */ }
      return true;
    }
    return false;
  } catch (e) {
    return false;
  }
}

let sweepTimer = null;
function startLetterSweep(intervalMs = 3_600_000) {
  if (sweepTimer) return;
  sweepLetters().catch(() => {});
  sweepTimer = setInterval(() => { sweepLetters().catch(() => {}); }, intervalMs);
  if (sweepTimer.unref) sweepTimer.unref();
}

// ── Chat intercept ───────────────────────────────────────────────────────────

const CHAT_RE = /\b(?:(?:this |last )?week'?s letter|weekly (?:agent )?letter|agent letter)\b/i;

async function maybeHandleLetterChat(userId, text) {
  if (!CHAT_RE.test(String(text || ''))) return null;
  try {
    const { letter } = await getLetter(lastCompletedWeek());
    const secs = letter.sections.map(s => `<b>${esc(s.title)}</b><br>${s.html}`).join('<br><br>');
    return {
      reply_html: `📜 <b>The Agent Letter — ${esc(letter.week_key)}</b> `
        + `<i>(${esc(letter.period.start)} → ${esc(letter.period.end)})</i><br><br>${secs}`
        + `<br><br><i>${esc(letter.footer)}</i>`,
      intent: 'letter',
    };
  } catch (e) {
    return { reply_html: 'The letter press jammed — try again in a moment.', intent: 'letter' };
  }
}

module.exports = {
  weekKey,
  weekRangeFromKey,
  lastCompletedWeek,
  composeLetter,
  composePublicLetter,
  getPublicLetter,
  getLetter,
  getLetterByKey,
  listLetters,
  sweepLetters,
  startLetterSweep,
  maybeHandleLetterChat,
};
