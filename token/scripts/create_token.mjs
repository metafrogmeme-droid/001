// Create the $RCLAW SPL Token-2022 mint on DEVNET:
//   - metadata extension (name/symbol/uri) stored in the mint account
//   - freeze authority set to null at init (no wallet can ever be frozen)
//   - full fixed supply minted to the payer's ATA
//   - mint authority revoked afterwards (supply can never increase)
//
// DRAFT / DEVNET-ONLY. Mainnet is gated behind legal review + audit
// (docs/TOKEN_ROADMAP.md §10-11). getConnection() refuses mainnet URLs.
import {
  Keypair,
  SystemProgram,
  Transaction,
  sendAndConfirmTransaction,
} from '@solana/web3.js';
import {
  ExtensionType,
  TOKEN_2022_PROGRAM_ID,
  createInitializeMintInstruction,
  createInitializeMetadataPointerInstruction,
  getMintLen,
  getAssociatedTokenAddressSync,
  createAssociatedTokenAccountInstruction,
  createMintToInstruction,
  createSetAuthorityInstruction,
  AuthorityType,
  getMint,
  getTokenMetadata,
} from '@solana/spl-token';
import {
  createInitializeInstruction as createInitializeMetadataInstruction,
  pack,
} from '@solana/spl-token-metadata';
import { loadConfig, loadEnv, getConnection, loadKeypair, saveArtifact } from './lib.mjs';

const cfg = loadConfig();
const env = loadEnv();
const connection = getConnection(env);
const payer = loadKeypair(env);

console.log('=== $RCLAW token create (DEVNET / DRAFT) ===');
console.log('Payer:', payer.publicKey.toBase58());
console.log('RPC  :', env.RPC_URL || 'devnet default');

const decimals = cfg.decimals;
const supplyBase = BigInt(cfg.totalSupply) * 10n ** BigInt(decimals);

const mint = Keypair.generate();
console.log('Mint :', mint.publicKey.toBase58());

// Metadata that lives inside the mint account (Token-2022 metadata extension).
const metadata = {
  mint: mint.publicKey,
  name: cfg.name,
  symbol: cfg.symbol,
  uri: cfg.metadataUri,
  additionalMetadata: cfg.additionalMetadata || [],
};

// Size = mint with MetadataPointer extension + the packed metadata (TLV) it points to.
const mintLen = getMintLen([ExtensionType.MetadataPointer]);
const metadataLen = pack(metadata).length + 4; // +4 TLV size prefix
const lamports = await connection.getMinimumBalanceForRentExemption(mintLen + metadataLen);

const freezeAuthority = cfg.authorities.setFreezeAuthorityToNull ? null : payer.publicKey;

const createIx = SystemProgram.createAccount({
  fromPubkey: payer.publicKey,
  newAccountPubkey: mint.publicKey,
  space: mintLen,
  lamports,
  programId: TOKEN_2022_PROGRAM_ID,
});

// Metadata pointer points the mint at itself (metadata stored in-mint).
const pointerIx = createInitializeMetadataPointerInstruction(
  mint.publicKey,
  payer.publicKey,
  mint.publicKey,
  TOKEN_2022_PROGRAM_ID
);

const initMintIx = createInitializeMintInstruction(
  mint.publicKey,
  decimals,
  payer.publicKey, // mint authority (revoked later)
  freezeAuthority,
  TOKEN_2022_PROGRAM_ID
);

const initMetaIx = createInitializeMetadataInstruction({
  programId: TOKEN_2022_PROGRAM_ID,
  metadata: mint.publicKey,
  updateAuthority: payer.publicKey,
  mint: mint.publicKey,
  mintAuthority: payer.publicKey,
  name: metadata.name,
  symbol: metadata.symbol,
  uri: metadata.uri,
});

console.log('\n[1/3] Creating mint + metadata…');
const tx1 = new Transaction().add(createIx, pointerIx, initMintIx, initMetaIx);
const sig1 = await sendAndConfirmTransaction(connection, tx1, [payer, mint]);
console.log('  tx:', sig1);

// Mint the full fixed supply to the payer's ATA.
const ata = getAssociatedTokenAddressSync(mint.publicKey, payer.publicKey, false, TOKEN_2022_PROGRAM_ID);
console.log('\n[2/3] Minting supply to ATA', ata.toBase58(), '…');
const tx2 = new Transaction().add(
  createAssociatedTokenAccountInstruction(payer.publicKey, ata, payer.publicKey, mint.publicKey, TOKEN_2022_PROGRAM_ID),
  createMintToInstruction(mint.publicKey, ata, payer.publicKey, supplyBase, [], TOKEN_2022_PROGRAM_ID)
);
const sig2 = await sendAndConfirmTransaction(connection, tx2, [payer]);
console.log('  tx:', sig2);
console.log('  minted', cfg.totalSupply, cfg.symbol, `(${supplyBase} base units)`);

// Revoke mint authority so supply is permanently fixed.
if (cfg.authorities.revokeMintAuthorityAfterMint) {
  console.log('\n[3/3] Revoking mint authority (supply becomes fixed)…');
  const tx3 = new Transaction().add(
    createSetAuthorityInstruction(mint.publicKey, payer.publicKey, AuthorityType.MintTokens, null, [], TOKEN_2022_PROGRAM_ID)
  );
  const sig3 = await sendAndConfirmTransaction(connection, tx3, [payer]);
  console.log('  tx:', sig3);
} else {
  console.log('\n[3/3] SKIPPED mint-authority revoke (config flag off).');
}

const onchain = await getMint(connection, mint.publicKey, 'confirmed', TOKEN_2022_PROGRAM_ID);
const meta = await getTokenMetadata(connection, mint.publicKey);

const artifact = {
  cluster: env.CLUSTER || 'devnet',
  mint: mint.publicKey.toBase58(),
  ata: ata.toBase58(),
  decimals: onchain.decimals,
  supply: onchain.supply.toString(),
  mintAuthority: onchain.mintAuthority ? onchain.mintAuthority.toBase58() : null,
  freezeAuthority: onchain.freezeAuthority ? onchain.freezeAuthority.toBase58() : null,
  metadata: meta ? { name: meta.name, symbol: meta.symbol, uri: meta.uri } : null,
  txs: { create: sig1, mint: sig2 },
  note: 'DRAFT / DEVNET artifact — see docs/TOKEN_ROADMAP.md',
};
const out = saveArtifact('token.devnet.json', artifact);

console.log('\n=== DONE ===');
console.log('Mint authority  :', artifact.mintAuthority, artifact.mintAuthority ? '(NOT revoked!)' : '(revoked ✓)');
console.log('Freeze authority:', artifact.freezeAuthority, artifact.freezeAuthority ? '(present!)' : '(null ✓)');
console.log('Artifact written:', out);
console.log('Verify with: npm run verify');
