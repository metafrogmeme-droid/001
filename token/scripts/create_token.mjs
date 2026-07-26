// Create the $RCLAW SPL Token-2022 mint on DEVNET:
//   - metadata extension (name/symbol/uri) stored in the mint account
//   - freeze authority set to null at init (no wallet can ever be frozen)
//   - full fixed supply minted to the payer's ATA
//   - mint authority revoked afterwards (supply can never increase)
//   - metadata update + pointer authorities renounced (identity becomes immutable)
//
// DRAFT / DEVNET-ONLY. Mainnet is gated behind legal review + audit
// (docs/TOKEN_ROADMAP.md §10-11). getConnection() proves the cluster by genesis
// hash and refuses anything that is not the cluster named in token.config.json.
import {
  Keypair,
  PublicKey,
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
  getMetadataPointerState,
  getTokenMetadata,
} from '@solana/spl-token';
import {
  createInitializeInstruction as createInitializeMetadataInstruction,
  createUpdateAuthorityInstruction,
  pack,
} from '@solana/spl-token-metadata';
import { loadConfig, loadEnv, getConnection, loadKeypair, saveArtifact } from './lib.mjs';

const cfg = loadConfig();
const env = loadEnv();
const connection = await getConnection(env, { expectCluster: cfg.cluster || 'devnet' });
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

console.log('\n[1/4] Creating mint + metadata…');
const tx1 = new Transaction().add(createIx, pointerIx, initMintIx, initMetaIx);
const sig1 = await sendAndConfirmTransaction(connection, tx1, [payer, mint]);
console.log('  tx:', sig1);

// Mint the full fixed supply to the payer's ATA.
const ata = getAssociatedTokenAddressSync(mint.publicKey, payer.publicKey, false, TOKEN_2022_PROGRAM_ID);
console.log('\n[2/4] Minting supply to ATA', ata.toBase58(), '…');
const tx2 = new Transaction().add(
  createAssociatedTokenAccountInstruction(payer.publicKey, ata, payer.publicKey, mint.publicKey, TOKEN_2022_PROGRAM_ID),
  createMintToInstruction(mint.publicKey, ata, payer.publicKey, supplyBase, [], TOKEN_2022_PROGRAM_ID)
);
const sig2 = await sendAndConfirmTransaction(connection, tx2, [payer]);
console.log('  tx:', sig2);
console.log('  minted', cfg.totalSupply, cfg.symbol, `(${supplyBase} base units)`);

// Revoke mint authority so supply is permanently fixed.
if (cfg.authorities.revokeMintAuthorityAfterMint) {
  console.log('\n[3/4] Revoking mint authority (supply becomes fixed)…');
  const tx3 = new Transaction().add(
    createSetAuthorityInstruction(mint.publicKey, payer.publicKey, AuthorityType.MintTokens, null, [], TOKEN_2022_PROGRAM_ID)
  );
  const sig3 = await sendAndConfirmTransaction(connection, tx3, [payer]);
  console.log('  tx:', sig3);
} else {
  console.log('\n[3/4] SKIPPED mint-authority revoke (config flag off).');
}

// Renounce the two authorities that otherwise survive creation.
//
// Revoking MintTokens alone fixes the SUPPLY but not the IDENTITY: whoever holds
// the metadata update authority can rewrite name/symbol/uri on the existing
// mint, and whoever holds the MetadataPointer authority can redirect where
// wallets look for metadata entirely. Both must happen AFTER the final metadata
// write, and renouncing the pointer last means the pointer still resolves while
// the update authority is being dropped.
const metaRevokes = [];
if (cfg.authorities.revokeMetadataUpdateAuthority) {
  metaRevokes.push(
    createUpdateAuthorityInstruction({
      programId: TOKEN_2022_PROGRAM_ID,
      metadata: mint.publicKey,
      oldAuthority: payer.publicKey,
      newAuthority: null, // encoded as the all-zero pubkey == renounced
    })
  );
}
if (cfg.authorities.revokeMetadataPointerAuthority) {
  metaRevokes.push(
    createSetAuthorityInstruction(
      mint.publicKey,
      payer.publicKey,
      AuthorityType.MetadataPointer,
      null,
      [],
      TOKEN_2022_PROGRAM_ID
    )
  );
}
let sig4 = null;
if (metaRevokes.length) {
  console.log('\n[4/4] Renouncing metadata authorities (token identity becomes immutable)…');
  sig4 = await sendAndConfirmTransaction(connection, new Transaction().add(...metaRevokes), [payer]);
  console.log('  tx:', sig4);
  console.log(
    '  NOTE: name/symbol/uri can never be changed again. The uri must already point\n' +
    '        at immutable, content-addressed storage — a mutable URL is now frozen\n' +
    '        in place pointing at content someone else can still edit.'
  );
} else {
  console.log('\n[4/4] SKIPPED metadata-authority renounce (config flags off).');
}

const onchain = await getMint(connection, mint.publicKey, 'confirmed', TOKEN_2022_PROGRAM_ID);
const meta = await getTokenMetadata(connection, mint.publicKey);
const pointer = getMetadataPointerState(onchain);
const SYSTEM_PROGRAM = PublicKey.default.toBase58();
const asAuthority = (v) => {
  const s = v ? v.toBase58() : null;
  return s === SYSTEM_PROGRAM ? null : s;
};

const artifact = {
  cluster: cfg.cluster || 'devnet',
  mint: mint.publicKey.toBase58(),
  ata: ata.toBase58(),
  decimals: onchain.decimals,
  supply: onchain.supply.toString(),
  mintAuthority: asAuthority(onchain.mintAuthority),
  freezeAuthority: asAuthority(onchain.freezeAuthority),
  metadataUpdateAuthority: asAuthority(meta && meta.updateAuthority),
  metadataPointerAuthority: asAuthority(pointer && pointer.authority),
  metadata: meta ? { name: meta.name, symbol: meta.symbol, uri: meta.uri } : null,
  txs: { create: sig1, mint: sig2, ...(sig4 ? { renounceMetadata: sig4 } : {}) },
  note: 'DRAFT / DEVNET artifact — see docs/TOKEN_ROADMAP.md',
};
const out = saveArtifact('token.devnet.json', artifact);

const mark = (v) => (v ? `${v} (NOT revoked!)` : 'null (revoked ✓)');
console.log('\n=== DONE ===');
console.log('Mint authority             :', mark(artifact.mintAuthority));
console.log('Freeze authority           :', mark(artifact.freezeAuthority));
console.log('Metadata update authority  :', mark(artifact.metadataUpdateAuthority));
console.log('Metadata pointer authority :', mark(artifact.metadataPointerAuthority));
console.log('Artifact written:', out);
console.log('Verify with: npm run verify');
