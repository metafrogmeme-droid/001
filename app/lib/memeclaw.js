'use strict';
/**
 * Memeclaw: a memecoin launchpad on the DogeOS testnet (docs/memeclaw/
 * MEMECLAW_PLAN.md). This module is what the website knows about it: the
 * contract's ABI, the page's rules for a launch, the terms it seals, the
 * transactions it hands a wallet, and the readings of the chain it records.
 *
 * NON-CUSTODIAL. Nothing here signs or sends. A plan is a transaction for
 * the person's own wallet; a verification reads the chain's receipt and
 * records only what the chain says.
 *
 * NO ACCOUNT. The wallet that sends the launch is the creator: the chain
 * says so (`Launched.creator` is msg.sender), and the terms hash binds the
 * creator in, so terms planned for one wallet cannot be launched by another.
 *
 * ONE READING OF THE CONTRACT. The ABI and the runtime code are the
 * committed artifact (contracts/memeclaw/compiled, copied into app/content by
 * scripts/sync_content.js because the web deploy ships app/ alone). Call
 * signatures and events are derived from that ABI, not typed here.
 *
 * NOT ETHERS. Production resolves `ethers` to a stub (app/test/
 * ethers_stub_calldata.test.js), so calls are encoded by abi_call.js and
 * logs decoded by abi_decode.js.
 */
const crypto = require('crypto');

const ARTIFACT = require('../content/memeclaw/MemeclawFactory.json');
const RESERVED = require('./memeclaw_reserved.json');
const { encodeCall } = require('./abi_call');
const { decodeLog, decodeWords } = require('./abi_decode');
const curve = require('./memeclaw_curve');
const chain = require('./memeclaw_chain');

const FACTORY = ARTIFACT.contracts.MemeclawFactory;
const TOKEN = ARTIFACT.contracts.MemeclawToken;

const ADDR_RE = /^0x[0-9a-f]{40}$/;
const TX_RE = /^0x[0-9a-f]{64}$/;
const HASH_RE = /^0x[0-9a-f]{64}$/;
const WEI_RE = /^\d{1,40}$/;

const MAX_NAME_BYTES = 32;   // the contract's MAX_NAME_BYTES (the contracts test ties them)
const MAX_SYMBOL_BYTES = 10; // the contract's MAX_SYMBOL_BYTES
const MAX_DESCRIPTION = 280;
const PLAN_TTL_S = 20 * 60;  // a wallet has twenty minutes to send a planned transaction
const MAX_SLIPPAGE_BPS = 1000;
const DEFAULT_SLIPPAGE_BPS = 100;

// ── the ABI, read from the artifact ─────────────────────────────────────────

function entry(abi, type, name) {
  const e = abi.find((x) => x.type === type && x.name === name);
  if (!e) throw new Error(`memeclaw: the artifact has no ${type} ${name}`);
  return e;
}
function sigOf(abi, name) {
  return `${name}(${entry(abi, 'function', name).inputs.map((i) => i.type).join(',')})`;
}
function eventOf(abi, name) {
  return { name, inputs: entry(abi, 'event', name).inputs.map((i) => ({ name: i.name, type: i.type, indexed: !!i.indexed })) };
}

const CALLS = {
  launch: sigOf(FACTORY.abi, 'launch'),
  buy: sigOf(FACTORY.abi, 'buy'),
  sell: sigOf(FACTORY.abi, 'sell'),
  curves: sigOf(FACTORY.abi, 'curves'),
  approve: sigOf(TOKEN.abi, 'approve'),
  allowance: sigOf(TOKEN.abi, 'allowance'),
  balanceOf: sigOf(TOKEN.abi, 'balanceOf'),
};
const EVENTS = { Launched: eventOf(FACTORY.abi, 'Launched'), Trade: eventOf(FACTORY.abi, 'Trade') };
const CURVES_OUT = entry(FACTORY.abi, 'function', 'curves').outputs.map((o) => o.type);

const lower = (s) => String(s == null ? '' : s).trim().toLowerCase();
const eq = (a, b) => lower(a) === lower(b);

// ── the page's rules for a launch ───────────────────────────────────────────

const RESERVED_SYMBOLS = new Map();
for (const [kind, list] of Object.entries(RESERVED.symbols)) {
  for (const s of list) if (!RESERVED_SYMBOLS.has(s)) RESERVED_SYMBOLS.set(s, kind);
}
// Our own names inside a coin's name ("Official RUNECLAW Inu").
const PROJECT_WORDS = /\b(rune\s*claw|rclaw|meme\s*claw|humanoid\s*traders?)\b/i;
// A coin presented as a stock, a fund or a tokenized asset (owner, 21 Oct:
// no tokenized stocks).
const AS_A_SECURITY = /\b(stocks?|shares?|equit(y|ies)|tokeni[sz]ed|etfs?|xstocks?|securit(y|ies)|dividends?|ipo)\b/i;
// A link of any kind, in a name or a description: the page is not a place to
// send people elsewhere.
const LINKISH = /(https?:|www\.|t\.me\b|discord\.(gg|com)|\b[a-z0-9-]+\.(com|io|xyz|net|org|app|gg|me|co|finance|fi|dev|ai|fun|meme|lol|club|site|online)\b)/i;
const CONTROL = /[\u0000-\u001f\u007f-\u009f​-‏‪-‮⁠-⁤﻿]/;

/** The contract's own rules for a name: printable ASCII, 1 to 32 bytes, no edge spaces. */
function chainNameProblem(name) {
  const s = String(name);
  if (s.length < 1 || s.length > MAX_NAME_BYTES) return 'name_length';
  if (!/^[\x20-\x7e]+$/.test(s)) return 'name_characters';
  if (s[0] === ' ' || s[s.length - 1] === ' ') return 'name_spaces';
  return null;
}

/** The contract's own rules for a symbol: A-Z and 0-9, 1 to 10. */
function chainSymbolProblem(symbol) {
  const s = String(symbol);
  if (s.length < 1 || s.length > MAX_SYMBOL_BYTES) return 'symbol_length';
  if (!/^[A-Z0-9]+$/.test(s)) return 'symbol_characters';
  return null;
}

/**
 * The page's rules on top of the contract's: null when a coin may be listed,
 * or { reason, detail }. Applied to a launch planned here AND to a coin
 * launched straight at the contract (which the indexer lists hidden when it
 * fails), so the page never shows a name it would have refused.
 */
function pageProblem({ name, symbol, description }) {
  const c = chainNameProblem(name) || chainSymbolProblem(symbol);
  if (c) return { reason: c };
  const kind = RESERVED_SYMBOLS.get(String(symbol));
  if (kind) return { reason: 'symbol_reserved', detail: kind };
  if (PROJECT_WORDS.test(name)) return { reason: 'name_reserved', detail: 'project' };
  const words = String(name).toUpperCase().split(/[^A-Z0-9]+/).filter(Boolean);
  const taken = words.find((w) => RESERVED_SYMBOLS.get(w) === 'tokenized' || RESERVED_SYMBOLS.get(w) === 'project');
  if (taken) return { reason: 'name_reserved', detail: RESERVED_SYMBOLS.get(taken) };
  if (AS_A_SECURITY.test(name) || AS_A_SECURITY.test(description || '')) return { reason: 'presented_as_security' };
  if (LINKISH.test(name)) return { reason: 'name_link' };
  if (description != null && description !== '') {
    const d = String(description);
    if (d.length > MAX_DESCRIPTION) return { reason: 'description_length' };
    if (CONTROL.test(d.replace(/\n/g, ''))) return { reason: 'description_characters' };
    if (LINKISH.test(d)) return { reason: 'description_link' };
  }
  return null;
}

/** A description as stored: trimmed, inner whitespace runs collapsed, '' when empty. */
function cleanDescription(d) {
  return String(d == null ? '' : d).replace(/[ \t]+/g, ' ').replace(/\n{3,}/g, '\n\n').trim();
}

// ── the terms and their hash ────────────────────────────────────────────────

/**
 * The terms a launch is sealed under, as canonical JSON (fixed key order).
 * The creator, the chain and the factory are in it, so the same words planned
 * for another wallet or another deployment hash differently.
 */
function canonicalTerms(t) {
  return JSON.stringify({
    v: 1,
    kind: 'memeclaw_terms',
    chain_id: Number(t.chain_id),
    factory: lower(t.factory),
    creator: lower(t.creator),
    name: String(t.name),
    symbol: String(t.symbol),
    description: cleanDescription(t.description),
  });
}

function termsHash(t) {
  return '0x' + crypto.createHash('sha256').update(canonicalTerms(t), 'utf8').digest('hex');
}

// ── plans: the transactions a wallet is handed ──────────────────────────────

function nowSeconds(now) {
  return Math.floor((now instanceof Date ? now.getTime() : Number(now || Date.now())) / 1000);
}

/**
 * A launch, planned: { ok: true, plan } or { ok: false, reason, detail }.
 * `devBuy` is native coin in base units (a decimal string), '0' for none.
 */
function launchPlan({ factory, chainId, creator, name, symbol, description, devBuy = '0', now }) {
  if (!ADDR_RE.test(lower(factory))) return { ok: false, reason: 'not_deployed' };
  if (!ADDR_RE.test(lower(creator))) return { ok: false, reason: 'creator_address' };
  if (!WEI_RE.test(String(devBuy))) return { ok: false, reason: 'dev_buy_amount' };
  const desc = cleanDescription(description);
  const problem = pageProblem({ name, symbol, description: desc });
  if (problem) return { ok: false, ...problem };
  const value = BigInt(devBuy);
  const fresh = { dogeReserve: 0n, tokenReserve: curve.SUPPLY };
  if (value > curve.maxBuy(fresh)) return { ok: false, reason: 'dev_buy_too_large' };
  // A brand-new curve has no other trades, so the creator's buy receives
  // exactly the quote: the floor is the quote itself.
  const expected = value > 0n ? curve.quoteBuy(fresh, value) : 0n;
  if (value > 0n && expected === 0n) return { ok: false, reason: 'dev_buy_too_small' };
  const terms = { chain_id: chainId, factory: lower(factory), creator: lower(creator), name, symbol, description: desc };
  const hash = termsHash(terms);
  const deadline = nowSeconds(now) + PLAN_TTL_S;
  return {
    ok: true,
    plan: {
      chain_id: Number(chainId),
      from: lower(creator),
      to: lower(factory),
      value: '0x' + value.toString(16),
      data: encodeCall(CALLS.launch, [name, symbol, hash, expected, deadline]),
      deadline,
      terms,
      terms_payload: canonicalTerms(terms),
      terms_hash: hash,
      dev_buy: value.toString(),
      expected_tokens: expected.toString(),
    },
  };
}

/** The live curve of a coin, read from the factory: { dogeReserve, tokenReserve, creator, launchedAt } or null. */
async function readCurve(factory, token) {
  const out = await chain.call(lower(factory), encodeCall(CALLS.curves, [lower(token)]));
  const [dogeReserve, tokenReserve, creator, launchedAt] = decodeWords(CURVES_OUT, out);
  if (/^0x0{40}$/.test(creator)) return null; // not a coin of this factory
  return { dogeReserve, tokenReserve, creator, launchedAt };
}

async function readAllowance(token, owner, spender) {
  const out = await chain.call(lower(token), encodeCall(CALLS.allowance, [lower(owner), lower(spender)]));
  return decodeWords(['uint256'], out)[0];
}

async function readHeld(token, owner) {
  const out = await chain.call(lower(token), encodeCall(CALLS.balanceOf, [lower(owner)]));
  return decodeWords(['uint256'], out)[0];
}

/**
 * A buy or a sell, planned against the LIVE curve: { ok, plans: [ ... ], quote }.
 * A sell the factory is not yet approved for comes as two transactions,
 * approve then sell. Throws ChainUnreadable when the chain does not answer.
 */
async function tradePlan({ factory, chainId, action, token, wallet, amount, slippageBps = DEFAULT_SLIPPAGE_BPS, now }) {
  if (!ADDR_RE.test(lower(factory))) return { ok: false, reason: 'not_deployed' };
  if (!ADDR_RE.test(lower(token))) return { ok: false, reason: 'coin_address' };
  if (!ADDR_RE.test(lower(wallet))) return { ok: false, reason: 'wallet_address' };
  if (!WEI_RE.test(String(amount)) || BigInt(amount) === 0n) return { ok: false, reason: 'amount' };
  const bps = Number(slippageBps);
  if (!Number.isInteger(bps) || bps < 0 || bps > MAX_SLIPPAGE_BPS) return { ok: false, reason: 'slippage' };
  const live = await readCurve(factory, token);
  if (!live) return { ok: false, reason: 'unknown_coin' };
  const state = { dogeReserve: live.dogeReserve, tokenReserve: live.tokenReserve };
  const value = BigInt(amount);
  const deadline = nowSeconds(now) + PLAN_TTL_S;
  const base = { chain_id: Number(chainId), from: lower(wallet), deadline };
  if (action === 'buy') {
    const quote = curve.quoteBuy(state, value);
    if (quote === null) return { ok: false, reason: 'sold_out', max_buy: curve.maxBuy(state).toString() };
    if (quote === 0n) return { ok: false, reason: 'too_small' };
    const floor = quote * BigInt(10000 - bps) / 10000n;
    return {
      ok: true,
      quote: { gives: value.toString(), gets: quote.toString(), floor: floor.toString(), slippage_bps: bps },
      plans: [{ ...base, step: 'buy', to: lower(factory), value: '0x' + value.toString(16),
        data: encodeCall(CALLS.buy, [lower(token), floor, deadline]) }],
    };
  }
  if (action === 'sell') {
    const held = await readHeld(token, wallet);
    if (held < value) return { ok: false, reason: 'not_held', held: held.toString() };
    const quote = curve.quoteSell(state, value);
    if (quote === 0n) return { ok: false, reason: 'too_small' };
    const floor = quote * BigInt(10000 - bps) / 10000n;
    const plans = [];
    if ((await readAllowance(token, wallet, factory)) < value) {
      plans.push({ ...base, step: 'approve', to: lower(token), value: '0x0',
        data: encodeCall(CALLS.approve, [lower(factory), value]) });
    }
    plans.push({ ...base, step: 'sell', to: lower(factory), value: '0x0',
      data: encodeCall(CALLS.sell, [lower(token), value, floor, deadline]) });
    return { ok: true, quote: { gives: value.toString(), gets: quote.toString(), floor: floor.toString(), slippage_bps: bps }, plans };
  }
  return { ok: false, reason: 'action' };
}

// ── readings of the chain ───────────────────────────────────────────────────

/** Factory events in one receipt's or one getLogs answer's logs, in chain order. */
function factoryEvents(logs, factory) {
  const out = [];
  for (const l of logs || []) {
    if (!eq(l.address, factory)) continue;
    const ev = decodeLog(EVENTS.Launched, l) || decodeLog(EVENTS.Trade, l);
    if (!ev) continue;
    out.push({ ...ev, tx_hash: lower(l.transactionHash), log_index: Number(BigInt(l.logIndex)),
      block_number: Number(BigInt(l.blockNumber)) });
  }
  return out.sort((a, b) => (a.block_number - b.block_number) || (a.log_index - b.log_index));
}

/**
 * A transaction's state on the chain: { status: 'pending' | 'success' |
 * 'failed' | 'missing' } or { status: 'unknown', reason } when the chain did
 * not answer. What the page waits on between an approval and a sale.
 */
async function txState(txHash) {
  const tx = lower(txHash);
  if (!TX_RE.test(tx)) return { status: 'missing', reason: 'malformed transaction hash' };
  try {
    const rc = await chain.receipt(tx);
    if (rc) return { status: rc.status === '0x1' ? 'success' : 'failed', block_number: Number(BigInt(rc.blockNumber)) };
    return (await chain.transaction(tx)) ? { status: 'pending' } : { status: 'missing' };
  } catch (e) {
    return { status: 'unknown', reason: `the chain could not be read (${e && e.name ? e.name : 'Error'})` };
  }
}

/**
 * The Trade events of one mined transaction at the factory:
 *   { status: 'verified', trades, block_number, block_time } | mismatch | unknown.
 */
async function readTrades(txHash, factory) {
  const tx = lower(txHash);
  if (!TX_RE.test(tx)) return { status: 'mismatch', reason: 'malformed transaction hash' };
  let rc;
  try { rc = await chain.receipt(tx); } catch (e) {
    return { status: 'unknown', reason: `the chain could not be read (${e && e.name ? e.name : 'Error'})` };
  }
  if (!rc) return { status: 'unknown', reason: 'not yet mined' };
  if (rc.status !== '0x1') return { status: 'mismatch', reason: 'the transaction failed on chain' };
  const trades = factoryEvents(rc.logs, factory).filter((e) => e.name === 'Trade').map((e) => ({ ...e, tx_hash: tx }));
  if (!trades.length) return { status: 'mismatch', reason: 'no trade at the Memeclaw factory in this transaction' };
  const blockNumber = Number(BigInt(rc.blockNumber));
  let blockTime = null;
  try {
    const b = await chain.block(blockNumber);
    blockTime = b && b.timestamp ? new Date(Number(BigInt(b.timestamp)) * 1000) : null;
  } catch (e) { blockTime = null; }
  if (!blockTime) return { status: 'unknown', reason: 'the block time could not be read' };
  return { status: 'verified', trades, block_number: blockNumber, block_time: blockTime };
}

/**
 * Read a launch transaction from the chain.
 *   { status: 'verified', launch, trades, block_number, block_time }
 *   { status: 'mismatch', reason }   it exists and is not a launch at the factory
 *   { status: 'unknown', reason }    the chain could not say (yet): NOT a verdict
 */
async function readLaunch(txHash, factory) {
  const tx = lower(txHash);
  if (!TX_RE.test(tx)) return { status: 'mismatch', reason: 'malformed transaction hash' };
  let rc;
  try {
    rc = await chain.receipt(tx);
    if (!rc) {
      const sent = await chain.transaction(tx);
      return sent ? { status: 'unknown', reason: 'not yet mined' }
        : { status: 'mismatch', reason: 'no such transaction on the DogeOS testnet' };
    }
  } catch (e) {
    return { status: 'unknown', reason: `the chain could not be read (${e && e.name ? e.name : 'Error'})` };
  }
  if (rc.status !== '0x1') return { status: 'mismatch', reason: 'the transaction failed on chain' };
  if (!eq(rc.to, factory)) return { status: 'mismatch', reason: 'not sent to the Memeclaw factory' };
  const events = factoryEvents(rc.logs, factory).map((e) => ({ ...e, tx_hash: tx }));
  const launched = events.filter((e) => e.name === 'Launched');
  if (launched.length !== 1) return { status: 'mismatch', reason: 'no launch in this transaction' };
  const blockNumber = Number(BigInt(rc.blockNumber));
  let blockTime = null;
  try {
    const b = await chain.block(blockNumber);
    blockTime = b && b.timestamp ? new Date(Number(BigInt(b.timestamp)) * 1000) : null;
  } catch (e) { blockTime = null; }
  if (!blockTime) return { status: 'unknown', reason: 'the block time could not be read' };
  return { status: 'verified', launch: launched[0], trades: events.filter((e) => e.name === 'Trade'),
    block_number: blockNumber, block_time: blockTime };
}

/**
 * Is the code at `address` the committed factory, on the DogeOS testnet?
 *   { status: 'verified' } | { status: 'mismatch', reason } | { status: 'unknown', reason }
 */
async function checkFactory(address) {
  const a = lower(address);
  if (!ADDR_RE.test(a)) return { status: 'mismatch', reason: 'not an address' };
  try {
    const id = await chain.chainId();
    if (id !== chain.chain().chain_id) return { status: 'mismatch', reason: `the RPC answered for chain ${id}` };
    const code = lower(await chain.code(a));
    if (!code || code === '0x') return { status: 'mismatch', reason: 'no contract at that address' };
    if (code !== lower(FACTORY.deployedBytecode)) return { status: 'mismatch', reason: 'the code there is not the Memeclaw factory this site carries' };
    return { status: 'verified' };
  } catch (e) {
    return { status: 'unknown', reason: `the chain could not be read (${e && e.name ? e.name : 'Error'})` };
  }
}

/** The factory's deployment, from the operator's deploy transaction: { status, address, block_number }. */
async function readDeployment(txHash) {
  const tx = lower(txHash);
  if (!TX_RE.test(tx)) return { status: 'mismatch', reason: 'malformed transaction hash' };
  let rc;
  try { rc = await chain.receipt(tx); } catch (e) {
    return { status: 'unknown', reason: `the chain could not be read (${e && e.name ? e.name : 'Error'})` };
  }
  if (!rc) return { status: 'unknown', reason: 'not yet mined, or no such transaction' };
  if (rc.status !== '0x1' || !rc.contractAddress) return { status: 'mismatch', reason: 'not a successful contract creation' };
  const check = await checkFactory(rc.contractAddress);
  if (check.status !== 'verified') return check;
  return { status: 'verified', address: lower(rc.contractAddress), block_number: Number(BigInt(rc.blockNumber)) };
}

/** The deploy transaction the operator's wallet sends: the factory's creation code, nothing else. */
function deployPlan(chainId) {
  return { chain_id: Number(chainId), value: '0x0', data: FACTORY.bytecode };
}

module.exports = {
  ARTIFACT, FACTORY, TOKEN, CALLS, EVENTS, RESERVED_SYMBOLS,
  MAX_NAME_BYTES, MAX_SYMBOL_BYTES, MAX_DESCRIPTION, PLAN_TTL_S, MAX_SLIPPAGE_BPS, DEFAULT_SLIPPAGE_BPS,
  ADDR_RE, TX_RE, HASH_RE, WEI_RE,
  chainNameProblem, chainSymbolProblem, pageProblem, cleanDescription,
  canonicalTerms, termsHash, launchPlan, tradePlan,
  readCurve, readAllowance, readHeld, factoryEvents, txState, readTrades, readLaunch, checkFactory, readDeployment, deployPlan,
  lower,
};
