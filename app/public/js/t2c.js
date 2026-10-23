'use strict';

function esc(s) {
  return String(s)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;');
}

function show(v) {
  if (v === null || v === undefined) return '<span class="tone-muted">not read</span>';
  return esc(v);
}

function row(label, value) {
  return '<div class="row"><div class="k">' + esc(label) + '</div><div class="v">' + value + '</div></div>';
}

function panel(title, inner) {
  return '<section class="panel"><h2>' + esc(title) + '</h2>' + inner + '</section>';
}

function render(body) {
  const price = body.price || {};
  const duration = body.duration || {};
  const backtest = body.backtest || {};
  const etherfi = body.etherfi || {};
  const eth = etherfi.eth || {};
  const steth = etherfi.steth || {};
  const obs = body.observation || {};
  const scenario = obs.scenario || {};
  const cap = body.cap || {};
  const lines = [
    row('Flag', body.flag_on ? 'on' : 'off'),
    row('P', show(price.p)),
    row('Exchange rate', show(price.er)),
    row('Borrow rate', show(price.r_borrow)),
    row('Stake yield', show(price.y_stake) + ' (' + esc(price.y_stake_source || 'not read') + ')'),
    row('T p95, days', show(price.t_p95_days) + ' (' + esc(price.t_p95_source || 'not read') + ')'),
    row('Haircut s', show(price.s)),
    row('Duration p50, days', show(duration.p50_days)),
    row('Duration p95, days', show(duration.p95_days)),
    row('Cap ratio', show(cap.ratio)),
    row('Cap applied', cap.applied === false ? 'no' : show(cap.applied)),
    row('Backtest', esc(backtest.verdict || 'not read')),
    row('Widest stETH discount, bp', show(backtest.widest_steth_discount_bp) + ' on ' + show(backtest.widest_day)),
  ];
  const recorded = [
    row('As of', esc(etherfi.as_of || 'not read')),
    row('Source', 'recorded snapshot, not a live read'),
    row('Instant ETH', show(eth.instant_eth) + ' (' + esc(eth.instant_source || 'not read') + ')'),
    row('ETH pool', show(eth.pool_eth)),
    row('ETH floor', show(eth.floor_eth)),
    row('stETH limit', show(steth.limit_eth)),
    row('Issuer fee, bps', show(steth.issuer_fee_bps)),
  ];
  const queue = [
    row('Queue, ETH', show(obs.queue_eth)),
    row('Quoted wait', esc(obs.quoted_wait || 'not read')),
    row('Sweep, days', show(obs.sweep_days) + ' (' + esc(obs.sweep_source || 'not read') + ')'),
    row('Cap scenario, hours', show(scenario.sum_hours)),
    row('Matches the quote', scenario.matches_quoted_wait === false ? 'no' : show(scenario.matches_quoted_wait)),
  ];
  return panel('Signal', lines.join(''))
    + '<p class="note">' + esc(backtest.reason || '') + '</p>'
    + panel('ether.fi, recorded 23 Oct 2026', recorded.join(''))
    + panel('Exit queue observation', queue.join(''))
    + '<p class="note">' + esc(backtest.weeth_note || '') + '</p>';
}

async function load() {
  const host = document.getElementById('t2c-out');
  if (!host) return;
  try {
    const res = await fetch('/api/t2c/signal', { headers: { Accept: 'application/json' } });
    if (res.status === 404) {
      host.innerHTML = '<p class="tone-muted">This page is not available.</p>';
      return;
    }
    if (!res.ok) {
      host.innerHTML = '<p class="tone-muted">The signal could not be read.</p>';
      return;
    }
    const body = await res.json();
    host.innerHTML = render(body && typeof body === 'object' ? body : {});
  } catch (err) {
    host.innerHTML = '<p class="tone-muted">The signal could not be read.</p>';
  }
}

load();
