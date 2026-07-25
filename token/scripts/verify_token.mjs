// Verify the $RCLAW devnet mint matches the intended invariants:
//   supply == config, decimals == config, mint authority == null, freeze authority == null.
// Reads the mint from .artifacts/token.devnet.json (or MINT env override).
// DRAFT / DEVNET-ONLY.
import { PublicKey } from '@solana/web3.js';
import { getMint, getTokenMetadata, TOKEN_2022_PROGRAM_ID } from '@solana/spl-token';
import { loadConfig, loadEnv, getConnection, readArtifact } from './lib.mjs';

const cfg = loadConfig();
const env = loadEnv();
const connection = getConnection(env);

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

const expectedSupply = BigInt(cfg.totalSupply) * 10n ** BigInt(cfg.decimals);
const checks = [
  ['decimals', onchain.decimals === cfg.decimals, `${onchain.decimals} (want ${cfg.decimals})`],
  ['supply', onchain.supply === expectedSupply, `${onchain.supply} (want ${expectedSupply})`],
  [
    'mint authority revoked',
    onchain.mintAuthority === null || !cfg.authorities.revokeMintAuthorityAfterMint,
    onchain.mintAuthority ? onchain.mintAuthority.toBase58() : 'null',
  ],
  [
    'freeze authority null',
    onchain.freezeAuthority === null || !cfg.authorities.setFreezeAuthorityToNull,
    onchain.freezeAuthority ? onchain.freezeAuthority.toBase58() : 'null',
  ],
  ['metadata name', meta ? meta.name === cfg.name : false, meta ? meta.name : '(none)'],
  ['metadata symbol', meta ? meta.symbol === cfg.symbol : false, meta ? meta.symbol : '(none)'],
];

let ok = true;
for (const [label, pass, detail] of checks) {
  console.log(`  ${pass ? '✓' : '✗'} ${label}: ${detail}`);
  if (!pass) ok = false;
}

console.log(ok ? '\nALL CHECKS PASSED ✓' : '\nVERIFICATION FAILED ✗');
process.exit(ok ? 0 : 1);
