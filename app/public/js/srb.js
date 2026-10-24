'use strict';
/**
 * Renders the shadow restitution record.
 *
 * The provider status is the word the record sends. An amount is drawn only
 * from the operator block, and only when that block is present.
 */

var PREVIEW = 'Admin preview. Not public.';

function renderSrb(root, body) {
  var banner = root.querySelector('#srb-banner');
  var provider = root.querySelector('#srb-provider');
  var signals = root.querySelector('#srb-signals');
  var amounts = root.querySelector('#srb-amounts');
  var err = root.querySelector('#srb-err');
  banner.hidden = true;
  banner.textContent = '';
  provider.textContent = '';
  signals.textContent = '';
  amounts.hidden = true;
  amounts.textContent = '';
  err.hidden = true;
  err.textContent = '';
  if (!body || !body.public) {
    err.hidden = false;
    err.textContent = 'The shadow record could not be read.';
    return;
  }
  if (body.preview === true) {
    banner.hidden = false;
    banner.textContent = body.banner || PREVIEW;
  }
  var providers = body.public.providers || [];
  var adminLabel = body.admin && typeof body.admin.label === 'string' ? body.admin.label : '';
  if (providers.length) {
    var row = providers[0];
    var status = adminLabel ? adminLabel : row.status;
    provider.textContent = row.provider_id + ': ' + status;
  } else {
    provider.textContent = 'No provider in this record.';
  }
  var list = body.public.signals || [];
  if (!list.length) {
    var empty = document.createElement('p');
    empty.className = 'srb-sentence';
    empty.textContent = 'No public signal in this record.';
    signals.appendChild(empty);
  }
  for (var i = 0; i < list.length; i++) {
    var signal = list[i];
    var block = document.createElement('div');
    block.className = 'signal';
    var sentence = document.createElement('p');
    sentence.className = 'srb-sentence';
    sentence.textContent = signal.sentence || '';
    var meta = document.createElement('p');
    meta.className = 'meta';
    var ratio = (typeof signal.restitution_bps === 'number')
      ? (' · restitution ' + signal.restitution_bps + ' bps')
      : '';
    meta.textContent = 'Slots ' + signal.slot_lo + '–' + signal.slot_hi
      + ' · impact ' + signal.impact_bps + ' bps'
      + ' · ' + signal.restitution
      + ratio;
    block.appendChild(sentence);
    block.appendChild(meta);
    signals.appendChild(block);
  }
  var adminAmounts = body.admin && body.admin.amounts;
  var recorded = adminAmounts && adminAmounts.signals && adminAmounts.signals[0]
    ? adminAmounts.signals[0].would_record_base_units
    : undefined;
  var bond = adminAmounts ? adminAmounts.bond_base_units : undefined;
  var parts = [];
  if (typeof recorded === 'number') parts.push('Recorded base units: ' + String(recorded));
  if (typeof bond === 'number') parts.push('Bond base units: ' + String(bond));
  if (body.admin && body.admin.flag_on === true) parts.push('SRB_ENABLED is on.');
  else if (body.admin && body.admin.flag_on === false) parts.push('SRB_ENABLED is off.');
  if (parts.length) {
    amounts.hidden = false;
    amounts.textContent = parts.join(' · ');
  }
}

function boot() {
  var root = document.querySelector('.srb-wrap');
  fetch('/api/srb/signals').then(function (res) {
    if (!res.ok) {
      renderSrb(root, null);
      return null;
    }
    return res.json();
  }).then(function (body) {
    if (body) renderSrb(root, body);
  }).catch(function () {
    renderSrb(root, null);
  });
}

if (typeof module !== 'undefined' && module.exports) {
  module.exports = { PREVIEW: PREVIEW, renderSrb: renderSrb };
} else {
  document.addEventListener('DOMContentLoaded', boot);
}
