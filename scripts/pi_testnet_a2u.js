#!/usr/bin/env node
'use strict';
/**
 * Temporary Pi Testnet App-to-User payouts. The same library as
 * POST /api/pi/testnet-a2u/pay.
 *
 *   node scripts/pi_testnet_a2u.js list
 *   node scripts/pi_testnet_a2u.js pay <uid>
 *
 * Refuses the in-memory database. A list from it would be empty, and a pay
 * would not be the website's book. Source ~/runeclaw-persist/web.env first
 * so DATABASE_URL, the flag and the Testnet key are the website's.
 * Prints codes only. The seed and the API key are not printed.
 */

const path = require('path');
const a2u = require(path.join(__dirname, '..', 'app', 'lib', 'pi_testnet_a2u'));
const { backend } = require(path.join(__dirname, '..', 'app', 'db'));

function fail(code) {
  console.error(code);
  process.exit(1);
}

async function main() {
  const [cmd, uid] = process.argv.slice(2);
  if (cmd !== 'list' && cmd !== 'pay') {
    console.error('usage: node scripts/pi_testnet_a2u.js list');
    console.error('       node scripts/pi_testnet_a2u.js pay <uid>');
    process.exit(2);
  }
  if (backend() !== 'mysql') fail('no_database');
  if (cmd === 'list') {
    const rows = await a2u.listSignins();
    for (const row of rows) {
      const when = row.signed_in_at instanceof Date
        ? row.signed_in_at.toISOString()
        : String(row.signed_in_at);
      console.log(`${row.uid}\t${when}`);
    }
    return;
  }
  if (!uid) {
    console.error('usage: node scripts/pi_testnet_a2u.js pay <uid>');
    process.exit(2);
  }
  const result = await a2u.pay(uid);
  console.log(JSON.stringify({
    uid: result.uid,
    amount: result.amount,
    payment_id: result.payment_id,
    txid: result.txid,
    status: result.status,
  }));
}

main().catch((err) => fail(err && err.code ? err.code : 'failed'));
