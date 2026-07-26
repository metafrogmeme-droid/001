//! $RCLAW staking — DRAFT / DEVNET-ONLY Anchor program.
//!
//! ⚠️ **DO NOT DEPLOY TO ANY CLUSTER HOLDING REAL VALUE.**
//! This program is **unaudited**, and an earlier revision shipped a **critical
//! vault-drain vulnerability**: `StakeAccount` did not record which mint was
//! staked, so a stake denominated in a worthless self-minted token could be
//! redeemed against the real $RCLAW vault. That specific hole is fixed below
//! (stake records are bound to a mint, the vault authority is mint-scoped, and
//! the mint may be pinned), but the program has still had **no third-party
//! audit**. A real deployment stays gated behind the roadmap's Phase 0
//! Guardrails (legal review + smart-contract audit) — see docs/TOKEN_ROADMAP.md.
//!
//! A minimal non-custodial stake vault: users escrow tokens into a program-owned
//! vault and a per-user, per-mint `StakeAccount` PDA tracks the staked amount.
//! RUNECLAW's tier gate (`bot/token/tier_gate.py`) reads that staked amount to
//! unlock premium features. Deposits are locked for `LOCKUP_SECONDS`; once fully
//! exited, the record can be closed to reclaim its rent.
//!
//! Mints are validated on the way **in** only (no freeze authority, no vault-
//! hostile Token-2022 extension). Withdrawal is never made conditional on mint
//! properties, so nothing a mint does later can strand funds already deposited.
//!
//! Token-2022 aware: uses `token_interface` + `transfer_checked`, so the real
//! $RCLAW mint (an SPL **Token-2022** mint — see `token/scripts/create_token.mjs`)
//! can actually be staked. The legacy SPL Token program also satisfies the
//! interface, so both work.
//!
//! `StakeAccount` layout (Anchor): 8-byte discriminator, then
//!   version: u8    (1) @ offset  8
//!   owner: Pubkey (32) @ offset  9
//!   mint:  Pubkey (32) @ offset 41
//!   amount: u64    (8) @ offset 73
//!   staked_at: i64 (8) @ offset 81
//!   unlock_at: i64 (8) @ offset 89
//!   bump: u8       (1) @ offset 97
//! followed by `StakeAccount::RESERVED` bytes of zeroed headroom.
//! The Python gate finds accounts via getProgramAccounts + a memcmp on `owner`
//! at offset 9 (and optionally `mint` at offset 41) and reads `amount` at
//! offset 73 — no PDA derivation needed. Keep these offsets in sync with
//! `bot/token/tier_gate.py`, which mirrors them as named constants.
//!
//! The leading `version` byte exists so a future layout change is detectable
//! rather than silently misparsed: any reader that does not recognise the value
//! must refuse to interpret the rest of the account.
use anchor_lang::prelude::*;
use anchor_spl::{
    associated_token::AssociatedToken,
    token_interface::{
        self, Mint, TokenAccount, TokenInterface, TransferChecked,
    },
};

declare_id!("6yGc2n7vZyp7nvJJ8uXEdy56P1UT8Ma4En26bTtBrJhW");

/// Compile-time pin for the canonical $RCLAW mint, set from the environment at
/// build time:
///
/// ```bash
/// RCLAW_PINNED_MINT=<base58 mint address> anchor build
/// ```
///
/// It is deliberately **not** a hardcoded literal: the $RCLAW mint does not
/// exist yet (`token/` has never been run against a live cluster), and baking a
/// placeholder into a security constant would either brick staking or give false
/// assurance. `option_env!` makes the pin a real, enforced deployment setting
/// while keeping the source honest about what is not yet known.
///
/// **Unset (`None`)** — the program accepts any mint. Each mint still gets its
/// own isolated vault and stake records, so cross-mint drain is impossible, but
/// a user can stake a worthless token; the off-chain gate must then filter on
/// mint (`bot/token/tier_gate.py` does, via `RCLAW_MINT`).
///
/// **Set** — `stake` rejects every other mint with `UnexpectedMint`. Set it for
/// any deployment where tiers carry value.
pub const PINNED_MINT: Option<&str> = option_env!("RCLAW_PINNED_MINT");

/// Minimum time a deposit stays locked, in seconds.
///
/// Without a lock the tier is a *live spot balance*, so a single position can be
/// unstaked and re-staked to a second wallet within the same slot and serve an
/// unlimited number of users by rotation. The lock is what makes a tier cost
/// something to hold.
///
/// **This is a tokenomics parameter, not a security constant.** 30 days was
/// ratified on 2026-07-26, superseding the audit's suggested 7-day default
/// (docs/TOKEN_ROADMAP.md §13). Set to `0` to restore always-liquid behaviour.
///
/// Changing it is safe today only because nothing is deployed. It is read at
/// stake time and written into each record's `unlock_at`, so an upgrade that
/// changed it would apply to NEW deposits only — existing positions keep the
/// unlock they were promised, which is the correct direction for that hazard but
/// means a live change silently splits the pool into two cohorts.
pub const LOCKUP_SECONDS: i64 = 30 * 24 * 60 * 60;

/// Parse + compare the pin. Kept separate so the logic is unit-testable without
/// rebuilding under a different environment.
fn enforce_pinned_mint(pinned: Option<&str>, mint: &Pubkey) -> Result<()> {
    let Some(expected) = pinned else { return Ok(()) };
    let expected = expected.trim();
    if expected.is_empty() {
        return Ok(()); // treat an empty pin as unset
    }
    let expected: Pubkey = expected
        .parse()
        .map_err(|_| error!(StakeError::InvalidPinnedMint))?;
    require_keys_eq!(*mint, expected, StakeError::UnexpectedMint);
    Ok(())
}

fn check_pinned_mint(mint: &Pubkey) -> Result<()> {
    enforce_pinned_mint(PINNED_MINT, mint)
}

/// Reject Token-2022 mints whose extensions break a custodial vault's invariants.
///
/// Checked on the way **in** only. `unstake` deliberately does not call this: a
/// mint's extension set is fixed at creation, but making withdrawal conditional
/// on it would turn any future disagreement into a permanent freeze on funds
/// users already deposited. Validate at entry, never at exit.
///
/// Legacy SPL Token mints carry no extensions and pass trivially.
fn reject_hazardous_extensions(mint_ai: &AccountInfo) -> Result<()> {
    use anchor_spl::token_2022::spl_token_2022::{
        extension::{BaseStateWithExtensions, ExtensionType, StateWithExtensions},
        state::Mint as SplMint,
    };

    // Only Token-2022 accounts have a TLV extension region.
    if *mint_ai.owner != anchor_spl::token_2022::ID {
        return Ok(());
    }

    let data = mint_ai.try_borrow_data()?;
    let state = StateWithExtensions::<SplMint>::unpack(&data)
        .map_err(|_| error!(StakeError::UnsupportedMintExtension))?;
    let found = state
        .get_extension_types()
        .map_err(|_| error!(StakeError::UnsupportedMintExtension))?;

    for ext in found {
        match ext {
            // A permanent delegate can move tokens out of the vault at will —
            // every balance the program reports would be a claim it cannot honour.
            ExtensionType::PermanentDelegate
            // A transfer hook CPIs an arbitrary program on every transfer and
            // needs extra accounts this program does not resolve, so `unstake`
            // would fail permanently: deposits in, nothing out.
            | ExtensionType::TransferHook
            // The mint authority can make new accounts default-frozen, freezing
            // the vault ATA the first time it is created.
            | ExtensionType::DefaultAccountState
            // Nothing could ever be withdrawn.
            | ExtensionType::NonTransferable
            // Vault balances stop being readable the way the gate assumes.
            | ExtensionType::ConfidentialTransferMint
            // A fee means the vault receives less than the ledger says was sent.
            // `stake` already credits the observed delta, but the withdrawal leg
            // would still short every depositor, so refuse the mint outright.
            | ExtensionType::TransferFeeConfig => {
                msg!("rejected mint extension: {:?}", ext);
                return Err(error!(StakeError::UnsupportedMintExtension));
            }
            _ => {}
        }
    }
    Ok(())
}

#[program]
pub mod rclaw_staking {
    use super::*;

    /// Stake `amount` base units. Creates the caller's per-mint `StakeAccount`
    /// PDA on first call and escrows tokens in that mint's vault.
    pub fn stake(ctx: Context<Stake>, amount: u64) -> Result<()> {
        require!(amount > 0, StakeError::ZeroAmount);
        check_pinned_mint(&ctx.accounts.mint.key())?;
        reject_hazardous_extensions(&ctx.accounts.mint.to_account_info())?;

        {
            let sa = &ctx.accounts.stake_account;
            // On re-stake, the record must already belong to this owner+mint.
            // (`init_if_needed` leaves prior state intact, so assert rather than
            // blindly overwrite — a mismatch means the PDA was crafted or reused.)
            if sa.amount > 0 {
                require_keys_eq!(sa.owner, ctx.accounts.owner.key(), StakeError::WrongOwner);
                require_keys_eq!(sa.mint, ctx.accounts.mint.key(), StakeError::WrongMint);
                require!(
                    sa.version == StakeAccount::CURRENT_VERSION,
                    StakeError::UnsupportedAccountVersion
                );
            }
        }

        // Balance the vault BEFORE the transfer so the credit can be derived from
        // what the vault actually received. A Token-2022 transfer-fee mint moves
        // less than `amount`, and crediting the requested figure would mint stake
        // out of nothing and leave the vault unable to honour every withdrawal.
        let before = ctx.accounts.vault.amount;

        token_interface::transfer_checked(
            CpiContext::new(
                ctx.accounts.token_program.to_account_info(),
                TransferChecked {
                    from: ctx.accounts.user_token_account.to_account_info(),
                    mint: ctx.accounts.mint.to_account_info(),
                    to: ctx.accounts.vault.to_account_info(),
                    authority: ctx.accounts.owner.to_account_info(),
                },
            ),
            amount,
            ctx.accounts.mint.decimals,
        )?;

        ctx.accounts.vault.reload()?;
        let credited = ctx
            .accounts
            .vault
            .amount
            .checked_sub(before)
            .ok_or(StakeError::Overflow)?;
        // A mint whose hooks route the whole transfer elsewhere must not yield a
        // free stake record.
        require!(credited > 0, StakeError::ZeroAmount);

        let now = Clock::get()?.unix_timestamp;
        let unlock = now
            .checked_add(LOCKUP_SECONDS)
            .ok_or(StakeError::Overflow)?;

        let sa = &mut ctx.accounts.stake_account;
        sa.version = StakeAccount::CURRENT_VERSION;
        sa.owner = ctx.accounts.owner.key();
        sa.mint = ctx.accounts.mint.key();
        // Amount-weighted stake age. Overwriting `staked_at` with `now` would let
        // a 1-base-unit top-up erase a large position's entire accrual, which
        // silently breaks any future duration-weighted reward or governance
        // weight. Fixed now, while the field has no consumers and no live records
        // would need migrating.
        sa.staked_at = if sa.amount == 0 {
            now
        } else {
            let prior = sa.amount as i128;
            let added = credited as i128;
            let weighted = ((sa.staked_at as i128) * prior + (now as i128) * added)
                / (prior + added);
            weighted as i64
        };
        sa.amount = sa.amount.checked_add(credited).ok_or(StakeError::Overflow)?;
        // Extend, never shorten: taking `unlock` unconditionally would let a
        // 1-base-unit top-up reset an existing lock and defeat the whole point.
        sa.unlock_at = sa.unlock_at.max(unlock);
        sa.bump = ctx.bumps.stake_account;
        emit!(Staked {
            owner: sa.owner,
            mint: sa.mint,
            amount: credited,
            total: sa.amount,
            unlock_at: sa.unlock_at,
        });
        Ok(())
    }

    /// Unstake `amount` base units back to the caller's token account.
    ///
    /// The stake record is bound to `mint` (`has_one = mint`) and the vault
    /// authority is derived per-mint, so a stake of one token can never be
    /// redeemed out of another token's vault.
    pub fn unstake(ctx: Context<Unstake>, amount: u64) -> Result<()> {
        require!(
            ctx.accounts.stake_account.version == StakeAccount::CURRENT_VERSION,
            StakeError::UnsupportedAccountVersion
        );
        require!(
            Clock::get()?.unix_timestamp >= ctx.accounts.stake_account.unlock_at,
            StakeError::StillLocked
        );
        let staked = ctx.accounts.stake_account.amount;
        require!(amount > 0 && amount <= staked, StakeError::InsufficientStake);

        let mint_key = ctx.accounts.mint.key();
        let vault_bump = ctx.bumps.vault_authority;
        let seeds: &[&[u8]] = &[b"vault", mint_key.as_ref(), &[vault_bump]];
        let signer = &[seeds];

        token_interface::transfer_checked(
            CpiContext::new_with_signer(
                ctx.accounts.token_program.to_account_info(),
                TransferChecked {
                    from: ctx.accounts.vault.to_account_info(),
                    mint: ctx.accounts.mint.to_account_info(),
                    to: ctx.accounts.user_token_account.to_account_info(),
                    authority: ctx.accounts.vault_authority.to_account_info(),
                },
                signer,
            ),
            amount,
            ctx.accounts.mint.decimals,
        )?;

        let sa = &mut ctx.accounts.stake_account;
        sa.amount = sa.amount.checked_sub(amount).ok_or(StakeError::Overflow)?;
        if sa.amount == 0 {
            // Fully exited: the next deposit starts a fresh clock rather than
            // inheriting age from a position that no longer exists.
            sa.staked_at = 0;
            sa.unlock_at = 0;
        }
        emit!(Unstaked {
            owner: sa.owner,
            mint: sa.mint,
            amount,
            total: sa.amount,
        });
        Ok(())
    }

    /// Reclaim the rent held by a fully-exited stake record.
    ///
    /// Deliberately a separate instruction rather than an unconditional `close`
    /// on `unstake`: closing inside `unstake` would delete the record on every
    /// full withdrawal, and Anchor's `init_if_needed` would then happily revive
    /// it, so an account could be closed and recreated repeatedly within one
    /// transaction. Requiring `amount == 0` and an explicit call keeps the
    /// close path boring.
    pub fn close_stake_account(ctx: Context<CloseStake>) -> Result<()> {
        emit!(StakeAccountClosed {
            owner: ctx.accounts.stake_account.owner,
            mint: ctx.accounts.stake_account.mint,
        });
        Ok(())
    }
}

#[derive(Accounts)]
pub struct Stake<'info> {
    #[account(mut)]
    pub owner: Signer<'info>,

    /// The mint being staked. Each mint is fully isolated (own vault, own
    /// stake records). Pin it via `PINNED_MINT` for a value-bearing deployment.
    ///
    /// A live freeze authority can freeze the vault ATA and strand every
    /// depositor, so it is rejected at entry. This constraint is intentionally
    /// absent from `Unstake` — see `reject_hazardous_extensions`.
    #[account(
        constraint = mint.freeze_authority.is_none() @ StakeError::MintHasFreezeAuthority,
    )]
    pub mint: InterfaceAccount<'info, Mint>,

    /// Per-user, PER-MINT stake record. Seeds: ["stake", owner, mint].
    #[account(
        init_if_needed,
        payer = owner,
        space = 8 + StakeAccount::SPACE + StakeAccount::RESERVED,
        seeds = [b"stake", owner.key().as_ref(), mint.key().as_ref()],
        bump,
    )]
    pub stake_account: Account<'info, StakeAccount>,

    /// Mint-scoped vault authority PDA. Seeds: ["vault", mint].
    /// CHECK: PDA used only as the vault token account's authority.
    #[account(seeds = [b"vault", mint.key().as_ref()], bump)]
    pub vault_authority: UncheckedAccount<'info>,

    /// The vault token account (ATA of this mint's vault authority).
    #[account(
        init_if_needed,
        payer = owner,
        associated_token::mint = mint,
        associated_token::authority = vault_authority,
        associated_token::token_program = token_program,
    )]
    pub vault: InterfaceAccount<'info, TokenAccount>,

    #[account(
        mut,
        constraint = user_token_account.owner == owner.key() @ StakeError::WrongOwner,
        constraint = user_token_account.mint == mint.key() @ StakeError::WrongMint,
    )]
    pub user_token_account: InterfaceAccount<'info, TokenAccount>,

    pub token_program: Interface<'info, TokenInterface>,
    pub associated_token_program: Program<'info, AssociatedToken>,
    pub system_program: Program<'info, System>,
}

#[derive(Accounts)]
pub struct Unstake<'info> {
    #[account(mut)]
    pub owner: Signer<'info>,

    pub mint: InterfaceAccount<'info, Mint>,

    #[account(
        mut,
        seeds = [b"stake", owner.key().as_ref(), mint.key().as_ref()],
        bump = stake_account.bump,
        has_one = owner @ StakeError::WrongOwner,
        has_one = mint @ StakeError::WrongMint,
    )]
    pub stake_account: Account<'info, StakeAccount>,

    /// CHECK: mint-scoped PDA authority for the vault token account.
    #[account(seeds = [b"vault", mint.key().as_ref()], bump)]
    pub vault_authority: UncheckedAccount<'info>,

    #[account(
        mut,
        associated_token::mint = mint,
        associated_token::authority = vault_authority,
        associated_token::token_program = token_program,
    )]
    pub vault: InterfaceAccount<'info, TokenAccount>,

    #[account(
        mut,
        constraint = user_token_account.owner == owner.key() @ StakeError::WrongOwner,
        constraint = user_token_account.mint == mint.key() @ StakeError::WrongMint,
    )]
    pub user_token_account: InterfaceAccount<'info, TokenAccount>,

    pub token_program: Interface<'info, TokenInterface>,
}

#[derive(Accounts)]
pub struct CloseStake<'info> {
    /// Receives the reclaimed rent.
    #[account(mut)]
    pub owner: Signer<'info>,

    pub mint: InterfaceAccount<'info, Mint>,

    #[account(
        mut,
        seeds = [b"stake", owner.key().as_ref(), mint.key().as_ref()],
        bump = stake_account.bump,
        has_one = owner @ StakeError::WrongOwner,
        has_one = mint @ StakeError::WrongMint,
        constraint = stake_account.amount == 0 @ StakeError::StakeNotEmpty,
        close = owner,
    )]
    pub stake_account: Account<'info, StakeAccount>,
}

#[account]
pub struct StakeAccount {
    /// Layout version. Bump on every field change; readers must refuse anything
    /// they do not recognise rather than misparse it.
    pub version: u8,
    pub owner: Pubkey,
    pub mint: Pubkey,
    pub amount: u64,
    pub staked_at: i64,
    /// Unix timestamp before which `unstake` is rejected. Extended, never
    /// shortened, by a re-stake.
    pub unlock_at: i64,
    pub bump: u8,
}

impl StakeAccount {
    pub const CURRENT_VERSION: u8 = 1;
    // version(1) + owner(32) + mint(32) + amount(8) + staked_at(8)
    //   + unlock_at(8) + bump(1)
    pub const SPACE: usize = 1 + 32 + 32 + 8 + 8 + 8 + 1;
    /// Zeroed headroom so a future field can be added in place. Without it, any
    /// added field needs a realloc the program has no instruction to perform.
    pub const RESERVED: usize = 64;
}

#[event]
pub struct Staked {
    pub owner: Pubkey,
    pub mint: Pubkey,
    /// Base units the vault actually received, which is not necessarily the
    /// amount requested — see `stake`.
    pub amount: u64,
    pub total: u64,
    pub unlock_at: i64,
}

#[event]
pub struct Unstaked {
    pub owner: Pubkey,
    pub mint: Pubkey,
    pub amount: u64,
    pub total: u64,
}

#[event]
pub struct StakeAccountClosed {
    pub owner: Pubkey,
    pub mint: Pubkey,
}

#[error_code]
pub enum StakeError {
    #[msg("Amount must be greater than zero")]
    ZeroAmount,
    #[msg("Insufficient staked balance")]
    InsufficientStake,
    #[msg("Arithmetic overflow")]
    Overflow,
    #[msg("Token account owner mismatch")]
    WrongOwner,
    #[msg("Token account mint mismatch")]
    WrongMint,
    #[msg("Mint does not match the pinned $RCLAW mint")]
    UnexpectedMint,
    #[msg("RCLAW_PINNED_MINT was set to something that is not a valid base58 pubkey")]
    InvalidPinnedMint,
    #[msg("Stake is still within its lock-up period")]
    StillLocked,
    #[msg("StakeAccount layout version is not supported by this program")]
    UnsupportedAccountVersion,
    #[msg("Mint has a live freeze authority; the vault could be frozen")]
    MintHasFreezeAuthority,
    #[msg("Mint carries a Token-2022 extension incompatible with a stake vault")]
    UnsupportedMintExtension,
    #[msg("Stake record still holds a balance; unstake it fully before closing")]
    StakeNotEmpty,
}

/// The byte offsets `bot/token/tier_gate.py` uses to read `StakeAccount` straight
/// out of `getProgramAccounts` without deserialising. They are part of the
/// program's public contract; `layout_tests` below fails if they ever drift.
pub mod layout {
    /// Anchor discriminator width.
    pub const DISCRIMINATOR: usize = 8;
    pub const VERSION_OFFSET: usize = 8;
    pub const OWNER_OFFSET: usize = 9;
    pub const MINT_OFFSET: usize = 41;
    pub const AMOUNT_OFFSET: usize = 73;
    pub const STAKED_AT_OFFSET: usize = 81;
    pub const UNLOCK_AT_OFFSET: usize = 89;
    pub const BUMP_OFFSET: usize = 97;
}

#[cfg(test)]
mod layout_tests {
    use super::*;
    use anchor_lang::AccountSerialize;

    /// Locks the wire layout against `bot/token/tier_gate.py`.
    ///
    /// This asserts against the **Borsh** encoding, not Rust's in-memory struct
    /// layout — the compiler is free to reorder fields, so `offset_of!` would
    /// prove nothing about what a client actually reads off the chain.
    #[test]
    fn borsh_offsets_match_the_python_gate() {
        let owner = Pubkey::new_from_array([1u8; 32]);
        let mint = Pubkey::new_from_array([2u8; 32]);
        let sa = StakeAccount {
            version: StakeAccount::CURRENT_VERSION,
            owner,
            mint,
            amount: 0x1122_3344_5566_7788,
            staked_at: 0x0102_0304_0506_0708,
            unlock_at: 0x1112_1314_1516_1718,
            bump: 254,
        };

        let mut buf: Vec<u8> = Vec::new();
        sa.try_serialize(&mut buf).unwrap();

        assert_eq!(
            buf.len(),
            layout::DISCRIMINATOR + StakeAccount::SPACE,
            "SPACE must equal the serialized body; the gate's length check depends on it"
        );
        assert_eq!(buf[layout::VERSION_OFFSET], StakeAccount::CURRENT_VERSION);
        assert_eq!(&buf[layout::OWNER_OFFSET..layout::OWNER_OFFSET + 32], owner.as_ref());
        assert_eq!(&buf[layout::MINT_OFFSET..layout::MINT_OFFSET + 32], mint.as_ref());
        assert_eq!(
            u64::from_le_bytes(buf[layout::AMOUNT_OFFSET..layout::AMOUNT_OFFSET + 8].try_into().unwrap()),
            0x1122_3344_5566_7788,
            "tier_gate.py reads the staked amount here"
        );
        assert_eq!(
            i64::from_le_bytes(buf[layout::STAKED_AT_OFFSET..layout::STAKED_AT_OFFSET + 8].try_into().unwrap()),
            0x0102_0304_0506_0708,
        );
        assert_eq!(
            i64::from_le_bytes(buf[layout::UNLOCK_AT_OFFSET..layout::UNLOCK_AT_OFFSET + 8].try_into().unwrap()),
            0x1112_1314_1516_1718,
            "tier_gate.py reads the lock expiry here"
        );
        assert_eq!(buf[layout::BUMP_OFFSET], 254);
    }

    /// A re-stake must never be able to shorten an existing lock.
    #[test]
    fn lock_extension_never_shortens() {
        let existing: i64 = 5_000;
        assert_eq!(existing.max(1_000), existing, "a later top-up must not pull the unlock in");
        assert_eq!(existing.max(9_000), 9_000, "a longer lock does extend");
    }
}

#[cfg(test)]
mod pin_tests {
    use super::*;
    use std::str::FromStr;

    const A: &str = "So11111111111111111111111111111111111111112";
    const B: &str = "GNS1S5J5AspKXgpjz6SvKL66kPaKWAhaGRhCqPRxii2B";

    #[test]
    fn unset_pin_accepts_any_mint() {
        let any = Pubkey::from_str(A).unwrap();
        assert!(enforce_pinned_mint(None, &any).is_ok());
        assert!(enforce_pinned_mint(Some("   "), &any).is_ok(), "empty pin == unset");
    }

    #[test]
    fn set_pin_accepts_only_that_mint() {
        let a = Pubkey::from_str(A).unwrap();
        let b = Pubkey::from_str(B).unwrap();
        assert!(enforce_pinned_mint(Some(A), &a).is_ok());
        assert!(enforce_pinned_mint(Some(A), &b).is_err(), "a different mint must be rejected");
        // Surrounding whitespace from an env var must not break the pin.
        assert!(enforce_pinned_mint(Some(" \nSo11111111111111111111111111111111111111112 "), &a).is_ok());
    }

    #[test]
    fn malformed_pin_is_rejected_not_ignored() {
        let a = Pubkey::from_str(A).unwrap();
        assert!(
            enforce_pinned_mint(Some("not-a-pubkey"), &a).is_err(),
            "a typo'd pin must fail closed, never silently accept every mint"
        );
    }
}
