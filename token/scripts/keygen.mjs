// Generate a throwaway DEVNET payer keypair and (optionally) request an airdrop.
// DRAFT / DEVNET-ONLY. Never reuse this key on mainnet.
import fs from 'node:fs';
import path from 'node:path';
import { Keypair, LAMPORTS_PER_SOL } from '@solana/web3.js';
import { ROOT, loadEnv, getConnection } from './lib.mjs';

const env = loadEnv();
const dir = path.join(ROOT, '.keys');
// mkdirSync's mode is masked by the process umask, so set it explicitly after.
fs.mkdirSync(dir, { recursive: true, mode: 0o700 });
if (process.platform !== 'win32') fs.chmodSync(dir, 0o700);
const outPath = path.join(dir, 'mint-payer.json');

if (fs.existsSync(outPath)) {
  // Repair rather than merely report: a key written by an earlier version of
  // this script is world-readable, and saying so without fixing it leaves the
  // exposure in place.
  if (process.platform !== 'win32') fs.chmodSync(outPath, 0o600);
  console.log(`Keypair already exists at ${outPath} — left in place, mode repaired to 0600.`);
} else {
  const kp = Keypair.generate();
  fs.writeFileSync(outPath, JSON.stringify(Array.from(kp.secretKey)), { mode: 0o600 });
  if (process.platform !== 'win32') fs.chmodSync(outPath, 0o600);
  console.log(`Generated devnet payer: ${kp.publicKey.toBase58()}`);
  console.log(`Saved secret key to ${outPath} (gitignored, mode 0600).`);
  console.log(
    'NOTE: this single key holds mint, metadata, presale and LP authority plus the\n' +
    '      entire supply. Move it to a Squads multisig before any value-bearing run\n' +
    '      (docs/TOKEN_ROADMAP.md §11).'
  );
}

// Best-effort devnet airdrop so the create script has SOL for fees/rent.
try {
  const { Keypair: KP } = await import('@solana/web3.js');
  const secret = Uint8Array.from(JSON.parse(fs.readFileSync(outPath, 'utf8')));
  const kp = KP.fromSecretKey(secret);
  const conn = await getConnection(env);
  const bal = await conn.getBalance(kp.publicKey);
  if (bal < LAMPORTS_PER_SOL) {
    console.log('Requesting 1 SOL devnet airdrop…');
    const sig = await conn.requestAirdrop(kp.publicKey, LAMPORTS_PER_SOL);
    await conn.confirmTransaction(sig, 'confirmed');
    console.log('Airdrop confirmed.');
  } else {
    console.log(`Balance already ${(bal / LAMPORTS_PER_SOL).toFixed(3)} SOL.`);
  }
} catch (e) {
  console.warn('Airdrop skipped (devnet faucet may be rate-limited):', e.message);
  console.warn('Fund the printed address manually via https://faucet.solana.com if needed.');
}
