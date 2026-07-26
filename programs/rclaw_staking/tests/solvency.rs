//! Mechanically-checked vault invariants under randomised multi-actor traffic.
//!
//! The audit's Coverage & Limitations section said plainly: "No property-based
//! or differential testing was run against either handler; no invariant
//! (including vault solvency) was mechanically proven." `attack.rs` proves that
//! specific *named* attacks fail. Nothing proved that the accounting holds under
//! traffic nobody thought to write a test for — interleaved partial withdrawals,
//! top-ups against an extended lock, closes racing re-stakes, two mints in
//! flight at once.
//!
//! This drives the real program through a deterministic pseudo-random operation
//! sequence and re-checks, after EVERY operation whether it succeeded or failed,
//! the properties that must never be false:
//!
//!   I1 SOLVENCY     — for each mint, Σ stake_account.amount over all users
//!                     equals that mint's vault balance exactly. If this is ever
//!                     false the program has promised out more than it holds and
//!                     some depositor cannot withdraw.
//!   I2 CONSERVATION — for each mint, Σ user balances + vault balance equals the
//!                     total ever minted. Catches tokens created or destroyed.
//!   I3 LOCK-UP      — no unstake succeeds while the record's `unlock_at` is in
//!                     the future. Checked against the state observed *before*
//!                     the operation, so it cannot be satisfied retroactively.
//!   I4 MONOTONIC    — a record's `unlock_at` never moves backwards while it
//!                     holds a balance.
//!
//! It is deterministic by construction: a fixed seed, an inline xorshift, and no
//! wall-clock or thread scheduling in the operation choice. A failure reproduces
//! exactly, and the panic message prints the operation index and the seed so the
//! offending sequence can be replayed.
//!
//! This is not a substitute for a fuzzer or a formal proof — the state space is
//! sampled, not exhausted. It is the difference between "these four attacks are
//! blocked" and "the accounting identity held across N randomised operations".
use anchor_lang::{InstructionData, ToAccountMetas};
use solana_program_test::{processor, ProgramTest, ProgramTestContext};
use solana_sdk::{
    account::Account,
    clock::Clock,
    instruction::Instruction,
    program_option::COption,
    program_pack::Pack,
    pubkey::Pubkey,
    signature::{Keypair, Signer},
    system_instruction,
    transaction::Transaction,
};
use std::str::FromStr;

const DECIMALS: u8 = 9;
/// Base units minted to each user, per mint, at setup.
const INITIAL_PER_USER: u64 = 1_000_000;
const USERS: usize = 3;
/// Operations to run. Each is one transaction against the real program, plus
/// four invariant re-reads, so this is the cost knob.
const OPS: usize = 72;
/// Changing this explores a different sequence. Kept fixed so CI is reproducible.
const SEED: u64 = 0x5241_434C_4157_0001;

// ── Deterministic RNG ───────────────────────────────────────────────────────
//
// Inline rather than a `rand` dev-dependency: this needs to be reproducible
// across toolchains forever, and an external generator can change its stream on
// a version bump, silently changing what the test covers.
struct Rng(u64);

impl Rng {
    fn next_u64(&mut self) -> u64 {
        // xorshift64*
        let mut x = self.0;
        x ^= x >> 12;
        x ^= x << 25;
        x ^= x >> 27;
        self.0 = x;
        x.wrapping_mul(0x2545_F491_4F6C_DD1D)
    }
    fn below(&mut self, n: u64) -> u64 {
        if n == 0 {
            0
        } else {
            self.next_u64() % n
        }
    }
}

// ── Program plumbing (mirrors attack.rs; test binaries do not share code) ───

fn program_id() -> Pubkey {
    rclaw_staking::ID
}

fn pinned_mint() -> Option<Pubkey> {
    rclaw_staking::PINNED_MINT
        .map(str::trim)
        .filter(|s| !s.is_empty())
        .and_then(|s| Pubkey::from_str(s).ok())
}

fn entry_shim(
    program_id: &Pubkey,
    accounts: &[anchor_lang::solana_program::account_info::AccountInfo],
    data: &[u8],
) -> anchor_lang::solana_program::entrypoint::ProgramResult {
    // SAFETY: re-binds only the lifetime, not the data — program-test owns the
    // AccountInfos for the whole call. Same shim as tests/attack.rs.
    let accounts: &[anchor_lang::solana_program::account_info::AccountInfo] =
        unsafe { core::mem::transmute(accounts) };
    rclaw_staking::entry(program_id, accounts, data)
}

fn stake_pda(owner: &Pubkey, mint: &Pubkey) -> Pubkey {
    Pubkey::find_program_address(&[b"stake", owner.as_ref(), mint.as_ref()], &program_id()).0
}

fn vault_authority(mint: &Pubkey) -> Pubkey {
    Pubkey::find_program_address(&[b"vault", mint.as_ref()], &program_id()).0
}

fn vault_ata(mint: &Pubkey) -> Pubkey {
    spl_associated_token_account::get_associated_token_address(&vault_authority(mint), mint)
}

fn stake_ix(owner: &Pubkey, mint: &Pubkey, user_ata: &Pubkey, amount: u64) -> Instruction {
    let accounts = rclaw_staking::accounts::Stake {
        owner: *owner,
        mint: *mint,
        stake_account: stake_pda(owner, mint),
        vault_authority: vault_authority(mint),
        vault: vault_ata(mint),
        user_token_account: *user_ata,
        token_program: spl_token::id(),
        associated_token_program: spl_associated_token_account::id(),
        system_program: solana_sdk::system_program::id(),
    };
    Instruction {
        program_id: program_id(),
        accounts: accounts.to_account_metas(None),
        data: rclaw_staking::instruction::Stake { amount }.data(),
    }
}

fn unstake_ix(owner: &Pubkey, mint: &Pubkey, user_ata: &Pubkey, amount: u64) -> Instruction {
    let accounts = rclaw_staking::accounts::Unstake {
        owner: *owner,
        mint: *mint,
        stake_account: stake_pda(owner, mint),
        vault_authority: vault_authority(mint),
        vault: vault_ata(mint),
        user_token_account: *user_ata,
        token_program: spl_token::id(),
    };
    Instruction {
        program_id: program_id(),
        accounts: accounts.to_account_metas(None),
        data: rclaw_staking::instruction::Unstake { amount }.data(),
    }
}

fn close_ix(owner: &Pubkey, mint: &Pubkey) -> Instruction {
    let accounts = rclaw_staking::accounts::CloseStake {
        owner: *owner,
        mint: *mint,
        stake_account: stake_pda(owner, mint),
    };
    Instruction {
        program_id: program_id(),
        accounts: accounts.to_account_metas(None),
        data: rclaw_staking::instruction::CloseStakeAccount {}.data(),
    }
}

// ── Setup helpers ───────────────────────────────────────────────────────────

async fn create_mint(ctx: &mut ProgramTestContext, authority: &Pubkey) -> Pubkey {
    let mint = Keypair::new();
    let rent = ctx.banks_client.get_rent().await.unwrap();
    let ix = vec![
        system_instruction::create_account(
            &ctx.payer.pubkey(),
            &mint.pubkey(),
            rent.minimum_balance(spl_token::state::Mint::LEN),
            spl_token::state::Mint::LEN as u64,
            &spl_token::id(),
        ),
        spl_token::instruction::initialize_mint(
            &spl_token::id(),
            &mint.pubkey(),
            authority,
            None,
            DECIMALS,
        )
        .unwrap(),
    ];
    let bh = ctx.banks_client.get_latest_blockhash().await.unwrap();
    let tx = Transaction::new_signed_with_payer(
        &ix,
        Some(&ctx.payer.pubkey()),
        &[&ctx.payer, &mint],
        bh,
    );
    ctx.banks_client.process_transaction(tx).await.unwrap();
    mint.pubkey()
}

/// The mint the program accepts. Under a pin, materialise an SPL mint directly
/// AT the pinned address so this suite exercises the vault in the only
/// configuration intended to hold value (same technique as attack.rs).
async fn canonical_mint(ctx: &mut ProgramTestContext, authority: &Pubkey) -> Pubkey {
    let Some(pinned) = pinned_mint() else {
        return create_mint(ctx, authority).await;
    };
    let rent = ctx.banks_client.get_rent().await.unwrap();
    let mut data = vec![0u8; spl_token::state::Mint::LEN];
    spl_token::state::Mint {
        mint_authority: COption::Some(*authority),
        supply: 0,
        decimals: DECIMALS,
        is_initialized: true,
        freeze_authority: COption::None,
    }
    .pack_into_slice(&mut data);
    ctx.set_account(
        &pinned,
        &Account {
            lamports: rent.minimum_balance(spl_token::state::Mint::LEN),
            data,
            owner: spl_token::id(),
            executable: false,
            rent_epoch: 0,
        }
        .into(),
    );
    pinned
}

async fn create_ata_and_mint(
    ctx: &mut ProgramTestContext,
    mint: &Pubkey,
    owner: &Pubkey,
    amount: u64,
) -> Pubkey {
    let ata = spl_associated_token_account::get_associated_token_address(owner, mint);
    let mut ix = vec![
        spl_associated_token_account::instruction::create_associated_token_account(
            &ctx.payer.pubkey(),
            owner,
            mint,
            &spl_token::id(),
        ),
    ];
    if amount > 0 {
        ix.push(
            spl_token::instruction::mint_to(
                &spl_token::id(),
                mint,
                &ata,
                &ctx.payer.pubkey(),
                &[],
                amount,
            )
            .unwrap(),
        );
    }
    let bh = ctx.banks_client.get_latest_blockhash().await.unwrap();
    let tx =
        Transaction::new_signed_with_payer(&ix, Some(&ctx.payer.pubkey()), &[&ctx.payer], bh);
    ctx.banks_client.process_transaction(tx).await.unwrap();
    ata
}

/// Token balance, or 0 when the account does not exist yet (the vault ATA is
/// created lazily by the first successful stake).
async fn token_balance(ctx: &mut ProgramTestContext, account: &Pubkey) -> u64 {
    match ctx.banks_client.get_account(*account).await.unwrap() {
        Some(acc) if acc.data.len() == spl_token::state::Account::LEN => {
            spl_token::state::Account::unpack(&acc.data).map(|a| a.amount).unwrap_or(0)
        }
        _ => 0,
    }
}

/// `(amount, unlock_at)` from a stake record, or `(0, 0)` when it is absent.
///
/// Read straight off the wire at the offsets `bot/token/tier_gate.py` uses, so
/// a layout change breaks this too rather than silently reading garbage.
async fn stake_state(ctx: &mut ProgramTestContext, owner: &Pubkey, mint: &Pubkey) -> (u64, i64) {
    use rclaw_staking::layout::{AMOUNT_OFFSET, UNLOCK_AT_OFFSET};
    match ctx.banks_client.get_account(stake_pda(owner, mint)).await.unwrap() {
        Some(acc) if acc.data.len() >= UNLOCK_AT_OFFSET + 8 => (
            u64::from_le_bytes(acc.data[AMOUNT_OFFSET..AMOUNT_OFFSET + 8].try_into().unwrap()),
            i64::from_le_bytes(
                acc.data[UNLOCK_AT_OFFSET..UNLOCK_AT_OFFSET + 8].try_into().unwrap(),
            ),
        ),
        _ => (0, 0),
    }
}

async fn now(ctx: &mut ProgramTestContext) -> i64 {
    let clock: Clock = ctx.banks_client.get_sysvar().await.unwrap();
    clock.unix_timestamp
}

/// One user's whole position across both mints.
struct Actor {
    keypair: Keypair,
    /// ATA per mint, index-aligned with `mints`.
    atas: Vec<Pubkey>,
}

/// What the invariant checker measured for one mint at one point in time.
struct Snapshot {
    vault: u64,
    recorded: u64,
    user_held: u64,
}

async fn snapshot(ctx: &mut ProgramTestContext, actors: &[Actor], mint_ix: usize, mint: &Pubkey) -> Snapshot {
    let mut recorded = 0u64;
    let mut user_held = 0u64;
    for a in actors {
        let (amt, _) = stake_state(ctx, &a.keypair.pubkey(), mint).await;
        recorded += amt;
        user_held += token_balance(ctx, &a.atas[mint_ix]).await;
    }
    Snapshot {
        vault: token_balance(ctx, &vault_ata(mint)).await,
        recorded,
        user_held,
    }
}

/// I1 + I2, for every mint. Called after every operation, successful or not —
/// a rejected transaction that left state half-applied is exactly the bug this
/// is looking for, and only checking after successes would miss it.
async fn assert_invariants(
    ctx: &mut ProgramTestContext,
    actors: &[Actor],
    mints: &[Pubkey],
    total_minted: u64,
    op: usize,
    what: &str,
) {
    for (i, mint) in mints.iter().enumerate() {
        let s = snapshot(ctx, actors, i, mint).await;
        assert_eq!(
            s.recorded, s.vault,
            "I1 SOLVENCY VIOLATED after op {op} ({what}) on mint {i} ({mint}): \
             stake records total {} but the vault holds {}. The program has \
             promised out more than it can pay. seed={SEED:#x}",
            s.recorded, s.vault
        );
        assert_eq!(
            s.user_held + s.vault,
            total_minted,
            "I2 CONSERVATION VIOLATED after op {op} ({what}) on mint {i} ({mint}): \
             users hold {} + vault {} = {}, but {} was minted. Tokens were \
             created or destroyed. seed={SEED:#x}",
            s.user_held,
            s.vault,
            s.user_held + s.vault,
            total_minted
        );
    }
}

/// Fund a fresh actor with lamports directly. `stake` pays rent for two
/// `init_if_needed` accounts out of the owner's balance, so users need real
/// lamports, not just tokens.
fn fund(ctx: &mut ProgramTestContext, who: &Pubkey) {
    ctx.set_account(
        who,
        &Account {
            lamports: 100_000_000_000,
            data: vec![],
            owner: solana_sdk::system_program::id(),
            executable: false,
            rent_epoch: 0,
        }
        .into(),
    );
}

#[tokio::test]
async fn vault_invariants_hold_under_randomised_traffic() {
    let pt = ProgramTest::new("rclaw_staking", program_id(), processor!(entry_shim));
    let mut ctx = pt.start_with_context().await;
    let payer = ctx.payer.pubkey();

    // Two mints so cross-mint interference is in scope, not just assumed absent.
    // Under a pin the second mint is rejected by `stake` on every attempt; the
    // invariants must still hold for it (its vault stays absent, i.e. 0/0), and
    // that is itself worth asserting — a rejected mint must not create state.
    let mint_a = canonical_mint(&mut ctx, &payer).await;
    let mint_b = create_mint(&mut ctx, &payer).await;
    let mints = vec![mint_a, mint_b];

    let mut actors = Vec::new();
    for _ in 0..USERS {
        let kp = Keypair::new();
        fund(&mut ctx, &kp.pubkey());
        let mut atas = Vec::new();
        for m in &mints {
            atas.push(create_ata_and_mint(&mut ctx, m, &kp.pubkey(), INITIAL_PER_USER).await);
        }
        actors.push(Actor { keypair: kp, atas });
    }
    let total_minted = INITIAL_PER_USER * USERS as u64;

    assert_invariants(&mut ctx, &actors, &mints, total_minted, 0, "setup").await;

    let mut rng = Rng(SEED);
    // Guards against a sequence that happens to reject everything: a suite where
    // no operation ever lands would satisfy every invariant vacuously.
    let mut landed_stakes = 0usize;
    let mut landed_unstakes = 0usize;
    let mut landed_closes = 0usize;

    // A sink for the uniquifying lamport transfer below. It must be funded
    // up front: a transfer of a few lamports to a fresh system account leaves it
    // below the rent-exempt minimum, and the runtime rejects the whole
    // transaction with InsufficientFundsForRent — which would fail every
    // operation for a reason that has nothing to do with the program.
    let sink = Pubkey::new_unique();
    fund(&mut ctx, &sink);

    for op in 1..=OPS {
        let ai = rng.below(USERS as u64) as usize;
        let mi = rng.below(mints.len() as u64) as usize;
        let mint = mints[mi];
        let owner = actors[ai].keypair.pubkey();
        let ata = actors[ai].atas[mi];

        // Every so often, jump the clock past a lock-up so withdrawals are
        // actually reachable. Without this the lock would reject every unstake
        // and the withdrawal accounting would never be exercised at all.
        //
        // This MUST happen before the state is read below. Warping afterwards
        // leaves `clock_before` stale, and I3 then compares a landed unstake
        // against a timestamp earlier than the one the program actually saw —
        // which reports a lock-up violation for a withdrawal that was properly
        // unlocked. (That is exactly what the first run of this test did.)
        if rng.below(4) == 0 {
            let mut clock: Clock = ctx.banks_client.get_sysvar().await.unwrap();
            clock.unix_timestamp += rclaw_staking::LOCKUP_SECONDS / 2 + 1;
            ctx.set_sysvar(&clock);
        }

        let (staked_before, unlock_before) = stake_state(&mut ctx, &owner, &mint).await;
        let held_before = token_balance(&mut ctx, &ata).await;
        // Read after the warp, so this is the timestamp the transaction will run
        // at: program-test's clock does not advance on its own between
        // transactions in a bank, it only moves when set explicitly above.
        let clock_before = now(&mut ctx).await;

        // 0..=3 chosen so stake is the most common action — a sequence that
        // mostly withdraws never builds enough state to be interesting.
        let action = rng.below(10);
        let (ix, what) = if action < 5 {
            // Deliberately over-range sometimes (the +1 slack and the 0 case) so
            // the rejection paths get exercised alongside the happy path.
            let amount = rng.below(held_before.saturating_add(2));
            (stake_ix(&owner, &mint, &ata, amount), format!("stake {amount}"))
        } else if action < 9 {
            // Every third withdrawal attempts a FULL exit. A purely uniform
            // amount almost never lands a record on exactly zero, so without
            // this the close path is never reachable and `close` would be
            // covered by nothing (the first run of this test landed 0 closes).
            let amount = if rng.below(3) == 0 {
                staked_before
            } else {
                rng.below(staked_before.saturating_add(2))
            };
            (unstake_ix(&owner, &mint, &ata, amount), format!("unstake {amount}"))
        } else {
            (close_ix(&owner, &mint), "close".to_string())
        };

        // Make every transaction unique. Two identical operations under the same
        // blockhash would otherwise collide as a duplicate signature and be
        // rejected by the harness rather than by the program — a rejection that
        // proves nothing about the program.
        let uniquify = system_instruction::transfer(&payer, &sink, op as u64);
        let bh = ctx.banks_client.get_latest_blockhash().await.unwrap();
        let tx = Transaction::new_signed_with_payer(
            &[uniquify, ix],
            Some(&payer),
            &[&ctx.payer, &actors[ai].keypair],
            bh,
        );
        let landed = ctx.banks_client.process_transaction(tx).await.is_ok();

        assert_invariants(&mut ctx, &actors, &mints, total_minted, op, &what).await;

        let (staked_after, unlock_after) = stake_state(&mut ctx, &owner, &mint).await;

        if landed {
            if what.starts_with("stake") {
                landed_stakes += 1;
            } else if what.starts_with("unstake") {
                landed_unstakes += 1;
                // I3: judged against the state read BEFORE the operation, so a
                // withdrawal cannot be excused by a lock that was cleared as a
                // side effect of the very transaction being judged.
                assert!(
                    clock_before >= unlock_before,
                    "I3 LOCK-UP VIOLATED at op {op}: unstake succeeded at t={clock_before} \
                     while the record was locked until {unlock_before}. seed={SEED:#x}"
                );
            } else {
                landed_closes += 1;
                assert_eq!(
                    staked_before, 0,
                    "a record holding {staked_before} base units was closed at op {op} — \
                     escrowed tokens would be orphaned in the vault. seed={SEED:#x}"
                );
            }
        }

        // I4: a live position's unlock must never move backwards. (A fully
        // exited record legitimately resets to 0, and a closed one reads 0.)
        if staked_before > 0 && staked_after > 0 {
            assert!(
                unlock_after >= unlock_before,
                "I4 MONOTONIC LOCK VIOLATED at op {op} ({what}): unlock_at moved \
                 {unlock_before} -> {unlock_after} on a position still holding \
                 {staked_after}. seed={SEED:#x}"
            );
        }
    }

    // The suite must have actually exercised the paths it claims to cover. This
    // is the check that stops the whole test degrading into a tautology if a
    // future change causes every operation to be rejected.
    assert!(
        landed_stakes >= 5,
        "only {landed_stakes} stakes landed in {OPS} ops — the invariants above were \
         checked against a vault nothing ever entered. seed={SEED:#x}"
    );
    assert!(
        landed_unstakes >= 3,
        "only {landed_unstakes} unstakes landed in {OPS} ops — the withdrawal accounting \
         was never exercised. seed={SEED:#x}"
    );
    assert!(
        landed_closes >= 1,
        "no close landed in {OPS} ops — rent reclaim and the zero-balance constraint \
         were never exercised. seed={SEED:#x}"
    );
    eprintln!(
        "solvency sim: {OPS} ops, {landed_stakes} stakes / {landed_unstakes} unstakes / \
         {landed_closes} closes landed; I1-I4 held throughout (seed {SEED:#x})"
    );
}
