'use strict';
/**
 * One reading of bot/xnav/placeholders.json.
 * A missing file or a missing key throws. It is not filled with zero.
 */

const fs = require('node:fs');
const path = require('node:path');

const FILE = path.join(__dirname, '..', '..', '..', 'bot', 'xnav', 'placeholders.json');

const REQUIRED = [
  'staleness_seconds', 'max_swing_bps', 'flow_threshold_bps', 'delta_cap_bps',
  'twap_snapshots', 'max_feed_bps', 'stress_depth_num', 'stress_depth_den',
  'stress_shift_bps', 'equity_scale', 'l1_read_gas_base', 'l1_read_gas_per_word',
  'epoch_seconds', 'hyperevm_testnet_chain_id', 'hyperevm_mainnet_chain_id',
  'eip712_name', 'eip712_version', 'eip712_domain', 'eip712_type',
  'add_api_wallet_action',
];

function loadPlaceholders(file) {
  const src = file || FILE;
  let data;
  try {
    data = JSON.parse(fs.readFileSync(src, 'utf8'));
  } catch (err) {
    const error = new Error('PlaceholdersUnreadable');
    error.code = 'PlaceholdersUnreadable';
    throw error;
  }
  if (!data || typeof data !== 'object' || Array.isArray(data)) {
    const error = new Error('PlaceholdersUnreadable');
    error.code = 'PlaceholdersUnreadable';
    throw error;
  }
  for (const key of REQUIRED) {
    if (!Object.prototype.hasOwnProperty.call(data, key)) {
      const error = new Error('PlaceholdersUnreadable');
      error.code = 'PlaceholdersUnreadable';
      throw error;
    }
  }
  return data;
}

function l1ReadGas(inWords, outWords, table) {
  const src = table || loadPlaceholders();
  return src.l1_read_gas_base + src.l1_read_gas_per_word * (inWords + outWords);
}

module.exports = { FILE, loadPlaceholders, l1ReadGas };
