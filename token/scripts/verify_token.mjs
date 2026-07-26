// Verify the $RCLAW devnet mint matches the intended invariants:
//   supply == config, decimals == config, mint authority == null, freeze authority == null.
// Reads the mint from .artifacts/token.devnet.json (or MINT env override).
// DRAFT / DEVNET-ONLY.
import { PublicKey } from '@solana/web3.js';
import {
  getMint,
  getMetadataPointerState,
  getTokenMetadata,
  TOKEN_2022_PROGRAM_ID,
} from '@solana/spl-token';
import { loadConfig, loadEnv, getConnection, readArtifact } from './lib.mjs';

// A renounced Solana authority is the all-zero pubkey, not a missing field.
const SYSTEM_PROGRAM = PublicKey.default.toBase58();

const cfg = loadConfig();
const env = loadEnv();

// The config must never be able to waive the property it configures. If someone
// flips these off, the mint is unsafe AND the check that would have caught it is
// disabled in the same edit — so refuse to verify at all rather than print a
// green tick over an unsafe token.
if (!cfg.authorities.revokeMintAuthorityAfterMint || !cfg.authorities.setFreezeAuthorityToNull) {
  console.error(
    'UNSAFE CONFIG: authorities.revokeMintAuthorityAfterMint and ' +
    'authorities.setFreezeAuthorityToNull must both be true. Refusing to verify.'
  );
  process.exit(1);
}

const connection = await getConnection(env, { expectCluster: cfg.cluster || 'devnet' });

const artifact = readArtifact('token.devnet.json');
const mintStr = env.MINT || (artifact && artifact.mint);
if (!mintStr) {
  console.error('No mint to verify. Run `npm run create` first, or set MINT=<address>.');
  process.exit(1);
}
const mint = new PublicKey(mintStr);
console.log('Verifying mint:', mint.toBase58());

const onchain = await getMint(connection, mint, 'confirmed', TOKEN_2022_PROGRAM_ID);
const meta = await getTokenMetadata(connection, mint);
const pointer = getMetadataPointerState(onchain);

const expectedSupply = BigInt(cfg.totalSupply) * 10n ** BigInt(cfg.decimals);
const checks = [
  ['decimals', onchain.decimals === cfg.decimals, `${onchain.decimals} (want ${cfg.decimals})`],
  ['supply', onchain.supply === expectedSupply, `${onchain.supply} (want ${expectedSupply})`],
  // Absolute invariants: a live mint or freeze authority is a FAIL regardless of
  // config. These were previously `X === null || !cfg.authorities.Y`, which is a
  // tautology over the same file that drove creation — it could never fail.
  [
    'mint authority revoked',
    onchain.mintAuthority === null,
    onchain.mintAuthority ? onchain.mintAuthority.toBase58() : 'null',
  ],
  [
    'freeze authority null',
    onchain.freezeAuthority === null,
    onchain.freezeAuthority ? onchain.freezeAuthority.toBase58() : 'null',
  ],
  ['metadata name', meta ? meta.name === cfg.name : false, meta ? meta.name : '(none)'],
  ['metadata symbol', meta ? meta.symbol === cfg.symbol : false, meta ? meta.symbol : '(none)'],
  ['metadata uri', meta ? meta.uri === cfg.metadataUri : false, meta ? meta.uri : '(none)'],
  // Rent was paid for these at creation; assert they were actually written.
  [
    'additionalMetadata fields',
    (() => {
      const want = cfg.additionalMetadata || [];
      if (!want.length) return true;
      const got = new Map((meta && meta.additionalMetadata) || []);
      return want.every(([k, v]) => got.get(k) === v);
    })(),
    `${((meta && meta.additionalMetadata) || []).length}/${(cfg.additionalMetadata || []).length} present`,
  ],
  // The two authorities create_token.mjs leaves live unless told otherwise.
  // Whoever holds these can rewrite the token's displayed identity or redirect
  // where wallets look for it, so they belong in the verified set.
  [
    'metadata update authority renounced',
    !meta || !meta.updateAuthority || meta.updateAuthority.toBase58() === SYSTEM_PROGRAM,
    meta && meta.updateAuthority ? meta.updateAuthority.toBase58() : 'null',
  ],
  [
    'metadata pointer authority renounced',
    !pointer || !pointer.authority || pointer.authority.toBase58() === SYSTEM_PROGRAM,
    pointer && pointer.authority ? pointer.authority.toBase58() : 'null',
  ],
];

let ok = true;
for (const [label, pass, detail] of checks) {
  console.log(`  ${pass ? '✓' : '✗'} ${label}: ${detail}`);
  if (!pass) ok = false;
}

console.log(ok ? '\nALL CHECKS PASSED ✓' : '\nVERIFICATION FAILED ✗');
process.exit(ok ? 0 : 1);
