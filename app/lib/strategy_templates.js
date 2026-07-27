'use strict';
/**
 * Strategy archetype templates — curated starting points for the builder,
 * expressed ENTIRELY in the validated community-strategy rule vocabulary.
 *
 * Honesty (§4) by construction:
 * - a template is a CONFIG teaching the vocabulary by example — taglines are
 *   phrased as INTENT ("ride confirmed uptrends"), never as an edge or a
 *   performance claim, and carry no dollar figure and no fabricated number;
 * - every template must pass the same validateStrategy gate a member's own
 *   draft passes (pinned by test, so drift is structurally impossible);
 * - templates only use rules the builder form can render, so "start from an
 *   archetype" always yields a fully editable draft.
 */

const TEMPLATES = [
  {
    key: 'trend-rider', icon: '📈', name: 'Trend Rider',
    tagline: 'Ride confirmed uptrends in majors, exit when the trend does.',
    how: 'Long only while the regime reads trend-up. Waits for higher-confidence signals, asks 2:1 reward on every entry, and hands the book back at a 15% drawdown.',
    regime: 'trend_up', horizon: 'swing',
    rules: [
      { type: 'direction', value: 'long_only' },
      { type: 'min_confidence', value: 0.6 },
      { type: 'min_rr', value: 2 },
      { type: 'max_position_pct', value: 5 },
      { type: 'max_open_positions', value: 3 },
      { type: 'max_drawdown_pct', value: 15 },
      { type: 'allowed_symbols', value: ['BTC', 'ETH', 'SOL'] },
    ],
  },
  {
    key: 'range-fader', icon: '🎯', name: 'Range Fader',
    tagline: 'Fade the edges of a range in the two deepest books.',
    how: 'Trades both directions only inside a ranging regime, small size per attempt, and stops for the day after a 3% daily loss — a range that breaks is not argued with.',
    regime: 'range', horizon: 'intraday',
    rules: [
      { type: 'min_confidence', value: 0.65 },
      { type: 'min_rr', value: 1.6 },
      { type: 'max_position_pct', value: 4 },
      { type: 'max_open_positions', value: 4 },
      { type: 'max_daily_loss_pct', value: 3 },
      { type: 'allowed_symbols', value: ['BTC', 'ETH'] },
    ],
  },
  {
    key: 'storm-shelter', icon: '⛈️', name: 'Storm Shelter',
    tagline: 'Trade less, demand more, when the sea is rough.',
    how: 'A volatile-regime posture: only the highest-confidence setups, 2.5:1 reward demanded, tiny size, two positions at most, and hard halts at 2% daily / 8% drawdown.',
    regime: 'volatile', horizon: 'intraday',
    rules: [
      { type: 'min_confidence', value: 0.75 },
      { type: 'min_rr', value: 2.5 },
      { type: 'max_position_pct', value: 2 },
      { type: 'max_open_positions', value: 2 },
      { type: 'max_daily_loss_pct', value: 2 },
      { type: 'max_drawdown_pct', value: 8 },
    ],
  },
  {
    key: 'patient-majors', icon: '🗿', name: 'Patient Majors',
    tagline: 'Few positions, wide horizons, only the two oldest books.',
    how: 'Position-horizon longs in BTC and ETH only. High conviction required, 3:1 reward asked, two slots — most days this strategy correctly does nothing.',
    regime: 'any', horizon: 'position',
    rules: [
      { type: 'direction', value: 'long_only' },
      { type: 'min_confidence', value: 0.7 },
      { type: 'min_rr', value: 3 },
      { type: 'max_position_pct', value: 6 },
      { type: 'max_open_positions', value: 2 },
      { type: 'max_drawdown_pct', value: 20 },
      { type: 'allowed_symbols', value: ['BTC', 'ETH'] },
    ],
  },
  {
    key: 'breakout-scout', icon: '🚀', name: 'Breakout Scout',
    tagline: 'Probe fresh momentum with small, capped attempts.',
    how: 'Long-only intraday probes when trend-up momentum appears. Size stays small because breakouts fail often; the 4% daily halt decides when the day is over, not hope.',
    regime: 'trend_up', horizon: 'intraday',
    rules: [
      { type: 'direction', value: 'long_only' },
      { type: 'min_confidence', value: 0.6 },
      { type: 'min_rr', value: 1.8 },
      { type: 'max_position_pct', value: 3 },
      { type: 'max_open_positions', value: 5 },
      { type: 'max_daily_loss_pct', value: 4 },
    ],
  },
  {
    key: 'downtrend-discipline', icon: '🧊', name: 'Downtrend Discipline',
    tagline: 'Short weakness only when the regime agrees, halt early.',
    how: 'Short-only inside a trend-down regime — never against it. High confidence required, two positions, and tight halts (3% daily, 10% drawdown) because shorts squeeze.',
    regime: 'trend_down', horizon: 'swing',
    rules: [
      { type: 'direction', value: 'short_only' },
      { type: 'min_confidence', value: 0.7 },
      { type: 'min_rr', value: 2 },
      { type: 'max_position_pct', value: 3 },
      { type: 'max_open_positions', value: 2 },
      { type: 'max_daily_loss_pct', value: 3 },
      { type: 'max_drawdown_pct', value: 10 },
    ],
  },
  {
    key: 'guarded-scalper', icon: '⚡', name: 'Guarded Scalper',
    tagline: 'Many small attempts inside a hard containment shell.',
    how: 'Scalp-horizon in both directions with 1.5% size per attempt. The edge budget is strict: 2% daily loss or 6% drawdown ends the experiment — the shell matters more than any single trade.',
    regime: 'any', horizon: 'scalp',
    rules: [
      { type: 'min_confidence', value: 0.55 },
      { type: 'min_rr', value: 1.2 },
      { type: 'max_position_pct', value: 1.5 },
      { type: 'max_open_positions', value: 6 },
      { type: 'max_daily_loss_pct', value: 2 },
      { type: 'max_drawdown_pct', value: 6 },
    ],
  },
  {
    key: 'drawdown-warden', icon: '🛡️', name: 'Drawdown Warden',
    tagline: 'Containment first — the strategy is the halts.',
    how: 'A containment-first posture for any regime: modest size, 2:1 reward asked, and the tightest halts in the gallery (2% daily, 5% drawdown). Designed to survive being wrong.',
    regime: 'any', horizon: 'swing',
    rules: [
      { type: 'min_confidence', value: 0.65 },
      { type: 'min_rr', value: 2 },
      { type: 'max_position_pct', value: 2.5 },
      { type: 'max_open_positions', value: 3 },
      { type: 'max_daily_loss_pct', value: 2 },
      { type: 'max_drawdown_pct', value: 5 },
    ],
  },
  {
    key: 'basket-rotator', icon: '🧺', name: 'Basket Rotator',
    tagline: 'Spread attempts across the five deepest majors.',
    how: 'Swing trades across a fixed five-major basket, both directions, capped per position so no single name dominates the book. A 12% drawdown parks the basket.',
    regime: 'any', horizon: 'swing',
    rules: [
      { type: 'min_confidence', value: 0.6 },
      { type: 'min_rr', value: 1.8 },
      { type: 'max_position_pct', value: 4 },
      { type: 'max_open_positions', value: 5 },
      { type: 'max_drawdown_pct', value: 12 },
      { type: 'allowed_symbols', value: ['BTC', 'ETH', 'SOL', 'BNB', 'XRP'] },
    ],
  },
];

function findTemplate(key) {
  return TEMPLATES.find((t) => t.key === String(key || '')) || null;
}

module.exports = { TEMPLATES, findTemplate };
