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
//! unlock premium features. Users can unstake at any time.
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

declare_id!("Fg6PaFpoGXkYsidMpWTK6W2BeZ7FEfcYkg476zPFsLnS");

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
/// **This is a tokenomics parameter, not a security constant** — the 7-day value
/// is the audit's suggested default and should be ratified alongside the tier
/// thresholds before any value-bearing deployment (docs/TOKEN_ROADMAP.md §13).
/// Set to `0` to restore the previous always-liquid behaviour.
pub const LOCKUP_SECONDS: i64 = 7 * 24 * 60 * 60;

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

#[program]
pub mod rclaw_staking {
    use super::*;

    /// Stake `amount` base units. Creates the caller's per-mint `StakeAccount`
    /// PDA on first call and escrows tokens in that mint's vault.
    pub fn stake(ctx: Context<Stake>, amount: u64) -> Result<()> {
        require!(amount > 0, StakeError::ZeroAmount);
        check_pinned_mint(&ctx.accounts.mint.key())?;

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
        sa.amount = sa.amount.checked_add(credited).ok_or(StakeError::Overflow)?;
        sa.staked_at = now;
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
        emit!(Unstaked {
            owner: sa.owner,
            mint: sa.mint,
            amount,
            total: sa.amount,
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
