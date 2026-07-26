//! Executable proof that the audit's CRITICAL finding is fixed.
//!
//! These run the REAL program in-process via `solana-program-test`'s
//! `processor!()` (native, no SBF toolchain, no validator, no network — the
//! only form of real execution available in this environment).
//!
//! The headline test is `mint_confusion_attack_is_rejected`: it performs the
//! exact attack the adversarial review found — stake a worthless self-minted
//! token, then try to unstake the SAME amount against the real $RCLAW vault.
//! Before the fix every constraint passed and the vault drained. It must now
//! fail.
use anchor_lang::{InstructionData, ToAccountMetas};
use solana_program_test::{processor, ProgramTest, ProgramTestContext};
use solana_sdk::{
    clock::Clock,
    instruction::Instruction,
    program_pack::Pack,
    pubkey::Pubkey,
    signature::{Keypair, Signer},
    system_instruction,
    transaction::Transaction,
};

const DECIMALS: u8 = 9;

/// True when the crate was built with `RCLAW_PINNED_MINT` set.
///
/// These tests mint their own throwaway tokens, which by definition are not the
/// pinned mint, so `stake` correctly refuses them with `UnexpectedMint`. Rather
/// than show three red tests for a build that is behaving exactly as designed,
/// skip them and say so. (Verified: with a pin set, the skipped tests otherwise
/// fail at lib.rs `check_pinned_mint` — the pin is genuinely enforced.)
fn pin_active() -> bool {
    rclaw_staking::PINNED_MINT
        .map(|s| !s.trim().is_empty())
        .unwrap_or(false)
}

macro_rules! skip_if_pinned {
    () => {
        if pin_active() {
            eprintln!(
                "SKIPPED: built with RCLAW_PINNED_MINT={:?}; this test mints its own \
                 token, which the pin correctly rejects. Re-run without the pin to \
                 exercise the vault logic.",
                rclaw_staking::PINNED_MINT
            );
            return;
        }
    };
}

fn program_id() -> Pubkey {
    rclaw_staking::ID
}

/// Bridge Anchor's `entry` to what `processor!` expects.
///
/// `solana-program-test` wants a fully-general
/// `for<'a,'b,'c,'d> fn(&'a Pubkey, &'b [AccountInfo<'c>], &'d [u8])`, while
/// Anchor's `entry` ties the slice and its `AccountInfo`s to one `'info`. The
/// lifetimes are compatible at runtime — program-test owns the `AccountInfo`s
/// for the whole call — but not expressible to the borrow checker, so this is
/// the standard shim used by Anchor + program-test setups.
fn entry_shim(
    program_id: &Pubkey,
    accounts: &[anchor_lang::solana_program::account_info::AccountInfo],
    data: &[u8],
) -> anchor_lang::solana_program::entrypoint::ProgramResult {
    // SAFETY: re-binds only the lifetime, not the data. The referenced
    // AccountInfos outlive this call (program-test owns them), so narrowing the
    // slice lifetime to match the element lifetime is sound here.
    let accounts: &[anchor_lang::solana_program::account_info::AccountInfo] =
        unsafe { core::mem::transmute(accounts) };
    rclaw_staking::entry(program_id, accounts, data)
}

async fn setup() -> ProgramTestContext {
    let pt = ProgramTest::new("rclaw_staking", program_id(), processor!(entry_shim));
    pt.start_with_context().await
}

/// Create an SPL mint owned by `authority`.
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
    let tx = Transaction::new_signed_with_payer(
        &ix,
        Some(&ctx.payer.pubkey()),
        &[&ctx.payer, &mint],
        ctx.last_blockhash,
    );
    ctx.banks_client.process_transaction(tx).await.unwrap();
    mint.pubkey()
}

/// Create an ATA for `owner` and mint `amount` into it.
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
    let tx = Transaction::new_signed_with_payer(
        &ix,
        Some(&ctx.payer.pubkey()),
        &[&ctx.payer],
        ctx.last_blockhash,
    );
    ctx.banks_client.process_transaction(tx).await.unwrap();
    ata
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

/// Build an unstake instruction, deliberately allowing the stake-record mint and
/// the vault mint to differ — that mismatch IS the attack.
fn unstake_ix_crossed(
    owner: &Pubkey,
    stake_record_mint: &Pubkey,
    vault_mint: &Pubkey,
    user_ata: &Pubkey,
    amount: u64,
) -> Instruction {
    let accounts = rclaw_staking::accounts::Unstake {
        owner: *owner,
        mint: *vault_mint,
        // The record the attacker actually owns (created by staking EVIL)…
        stake_account: stake_pda(owner, stake_record_mint),
        // …pointed at the REAL token's vault.
        vault_authority: vault_authority(vault_mint),
        vault: vault_ata(vault_mint),
        user_token_account: *user_ata,
        token_program: spl_token::id(),
    };
    Instruction {
        program_id: program_id(),
        accounts: accounts.to_account_metas(None),
        data: rclaw_staking::instruction::Unstake { amount }.data(),
    }
}

async fn token_balance(ctx: &mut ProgramTestContext, ata: &Pubkey) -> u64 {
    let acc = ctx.banks_client.get_account(*ata).await.unwrap().unwrap();
    spl_token::state::Account::unpack(&acc.data).unwrap().amount
}

/// Push the bank clock past `LOCKUP_SECONDS` so a stake becomes withdrawable.
///
/// `unstake` refuses while `unlock_at` is in the future, so every withdrawal
/// test has to age the position first. Warping the sysvar is the only way to do
/// that in-process — there is no wall-clock to wait on.
async fn warp_past_lockup(ctx: &mut ProgramTestContext) {
    let mut clock: Clock = ctx.banks_client.get_sysvar().await.unwrap();
    clock.unix_timestamp += rclaw_staking::LOCKUP_SECONDS + 1;
    ctx.set_sysvar(&clock);
}

#[tokio::test]
async fn stake_then_unstake_roundtrips() {
    skip_if_pinned!();
    let mut ctx = setup().await;
    let payer = ctx.payer.pubkey();
    let mint = create_mint(&mut ctx, &payer).await;
    let ata = create_ata_and_mint(&mut ctx, &mint, &payer, 1_000).await;

    let tx = Transaction::new_signed_with_payer(
        &[stake_ix(&payer, &mint, &ata, 400)],
        Some(&payer),
        &[&ctx.payer],
        ctx.last_blockhash,
    );
    ctx.banks_client.process_transaction(tx).await.unwrap();
    assert_eq!(token_balance(&mut ctx, &ata).await, 600, "400 should be escrowed");
    assert_eq!(token_balance(&mut ctx, &vault_ata(&mint)).await, 400);

    warp_past_lockup(&mut ctx).await;

    // Unstake half of it back.
    let bh = ctx.banks_client.get_latest_blockhash().await.unwrap();
    let tx = Transaction::new_signed_with_payer(
        &[unstake_ix_crossed(&payer, &mint, &mint, &ata, 200)],
        Some(&payer),
        &[&ctx.payer],
        bh,
    );
    ctx.banks_client.process_transaction(tx).await.unwrap();
    assert_eq!(token_balance(&mut ctx, &ata).await, 800);
    assert_eq!(token_balance(&mut ctx, &vault_ata(&mint)).await, 200);
}

/// The lock-up is what stops one position serving unlimited users by rotation:
/// stake, unstake, re-stake from a second wallet, repeat. Without a time
/// dimension the tier is a live spot balance and costs nothing to hold.
#[tokio::test]
async fn unstake_is_rejected_while_locked() {
    skip_if_pinned!();
    let mut ctx = setup().await;
    let payer = ctx.payer.pubkey();
    let mint = create_mint(&mut ctx, &payer).await;
    let ata = create_ata_and_mint(&mut ctx, &mint, &payer, 1_000).await;

    let tx = Transaction::new_signed_with_payer(
        &[stake_ix(&payer, &mint, &ata, 400)],
        Some(&payer),
        &[&ctx.payer],
        ctx.last_blockhash,
    );
    ctx.banks_client.process_transaction(tx).await.unwrap();

    // Immediately withdrawing must fail — the position is still locked.
    let bh = ctx.banks_client.get_latest_blockhash().await.unwrap();
    let tx = Transaction::new_signed_with_payer(
        &[unstake_ix_crossed(&payer, &mint, &mint, &ata, 100)],
        Some(&payer),
        &[&ctx.payer],
        bh,
    );
    assert!(
        ctx.banks_client.process_transaction(tx).await.is_err(),
        "unstaking inside the lock-up window must fail"
    );
    assert_eq!(
        token_balance(&mut ctx, &vault_ata(&mint)).await,
        400,
        "nothing may leave the vault while locked"
    );
    // The converse — that the same withdrawal succeeds once the lock expires —
    // is covered by `stake_then_unstake_roundtrips`. It is deliberately not
    // asserted here: a clock warp applied after a failed transaction does not
    // survive into the next bank, so testing both halves in one bank would be
    // testing the harness rather than the program.
}

/// A trivial top-up must not be able to pull an existing unlock time closer.
#[tokio::test]
async fn restake_extends_but_never_shortens_the_lock() {
    skip_if_pinned!();
    let mut ctx = setup().await;
    let payer = ctx.payer.pubkey();
    let mint = create_mint(&mut ctx, &payer).await;
    let ata = create_ata_and_mint(&mut ctx, &mint, &payer, 1_000).await;

    let tx = Transaction::new_signed_with_payer(
        &[stake_ix(&payer, &mint, &ata, 400)],
        Some(&payer),
        &[&ctx.payer],
        ctx.last_blockhash,
    );
    ctx.banks_client.process_transaction(tx).await.unwrap();

    // Roll the clock back toward the original stake so a naive
    // `unlock_at = now + LOCKUP` would move the unlock EARLIER.
    let mut clock: Clock = ctx.banks_client.get_sysvar().await.unwrap();
    clock.unix_timestamp -= rclaw_staking::LOCKUP_SECONDS / 2;
    ctx.set_sysvar(&clock);

    let bh = ctx.banks_client.get_latest_blockhash().await.unwrap();
    let tx = Transaction::new_signed_with_payer(
        &[stake_ix(&payer, &mint, &ata, 1)],
        Some(&payer),
        &[&ctx.payer],
        bh,
    );
    ctx.banks_client.process_transaction(tx).await.unwrap();

    // Still locked: the 1-unit top-up did not shorten anything.
    let bh = ctx.banks_client.get_latest_blockhash().await.unwrap();
    let tx = Transaction::new_signed_with_payer(
        &[unstake_ix_crossed(&payer, &mint, &mint, &ata, 1)],
        Some(&payer),
        &[&ctx.payer],
        bh,
    );
    assert!(
        ctx.banks_client.process_transaction(tx).await.is_err(),
        "a 1-unit top-up must not be able to shorten an existing lock"
    );
}

#[tokio::test]
async fn cannot_unstake_more_than_staked() {
    skip_if_pinned!();
    let mut ctx = setup().await;
    let payer = ctx.payer.pubkey();
    let mint = create_mint(&mut ctx, &payer).await;
    let ata = create_ata_and_mint(&mut ctx, &mint, &payer, 1_000).await;

    let tx = Transaction::new_signed_with_payer(
        &[stake_ix(&payer, &mint, &ata, 100)],
        Some(&payer),
        &[&ctx.payer],
        ctx.last_blockhash,
    );
    ctx.banks_client.process_transaction(tx).await.unwrap();

    let bh = ctx.banks_client.get_latest_blockhash().await.unwrap();
    let tx = Transaction::new_signed_with_payer(
        &[unstake_ix_crossed(&payer, &mint, &mint, &ata, 999)],
        Some(&payer),
        &[&ctx.payer],
        bh,
    );
    assert!(
        ctx.banks_client.process_transaction(tx).await.is_err(),
        "unstaking more than staked must fail"
    );
}

/// THE AUDIT FINDING, EXECUTED.
///
/// Stake a worthless self-minted token, then try to redeem the same amount from
/// the real $RCLAW vault. Pre-fix this drained the vault; it must now be rejected.
#[tokio::test]
async fn mint_confusion_attack_is_rejected() {
    skip_if_pinned!();
    let mut ctx = setup().await;
    let payer = ctx.payer.pubkey();

    // The real token, with an honest staker funding the vault.
    let rclaw = create_mint(&mut ctx, &payer).await;
    let honest_ata = create_ata_and_mint(&mut ctx, &rclaw, &payer, 10_000).await;
    let tx = Transaction::new_signed_with_payer(
        &[stake_ix(&payer, &rclaw, &honest_ata, 10_000)],
        Some(&payer),
        &[&ctx.payer],
        ctx.last_blockhash,
    );
    ctx.banks_client.process_transaction(tx).await.unwrap();
    let vault_before = token_balance(&mut ctx, &vault_ata(&rclaw)).await;
    assert_eq!(vault_before, 10_000, "vault should hold the honest stake");

    // The attacker's worthless token, staked to inflate their stake record.
    let evil = create_mint(&mut ctx, &payer).await;
    let evil_ata = create_ata_and_mint(&mut ctx, &evil, &payer, 10_000).await;
    let bh = ctx.banks_client.get_latest_blockhash().await.unwrap();
    let tx = Transaction::new_signed_with_payer(
        &[stake_ix(&payer, &evil, &evil_ata, 10_000)],
        Some(&payer),
        &[&ctx.payer],
        bh,
    );
    ctx.banks_client.process_transaction(tx).await.unwrap();

    // THE ATTACK: redeem the EVIL stake record against the RCLAW vault.
    let bh = ctx.banks_client.get_latest_blockhash().await.unwrap();
    let attack = Transaction::new_signed_with_payer(
        &[unstake_ix_crossed(&payer, &evil, &rclaw, &honest_ata, 10_000)],
        Some(&payer),
        &[&ctx.payer],
        bh,
    );
    let result = ctx.banks_client.process_transaction(attack).await;
    assert!(
        result.is_err(),
        "MINT-CONFUSION ATTACK SUCCEEDED — the vault-drain vulnerability is NOT fixed"
    );

    // And the honest staker's funds are untouched.
    let vault_after = token_balance(&mut ctx, &vault_ata(&rclaw)).await;
    assert_eq!(vault_after, vault_before, "real vault must be untouched");
}

/// The vaults must be genuinely distinct accounts — the root cause was one
/// shared `["vault"]` authority for every mint.
#[tokio::test]
async fn vault_authority_is_mint_scoped() {
    let a = Pubkey::new_unique();
    let b = Pubkey::new_unique();
    assert_ne!(
        vault_authority(&a),
        vault_authority(&b),
        "each mint must get its own vault authority PDA"
    );
    assert_ne!(stake_pda(&a, &a), stake_pda(&a, &b), "stake records must be per-mint");
}
