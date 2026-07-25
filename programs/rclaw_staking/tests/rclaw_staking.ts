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

  const [stakePda] = anchor.web3.PublicKey.findProgramAddressSync(
    [Buffer.from("stake"), owner.publicKey.toBuffer()],
    program.programId
  );
  const [vaultAuthority] = anchor.web3.PublicKey.findProgramAddressSync(
    [Buffer.from("vault")],
    program.programId
  );

  before(async () => {
    mint = await createMint(
      provider.connection,
      owner.payer,
      owner.publicKey,
      null,
      9
    );
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
  });

  it("unstakes part of the balance", async () => {
    const amount = new anchor.BN(20_000_000_000); // 20 tokens
    await program.methods
      .unstake(amount)
      .accounts({ owner: owner.publicKey, mint, userTokenAccount: userAta })
      .rpc();
    const sa = await program.account.stakeAccount.fetch(stakePda);
    assert.equal(sa.amount.toString(), "30000000000"); // 50 - 20
  });

  it("rejects unstaking more than staked", async () => {
    try {
      await program.methods
        .unstake(new anchor.BN(999_000_000_000))
        .accounts({ owner: owner.publicKey, mint, userTokenAccount: userAta })
        .rpc();
      assert.fail("should have thrown");
    } catch (e) {
      assert.include(String(e), "InsufficientStake");
    }
  });
});
