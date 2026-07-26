/**
 * $RCLAW staking — Anchor tests (DRAFT / DEVNET or localnet).
 *
 * Spec for the stake/unstake lifecycle and the StakeAccount layout the Python
 * tier gate depends on. Run with `anchor test` (needs the Anchor + Solana
 * toolchain). This file is the executable spec; it is not run in CI (no
 * validator/toolchain there).
 */
import * as anchor from "@coral-xyz/anchor";
// NOTE: the generated IDL type lives at ../../../target/types/rclaw_staking and
// only exists AFTER `anchor build`. It is intentionally not imported here so the
// spec typechecks before a build has run; once you have built, you may swap
// `Program<any>` below for the generated `Program<RclawStaking>` for full typing.
import {
  createMint,
  getAccount,
  getAssociatedTokenAddressSync,
  getOrCreateAssociatedTokenAccount,
  mintTo,
} from "@solana/spl-token";
import { assert } from "chai";

describe("rclaw_staking", () => {
  const provider = anchor.AnchorProvider.env();
  anchor.setProvider(provider);
  // Untyped until `anchor build` generates the IDL types (see note above).
  const program: any = anchor.workspace.RclawStaking;
  const owner = provider.wallet as anchor.Wallet;

  let mint: anchor.web3.PublicKey;
  let userAta: anchor.web3.PublicKey;
  // Both PDAs are mint-scoped — that IS the vault-drain fix. Deriving them
  // without the mint (as this spec previously did) reproduces the pre-fix
  // addresses, so every account would mismatch and nothing could pass. They
  // cannot stay at module scope because they now depend on `mint`, which only
  // exists after before().
  let stakePda: anchor.web3.PublicKey;
  let vaultAuthority: anchor.web3.PublicKey;
  let vault: anchor.web3.PublicKey;

  before(async () => {
    mint = await createMint(
      provider.connection,
      owner.payer,
      owner.publicKey,
      null,
      9
    );
    [stakePda] = anchor.web3.PublicKey.findProgramAddressSync(
      [Buffer.from("stake"), owner.publicKey.toBuffer(), mint.toBuffer()],
      program.programId
    );
    [vaultAuthority] = anchor.web3.PublicKey.findProgramAddressSync(
      [Buffer.from("vault"), mint.toBuffer()],
      program.programId
    );
    vault = getAssociatedTokenAddressSync(mint, vaultAuthority, true);

    const ata = await getOrCreateAssociatedTokenAccount(
      provider.connection,
      owner.payer,
      mint,
      owner.publicKey
    );
    userAta = ata.address;
    await mintTo(
      provider.connection,
      owner.payer,
      mint,
      userAta,
      owner.publicKey,
      1_000_000_000_000n // 1000 tokens @ 9dp
    );
  });

  it("stakes tokens and records the amount", async () => {
    const amount = new anchor.BN(50_000_000_000); // 50 tokens
    const vault = anchor.utils.token.associatedAddress({
      mint,
      owner: vaultAuthority,
    });
    await program.methods
      .stake(amount)
      .accounts({ owner: owner.publicKey, mint, userTokenAccount: userAta })
      .rpc();

    const sa = await program.account.stakeAccount.fetch(stakePda);
    assert.ok(sa.owner.equals(owner.publicKey));
    assert.equal(sa.amount.toString(), amount.toString());
    assert.isAbove(sa.stakedAt.toNumber(), 0);
    // The binding that closes the drain: the record names its mint.
    assert.ok(sa.mint.equals(mint), "stake record must be bound to the mint");
    // And the tokens are really escrowed in THIS mint's vault, not merely
    // recorded as if they were.
    const vaultAcct = await getAccount(provider.connection, vault);
    assert.equal(vaultAcct.amount.toString(), amount.toString());
    assert.ok(vaultAcct.owner.equals(vaultAuthority));
  });

  it("refuses to unstake inside the lock-up window", async () => {
    // LOCKUP_SECONDS is 7 days and a validator clock cannot be warped from here,
    // so this asserts the lock holds. The post-expiry path is covered in-process
    // by tests/attack.rs, which can move the sysvar clock.
    try {
      await program.methods
        .unstake(new anchor.BN(20_000_000_000))
        .accounts({ owner: owner.publicKey, mint, userTokenAccount: userAta })
        .rpc();
      assert.fail("unstaking during the lock-up must fail");
    } catch (e) {
      assert.include(String(e), "StillLocked");
    }
    const sa = await program.account.stakeAccount.fetch(stakePda);
    assert.equal(sa.amount.toString(), "50000000000", "balance must be untouched");
  });

  it("rejects unstaking more than staked", async () => {
    try {
      await program.methods
        .unstake(new anchor.BN(999_000_000_000))
        .accounts({ owner: owner.publicKey, mint, userTokenAccount: userAta })
        .rpc();
      assert.fail("should have thrown");
    } catch (e) {
      // The lock is checked before the balance, so either guard refusing is a
      // pass — what must never happen is the withdrawal succeeding.
      const msg = String(e);
      assert.ok(
        msg.includes("InsufficientStake") || msg.includes("StillLocked"),
        `expected InsufficientStake or StillLocked, got: ${msg}`
      );
    }
  });
});
