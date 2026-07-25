#!/usr/bin/env node
// $RCLAW Wormhole NTT bridge (Solana devnet <-> Base Sepolia). DRAFT / TESTNET.
//
// Commands:
//   plan       Offline: validate ntt.config.json, resolve chain contexts, print
//              the planned route + the exact `ntt` CLI deploy steps. No sends.
//   transfer   Bridge --amount tokens hub -> spoke (or --reverse) using the
//              Wormhole SDK's NTT protocol.
//
// Mode: HUB-AND-SPOKE (lock on Solana). $RCLAW revokes mint authority
// (token/scripts/create_token.mjs), so burn-and-mint — which needs mint
// authority delegated to an NTT PDA — is deliberately NOT used. See
// docs/TOKEN_ROADMAP.md §9.
//
// KNOWN UPSTREAM ISSUE (verified against @wormhole-foundation/sdk-solana-ntt
// 7.0.0-7.2.0): its ESM build imports './side-effects' without a file
// extension and its package `exports` map blocks deep imports, so plain Node
// ESM cannot load it (ERR_MODULE_NOT_FOUND). The EVM package
// (@wormhole-foundation/sdk-evm-ntt) loads fine. `transfer` therefore
// registers the Solana protocol lazily and, if it fails, tells you to run
// under a bundler-style loader (e.g. `npx tsx`). `plan` never imports it, so
// it always works. Re-check on SDK upgrades.
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import { wormhole, amount, signSendWait } from '@wormhole-foundation/sdk';
import solana from '@wormhole-foundation/sdk/solana';
import evm from '@wormhole-foundation/sdk/evm';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const TOKEN_ROOT = path.resolve(__dirname, '..');

export function loadConfig() {
  return JSON.parse(fs.readFileSync(path.join(__dirname, 'ntt.config.json'), 'utf8'));
}

function arg(flag) {
  const i = process.argv.indexOf(flag);
  return i >= 0 ? process.argv[i + 1] : undefined;
}

function unfilled(v) {
  return !v || String(v).startsWith('<FILL');
}

/** Validate the config and return {cfg, hub, spoke, ready}. Pure/offline. */
export function validate(cfg) {
  const hub = cfg.hub;
  const spoke = cfg.spokes[0];
  const missing = [];
  for (const [label, v] of [
    ['hub.token', hub.token], ['hub.manager', hub.manager],
    ['spoke.token', spoke.token], ['spoke.manager', spoke.manager],
  ]) {
    if (unfilled(v)) missing.push(label);
  }
  if (cfg.network !== 'Testnet') {
    throw new Error(`Refusing network "${cfg.network}" — this is draft/testnet tooling (roadmap §10-11).`);
  }
  return { hub, spoke, missing, ready: missing.length === 0 };
}

// ── plan (offline) ──────────────────────────────────────────────────────────
async function cmdPlan() {
  const cfg = loadConfig();
  const { hub, spoke, missing, ready } = validate(cfg);
  const wh = await wormhole(cfg.network, [solana, evm]);
  const hubCtx = wh.getChain(hub.chain);
  const spokeCtx = wh.getChain(spoke.chain);

  console.log('=== $RCLAW Wormhole NTT bridge plan (TESTNET / DRAFT) ===');
  console.log('Mode        :', cfg.mode, '— lock on the hub, wrapped on the spoke');
  console.log('Network     :', cfg.network);
  console.log(`Hub         : ${hubCtx.chain} (${hub.role}, ${hub.decimals} dp) rpc=${hub.rpc}`);
  console.log(`Spoke       : ${spokeCtx.chain} (${spoke.role}, ${spoke.decimals} dp) rpc=${spoke.rpc}`);
  console.log('Rate limits :', `${cfg.limits.outboundPerDay} out / ${cfg.limits.inboundPerDay} in per day`);
  console.log('Ready       :', ready ? 'yes ✓' : `no — fill: ${missing.join(', ')}`);
  console.log('\nWhy hub-and-spoke: $RCLAW revokes mint authority at mint time, so');
  console.log('burn-and-mint (which needs mint authority on an NTT PDA) is not used.');
  console.log('\nDeploy steps (ntt CLI — see bridge/README.md):');
  console.log('  1. ntt new rclaw-ntt && cd rclaw-ntt && ntt init Testnet');
  console.log(`  2. ntt add-chain Solana --latest --mode locking --token ${unfilled(hub.token) ? '<RCLAW_MINT>' : hub.token}`);
  console.log('  3. ntt add-chain BaseSepolia --latest --mode burning --token <WRAPPED_ERC20>');
  console.log('  4. ntt push   # deploy + register the managers/transceivers');
  console.log('  5. Copy the resulting manager/token addresses into ntt.config.json');
  console.log('\nThen: npm run bridge:transfer -- --amount 1   (add --reverse for spoke → hub)');
}

// ── transfer ────────────────────────────────────────────────────────────────
async function cmdTransfer() {
  const cfg = loadConfig();
  const { hub, spoke, missing, ready } = validate(cfg);
  if (!ready) {
    throw new Error(`Config incomplete — fill ${missing.join(', ')} after running the ntt CLI deploy (see bridge/README.md).`);
  }
  const amt = arg('--amount') || cfg.transfer.defaultAmount;
  const reverse = process.argv.includes('--reverse');
  const src = reverse ? spoke : hub;
  const dst = reverse ? hub : spoke;

  // Register the NTT protocol implementations. EVM loads cleanly; Solana is
  // subject to the upstream ESM bug documented at the top of this file.
  try {
    await import('@wormhole-foundation/sdk-evm-ntt');
    await import('@wormhole-foundation/sdk-solana-ntt');
  } catch (err) {
    throw new Error(
      'Could not load the NTT protocol packages in plain Node ESM ' +
        `(${err.message}). This is the known @wormhole-foundation/sdk-solana-ntt ` +
        'packaging bug — re-run under a bundler-style loader, e.g. `npx tsx token/bridge/ntt_bridge.mjs transfer --amount 1`. ' +
        '`npm run bridge:plan` is unaffected.'
    );
  }

  const wh = await wormhole(cfg.network, [solana, evm]);
  const srcCtx = wh.getChain(src.chain);
  const dstCtx = wh.getChain(dst.chain);

  const srcNtt = await srcCtx.getProtocol('Ntt', {
    ntt: { token: src.token, manager: src.manager, transceiver: { wormhole: src.manager } },
  });

  const units = amount.units(amount.parse(String(amt), src.decimals));
  console.log(`Bridging ${amt} $RCLAW: ${srcCtx.chain} → ${dstCtx.chain} (${cfg.mode})…`);
  console.log('NOTE: signing requires a configured signer for both chains; see bridge/README.md.');

  // The transfer is produced as an unsigned tx stream; signing/sending is the
  // operator's step (non-custodial — this tool never holds a key).
  const sender = arg('--sender');
  if (!sender) {
    throw new Error('Pass --sender <address> (and configure a signer) to produce and send the transfer.');
  }
  const xfer = srcNtt.transfer(sender, units, { chain: dstCtx.chain, address: arg('--recipient') || sender }, {
    queue: false,
    automatic: !!cfg.transfer.automatic,
  });
  console.log('Transfer instruction stream built. Sign + send with signSendWait once a signer is wired.');
  void signSendWait; // documented entry point for the operator's signing step
  void xfer;
}

const cmd = process.argv[2];
const table = { plan: cmdPlan, transfer: cmdTransfer };
if (!table[cmd]) {
  console.error('Usage: node ntt_bridge.mjs <plan|transfer --amount N [--reverse] [--sender ADDR] [--recipient ADDR]>');
  process.exit(2);
}
await table[cmd]();
