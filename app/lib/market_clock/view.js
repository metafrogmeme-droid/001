'use strict';
/**
 * Market Clock view. UTC and Europe/Amsterdam, source rank, and a status.
 * A docs convention is not a symbol's activation time. Unknown stays null.
 * On a conflict, the alert instant is the earliest stated instant.
 */

const { CHECKED_ON, SOURCES, RANKS, ACTIVATION_CONVENTION, PENDING, EVENTS } = require('./seed');

const ZONE = 'Europe/Amsterdam';
const ACTIVATION_DEFAULT_MIN = 15;
const HALT_DEFAULT_MIN = 5;
const REGIME_LEAD_MIN = 15;
const EVENT_LEADS = [1440, 60, 5];
const LOCKOUT_LEADS = [2880, 1440];

const TREATMENTS = new Set([
  'rescale_positions', 'settle_close', 'funding_payment', 'post_only', 'reduce_only',
  'halt', 'pause_all', 'block_repay', 'block_add_collateral', 'oracle_frozen',
  'oracle_regime_switch', 'balance_rebase', 'display_only_multiplier',
]);

const ALERT_TYPES = ['event_window', 'regime_switch', 'lockout', 'conflict'];

const PRICE_KEY = /(^|_)(price|prices|px|pnl|usd|usdt|usdc|dollar|dollars|notional|bid|ask|volume|fair)($|_)|fair_value|market_cap|open_interest|last_price|mark_price/i;
const VERDICT = /\b(buy|sell|scam|safe)\b/i;

function labelText(text) {
  const t = String(text == null ? '' : text).trim();
  if (!t) return t;
  if (t.startsWith('AI-written')) return t;
  return 'AI-written. ' + t;
}

function parseClock(clock) {
  const m = /^([01]\d|2[0-3]):([0-5]\d)$/.exec(String(clock || ''));
  if (!m) return null;
  return { hour: Number(m[1]), minute: Number(m[2]) };
}

function clockText(hour, minute) {
  return String(hour).padStart(2, '0') + ':' + String(minute).padStart(2, '0');
}

function wall(date, timeZone) {
  if (!(date instanceof Date) || Number.isNaN(date.getTime())) return { readable: false };
  let bag;
  try {
    const fmt = new Intl.DateTimeFormat('en-GB', {
      timeZone,
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
      hourCycle: 'h23',
      timeZoneName: 'longOffset',
    });
    bag = {};
    for (const p of fmt.formatToParts(date)) {
      if (p.type !== 'literal') bag[p.type] = p.value;
    }
  } catch (err) {
    return { readable: false };
  }
  if (!bag.year || !bag.hour || !bag.minute) return { readable: false };
  return {
    readable: true,
    time_zone: timeZone,
    date: bag.year + '-' + bag.month + '-' + bag.day,
    clock: bag.hour + ':' + bag.minute,
    offset: bag.timeZoneName || null,
  };
}

function amsterdamForInstant(iso) {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return { readable: false, time_zone: ZONE };
  const got = wall(date, ZONE);
  if (!got.readable) return { readable: false, time_zone: ZONE };
  return { readable: true, time_zone: ZONE, date: got.date, clock: got.clock, offset: got.offset };
}

function amsterdamForUtcClock(clock) {
  if (!parseClock(clock)) return { readable: false, time_zone: ZONE };
  const winterIso = '2026-01-15T' + clock + ':00Z';
  const summerIso = '2026-07-15T' + clock + ':00Z';
  const winter = amsterdamForInstant(winterIso);
  const summer = amsterdamForInstant(summerIso);
  if (!winter.readable || !summer.readable) return { readable: false, time_zone: ZONE };
  return {
    readable: true,
    time_zone: ZONE,
    standard: { clock: winter.clock, date: winter.date, offset: winter.offset, reference_utc: winterIso },
    daylight: { clock: summer.clock, date: summer.date, offset: summer.offset, reference_utc: summerIso },
  };
}

function localToUtcClock(clock, offsetMinutes) {
  const parts = parseClock(clock);
  if (!parts || !Number.isInteger(offsetMinutes)) return null;
  let total = parts.hour * 60 + parts.minute - offsetMinutes;
  let dayShift = 0;
  while (total >= 1440) {
    total -= 1440;
    dayShift += 1;
  }
  while (total < 0) {
    total += 1440;
    dayShift -= 1;
  }
  return { clock: clockText(Math.floor(total / 60), total % 60), day_shift: dayShift };
}

const EX_LINE = ['day_prior_to_ex_date', 'ex_date', 'day_after_ex_date'];

function shiftDayAnchor(anchor, dayShift) {
  if (!Number.isInteger(dayShift)) return null;
  if (anchor === 'effective_date') {
    return {
      day_anchor: 'effective_date',
      day_shift: dayShift,
      note: dayShift === 0 ? null : 'Shifted off the effective date. Not placed on an ex-date.',
    };
  }
  const i = EX_LINE.indexOf(anchor);
  if (i < 0) return null;
  const next = i + dayShift;
  if (next < 0 || next >= EX_LINE.length) {
    return { day_anchor: null, day_shift: dayShift, note: 'The day shift does not land on a named anchor.' };
  }
  return { day_anchor: EX_LINE[next], day_shift: dayShift, note: null };
}

function zonedWallToUtc(year, month, day, clock, timeZone) {
  const parts = parseClock(clock);
  if (!parts) return null;
  if (!Number.isInteger(year) || !Number.isInteger(month) || !Number.isInteger(day)) return null;
  let guess = Date.UTC(year, month - 1, day, parts.hour, parts.minute, 0);
  for (let i = 0; i < 6; i += 1) {
    const got = wall(new Date(guess), timeZone);
    if (!got.readable) return null;
    const ymd = got.date.split('-').map(Number);
    const hm = got.clock.split(':').map(Number);
    const asUtc = Date.UTC(ymd[0], ymd[1] - 1, ymd[2], hm[0], hm[1], 0);
    const want = Date.UTC(year, month - 1, day, parts.hour, parts.minute, 0);
    const diff = asUtc - want;
    if (diff === 0) return new Date(guess).toISOString();
    guess -= diff;
  }
  return null;
}

function readMinutes(env, name, fallback) {
  const src = env || process.env;
  if (!Object.prototype.hasOwnProperty.call(src, name) || src[name] == null || String(src[name]).trim() === '') {
    return { minutes: fallback, source: 'default', readable: true };
  }
  const raw = String(src[name]).trim();
  if (!/^[1-9]\d*$/.test(raw)) return { minutes: null, source: 'env', readable: false };
  return { minutes: Number(raw), source: 'env', readable: true };
}

function tolerances(env) {
  const activation = readMinutes(env, 'MARKET_CLOCK_ACTIVATION_TOLERANCE_MIN', ACTIVATION_DEFAULT_MIN);
  const halt = readMinutes(env, 'MARKET_CLOCK_HALT_TOLERANCE_MIN', HALT_DEFAULT_MIN);
  return {
    activation_minutes: activation.minutes,
    activation_source: activation.source,
    activation_readable: activation.readable,
    halt_minutes: halt.minutes,
    halt_source: halt.source,
    halt_readable: halt.readable,
    readable: activation.readable === true && halt.readable === true,
  };
}

function sourceRef(id) {
  const row = SOURCES[id];
  if (!row || !row.url) return null;
  return {
    id,
    name: row.name,
    url: row.url,
    checked_on: row.checked_on,
    page_says_updated: row.page_says_updated || null,
    link_note: row.link_note ? labelText(row.link_note) : null,
    present: row.present !== false,
  };
}

function displayInput(input) {
  if (!input) return { readable: false, utc: null, amsterdam: null };
  if (input.kind === 'utc_instant') {
    const iso = new Date(input.utc).toISOString();
    if (Number.isNaN(Date.parse(iso))) return { readable: false, utc: null, amsterdam: null };
    const ams = amsterdamForInstant(iso);
    if (!ams.readable) return { readable: false, utc: null, amsterdam: null };
    return { readable: true, known: true, kind: 'utc_instant', utc: iso, amsterdam: ams, approximate: input.approximate === true };
  }
  if (input.kind === 'zoned_instant') {
    const iso = zonedWallToUtc(input.year, input.month, input.day, input.clock, input.time_zone);
    if (!iso) return { readable: false, utc: null, amsterdam: null };
    const ams = amsterdamForInstant(iso);
    if (!ams.readable) return { readable: false, utc: null, amsterdam: null };
    return {
      readable: true,
      known: true,
      kind: 'zoned_instant',
      stated_clock: input.clock,
      stated_zone: input.zone_label || input.time_zone,
      utc: iso,
      amsterdam: ams,
      approximate: false,
    };
  }
  if (input.kind === 'utc_clock') {
    const ams = amsterdamForUtcClock(input.clock);
    if (!ams.readable) return { readable: false, utc: null, amsterdam: null };
    return {
      readable: true,
      known: false,
      kind: 'utc_clock',
      utc: null,
      clock: input.clock,
      day_anchor: input.day_anchor,
      approximate: input.approximate === true,
      amsterdam: ams,
    };
  }
  if (input.kind === 'offset_clock') {
    const primary = localToUtcClock(input.clock, input.offset_minutes);
    const primaryAnchor = primary && shiftDayAnchor(input.day_anchor, primary.day_shift);
    const alternate = localToUtcClock(input.clock, input.alternate_offset_minutes);
    const alternateAnchor = alternate && shiftDayAnchor(input.day_anchor, alternate.day_shift);
    if (!primary || !primaryAnchor || !alternate || !alternateAnchor) return { readable: false, utc: null, amsterdam: null };
    const primaryAms = amsterdamForUtcClock(primary.clock);
    const alternateAms = amsterdamForUtcClock(alternate.clock);
    if (!primaryAms.readable || !alternateAms.readable) return { readable: false, utc: null, amsterdam: null };
    return {
      readable: true,
      known: false,
      kind: 'offset_clock',
      utc: null,
      amsterdam: null,
      selected: null,
      normalizations: [
        {
          assumption: input.assumption,
          offset_label: input.offset_label,
          utc_clock: primary.clock,
          day_anchor: primaryAnchor.day_anchor,
          day_shift: primaryAnchor.day_shift,
          approximate: true,
          amsterdam: primaryAms,
        },
        {
          assumption: input.alternate_assumption,
          offset_label: input.alternate_offset_label,
          utc_clock: alternate.clock,
          day_anchor: alternateAnchor.day_anchor,
          day_shift: alternateAnchor.day_shift,
          approximate: true,
          amsterdam: alternateAms,
        },
      ],
    };
  }
  if (input.kind === 'unknown' || input.kind === 'relative' || input.kind === 'relative_duration' || input.kind === 'conditional') {
    return {
      readable: true,
      known: false,
      kind: input.kind,
      stated: input.stated || null,
      utc: null,
      amsterdam: null,
      minutes: Number.isInteger(input.minutes) ? input.minutes : null,
      older_than_minutes: Number.isInteger(input.older_than_minutes) ? input.older_than_minutes : null,
      seconds_before_anchor: Number.isInteger(input.seconds_before_anchor) ? input.seconds_before_anchor : null,
      anchor: input.anchor || null,
      approximate: input.approximate === true,
    };
  }
  if (input.kind === 'calendar_date') {
    if (!/^\d{4}-\d{2}-\d{2}$/.test(String(input.date || ''))) return { readable: false, utc: null, amsterdam: null };
    return {
      readable: true,
      known: false,
      kind: 'calendar_date',
      date: input.date,
      utc: null,
      amsterdam: { time_zone: ZONE, date: input.date, clock: null, offset: null },
      note: input.note ? labelText(input.note) : null,
    };
  }
  return { readable: false, utc: null, amsterdam: null };
}

function instantMs(value) {
  if (typeof value !== 'string' || value === '') return null;
  const ms = Date.parse(value);
  if (Number.isNaN(ms)) return null;
  return ms;
}

/**
 * Status from the precedence ranks.
 * Rank 4 alone is provisional, even when those conventions disagree.
 * conflict is a gap wider than tolerance between the two best ranks that
 * carry an instant (the rank-1 versus rank-2 case when both are present).
 * resolved_value is the best rank's instant, or null when the only rank is 4
 * and the conventions do not agree. delta_s is null when fewer than two
 * instants were compared.
 */
function resolveField(claims, toleranceSeconds) {
  const rows = (claims || []).filter((c) => c && Number.isInteger(c.rank));
  const ranks = [...new Set(rows.map((c) => c.rank))].sort((a, b) => a - b);
  const timed = rows.filter((c) => instantMs(c.utc) != null);
  const timedRanks = [...new Set(timed.map((c) => c.rank))].sort((a, b) => a - b);
  const onlyConventions = ranks.length > 0 && ranks.every((r) => r === 4);

  let delta_s = null;
  let disagreement = false;
  let status = 'single_source';
  let resolved_value = null;
  let alert_at_utc = null;

  if (ranks.length === 0) {
    status = 'provisional';
  } else if (onlyConventions) {
    status = 'provisional';
    const signatures = rows.map((c) => JSON.stringify({
      stated: c.stated || null,
      utc: c.utc || null,
      clock: c.clock || null,
      day_anchor: c.day_anchor || null,
    }));
    disagreement = new Set(signatures).size > 1;
  } else if (timedRanks.length >= 2) {
    const top = timed.filter((c) => c.rank === timedRanks[0]);
    const next = timed.filter((c) => c.rank === timedRanks[1]);
    const topMs = instantMs(top[0].utc);
    const nextMs = instantMs(next[0].utc);
    delta_s = Math.round(Math.abs(topMs - nextMs) / 1000);
    resolved_value = top[0].utc;
    if (toleranceSeconds == null) {
      status = 'unreadable';
    } else if (delta_s > toleranceSeconds) {
      status = 'conflict';
      disagreement = true;
      const all = timed.map((c) => instantMs(c.utc));
      alert_at_utc = new Date(Math.min.apply(null, all)).toISOString();
    } else {
      status = 'confirmed';
    }
  } else if (timed.length >= 2 && timedRanks.length === 1) {
    const all = timed.map((c) => instantMs(c.utc));
    delta_s = Math.round((Math.max.apply(null, all) - Math.min.apply(null, all)) / 1000);
    resolved_value = timed[0].utc;
    if (toleranceSeconds == null) {
      status = 'unreadable';
    } else if (delta_s > toleranceSeconds) {
      status = 'conflict';
      disagreement = true;
      alert_at_utc = new Date(Math.min.apply(null, all)).toISOString();
    } else {
      status = 'confirmed';
    }
  } else if (timed.length === 1) {
    status = 'single_source';
    resolved_value = timed[0].utc;
    delta_s = null;
  } else {
    status = ranks[0] === 4 ? 'provisional' : 'single_source';
  }

  return {
    status,
    resolved_value,
    delta_s,
    disagreement: disagreement === true,
    conflict: status === 'conflict' || disagreement === true,
    alert_at_utc: status === 'conflict' ? alert_at_utc : null,
    selected_source: null,
  };
}

function applyStale(resolved, fetchedAt, maxAgeHours, nowMs) {
  if (maxAgeHours == null) {
    return Object.assign({}, resolved, { stale_check: 'max_age_unset' });
  }
  const ms = instantMs(fetchedAt);
  if (ms == null) {
    return Object.assign({}, resolved, { stale_check: 'not_evaluated', reason: 'no_timestamp' });
  }
  const ageMs = nowMs - ms;
  if (ageMs > maxAgeHours * 3600 * 1000) {
    return Object.assign({}, resolved, { status: 'stale', stale_check: 'older_than_max_age' });
  }
  return Object.assign({}, resolved, { stale_check: 'within_max_age' });
}

function toleranceSeconds(tol, which) {
  if (!tol || tol.readable !== true) return null;
  const minutes = which === 'halt' ? tol.halt_minutes : tol.activation_minutes;
  if (!Number.isInteger(minutes)) return null;
  return minutes * 60;
}

function claimView(raw) {
  const src = sourceRef(raw.source);
  if (!src) return null;
  const shown = raw.input ? displayInput(raw.input) : displayInput(raw.utc ? { kind: 'utc_instant', utc: raw.utc } : { kind: 'unknown', stated: raw.stated });
  if (!shown.readable) return null;
  return {
    source: src,
    rank: raw.rank,
    stated: raw.stated,
    also_stated: raw.also_stated || null,
    utc: shown.utc || raw.utc || null,
    clock: shown.clock || null,
    day_anchor: shown.day_anchor || null,
    amsterdam: shown.amsterdam,
    normalizations: shown.normalizations || null,
    selected: null,
    network: raw.network == null ? null : raw.network,
    network_note: raw.network_note ? labelText(raw.network_note) : null,
  };
}

function buildConvention(tol) {
  const claims = [];
  for (const raw of ACTIVATION_CONVENTION.claims) {
    const view = claimView(raw);
    if (!view) return null;
    claims.push(view);
  }
  const resolved = resolveField(claims, toleranceSeconds(tol, 'activation'));
  return {
    id: ACTIVATION_CONVENTION.id,
    field: ACTIVATION_CONVENTION.field,
    scope: 'convention',
    ai_written: true,
    ai_written_label: 'AI-written',
    label: labelText(ACTIVATION_CONVENTION.summary + ' Each source\'s time is listed. No source is selected.'),
    status: resolved.status,
    conflict: resolved.conflict === true,
    disagreement: resolved.disagreement === true,
    resolved_value: resolved.resolved_value,
    delta_s: resolved.delta_s,
    alert_at_utc: resolved.alert_at_utc,
    selected_source: null,
    claims,
  };
}

function atLead(anchorIso, minutes, type, extra) {
  const ms = instantMs(anchorIso);
  if (ms == null) return null;
  const at = new Date(ms - minutes * 60 * 1000).toISOString();
  const ams = amsterdamForInstant(at);
  if (!ams.readable) return null;
  return Object.assign({
    type,
    lead_minutes: minutes,
    at_utc: at,
    amsterdam: ams,
  }, extra || {});
}

function alertsFor(event, windows, field) {
  const out = [];
  const starts = windows.map((w) => w.start_utc).filter((v) => instantMs(v) != null);
  const anchor = field && field.alert_at_utc ? field.alert_at_utc : (starts.length ? new Date(Math.min.apply(null, starts.map((v) => instantMs(v)))).toISOString() : null);
  if (event.alert_class === 'event_window' && anchor) {
    for (const lead of EVENT_LEADS) {
      const row = atLead(anchor, lead, 'event_window');
      if (row) out.push(row);
    }
    const openAms = amsterdamForInstant(anchor);
    if (openAms.readable) {
      out.push({ type: 'event_window', phase: 'window_open', lead_minutes: 0, at_utc: anchor, amsterdam: openAms });
    }
    const ends = windows.map((w) => w.end_utc).filter((v) => instantMs(v) != null);
    if (ends.length) {
      const end = new Date(Math.max.apply(null, ends.map((v) => instantMs(v)))).toISOString();
      const endAms = amsterdamForInstant(end);
      if (endAms.readable) {
        out.push({
          type: 'event_window',
          phase: 'window_closed',
          lead_minutes: 0,
          at_utc: end,
          amsterdam: endAms,
          end_is_indicative: windows.some((w) => w.end_is_indicative === true),
        });
      }
    }
  }
  if (event.alert_class === 'regime_switch' && anchor) {
    const row = atLead(anchor, REGIME_LEAD_MIN, 'regime_switch', { opt_in: true, default_enabled: false });
    if (row) out.push(row);
  }
  if (event.alert_class === 'lockout') {
    if (anchor) {
      for (const lead of LOCKOUT_LEADS) {
        const row = atLead(anchor, lead, 'lockout');
        if (row) out.push(row);
      }
    } else {
      out.push({
        type: 'lockout',
        lead_minutes: LOCKOUT_LEADS.slice(),
        at_utc: null,
        amsterdam: null,
        note: labelText('Lockout alerts are 48 hours and 24 hours before the corporate-action instant. This row has no instant.'),
      });
    }
  }
  if (field && field.status === 'conflict') {
    out.push({
      type: 'conflict',
      at_utc: field.alert_at_utc,
      amsterdam: field.alert_at_utc ? amsterdamForInstant(field.alert_at_utc) : null,
      note: labelText('Status is conflict. The alert instant is the earliest stated instant.'),
    });
  }
  return out;
}

function buildWindow(raw, eventClaims, tol) {
  if (!TREATMENTS.has(raw.treatment)) return { readable: false };
  const src = raw.source ? sourceRef(raw.source) : null;
  if (raw.source && !src) return { readable: false };
  const start = displayInput(raw.start_input);
  if (!start.readable) return { readable: false };
  const end = raw.end_input ? displayInput(raw.end_input) : null;
  if (end && !end.readable) return { readable: false };
  const claims = [];
  const pool = eventClaims && eventClaims.length ? eventClaims : (src ? [{
    source: src,
    rank: raw.rank,
    stated: start.stated_clock ? (start.stated_clock + ' ' + (start.stated_zone || '')) : (raw.start_input && raw.start_input.stated) || start.utc || null,
    utc: start.utc || null,
    clock: start.clock || null,
    day_anchor: start.day_anchor || null,
    amsterdam: start.amsterdam || null,
  }] : []);
  for (const c of pool) claims.push(c);
  const resolved = resolveField(claims, toleranceSeconds(tol, raw.tolerance));
  if (raw.rank === 4 && claims.length === 1) {
    resolved.status = 'provisional';
    resolved.resolved_value = null;
  }
  return {
    readable: true,
    venue: raw.venue,
    venue_type: raw.venue_type,
    treatment: raw.treatment,
    chain_scope: raw.chain_scope || null,
    start_utc: start.utc || null,
    end_utc: end && end.utc ? end.utc : null,
    end_is_indicative: raw.end_is_indicative === true,
    time: start,
    end_time: end,
    orders_effect: raw.orders_effect || null,
    liq_risk_note: raw.liq_risk_note || null,
    blocked_actions: raw.blocked_actions || null,
    thin_source: raw.thin_source === true,
    minutes: Object.prototype.hasOwnProperty.call(raw, 'minutes') ? raw.minutes : (start.minutes == null ? null : start.minutes),
    source_rank: raw.rank,
    source_url: src ? src.url : null,
    source: src,
    sealed_hash: null,
    status: resolved.status,
    conflict: resolved.conflict === true,
    resolved_value: raw.rank === 4 && (!eventClaims || eventClaims.length === 0) ? null : resolved.resolved_value,
    delta_s: resolved.delta_s,
    alert_at_utc: resolved.alert_at_utc,
    last_checked_utc: null,
    checked_on: CHECKED_ON,
    absent_rank1: raw.absent_rank1 ? sourceRef(raw.absent_rank1) : null,
    rules: (raw.rules || []).map(labelText),
    ai_written: true,
    ai_written_label: 'AI-written',
    field: resolved,
  };
}

function citedLossView(raw) {
  if (!raw) return null;
  const figureSource = sourceRef(raw.sources.single_source_figure);
  const rangeSource = sourceRef(raw.sources.range);
  const wideSource = sourceRef(raw.sources.widely_reported);
  if (!figureSource || !rangeSource || !wideSource) return null;
  return {
    quoted_as: labelText(
      'Quoted liquidation range ' + raw.range + '. ' + raw.widely_reported
      + '. ' + raw.single_source_figure + ' is single-source (' + raw.single_source_name + ').'
    ),
    range: raw.range,
    range_source: rangeSource,
    widely_reported: raw.widely_reported,
    widely_reported_source: wideSource,
    single_source_figure: raw.single_source_figure,
    single_source_name: raw.single_source_name,
    single_source: true,
    figure_source: figureSource,
    status: 'single_source',
  };
}

function buildEvent(raw, tol) {
  const claimViews = [];
  for (const c of raw.claims || []) {
    const view = claimView(c);
    if (!view) return { readable: false, event_id: raw.event_id };
    claimViews.push(view);
  }
  const windows = [];
  for (const w of raw.venue_windows) {
    const built = buildWindow(w, claimViews, tol);
    if (!built.readable) return { readable: false, event_id: raw.event_id };
    windows.push(built);
  }
  const field = claimViews.length ? resolveField(claimViews, toleranceSeconds(tol, 'activation')) : null;
  const citedLoss = citedLossView(raw.cited_loss);
  if (raw.cited_loss && !citedLoss) return { readable: false, event_id: raw.event_id };
  const rules = [];
  if (citedLoss) rules.push(citedLoss.quoted_as);
  if (raw.cover) {
    rules.push(labelText(
      'A cover was announced on ' + raw.cover.announced_on
      + ' as a one-time discretionary decision. Whether payouts were completed is not verified. payout_completed is null.'
    ));
  }
  return {
    readable: true,
    event_id: raw.event_id,
    symbol: raw.symbol || null,
    symbols: raw.symbols || [],
    action: raw.action,
    action_note: raw.action_note ? labelText(raw.action_note) : null,
    venue_windows: windows.map(function (w) {
      const copy = Object.assign({}, w);
      delete copy.field;
      delete copy.readable;
      return copy;
    }),
    claims: claimViews,
    status: field ? field.status : (windows[0] ? windows[0].status : 'single_source'),
    conflict: field ? field.conflict === true : windows.some((w) => w.conflict === true),
    resolved_value: field ? field.resolved_value : null,
    delta_s: field ? field.delta_s : null,
    alert_at_utc: field ? field.alert_at_utc : null,
    cited_loss: citedLoss,
    cover: raw.cover ? {
      announced_on: raw.cover.announced_on,
      announced: true,
      payout_completed: null,
    } : null,
    rules,
    alerts: alertsFor(raw, windows, field),
    ai_written: true,
    ai_written_label: 'AI-written',
  };
}

function buildPending(raw) {
  const src = sourceRef(raw.source);
  if (!src) return null;
  return {
    id: raw.id,
    symbol: raw.symbol,
    source: src,
    source_rank: raw.rank,
    status: 'single_source',
    activation_pending: false,
    activationDateTime: null,
    raw_sentinel: raw.raw_sentinel,
    note: labelText(raw.note),
    ai_written: true,
    ai_written_label: 'AI-written',
  };
}

function normSymbol(value) {
  return String(value || '').trim().toLowerCase();
}

function eventSymbols(event) {
  const out = [];
  if (event.symbols) out.push.apply(out, event.symbols);
  const box = event.symbol && event.symbol.venue_symbols;
  if (box) {
    for (const key of Object.keys(box)) out.push(box[key]);
  }
  if (event.symbol && event.symbol.underlying) out.push(event.symbol.underlying);
  return out;
}

function priceKeyFound(value, trail) {
  if (value == null || typeof value !== 'object') return null;
  if (Array.isArray(value)) {
    for (let i = 0; i < value.length; i += 1) {
      const hit = priceKeyFound(value[i], trail + '[' + i + ']');
      if (hit) return hit;
    }
    return null;
  }
  for (const key of Object.keys(value)) {
    if (PRICE_KEY.test(key)) return trail + '.' + key;
    const hit = priceKeyFound(value[key], trail + '.' + key);
    if (hit) return hit;
  }
  return null;
}

function narrativeLeak(value, key) {
  if (key === 'url' || key === 'endpoint' || key === 'source_url') return null;
  if (typeof value === 'string') {
    if (VERDICT.test(value)) return value.slice(0, 80);
    return null;
  }
  if (Array.isArray(value)) {
    for (const item of value) {
      const hit = narrativeLeak(item, key);
      if (hit) return hit;
    }
    return null;
  }
  if (value && typeof value === 'object') {
    for (const child of Object.keys(value)) {
      const hit = narrativeLeak(value[child], child);
      if (hit) return hit;
    }
  }
  return null;
}

function calendar(symbol, env) {
  const tol = tolerances(env);
  const convention = buildConvention(tol);
  if (!convention) return { readable: false, reason: 'convention_unreadable', events: null };
  const events = [];
  for (const raw of EVENTS) {
    const built = buildEvent(raw, tol);
    if (!built.readable) return { readable: false, reason: 'event_unreadable', event_id: built.event_id, events: null };
    events.push(built);
  }
  const pending = [];
  for (const raw of PENDING) {
    const built = buildPending(raw);
    if (!built) return { readable: false, reason: 'pending_unreadable', events: null };
    pending.push(built);
  }
  const asked = normSymbol(symbol);
  let symbol_event_ids = null;
  let symbol_note = null;
  if (asked) {
    symbol_event_ids = [];
    for (const event of events) {
      if (eventSymbols(event).some((s) => normSymbol(s) === asked)) symbol_event_ids.push(event.event_id);
    }
    for (const row of pending) {
      if (eventSymbols(row).some((s) => normSymbol(s) === asked)) symbol_event_ids.push(row.id);
    }
    if (symbol_event_ids.length === 0) {
      symbol_note = labelText('No dated entry for this symbol in the static seed. That is not a measurement that no corporate action exists.');
    }
  }
  const body = {
    product: 'market_clock',
    read_only: true,
    readable: tol.readable === true,
    ai_written: true,
    ai_written_label: 'AI-written',
    checked_on: CHECKED_ON,
    snapshot: 'static',
    refetched: false,
    seal: null,
    seal_note: labelText('This process does not seal a live page. null means no seal was made.'),
    notice: labelText('Times and rules only. Information, not advice. Static seed checked ' + CHECKED_ON + '. Not refetched by this process.'),
    zones: ['UTC', ZONE],
    ranks: RANKS,
    tolerances: tol,
    alert_types: ALERT_TYPES.map(function (type) {
      return { type, ai_written_label: 'AI-written' };
    }),
    convention,
    pending,
    events,
    query_symbol: asked || null,
    symbol_event_ids,
    symbol_note,
    unreadable_ids: [],
  };
  if (!tol.readable) {
    body.readable = false;
    body.reason = 'tolerance_unreadable';
  }
  if (priceKeyFound(body, '$') || narrativeLeak(body, '')) {
    return { readable: false, reason: 'price_or_verdict', events: null, convention: null };
  }
  return body;
}

module.exports = {
  ZONE,
  TREATMENTS,
  ALERT_TYPES,
  ACTIVATION_DEFAULT_MIN,
  HALT_DEFAULT_MIN,
  calendar,
  resolveField,
  applyStale,
  displayInput,
  tolerances,
  zonedWallToUtc,
  amsterdamForInstant,
  amsterdamForUtcClock,
  localToUtcClock,
  priceKeyFound,
  labelText,
};
