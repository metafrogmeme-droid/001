#!/usr/bin/env node
'use strict';
/**
 * Record one RWA pre-check transcript per real contract.
 *
 * RPC URLs come only from RWA_RPC_URL_* in the environment. This file names
 * none. A transcript that contains "http" is refused: the fixture is the
 * JSON-RPC answers, not the endpoint that served them.
 *
 * Run from the repo root:
 *   RWA_RPC_URL_ETHEREUM=… node scripts/rwa_record_fixtures.js
 */

const fs = require('fs');
const path = require('path');
const { precheck } = require('../app/lib/rwa_precheck');
const { httpRpc } = require('../app/lib/rwa_evm');
const { rpcUrls, chainByRef } = require('../app/lib/rwa_chains');

const OUT = path.join(__dirname, '..', 'app', 'test', 'fixtures', 'rwa');

/**
 * Public contracts read on 23 October 2026. An entry with `candidates` keeps
 * the first address whose reading matches `role`.
 */
const ASSETS = [
  {
    file: 'ethereum-erc3643-mspoc.json',
    chain: 'ethereum',
    asset: '0x887C7cb4747039D26fFf92156F33a26E4b306e6b',
    role: 'erc3643',
    note: 'MS PoC Demo Token (MSPOC). ERC-3643 / T-REX proxy, version 4.1.3, paused. Deployed by the official T-REX factory in tx 0x0c39814bbf475b1264976b3ba86c04ad753787ed66def2a2434093ba041085ab.',
  },
  {
    file: 'ethereum-por-wbtc.json',
    chain: 'ethereum',
    asset: '0xa81FE04086865e63E12dD3776978E49DEEa2ea4e',
    role: 'por',
    note: 'The WBTC proof-of-reserve feed itself (description "WBTC PoR"). The WBTC token does not name this feed through the getters this reader checks, so the recorded asset is the feed.',
  },
  {
    file: 'ethereum-nav-jtrsy.json',
    chain: 'ethereum',
    asset: '0x0C2e4Df738e99e8db80012f5bb2a303f3f48Ca74',
    role: 'nav',
    note: 'The JTRSY NAV feed itself (description "JTRSY NAV"). The Janus Henderson token does not name this feed through the getters this reader checks, so the recorded asset is the feed.',
  },
  {
    file: 'base-erc3643-csm.json',
    chain: 'base',
    asset: '0xF2299DcD9c189e0D7e47C55Ee7c7b215ecd37c9D',
    role: 'erc3643',
    note: 'CSM Demo (CSM). ERC-3643 / T-REX proxy, version 4.1.3, paused. Factory tx 0x26bb250ba46a35911a0e6f3d6875be08c49e5c3bff5b90fd2cc5d12c864cd98f.',
  },
  {
    file: 'base-oracle-aapl.json',
    chain: 'base',
    asset: '0x787f13dEa48Db0897CbCDD985de77809D837F988',
    role: 'oracle',
    note: 'Coinbase AAPL price feed on Base (description "Coinbase AAPL"). The description does not classify as proof-of-reserve or NAV, so kind is other. heartbeat() reverts on this proxy. The Base AAPL stock token does not name this feed.',
  },
  {
    file: 'base-stock-aaplc.json',
    chain: 'base',
    asset: '0xb200000000000000000000C2e324d24d7eEcd1fb',
    role: 'marker',
    note: 'Base stock token AAPLc (Apple Inc.). Code is the 0xef marker, not an EVM body. View calls are still read. Selectors are not reported as absent.',
  },
  {
    file: 'avalanche-erc3643-av4t2.json',
    chain: 'avalanche',
    asset: '0x0fbe4ef1f715815366189a747cccc84f317c9b45',
    role: 'erc3643',
    note: 'AV4T2. ERC-3643 / T-REX proxy, paused. Read from the official T-REX factory log on Avalanche C-Chain.',
  },
  {
    file: 'avalanche-por-link-e.json',
    chain: 'avalanche',
    asset: '0x943cEF1B112Ca9FD7EDaDC9A46477d3812a382b6',
    role: 'por',
    note: 'The LINK.e proof-of-reserve feed itself (description "LINK.e PoR"). heartbeat() reverts on this proxy. The recorded asset is the feed.',
  },
  {
    file: 'arbitrum-erc3643.json',
    chain: 'arbitrum',
    role: 'erc3643',
    candidates: [
      '0x6de1f50724489Ba80b26Bc8f8B29d3ae4AE47059',
      '0x212385B9F335e0Bf27a3AD929712E226C7ceb90b',
      '0xefbf1ed588bE84373b4f50AcAaA1A16e1380588C',
    ],
    note: 'ERC-3643 / T-REX proxy from the official factory log on Arbitrum One (token besync transfer, TB2TO, version 4.1.3). The first candidate whose identityRegistry() returned an address was kept.',
  },
  {
    file: 'arbitrum-por-fbtc.json',
    chain: 'arbitrum',
    asset: '0xfb010c715Ff2B85DB9c896f0C030bF9329638905',
    role: 'por',
    note: 'The FBTC proof-of-reserve feed itself (description "FBTC PoR"). heartbeat() reverts on this proxy. The recorded asset is the feed.',
  },
  {
    file: 'polygon-por-bnvda.json',
    chain: 'polygon',
    asset: '0x0fB2beD999da86Cb1Fdd97E746600A96141EeA09',
    role: 'por',
    note: 'The bNVDA reserves feed itself. The bNVDA token was not shown to name this feed through the getters this reader checks, so the recorded asset is the feed.',
  },
  {
    file: 'polygon-erc3643-egx.json',
    chain: 'polygon',
    asset: '0xA8C557c7ac1626EacAa0e80fAc7b6997346306E8',
    role: 'erc3643',
    note: 'ENEGRA (EGX). ERC-3643 / T-REX proxy, version 4.1.3. Factory tx 0x7c6e51916eab34afeca755264f669fed7bad661ce78fa19087b6e6795a4fda03.',
  },
  {
    file: 'bnb-tokenized-tbill.json',
    chain: 'bnb',
    asset: '0x5b4681F0d7A01B817675F25892D3Ad73572FD1D9',
    role: 'tokenized',
    note: 'OpenEden T-Bills (TBILL), a tokenized T-bill vault. The official T-REX factory 0x7F1fD450a5c4d91286f00F3297dCD5C12767f17f had no code on BNB Chain on 23 October 2026, and BNB Chain is not in the ERC-3643 association production list, so this is not an ERC-3643 reading. The vault does not name a feed through the getters this reader checks.',
  },
  {
    file: 'zksync-tokenized-bcap.json',
    chain: 'zksync',
    asset: '0x57fd71a86522dc06d6255537521886057c1772a3',
    role: 'tokenized',
    note: 'Blockchain Capital Token (BCAP), a tokenized fund. The official T-REX factory 0x7F1fD450a5c4d91286f00F3297dCD5C12767f17f had no code on ZKsync Era on 23 October 2026, and ZKsync Era is not in the ERC-3643 association production list, so this is not an ERC-3643 reading. The bytecode is EraVM, so a selector that was not seen is not proof it is absent. The token does not name a feed through the getters this reader checks.',
  },
];

function recordingRpc(urls) {
  const inner = httpRpc(urls, fetch, 20000);
  const calls = [];
  const rpc = async (method, params) => {
    const res = await inner(method, params);
    const row = { method, params, ok: res.ok === true };
    if (res.ok === true) row.result = res.result;
    else row.reason = res.reason || 'unreadable';
    calls.push(row);
    return res;
  };
  return { rpc, calls };
}

function accept(role, reading) {
  if (!reading || reading.read !== true) return 'read:' + (reading && reading.reason);
  const ids = (reading.flags || []).map((f) => f.id + ':' + f.state);
  if (role === 'erc3643' && !ids.includes('erc3643:detected')) return 'not erc3643';
  if (role === 'por') {
    if (!reading.oracle || reading.oracle.kind !== 'por') return 'kind ' + (reading.oracle && reading.oracle.kind);
  }
  if (role === 'nav') {
    if (!reading.oracle || reading.oracle.kind !== 'nav') return 'kind ' + (reading.oracle && reading.oracle.kind);
  }
  if (role === 'oracle') {
    if (!reading.oracle || reading.oracle.referenced !== true) return 'no feed';
  }
  if (role === 'marker' && reading.code !== 'marker') return 'code ' + reading.code;
  return null;
}

function oracleExpect(oracle) {
  if (!oracle) return null;
  return {
    referenced: oracle.referenced === true,
    kind: oracle.kind || null,
    source: oracle.source || null,
    stale: Object.prototype.hasOwnProperty.call(oracle, 'stale') ? oracle.stale : null,
    update: oracle.update || null,
    age_seconds: Object.prototype.hasOwnProperty.call(oracle, 'age_seconds') ? oracle.age_seconds : null,
    heartbeat_seconds: Object.prototype.hasOwnProperty.call(oracle, 'heartbeat_seconds')
      ? oracle.heartbeat_seconds : null,
  };
}

async function recordOne(spec, asset) {
  const chain = chainByRef(spec.chain);
  const urls = rpcUrls(process.env, chain);
  if (!urls.length) {
    return { error: 'rpc_unconfigured' };
  }
  const rec = recordingRpc(urls);
  const reading = await precheck({
    chain: spec.chain,
    asset,
    rpc: rec.rpc,
    env: process.env,
    timeoutMs: 20000,
  });
  const why = accept(spec.role, reading);
  if (why) {
    return {
      error: why,
      token: reading && reading.token,
      flags: reading && reading.flags && reading.flags.map((f) => f.id + ':' + f.state),
      code: reading && reading.code,
    };
  }
  const body = {
    chain: spec.chain,
    asset: reading.asset,
    role: spec.role,
    note: spec.note,
    recorded_on: '2026-10-23',
    block: reading.block,
    token: reading.token,
    code: reading.code,
    flags: reading.flags.map((f) => f.id + ':' + f.state),
    unreadable: reading.unreadable,
    oracle: oracleExpect(reading.oracle),
    calls: rec.calls,
  };
  const raw = JSON.stringify(body);
  if (/https?:\/\//i.test(raw)) {
    return { error: 'transcript contains a URL' };
  }
  fs.mkdirSync(OUT, { recursive: true });
  const dest = path.join(OUT, spec.file);
  fs.writeFileSync(dest, JSON.stringify(body));
  return {
    ok: true,
    file: spec.file,
    asset: reading.asset,
    bytes: raw.length,
    calls: rec.calls.length,
    token: reading.token,
    flags: body.flags,
    unreadable: body.unreadable,
    oracle: body.oracle,
    block: reading.block,
  };
}

async function main() {
  const only = process.env.RWA_ONLY
    ? new Set(process.env.RWA_ONLY.split(',').map((s) => s.trim()).filter(Boolean))
    : null;
  const summaries = [];
  for (const spec of ASSETS) {
    if (only && !only.has(spec.file)) continue;
    const list = spec.candidates || [spec.asset];
    let kept = null;
    const tried = [];
    for (const asset of list) {
      process.stderr.write('record ' + spec.chain + ' ' + asset + '\n');
      const result = await recordOne(spec, asset);
      tried.push({ asset, result });
      if (result.ok) {
        kept = result;
        break;
      }
      process.stderr.write('  skip ' + result.error + '\n');
    }
    summaries.push({ file: spec.file, chain: spec.chain, role: spec.role, kept, tried: tried.map((t) => ({
      asset: t.asset,
      error: t.result.ok ? null : t.result.error,
      token: t.result.token || null,
    })) });
  }
  const brief = summaries.map((s) => ({
    file: s.file,
    chain: s.chain,
    role: s.role,
    ok: Boolean(s.kept),
    asset: s.kept ? s.kept.asset : null,
    token: s.kept ? s.kept.token : (s.tried[0] && s.tried[0].token),
    bytes: s.kept ? s.kept.bytes : null,
    calls: s.kept ? s.kept.calls : null,
    flags: s.kept ? s.kept.flags : null,
    unreadable: s.kept ? s.kept.unreadable : null,
    oracle: s.kept ? s.kept.oracle : null,
    block: s.kept ? s.kept.block : null,
    attempts: s.tried,
  }));
  process.stdout.write(JSON.stringify(brief, null, 2) + '\n');
  if (brief.some((s) => !s.ok && s.role === 'erc3643' && s.chain !== 'arbitrum')) process.exitCode = 1;
}

main().catch((err) => {
  process.stderr.write((err && err.constructor ? err.constructor.name : 'Error') + '\n');
  process.exit(1);
});
